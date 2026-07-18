from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from app.services.filesystem import LibraryRootRegistry, SafeFilesystem
from app.services.indexer import IndexedAsset, LibraryIndexer, duplicate_groups
from app.worker import IndexingWorker
from app.services.thumbnails import ThumbnailCache


class MemoryRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], IndexedAsset] = {}
        self.creates = 0
        self.updates = 0

    def get(self, library_key: str, relative_path: str) -> IndexedAsset | None:
        return self.records.get((library_key, relative_path))

    def create(self, asset: IndexedAsset) -> None:
        self.creates += 1
        self.records[(asset.library_key, asset.relative_path)] = asset

    def update(self, asset: IndexedAsset) -> None:
        self.updates += 1
        self.records[(asset.library_key, asset.relative_path)] = asset

    def mark_missing(self, library_key: str, present_relative_paths: set[str]) -> int:
        missing = 0
        for key, asset in tuple(self.records.items()):
            if key[0] == library_key and key[1] not in present_relative_paths and not asset.missing:
                self.records[key] = replace(asset, missing=True)
                missing += 1
        return missing


def _library(key: str, root_name: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, root_name=root_name)


def _indexer(tmp_path: Path) -> tuple[LibraryIndexer, MemoryRepository, SimpleNamespace, Path]:
    root = tmp_path / "models"
    root.mkdir()
    library = _library("models", "Models")
    filesystem = SafeFilesystem(LibraryRootRegistry({"Models": root}))
    repository = MemoryRepository()
    return LibraryIndexer(filesystem, repository, ThumbnailCache(tmp_path / "thumbnails")), repository, library, root


def test_indexer_only_records_supported_regular_model_files_and_ignores_others(tmp_path: Path) -> None:
    indexer, repository, library, root = _indexer(tmp_path)
    (root / "part.stl").write_text("solid part\nendsolid part\n", encoding="utf-8")
    (root / "mesh.OBJ").write_text("f 1 2 3\n", encoding="utf-8")
    (root / "project.3mf").write_bytes(b"not-a-zip")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")
    outside = tmp_path / "outside.stl"
    outside.write_text("solid outside\nendsolid outside\n", encoding="utf-8")
    (root / "linked.stl").symlink_to(outside)

    result = indexer.scan(library)

    assert result.created == 3
    assert result.skipped == 1
    assert set(repository.records) == {("models", "part.stl"), ("models", "mesh.OBJ"), ("models", "project.3mf")}
    assert repository.records[("models", "mesh.OBJ")].format == "obj"
    assert repository.records[("models", "part.stl")].geometry.triangle_count == 0


def test_indexer_is_idempotent_updates_changed_fingerprint_and_marks_missing(tmp_path: Path) -> None:
    indexer, repository, library, root = _indexer(tmp_path)
    model = root / "part.obj"
    model.write_text("f 1 2 3\n", encoding="utf-8")

    first = indexer.scan(library)
    second = indexer.scan(library)
    old_fingerprint = repository.records[("models", "part.obj")].fingerprint
    model.write_text("f 1 2 3\nf 1 3 4\n", encoding="utf-8")
    changed = indexer.scan(library)
    model.unlink()
    missing = indexer.scan(library)

    assert (first.created, second.unchanged, changed.updated, missing.missing) == (1, 1, 1, 1)
    assert repository.creates == 1
    assert repository.updates == 1
    assert repository.records[("models", "part.obj")].fingerprint.sha256 != old_fingerprint.sha256
    assert repository.records[("models", "part.obj")].missing is True


def test_duplicate_groups_use_sha256_and_exclude_unique_assets(tmp_path: Path) -> None:
    indexer, repository, library, root = _indexer(tmp_path)
    (root / "one.stl").write_text("solid same\nendsolid same\n", encoding="utf-8")
    (root / "two.stl").write_text("solid same\nendsolid same\n", encoding="utf-8")
    (root / "other.stl").write_text("solid other\nendsolid other\n", encoding="utf-8")
    indexer.scan(library)

    groups = duplicate_groups(repository.records.values())

    assert len(groups) == 1
    only_group = next(iter(groups.values()))
    assert {asset.relative_path for asset in only_group} == {"one.stl", "two.stl"}


def test_worker_delegates_one_registered_library_scan(tmp_path: Path) -> None:
    indexer, repository, library, root = _indexer(tmp_path)
    (root / "queued.stl").write_text("solid queued\nendsolid queued\n", encoding="utf-8")

    result = IndexingWorker(indexer).index_library(library)

    assert result.created == 1
    assert ("models", "queued.stl") in repository.records
