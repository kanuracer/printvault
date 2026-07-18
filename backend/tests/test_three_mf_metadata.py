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
