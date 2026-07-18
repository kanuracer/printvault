from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.archive import ArchiveService
from app.services.filesystem import LibraryRootRegistry, PathCollisionError, UnsafePathError


def library(key: str, root_name: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, root_name=root_name)


def asset(source_library: SimpleNamespace, relative_path: str) -> SimpleNamespace:
    return SimpleNamespace(library=source_library, relative_path=relative_path)


@pytest.fixture
def archive_service(
    tmp_path: Path,
) -> tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path]:
    models_root = tmp_path / "models"
    projects_root = tmp_path / "projects"
    archive_root = tmp_path / "archive"
    for root in (models_root, projects_root, archive_root):
        root.mkdir()
    models = library("models", "Models")
    projects = library("projects", "Projects")
    archive = library("archive", "Archive")
    registry = LibraryRootRegistry(
        {"Models": models_root, "Projects": projects_root, "Archive": archive_root}
    )
    return ArchiveService(registry, archive), models, projects, archive, models_root, projects_root, archive_root


def test_archive_moves_a_regular_asset_to_archive_and_returns_immutable_source_metadata(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path],
) -> None:
    service, models, _, _, models_root, _, archive_root = archive_service
    source = models_root / "parts" / "bracket.stl"
    source.parent.mkdir()
    source.write_text("mesh", encoding="utf-8")

    result = service.archive_asset(asset(models, "parts/bracket.stl"))

    assert result.action == "archive"
    assert result.status == "completed"
    assert result.metadata.source_library_key == "models"
    assert result.metadata.source_relative_path == "parts/bracket.stl"
    assert result.metadata.archive_relative_path == "models/parts/bracket.stl"
    assert not source.exists()
    assert (archive_root / "models" / "parts" / "bracket.stl").read_text(encoding="utf-8") == "mesh"


def test_archive_rejects_a_destination_collision_without_overwriting(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path],
) -> None:
    service, models, _, _, models_root, _, archive_root = archive_service
    source = models_root / "part.stl"
    destination = archive_root / "models" / "part.stl"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("archive copy", encoding="utf-8")

    with pytest.raises(PathCollisionError):
        service.archive_asset(asset(models, "part.stl"))

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "archive copy"


def test_restore_uses_only_recorded_source_library_and_relative_path(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path],
) -> None:
    service, models, _, _, models_root, _, _ = archive_service
    source = models_root / "parts" / "bracket.stl"
    source.parent.mkdir()
    source.write_text("mesh", encoding="utf-8")
    archived = service.archive_asset(asset(models, "parts/bracket.stl"))

    restored = service.restore(archived.metadata)

    assert restored.action == "restore"
    assert restored.status == "completed"
    assert restored.destination_library_key == "models"
    assert restored.destination_relative_path == "parts/bracket.stl"
    assert source.read_text(encoding="utf-8") == "mesh"


def test_restore_rejects_a_collision_at_its_recorded_original_location(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path],
) -> None:
    service, models, _, _, models_root, _, _ = archive_service
    source = models_root / "part.stl"
    source.write_text("to archive", encoding="utf-8")
    archived = service.archive_asset(asset(models, "part.stl"))
    source.write_text("replacement", encoding="utf-8")

    with pytest.raises(PathCollisionError):
        service.restore(archived.metadata)

    assert source.read_text(encoding="utf-8") == "replacement"


def test_permanent_delete_returns_a_denied_audit_ready_result_without_removing_file(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path],
) -> None:
    service, models, _, _, models_root, _, _ = archive_service
    source = models_root / "part.stl"
    source.write_text("keep", encoding="utf-8")

    result = service.permanently_delete(asset(models, "part.stl"), authorized=False)

    assert result.action == "permanent_delete"
    assert result.status == "denied"
    assert result.performed is False
    assert source.read_text(encoding="utf-8") == "keep"


def test_permanent_delete_requires_safe_regular_file_and_accepts_an_authorization_callback(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path],
) -> None:
    service, models, _, _, models_root, _, _ = archive_service
    source = models_root / "part.stl"
    source.write_text("delete", encoding="utf-8")

    result = service.permanently_delete(asset(models, "part.stl"), authorized=lambda candidate: candidate.library.key == "models")

    assert result.status == "completed"
    assert result.performed is True
    assert not source.exists()


def test_archive_rejects_a_symlink_source_before_it_can_be_moved(
    archive_service: tuple[ArchiveService, SimpleNamespace, SimpleNamespace, SimpleNamespace, Path, Path, Path], tmp_path: Path
) -> None:
    service, models, _, _, models_root, _, _ = archive_service
    outside = tmp_path / "outside.stl"
    outside.write_text("external", encoding="utf-8")
    (models_root / "linked.stl").symlink_to(outside)

    with pytest.raises(UnsafePathError, match="symlink"):
        service.archive_asset(asset(models, "linked.stl"))

    assert outside.read_text(encoding="utf-8") == "external"
