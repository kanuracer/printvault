"""Safe thumbnail-cache behavior for indexed PrintVault model assets.

No external model renderer is assumed.  We extract only an explicitly bounded
embedded 3MF image or generate a deterministic SVG placeholder from trusted
fingerprint fields.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.metadata import FileFingerprint

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ThumbnailResult:
    path: Path
    kind: str  # ``embedded`` or ``placeholder``


class ThumbnailCache:
    """Write thumbnails below a server-owned cache root, keyed solely by hash."""

    def __init__(self, root: Path, *, max_archive_bytes: int = 32 * 1024 * 1024, max_member_bytes: int = 4 * 1024 * 1024, max_members: int = 256) -> None:
        if max_archive_bytes < 1 or max_member_bytes < 1 or max_members < 1:
            raise ValueError("thumbnail limits must be positive")
        self.root = root
        self.max_archive_bytes = max_archive_bytes
        self.max_member_bytes = max_member_bytes
        self.max_members = max_members

    def create(self, model_path: Path, fingerprint: FileFingerprint) -> ThumbnailResult:
        """Return the cached thumbnail, never exposing a model filename in output."""
        self._validate_fingerprint(fingerprint)
        embedded = self._embedded_thumbnail(model_path, fingerprint)
        if embedded is not None:
            suffix, image = embedded
            target = self._target(fingerprint.sha256, suffix)
            self._write_once(target, image)
            return ThumbnailResult(path=target, kind="embedded")

        target = self._target(fingerprint.sha256, ".svg")
        self._write_once(target, self._placeholder_svg(fingerprint))
        return ThumbnailResult(path=target, kind="placeholder")

    def _embedded_thumbnail(self, model_path: Path, fingerprint: FileFingerprint) -> tuple[str, bytes] | None:
        if fingerprint.format != "3mf":
            return None
        try:
            if model_path.stat().st_size > self.max_archive_bytes:
                return None
            with zipfile.ZipFile(model_path) as archive:
                members = archive.infolist()
                if len(members) > self.max_members or any(not self._safe_member(member) for member in members):
                    return None
                for member in members:
                    suffix = Path(member.filename).suffix.lower()
                    if not self._is_thumbnail_member(member.filename, suffix):
                        continue
                    if member.file_size > self.max_member_bytes or member.compress_size > self.max_archive_bytes:
                        return None
                    with archive.open(member, "r") as source:
                        payload = source.read(self.max_member_bytes + 1)
                    if len(payload) > self.max_member_bytes:
                        return None
                    return suffix, payload
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
            return None
        return None

    @staticmethod
    def _safe_member(member: zipfile.ZipInfo) -> bool:
        name = member.filename
        if not name or "\x00" in name or "\\" in name:
            return False
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            return False
        mode = member.external_attr >> 16
        return not stat.S_ISLNK(mode)

    @staticmethod
    def _is_thumbnail_member(name: str, suffix: str) -> bool:
        if suffix not in _IMAGE_EXTENSIONS:
            return False
        normalized = name.casefold()
        return "thumbnail" in PurePosixPath(normalized).name

    def _target(self, digest: str, suffix: str) -> Path:
        return self.root / digest[:2] / f"{digest}{suffix}"

    @staticmethod
    def _validate_fingerprint(fingerprint: FileFingerprint) -> None:
        if not _DIGEST.fullmatch(fingerprint.sha256) or fingerprint.format not in {"stl", "obj", "3mf"}:
            raise ValueError("thumbnail cache requires a normalized fingerprint")

    @staticmethod
    def _placeholder_svg(fingerprint: FileFingerprint) -> bytes:
        # Both values are validated above: no filename/user metadata reaches SVG.
        label = fingerprint.format.upper()
        return (
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"240\" viewBox=\"0 0 320 240\" role=\"img\" aria-label=\"Generated model thumbnail\">"
            "<rect width=\"320\" height=\"240\" fill=\"#1f2937\"/>"
            f"<text x=\"160\" y=\"110\" text-anchor=\"middle\" fill=\"#e5e7eb\" font-family=\"sans-serif\" font-size=\"32\">{label}</text>"
            f"<text x=\"160\" y=\"145\" text-anchor=\"middle\" fill=\"#9ca3af\" font-family=\"monospace\" font-size=\"10\">{fingerprint.sha256}</text>"
            "</svg>"
        ).encode("utf-8")

    @staticmethod
    def _write_once(target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=".printvault-thumb-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


__all__ = ["ThumbnailCache", "ThumbnailResult"]
