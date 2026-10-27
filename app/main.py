"""FastAPI application: routers, health check and fail-fast startup."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import ValidationError

from app.config import Settings, configure_logging, format_config_error, get_settings
from app.routers import estimations
from app.services.llm_service import get_llm_provider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        settings = get_settings()
        get_llm_provider()
    except ValidationError as exc:
        message = format_config_error(exc)
        logging.basicConfig(level=logging.ERROR)
        logger.error(message)
        raise RuntimeError(message) from None
    configure_logging(settings)
    yield


app = FastAPI(
    title="Software Estimation API",
    description="Receives a client meeting transcription and returns an LLM-generated "
    "software estimation (static context in the prompt, CAG).",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(estimations.router, prefix="/api/v1")


@app.get("/health", tags=["health"], summary="Health check")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    return {
        "status": "ok",
        "env": settings.app_env,
        "provider": settings.llm_provider,
        "model": settings.effective_model,
    }
