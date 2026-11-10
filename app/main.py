"""FastAPI application: routers, health check and fail-fast startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from app.config import Settings, configure_logging, format_config_error, get_settings
from app.routers import estimations
from app.schemas import HealthResponse
from app.services.cache import ResponseCache, get_response_cache
from app.services.llm_service import get_llm_provider

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        settings = get_settings()
    except ValidationError as exc:
        message = format_config_error(exc)
        configure_logging(Settings.model_construct(app_env="development", log_level="INFO"))
        logger.error("config_invalid", message=message)
        raise RuntimeError(message) from None
    configure_logging(settings)
    get_llm_provider()
    if settings.fallback is None:
        logger.warning("llm_fallback_disabled", reason=settings.fallback_disabled_reason)
    yield


app = FastAPI(
    title="Software Estimation API",
    description="Receives a client meeting transcription and returns an LLM-generated "
    "software estimation (static context in the prompt, CAG).",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(estimations.router, prefix="/api/v1")


@app.get("/health", tags=["health"], summary="Health check", response_model=HealthResponse)
def health(
    settings: Annotated[Settings, Depends(get_settings)],
    cache: Annotated[ResponseCache | None, Depends(get_response_cache)],
) -> HealthResponse:
    fallback = settings.fallback
    return HealthResponse(
        status="ok",
        env=settings.app_env,
        primary_model=settings.primary.model,
        fallback_model=fallback.model if fallback else None,
        cache="ok" if cache is not None and cache.ping() else "unavailable",
    )
