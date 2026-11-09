"""Streamlit chat UI: in-process client of the estimation service layer."""

import time
from collections.abc import Iterator
from dataclasses import dataclass

import streamlit as st
import structlog
from pydantic import ValidationError

from app.config import Settings, configure_logging, format_config_error, get_settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.services.llm_service import (
    MAX_TRANSCRIPTION_CHARS,
    ChatMessage,
    EstimationResult,
    EstimationService,
    EstimationStream,
    LLMProvider,
    LLMProviderError,
    get_llm_provider,
    system_prompt_for,
)

logger = structlog.get_logger(__name__)

PROVIDER_ERROR_MESSAGE = "No se pudo generar la estimación. Inténtalo de nuevo en unos instantes."
TRUNCATED_WARNING = (
    "La respuesta se ha cortado por el límite de longitud. Pide un ajuste o una versión más breve."
)


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


def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("last_metrics", None)
    st.session_state.setdefault("truncated_turns", set())


def warn_fallback_disabled_once(settings: Settings) -> None:
    """Log the disabled-fallback warning once per session, not on every rerun."""
    if settings.fallback is None and not st.session_state.get("fallback_warning_logged"):
        logger.warning("llm_fallback_disabled", reason=settings.fallback_disabled_reason)
        st.session_state.fallback_warning_logged = True


def load_config() -> tuple[Settings, LLMProvider]:
    """Return settings and provider, or show a clear error and stop the script."""
    try:
        settings = get_settings()
        provider = get_llm_provider()
    except ValidationError as exc:
        st.error(format_config_error(exc))
        st.stop()
    configure_logging(settings)
    warn_fallback_disabled_once(settings)
    return settings, provider


def render_history() -> None:
    for index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if index in st.session_state.truncated_turns:
                st.warning(TRUNCATED_WARNING)


def validate_input(text: str) -> str | None:
    """Return a warning for a rejected message, or None if it is valid."""
    if not text:
        return "El mensaje está vacío."
    if len(text) > MAX_TRANSCRIPTION_CHARS:
        return f"El mensaje supera el límite de {MAX_TRANSCRIPTION_CHARS} caracteres ({len(text)})."
    return None


def timed_fragments(stream: EstimationStream, timings: dict[str, float]) -> Iterator[str]:
    """Yield the stream fragments while recording time to first fragment and total."""
    start = time.perf_counter()
    for fragment in stream:
        timings.setdefault("ttft_s", time.perf_counter() - start)
        yield fragment
    timings["total_s"] = time.perf_counter() - start


def build_metrics(result: EstimationResult, timings: dict[str, float]) -> CallMetrics:
    return CallMetrics(
        model=result.model,
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        ttft_s=timings["ttft_s"],
        total_s=timings["total_s"],
        truncated=result.truncated,
        fallback_used=result.fallback_used,
        cache_hit=result.cache_hit,
    )


def handle_user_message(text: str, provider: LLMProvider) -> None:
    """Stream the reply; on success persist both turns and metrics, then rerun.

    On provider error nothing is persisted and no rerun happens, so the error stays visible.
    """
    new_message: ChatMessage = {"role": "user", "content": text}
    with st.chat_message("user"):
        st.markdown(text)
    timings: dict[str, float] = {}
    with st.chat_message("assistant"):
        try:
            stream = EstimationService(provider).stream_chat(
                [*st.session_state.messages, new_message]
            )
            reply = st.write_stream(timed_fragments(stream, timings))
            result = stream.result
        except LLMProviderError:
            st.error(PROVIDER_ERROR_MESSAGE)
            return
        if "ttft_s" not in timings or not str(reply).strip():
            logger.warning("empty_llm_reply", provider=result.provider, model=result.model)
            st.error(PROVIDER_ERROR_MESSAGE)
            return
    if result.truncated:
        st.session_state.truncated_turns.add(len(st.session_state.messages) + 1)
    st.session_state.messages.extend([new_message, {"role": "assistant", "content": str(reply)}])
    st.session_state.last_metrics = build_metrics(result, timings)
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


def next_turn_system_prompt() -> str:
    """System prompt the next send will use, given the current history."""
    placeholder: ChatMessage = {"role": "user", "content": ""}
    return system_prompt_for([*st.session_state.messages, placeholder])


def render_models(settings: Settings) -> None:
    primary, fallback = settings.primary, settings.fallback
    fallback_text = (
        "Sin respaldo" if fallback is None else f"`{fallback.model}` (`{fallback.provider}`)"
    )
    st.markdown(
        f"**Modelo primario:** `{primary.model}` (`{primary.provider}`)  \n"
        f"**Modelo de respaldo:** {fallback_text}"
    )


def render_sidebar(settings: Settings) -> None:
    with st.sidebar:
        st.button("Nueva conversación", on_click=reset_conversation)
        render_models(settings)
        render_metrics(st.session_state.last_metrics)
        st.subheader("Contexto CAG")
        with st.expander("System prompt del siguiente turno"):
            st.text_area(
                "System prompt",
                value=next_turn_system_prompt(),
                height=300,
                disabled=True,
                label_visibility="collapsed",
            )
        for number, example in enumerate(ESTIMATION_EXAMPLES, start=1):
            with st.expander(f"Ejemplo {number}"):
                st.markdown("**Resumen de la reunión**")
                st.markdown(example["meeting_summary"])
                st.markdown("**Estimación**")
                st.markdown(example["estimation"])


def main() -> None:
    st.set_page_config(page_title="Estimador de software", page_icon=":material/calculate:")
    st.title("Estimador de software")
    settings, provider = load_config()
    init_state()
    render_sidebar(settings)
    render_history()
    prompt = st.chat_input("Pega la transcripción de la reunión o pide un ajuste")
    if prompt is not None:
        text = prompt.strip()
        warning = validate_input(text)
        if warning:
            st.warning(warning)
        else:
            handle_user_message(text, provider)


main()
