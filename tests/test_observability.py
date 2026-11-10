"""Tests for structured logging and LLM/cache events (spec: observability, llm-provider)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.config import LITELLM_LOGGERS, configure_logging, get_settings
from app.main import app
from app.services import llm_service
from app.services.cache import ResponseCache
from app.services.llm_service import (
    CachedProvider,
    ChatMessage,
    EstimationService,
    LiteLLMProvider,
    LLMProviderError,
)
from tests.conftest import make_input, make_settings, valid_body
from tests.test_llm_provider import PRIMARY, FakeRouter, FakeStream, chunk, response, usage_chunk

SENTINEL = "DESCRIPCION-SECRETA-123"
REFERENCE_SENTINEL = "REFERENCIA-SECRETA-456"
API_KEY = "sk-test-visible-value"
MESSAGES: list[ChatMessage] = [{"role": "user", "content": SENTINEL}]


def events(logs: list[dict], name: str) -> list[dict]:
    return [e for e in logs if e["event"] == name]


def names(logs: list[dict]) -> list[str]:
    return [e["event"] for e in logs]


def stack(router: FakeRouter, cache: ResponseCache | None) -> CachedProvider:
    return CachedProvider(LiteLLMProvider(router, PRIMARY, None), cache, PRIMARY.model)


# --- Requirement: Logging estructurado ---


def test_production_logs_are_json_with_timestamp_level_and_event(monkeypatch, capsys):
    """Requirement: Logging estructurado / Scenario: Producción en JSON."""
    configure_logging(make_settings(app_env="production", log_level="INFO"))
    llm_service.logger.info("llm_call_completed", model="gpt-4o-mini", tokens=3)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert {"timestamp", "level", "event"} <= set(record)
    assert record["event"] == "llm_call_completed"
    assert record["level"] == "info"
    assert record["model"] == "gpt-4o-mini"


def test_development_logs_are_readable_text_not_json(capsys):
    """Requirement: Logging estructurado / Scenario: Desarrollo en consola."""
    configure_logging(make_settings(app_env="development", log_level="INFO"))
    llm_service.logger.info("llm_call_completed", model="gpt-4o-mini")
    line = capsys.readouterr().err.strip()
    assert "llm_call_completed" in line
    with pytest.raises(ValueError):
        json.loads(line)


def test_stdlib_loggers_are_rendered_by_the_same_formatter_in_production(capsys):
    """Requirement: Logging estructurado (uvicorn/stdlib comparten formato)."""
    configure_logging(make_settings(app_env="production", log_level="INFO"))
    logging.getLogger("uvicorn.error").info("Started server process")
    record = json.loads(capsys.readouterr().err.strip())
    assert record["event"] == "Started server process"
    assert record["level"] == "info"


def test_configure_logging_is_idempotent(capsys):
    """Requirement: Logging estructurado (reruns de Streamlit no duplican handlers)."""
    settings = make_settings(app_env="production", log_level="INFO")
    for _ in range(3):
        configure_logging(settings)
    assert len(logging.getLogger().handlers) == 1
    llm_service.logger.info("once")
    assert len([ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]) == 1


def test_litellm_loggers_are_silenced_by_configure_logging():
    """Requirement: Datos que nunca se registran (los loggers de LiteLLM no emiten cuerpos)."""
    for name in LITELLM_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
    configure_logging(make_settings(log_level="DEBUG"))
    for name in LITELLM_LOGGERS:
        logger = logging.getLogger(name)
        assert logger.level == logging.CRITICAL
        assert logger.propagate is False
        assert logger.handlers == []


@pytest.mark.parametrize("name", ["httpx", "httpcore", "openai", "openai._base_client"])
def test_http_loggers_never_below_info(name):
    """Requirement: Datos que nunca se registran (httpx/httpcore/openai no vuelcan la petición)."""
    configure_logging(make_settings(log_level="DEBUG"))
    assert logging.getLogger(name).getEffectiveLevel() >= logging.INFO


def test_openai_sdk_debug_payload_never_reaches_the_log_output(capsys):
    """Requirement: Datos que nunca se registran (regression: openai._base_client DEBUG
    dumped the request payload when LOG_LEVEL=DEBUG)."""
    configure_logging(make_settings(log_level="DEBUG"))
    logging.getLogger("openai._base_client").debug(
        "Request options: %s", {"messages": [{"content": "CENTINELA"}]}
    )
    captured = capsys.readouterr()
    assert "CENTINELA" not in captured.out + captured.err


# --- Requirement: Eventos de la llamada al LLM ---


def test_successful_call_logs_started_and_completed_with_fields():
    """Requirement: Eventos de la llamada / Scenario: Llamada correcta."""
    router = FakeRouter(response(usage=(1200, 300)))
    with capture_logs() as logs:
        LiteLLMProvider(router, PRIMARY, None).generate("S", MESSAGES)
    assert names(logs) == ["llm_call_started", "llm_call_completed"]
    started, completed = logs
    assert started["mode"] == "generate"
    assert started["primary_model"] == "gpt-4o-mini"
    assert completed["model"] == "gpt-4o-mini"
    assert completed["provider"] == "openai"
    assert (completed["input_tokens"], completed["output_tokens"]) == (1200, 300)
    assert completed["cost_usd"] == 0.00036
    assert completed["latency_ms"] >= 0
    assert completed["fallback_used"] is False
    assert completed["cache_hit"] is False
    assert completed["log_level"] == "info"


def test_stream_call_logs_started_and_completed():
    """Requirement: Eventos de la llamada / Scenario: Llamada correcta (streaming)."""
    items = [chunk("a"), chunk("b", "stop"), usage_chunk(1200, 300)]
    stream = LiteLLMProvider(FakeRouter(FakeStream(items)), PRIMARY, None).stream("S", MESSAGES)
    with capture_logs() as logs:
        list(stream)
    assert names(logs) == ["llm_call_started", "llm_call_completed"]
    assert logs[0]["mode"] == "stream"
    assert (logs[1]["input_tokens"], logs[1]["output_tokens"]) == (1200, 300)


@pytest.mark.parametrize("mode", ["generate", "stream"])
def test_failed_call_logs_type_status_and_latency_without_message(mode):
    """Requirement: Eventos / Scenario: Llamada fallida; Errores sin datos sensibles."""

    class UpstreamError(Exception):
        status_code = 503

    error = UpstreamError(f"provider body with {API_KEY} and {SENTINEL}")
    provider = LiteLLMProvider(FakeRouter(error=error), PRIMARY, None)
    with capture_logs() as logs, pytest.raises(LLMProviderError):
        if mode == "generate":
            provider.generate("S", MESSAGES)
        else:
            list(provider.stream("S", MESSAGES))
    failed = events(logs, "llm_call_failed")
    assert len(failed) == 1
    assert failed[0]["error_type"] == "UpstreamError"
    assert failed[0]["status_code"] == 503
    assert failed[0]["latency_ms"] >= 0
    assert failed[0]["mode"] == mode
    assert failed[0]["log_level"] == "error"
    assert not events(logs, "llm_call_completed")
    rendered = repr(logs)
    assert API_KEY not in rendered
    assert SENTINEL not in rendered
    assert "exc_info" not in failed[0]


def test_fallback_is_logged_with_fallback_model_and_flag():
    """Requirement: Eventos / Scenario: Fallback registrado."""
    router = FakeRouter(response(model_id="fallback"))
    provider = LiteLLMProvider(
        router, PRIMARY, llm_service.Deployment("claude-haiku-4-5", "anthropic")
    )
    with capture_logs() as logs:
        provider.generate("S", MESSAGES)
    completed = events(logs, "llm_call_completed")[0]
    assert completed["model"] == "claude-haiku-4-5"
    assert completed["provider"] == "anthropic"
    assert completed["fallback_used"] is True


def test_truncated_output_logs_a_warning():
    """Requirement: Límites / Scenario: Salida truncada (warning registrado)."""
    with capture_logs() as logs:
        LiteLLMProvider(FakeRouter(response(finish_reason="length")), PRIMARY, None).generate(
            "S", MESSAGES
        )
    truncated = events(logs, "llm_output_truncated")
    assert len(truncated) == 1
    assert truncated[0]["log_level"] == "warning"
    assert truncated[0]["max_tokens"] == llm_service.MAX_TOKENS


def test_cache_hit_also_logs_llm_call_completed_with_cache_flag(response_cache):
    """Requirement: Eventos de caché (el acierto emite `llm_call_completed`, D8)."""
    provider = stack(FakeRouter(response()), response_cache)
    provider.generate("S", MESSAGES)
    with capture_logs() as logs:
        provider.generate("S", MESSAGES)
    assert names(logs) == ["cache_hit", "llm_call_completed"]
    assert logs[1]["cache_hit"] is True


# --- Requirement: Eventos de caché ---


def test_cache_miss_and_stored_then_hit(response_cache):
    """Requirement: Eventos de caché / Scenario: Fallo y acierto."""
    provider = stack(FakeRouter(response()), response_cache)
    with capture_logs() as first:
        provider.generate("S", MESSAGES)
    with capture_logs() as second:
        provider.generate("S", MESSAGES)
    assert [e for e in names(first) if e.startswith("cache")] == ["cache_miss", "cache_stored"]
    assert [e for e in names(second) if e.startswith("cache")] == ["cache_hit"]
    stored = events(first, "cache_stored")[0]
    assert stored["ttl"] == 60
    # Events correlate through a short hash prefix, never the full key.
    prefixes = {e["key_prefix"] for e in first + second if e["event"].startswith("cache")}
    assert len(prefixes) == 1
    assert len(prefixes.pop()) == 12


def test_truncated_response_logs_cache_skipped_with_reason(response_cache):
    """Requirement: Eventos de caché (cache_skipped con su motivo, sin guardar)."""
    truncated = stack(FakeRouter(response(finish_reason="length")), response_cache)
    with capture_logs() as logs:
        truncated.generate("S", MESSAGES)
    assert events(logs, "cache_skipped")[0]["reason"] == "truncated"


# --- Requirement: Datos que nunca se registran ---


def _production_stderr(capsys, level: str = "DEBUG") -> None:
    configure_logging(make_settings(app_env="production", log_level=level))
    capsys.readouterr()  # drop anything emitted before


def test_logs_never_contain_the_request_content_with_cache_miss_and_hit(capsys, response_cache):
    """Requirement: Datos que nunca se registran / Scenario: Log sin contenido de la petición."""
    _production_stderr(capsys)
    provider = stack(FakeRouter(response(text="respuesta")), response_cache)
    service = EstimationService(provider)
    request = make_input(description=SENTINEL)
    service.estimate(request)  # miss
    service.estimate(request)  # hit
    logged = capsys.readouterr().err
    assert "cache_miss" in logged and "cache_hit" in logged  # the pipeline really logged
    assert "prompt_rendered" in logged
    assert SENTINEL not in logged


def test_logs_never_contain_reference_projects(capsys, response_cache):
    """Requirement: Datos que nunca se registran / Scenario: Log sin proyectos de referencia."""
    _production_stderr(capsys)
    provider = stack(FakeRouter(response(text="respuesta")), response_cache)
    service = EstimationService(provider)
    request = make_input(
        reference_projects=[{"name": REFERENCE_SENTINEL, "summary": "Resumen del proyecto"}]
    )
    service.estimate(request)  # miss
    service.estimate(request)  # hit
    logged = capsys.readouterr().err
    assert "prompt_rendered" in logged
    assert REFERENCE_SENTINEL not in logged


def test_logs_never_contain_the_content_on_stream_and_failures(capsys, response_cache):
    """Requirement: Datos que nunca se registran (streaming, truncado y fallo)."""
    _production_stderr(capsys)
    items = [chunk("a"), chunk("b", "length"), usage_chunk()]
    list(stack(FakeRouter(FakeStream(items)), response_cache).stream(f"S {SENTINEL}", MESSAGES))
    failing = stack(FakeRouter(error=RuntimeError(f"upstream echo: {SENTINEL}")), response_cache)
    with pytest.raises(LLMProviderError):
        failing.generate(f"S {SENTINEL}", MESSAGES)
    logged = capsys.readouterr().err
    assert "llm_call_failed" in logged
    assert SENTINEL not in logged


def test_logs_never_contain_api_keys_when_the_error_message_has_the_key(capsys, response_cache):
    """Requirement: Datos que nunca se registran / Scenario: Log sin API keys."""
    _production_stderr(capsys)
    error = RuntimeError(f"Incorrect API key provided: {API_KEY}")
    provider = stack(FakeRouter(error=error), response_cache)
    with pytest.raises(LLMProviderError):
        provider.generate("S", MESSAGES)
    with pytest.raises(LLMProviderError):
        list(provider.stream("S", MESSAGES))
    logged = capsys.readouterr().err
    assert "llm_call_failed" in logged
    assert API_KEY not in logged
    assert "Incorrect API key" not in logged  # the message itself is not logged


def test_real_router_failure_does_not_leak_key_through_litellm_loggers(capsys, make_provider):
    """Requirement: Log sin API keys (Router real: los logs propios de LiteLLM no filtran)."""
    from tests.conftest import FlakyStream

    # Re-enable LiteLLM's loggers as they are by default, so `configure_logging` must silence them.
    for name in LITELLM_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
        logging.getLogger(name).propagate = True
    provider = make_provider(
        primary={"model": "flaky/model"},
        fallback="litellm.InternalServerError",
        with_fallback=True,
        custom={"flaky": FlakyStream(message=f"auth failed for {API_KEY}")},
    )
    _production_stderr(capsys)
    with pytest.raises(LLMProviderError):
        list(provider.stream(f"S {SENTINEL}", MESSAGES))
    logged = capsys.readouterr().err
    assert "llm_call_failed" in logged
    assert API_KEY not in logged
    assert SENTINEL not in logged


def test_http_request_logs_through_the_app_never_include_the_description(
    capsys, monkeypatch, response_cache
):
    """Requirement: Log sin contenido de la petición (extremo a extremo por la API)."""
    monkeypatch.setenv("OPENAI_API_KEY", API_KEY)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    provider = stack(FakeRouter(response(text="respuesta")), response_cache)
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    try:
        with TestClient(app) as client:
            for _ in range(2):  # miss, then hit
                body = client.post(
                    "/api/v1/estimate",
                    json=valid_body(
                        description=SENTINEL,
                        reference_projects=[{"name": REFERENCE_SENTINEL, "summary": "Resumen"}],
                    ),
                ).json()
                assert body["text"] == "respuesta"
    finally:
        app.dependency_overrides.clear()
    logged = capsys.readouterr().err
    assert "cache_hit" in logged
    assert SENTINEL not in logged
    assert REFERENCE_SENTINEL not in logged
    assert API_KEY not in logged
