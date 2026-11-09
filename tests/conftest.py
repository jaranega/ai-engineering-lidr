"""Shared pytest configuration and fixtures."""

# `app/__init__.py` forces LiteLLM's local cost map; it must run before `litellm` is imported
# below, or LiteLLM would try to download its price map (the suite must run offline).
import app.config  # noqa: F401

# isort: split
import copy
import logging
import socket
from collections.abc import Callable, Generator, Iterator, Sequence

import fakeredis
import litellm
import pytest
import structlog
from fastapi.testclient import TestClient
from litellm import CustomLLM
from litellm import Router as RealRouter

from app.config import LITELLM_LOGGERS, Settings, get_settings
from app.main import app
from app.services import cache as cache_module
from app.services import llm_service
from app.services.cache import ResponseCache
from app.services.llm_service import (
    FALLBACK_GROUP,
    PRIMARY_GROUP,
    ChatMessage,
    LiteLLMProvider,
    LLMProviderError,
    LLMResult,
    LLMStream,
    build_router,
)


def _clear_caches() -> None:
    get_settings.cache_clear()
    llm_service.get_llm_provider.cache_clear()
    cache_module.get_response_cache.cache_clear()
    llm_service._system_prompt.cache_clear()
    llm_service._chat_system_prompt.cache_clear()


CONFIG_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "PRIMARY_MODEL",
    "FALLBACK_MODEL",
    "LLM_TIMEOUT",
    "LLM_RETRIES",
    "REDIS_URL",
    "CACHE_TTL",
    "APP_ENV",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Hide the developer's real configuration from every test.

    Config variables are removed from the process environment and the cwd moves to an empty
    tmp dir: `Settings` resolves `env_file=".env"` against the cwd, so a local `.env` is never
    read, not even by code that calls `get_settings()` directly (the app lifespan, which
    `dependency_overrides` cannot reach). Tests that need configuration set it explicitly.
    """
    for var in CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Empty REDIS_URL disables the cache: no test ever needs (or reaches) a Redis server.
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly on any attempt to open a network connection (ci-pipeline: tests sin red)."""

    def blocked(*args, **kwargs):
        raise AssertionError("Network access attempted during a test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Iterator[None]:
    """`configure_logging` mutates global logging and structlog state: restore it per test.

    Also keeps LiteLLM's own loggers quiet when a test does not call `configure_logging`.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    names = ("httpx", "httpcore", "asyncio", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {name: logging.getLogger(name).level for name in (*names, *LITELLM_LOGGERS)}
    for name in LITELLM_LOGGERS:
        quiet = logging.getLogger(name)
        quiet.setLevel(logging.CRITICAL)
        quiet.propagate = False
    yield
    root.handlers = handlers
    root.setLevel(level)
    for name, saved_level in saved.items():
        logging.getLogger(name).setLevel(saved_level)
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def _clear_cached_state():
    """Reset cached settings, provider and system prompt so tests never leak state."""
    _clear_caches()
    yield
    _clear_caches()


class FakeProvider:
    """In-memory LLMProvider: counts calls, records what it receives, optionally fails.

    `stream()` yields `fragments` (default: the whole `text` as one fragment). With
    `error_after=N` it raises `error` after N fragments (0 = before the first one, like an
    SDK failure at creation: the real streams are lazy, so it surfaces on iteration).
    `fragments=[]` delivers zero fragments (empty reply); `truncated` sets the result flag.
    """

    def __init__(
        self,
        text: str = "## Estimación: fake",
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        input_tokens: int = 1000,
        output_tokens: int = 500,
        error: LLMProviderError | None = None,
        fragments: Sequence[str] | None = None,
        error_after: int | None = None,
        truncated: bool = False,
        fallback_used: bool = False,
        cache_hit: bool = False,
    ) -> None:
        self.result = LLMResult(
            text=text,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            truncated=truncated,
            fallback_used=fallback_used,
            cache_hit=cache_hit,
        )
        self.error = error
        self.fragments = list(fragments) if fragments is not None else [text]
        self.error_after = error_after
        # (system, messages) received by generate() and stream(), in call order.
        self.calls: list[tuple[str, list[ChatMessage]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult:
        self.calls.append((system, list(messages)))
        if self.error is not None:
            raise self.error
        return self.result

    def stream(self, system: str, messages: Sequence[ChatMessage]) -> LLMStream:
        self.calls.append((system, list(messages)))
        return LLMStream(self._stream())

    def _stream(self) -> Generator[str, None, LLMResult]:
        for index, fragment in enumerate(self.fragments):
            if self.error is not None and self.error_after == index:
                raise self.error
            yield fragment
        if self.error is not None and (self.error_after is not None or not self.fragments):
            raise self.error
        return LLMResult(
            text="".join(self.fragments),
            model=self.result.model,
            provider=self.result.provider,
            input_tokens=self.result.input_tokens,
            output_tokens=self.result.output_tokens,
            truncated=self.result.truncated,
            fallback_used=self.result.fallback_used,
            cache_hit=self.result.cache_hit,
        )


@pytest.fixture
def fake_provider_cls() -> type[FakeProvider]:
    """The FakeProvider class, to build providers with custom results or errors."""
    return FakeProvider


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


def make_settings(**kwargs) -> Settings:
    """Settings built without reading any `.env`; defaults to a dummy OpenAI key only."""
    kwargs.setdefault("openai_api_key", "sk-fake-openai")
    return Settings(_env_file=None, **kwargs)


@pytest.fixture
def settings() -> Settings:
    """Settings with deliberately non-default values, to prove responses follow the config."""
    return make_settings(
        app_env="staging", primary_model="gpt-4o", anthropic_api_key="sk-ant-fake-anthropic"
    )


@pytest.fixture
def provider(settings: Settings) -> FakeProvider:
    """FakeProvider that reports the provider/model of the overridden settings."""
    return FakeProvider(model=settings.primary.model, provider=settings.primary.provider)


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def response_cache(fake_redis: fakeredis.FakeRedis) -> ResponseCache:
    return ResponseCache(fake_redis, ttl=60)


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, provider: FakeProvider
) -> Iterator[TestClient]:
    """TestClient running the lifespan, with settings and LLM provider overridden.

    The lifespan calls `get_settings()` directly, so it gets a dummy key from the environment
    (the autouse fixture already hid any real `.env`); building the real provider does no I/O.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-lifespan")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


# --- Real LiteLLM Router with mocked deployments (contract and log tests) ---


def _chunk(text: str, finish_reason: str | None = None) -> dict:
    return {
        "text": text,
        "finish_reason": finish_reason,
        "index": 0,
        "is_finished": finish_reason is not None,
        "tool_use": None,
        "usage": {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0},
    }


class FlakyStream(CustomLLM):
    """Custom provider: delivers one fragment and then fails (error after the first chunk)."""

    def __init__(self, message: str = "boom sk-contract-secret") -> None:
        super().__init__()
        self.message = message

    def streaming(self, *args, **kwargs) -> Iterator[dict]:
        yield _chunk("parcial")
        raise RuntimeError(self.message)


class LengthStream(CustomLLM):
    """Custom provider: ends the stream with the given `finish_reason`."""

    def __init__(self, finish_reason: str) -> None:
        super().__init__()
        self.finish_reason = finish_reason

    def streaming(self, *args, **kwargs) -> Iterator[dict]:
        yield _chunk("corta")
        yield _chunk("da", self.finish_reason)


@pytest.fixture
def make_provider(monkeypatch: pytest.MonkeyPatch) -> Callable[..., LiteLLMProvider]:
    """Build the real Router (through `build_router`) with mocked deployments.

    `primary` / `fallback` are `mock_response` values, or a `{"model": ...}` dict to replace the
    deployment's `litellm_params` (used to plug a custom provider).
    """
    original_providers = litellm.custom_provider_map

    def factory(
        primary: object = "respuesta del primario",
        fallback: object = "respuesta del respaldo",
        *,
        with_fallback: bool = True,
        retries: int = 0,
        custom: dict[str, CustomLLM] | None = None,
    ) -> LiteLLMProvider:
        settings = make_settings(
            llm_retries=retries,
            anthropic_api_key="sk-ant-fake" if with_fallback else None,
        )
        mocks = {PRIMARY_GROUP: primary, FALLBACK_GROUP: fallback}

        def router_with_mocks(**kwargs) -> RealRouter:
            model_list = copy.deepcopy(kwargs.pop("model_list"))
            for deployment in model_list:
                mock = mocks[deployment["model_name"]]
                if isinstance(mock, dict):
                    deployment["litellm_params"] = {**mock, "timeout": 5}
                else:
                    deployment["litellm_params"]["mock_response"] = mock
            return RealRouter(model_list=model_list, **kwargs)

        monkeypatch.setattr(llm_service, "Router", router_with_mocks)
        if custom:
            litellm.custom_provider_map = [
                {"provider": name, "custom_handler": handler} for name, handler in custom.items()
            ]
        router = build_router(settings)
        assert (settings.fallback is not None) is with_fallback
        return LiteLLMProvider(router, settings.primary, settings.fallback)

    yield factory
    litellm.custom_provider_map = original_providers
