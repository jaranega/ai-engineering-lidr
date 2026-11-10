"""Conversation session endpoints: HTTP validation and error mapping only."""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import BeforeValidator, ValidationError

from app.attachments import AttachmentError, UploadedFile, read_limited
from app.config import Settings, get_settings
from app.domain import (
    MAX_TRANSCRIPT_CHARS,
    DetailLevel,
    OutputFormat,
    ProjectType,
    SessionTurnInput,
)
from app.routers.estimations import PROVIDER_ERROR_DETAIL, build_estimation_response
from app.schemas import (
    AttachmentErrorResponse,
    SessionCreated,
    SessionErrorResponse,
    SessionEstimateResponse,
    SessionGuardrailErrorResponse,
    SessionInfo,
)
from app.services.llm_service import (
    DEFAULT_SESSION_PROMPT_VERSION,
    GenerationOptions,
    InputGuardrailViolation,
    InvalidOptionError,
    LLMProviderError,
    SessionPromptVersion,
)
from app.services.session_service import SessionService, get_session_service
from app.sessions import SessionBusyError, SessionNotFoundError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _drop_blank_parts(value: object) -> object:
    # A blank Swagger field arrives as an empty string part, not as a file.
    if isinstance(value, list):
        return [v for v in value if v != ""]
    return None if value == "" else value


_STATUS_BY_REASON = {
    "too_many_files": 413,
    "file_too_large": 413,
    "too_much_text": 413,
    "unsupported_type": 415,
}
_NOT_FOUND = {
    "reason": "session_not_found",
    "message": "Session not found or expired.",
}
_BUSY = {
    "reason": "session_busy",
    "message": "The session is processing another turn.",
}


def _attachment_http_error(exc: AttachmentError) -> HTTPException:
    detail = {"reason": exc.reason, "index": exc.index, "message": exc.message}
    return HTTPException(status_code=_STATUS_BY_REASON.get(exc.reason, 422), detail=detail)


def _read_uploads(files: list[UploadFile], settings: Settings) -> list[UploadedFile]:
    # Swagger UI sends an empty file part when the field is left blank: ignore it.
    present = [f for f in files if f.filename or (f.size or 0) > 0]
    max_files = settings.max_attachments
    max_bytes = settings.max_attachment_bytes
    if len(present) > max_files:
        raise AttachmentError("too_many_files")
    uploads: list[UploadedFile] = []
    for index, upload in enumerate(present):
        if (upload.size or 0) > max_bytes:
            raise AttachmentError("file_too_large", index)
        uploads.append(
            UploadedFile(
                filename=upload.filename or "",
                data=read_limited(upload.file, max_bytes, index),
            )
        )
    return uploads


@router.post(
    "",
    status_code=201,
    response_model=SessionCreated,
    summary="Create a conversation session",
    responses={201: {"description": "Session created."}},
)
def create_session(
    service: Annotated[SessionService, Depends(get_session_service)],
) -> SessionCreated:
    return SessionCreated(session_id=service.create())


@router.get(
    "/{session_id}",
    response_model=SessionInfo,
    summary="Get the state of a session",
    responses={404: {"model": SessionErrorResponse, "description": "Session not found."}},
)
def get_session(
    session_id: UUID,
    service: Annotated[SessionService, Depends(get_session_service)],
) -> SessionInfo:
    try:
        snapshot = service.info(str(session_id))
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    return SessionInfo(
        session_id=snapshot.session_id,
        turn_count=snapshot.turn_count,
        history_turns=snapshot.history_turns,
        max_turns=snapshot.max_turns,
        project_metadata=snapshot.project_metadata,
    )


@router.post(
    "/{session_id}/estimate",
    response_model=SessionEstimateResponse,
    summary="Run one turn of a session (multipart, optional PDF/DOCX attachments)",
    responses={
        400: {
            "model": SessionGuardrailErrorResponse,
            "description": "Input blocked by the guardrails (transcript or attachment).",
        },
        404: {"model": SessionErrorResponse, "description": "Session not found."},
        409: {"model": SessionErrorResponse, "description": "Session busy with another turn."},
        413: {
            "model": AttachmentErrorResponse,
            "description": "Too many files, file too large or too much extracted text.",
        },
        415: {"model": AttachmentErrorResponse, "description": "Unsupported attachment type."},
        422: {
            "description": "Invalid form (FastAPI validation shape) or unreadable, "
            "encrypted or empty attachment (`AttachmentErrorResponse` shape)."
        },
        502: {"description": "The LLM provider failed (generic error, no details)."},
    },
)
def estimate_turn(
    session_id: UUID,
    service: Annotated[SessionService, Depends(get_session_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    transcript: Annotated[str, Form(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)],
    project_type: Annotated[ProjectType, Form()],
    detail_level: Annotated[DetailLevel, Form()],
    output_format: Annotated[OutputFormat, Form()],
    attachments: Annotated[
        list[UploadFile] | None,
        BeforeValidator(_drop_blank_parts),
        File(description="Optional .pdf / .docx files."),
    ] = None,
    prompt_version: Annotated[
        SessionPromptVersion, Query(description="Version of the prompt templates to render.")
    ] = DEFAULT_SESSION_PROMPT_VERSION,
) -> SessionEstimateResponse:
    try:
        turn = SessionTurnInput(
            transcript=transcript,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
        )
    except ValidationError as exc:
        error = {
            "loc": ("body", "transcript"),
            "msg": exc.errors()[0]["msg"],
            "type": "value_error",
        }
        raise RequestValidationError([error]) from exc
    try:
        uploads = _read_uploads(attachments or [], settings)
    except AttachmentError as exc:
        logger.warning("attachment_rejected", reason=exc.reason, index=exc.index)
        raise _attachment_http_error(exc) from exc
    try:
        outcome = service.estimate(
            str(session_id), turn, uploads, GenerationOptions(prompt_version=prompt_version)
        )
    except AttachmentError as exc:
        # The service already logs `attachment_rejected`.
        raise _attachment_http_error(exc) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=_BUSY) from exc
    except InvalidOptionError as exc:
        location = "query" if exc.field == "prompt_version" else "body"
        error = {"loc": (location, exc.field), "msg": exc.message, "type": "value_error"}
        raise RequestValidationError([error]) from exc
    except InputGuardrailViolation as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": exc.reason,
                "message": exc.message,
                "field": exc.field if exc.field in ("transcript", "attachments") else "input",
                "index": exc.index,
            },
        ) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=PROVIDER_ERROR_DETAIL) from exc
    base = build_estimation_response(outcome.outcome)
    return SessionEstimateResponse(
        **base.model_dump(),
        session_id=outcome.session_id,
        turn=outcome.turn,
        project_metadata=outcome.project_metadata,
    )
