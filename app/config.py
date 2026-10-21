"""Application settings loaded from environment variables and an optional `.env` file."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
}

_KEY_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_model: str | None = None
    app_env: str = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEBUG"

    @field_validator("openai_api_key", "anthropic_api_key", "llm_model", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def _require_active_provider_key(self) -> Self:
        var = _KEY_VARS[self.llm_provider]
        if getattr(self, var.lower()) is None:
            raise ValueError(
                f"{var} is required when LLM_PROVIDER={self.llm_provider}. "
                f"Define it in .env or set LLM_PROVIDER to a provider whose key is defined."
            )
        return self

    @property
    def effective_model(self) -> str:
        return self.llm_model or DEFAULT_MODELS[self.llm_provider]


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
