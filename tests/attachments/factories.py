"""Builders of PDF and DOCX attachments for the tests (no binary fixtures on disk)."""

import zipfile
from io import BytesIO

from docx import Document
from pypdf import PdfReader, PdfWriter

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]] | list[str] | str) -> bytes:
    """Hand-written PDF with one Helvetica text page per entry (a list of lines each).

    A plain string is one page with one line; a list of strings is one page with those lines.
    """
    if isinstance(pages, str):
        pages = [[pages]]
    elif pages and isinstance(pages[0], str):
        pages = [list(pages)]  # type: ignore[list-item]
    objs: list[bytes] = []
    count = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    font_id = 3 + 2 * count
    for i, lines in enumerate(pages):
        content_id = 4 + 2 * i
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        ops = "BT /F1 12 Tf 14 TL 72 720 Td " + " T* ".join(
            f"({_escape(line)}) Tj" for line in lines
        )
        stream = (ops + " ET").encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def make_blank_pdf() -> bytes:
    """PDF with one page and no text (what a scanned document looks like without OCR)."""
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_encrypted_pdf(data: bytes | None = None, password: str = "secret") -> bytes:
    """RC4-128 PDF with a non-empty user password (AES would need `cryptography`)."""
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data or make_pdf([["Secret text"]]))))
    writer.encrypt(password, algorithm="RC4-128")
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_corrupt_pdf() -> bytes:
    """Starts like a PDF but the rest is garbage."""
    return b"%PDF-1.4\n" + b"\x00\xffthis is not a pdf at all\x01" * 20


def make_docx(
    paragraphs: list[str] | None = None,
    table: list[list[str]] | None = None,
    *,
    header: str | None = None,
) -> bytes:
    """DOCX with the paragraphs, then an optional table, built with python-docx."""
    document = Document()
    for text in paragraphs or []:
        document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    if header:
        document.sections[0].header.paragraphs[0].text = header
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_docx_with_merged_row(cells: list[str]) -> bytes:
    """DOCX whose only table row has its first two cells merged into one."""
    document = Document()
    grid = document.add_table(rows=1, cols=len(cells))
    grid.cell(0, 0).merge(grid.cell(0, 1))
    grid.cell(0, 0).text = cells[0]
    for c, value in enumerate(cells[2:], start=2):
        grid.cell(0, c).text = value
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_zip_without_document() -> bytes:
    """A generic zip: starts with `PK\\x03\\x04` but is not a Word document."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    return buffer.getvalue()
