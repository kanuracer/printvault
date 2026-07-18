from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from app.services.metadata import extract_geometry, fingerprint_model


FIXTURES = Path(__file__).parent / "fixtures"


def test_fingerprint_streams_hash_and_records_size_mtime_and_normalized_format(tmp_path: Path) -> None:
    model = tmp_path / "Bracket.STL"
    contents = b"solid bracket\nendsolid bracket\n"
    model.write_bytes(contents)

    fingerprint = fingerprint_model(model, chunk_size=7)

    assert fingerprint.sha256 == hashlib.sha256(contents).hexdigest()
    assert fingerprint.byte_size == len(contents)
    assert fingerprint.mtime_ns == model.stat().st_mtime_ns
    assert fingerprint.format == "stl"


def test_ascii_and_binary_stl_geometry_reports_triangle_counts(tmp_path: Path) -> None:
    ascii_metadata = extract_geometry(FIXTURES / "two-facet.stl", "stl")
    binary = tmp_path / "one-triangle.stl"
    binary.write_bytes(b"binary".ljust(80, b" ") + struct.pack("<I", 1) + (b"\0" * 50))

    binary_metadata = extract_geometry(binary, "stl")

    assert ascii_metadata.triangle_count == 2
    assert binary_metadata.triangle_count == 1
    assert ascii_metadata.face_count is None
    assert binary_metadata.face_count is None


def test_obj_geometry_counts_faces_without_loading_an_untrusted_file(tmp_path: Path) -> None:
    model = tmp_path / "part.obj"
    model.write_bytes((FIXTURES / "two-faces.obj").read_bytes())

    metadata = extract_geometry(model, "obj")

    assert metadata.face_count == 2
    assert metadata.triangle_count is None


def test_malformed_or_limited_models_return_unknown_geometry_without_crashing(tmp_path: Path) -> None:
    malformed_stl = tmp_path / "broken.stl"
    malformed_stl.write_bytes(b"not actually an stl\xff\x00")
    limited_3mf = tmp_path / "limited.3mf"
    limited_3mf.write_bytes(b"not a zip")

    assert extract_geometry(malformed_stl, "stl").triangle_count is None
    assert extract_geometry(limited_3mf, "3mf").triangle_count is None
