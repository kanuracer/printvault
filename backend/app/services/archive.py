"""Archive, restore, and permanent-delete operations for PrintVault assets."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.filesystem import (
    AssetLike,
    FileActionResult,
    LibraryLike,
    LibraryRootRegistry,
    PathCollisionError,
    SafeFilesystem,
    UnsafePathError,
    _move_without_overwrite,
    normalize_relative_path,
)


@dataclass(frozen=True)
class ArchiveMetadata:
    """The only location information accepted by a restore operation."""

    source_library_key: str
    source_relative_path: str
    archive_relative_path: str

    def audit_metadata(self) -> dict[str, str]:
        return {
            "source_library_key": self.source_library_key,
            "source_relative_path": self.source_relative_path,
            "archive_relative_path": self.archive_relative_path,
        }


@dataclass(frozen=True)
class ArchiveResult:
    """Completed archive result with the immutable restore record."""

    action: str
    status: str
    performed: bool
    source_library_key: str
    source_relative_path: str
    destination_library_key: str
    destination_relative_path: str
    metadata: ArchiveMetadata

    def audit_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "performed": self.performed,
            "source_library_key": self.source_library_key,
            "source_relative_path": self.source_relative_path,
            "destination_library_key": self.destination_library_key,
            "destination_relative_path": self.destination_relative_path,
            "metadata": self.metadata.audit_metadata(),
        }


class ArchiveService:
    """Safely archive, restore, and delete regular persisted assets."""

    def __init__(self, registry: LibraryRootRegistry, archive_library: LibraryLike) -> None:
        self.filesystem = SafeFilesystem(registry)
        self.registry = registry
        self.archive_library = archive_library
        self._archive_identity = registry.register_library(archive_library)

    def archive_asset(self, asset: AssetLike) -> ArchiveResult:
        """Move an asset into the archive root, preserving its original location metadata."""
        source_identity = self.registry.register_library(asset.library)
        if source_identity.key == self._archive_identity.key:
            raise UnsafePathError("assets already in the archive cannot be archived again")
        source_relative_path = normalize_relative_path(asset.relative_path)
        source = self.filesystem.resolve_asset(asset, require_regular=True)

        # Including the source library key avoids collisions between a model and
        # a project that have the same saved relative path.
        archive_relative_path = normalize_relative_path(f"{source_identity.key}/{source_relative_path}")
        destination = self.filesystem.prepare_destination(self.archive_library, archive_relative_path)
        _move_without_overwrite(source, destination)

        metadata = ArchiveMetadata(
            source_library_key=source_identity.key,
            source_relative_path=source_relative_path,
            archive_relative_path=archive_relative_path,
        )
        return ArchiveResult(
            action="archive",
            status="completed",
            performed=True,
            source_library_key=source_identity.key,
            source_relative_path=source_relative_path,
            destination_library_key=self._archive_identity.key,
            destination_relative_path=archive_relative_path,
            metadata=metadata,
        )

    def restore(self, metadata: ArchiveMetadata) -> FileActionResult:
        """Restore strictly to the library and relative path recorded during archive."""
        if not isinstance(metadata, ArchiveMetadata):
            raise TypeError("restore requires ArchiveMetadata returned by archive")

        source_relative_path = normalize_relative_path(metadata.source_relative_path)
        archive_relative_path = normalize_relative_path(metadata.archive_relative_path)
        expected_archive_relative_path = normalize_relative_path(f"{metadata.source_library_key}/{source_relative_path}")
        if archive_relative_path != expected_archive_relative_path:
            raise UnsafePathError("archive metadata does not match its recorded source")

        original_library = self.registry.library_for_key(metadata.source_library_key)
        archived_file = self.filesystem.resolve_library_path(
            self.archive_library, archive_relative_path, require_regular=True
        )
        destination = self.filesystem.prepare_destination(original_library, source_relative_path)
        _move_without_overwrite(archived_file, destination)
        return FileActionResult(
            action="restore",
            status="completed",
            performed=True,
            source_library_key=self._archive_identity.key,
            source_relative_path=archive_relative_path,
            destination_library_key=original_library.key,
            destination_relative_path=source_relative_path,
            metadata=metadata.audit_metadata(),
        )

    def permanently_delete(
        self, asset: AssetLike, *, authorized: bool | Callable[[AssetLike], bool]
    ) -> FileActionResult:
        """Delete one safe regular file only after explicit caller authorization.

        A denied request is returned as a non-performed structured outcome so a
        caller can audit denial attempts without inferring a host path.
        """
        identity = self.registry.register_library(asset.library)
        relative_path = normalize_relative_path(asset.relative_path)
        permitted = self._is_authorized(asset, authorized)
        if not permitted:
            return FileActionResult(
                action="permanent_delete",
                status="denied",
                performed=False,
                source_library_key=identity.key,
                source_relative_path=relative_path,
                metadata={"authorization": "denied"},
            )

        source = self.filesystem.resolve_asset(asset, require_regular=True)
        # lstat in resolve_asset above proves this is a regular non-symlink file;
        # unlink removes the directory entry rather than following a target.
        os.unlink(source)
        return FileActionResult(
            action="permanent_delete",
            status="completed",
            performed=True,
            source_library_key=identity.key,
            source_relative_path=relative_path,
            metadata={"authorization": "granted"},
        )

    @staticmethod
    def _is_authorized(asset: AssetLike, authorized: bool | Callable[[AssetLike], bool]) -> bool:
        if isinstance(authorized, bool):
            return authorized
        if callable(authorized):
            decision = authorized(asset)
            if not isinstance(decision, bool):
                raise TypeError("authorization callback must return a boolean")
            return decision
        raise TypeError("permanent deletion requires an explicit boolean or authorization callback")


__all__ = ["ArchiveMetadata", "ArchiveResult", "ArchiveService", "PathCollisionError"]
