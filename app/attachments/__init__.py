"""Local text extraction from user attachments (PDF and DOCX)."""

from app.attachments.extractor import (
    MAX_DOCX_UNCOMPRESSED_BYTES,
    AttachmentError,
    AttachmentErrorReason,
    AttachmentKind,
    AttachmentLimits,
    ExtractedAttachment,
    UploadedFile,
    extract_attachments,
    read_limited,
    safe_attachment_name,
)

__all__ = [
    "MAX_DOCX_UNCOMPRESSED_BYTES",
    "AttachmentError",
    "AttachmentErrorReason",
    "AttachmentKind",
    "AttachmentLimits",
    "ExtractedAttachment",
    "UploadedFile",
    "extract_attachments",
    "read_limited",
    "safe_attachment_name",
]
