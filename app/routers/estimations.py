"""Estimation endpoints: HTTP validation and error mapping only."""

from collections.abc import Generator, Iterator
from dataclasses import asdict
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.config import Settings, get_settings
from app.schemas import (
    ContextResponse,
    EstimationMetrics,
    EstimationRequest,
    EstimationResponse,
    StreamEstimationRequest,
    StructureCheckResponse,
)
from app.services.llm_service import (
    MAX_TRANSCRIPTION_CHARS,
    EstimationResult,
    EstimationService,
    GenerationOptions,
    InvalidOptionError,
    LLMProvider,
    LLMProviderError,
    get_llm_provider,
    prompt_context,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["estimations"])

PROVIDER_ERROR_DETAIL = "LLM provider error"


def get_estimation_service(
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EstimationService:
    return EstimationService(provider, resolve_model=settings.resolve_request_model)


def _metrics(result: EstimationResult) -> EstimationMetrics:
    return EstimationMetrics(
        model=result.model,
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        truncated=result.truncated,
        fallback_used=result.fallback_used,
        cache_hit=result.cache_hit,
        latency_ms=result.latency_ms,
    )


@router.post(
    "/estimate",
    response_model=EstimationResponse,
    summary="Generate a software estimation from a meeting transcription",
    responses={
        422: {"description": "Invalid body or generation option (including an unusable `model`)."},
        502: {"description": "The LLM provider failed (generic error, no details)."},
    },
)
def estimate(
    request: EstimationRequest,
    service: Annotated[EstimationService, Depends(get_estimation_service)],
) -> EstimationResponse:
    options = GenerationOptions(
        preprocessing=request.preprocessing,
        example_format=request.example_format,
        num_examples=request.num_examples,
        use_examples=request.use_examples,
        model=request.model,
        max_tokens=request.max_tokens,
        thinking_budget=request.thinking_budget,
        evaluate=request.evaluate,
    )
    try:
        result = service.estimate(request.transcription, options)
    except InvalidOptionError as exc:
        error = {"loc": ("body", exc.field), "msg": exc.message, "type": "value_error"}
        raise RequestValidationError([error]) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=PROVIDER_ERROR_DETAIL) from exc
    validation = (
        StructureCheckResponse(
            **{**asdict(result.validation), "issues": list(result.validation.issues)}
        )
        if result.validation
        else None
    )
    return EstimationResponse(
        estimation=result.text,
        preprocessing=result.preprocessing,
        extracted_requirements=result.extracted_requirements,
        preprocessing_input_tokens=result.preprocessing_input_tokens,
        preprocessing_output_tokens=result.preprocessing_output_tokens,
        preprocessing_cost_usd=result.preprocessing_cost_usd,
        validation=validation,
        **_metrics(result).model_dump(),
    )


def estimate_stream_events(
    request: StreamEstimationRequest, service: EstimationService
) -> Generator[ServerSentEvent, None, None]:
    stream = service.stream_chat(request.chat_messages())
    sent = 0
    try:
        for fragment in stream:
            sent += 1
            yield ServerSentEvent(event="token", data=fragment)
    except GeneratorExit:
        logger.info("estimate_stream_cancelled", fragments=sent)
        raise
    except Exception as exc:  # LLMProviderError or a bug: the stream needs a terminal event
        if not isinstance(exc, LLMProviderError):
            logger.error("estimate_stream_failed", error_type=type(exc).__name__)
        yield ServerSentEvent(event="error", data={"detail": PROVIDER_ERROR_DETAIL})
        return
    yield ServerSentEvent(event="metrics", data=_metrics(stream.result).model_dump())


def stream_closer() -> Iterator[list[Generator[ServerSentEvent, None, None]]]:
    """Close the registered SSE generators when the request ends.

    FastAPI's native SSE does not close the endpoint generator when the client
    disconnects; yield dependencies are torn down after the response, also on a
    disconnect, so closing here raises GeneratorExit and the LLM stream is closed.
    """
    streams: list[Generator[ServerSentEvent, None, None]] = []
    try:
        yield streams
    finally:
        for stream in streams:
            stream.close()


@router.post(
    "/estimate/stream",
    response_class=EventSourceResponse,
    summary="Stream a software estimation as Server-Sent Events",
    description="Events: `token` (JSON string with a text fragment), then a terminal "
    '`metrics` (EstimationMetrics) or `error` (`{"detail": "LLM provider error"}`).',
)
def estimate_stream(
    request: StreamEstimationRequest,
    service: Annotated[EstimationService, Depends(get_estimation_service)],
    streams: Annotated[list[Generator[ServerSentEvent, None, None]], Depends(stream_closer)],
) -> Iterator[ServerSentEvent]:
    events = estimate_stream_events(request, service)
    streams.append(events)
    yield from events


@router.get(
    "/context",
    response_model=ContextResponse,
    summary="CAG context used for a conversation with the given number of messages",
)
def context(message_count: Annotated[int, Query(ge=1)] = 1) -> ContextResponse:
    ctx = prompt_context(message_count)
    return ContextResponse(
        system_prompt=ctx.system_prompt,
        examples=list(ctx.examples),
        max_message_chars=MAX_TRANSCRIPTION_CHARS,
    )
