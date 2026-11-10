"""Estimation endpoint: HTTP validation and error mapping only."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError

from app.config import Settings, get_settings
from app.schemas import (
    EstimationRequest,
    EstimationResponse,
    GuardrailErrorResponse,
    StructureCheckResponse,
)
from app.services.llm_service import (
    DEFAULT_PROMPT_VERSION,
    EstimationOutcome,
    EstimationService,
    GenerationOptions,
    InputGuardrailViolation,
    InvalidOptionError,
    LLMProvider,
    LLMProviderError,
    Moderator,
    PromptVersion,
    get_llm_provider,
    get_moderator,
)

router = APIRouter(tags=["estimations"])

PROVIDER_ERROR_DETAIL = "LLM provider error"


def get_estimation_service(
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    moderator: Annotated[Moderator, Depends(get_moderator)],
) -> EstimationService:
    return EstimationService(
        provider, resolve_model=settings.resolve_request_model, moderator=moderator
    )


@router.post(
    "/estimate",
    response_model=EstimationResponse,
    summary="Generate a software estimation from a typed project description",
    responses={
        400: {
            "model": GuardrailErrorResponse,
            "description": "Input blocked by the guardrails (injection, moderation or similar).",
        },
        422: {
            "description": "Invalid body, prompt version or generation option "
            "(including an unusable `model`)."
        },
        502: {"description": "The LLM provider failed (generic error, no details)."},
    },
)
def estimate(
    request: EstimationRequest,
    service: Annotated[EstimationService, Depends(get_estimation_service)],
    prompt_version: Annotated[
        PromptVersion, Query(description="Version of the prompt templates to render.")
    ] = DEFAULT_PROMPT_VERSION,
) -> EstimationResponse:
    options = GenerationOptions(
        prompt_version=prompt_version,
        num_examples=request.num_examples,
        use_examples=request.use_examples,
        model=request.model,
        max_tokens=request.max_tokens,
        evaluate=request.evaluate,
    )
    try:
        result = service.estimate(request, options)
    except InvalidOptionError as exc:
        location = "query" if exc.field == "prompt_version" else "body"
        error = {"loc": (location, exc.field), "msg": exc.message, "type": "value_error"}
        raise RequestValidationError([error]) from exc
    except InputGuardrailViolation as exc:
        # The service already logs `input_guardrail_blocked` (reason, field, rule only).
        raise HTTPException(
            status_code=400, detail={"reason": exc.reason, "message": exc.message}
        ) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=PROVIDER_ERROR_DETAIL) from exc
    return build_estimation_response(result)


def build_estimation_response(result: EstimationOutcome) -> EstimationResponse:
    validation = (
        StructureCheckResponse(
            **{**asdict(result.validation), "issues": list(result.validation.issues)}
        )
        if result.validation
        else None
    )
    return EstimationResponse(
        text=result.text,
        result=result.result,
        prompt_version=result.prompt_version,
        model=result.model,
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        truncated=result.truncated,
        fallback_used=result.fallback_used,
        cache_hit=result.cache_hit,
        latency_ms=result.latency_ms,
        validation=validation,
    )
