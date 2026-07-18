from __future__ import annotations

import zipfile
from pathlib import Path

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
