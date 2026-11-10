"""ci-pipeline / Requirement: Cobertura de sesiones y adjuntos / Scenario: Fixture de PDF válido.

The PDF and DOCX fixtures are generated in code and validated with the same libraries the
extractor uses.
"""

from io import BytesIO

from docx import Document
from pypdf import PdfReader

from tests.attachments.factories import make_blank_pdf, make_docx, make_encrypted_pdf, make_pdf


def test_generated_pdf_with_two_lines_is_readable_by_pypdf():
    reader = PdfReader(BytesIO(make_pdf(["Linea uno", "Linea dos"])))
    text = reader.pages[0].extract_text()
    assert "Linea uno" in text and "Linea dos" in text


def test_generated_pdf_pages_and_encryption_flags():
    assert len(PdfReader(BytesIO(make_pdf([["a"], ["b"], ["c"]]))).pages) == 3
    assert PdfReader(BytesIO(make_blank_pdf())).pages[0].extract_text().strip() == ""
    assert PdfReader(BytesIO(make_encrypted_pdf())).is_encrypted


def test_generated_docx_is_readable_by_python_docx():
    document = Document(BytesIO(make_docx(["Parrafo"], table=[["A", "B"], ["1", "2"]])))
    assert [p.text for p in document.paragraphs] == ["Parrafo"]
    assert [[c.text for c in row.cells] for row in document.tables[0].rows] == [
        ["A", "B"],
        ["1", "2"],
    ]
