"""Indexed inline images must not discard the surrounding page text.

Reproduces the pypdf 5.1.0 ArrayObject failure seen on KR20240147667A p.19
without storing the user's document in the test suite.
"""

from io import BytesIO

import pypdf
import pytest
from pypdf.generic import DecodedStreamObject, NameObject

from app.ingestion.service import extract_pdf
from app.retrieval import index as index_module
from app.retrieval.extraction import STATUS_OK, extract_document
from app.retrieval.versions import EXTRACTOR_VERSION

from .pdf_fixture import build_pdf


@pytest.fixture
def indexed_image_pdf(tmp_path):
    reader = pypdf.PdfReader(BytesIO(build_pdf([
        "Text before the indexed image.",
        "The following page must also survive.",
    ])))
    writer = pypdf.PdfWriter()
    writer.append_pages_from_reader(reader)
    page = writer.pages[0]
    stream = DecodedStreamObject()
    # The real failing image is 15x15, 1 bit per pixel with an array /CS.
    # Each row occupies ceil(15/8) bytes, so the image has 30 bytes.
    stream.set_data(
        page.get_contents().get_data()
        + b"\nq\nBI /W 15 /H 15 /BPC 1 /CS [/I /RGB 1 <000000FFFFFF>] ID\n"
        + b"\x00\x00" * 15
        + b"\nEI\nQ\nBT /F1 12 Tf 72 680 Td (Text after the indexed image.) Tj ET\n"
    )
    page[NameObject("/Contents")] = stream
    path = tmp_path / "indexed-inline-image.pdf"
    writer.write(path)
    return path


@pytest.mark.parametrize("mode", ["plain", "layout"])
def test_inline_colorspace_array_keeps_surrounding_text(indexed_image_pdf, mode):
    page = pypdf.PdfReader(indexed_image_pdf).pages[0]
    text = page.extract_text(extraction_mode=mode)
    assert "Text before the indexed image." in text
    assert "Text after the indexed image." in text


def test_ingestion_keeps_page_with_indexed_inline_image(indexed_image_pdf):
    text, count, error = extract_pdf(indexed_image_pdf)
    assert count == 2
    assert error is None
    assert "Text before the indexed image." in text
    assert "Text after the indexed image." in text
    assert "The following page must also survive." in text
    assert "추출 실패" not in text


def test_retrieval_indexes_page_with_indexed_inline_image(indexed_image_pdf):
    result = extract_document(
        indexed_image_pdf, attachment_id="inline-image", filename=indexed_image_pdf.name,
        sha256="test-inline-colorspace",
    )
    assert result.source_page_count == result.processed_page_count == 2
    assert result.report()["extraction_failed_pages"] == []
    assert all(page.status == STATUS_OK for page in result.pages)
    assert "Text before the indexed image." in result.pages[0].text
    assert "Text after the indexed image." in result.pages[0].text
    assert "The following page must also survive." in result.pages[1].text


def test_old_failed_index_is_rebuilt_after_extractor_upgrade(indexed_image_pdf, monkeypatch):
    from app.retrieval.extraction import DocumentExtraction, PageRecord, STATUS_FAILED

    path = indexed_image_pdf.with_suffix(".sqlite3")
    old_result = DocumentExtraction(
        attachment_id="inline-image", filename=indexed_image_pdf.name,
        sha256="unchanged-pdf", source_page_count=2,
        pages=[
            PageRecord(page_number=1, status=STATUS_FAILED,
                       extraction_error="TypeError: unhashable type: 'ArrayObject'"),
            PageRecord(page_number=2, text="The following page must also survive."),
        ],
    )
    with monkeypatch.context() as old:
        old.setattr(index_module, "EXTRACTOR_VERSION", "pypdf-5.1.0+prism-1")
        index_module.build_index(path, old_result)

    def extract():
        return extract_document(
            indexed_image_pdf, attachment_id="inline-image",
            filename=indexed_image_pdf.name, sha256="unchanged-pdf",
        )

    index, report, rebuilt = index_module.ensure_index(path, extract, sha256="unchanged-pdf")
    with index:
        assert rebuilt
        assert index.fingerprint()["extractor_version"] == EXTRACTOR_VERSION
        assert report["extraction_failed_pages"] == []
        text = "\n".join(row.text for row in index.page_rows(1))
        assert "Text before the indexed image." in text
        assert "Text after the indexed image." in text
