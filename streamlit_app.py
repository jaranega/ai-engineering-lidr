"""Streamlit chat UI: HTTP client of the estimation API (SSE streaming)."""

import contextlib
import json
import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

import httpx
import streamlit as st
import structlog
from dotenv import dotenv_values

logger = structlog.get_logger(__name__)

BASE_URL_KEY = "ESTIMATOR_API_BASE_URL"
DEFAULT_BASE_URL = "http://localhost:8000"
STREAM_PATH = "/api/v1/estimate/stream"
GET_TIMEOUT = httpx.Timeout(3.0)
STREAM_TIMEOUT = httpx.Timeout(connect=3.0, read=45.0, write=10.0, pool=3.0)

PROVIDER_ERROR_MESSAGE = "No se pudo generar la estimación. Inténtalo de nuevo en unos instantes."
TRUNCATED_WARNING = (
    "La respuesta se ha cortado por el límite de longitud. Pide un ajuste o una versión más breve."
)


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class ApiUnreachable(Exception):
    """The API did not accept the connection."""


class StreamFailed(Exception):
    """The stream ended without a valid terminal `metrics` event."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CallMetrics:
    """Metrics of the last completed LLM call."""

    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    ttft_s: float
    total_s: float
    truncated: bool
    fallback_used: bool
    cache_hit: bool


def get_base_url() -> str:
    """API base URL from the environment or, for that single key only, from `.env`."""
    value = os.environ.get(BASE_URL_KEY) or dotenv_values(".env").get(BASE_URL_KEY)
    return (value or DEFAULT_BASE_URL).rstrip("/")


def api_unavailable_message(base_url: str) -> str:
    return (
        f"No se puede conectar con el servicio de estimación (`{base_url}`). "
        "Arráncalo con `uv run uvicorn app.main:app` o `docker compose up`."
    )


def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("last_metrics", None)
    st.session_state.setdefault("truncated_turns", set())


def fetch_json(base_url: str, path: str, params: dict[str, Any] | None = None) -> dict | None:
    """GET a JSON object from the API, or None if it is unreachable or answers badly."""
    try:
        with httpx.Client(base_url=base_url, timeout=GET_TIMEOUT) as client:
            response = client.get(path, params=params)
        data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield (event, data) pairs from SSE lines, ignoring comments such as `: ping`."""
    event, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
    if data:
        yield event, "\n".join(data)


def stream_estimate(
    base_url: str, messages: list[ChatMessage], outcome: dict[str, Any]
) -> Iterator[str]:
    """Yield reply fragments; fill `outcome` with the metrics payload and client timings.

    The client and the response live in `with` blocks, so closing the generator
    (Stop in Streamlit) closes the connection.
    """
    start = time.perf_counter()
    connected = False
    try:
        with (
            httpx.Client(base_url=base_url, timeout=STREAM_TIMEOUT) as client,
            client.stream("POST", STREAM_PATH, json={"messages": messages}) as response,
        ):
            connected = True
            if response.status_code != 200:
                raise StreamFailed("http_status")
            for event, data in parse_sse(response.iter_lines()):
                if event == "token":
                    outcome.setdefault("ttft_s", time.perf_counter() - start)
                    yield json.loads(data)
                elif event == "metrics":
                    outcome["metrics"] = json.loads(data)
                    outcome["total_s"] = time.perf_counter() - start
                    return
                elif event == "error":
                    raise StreamFailed("error_event")
        raise StreamFailed("no_terminal_event")
    except httpx.TransportError as exc:
        if not connected:
            raise ApiUnreachable(type(exc).__name__) from exc
        raise StreamFailed("transport_error") from exc
    except ValueError as exc:
        raise StreamFailed("malformed_event") from exc


def build_metrics(payload: dict[str, Any], outcome: dict[str, Any]) -> CallMetrics:
    return CallMetrics(
        model=payload["model"],
        provider=payload["provider"],
        input_tokens=payload["input_tokens"],
        output_tokens=payload["output_tokens"],
        cost_usd=payload["cost_usd"],
        ttft_s=outcome["ttft_s"],
        total_s=outcome["total_s"],
        truncated=payload["truncated"],
        fallback_used=payload["fallback_used"],
        cache_hit=payload["cache_hit"],
    )


def render_history() -> None:
    for index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if index in st.session_state.truncated_turns:
                st.warning(TRUNCATED_WARNING)


def validate_input(text: str, max_chars: int | None) -> str | None:
    """Return a warning for a rejected message, or None if it is valid."""
    if not text:
        return "El mensaje está vacío."
    if max_chars is not None and len(text) > max_chars:
        return f"El mensaje supera el límite de {max_chars} caracteres ({len(text)})."
    return None


def read_reply(base_url: str, messages: list[ChatMessage], outcome: dict[str, Any]) -> str | None:
    """Stream the reply into the chat; on failure show the error and return None."""
    try:
        with contextlib.closing(stream_estimate(base_url, messages, outcome)) as fragments:
            return str(st.write_stream(fragments))
    except ApiUnreachable as exc:
        logger.error("api_unreachable", error_type=str(exc))
        st.warning(api_unavailable_message(base_url))
    except StreamFailed as exc:
        logger.warning("api_stream_failed", reason=exc.reason)
        st.error(PROVIDER_ERROR_MESSAGE)
    return None


def handle_user_message(text: str, base_url: str) -> None:
    """Stream the reply; on success persist both turns and metrics, then rerun.

    On failure nothing is persisted and no rerun happens, so the error stays visible.
    """
    new_message: ChatMessage = {"role": "user", "content": text}
    with st.chat_message("user"):
        st.markdown(text)
    outcome: dict[str, Any] = {}
    with st.chat_message("assistant"):
        reply = read_reply(base_url, [*st.session_state.messages, new_message], outcome)
        if reply is None:
            return
        try:
            payload = outcome["metrics"]
            if "ttft_s" not in outcome or not reply.strip():
                logger.warning(
                    "empty_llm_reply", provider=payload["provider"], model=payload["model"]
                )
                st.error(PROVIDER_ERROR_MESSAGE)
                return
            metrics = build_metrics(payload, outcome)
        except (KeyError, TypeError):
            logger.warning("api_stream_failed", reason="malformed_event")
            st.error(PROVIDER_ERROR_MESSAGE)
            return
    if metrics.truncated:
        st.session_state.truncated_turns.add(len(st.session_state.messages) + 1)
    st.session_state.messages.extend([new_message, {"role": "assistant", "content": reply}])
    st.session_state.last_metrics = metrics
    st.rerun()  # control-flow exception: keep outside try/except; refreshes the sidebar


def reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.last_metrics = None
    st.session_state.truncated_turns = set()


def render_metrics(metrics: CallMetrics | None) -> None:
    st.subheader("Métricas de la última llamada")
    if metrics is None:
        st.caption("Aún no hay métricas")
        return
    cost = "n/d" if metrics.cost_usd is None else f"${metrics.cost_usd:.6f}"
    st.markdown(
        f"- Modelo: `{metrics.model}`\n"
        f"- Proveedor: `{metrics.provider}`\n"
        f"- Respaldo: {'sí' if metrics.fallback_used else 'no'}\n"
        f"- Desde caché: {'sí' if metrics.cache_hit else 'no'}\n"
        f"- Tokens de entrada: {metrics.input_tokens}\n"
        f"- Tokens de salida: {metrics.output_tokens}\n"
        f"- Tiempo total: {metrics.total_s:.2f} s\n"
        f"- Tiempo hasta el primer fragmento: {metrics.ttft_s:.2f} s\n"
        f"- Coste estimado: {cost}\n"
        f"- Respuesta cortada: {'sí' if metrics.truncated else 'no'}"
    )


def render_health(health: dict) -> None:
    fallback = health.get("fallback_model")
    fallback_text = "Sin respaldo" if fallback is None else f"`{fallback}`"
    cache_text = "disponible" if health.get("cache") == "ok" else "no disponible"
    st.markdown(
        f"**Modelo primario:** `{health.get('primary_model')}`  \n"
        f"**Modelo de respaldo:** {fallback_text}  \n"
        f"**Caché:** {cache_text}"
    )


def render_context(context: dict) -> None:
    st.subheader("Contexto CAG")
    with st.expander("System prompt del siguiente turno"):
        st.text_area(
            "System prompt",
            value=context.get("system_prompt", ""),
            height=300,
            disabled=True,
            label_visibility="collapsed",
        )
    for number, example in enumerate(context.get("examples", []), start=1):
        with st.expander(f"Ejemplo {number}"):
            st.markdown("**Resumen de la reunión**")
            st.markdown(example["meeting_summary"])
            st.markdown("**Estimación**")
            st.markdown(example["estimation"])


def render_sidebar(base_url: str) -> int | None:
    """Paint the sidebar from the API; return `max_message_chars` if it was obtained."""
    health = fetch_json(base_url, "/health")
    message_count = len(st.session_state.messages) + 1
    context = fetch_json(base_url, "/api/v1/context", {"message_count": message_count})
    with st.sidebar:
        st.button("Nueva conversación", on_click=reset_conversation)
        if health is None and context is None:
            st.warning(api_unavailable_message(base_url))
        if health is not None:
            render_health(health)
        render_metrics(st.session_state.last_metrics)
        if context is not None:
            render_context(context)
    max_chars = context.get("max_message_chars") if context else None
    return max_chars if isinstance(max_chars, int) else None


def main() -> None:
    st.set_page_config(page_title="Estimador de software", page_icon=":material/calculate:")
    st.title("Estimador de software")
    base_url = get_base_url()
    init_state()
    max_chars = render_sidebar(base_url)
    render_history()
    prompt = st.chat_input("Pega la transcripción de la reunión o pide un ajuste")
    if prompt is not None:
        text = prompt.strip()
        warning = validate_input(text, max_chars)
        if warning:
            st.warning(warning)
        else:
            handle_user_message(text, base_url)


main()
