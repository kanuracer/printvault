"""Safe, idempotent configured-library indexing for PrintVault."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.models import normalize_relative_glob_pattern
from app.services.filesystem import LibraryLike, SafeFilesystem, UnsafePathError, UnsupportedMutationError
from app.services.metadata import FileFingerprint, GeometryMetadata, SUPPORTED_FORMATS, extract_geometry, fingerprint_model
from app.services.thumbnails import ThumbnailCache
from app.services.three_mf_metadata import ThreeMfExtractionError, extract_three_mf_metadata


@dataclass(frozen=True)
class IndexedAsset:
    """Repository-neutral index record; persistence adapters map this to ORM fields."""

    library_key: str
    relative_path: str
    format: str
    fingerprint: FileFingerprint
    geometry: GeometryMetadata
    thumbnail_path: str
    metadata: dict[str, object] = field(default_factory=dict)
    missing: bool = False


@dataclass(frozen=True)
class ScanResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    skipped: int = 0
    failed: int = 0


class IndexRepository(Protocol):
    """Injectable persistence boundary for index operations and tests."""

    def get(self, library_key: str, relative_path: str) -> IndexedAsset | None: ...

    def create(self, asset: IndexedAsset) -> None: ...

    def update(self, asset: IndexedAsset) -> None: ...

    def mark_missing(self, library_key: str, present_relative_paths: set[str]) -> int: ...


class LibraryIndexer:
    """Index supported regular files only under a registered configured root."""

    def __init__(
        self, filesystem: SafeFilesystem, repository: IndexRepository, thumbnails: ThumbnailCache, *, exclude_patterns: Iterable[str] = ()
    ) -> None:
        self.filesystem = filesystem
        self.repository = repository
        self.thumbnails = thumbnails
        self.exclude_patterns = tuple(_safe_exclude_pattern(pattern) for pattern in exclude_patterns)

    def scan(self, library: LibraryLike) -> ScanResult:
        """Scan one configured library without following links or deleting records."""
        identity = self.filesystem.registry.register_library(library)
        root = self.filesystem.registry.root_for_key(identity.key)
        created = updated = unchanged = skipped = failed = 0
        present: set[str] = set()

        for directory, directories, filenames in os.walk(root, followlinks=False):
            # Do not attempt to index below a symlink even where platform
            # traversal semantics change.  SafeFilesystem rechecks file paths.
            directories[:] = [name for name in directories if not (Path(directory) / name).is_symlink()]
            for filename in filenames:
                candidate = Path(directory) / filename
                relative_path = candidate.relative_to(root).as_posix()
                if any(_matches_exclude_pattern(relative_path, pattern) for pattern in self.exclude_patterns):
                    skipped += 1
                    continue
                if candidate.suffix.casefold().lstrip(".") not in SUPPORTED_FORMATS:
                    skipped += 1
                    continue
                try:
                    resolved = self.filesystem.resolve_library_path(library, relative_path, require_regular=True)
                    fingerprint = fingerprint_model(resolved)
                except (OSError, ValueError, UnsafePathError, UnsupportedMutationError):
                    failed += 1
                    continue

                present.add(relative_path)
                existing = self.repository.get(identity.key, relative_path)
                metadata = _three_mf_metadata(resolved, fingerprint.format)
                if existing is not None and existing.fingerprint == fingerprint and not existing.missing:
                    if existing.metadata == metadata:
                        unchanged += 1
                        continue
                    self.repository.update(
                        IndexedAsset(
                            library_key=identity.key,
                            relative_path=relative_path,
                            format=fingerprint.format,
                            fingerprint=fingerprint,
                            geometry=existing.geometry,
                            thumbnail_path=existing.thumbnail_path,
                            metadata=metadata,
                        )
                    )
                    updated += 1
                    continue

                try:
                    geometry = extract_geometry(resolved, fingerprint.format)
                    thumbnail = self.thumbnails.create(resolved, fingerprint)
                except (OSError, ValueError):
                    # A metadata/thumbnail parsing failure must not make the
                    # model disappear from the index.  Keep conservative data.
                    geometry = GeometryMetadata()
                    thumbnail_path = ""
                else:
                    thumbnail_path = str(thumbnail.path)

                record = IndexedAsset(
                    library_key=identity.key,
                    relative_path=relative_path,
                    format=fingerprint.format,
                    fingerprint=fingerprint,
                    geometry=geometry,
                    thumbnail_path=thumbnail_path,
                    metadata=metadata,
                )
                if existing is None:
                    self.repository.create(record)
                    created += 1
                else:
                    self.repository.update(record)
                    updated += 1

        missing = self.repository.mark_missing(identity.key, present)
        return ScanResult(created=created, updated=updated, unchanged=unchanged, missing=missing, skipped=skipped, failed=failed)


def _safe_exclude_pattern(value: str) -> str:
    return normalize_relative_glob_pattern(value)


def _matches_exclude_pattern(relative_path: str, pattern: str) -> bool:
    path = PurePosixPath(relative_path)
    return path.match(pattern) or (pattern.startswith("**/") and path.match(pattern[3:]))


def _three_mf_metadata(path: Path, format_name: str) -> dict[str, object]:
    if format_name != "3mf":
        return {}
    try:
        extraction = extract_three_mf_metadata(path)
    except (OSError, ValueError, ThreeMfExtractionError):
        return {}
    presentation: dict[str, object] = {
        "core": dict(extraction.metadata),
        "documents": [
            {
                "label": document.display_label,
                "content_type": document.content_type,
                "byte_size": document.byte_size,
                **({"text": document.text_content} if document.text_content is not None else {}),
            }
            for document in extraction.documents
        ],
    }
    if extraction.build_colors:
        presentation["build_colors"] = list(extraction.build_colors)
    if extraction.build_transforms:
        presentation["build_transforms"] = [list(transform) for transform in extraction.build_transforms]
    return {"three_mf": presentation}


def duplicate_groups(assets: Iterable[IndexedAsset]) -> dict[str, tuple[IndexedAsset, ...]]:
    """Group present indexed assets by content SHA-256, retaining duplicates only."""
    grouped: dict[str, list[IndexedAsset]] = defaultdict(list)
    for asset in assets:
        if not asset.missing:
            grouped[asset.fingerprint.sha256].append(asset)
    return {
        digest: tuple(sorted(group, key=lambda asset: (asset.library_key, asset.relative_path)))
        for digest, group in grouped.items()
        if len(group) > 1
    }


__all__ = ["IndexRepository", "IndexedAsset", "LibraryIndexer", "ScanResult", "duplicate_groups"]
