"""Safe filesystem primitives for configured PrintVault libraries.

Host paths enter this module only when the application builds the configured
library registry.  All asset operations are expressed as a persisted library
identity plus a normalized relative path; no operation accepts a client host
path.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Protocol


class UnsafePathError(ValueError):
    """Raised when a path is not a safe member of a configured library."""


class PathCollisionError(FileExistsError):
    """Raised when a mutation would overwrite an existing destination."""


class UnsupportedMutationError(ValueError):
    """Raised when an operation is outside the supported safe mutation set."""


class LibraryLike(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def root_name(self) -> str: ...


class AssetLike(Protocol):
    library: LibraryLike
    relative_path: str


@dataclass(frozen=True)
class RegisteredLibrary:
    """The non-host-path identity needed to resolve a registered library."""

    key: str
    root_name: str


@dataclass(frozen=True)
class FileActionResult:
    """Structured mutation outcome that can be persisted as an audit event."""

    action: str
    status: str
    performed: bool
    source_library_key: str
    source_relative_path: str
    destination_library_key: str | None = None
    destination_relative_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def audit_metadata(self) -> dict[str, Any]:
        """Return JSON-ready details for a future immutable audit record."""
        return {
            "status": self.status,
            "performed": self.performed,
            "source_library_key": self.source_library_key,
            "source_relative_path": self.source_relative_path,
            "destination_library_key": self.destination_library_key,
            "destination_relative_path": self.destination_relative_path,
            "metadata": dict(self.metadata),
        }


def normalize_relative_path(value: str) -> str:
    """Normalize an asset-relative path while refusing all escape syntax."""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw or "\x00" in raw:
        raise UnsafePathError("path must be a non-empty relative path")
    windows = PureWindowsPath(raw)
    if PurePosixPath(raw).is_absolute() or windows.is_absolute() or windows.drive:
        raise UnsafePathError("absolute paths are not permitted")

    parts = raw.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise UnsafePathError("path traversal is not permitted")
    normalized = [part for part in parts if part and part != "."]
    if not normalized:
        raise UnsafePathError("path must be a non-empty relative path")
    if any(part.startswith(".") for part in normalized):
        raise UnsafePathError("hidden path components are not permitted")
    return "/".join(normalized)


def _library_identity(library: LibraryLike) -> RegisteredLibrary:
    key = getattr(library, "key", None)
    root_name = getattr(library, "root_name", None)
    if not isinstance(key, str) or not key.strip() or not isinstance(root_name, str) or not root_name.strip():
        raise UnsafePathError("library must have a stable key and root name")
    # A key becomes part of an archive-relative path, so it must be exactly one
    # safe path component rather than merely a non-empty database string.
    normalized_key = normalize_relative_path(key)
    if normalized_key != key.strip() or "/" in normalized_key:
        raise UnsafePathError("library key must be a single safe path component")
    return RegisteredLibrary(key=normalized_key, root_name=root_name.strip())


def _path_exists_or_is_link(path: Path) -> bool:
    return os.path.lexists(path)


class LibraryRootRegistry:
    """Maps configured absolute roots to stable ``Library.root_name`` values."""

    def __init__(self, roots_by_name: Mapping[str, str | Path]) -> None:
        if not roots_by_name:
            raise ValueError("at least one configured library root is required")

        roots: dict[str, Path] = {}
        canonical_roots: set[Path] = set()
        for raw_name, raw_root in roots_by_name.items():
            root_name = raw_name.strip() if isinstance(raw_name, str) else ""
            if not root_name:
                raise ValueError("configured library root names must not be empty")
            root = Path(raw_root)
            if not root.is_absolute():
                raise ValueError("configured library roots must be absolute")
            if root.is_symlink():
                raise UnsafePathError("configured library roots must not be symlinks")
            if not root.is_dir():
                raise UnsafePathError("configured library roots must be existing directories")
            canonical = root.resolve(strict=True)
            if canonical in canonical_roots:
                raise ValueError("configured library roots must be unique")
            roots[root_name] = canonical
            canonical_roots.add(canonical)

        self._roots_by_name = roots
        self._libraries_by_key: dict[str, RegisteredLibrary] = {}

    def register_library(self, library: LibraryLike) -> RegisteredLibrary:
        """Register a DB library identity after proving its root name is configured."""
        identity = _library_identity(library)
        if identity.root_name not in self._roots_by_name:
            raise UnsafePathError("library root is not configured")
        existing = self._libraries_by_key.get(identity.key)
        if existing is not None and existing.root_name != identity.root_name:
            raise UnsafePathError("library key is already registered to another root")
        self._libraries_by_key[identity.key] = identity
        return identity

    def root_for(self, library: LibraryLike) -> Path:
        """Return the configured root for a known Library identity only."""
        identity = self.register_library(library)
        return self._roots_by_name[identity.root_name]

    def library_for_key(self, key: str) -> RegisteredLibrary:
        """Return a previously registered library by stable key for restore operations."""
        if not isinstance(key, str):
            raise UnsafePathError("library key must be a string")
        library = self._libraries_by_key.get(key)
        if library is None:
            raise UnsafePathError("recorded source library is not registered")
        return library

    def root_for_key(self, key: str) -> Path:
        library = self.library_for_key(key)
        return self._roots_by_name[library.root_name]


def _assert_no_symlink_components(root: Path, relative_path: str) -> Path:
    """Build a candidate path while rejecting every existing symlink component."""
    candidate = root.joinpath(*relative_path.split("/"))
    current = root
    for part in relative_path.split("/"):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            # Descendants cannot already exist once a parent is absent.  The
            # final containment check still protects the syntactic path.
            break
        if stat.S_ISLNK(mode):
            raise UnsafePathError("symlink components are not permitted")
    return candidate


def _assert_contained(root: Path, candidate: Path) -> None:
    """Defend in depth against unexpected canonical resolution outside root."""
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise UnsafePathError("path escapes its configured library root") from error


def _move_without_overwrite(source: Path, destination: Path) -> None:
    """Move a verified regular file, preferring ``os.replace`` on one filesystem."""
    if _path_exists_or_is_link(destination):
        raise PathCollisionError("destination already exists")
    try:
        os.replace(source, destination)
        return
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise

    # Cross-device rename is not atomic.  Stage in the destination directory,
    # atomically publish the finished copy there, then remove the source.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".printvault-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(source, temporary, follow_symlinks=False)
        if _path_exists_or_is_link(destination):
            raise PathCollisionError("destination already exists")
        os.replace(temporary, destination)
        source.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class SafeFilesystem:
    """Resolve and mutate only regular files inside registered library roots."""

    def __init__(self, registry: LibraryRootRegistry) -> None:
        self.registry = registry

    def resolve_asset(self, asset: AssetLike, *, require_regular: bool = False) -> Path:
        """Resolve one persisted asset path without accepting a host path from callers."""
        if not hasattr(asset, "library") or not hasattr(asset, "relative_path"):
            raise UnsafePathError("asset must provide persisted library and relative path")
        identity = self.registry.register_library(asset.library)
        return self._resolve(identity, asset.relative_path, require_regular=require_regular)

    def resolve_library_path(
        self, library: LibraryLike | RegisteredLibrary, relative_path: str, *, require_regular: bool = False
    ) -> Path:
        """Resolve a persisted relative path under one configured library identity."""
        identity = self.registry.register_library(library)
        return self._resolve(identity, relative_path, require_regular=require_regular)

    def prepare_destination(self, library: LibraryLike | RegisteredLibrary, relative_path: str) -> Path:
        """Create only real directories below a configured root and return a safe destination."""
        identity = self.registry.register_library(library)
        destination = self._resolve(identity, relative_path)
        if _path_exists_or_is_link(destination):
            raise PathCollisionError("destination already exists")

        root = self.registry.root_for_key(identity.key)
        relative_parent = Path(normalize_relative_path(relative_path)).parent
        parent = root
        if relative_parent != Path("."):
            for part in relative_parent.parts:
                parent = parent / part
                if _path_exists_or_is_link(parent):
                    if stat.S_ISLNK(os.lstat(parent).st_mode):
                        raise UnsafePathError("symlink components are not permitted")
                    if not parent.is_dir():
                        raise UnsafePathError("destination parent is not a directory")
                else:
                    parent.mkdir()
        _assert_contained(root, parent)
        return destination

    def move_asset(
        self, asset: AssetLike, destination_library: LibraryLike, destination_relative_path: str
    ) -> FileActionResult:
        """Move one regular asset to a configured library-relative destination."""
        source_identity = self.registry.register_library(asset.library)
        destination_identity = self.registry.register_library(destination_library)
        source_relative_path = normalize_relative_path(asset.relative_path)
        destination_relative_path = normalize_relative_path(destination_relative_path)
        source = self._resolve(source_identity, source_relative_path, require_regular=True)
        destination = self.prepare_destination(destination_identity, destination_relative_path)
        _move_without_overwrite(source, destination)
        return FileActionResult(
            action="move",
            status="completed",
            performed=True,
            source_library_key=source_identity.key,
            source_relative_path=source_relative_path,
            destination_library_key=destination_identity.key,
            destination_relative_path=destination_relative_path,
        )

    def _resolve(self, identity: RegisteredLibrary, relative_path: str, *, require_regular: bool = False) -> Path:
        normalized = normalize_relative_path(relative_path)
        root = self.registry.root_for_key(identity.key)
        candidate = _assert_no_symlink_components(root, normalized)
        _assert_contained(root, candidate)
        if require_regular:
            try:
                mode = os.lstat(candidate).st_mode
            except FileNotFoundError as error:
                raise UnsafePathError("asset file does not exist") from error
            if stat.S_ISLNK(mode):
                raise UnsafePathError("symlink components are not permitted")
            if not stat.S_ISREG(mode):
                raise UnsupportedMutationError("only regular files may be mutated")
        return candidate


# Explicit alias for applications that prefer the domain-oriented name.
LibraryFilesystem = SafeFilesystem

__all__ = [
    "FileActionResult",
    "LibraryFilesystem",
    "LibraryRootRegistry",
    "PathCollisionError",
    "SafeFilesystem",
    "UnsafePathError",
    "UnsupportedMutationError",
    "normalize_relative_path",
]
