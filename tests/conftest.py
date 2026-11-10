"""Shared pytest configuration and fixtures."""

# `app/__init__.py` forces LiteLLM's local cost map; it must run before `litellm` is imported
# below, or LiteLLM would try to download its price map (the suite must run offline).
import app.config  # noqa: F401

# isort: split
import copy
import logging
import socket
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import ExitStack
from functools import partial

import fakeredis
import litellm
import pytest
import structlog
from fastapi.testclient import TestClient
from litellm import CustomLLM
from litellm import Router as RealRouter

from app.config import LITELLM_LOGGERS, Settings, get_settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.domain import EstimationInput
from app.estimation.compute import compute_estimate
from app.estimation.examples import example_result
from app.estimation.render import render_markdown
from app.estimation.schema import EstimationResult
from app.main import app
from app.prompts import loader as prompt_loader
from app.services import cache as cache_module
from app.services import llm_service
from app.services import moderation as moderation_module
from app.services import session_service as session_service_module
from app.services.cache import ResponseCache
from app.services.llm_service import (
    FALLBACK_GROUP,
    PRIMARY_GROUP,
    ChatMessage,
    LiteLLMProvider,
    LLMCallOptions,
    LLMProviderError,
    LLMResult,
    LLMStream,
    StructuredResult,
    build_override_router,
    build_router,
)
from app.sessions import SessionStore


def _clear_caches() -> None:
    get_settings.cache_clear()
    llm_service.get_llm_provider.cache_clear()
    cache_module.get_response_cache.cache_clear()
    moderation_module.get_moderator.cache_clear()
    prompt_loader._environment.cache_clear()
    session_service_module.get_session_store.cache_clear()


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
    "STRUCTURED_MAX_RETRIES",
    "MODERATION_ENABLED",
    "MAX_CONVERSATION_TURNS",
    "MAX_SESSIONS",
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_ATTACHMENT_CHARS",
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


DESCRIPTION = (
    "A web platform to book clinic appointments, with patient records, "
    "reminders and an admin panel."
)


def valid_body(**overrides) -> dict:
    """Smallest valid body of `POST /api/v1/estimate`; overrides replace or add fields."""
    return {
        "description": DESCRIPTION,
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "line_items",
        **overrides,
    }


TRANSCRIPT = "We need a clinic booking platform with reminders and an admin panel for the staff."


def turn_data(**overrides) -> dict:
    """Smallest valid multipart form of `POST /api/v1/sessions/{id}/estimate`."""
    return {
        "transcript": TRANSCRIPT,
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "line_items",
        **overrides,
    }


def attachment_parts(*files: tuple[str, bytes] | tuple[str, bytes, str]) -> list[tuple]:
    """`files=` argument for httpx: one `attachments` part per `(name, data[, mime])`."""
    return [
        ("attachments", (name, data, *(rest or ("application/octet-stream",))))
        for name, data, *rest in files
    ]


def make_input(**overrides) -> EstimationInput:
    """Typed estimation input built from `valid_body` (extra keys are ignored)."""
    return EstimationInput.model_validate(valid_body(**overrides))


def valid_payload(**overrides) -> dict:
    """JSON payload of a valid in-scope `EstimationResult` (example 1) valid for every
    format and level: it carries narrative paragraphs and per-phase assumptions.
    """
    data = example_result(ESTIMATION_EXAMPLES[0]).model_dump(mode="json")
    # English free text, so label-language checks cannot be confused by Spanish content.
    data["language"] = "en"
    data["title"] = "Clinic booking platform"
    data["summary"] = "Internal web platform with an agenda and reminders."
    for index, row in enumerate(data["rows"], start=1):
        row["task"] = f"Work item {index}"
    for member in data["team"]:
        member["role"] = "engineers"
    data["assumptions_and_risks"] = ["The SMS provider is pay per use."]
    data["narrative"] = ["First paragraph of prose.", "Second paragraph of prose."]
    for phase in data["phases"]:
        phase["assumptions"] = [f"Assumption of {phase['phase']}"]
    return {**data, **overrides}


def valid_result(**overrides) -> EstimationResult:
    return EstimationResult.model_validate(valid_payload(**overrides))


def example_estimates(
    output_format: str, detail_level: str = "medium", language: str = "es"
) -> list[str]:
    """Markdown of each CAG example as rendered by the server for the format and level."""
    return [
        render_markdown(
            compute_estimate(example_result(example), language), output_format, detail_level
        )
        for example in ESTIMATION_EXAMPLES
    ]


def out_of_scope_payload(**overrides) -> dict:
    """JSON payload of a valid out-of-scope result (confidence below 30, nothing else)."""
    return {
        "language": "en",
        "title": "Unclear project",
        "summary": "The description is too vague to estimate.",
        "confidence_pct": 10,
        "rows": [],
        "phases": [],
        "team": [],
        "duration_weeks": None,
        "assumptions_and_risks": ["Scope, users and integrations are missing."],
        "narrative": [],
        **overrides,
    }


StructuredPayload = dict | EstimationResult
StructuredSource = (
    StructuredPayload
    | list[StructuredPayload]
    | Callable[[str, list[ChatMessage]], StructuredPayload]
)


class FakeProvider:
    """In-memory LLMProvider: counts calls, records what it receives, optionally fails.

    `stream()` yields `fragments` (default: the whole `text` as one fragment). With
    `error_after=N` it raises `error` after N fragments (0 = before the first one, like an
    SDK failure at creation: the real streams are lazy, so it surfaces on iteration).
    `fragments=[]` delivers zero fragments (empty reply); `truncated` sets the result flag.

    `structured` is the payload of `generate_structured`: a dict or `EstimationResult`, a list
    (the n-th call returns the n-th item, the last one repeats) or a callable
    `(system, messages) -> payload` that can react to what the provider receives.
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
        structured: StructuredSource | None = None,
        attempts: int = 1,
    ) -> None:
        # Payload returned by `generate_structured`, validated with the requested model.
        self.structured = structured if structured is not None else valid_payload()
        self.attempts = attempts
        # Response models received by `generate_structured`, in call order.
        self.response_models: list[type] = []
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
        # `LLMCallOptions` received by each call (None when the caller passed none), aligned
        # with `calls`. The service omits default options, so the default paths record None.
        self.options: list[LLMCallOptions | None] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMResult:
        self.calls.append((system, list(messages)))
        self.options.append(options)
        if self.error is not None:
            raise self.error
        return self.result

    def generate_structured(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type,
        options: LLMCallOptions | None = None,
    ) -> StructuredResult:
        self.calls.append((system, list(messages)))
        self.options.append(options)
        self.response_models.append(response_model)
        if self.error is not None:
            raise self.error
        source = self.structured
        if callable(source):
            source = source(system, list(messages))
        elif isinstance(source, list):
            source = source[min(len(self.calls), len(source)) - 1]
        payload = source.model_dump(mode="json") if isinstance(source, EstimationResult) else source
        return StructuredResult(
            value=response_model.model_validate(payload),
            model=self.result.model,
            provider=self.result.provider,
            input_tokens=self.result.input_tokens,
            output_tokens=self.result.output_tokens,
            attempts=self.attempts,
            fallback_used=self.result.fallback_used,
            cache_hit=self.result.cache_hit,
        )

    def stream(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMStream:
        self.calls.append((system, list(messages)))
        self.options.append(options)
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


class FakeModerator:
    """Moderator double: records the texts it receives; `result` is what `flagged` returns
    (`True`, `False` or `None` for "could not check"), or `error` is raised instead.
    """

    def __init__(self, result: bool | None = False, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[list[str]] = []

    def flagged(self, texts: Sequence[str]) -> bool | None:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return self.result


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
def moderator() -> FakeModerator:
    """Moderator double used by the `client` fixture: never flags unless told to."""
    return FakeModerator(result=False)


@pytest.fixture
def session_store(settings: Settings) -> SessionStore:
    """Fresh in-memory session store (one per test) sized like the `settings` fixture."""
    return SessionStore(
        max_sessions=settings.max_sessions, max_turns=settings.max_conversation_turns
    )


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    provider: FakeProvider,
    moderator: FakeModerator,
    session_store: SessionStore,
) -> Iterator[TestClient]:
    """TestClient running the lifespan, with settings and LLM provider overridden.

    The lifespan calls `get_settings()` directly, so it gets a dummy key from the environment
    (the autouse fixture already hid any real `.env`); building the real provider does no I/O.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-lifespan")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    app.dependency_overrides[moderation_module.get_moderator] = lambda: moderator
    app.dependency_overrides[session_service_module.get_session_store] = lambda: session_store
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def install_overrides(
    settings: Settings,
    provider: FakeProvider,
    moderator: FakeModerator,
    store: SessionStore,
) -> None:
    """Swap settings, provider, moderator and session store for doubles (offline wiring)."""
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    app.dependency_overrides[moderation_module.get_moderator] = lambda: moderator
    app.dependency_overrides[session_service_module.get_session_store] = lambda: store


@pytest.fixture
def make_session_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., tuple[TestClient, FakeProvider, FakeModerator, SessionStore]]]:
    """Factory of a started `TestClient` with doubles and custom `Settings` values.

    `factory(provider=None, moderator=None, **settings_kwargs)` returns
    `(client, provider, moderator, store)`; the store is sized like the settings.
    """
    stack = ExitStack()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-lifespan")

    def factory(
        provider: FakeProvider | None = None,
        moderator: FakeModerator | None = None,
        **settings_kwargs,
    ) -> tuple[TestClient, FakeProvider, FakeModerator, SessionStore]:
        cfg = make_settings(**settings_kwargs)
        provider = provider or FakeProvider(model=cfg.primary.model, provider=cfg.primary.provider)
        moderator = moderator or FakeModerator(result=False)
        store = SessionStore(max_sessions=cfg.max_sessions, max_turns=cfg.max_conversation_turns)
        install_overrides(cfg, provider, moderator, store)
        return stack.enter_context(TestClient(app)), provider, moderator, store

    try:
        yield factory
    finally:
        stack.close()
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
        **settings_kwargs,
    ) -> LiteLLMProvider:
        settings = make_settings(
            llm_retries=retries,
            anthropic_api_key="sk-ant-fake" if with_fallback else None,
            **settings_kwargs,
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
        # The override Router goes through the same (mocked) `Router` constructor.
        return LiteLLMProvider(
            router,
            settings.primary,
            settings.fallback,
            router_factory=partial(build_override_router, settings),
        )

    yield factory
    litellm.custom_provider_map = original_providers
