from __future__ import annotations

import io
import stat
import zipfile

import pytest

from app.services.three_mf_metadata import (
    ArchiveLimits,
    ThreeMfExtractionError,
    extract_three_mf_metadata,
)


CORE_MODEL = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<model xmlns=\"http://schemas.microsoft.com/3dmanufacturing/core/2015/02\">
  <metadata name=\"Title\">  Calibration Cube  </metadata>
  <metadata name=\"Designer\">PrintVault Team</metadata>
  <metadata name=\"Description\">A small test model.</metadata>
  <metadata name=\"internal-token\">top-secret-value</metadata>
</model>
"""


SLICER_MODEL = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<model xmlns=\"http://schemas.microsoft.com/3dmanufacturing/core/2015/02\">
  <resources>
    <object id=\"1\" type=\"model\"><mesh /></object>
    <object id=\"2\" type=\"model\"><mesh /></object>
  </resources>
  <build>
    <item objectid=\"2\" />
    <item objectid=\"1\" />
  </build>
</model>
"""


SLICER_MODEL_SETTINGS = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<config>
  <object id=\"1\"><metadata key=\"extruder\" value=\"1\" /></object>
  <object id=\"2\"><metadata key=\"extruder\" value=\"2\" /></object>
</config>
"""


SLICER_MODEL_SETTINGS_WITH_ASSEMBLY = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<config>
  <object id=\"1\"><metadata key=\"extruder\" value=\"1\" /></object>
  <object id=\"2\"><metadata key=\"extruder\" value=\"2\" /></object>
  <assemble_item object_id=\"1\" transform=\"1 0 0 0 1 0 0 0 1 10 20 30\" />
  <assemble_item object_id=\"2\" transform=\"1 0 0 0 1 0 0 0 1 40 50 60\" />
</config>
"""


def three_mf(entries: dict[str, bytes], *, symlink_name: str | None = None) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
        if symlink_name is not None:
            link = zipfile.ZipInfo(symlink_name)
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, b"target")
    return archive.getvalue()


def test_extracts_whitelisted_core_metadata_and_safe_instruction_documents() -> None:
    result = extract_three_mf_metadata(
        three_mf(
            {
                "3D/3dmodel.model": CORE_MODEL.encode("utf-8"),
                "docs/private/assembly-guide.md": b"# Assemble\nUse two screws.\n",
                "docs/print.pdf": b"%PDF-1.7 binary document",
                "3D/thumbnail.png": b"\x89PNG\r\n\x1a\n",
            }
        )
    )

    assert dict(result.metadata) == {
        "title": "Calibration Cube",
        "designer": "PrintVault Team",
        "description": "A small test model.",
    }
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.display_label == "assembly-guide.md"
    assert document.content_type == "text/markdown"
    assert document.byte_size == len(b"# Assemble\nUse two screws.\n")
    assert document.text_content == "# Assemble\nUse two screws.\n"
    assert "private/" not in repr(result)
    assert "top-secret-value" not in repr(result)
    with pytest.raises(TypeError):
        result.metadata["title"] = "changed"  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.documents.append(document)  # type: ignore[attr-defined]


def test_lists_instruction_pdf_without_returning_binary_bytes() -> None:
    pdf = b"%PDF-1.7\x00not text"

    result = extract_three_mf_metadata(three_mf({"instructions/print-manual.pdf": pdf}))

    assert result.metadata == {}
    assert result.documents[0].display_label == "print-manual.pdf"
    assert result.documents[0].content_type == "application/pdf"
    assert result.documents[0].byte_size == len(pdf)
    assert result.documents[0].text_content is None
    assert pdf.decode("latin-1") not in repr(result)


def test_extracts_bambu_slicer_filament_colours_in_build_order() -> None:
    result = extract_three_mf_metadata(
        three_mf(
            {
                "3D/3dmodel.model": SLICER_MODEL.encode("utf-8"),
                "Metadata/project_settings.config": b'{"filament_colour":["#ff0000","#00aa00"]}',
                "Metadata/model_settings.config": SLICER_MODEL_SETTINGS.encode("utf-8"),
            }
        )
    )

    assert result.build_colors == ("#00aa00", "#ff0000")


def test_extracts_bambu_assembly_transforms_in_core_build_order() -> None:
    result = extract_three_mf_metadata(
        three_mf(
            {
                "3D/3dmodel.model": SLICER_MODEL.encode("utf-8"),
                "Metadata/project_settings.config": b'{"filament_colour":["#ff0000","#00aa00"]}',
                "Metadata/model_settings.config": SLICER_MODEL_SETTINGS_WITH_ASSEMBLY.encode("utf-8"),
            }
        )
    )

    assert result.build_transforms == (
        (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 40.0, 50.0, 60.0),
        (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0),
    )


def test_extracts_bambu_colours_when_core_model_exceeds_metadata_limit() -> None:
    padded_model = SLICER_MODEL.replace("<resources>", f"<!-- {'x' * 600_000} --><resources>")

    result = extract_three_mf_metadata(
        three_mf(
            {
                "3D/3dmodel.model": padded_model.encode("utf-8"),
                "Metadata/project_settings.config": b'{"filament_colour":["#ff0000","#00aa00"]}',
                "Metadata/model_settings.config": SLICER_MODEL_SETTINGS.encode("utf-8"),
            }
        )
    )

    assert result.build_colors == ("#00aa00", "#ff0000")


def test_extracts_bambu_colours_when_ignored_members_exceed_total_read_limit() -> None:
    result = extract_three_mf_metadata(
        three_mf(
            {
                "3D/3dmodel.model": SLICER_MODEL.encode("utf-8"),
                "Metadata/project_settings.config": b'{"filament_colour":["#ff0000","#00aa00"]}',
                "Metadata/model_settings.config": SLICER_MODEL_SETTINGS.encode("utf-8"),
                "3D/objects/ignored.model": b"unread mesh payload" * 200,
            }
        ),
        limits=ArchiveLimits(
            max_archive_bytes=10_000,
            max_member_bytes=10_000,
            max_total_uncompressed_bytes=2_000,
        ),
    )

    assert result.build_colors == ("#00aa00", "#ff0000")


def test_extracts_bambu_colours_when_model_settings_exceed_core_metadata_limit() -> None:
    padded_settings = SLICER_MODEL_SETTINGS.replace("<config>", f"<!-- {'x' * 600_000} --><config>")

    result = extract_three_mf_metadata(
        three_mf(
            {
                "3D/3dmodel.model": SLICER_MODEL.encode("utf-8"),
                "Metadata/project_settings.config": b'{"filament_colour":["#ff0000","#00aa00"]}',
                "Metadata/model_settings.config": padded_settings.encode("utf-8"),
            }
        )
    )

    assert result.build_colors == ("#00aa00", "#ff0000")


def test_does_not_read_text_documents_over_the_configured_content_bound() -> None:
    text = b"a" * 11
    limits = ArchiveLimits(max_archive_bytes=10_000, max_text_document_bytes=10)

    result = extract_three_mf_metadata(three_mf({"docs/readme.txt": text}), limits=limits)

    assert result.documents[0].content_type == "text/plain"
    assert result.documents[0].byte_size == 11
    assert result.documents[0].text_content is None


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/absolute.txt",
        "C:/windows-drive.txt",
        "docs\\backslash.txt",
    ],
)
def test_rejects_archives_with_unsafe_member_names(name: str) -> None:
    with pytest.raises(ThreeMfExtractionError, match="unsafe archive member"):
        extract_three_mf_metadata(three_mf({name: b"not used"}))


def test_rejects_nul_in_raw_archive_member_names() -> None:
    safe_name = b"docs/Xnul.txt"
    unsafe_name = b"docs/\x00nul.txt"
    archive = three_mf({safe_name.decode("ascii"): b"not used"}).replace(safe_name, unsafe_name)

    with pytest.raises(ThreeMfExtractionError, match="unsafe archive member"):
        extract_three_mf_metadata(archive)


def test_rejects_symbolic_link_members() -> None:
    with pytest.raises(ThreeMfExtractionError, match="unsafe archive member"):
        extract_three_mf_metadata(three_mf({}, symlink_name="docs/instructions.txt"))


def test_rejects_archives_exceeding_member_and_size_limits() -> None:
    archive = three_mf({"a.txt": b"a", "b.txt": b"b"})

    with pytest.raises(ThreeMfExtractionError, match="too many archive members"):
        extract_three_mf_metadata(archive, limits=ArchiveLimits(max_archive_bytes=10_000, max_members=1))
    with pytest.raises(ThreeMfExtractionError, match="archive member exceeds size limit"):
        extract_three_mf_metadata(
            three_mf({"docs/readme.txt": b"abc"}),
            limits=ArchiveLimits(max_archive_bytes=10_000, max_member_bytes=2),
        )
    with pytest.raises(ThreeMfExtractionError, match="archive exceeds size limit"):
        extract_three_mf_metadata(archive, limits=ArchiveLimits(max_archive_bytes=len(archive) - 1))


def test_rejects_xml_entities_before_parsing_metadata() -> None:
    entity_model = b"""<!DOCTYPE model [<!ENTITY secret 'expanded'>]>
    <model xmlns='http://schemas.microsoft.com/3dmanufacturing/core/2015/02'>
      <metadata name='Title'>&secret;</metadata>
    </model>"""

    with pytest.raises(ThreeMfExtractionError, match="unsafe XML"):
        extract_three_mf_metadata(three_mf({"3D/3dmodel.model": entity_model}))
