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
import json
import math
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
    """Hard archive-validation and bounded-extraction limits."""

    max_archive_bytes: int = 16 * 1024 * 1024
    max_members: int = 512
    max_member_bytes: int = 4 * 1024 * 1024
    max_total_uncompressed_bytes: int = 32 * 1024 * 1024
    max_text_document_bytes: int = 512 * 1024
    max_metadata_xml_bytes: int = 512 * 1024
    max_slicer_model_settings_bytes: int = 1 * 1024 * 1024
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
    build_colors: tuple[str | None, ...] = ()
    build_transforms: tuple[tuple[float, ...], ...] = ()


@dataclass(slots=True)
class _ExtractionReadBudget:
    """Tracks bytes actually decompressed by this metadata-only reader."""

    remaining: int

    def consume(self, size: int) -> None:
        if size > self.remaining:
            raise ThreeMfExtractionError("archive exceeds extraction size limit")
        self.remaining -= size


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
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
_SLICER_PROJECT_SETTINGS = "Metadata/project_settings.config"
_SLICER_MODEL_SETTINGS = "Metadata/model_settings.config"


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
            budget = _ExtractionReadBudget(limits.max_total_uncompressed_bytes)
            metadata = _extract_metadata(archive, members, limits, budget)
            documents = _extract_documents(archive, members, limits, budget)
            build_colors, build_transforms = _extract_slicer_build_presentation(archive, members, limits, budget)
    except ThreeMfExtractionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ThreeMfExtractionError("invalid 3MF archive") from error

    return ThreeMfExtractionResult(
        metadata=MappingProxyType(metadata),
        documents=tuple(documents),
        build_colors=build_colors,
        build_transforms=build_transforms,
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
            limits.max_slicer_model_settings_bytes,
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
    budget: _ExtractionReadBudget,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    core_model = next((member for member in members if member.filename == "3D/3dmodel.model"), None)
    if core_model is None or core_model.file_size > limits.max_metadata_xml_bytes:
        return metadata
    xml_bytes = _read_member(archive, core_model, limits.max_metadata_xml_bytes, budget)
    lowered = xml_bytes.lower()
    if any(marker in lowered for marker in _XML_ENTITY_MARKERS):
        raise ThreeMfExtractionError("unsafe XML in 3MF metadata")
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as error:
        raise ThreeMfExtractionError("invalid 3MF metadata XML") from error
    if _local_name(root.tag) != "model":
        return metadata
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
    budget: _ExtractionReadBudget,
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
            text_content = _decode_utf8_text(_read_member(archive, member, limits.max_text_document_bytes, budget))
        documents.append(
            ThreeMfDocument(
                display_label=label,
                content_type=content_type,
                byte_size=member.file_size,
                text_content=text_content,
            )
        )
    return documents


def _extract_slicer_build_presentation(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    limits: ArchiveLimits,
    budget: _ExtractionReadBudget,
) -> tuple[tuple[str | None, ...], tuple[tuple[float, ...], ...]]:
    """Read Bambu/Orca slicer colors without exposing project configuration.

    Their project 3MFs store extruder colors outside core 3MF materials.  The
    returned tuple follows the core model's ``build/item`` order, matching the
    root children created by Three.js' ``ThreeMFLoader``.
    """
    member_by_name = {member.filename: member for member in members if not member.is_dir()}
    project_settings = member_by_name.get(_SLICER_PROJECT_SETTINGS)
    model_settings = member_by_name.get(_SLICER_MODEL_SETTINGS)
    core_model = member_by_name.get("3D/3dmodel.model")
    if project_settings is None or model_settings is None or core_model is None:
        return (), ()
    if (
        project_settings.file_size > limits.max_metadata_xml_bytes
        or model_settings.file_size > limits.max_slicer_model_settings_bytes
    ):
        return (), ()
    if core_model.file_size > limits.max_member_bytes:
        return (), ()

    try:
        project_data = json.loads(_read_member(archive, project_settings, limits.max_metadata_xml_bytes, budget))
    except (TypeError, ValueError, UnicodeDecodeError):
        return (), ()
    if not isinstance(project_data, dict):
        return (), ()
    raw_colors = project_data.get("filament_colour")
    if not isinstance(raw_colors, list):
        return (), ()
    colors = tuple(value if isinstance(value, str) and _HEX_COLOR.fullmatch(value) else None for value in raw_colors)
    if not any(colors):
        return (), ()

    settings_root = _parse_safe_xml(
        _read_member(archive, model_settings, limits.max_slicer_model_settings_bytes, budget)
    )
    model_root = _parse_safe_xml(_read_member(archive, core_model, limits.max_member_bytes, budget))
    if settings_root is None or model_root is None or _local_name(model_root.tag) != "model":
        return (), ()

    extruder_by_object_id: dict[str, int] = {}
    for object_node in settings_root.iter():
        object_id = object_node.attrib.get("id") if _local_name(object_node.tag) == "object" else None
        if not object_id:
            continue
        for node in object_node.iter():
            if _local_name(node.tag) != "metadata" or node.attrib.get("key") != "extruder":
                continue
            try:
                extruder_by_object_id[object_id] = int(node.attrib.get("value", ""))
            except ValueError:
                pass
            break

    build = next((node for node in model_root if _local_name(node.tag) == "build"), None)
    if build is None:
        return (), ()
    build_items = [item for item in build if _local_name(item.tag) == "item"]
    if not build_items:
        return (), ()
    build_colors: list[str | None] = []
    for item in build_items:
        extruder = extruder_by_object_id.get(item.attrib.get("objectid", ""))
        color_index = extruder - 1 if extruder is not None else -1
        build_colors.append(colors[color_index] if 0 <= color_index < len(colors) else None)
    return (
        tuple(build_colors) if any(build_colors) else (),
        _extract_assembly_transforms(settings_root, build_items),
    )


def _extract_assembly_transforms(
    settings_root: ElementTree.Element,
    build_items: list[ElementTree.Element],
) -> tuple[tuple[float, ...], ...]:
    """Return Bambu assembly transforms only when they cover the full build."""

    transforms_by_object_id: dict[str, tuple[float, ...]] = {}
    duplicate_object_ids: set[str] = set()
    for node in settings_root.iter():
        if _local_name(node.tag) != "assemble_item":
            continue
        object_id = node.attrib.get("object_id")
        transform = _parse_matrix(node.attrib.get("transform"))
        if not object_id or transform is None:
            continue
        if object_id in transforms_by_object_id:
            duplicate_object_ids.add(object_id)
            continue
        transforms_by_object_id[object_id] = transform

    transforms: list[tuple[float, ...]] = []
    for item in build_items:
        object_id = item.attrib.get("objectid", "")
        transform = transforms_by_object_id.get(object_id)
        if object_id in duplicate_object_ids or transform is None:
            return ()
        transforms.append(transform)
    return tuple(transforms)


def _parse_matrix(value: str | None) -> tuple[float, ...] | None:
    if value is None:
        return None
    try:
        matrix = tuple(float(item) for item in value.split())
    except ValueError:
        return None
    if len(matrix) != 12 or not all(math.isfinite(item) for item in matrix):
        return None
    return matrix


def _parse_safe_xml(xml_bytes: bytes) -> ElementTree.Element | None:
    if any(marker in xml_bytes.lower() for marker in _XML_ENTITY_MARKERS):
        raise ThreeMfExtractionError("unsafe XML in 3MF metadata")
    try:
        return ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return None


def _read_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    maximum: int,
    budget: _ExtractionReadBudget,
) -> bytes:
    if member.file_size > maximum:
        raise ThreeMfExtractionError("archive member exceeds extraction limit")
    try:
        with archive.open(member, "r") as stream:
            content = stream.read(maximum + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ThreeMfExtractionError("invalid 3MF archive member") from error
    if len(content) > maximum:
        raise ThreeMfExtractionError("archive member exceeds extraction limit")
    budget.consume(len(content))
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
