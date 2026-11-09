"""Application settings loaded from environment variables and an optional `.env` file."""

import logging
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Self

import litellm
import structlog
from litellm import get_llm_provider as resolve_litellm_provider
from pydantic import (
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Avoid LiteLLM printing its provider list on unknown models.
litellm.suppress_debug_info = True

# Supported providers and the variable holding each one's API key.
_KEY_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Default model per provider; an unset FALLBACK_MODEL defaults to the other provider's.
_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
}

# Third-party loggers emit full request payloads (client transcriptions) at DEBUG.
# "openai" covers openai._base_client, which LiteLLM uses for OpenAI calls.
NOISY_LOGGERS = ("httpx", "httpcore", "openai")

# LiteLLM's own loggers can include provider response bodies in error messages.
LITELLM_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


@dataclass(frozen=True)
class Deployment:
    """A resolved model: name without provider prefix plus its provider."""

    model: str
    provider: str


def _resolve(var: str, model: str) -> Deployment:
    supported = ", ".join(sorted(_KEY_VARS))
    try:
        name, provider, _, _ = resolve_litellm_provider(model=model)
    except Exception:
        raise ValueError(
            f"{var}={model!r} is not a recognized model. Supported providers: {supported}."
        ) from None
    if provider not in _KEY_VARS:
        raise ValueError(
            f"{var}={model!r} uses provider {provider!r}. Supported providers: {supported}."
        )
    return Deployment(model=name, provider=provider)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    primary_model: str = _DEFAULT_MODELS["openai"]
    fallback_model: str | None = None  # unset: other provider default; blank: disabled
    llm_timeout: int = Field(default=60, gt=0)
    llm_retries: int = Field(default=2, ge=0)
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl: int = Field(default=86400, gt=0)
    app_env: str = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEBUG"

    _primary: Deployment = PrivateAttr()
    _fallback: Deployment | None = PrivateAttr(default=None)
    _fallback_disabled_reason: str | None = PrivateAttr(default=None)

    @field_validator("openai_api_key", "anthropic_api_key", "fallback_model", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def _resolve_models(self) -> Self:
        primary = _resolve("PRIMARY_MODEL", self.primary_model)
        fallback_model = self.fallback_model
        if "fallback_model" not in self.model_fields_set:
            fallback_model = next(
                model for prov, model in _DEFAULT_MODELS.items() if prov != primary.provider
            )
        self.fallback_model = fallback_model
        fallback = _resolve("FALLBACK_MODEL", fallback_model) if fallback_model else None

        primary_var = _KEY_VARS[primary.provider]
        if self._key(primary.provider) is None:
            raise ValueError(
                f"{primary_var} is required for PRIMARY_MODEL={self.primary_model}. "
                f"Define it in .env or change PRIMARY_MODEL."
            )
        if fallback is not None and fallback == primary:
            raise ValueError("FALLBACK_MODEL must be different from PRIMARY_MODEL.")

        self._primary = primary
        if fallback is None:
            self._fallback_disabled_reason = "FALLBACK_MODEL is empty"
        elif self._key(fallback.provider) is None:
            self._fallback_disabled_reason = f"{_KEY_VARS[fallback.provider]} is not defined"
        else:
            self._fallback = fallback
        return self

    def _key(self, provider: str) -> SecretStr | None:
        return getattr(self, _KEY_VARS[provider].lower())

    def api_key(self, provider: str) -> str | None:
        """Return the raw API key of a provider (for building LLM clients only)."""
        key = self._key(provider)
        return key.get_secret_value() if key else None

    @property
    def primary(self) -> Deployment:
        return self._primary

    @property
    def fallback(self) -> Deployment | None:
        return self._fallback

    @property
    def fallback_disabled_reason(self) -> str | None:
        return self._fallback_disabled_reason


@lru_cache
def get_settings() -> Settings:
    return Settings()


def format_config_error(exc: ValidationError) -> str:
    """Build a clear, value-free message from a settings validation error."""
    lines = ["Invalid configuration:"]
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(part) for part in err["loc"])
        if err["type"] == "value_error":
            lines.append(f"- {err['msg'].removeprefix('Value error, ')}")
        else:
            lines.append(f"- {loc.upper()}: {err['msg']}")
    return "\n".join(lines)


def configure_logging(settings: Settings) -> None:
    """Configure structlog and root logging; idempotent, safe for Streamlit reruns."""
    level = logging.getLevelNamesMapping()[settings.log_level]
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.app_env == "production"
        else structlog.dev.ConsoleRenderer()
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True

    # Never below INFO, so HTTP debug logs do not dump customer data.
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.INFO, level))

    # LiteLLM's async-client cleanup logs a DEBUG "Using selector" line at exit.
    logging.getLogger("asyncio").setLevel(max(logging.INFO, level))

    for name in LITELLM_LOGGERS:
        ll_logger = logging.getLogger(name)
        ll_logger.handlers = []
        ll_logger.setLevel(logging.CRITICAL)
        ll_logger.propagate = False
