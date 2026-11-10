"""Extract text from PDF and DOCX attachments, in process and without content in errors."""

import re
import unicodedata
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO, Literal

import structlog
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PasswordType, PdfReader

from app.guardrails.input import mask_pii

logger = structlog.get_logger(__name__)

AttachmentKind = Literal["pdf", "docx"]
AttachmentErrorReason = Literal[
    "too_many_files",
    "file_too_large",
    "too_much_text",
    "unsupported_type",
    "unreadable",
    "encrypted",
    "no_text",
]

MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 1000
_MAX_NAME_CHARS = 80
_PDF_MAGIC_WINDOW = 1024


@dataclass(frozen=True)
class AttachmentLimits:
    max_files: int
    max_bytes: int
    max_chars: int


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    data: bytes


@dataclass(frozen=True)
class ExtractedAttachment:
    index: int
    name: str
    kind: AttachmentKind
    text: str
    size_bytes: int
    pages: int | None


def _message(reason: AttachmentErrorReason, limits: AttachmentLimits | None) -> str:
    if reason == "too_many_files":
        limit = f" (maximum {limits.max_files})" if limits else ""
        return f"Too many attachments{limit}."
    if reason == "file_too_large":
        limit = f" (maximum {limits.max_bytes} bytes)" if limits else ""
        return f"Attachment is too large{limit}."
    if reason == "too_much_text":
        limit = f" (maximum {limits.max_chars} characters in total)" if limits else ""
        return f"Attachments contain too much text{limit}."
    return {
        "unsupported_type": "Unsupported attachment type: only PDF and DOCX are accepted.",
        "unreadable": "The attachment could not be read.",
        "encrypted": "The attachment is encrypted and could not be decrypted.",
        "no_text": "The attachment contains no extractable text (no OCR).",
    }[reason]


class AttachmentError(Exception):
    """Attachment rejected. The message is fixed text: no file name, no content."""

    def __init__(
        self,
        reason: AttachmentErrorReason,
        index: int | None = None,
        limits: AttachmentLimits | None = None,
        *,
        kind: AttachmentKind | None = None,
        size_bytes: int | None = None,
        error_type: str | None = None,
    ) -> None:
        self.reason = reason
        self.index = index
        self.message = _message(reason, limits)
        self.kind = kind
        self.size_bytes = size_bytes
        self.error_type = error_type
        super().__init__(self.message)


def read_limited(stream: BinaryIO, max_bytes: int, index: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes; raise ``file_too_large`` if the file exceeds it."""
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AttachmentError(
            "file_too_large",
            index,
            AttachmentLimits(max_files=0, max_bytes=max_bytes, max_chars=0),
            size_bytes=len(data),
        )
    return data


def safe_attachment_name(raw: str, index: int, kind: AttachmentKind) -> str:
    base = re.split(r"[/\\]", raw)[-1]
    # Mask PII in the stem only: the email pattern would otherwise swallow ".pdf".
    stem, dot, ext = base.rpartition(".")
    name = mask_pii(stem)[0] + dot + ext if dot and stem else mask_pii(base)[0]
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf"))
    name = re.sub(r"[^\w .,()+\[\]-]", "_", name)
    name = re.sub(r"-{3,}", "-", name)
    name = re.sub(r" +", " ", name).strip()
    if len(name) > _MAX_NAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[: _MAX_NAME_CHARS - len(ext) - 1] + "." + ext
        else:
            name = name[:_MAX_NAME_CHARS]
    return name or f"attachment-{index + 1}.{kind}"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_kind(file: UploadedFile) -> AttachmentKind | None:
    name = file.filename.lower()
    data = file.data
    if name.endswith(".pdf") and b"%PDF-" in data[:_PDF_MAGIC_WINDOW]:
        return "pdf"
    if name.endswith(".docx") and data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            return None
    return None


class _Budget:
    """Running character total across the attachments of one turn."""

    def __init__(self, limits: AttachmentLimits) -> None:
        self.limits = limits
        self.used = 0

    def add(self, chunk: str, index: int, kind: AttachmentKind, size: int) -> None:
        self.used += len(chunk)
        if self.used > self.limits.max_chars:
            raise AttachmentError("too_much_text", index, self.limits, kind=kind, size_bytes=size)


def _extract_pdf(data: bytes, index: int, budget: _Budget) -> tuple[list[str], int]:
    size = len(data)
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        raise AttachmentError("encrypted", index, kind="pdf", size_bytes=size)
    chunks: list[str] = []
    pages = reader.pages
    for page in pages:
        chunk = _normalize(page.extract_text() or "")
        budget.add(chunk, index, "pdf", size)
        chunks.append(chunk)
    return chunks, len(pages)


def _row_text(table: Table) -> list[str]:
    lines: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            if previous is not None and cell._tc is previous:
                continue
            previous = cell._tc
            cells.append(cell.text.strip())
        lines.append(" | ".join(cells))
    return lines


def _check_docx_zip(data: bytes, index: int) -> None:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        if (
            len(infos) > MAX_DOCX_ENTRIES
            or sum(i.file_size for i in infos) > MAX_DOCX_UNCOMPRESSED_BYTES
        ):
            raise AttachmentError("unreadable", index, kind="docx", size_bytes=len(data))


def _extract_docx(data: bytes, index: int, budget: _Budget) -> list[str]:
    size = len(data)
    _check_docx_zip(data, index)
    document = Document(BytesIO(data))
    chunks: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            chunk = block.text
        elif isinstance(block, Table):
            chunk = "\n".join(_row_text(block))
        else:
            continue
        chunk = _normalize(chunk)
        if not chunk:
            continue
        budget.add(chunk, index, "docx", size)
        chunks.append(chunk)
    return chunks


def extract_attachments(
    files: Sequence[UploadedFile], limits: AttachmentLimits
) -> list[ExtractedAttachment]:
    if len(files) > limits.max_files:
        _reject(AttachmentError("too_many_files", None, limits))
    budget = _Budget(limits)
    results: list[ExtractedAttachment] = []
    for index, file in enumerate(files):
        size = len(file.data)
        try:
            if size > limits.max_bytes:
                raise AttachmentError("file_too_large", index, limits, size_bytes=size)
            kind = _detect_kind(file)
            if kind is None:
                raise AttachmentError("unsupported_type", index, size_bytes=size)
            pages: int | None = None
            try:
                if kind == "pdf":
                    chunks, pages = _extract_pdf(file.data, index, budget)
                else:
                    chunks = _extract_docx(file.data, index, budget)
            except AttachmentError:
                raise
            except Exception as exc:
                reason: AttachmentErrorReason = (
                    "encrypted" if type(exc).__name__ == "DependencyError" else "unreadable"
                )
                raise AttachmentError(
                    reason, index, kind=kind, size_bytes=size, error_type=type(exc).__name__
                ) from exc
            text = _normalize("\n\n".join(c for c in chunks if c))
            if not text:
                raise AttachmentError("no_text", index, kind=kind, size_bytes=size)
        except AttachmentError as err:
            _reject(err)
        result = ExtractedAttachment(
            index=index,
            name=safe_attachment_name(file.filename, index, kind),
            kind=kind,
            text=text,
            size_bytes=size,
            pages=pages,
        )
        logger.info(
            "attachment_extracted",
            index=index,
            kind=kind,
            size_bytes=size,
            chars=len(text),
            pages=pages,
        )
        results.append(result)
    return results


def _reject(err: AttachmentError) -> None:
    fields: dict[str, object] = {"index": err.index, "reason": err.reason}
    if err.kind:
        fields["kind"] = err.kind
    if err.size_bytes is not None:
        fields["size_bytes"] = err.size_bytes
    if err.error_type:
        fields["error_type"] = err.error_type
    logger.warning("attachment_rejected", **fields)
    raise err
