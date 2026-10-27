"""Shared pytest configuration and fixtures."""

from collections.abc import Generator, Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services import llm_service
from app.services.llm_service import ChatMessage, LLMProviderError, LLMResult, LLMStream


def _clear_caches() -> None:
    get_settings.cache_clear()
    llm_service.get_llm_provider.cache_clear()
    llm_service._system_prompt.cache_clear()
    llm_service._chat_system_prompt.cache_clear()


CONFIG_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
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
    monkeypatch.chdir(tmp_path)


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
    ) -> None:
        self.result = LLMResult(
            text=text,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
        )


@pytest.fixture
def fake_provider_cls() -> type[FakeProvider]:
    """The FakeProvider class, to build providers with custom results or errors."""
    return FakeProvider


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


def make_settings(**kwargs) -> Settings:
    """Settings built without reading any `.env`; defaults to a dummy OpenAI key."""
    if kwargs.get("llm_provider", "openai") == "openai":
        kwargs.setdefault("openai_api_key", "sk-fake-openai")
    return Settings(_env_file=None, **kwargs)


@pytest.fixture
def settings() -> Settings:
    """Settings with deliberately non-default values, to prove responses follow the config."""
    return make_settings(app_env="staging", llm_model="custom-model-x")


@pytest.fixture
def provider(settings: Settings) -> FakeProvider:
    """FakeProvider that reports the provider/model of the overridden settings."""
    return FakeProvider(model=settings.effective_model, provider=settings.llm_provider)


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
