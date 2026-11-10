"""Tests for `POST /api/v1/estimate/stream` and `GET /api/v1/context`.

Specs: streaming-api, estimation-api (Contexto CAG de solo lectura) and observability
(eventos del streaming por HTTP), change s03-add-sse-streaming-and-docker. Offline: the LLM is
a `FakeProvider` injected through `dependency_overrides`; the cache is `fakeredis`.
"""

import json
import socket
import threading
import time
from collections.abc import Generator, Sequence

import httpx
import pytest
import uvicorn
from structlog.testing import capture_logs

from app.config import get_settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.main import app
from app.routers import estimations
from app.routers.estimations import PROVIDER_ERROR_DETAIL, estimate_stream_events, stream_closer
from app.schemas import EstimationMetrics, EstimationResponse, StreamEstimationRequest
from app.services import llm_service
from app.services.llm_service import (
    MAX_TRANSCRIPTION_CHARS,
    CachedProvider,
    ChatMessage,
    EstimationService,
    LLMProviderError,
    LLMResult,
    LLMStream,
    prompt_context,
    system_prompt_for,
)
from tests.conftest import FakeProvider

STREAM = "/api/v1/estimate/stream"
CONTEXT = "/api/v1/context"
USER = {"role": "user", "content": "Necesitamos una web de reservas"}
METRICS_FIELDS = {
    "model",
    "provider",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "truncated",
    "fallback_used",
    "cache_hit",
    "latency_ms",
}


def parse_sse(body: str) -> list[tuple[str, str]]:
    """Parse a `text/event-stream` body into (event, data) pairs, ignoring comments."""
    parsed: list[tuple[str, str]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith(":") or not line:
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
        if data:
            parsed.append((event, "\n".join(data)))
    return parsed


def post(client, provider, messages=None):
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    return client.post(STREAM, json={"messages": messages if messages is not None else [USER]})


def names(events) -> list[str]:
    return [name for name, _ in events]


def conversation(count: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"mensaje {i}"}
        for i in range(count)
    ]


# --- Requirement: Endpoint de estimación en streaming ---


def test_first_turn_streams_three_tokens_then_metrics(client):
    """Requirement: Endpoint en streaming / Scenario: Primer turno."""
    fake = FakeProvider(fragments=["Uno ", "dos ", "tres"])
    response = post(client, fake)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert names(events) == ["token", "token", "token", "metrics"]
    assert [json.loads(data) for _, data in events[:3]] == ["Uno ", "dos ", "tres"]
    assert fake.call_count == 1
    system, messages = fake.calls[0]
    assert messages == [USER]
    assert system == llm_service._system_prompt()


def test_adjustment_turn_sends_three_messages_with_conversation_prompt(client):
    """Requirement: Endpoint en streaming / Scenario: Turno de ajuste."""
    fake = FakeProvider()
    messages = [
        USER,
        {"role": "assistant", "content": "Estimación previa"},
        {"role": "user", "content": "Añade un panel de administración"},
    ]
    assert post(client, fake, messages).status_code == 200
    system, received = fake.calls[0]
    assert received == messages
    assert system == llm_service._chat_system_prompt()
    assert system != llm_service._system_prompt()


def test_message_is_trimmed_before_reaching_the_provider(client):
    """Requirement: Endpoint en streaming / Scenario: Espacios alrededor de los mensajes."""
    fake = FakeProvider()
    post(client, fake, [{"role": "user", "content": "  T  \n"}])
    assert fake.calls[0][1] == [{"role": "user", "content": "T"}]


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "assistant", "content": "E"}],
        [USER, {"role": "assistant", "content": "E"}],
        [USER, USER],
        [USER, {"role": "assistant", "content": "E"}, {"role": "assistant", "content": "F"}],
        [{"role": "user", "content": "   \n"}],
        [{"role": "user", "content": ""}],
        [{"role": "system", "content": "x"}],
    ],
    ids=[
        "empty",
        "starts-with-assistant",
        "ends-with-assistant",
        "same-role-twice",
        "assistant-twice",
        "blank-content",
        "empty-content",
        "unknown-role",
    ],
)
def test_invalid_conversation_is_422_json_without_calling_the_llm(client, messages):
    """Requirement: Endpoint en streaming / Scenario: Conversación inválida."""
    fake = FakeProvider()
    response = post(client, fake, messages)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert fake.call_count == 0


@pytest.mark.parametrize("payload", [{}, {"messages": "hola"}, {"messages": [{"role": "user"}]}])
def test_malformed_body_is_422_without_calling_the_llm(client, payload):
    """Requirement: Endpoint en streaming / Scenario: Conversación inválida (cuerpo mal formado)."""
    fake = FakeProvider()
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: fake
    assert client.post(STREAM, json=payload).status_code == 422
    assert fake.call_count == 0


def test_message_over_the_limit_is_422_and_at_the_limit_is_accepted(client):
    """Requirement: Endpoint en streaming / Scenario: Mensaje demasiado largo."""
    fake = FakeProvider()
    too_long = [{"role": "user", "content": "x" * (MAX_TRANSCRIPTION_CHARS + 1)}]
    assert post(client, fake, too_long).status_code == 422
    assert fake.call_count == 0
    exact = [{"role": "user", "content": " " + "x" * MAX_TRANSCRIPTION_CHARS + " "}]
    assert post(client, fake, exact).status_code == 200
    assert fake.call_count == 1


def test_valid_conversation_of_21_messages_is_processed(client):
    """Requirement: Endpoint en streaming / Scenario: Sin límite de mensajes."""
    fake = FakeProvider()
    messages = conversation(21)
    response = post(client, fake, messages)
    assert response.status_code == 200
    assert names(parse_sse(response.text))[-1] == "metrics"
    assert len(fake.calls[0][1]) == 21


def test_per_turn_prompt_matches_system_prompt_for(client):
    """Requirement: Endpoint en streaming (el system prompt es el de `system_prompt_for`)."""
    for count in (1, 3, 5):
        fake = FakeProvider()
        post(client, fake, conversation(count))
        assert fake.calls[0][0] == system_prompt_for(fake.calls[0][1])


def test_openapi_documents_the_stream_operation(client):
    """Requirement: Endpoint en streaming / Scenario: Documentado en OpenAPI."""
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][STREAM]["post"]
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/StreamEstimationRequest")
    assert "StreamEstimationRequest" in spec["components"]["schemas"]


def test_stream_request_exposes_no_generation_parameters():
    """Requirement: Endpoint en streaming (no acepta parámetros de generación)."""
    assert set(StreamEstimationRequest.model_fields) == {"messages"}


# --- Requirement: Formato de los eventos ---


def test_multiline_fragment_arrives_intact_in_a_single_data_line(client):
    """Requirement: Formato de los eventos / Scenario: Fragmento con saltos de línea."""
    fragment = "## Título\n\n| A | B |"
    response = post(client, FakeProvider(fragments=[fragment]))
    token_blocks = [b for b in response.text.split("\n\n") if "event: token" in b]
    assert len(token_blocks) == 1
    data_lines = [line for line in token_blocks[0].splitlines() if line.startswith("data:")]
    assert len(data_lines) == 1
    events = parse_sse(response.text)
    assert json.loads(events[0][1]) == fragment


def test_non_ascii_fragment_roundtrips_exactly(client):
    """Requirement: Formato de los eventos (los fragmentos con tildes y emoji se decodifican)."""
    fragment = "Estimación: 3 días ✓ 日本"
    events = parse_sse(post(client, FakeProvider(fragments=[fragment])).text)
    assert json.loads(events[0][1]) == fragment


def test_metrics_payload_has_exact_fields_and_no_estimation(client):
    """Requirement: Formato de los eventos / Scenario: Payload de métricas."""
    fake = FakeProvider(
        text="x",
        model="claude-haiku-4-5",
        provider="anthropic",
        input_tokens=1200,
        output_tokens=300,
        fallback_used=True,
        cache_hit=True,
    )
    events = parse_sse(post(client, fake).text)
    assert names(events) == ["token", "metrics"]
    payload = json.loads(events[-1][1])
    assert set(payload) == METRICS_FIELDS
    assert "estimation" not in payload
    assert payload["input_tokens"] == 1200
    assert payload["output_tokens"] == 300
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["provider"] == "anthropic"
    assert payload["fallback_used"] is True
    assert payload["cache_hit"] is True
    assert payload["truncated"] is False
    assert isinstance(payload["cost_usd"], float)
    assert isinstance(payload["latency_ms"], int)


RESPONSE_ONLY_FIELDS = {
    "preprocessing",
    "extracted_requirements",
    "preprocessing_input_tokens",
    "preprocessing_output_tokens",
    "preprocessing_cost_usd",
    "validation",
}


def test_metrics_fields_match_the_estimation_response_minus_estimation():
    """Requirement: Formato de los eventos (campos de `EstimationResponse` salvo `estimation`)."""
    assert set(EstimationMetrics.model_fields) == METRICS_FIELDS
    # Fields added to EstimationResponse only (S02 generation-options change): the SSE
    # `metrics` contract (EstimationMetrics) stays unchanged.
    response_only = {"estimation", *RESPONSE_ONLY_FIELDS}
    assert set(EstimationResponse.model_fields) == METRICS_FIELDS | response_only
    for name, field in EstimationMetrics.model_fields.items():
        assert EstimationResponse.model_fields[name].annotation == field.annotation


def test_truncated_generation_sets_truncated_in_metrics(client):
    """Requirement: Formato de los eventos / Scenario: Respuesta truncada."""
    events = parse_sse(post(client, FakeProvider(truncated=True)).text)
    assert json.loads(events[-1][1])["truncated"] is True


def test_cache_hit_streams_one_token_with_stored_text_then_metrics(client, response_cache):
    """Requirement: Formato de los eventos / Scenario: Acierto de caché."""
    inner = FakeProvider(fragments=["Parte 1 ", "parte 2"])
    cached = CachedProvider(inner, response_cache, "gpt-4o-mini")
    first = parse_sse(post(client, cached).text)
    assert names(first) == ["token", "token", "metrics"]
    assert json.loads(first[-1][1])["cache_hit"] is False
    second = parse_sse(post(client, cached).text)
    assert names(second) == ["token", "metrics"]
    assert json.loads(second[0][1]) == "Parte 1 parte 2"
    assert json.loads(second[-1][1])["cache_hit"] is True
    assert inner.call_count == 1


def test_stream_emits_only_token_and_metrics_events_and_no_done(client):
    """Requirement: Formato de los eventos (solo token/metrics/error, sin `done`)."""
    events = parse_sse(post(client, FakeProvider(fragments=["a", "b"])).text)
    assert set(names(events)) <= {"token", "metrics", "error"}
    assert names(events).count("metrics") == 1


# --- Requirement: Errores durante el streaming ---


def test_provider_down_before_first_fragment_yields_a_single_error_event(client):
    """Requirement: Errores durante el streaming / Scenario: Proveedor caído antes del 1er frag."""
    fake = FakeProvider(error=LLMProviderError("openai"), error_after=0, fragments=["x"])
    response = post(client, fake)
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events == [("error", json.dumps({"detail": PROVIDER_ERROR_DETAIL}))]


def test_failure_mid_stream_yields_two_tokens_then_error_and_no_metrics(client):
    """Requirement: Errores durante el streaming / Scenario: Fallo a mitad del stream."""
    fake = FakeProvider(error=LLMProviderError("openai"), error_after=2, fragments=["a", "b", "c"])
    events = parse_sse(post(client, fake).text)
    assert names(events) == ["token", "token", "error"]
    assert json.loads(events[-1][1]) == {"detail": "LLM provider error"}


def test_error_event_leaks_nothing_from_the_underlying_exception(client):
    """Requirement: Errores durante el streaming / Scenario: Error sin detalles."""
    secret = "sk-test-visible-value"
    cause = RuntimeError(f"auth failed with {secret}")
    error = LLMProviderError("openai")
    error.__cause__ = cause
    for fake in (
        FakeProvider(error=error, error_after=0),
        FakeProvider(error=error, error_after=1, fragments=["a", "b"]),
    ):
        response = post(client, fake)
        assert secret not in response.text
        assert "Traceback" not in response.text


class Bug(Exception):
    pass


def test_unexpected_exception_is_a_generic_error_event_logged_by_type_only(client):
    """Requirement: Errores durante el streaming (excepción inesperada) / Observability."""
    secret = "sk-test-visible-value"
    fake = FakeProvider(error=Bug(secret), error_after=1, fragments=["a", "b"])  # type: ignore[arg-type]
    with capture_logs() as logs:
        response = post(client, fake)
    events = parse_sse(response.text)
    assert names(events) == ["token", "error"]
    assert json.loads(events[-1][1]) == {"detail": PROVIDER_ERROR_DETAIL}
    assert secret not in response.text
    failed = [e for e in logs if e["event"] == "estimate_stream_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == "Bug"
    assert secret not in json.dumps(logs, default=str)


def test_provider_error_is_not_logged_as_unexpected_failure(client):
    """Requirement: Eventos del streaming por HTTP (`estimate_stream_failed` solo si no es del
    proveedor)."""
    fake = FakeProvider(error=LLMProviderError("openai"), error_after=0, fragments=["x"])
    with capture_logs() as logs:
        post(client, fake)
    assert not [e for e in logs if e["event"] == "estimate_stream_failed"]


def test_empty_reply_is_streamed_as_metrics_without_tokens(client):
    """Requirement: Errores durante el streaming / Scenario: Respuesta vacía."""
    events = parse_sse(post(client, FakeProvider(fragments=[])).text)
    assert names(events) == ["metrics"]


# --- Requirement: Desconexión del cliente (function level, design D4) ---


class _SlowList(list):
    """List that sleeps before yielding each item, to keep a stream open in real-HTTP tests."""

    def __init__(self, items, delay: float) -> None:
        super().__init__(items)
        self.delay = delay

    def __iter__(self):
        for item in super().__iter__():
            time.sleep(self.delay)
            yield item


class ClosableProvider(FakeProvider):
    """FakeProvider whose stream records that it was closed by the consumer."""

    def __init__(self, delay: float = 0.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.closed = False
        if delay:
            self.fragments = _SlowList(self.fragments, delay)

    def _stream(self) -> Generator[str, None, LLMResult]:
        try:
            return (yield from super()._stream())
        finally:
            self.closed = True


def start_events(provider: ClosableProvider, messages: Sequence[ChatMessage]):
    request = StreamEstimationRequest(messages=list(messages))  # type: ignore[arg-type]
    return estimate_stream_events(request, EstimationService(provider))


def test_closing_the_generator_closes_the_provider_stream_and_logs_cancellation():
    """Requirement: Desconexión del cliente / Scenario: Cliente que se desconecta;
    Observability / Scenario: Cancelación registrada (fragments = 2)."""
    provider = ClosableProvider(fragments=["a", "b", "c", "d"])
    events = start_events(provider, [USER])  # type: ignore[arg-type]
    with capture_logs() as logs:
        assert next(events).event == "token"
        assert next(events).event == "token"
        assert provider.closed is False
        events.close()
    assert provider.closed is True
    cancelled = [e for e in logs if e["event"] == "estimate_stream_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["fragments"] == 2


def test_stream_closer_teardown_closes_registered_stream_and_logs_cancellation():
    """Requirement: Desconexión del cliente / Scenario: Cliente que se desconecta (el cierre
    lo hace la dependencia `stream_closer` al terminar la petición);
    Observability / Scenario: Cancelación registrada (fragments = 2)."""
    provider = ClosableProvider(fragments=["a", "b", "c", "d"])
    dependency = stream_closer()
    streams = next(dependency)
    events = start_events(provider, [USER])  # type: ignore[arg-type]
    streams.append(events)
    with capture_logs() as logs:
        assert next(events).event == "token"
        assert next(events).event == "token"
        assert provider.closed is False
        dependency.close()
    assert provider.closed is True
    cancelled = [e for e in logs if e["event"] == "estimate_stream_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["fragments"] == 2


def test_stream_closer_teardown_of_an_exhausted_stream_logs_no_cancellation():
    """Requirement: Desconexión del cliente (un stream completo no es una cancelación);
    Observability / Scenario: Cancelación registrada."""
    provider = ClosableProvider(fragments=["a", "b"])
    dependency = stream_closer()
    streams = next(dependency)
    events = start_events(provider, [USER])  # type: ignore[arg-type]
    streams.append(events)
    with capture_logs() as logs:
        assert [e.event for e in events] == ["token", "token", "metrics"]
        dependency.close()
    assert not [e for e in logs if e["event"] == "estimate_stream_cancelled"]


def test_endpoint_registers_exactly_one_stream_in_stream_closer(client):
    """Requirement: Desconexión del cliente (el endpoint registra su generador en
    `stream_closer` y la dependencia se desmonta al terminar la petición)."""
    registered: list[list] = []
    torn_down: list[bool] = []

    def recording_closer():
        streams: list = []
        registered.append(streams)
        try:
            yield streams
        finally:
            torn_down.append(True)
            for stream in streams:
                stream.close()

    app.dependency_overrides[stream_closer] = recording_closer
    response = post(client, FakeProvider(fragments=["a", "b"]))
    assert response.status_code == 200
    assert names(parse_sse(response.text)) == ["token", "token", "metrics"]
    assert len(registered) == 1
    assert len(registered[0]) == 1
    assert torn_down == [True]


# Captured at import time, before the autouse `_no_network` fixture patches `socket`.
_REAL_SOCKET_FUNCTIONS = {
    (socket.socket, "connect"): socket.socket.connect,
    (socket.socket, "connect_ex"): socket.socket.connect_ex,
    (socket, "getaddrinfo"): socket.getaddrinfo,
    (socket, "create_connection"): socket.create_connection,
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_real_http_disconnect_closes_the_provider_stream_and_logs_cancellation(
    monkeypatch, settings
):
    """Requirement: Desconexión del cliente / Scenario: Cliente que se desconecta (corte HTTP
    real contra uvicorn); Observability / Scenario: Cancelación registrada."""
    # Loopback only: the suite-wide network guard is lifted for this one test.
    for (owner, name), original in _REAL_SOCKET_FUNCTIONS.items():
        monkeypatch.setattr(owner, name, original)
    provider = ClosableProvider(fragments=[f"f{i} " for i in range(200)], delay=0.02)
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    app.dependency_overrides[get_settings] = lambda: settings
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    with capture_logs() as logs:
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with (
                httpx.Client(timeout=5) as http,
                http.stream(
                    "POST", f"http://127.0.0.1:{port}{STREAM}", json={"messages": [USER]}
                ) as r,
            ):
                for line in r.iter_lines():
                    if line.startswith("event: token"):
                        break
            deadline = time.monotonic() + 3
            while not provider.closed and time.monotonic() < deadline:
                time.sleep(0.02)
            # Asserted while the server is still up: closing at shutdown does not count.
            closed_while_running = provider.closed
            cancelled = [e for e in logs if e["event"] == "estimate_stream_cancelled"]
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    assert closed_while_running is True
    assert len(cancelled) == 1


def test_disconnect_stores_nothing_in_the_cache(fake_redis, response_cache):
    """Requirement: Desconexión del cliente (la caché no guarda ninguna entrada)."""
    inner = ClosableProvider(fragments=["a", "b", "c"])
    cached = CachedProvider(inner, response_cache, "gpt-4o-mini")
    request = StreamEstimationRequest(messages=[USER])  # type: ignore[list-item]
    events = estimate_stream_events(request, EstimationService(cached))
    next(events)
    events.close()
    assert inner.closed is True
    assert fake_redis.keys("*") == []


def test_no_cancellation_logged_when_the_stream_completes(client):
    """Requirement: Eventos del streaming por HTTP (cancelación solo si se corta)."""
    with capture_logs() as logs:
        post(client, FakeProvider(fragments=["a", "b"]))
    assert not [e for e in logs if e["event"] == "estimate_stream_cancelled"]


def test_router_module_exposes_the_generic_detail_constant():
    """Requirement: Errores durante el streaming (detalle exacto)."""
    assert estimations.PROVIDER_ERROR_DETAIL == "LLM provider error"


# --- Requirement: Contexto CAG de solo lectura ---


def test_context_first_turn_matches_the_endpoint_prompt(client):
    """Requirement: Contexto CAG / Scenario: Primer turno."""
    response = client.get(CONTEXT)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"system_prompt", "examples", "max_message_chars"}
    assert body["system_prompt"] == llm_service._system_prompt()
    assert body["examples"] == [
        {"meeting_summary": e["meeting_summary"], "estimation": e["estimation"]}
        for e in ESTIMATION_EXAMPLES
    ]
    assert body["max_message_chars"] == 20000 == MAX_TRANSCRIPTION_CHARS


def test_context_with_default_equals_message_count_one(client):
    """Requirement: Contexto CAG (`message_count` opcional, por defecto 1)."""
    assert client.get(CONTEXT).json() == client.get(CONTEXT, params={"message_count": 1}).json()


def test_context_adjustment_turn_has_the_conversation_section(client):
    """Requirement: Contexto CAG / Scenario: Turno de ajuste."""
    body = client.get(CONTEXT, params={"message_count": 3}).json()
    assert body["system_prompt"] == llm_service._chat_system_prompt()
    assert body["system_prompt"] != client.get(CONTEXT).json()["system_prompt"]


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_context_rejects_invalid_message_count(client, value):
    """Requirement: Contexto CAG / Scenario: Parámetro inválido."""
    assert client.get(CONTEXT, params={"message_count": value}).status_code == 422


def test_context_does_not_call_the_llm(client, provider):
    """Requirement: Contexto CAG / Scenario: Sin llamada al LLM."""
    client.get(CONTEXT)
    client.get(CONTEXT, params={"message_count": 3})
    assert provider.call_count == 0


def test_openapi_documents_the_context_operation(client):
    """Requirement: Contexto CAG / Scenario: Documentado en OpenAPI."""
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][CONTEXT]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ContextResponse")
    parameter = next(p for p in operation["parameters"] if p["name"] == "message_count")
    assert parameter["required"] is False
    assert parameter["schema"]["minimum"] == 1


# --- Services: prompt_context / system_prompt_for (tasks 2.1) ---


def test_prompt_context_follows_the_turn_rule_and_lists_examples():
    """Design D5: `prompt_context` uses the same turn rule as `system_prompt_for`."""
    assert prompt_context(1).system_prompt == llm_service._system_prompt()
    assert prompt_context(2).system_prompt == llm_service._chat_system_prompt()
    assert prompt_context(3).system_prompt == llm_service._chat_system_prompt()
    assert list(prompt_context(1).examples) == list(ESTIMATION_EXAMPLES)


def test_prompt_context_rejects_less_than_one_message():
    """Design D5: `prompt_context(0)` raises ValueError."""
    with pytest.raises(ValueError):
        prompt_context(0)


def test_system_prompt_for_rejects_an_empty_conversation():
    """Design D5: `system_prompt_for([])` raises ValueError."""
    with pytest.raises(ValueError):
        system_prompt_for([])


def test_llm_stream_type_is_exported_for_doubles():
    """Sanity: the doubles in this module build real `LLMStream` objects."""
    assert isinstance(ClosableProvider().stream("s", [USER]), LLMStream)  # type: ignore[list-item]
