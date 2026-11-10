"""Tests for the Streamlit chat UI (spec: chat-ui) run without a browser via AppTest.

The UI is an HTTP client of the API. `httpx.Client` is patched with a factory (design D7):
`httpx.MockTransport` for the protocol cases and `TestClient(app)` (ASGI transport) for the
end-to-end ones. No network, no real server, no real LLM.

Manual checks (task 7.2, not automatable with AppTest): scenarios "Arranque de la interfaz",
"Texto incremental" (the incremental rendering; AppTest only sees the final text) and
"Generación interrumpida" / "Panel visible tras interrumpir" with a real Stop button (here only
approximated by a BaseException raised mid-stream).
"""

import ast
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from structlog.testing import capture_logs

from app.config import get_settings
from app.main import app
from app.services import llm_service
from app.services.cache import get_response_cache
from app.services.llm_service import prompt_context
from tests.conftest import FakeProvider, make_settings

APP_PATH = Path(__file__).resolve().parent.parent / "streamlit_app.py"
BASE_URL = "http://api.test"
GENERIC_ERROR = "No se pudo generar la estimación. Inténtalo de nuevo en unos instantes."
FAKE_OPENAI_KEY = "sk-fake-ui-SECRET-123"
FAKE_ANTHROPIC_KEY = "sk-ant-fake-ui-SECRET-456"
REAL_CLIENT = httpx.Client

HEALTH = {
    "status": "ok",
    "env": "development",
    "primary_model": "gpt-4o-mini",
    "fallback_model": "claude-haiku-4-5",
    "cache": "ok",
}
METRICS = {
    "model": "gpt-4o-mini",
    "provider": "openai",
    "input_tokens": 1200,
    "output_tokens": 300,
    "cost_usd": 0.00054,
    "truncated": False,
    "fallback_used": False,
    "cache_hit": False,
    "latency_ms": 900,
}


# --- SSE builders ---


def token(text: str) -> str:
    return f"event: token\ndata: {json.dumps(text)}\n\n"


def metrics_event(**overrides) -> str:
    return f"event: metrics\ndata: {json.dumps({**METRICS, **overrides})}\n\n"


def error_event() -> str:
    return 'event: error\ndata: {"detail": "LLM provider error"}\n\n'


PING = ": ping\n\n"


class BrokenStream(httpx.SyncByteStream):
    """Delivers `head` and then fails; records `close()` (the connection being released)."""

    def __init__(self, head: str, failure: BaseException) -> None:
        self.head = head
        self.failure = failure
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield self.head.encode()
        raise self.failure

    def close(self) -> None:
        self.closed = True


class FakeApi:
    """In-memory API behind `httpx.MockTransport`: records requests, configurable per test."""

    def __init__(self) -> None:
        self.health: dict | None = dict(HEALTH)
        self.context_status = 200
        self.max_chars: int | None = 20000
        self.down = False  # every request raises ConnectError
        self.stream_down = False  # only the stream raises ConnectError
        self.stream_status = 200
        self.stream_body: str | httpx.SyncByteStream | Callable[[], str] = token("E") + (
            metrics_event()
        )
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/health":
            if self.health is None:
                return httpx.Response(503)
            return httpx.Response(200, json=self.health)
        if path == "/api/v1/context":
            if self.context_status != 200:
                return httpx.Response(self.context_status, json={"detail": "boom"})
            count = int(request.url.params.get("message_count", "1"))
            ctx = prompt_context(count)
            return httpx.Response(
                200,
                json={
                    "system_prompt": ctx.system_prompt,
                    "examples": [dict(e) for e in ctx.examples],
                    "max_message_chars": self.max_chars,
                },
            )
        if path == "/api/v1/estimate/stream":
            if self.stream_down:
                raise httpx.ConnectError("connection refused", request=request)
            if self.stream_status != 200:
                return httpx.Response(self.stream_status, json={"detail": "SECRET-DETAIL"})
            body = self.stream_body() if callable(self.stream_body) else self.stream_body
            headers = {"content-type": "text/event-stream"}
            if isinstance(body, str):
                return httpx.Response(200, content=body.encode(), headers=headers)
            return httpx.Response(200, stream=body, headers=headers)
        return httpx.Response(404)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    @property
    def stream_calls(self) -> list[httpx.Request]:
        return self.calls("/api/v1/estimate/stream")

    def sent_messages(self, index: int = -1) -> list[dict]:
        return json.loads(self.stream_calls[index].content)["messages"]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()

    def factory(*args, **kwargs) -> httpx.Client:
        return REAL_CLIENT(*args, transport=httpx.MockTransport(fake.handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    monkeypatch.setenv("ESTIMATOR_API_BASE_URL", BASE_URL)
    return fake


def run_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=10).run()


def send(at: AppTest, text: str) -> AppTest:
    at.chat_input[0].set_value(text).run()
    return at


def history(at: AppTest) -> list[tuple[str, str]]:
    return [(m["role"], m["content"]) for m in at.session_state["messages"]]


def sidebar_text(at: AppTest) -> str:
    chunks = [e.value for e in at.sidebar.markdown] + [e.value for e in at.sidebar.caption]
    chunks += [e.value for e in at.sidebar.text_area] + [e.value for e in at.sidebar.warning]
    return "\n".join(chunks)


def all_text(at: AppTest) -> str:
    chunks: list[str] = []

    def walk(block) -> None:
        for child in block.children.values():
            for attr in ("value", "label"):
                value = getattr(child, attr, None)
                if isinstance(value, str):
                    chunks.append(value)
            if hasattr(child, "children"):
                walk(child)

    walk(at.main)
    walk(at.sidebar)
    return "\n".join(chunks)


def errors(at: AppTest) -> list[str]:
    return [e.value for e in at.error]


def sent_text_of_logs(logs: list[dict]) -> str:
    return json.dumps(logs, default=str)


def unavailable(url: str = BASE_URL) -> str:
    return (
        f"No se puede conectar con el servicio de estimación (`{url}`). "
        "Arráncalo con `uv run uvicorn app.main:app` o `docker compose up`."
    )


# --- Requirement: Aplicación Streamlit de chat ---


def test_app_runs_without_exceptions_and_shows_chat_input(api):
    """Requirement: Aplicación Streamlit de chat (arranque sin errores en AppTest)."""
    at = run_app()
    assert not at.exception
    assert len(at.chat_input) == 1
    assert at.title[0].value == "Estimador de software"


def test_transcription_gets_an_assistant_answer_in_history(api):
    """Requirement: Aplicación Streamlit de chat / Scenario: Estimación de una transcripción."""
    api.stream_body = token("ESTIMACION-1") + metrics_event()
    at = send(run_app(), "Necesitamos una web")
    assert not at.exception
    assert history(at) == [("user", "Necesitamos una web"), ("assistant", "ESTIMACION-1")]
    shown = [(m.name, m.markdown[0].value) for m in at.chat_message]
    assert shown == [("user", "Necesitamos una web"), ("assistant", "ESTIMACION-1")]


def test_request_is_posted_to_the_stream_endpoint_with_the_first_message(api):
    """Requirement: Aplicación Streamlit de chat / Scenario: Petición enviada a la API (turno 1)."""
    send(run_app(), "T")
    assert len(api.stream_calls) == 1
    request = api.stream_calls[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/api/v1/estimate/stream"
    assert json.loads(request.content) == {"messages": [{"role": "user", "content": "T"}]}


def test_adjustment_posts_the_three_messages_in_order(api):
    """Requirement: Aplicación Streamlit de chat / Scenario: Petición enviada a la API."""
    api.stream_body = token("E") + metrics_event()
    at = send(run_app(), "T")
    send(at, "quita la sección de blog")
    assert api.sent_messages() == [
        {"role": "user", "content": "T"},
        {"role": "assistant", "content": "E"},
        {"role": "user", "content": "quita la sección de blog"},
    ]


def test_streamlit_app_has_no_service_or_llm_imports():
    """Requirement: Aplicación Streamlit de chat / Scenario: Sin imports del servicio."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.level == 0
            imported.add(node.module.split(".")[0])
    assert not imported & {"app", "openai", "anthropic", "litellm", "redis"}


def test_module_level_httpx_helpers_are_not_used():
    """Design D6: only `httpx.Client` in a `with`, never `httpx.get`/`httpx.stream`/`post`."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "httpx"
    }
    assert not used & {"get", "post", "stream", "request", "put", "delete"}


# --- Requirement: Historial de la conversación (spec vigente) ---


def test_several_messages_in_a_row_are_rendered_in_order(api):
    """Requirement: Historial de la conversación / Scenario: Varios mensajes seguidos."""
    at = send(run_app(), "T")
    api.stream_body = token("E2") + metrics_event()
    send(at, "ajuste")
    shown = [(m.name, m.markdown[0].value) for m in at.chat_message]
    assert shown == [("user", "T"), ("assistant", "E"), ("user", "ajuste"), ("assistant", "E2")]


def test_new_session_starts_with_empty_history(api):
    """Requirement: Historial de la conversación / Scenario: Sesión nueva."""
    send(run_app(), "T")
    fresh = run_app()
    assert fresh.session_state["messages"] == []
    assert len(fresh.chat_message) == 0


def test_new_conversation_clears_history_and_metrics_and_next_send_is_single(api):
    """Requirement: Nueva conversación / Scenario: Empezar de cero."""
    at = send(run_app(), "T")
    assert at.session_state["last_metrics"] is not None
    calls_before = len(api.stream_calls)
    at.sidebar.button[0].click().run()
    assert at.sidebar.button[0].label == "Nueva conversación"
    assert at.session_state["messages"] == []
    assert at.session_state["last_metrics"] is None
    assert len(at.chat_message) == 0
    assert "Aún no hay métricas" in sidebar_text(at)
    assert len(api.stream_calls) == calls_before
    send(at, "T2")
    assert api.sent_messages() == [{"role": "user", "content": "T2"}]


# --- Requirement: Respuesta en streaming ---


def test_saved_message_is_the_concatenation_of_all_fragments(api):
    """Requirement: Respuesta en streaming / Scenario: Texto incremental (texto guardado)."""
    api.stream_body = token("## Esti") + token("mación: ") + token("web") + metrics_event()
    at = send(run_app(), "T")
    assert history(at)[1] == ("assistant", "## Estimación: web")


def test_multiline_fragment_is_saved_intact(api):
    """Requirement: Respuesta en streaming (fragmento multilínea decodificado del JSON)."""
    api.stream_body = token("## Título\n\n| A | B |") + token("\n| 1 | 2 |") + metrics_event()
    at = send(run_app(), "T")
    assert history(at)[1] == ("assistant", "## Título\n\n| A | B |\n| 1 | 2 |")


def test_keep_alive_comments_do_not_change_the_answer(api):
    """Requirement: Respuesta en streaming / Scenario: Keep-alive."""
    plain = send(run_app(), "T")
    expected = history(plain)
    api.stream_body = PING + token("E") + PING + PING + metrics_event() + PING
    at = send(run_app(), "T")
    assert history(at) == expected
    assert not errors(at)


def test_unknown_events_are_ignored(api):
    """Requirement: Respuesta en streaming (eventos desconocidos ignorados)."""
    api.stream_body = "event: weird\ndata: {}\n\n" + token("E") + metrics_event()
    at = send(run_app(), "T")
    assert history(at) == [("user", "T"), ("assistant", "E")]


# --- Requirement: Validación del mensaje del usuario ---


def test_message_is_trimmed_before_sending_and_saving(api):
    """Requirement: Validación del mensaje / Scenario: Espacios alrededor del mensaje."""
    at = send(run_app(), "  T  \n")
    assert api.sent_messages() == [{"role": "user", "content": "T"}]
    assert history(at)[0] == ("user", "T")


def test_message_over_the_api_limit_is_rejected_without_calling_the_stream(api):
    """Requirement: Validación del mensaje / Scenario: Mensaje demasiado largo."""
    at = send(run_app(), "x" * 20001)
    assert [w.value for w in at.warning] == [
        "El mensaje supera el límite de 20000 caracteres (20001)."
    ]
    assert api.stream_calls == []
    assert history(at) == []


def test_message_over_limit_after_a_turn_keeps_history_unchanged(api):
    """Requirement: Validación del mensaje / Scenario: Mensaje demasiado largo (con historial)."""
    at = send(run_app(), "T")
    before = history(at)
    calls = len(api.stream_calls)
    send(at, "x" * 20001)
    assert history(at) == before
    assert len(api.stream_calls) == calls


def test_limit_follows_what_the_api_reports(api):
    """Requirement: Validación del mensaje (límite obtenido de `max_message_chars`)."""
    api.max_chars = 10
    at = send(run_app(), "x" * 11)
    assert api.stream_calls == []
    assert "límite de 10 caracteres (11)" in at.warning[0].value
    send(at, "x" * 10)
    assert len(api.stream_calls) == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_message_is_rejected_without_calling_the_stream(api, blank):
    """Requirement: Validación del mensaje / Scenario: Mensaje en blanco."""
    at = send(run_app(), blank)
    assert [w.value for w in at.warning] == ["El mensaje está vacío."]
    assert api.stream_calls == []
    assert history(at) == []


def test_without_the_limit_only_empty_messages_are_rejected_and_the_api_decides(api):
    """Requirement: Validación del mensaje (sin `max_message_chars`, el límite lo pone la API)."""
    api.context_status = 500
    api.stream_status = 422
    at = run_app()
    send(at, "   ")
    assert api.stream_calls == []
    send(at, "x" * 20001)
    assert len(api.stream_calls) == 1
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []


# --- Requirement: Errores del proveedor en la interfaz ---


def test_error_event_shows_generic_error_and_keeps_history(api):
    """Requirement: Errores del proveedor / Scenario: Proveedor caído."""
    api.stream_body = error_event()
    with capture_logs() as logs:
        at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []
    assert not at.exception
    assert [e["reason"] for e in logs if e["event"] == "api_stream_failed"] == ["error_event"]
    assert len(at.chat_input) == 1


def test_failure_mid_stream_saves_neither_user_message_nor_partial_text(api):
    """Requirement: Errores del proveedor / Scenario: Fallo a mitad del streaming."""
    api.stream_body = token("parcial ") + token("otro") + error_event()
    at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []


def test_failed_call_keeps_previous_history_and_metrics(api):
    """Requirement: Métricas / Scenario: Llamada fallida; Errores / reenvío del mensaje."""
    api.stream_body = token("E") + metrics_event(input_tokens=1234)
    at = send(run_app(), "T")
    before = history(at)
    api.stream_body = token("parcial") + error_event()
    send(at, "ajuste")
    assert history(at) == before
    assert "1234" in sidebar_text(at)
    assert at.session_state["last_metrics"].input_tokens == 1234
    api.stream_body = token("E2") + metrics_event()
    send(at, "ajuste")
    assert history(at)[-2:] == [("user", "ajuste"), ("assistant", "E2")]


def test_stream_without_terminal_event_shows_generic_error_and_saves_nothing(api):
    """Requirement: Errores del proveedor / Scenario: Stream sin evento terminal."""
    api.stream_body = token("a") + PING + token("b")
    with capture_logs() as logs:
        at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []
    assert [e["reason"] for e in logs if e["event"] == "api_stream_failed"] == ["no_terminal_event"]


@pytest.mark.parametrize("status", [500, 422, 502])
def test_http_error_status_shows_generic_error_without_details(api, status):
    """Requirement: Errores del proveedor / Scenario: Estado HTTP de error."""
    api.stream_status = status
    with capture_logs() as logs:
        at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert "SECRET-DETAIL" not in all_text(at)
    assert history(at) == []
    assert [e["reason"] for e in logs if e["event"] == "api_stream_failed"] == ["http_status"]
    assert "SECRET-DETAIL" not in sent_text_of_logs(logs)


def test_connection_cut_mid_stream_is_a_generic_error(api):
    """Requirement: Errores del proveedor (se corta la conexión a mitad)."""
    api.stream_body = BrokenStream(token("parcial"), httpx.ReadError("cut"))
    with capture_logs() as logs:
        at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []
    assert [e["reason"] for e in logs if e["event"] == "api_stream_failed"] == ["transport_error"]


def test_malformed_event_is_a_generic_error(api):
    """Design D6: a `token` with invalid JSON counts as a failed stream (`malformed_event`)."""
    api.stream_body = "event: token\ndata: not-json\n\n" + metrics_event()
    with capture_logs() as logs:
        at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []
    assert [e["reason"] for e in logs if e["event"] == "api_stream_failed"] == ["malformed_event"]


@pytest.mark.parametrize(
    "body", [metrics_event(), token("   ") + token("\n") + metrics_event()], ids=["none", "blank"]
)
def test_empty_reply_shows_generic_error_and_keeps_state(api, body):
    """Requirement: Errores del proveedor / Scenario: Respuesta sin texto."""
    at = send(run_app(), "T")
    before_history, before_metrics = history(at), at.session_state["last_metrics"]
    api.stream_body = body
    with capture_logs() as logs:
        send(at, "ajuste")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == before_history
    assert at.session_state["last_metrics"] == before_metrics
    assert "ajuste" not in sent_text_of_logs(logs)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(metrics_event(), id="no-token-events"),
        pytest.param(token("   ") + token("\n") + metrics_event(), id="blank-tokens"),
    ],
)
def test_empty_reply_is_logged_as_empty_llm_reply(api, body):
    """Requirement: Observability (`empty_llm_reply` con modelo y proveedor, sin contenido)."""
    api.stream_body = body
    with capture_logs() as logs:
        send(run_app(), "mensaje-secreto")
    empty = [e for e in logs if e["event"] == "empty_llm_reply"]
    assert len(empty) == 1
    assert empty[0]["model"] == "gpt-4o-mini" and empty[0]["provider"] == "openai"
    assert not [e for e in logs if e["event"] == "api_stream_failed"]


def test_empty_reply_on_first_turn_leaves_no_history_or_metrics(api):
    """Requirement: Errores del proveedor / Scenario: Respuesta sin texto (primer turno)."""
    api.stream_body = metrics_event()
    at = send(run_app(), "T")
    assert history(at) == []
    assert at.session_state["last_metrics"] is None
    assert "Aún no hay métricas" in sidebar_text(at)


class Interrupted(BaseException):
    """Stand-in for the user pressing Stop (Streamlit stops a script via a BaseException)."""


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_interruption_mid_stream_persists_nothing_closes_the_connection_and_keeps_sidebar(api):
    """Requirement: Errores / Scenario: Generación interrumpida; Panel / Scenario: Panel visible
    tras interrumpir (approximation: AppTest cannot press Stop, so the stream raises a
    BaseException; the real Stop stays manual, task 7.2)."""
    api.stream_body = token("E") + metrics_event(input_tokens=1234)
    at = send(run_app(), "T")
    before = history(at)
    broken = BrokenStream(token("parcial"), Interrupted())
    api.stream_body = broken
    send(at, "ajuste")
    assert history(at) == before
    assert broken.closed is True
    assert "1234" in sidebar_text(at)
    assert [t for t in at.sidebar.text_area if t.value == prompt_context(3).system_prompt]


# --- Requirement: Panel de contexto CAG ---


def test_sidebar_shows_models_cache_prompt_and_examples(api):
    """Requirement: Panel de contexto CAG / Scenario: Contexto visible."""
    at = run_app()
    text = sidebar_text(at)
    assert "**Modelo primario:** `gpt-4o-mini`  \n" in text
    assert "**Modelo de respaldo:** `claude-haiku-4-5`" in text
    assert "**Caché:** disponible" in text
    ctx = prompt_context(1)
    assert [t.value for t in at.sidebar.text_area] == [ctx.system_prompt]
    assert at.sidebar.text_area[0].disabled
    for example in ctx.examples:
        assert example["meeting_summary"] in text
        assert example["estimation"] in text
    assert api.calls("/health") and api.calls("/api/v1/context")
    assert api.calls("/api/v1/context")[0].url.params["message_count"] == "1"


def test_sidebar_says_there_is_no_fallback(api):
    """Requirement: Panel de contexto CAG / Scenario: Sin respaldo."""
    api.health = {**HEALTH, "fallback_model": None}
    text = sidebar_text(run_app())
    assert "**Modelo de respaldo:** Sin respaldo" in text


def test_sidebar_says_the_cache_is_unavailable(api):
    """Requirement: Panel de contexto CAG / Scenario: Caché no disponible."""
    api.health = {**HEALTH, "cache": "unavailable"}
    assert "**Caché:** no disponible" in sidebar_text(run_app())


def test_sidebar_asks_for_message_count_3_after_the_first_reply(api):
    """Requirement: Panel de contexto CAG / Scenario: Prompt tras la primera respuesta."""
    at = send(run_app(), "T")
    assert api.calls("/api/v1/context")[-1].url.params["message_count"] == "3"
    assert [t.value for t in at.sidebar.text_area] == [prompt_context(3).system_prompt]
    assert prompt_context(3).system_prompt != prompt_context(1).system_prompt


def test_sidebar_returns_to_endpoint_prompt_on_new_conversation(api):
    """Requirement: Panel de contexto CAG (el prompt vuelve al del primer turno)."""
    at = send(run_app(), "T")
    at.sidebar.button[0].click().run()
    assert [t.value for t in at.sidebar.text_area] == [prompt_context(1).system_prompt]


# --- Requirement: Métricas de la última llamada ---


def test_sidebar_says_there_are_no_metrics_before_the_first_call(api):
    """Requirement: Métricas / Scenario: Antes de la primera llamada."""
    assert "Aún no hay métricas" in sidebar_text(run_app())


def test_metrics_come_from_the_metrics_event_plus_client_timings(api):
    """Requirement: Métricas / Scenario: Métricas tras una respuesta."""
    at = send(run_app(), "T")
    text = sidebar_text(at)
    assert "Modelo: `gpt-4o-mini`" in text
    assert "Proveedor: `openai`" in text
    assert "Respaldo: no" in text
    assert "Desde caché: no" in text
    assert "Tokens de entrada: 1200" in text
    assert "Tokens de salida: 300" in text
    assert "Tiempo total: " in text and "Tiempo hasta el primer fragmento: " in text
    assert "Coste estimado: $0.000540" in text
    assert "Respuesta cortada: no" in text
    metrics = at.session_state["last_metrics"]
    assert 0 <= metrics.ttft_s <= metrics.total_s


def test_metrics_of_a_fallback_answer(api):
    """Requirement: Métricas / Scenario: Métricas de una respuesta del respaldo."""
    api.stream_body = token("E") + metrics_event(
        model="claude-haiku-4-5", provider="anthropic", fallback_used=True
    )
    text = sidebar_text(send(run_app(), "T"))
    assert "Modelo: `claude-haiku-4-5`" in text
    assert "Respaldo: sí" in text


def test_metrics_of_a_cache_hit(api):
    """Requirement: Métricas / Scenario: Métricas de una respuesta desde la caché."""
    api.stream_body = token("E") + metrics_event(cache_hit=True)
    assert "Desde caché: sí" in sidebar_text(send(run_app(), "T"))


def test_metrics_of_a_truncated_answer_and_warning_under_the_reply(api):
    """Requirement: Métricas / Scenario: Métricas de una respuesta truncada."""
    api.stream_body = token("E") + metrics_event(truncated=True)
    at = send(run_app(), "T")
    assert "Respuesta cortada: sí" in sidebar_text(at)
    assert len(at.warning) == 1
    assert "límite de longitud" in at.warning[0].value


def test_truncation_warning_persists_and_only_under_the_truncated_reply(api):
    """Requirement: Respuesta truncada (aviso persistente bajo la respuesta cortada)."""
    api.stream_body = token("E") + metrics_event(truncated=True)
    at = send(run_app(), "T")
    api.stream_body = token("E2") + metrics_event()
    send(at, "ajuste")
    assert len(at.warning) == 1
    assert at.chat_message[1].warning
    assert not at.chat_message[3].warning
    assert api.sent_messages()[1] == {"role": "assistant", "content": "E"}


def test_new_conversation_removes_truncation_warnings(api):
    """Requirement: Nueva conversación (sin avisos de truncado)."""
    api.stream_body = token("E") + metrics_event(truncated=True)
    at = send(run_app(), "T")
    at.sidebar.button[0].click().run()
    assert len(at.warning) == 0


def test_cost_is_nd_when_the_event_has_no_price(api):
    """Requirement: Métricas (coste `n/d` si `cost_usd` es null)."""
    api.stream_body = token("E") + metrics_event(cost_usd=None)
    assert "Coste estimado: n/d" in sidebar_text(send(run_app(), "T"))


def test_metrics_are_kept_between_reruns_until_the_next_call(api):
    """Requirement: Métricas (se conservan entre reruns)."""
    at = send(run_app(), "T")
    first = at.session_state["last_metrics"]
    at.run()
    assert at.session_state["last_metrics"] == first
    assert "Tokens de entrada: 1200" in sidebar_text(at)


# --- Requirement: URL del servicio por configuración ---


def test_default_url_is_localhost_8000(api, monkeypatch):
    """Requirement: URL del servicio / Scenario: URL por defecto."""
    monkeypatch.delenv("ESTIMATOR_API_BASE_URL")
    send(run_app(), "T")
    assert str(api.stream_calls[0].url) == "http://localhost:8000/api/v1/estimate/stream"


def test_configured_url_from_the_environment(api, monkeypatch):
    """Requirement: URL del servicio / Scenario: URL configurada."""
    monkeypatch.setenv("ESTIMATOR_API_BASE_URL", "http://api.local:9000")
    send(run_app(), "T")
    assert str(api.stream_calls[0].url) == "http://api.local:9000/api/v1/estimate/stream"
    assert all(r.url.host == "api.local" for r in api.requests)


def test_url_is_read_from_the_env_file_only_for_that_key_and_keys_stay_out_of_the_ui(
    api, monkeypatch, tmp_path
):
    """Requirement: URL del servicio (`.env`) / Scenario: Key fuera de la interfaz."""
    monkeypatch.delenv("ESTIMATOR_API_BASE_URL")
    (tmp_path / ".env").write_text(
        "ESTIMATOR_API_BASE_URL=http://from-dotenv:7000\n"
        f"OPENAI_API_KEY={FAKE_OPENAI_KEY}\nANTHROPIC_API_KEY={FAKE_ANTHROPIC_KEY}\n"
    )
    at = send(run_app(), "T")
    assert str(api.stream_calls[0].url) == "http://from-dotenv:7000/api/v1/estimate/stream"
    text = all_text(at)
    assert FAKE_OPENAI_KEY not in text and FAKE_ANTHROPIC_KEY not in text
    assert "SECRET" not in text


def test_environment_wins_over_the_env_file(api, monkeypatch, tmp_path):
    """Requirement: URL del servicio (el entorno tiene precedencia sobre `.env`)."""
    (tmp_path / ".env").write_text("ESTIMATOR_API_BASE_URL=http://from-dotenv:7000\n")
    send(run_app(), "T")
    assert api.stream_calls[0].url.host == "api.test"


def test_app_does_not_load_provider_keys_into_the_environment(api, monkeypatch, tmp_path):
    """Requirement: URL del servicio (la app no carga las API keys de los proveedores)."""
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FAKE_OPENAI_KEY}\n")
    run_app()
    import os

    assert "OPENAI_API_KEY" not in os.environ


# --- Requirement: API no disponible ---


def test_opening_with_the_api_down_warns_and_keeps_the_chat_visible(api):
    """Requirement: API no disponible / Scenario: Abrir la app con la API parada."""
    api.down = True
    at = run_app()
    assert not at.exception
    assert [w.value for w in at.sidebar.warning] == [unavailable()]
    assert len(at.chat_input) == 1
    assert at.sidebar.button[0].label == "Nueva conversación"


def test_unavailable_warning_uses_the_configured_url(api, monkeypatch):
    """Requirement: API no disponible (el aviso incluye la URL configurada)."""
    monkeypatch.setenv("ESTIMATOR_API_BASE_URL", "http://api.local:9000/")
    api.down = True
    at = run_app()
    assert [w.value for w in at.sidebar.warning] == [unavailable("http://api.local:9000")]


def test_sending_with_the_api_down_warns_saves_nothing_and_recovers_without_reload(api):
    """Requirement: API no disponible / Scenario: Envío con la API parada; Observability /
    Scenario: API inaccesible desde la UI."""
    at = send(run_app(), "T")
    before = history(at)
    api.down = True
    with capture_logs() as logs:
        send(at, "mensaje-secreto-ajuste")
    assert not at.exception
    assert history(at) == before
    assert unavailable() in [w.value for w in at.warning]
    assert not errors(at)
    assert [m.markdown[0].value for m in at.chat_message[:2]] == [before[0][1], before[1][1]]
    assert len(at.chat_input) == 1
    unreachable = [e for e in logs if e["event"] == "api_unreachable"]
    assert len(unreachable) == 1
    assert unreachable[0]["error_type"] == "ConnectError"
    assert "mensaje-secreto-ajuste" not in sent_text_of_logs(logs)
    assert "connection refused" not in sent_text_of_logs(logs)

    api.down = False
    api.stream_body = token("E2") + metrics_event()
    send(at, "mensaje-secreto-ajuste")
    assert history(at)[-2:] == [("user", "mensaje-secreto-ajuste"), ("assistant", "E2")]


def test_send_with_health_up_but_stream_refused_shows_unavailable(api):
    """Requirement: API no disponible (conexión rechazada solo en el stream)."""
    api.stream_down = True
    at = send(run_app(), "T")
    assert unavailable() in [w.value for w in at.warning]
    assert history(at) == []


def test_unhealthy_health_but_working_context_does_not_show_the_unavailable_warning(api):
    """Design D6: the warning appears only if the API cannot be reached for both reads."""
    api.health = None
    at = run_app()
    assert not at.sidebar.warning
    assert [t.value for t in at.sidebar.text_area] == [prompt_context(1).system_prompt]


# --- End to end: UI -> API -> service (TestClient as httpx.Client) ---


@pytest.fixture
def e2e(monkeypatch: pytest.MonkeyPatch, response_cache):
    """Patch `httpx.Client` with an ASGI-backed TestClient over the real app and a FakeProvider."""
    provider = FakeProvider(
        fragments=["## Estimación\n\n", "| A | B |"], model="gpt-4o-mini", input_tokens=1200
    )
    settings = make_settings(anthropic_api_key="sk-ant-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-lifespan")
    monkeypatch.setenv("ESTIMATOR_API_BASE_URL", BASE_URL)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_response_cache] = lambda: response_cache
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider

    def factory(*args, **kwargs) -> TestClient:
        return TestClient(app, base_url=kwargs["base_url"])

    monkeypatch.setattr(httpx, "Client", factory)
    try:
        yield provider
    finally:
        app.dependency_overrides.clear()


def test_e2e_first_turn_and_adjustment_through_the_real_api(e2e):
    """Requirement: Aplicación Streamlit de chat / Scenarios: Estimación y Petición enviada;
    Panel de contexto (contenido real de la API); Tests sin red / Streamlit sin API real."""
    at = run_app()
    assert not at.exception
    assert "**Modelo de respaldo:** `claude-haiku-4-5`" in sidebar_text(at)
    assert "**Caché:** disponible" in sidebar_text(at)
    assert [t.value for t in at.sidebar.text_area] == [prompt_context(1).system_prompt]

    send(at, "  Necesitamos una web  ")
    assert not at.exception
    assert history(at) == [
        ("user", "Necesitamos una web"),
        ("assistant", "## Estimación\n\n| A | B |"),
    ]
    system, messages = e2e.calls[0]
    assert messages == [{"role": "user", "content": "Necesitamos una web"}]
    assert system == prompt_context(1).system_prompt
    assert "Tokens de entrada: 1200" in sidebar_text(at)
    assert [t.value for t in at.sidebar.text_area] == [prompt_context(3).system_prompt]

    send(at, "quita el blog")
    system, messages = e2e.calls[1]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert system == prompt_context(3).system_prompt
    assert len(history(at)) == 4


def test_e2e_provider_failure_reaches_the_ui_as_the_generic_error(e2e):
    """Requirement: Errores del proveedor / Scenario: Proveedor caído (de extremo a extremo)."""
    e2e.error = llm_service.LLMProviderError("openai")
    e2e.error_after = 1
    at = send(run_app(), "T")
    assert errors(at) == [GENERIC_ERROR]
    assert history(at) == []
    assert "Traceback" not in all_text(at)


def test_e2e_cache_hit_is_reported_in_the_ui(e2e, response_cache):
    """Requirement: Métricas / Scenario: Respuesta desde la caché (UI -> API -> caché)."""
    cached = llm_service.CachedProvider(e2e, response_cache, "gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: cached
    first = send(run_app(), "T")
    assert "Desde caché: no" in sidebar_text(first)
    second = send(run_app(), "T")
    assert "Desde caché: sí" in sidebar_text(second)
    assert history(second)[1] == ("assistant", "## Estimación\n\n| A | B |")
    assert e2e.call_count == 1
