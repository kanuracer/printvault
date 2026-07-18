from __future__ import annotations

from io import BytesIO
import hashlib
import zipfile
from pathlib import Path

import pytest

from app.services.metadata import fingerprint_model
from app.services.thumbnails import ThumbnailCache


def _three_mf(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)


def test_thumbnail_cache_creates_deterministic_safe_svg_placeholder(tmp_path: Path) -> None:
    model = tmp_path / "unsafe name <script>.stl"
    model.write_text("solid empty\nendsolid empty\n", encoding="utf-8")
    fingerprint = fingerprint_model(model)
    cache = ThumbnailCache(tmp_path / "thumbnails")

    thumbnail = cache.create(model, fingerprint)

    assert thumbnail.kind == "placeholder"
    assert thumbnail.path == tmp_path / "thumbnails" / fingerprint.sha256[:2] / f"{fingerprint.sha256}.svg"
    svg = thumbnail.path.read_text(encoding="utf-8")
    assert fingerprint.sha256 in svg
    assert "unsafe name" not in svg
    assert "<script>" not in svg
    assert thumbnail.path == cache.create(model, fingerprint).path


def test_thumbnail_cache_extracts_only_a_safe_bounded_embedded_3mf_thumbnail(tmp_path: Path) -> None:
    model = tmp_path / "model.3mf"
    image = b"\x89PNG\r\n\x1a\nsmall-image"
    _three_mf(model, {"Metadata/thumbnail.png": image, "3D/3dmodel.model": b"<model/>"})
    fingerprint = fingerprint_model(model)

    thumbnail = ThumbnailCache(tmp_path / "thumbnails", max_member_bytes=128).create(model, fingerprint)

    assert thumbnail.kind == "embedded"
    assert thumbnail.path.suffix == ".png"
    assert thumbnail.path.read_bytes() == image


def test_thumbnail_cache_refuses_oversized_or_unsafe_zip_members_and_uses_placeholder(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.3mf"
    _three_mf(oversized, {"Metadata/thumbnail.png": b"x" * 129})
    unsafe = tmp_path / "unsafe.3mf"
    _three_mf(unsafe, {"../../thumbnail.png": b"png"})
    cache = ThumbnailCache(tmp_path / "thumbnails", max_member_bytes=128)

    oversized_result = cache.create(oversized, fingerprint_model(oversized))
    unsafe_result = cache.create(unsafe, fingerprint_model(unsafe))

    assert oversized_result.kind == "placeholder"
    assert unsafe_result.kind == "placeholder"
    assert oversized_result.path.suffix == unsafe_result.path.suffix == ".svg"


def test_thumbnail_cache_prefers_named_3mf_thumbnail_before_plate_one(tmp_path: Path) -> None:
    model = tmp_path / "named-first.3mf"
    plate_one = b"plate-one"
    named = b"named-thumbnail"
    _three_mf(
        model,
        {
            "Metadata/plate_01.webp": plate_one,
            "Metadata/thumbnail.png": named,
        },
    )

    thumbnail = ThumbnailCache(tmp_path / "thumbnails").create(model, fingerprint_model(model))

    assert thumbnail.kind == "embedded"
    assert thumbnail.path.suffix == ".png"
    assert thumbnail.path.read_bytes() == named


def test_thumbnail_cache_uses_only_plate_one_as_3mf_fallback(tmp_path: Path) -> None:
    plate_one = tmp_path / "plate-one.3mf"
    plate_two = tmp_path / "plate-two.3mf"
    _three_mf(plate_one, {"Metadata/plate-1.jpg": b"plate-one"})
    _three_mf(plate_two, {"Metadata/plate_2.png": b"plate-two"})
    cache = ThumbnailCache(tmp_path / "thumbnails")

    first_result = cache.create(plate_one, fingerprint_model(plate_one))
    second_result = cache.create(plate_two, fingerprint_model(plate_two))

    assert first_result.kind == "embedded"
    assert first_result.path.read_bytes() == b"plate-one"
    assert second_result.kind == "placeholder"


def test_manual_thumbnail_cache_uses_content_hash_in_a_server_owned_path(tmp_path: Path) -> None:
    image = b"\x89PNG\r\n\x1a\nmanual-thumbnail"
    cache = ThumbnailCache(tmp_path / "thumbnails")

    thumbnail = cache.store_manual(BytesIO(image), "image/png")

    digest = hashlib.sha256(image).hexdigest()
    assert thumbnail.sha256 == digest
    assert thumbnail.path == tmp_path / "thumbnails" / "manual" / digest[:2] / f"{digest}.png"
    assert thumbnail.path.read_bytes() == image


@pytest.mark.parametrize(
    ("image", "content_type"),
    [
        (b"not an image", "image/png"),
        (b"\x89PNG\r\n\x1a\npng", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\npng", "text/plain"),
    ],
)
def test_manual_thumbnail_cache_rejects_invalid_magic_or_mime(tmp_path: Path, image: bytes, content_type: str) -> None:
    with pytest.raises(ValueError):
        ThumbnailCache(tmp_path / "thumbnails").store_manual(BytesIO(image), content_type)


def test_manual_thumbnail_cache_enforces_eight_mebibyte_payload_limit(tmp_path: Path) -> None:
    image = b"\x89PNG\r\n\x1a\n" + b"x" * (8 * 1024 * 1024)

    with pytest.raises(ValueError):
        ThumbnailCache(tmp_path / "thumbnails").store_manual(BytesIO(image), "image/png")
