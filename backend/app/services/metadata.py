"""Streaming, best-effort metadata extraction for model assets.

This module never treats model contents as trusted.  Hashing and lightweight
geometry inspection use bounded reads; unsupported or malformed geometry is
reported as unknown rather than aborting a library scan.
"""

from __future__ import annotations

import hashlib
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_FORMATS = frozenset({"stl", "obj", "3mf"})
_DEFAULT_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class FileFingerprint:
    sha256: str
    byte_size: int
    mtime_ns: int
    format: str


@dataclass(frozen=True)
class GeometryMetadata:
    triangle_count: int | None = None
    face_count: int | None = None


def model_format(path: Path) -> str:
    """Return a supported lowercase model format based on the extension."""
    extension = path.suffix.casefold().lstrip(".")
    if extension not in SUPPORTED_FORMATS:
        raise ValueError("unsupported model format")
    return extension


def fingerprint_model(path: Path, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> FileFingerprint:
    """Calculate a file fingerprint in bounded chunks without loading it all."""
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    file_stat = path.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("model must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return FileFingerprint(
        sha256=digest.hexdigest(),
        byte_size=file_stat.st_size,
        mtime_ns=file_stat.st_mtime_ns,
        format=model_format(path),
    )


def extract_geometry(path: Path, format: str, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> GeometryMetadata:
    """Extract only deterministic lightweight geometry details when feasible."""
    if format == "stl":
        return _stl_geometry(path, chunk_size=chunk_size)
    if format == "obj":
        return GeometryMetadata(face_count=_count_line_tokens(path, b"f", chunk_size=chunk_size))
    # 3MF geometry lives in arbitrary ZIP/XML payloads.  Avoid parsing it here;
    # a caller still receives useful fingerprint and format metadata.
    return GeometryMetadata()


def _stl_geometry(path: Path, *, chunk_size: int) -> GeometryMetadata:
    try:
        file_size = path.stat().st_size
        with path.open("rb") as source:
            header = source.read(84)
        if len(header) >= 84:
            declared_count = struct.unpack("<I", header[80:84])[0]
            expected_size = 84 + (declared_count * 50)
            if expected_size == file_size:
                return GeometryMetadata(triangle_count=declared_count)
        if header.lstrip().lower().startswith(b"solid"):
            return GeometryMetadata(triangle_count=_count_line_tokens(path, b"facet", chunk_size=chunk_size))
    except (OSError, ValueError, struct.error):
        pass
    return GeometryMetadata()


def _count_line_tokens(path: Path, token: bytes, *, chunk_size: int) -> int | None:
    """Count a line-leading ASCII token using bounded reads and bounded carry."""
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    pattern = re.compile(rb"(?:^|\n)[ \t]*" + re.escape(token) + rb"(?:[ \t]|\r?$)")
    count = 0
    carry = b""
    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                contents = carry + chunk
                boundary = len(carry)
                for match in pattern.finditer(contents):
                    if match.end() > boundary:
                        count += 1
                carry = contents[-128:]
    except OSError:
        return None
    return count


__all__ = ["FileFingerprint", "GeometryMetadata", "SUPPORTED_FORMATS", "extract_geometry", "fingerprint_model", "model_format"]
