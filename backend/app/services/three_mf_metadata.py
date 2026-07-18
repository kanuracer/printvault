"""Bounded, non-rendering extraction of user-visible metadata from 3MF packages.

3MF is a ZIP container and is always treated here as untrusted input.  This
module intentionally returns only a small, immutable presentation model: core
metadata plus instruction-like documents.  It never returns archive member
paths or binary document content.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, TypeAlias
import re
import stat
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile


class ThreeMfExtractionError(ValueError):
    """Raised when an untrusted 3MF package violates an extraction boundary."""


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Hard limits applied before archive members are opened."""

    max_archive_bytes: int = 16 * 1024 * 1024
    max_members: int = 512
    max_member_bytes: int = 4 * 1024 * 1024
    max_total_uncompressed_bytes: int = 32 * 1024 * 1024
    max_text_document_bytes: int = 512 * 1024
    max_metadata_xml_bytes: int = 512 * 1024
    max_compression_ratio: int = 100


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


@dataclass(frozen=True, slots=True)
class ThreeMfDocument:
    """A safe presentation record for an instruction-like package document."""

    display_label: str
    content_type: str
    byte_size: int
    text_content: str | None = None


@dataclass(frozen=True, slots=True)
class ThreeMfExtractionResult:
    """Immutable, path-free metadata extracted from a 3MF package."""

    metadata: Mapping[str, str]
    documents: tuple[ThreeMfDocument, ...]


ArchiveSource: TypeAlias = bytes | bytearray | memoryview | str | Path | BinaryIO


_METADATA_NAMES = frozenset(
    {
        "title",
        "designer",
        "description",
        "copyright",
        "licenseterms",
        "rating",
        "creationdate",
        "modificationdate",
        "application",
        "keywords",
    }
)
_TEXT_DOCUMENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
_DOCUMENT_TYPES = {**_TEXT_DOCUMENT_TYPES, ".pdf": "application/pdf"}
_INSTRUCTION_WORDS = ("instruction", "readme", "manual", "guide", "assembly")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_XML_ENTITY_MARKERS = (b"<!doctype", b"<!entity")


def extract_three_mf_metadata(
    source: ArchiveSource,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> ThreeMfExtractionResult:
    """Extract safe, bounded metadata from an untrusted 3MF ZIP archive.

    ``source`` can be package bytes, a binary stream, or a server-selected
    path.  Paths are not reflected in either returned values or errors.  Callers
    that receive paths from users must resolve them through their own configured
    root guard before calling this read-only helper.
    """

    _validate_limits(limits)
    package_bytes = _read_bounded_archive(source, limits.max_archive_bytes)
    try:
        with zipfile.ZipFile(BytesIO(package_bytes)) as archive:
            members = archive.infolist()
            _validate_members(members, limits)
            metadata = _extract_metadata(archive, members, limits)
            documents = _extract_documents(archive, members, limits)
    except ThreeMfExtractionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ThreeMfExtractionError("invalid 3MF archive") from error

    return ThreeMfExtractionResult(
        metadata=MappingProxyType(metadata),
        documents=tuple(documents),
    )


def _validate_limits(limits: ArchiveLimits) -> None:
    if any(
        value <= 0
        for value in (
            limits.max_archive_bytes,
            limits.max_members,
            limits.max_member_bytes,
            limits.max_total_uncompressed_bytes,
            limits.max_text_document_bytes,
            limits.max_metadata_xml_bytes,
            limits.max_compression_ratio,
        )
    ):
        raise ValueError("archive limits must be positive")



def _read_bounded_archive(source: ArchiveSource, maximum: int) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        package = bytes(source)
        if len(package) > maximum:
            raise ThreeMfExtractionError("archive exceeds size limit")
        return package

    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > maximum:
                raise ThreeMfExtractionError("archive exceeds size limit")
            with path.open("rb") as stream:
                package = stream.read(maximum + 1)
        except ThreeMfExtractionError:
            raise
        except OSError as error:
            raise ThreeMfExtractionError("unable to read 3MF archive") from error
    else:
        try:
            package = source.read(maximum + 1)
        except (AttributeError, OSError, TypeError) as error:
            raise ThreeMfExtractionError("unable to read 3MF archive") from error

    if not isinstance(package, bytes) or len(package) > maximum:
        raise ThreeMfExtractionError("archive exceeds size limit")
    return package


def _validate_members(members: list[zipfile.ZipInfo], limits: ArchiveLimits) -> None:
    if len(members) > limits.max_members:
        raise ThreeMfExtractionError("too many archive members")

    total_uncompressed = 0
    seen_names: set[str] = set()
    for member in members:
        raw_name = member.orig_filename
        _validate_member_name(raw_name)
        if raw_name in seen_names:
            raise ThreeMfExtractionError("duplicate archive member")
        seen_names.add(raw_name)
        _reject_unsafe_member_type(member)
        if member.flag_bits & 0x1:
            raise ThreeMfExtractionError("encrypted archive members are unsupported")
        if member.file_size > limits.max_member_bytes:
            raise ThreeMfExtractionError("archive member exceeds size limit")
        total_uncompressed += member.file_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            raise ThreeMfExtractionError("archive exceeds uncompressed size limit")
        if member.file_size > max(1, member.compress_size) * limits.max_compression_ratio:
            raise ThreeMfExtractionError("archive member compression ratio exceeds limit")


def _validate_member_name(name: str) -> None:
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or _DRIVE_PREFIX.match(name) is not None
    ):
        raise ThreeMfExtractionError("unsafe archive member")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts[:-1]) or parts[-1] in {".", ".."}:
        raise ThreeMfExtractionError("unsafe archive member")


def _reject_unsafe_member_type(member: zipfile.ZipInfo) -> None:
    unix_mode = member.external_attr >> 16
    member_type = stat.S_IFMT(unix_mode)
    if stat.S_ISLNK(unix_mode) or member_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ThreeMfExtractionError("unsafe archive member")


def _extract_metadata(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    limits: ArchiveLimits,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for member in members:
        if member.is_dir() or not member.filename.startswith("3D/") or not member.filename.lower().endswith(".model"):
            continue
        if member.file_size > limits.max_metadata_xml_bytes:
            continue
        xml_bytes = _read_member(archive, member, limits.max_metadata_xml_bytes)
        lowered = xml_bytes.lower()
        if any(marker in lowered for marker in _XML_ENTITY_MARKERS):
            raise ThreeMfExtractionError("unsafe XML in 3MF metadata")
        try:
            root = ElementTree.fromstring(xml_bytes)
        except ElementTree.ParseError as error:
            raise ThreeMfExtractionError("invalid 3MF metadata XML") from error
        if _local_name(root.tag) != "model":
            continue
        for child in root:
            if _local_name(child.tag) != "metadata":
                continue
            raw_name = child.attrib.get("name", "")
            name = raw_name.strip().casefold()
            if name not in _METADATA_NAMES or name in metadata:
                continue
            value = "".join(child.itertext()).strip()
            if value and len(value) <= 8_192:
                metadata[name] = value
    return metadata


def _extract_documents(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    limits: ArchiveLimits,
) -> list[ThreeMfDocument]:
    documents: list[ThreeMfDocument] = []
    used_labels: set[str] = set()
    for member in members:
        if member.is_dir():
            continue
        filename = member.filename.rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.casefold()
        content_type = _DOCUMENT_TYPES.get(suffix)
        if content_type is None or not _is_instruction_like(filename):
            continue
        label = _unique_label(_safe_display_label(filename), used_labels)
        text_content: str | None = None
        if suffix in _TEXT_DOCUMENT_TYPES and member.file_size <= limits.max_text_document_bytes:
            text_content = _decode_utf8_text(_read_member(archive, member, limits.max_text_document_bytes))
        documents.append(
            ThreeMfDocument(
                display_label=label,
                content_type=content_type,
                byte_size=member.file_size,
                text_content=text_content,
            )
        )
    return documents


def _read_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, maximum: int) -> bytes:
    if member.file_size > maximum:
        raise ThreeMfExtractionError("archive member exceeds extraction limit")
    try:
        with archive.open(member, "r") as stream:
            content = stream.read(maximum + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ThreeMfExtractionError("invalid 3MF archive member") from error
    if len(content) > maximum:
        raise ThreeMfExtractionError("archive member exceeds extraction limit")
    return content


def _decode_utf8_text(content: bytes) -> str | None:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_instruction_like(filename: str) -> bool:
    stem = Path(filename).stem.casefold()
    return any(word in stem for word in _INSTRUCTION_WORDS)


def _safe_display_label(filename: str) -> str:
    normalized = unicodedata.normalize("NFKC", filename)
    visible = "".join(character for character in normalized if character.isprintable())
    collapsed = " ".join(visible.split())
    return (collapsed[:120] or "Instructions")


def _unique_label(label: str, used_labels: set[str]) -> str:
    if label not in used_labels:
        used_labels.add(label)
        return label
    index = 2
    while f"{label} ({index})" in used_labels:
        index += 1
    unique = f"{label} ({index})"
    used_labels.add(unique)
    return unique


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
