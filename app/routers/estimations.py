"""Estimation endpoints: HTTP validation and error mapping only."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.schemas import EstimationRequest, EstimationResponse
from app.services.llm_service import (
    EstimationService,
    LLMProvider,
    LLMProviderError,
    get_llm_provider,
)

router = APIRouter(tags=["estimations"])


def get_estimation_service(
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> EstimationService:
    return EstimationService(provider)


@router.post(
    "/estimate",
    response_model=EstimationResponse,
    summary="Generate a software estimation from a meeting transcription",
    responses={502: {"description": "The LLM provider failed (generic error, no details)."}},
)
def estimate(
    request: EstimationRequest,
    service: Annotated[EstimationService, Depends(get_estimation_service)],
) -> EstimationResponse:
    try:
        result = service.estimate(request.transcription)
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail="LLM provider error") from exc
    return EstimationResponse(
        estimation=result.text,
        model=result.model,
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )
