"""Tests for the Streamlit chat UI (spec: chat-ui) run without a browser via AppTest.

The provider is replaced by patching `llm_service.build_provider` (design D9): the real
`get_llm_provider` (cleared between tests) then returns the double. Never real API calls.

Manual checks (task 7.2, not automatable with AppTest): scenarios "Arranque de la interfaz",
"Texto incremental" and "Generación interrumpida".
"""

import ast
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.config import Settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.services import llm_service
from app.services.llm_service import (
    MAX_TRANSCRIPTION_CHARS,
    LLMProviderError,
    _chat_system_prompt,
    _system_prompt,
)
from app.services.pricing import estimate_cost
from tests.conftest import FakeProvider

APP_PATH = Path(__file__).resolve().parent.parent / "streamlit_app.py"
FAKE_KEY = "sk-fake-ui-SECRET-123"
FAKE_ANTHROPIC_KEY = "sk-ant-fake-ui-SECRET-456"


def make_app(monkeypatch: pytest.MonkeyPatch, fake: FakeProvider | None = None) -> AppTest:
    """Run the app with a valid fake configuration and `fake` as the provider."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    fake = fake if fake is not None else FakeProvider()
    monkeypatch.setattr(llm_service, "build_provider", lambda name, settings: fake)
    return AppTest.from_file(str(APP_PATH), default_timeout=10).run()


def send(at: AppTest, text: str) -> AppTest:
    at.chat_input[0].set_value(text).run()
    return at


def history(at: AppTest) -> list[tuple[str, str]]:
    return [(m["role"], m["content"]) for m in at.session_state["messages"]]


def all_text(at: AppTest) -> str:
    """Concatenate the visible text of every element, main area and sidebar included."""
    chunks: list[str] = []

    def walk(block) -> None:
        for child in block.children.values():
            value = getattr(child, "value", None)
            if isinstance(value, str):
                chunks.append(value)
            label = getattr(child, "label", None)
            if isinstance(label, str):
                chunks.append(label)
            if hasattr(child, "children"):
                walk(child)

    walk(at.main)
    walk(at.sidebar)
    return "\n".join(chunks)


def sidebar_text(at: AppTest) -> str:
    chunks = [e.value for e in at.sidebar.markdown] + [e.value for e in at.sidebar.caption]
    chunks += [e.value for e in at.sidebar.text_area]
    return "\n".join(chunks)


def conversation_error(at: AppTest) -> str:
    return "\n".join(e.value for e in at.error)


# --- Requirement: Aplicación Streamlit de chat ---


def test_app_runs_without_exceptions_and_shows_chat_input(monkeypatch):
    """Requirement: Aplicación Streamlit de chat (arranque sin errores en AppTest)."""
    at = make_app(monkeypatch)
    assert not at.exception
    assert len(at.chat_input) == 1
    assert at.title[0].value == "Estimador de software"


def test_transcription_gets_an_assistant_answer_in_history(monkeypatch):
    """Requirement: Aplicación Streamlit de chat / Scenario: Estimación de una transcripción."""
    fake = FakeProvider(text="ESTIMACION-1")
    at = send(make_app(monkeypatch, fake), "Necesitamos una web")
    assert not at.exception
    assert history(at) == [("user", "Necesitamos una web"), ("assistant", "ESTIMACION-1")]
    shown = [(m.name, m.markdown[0].value) for m in at.chat_message]
    assert shown == [("user", "Necesitamos una web"), ("assistant", "ESTIMACION-1")]


def test_estimation_needs_no_fastapi_server(monkeypatch):
    """Requirement: Aplicación Streamlit de chat / Scenario: Sin servidor FastAPI.

    The UI only depends on the service layer: no uvicorn/FastAPI app is started here.
    """
    at = send(make_app(monkeypatch), "T")
    assert len(at.session_state["messages"]) == 2


def test_streamlit_app_does_not_import_llm_sdks():
    """Requirement: Aplicación Streamlit de chat (no importa openai ni anthropic)."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"openai", "anthropic"}


# --- Requirement: Historial de la conversación ---


def test_several_messages_in_a_row_are_rendered_in_order(monkeypatch):
    """Requirement: Historial de la conversación / Scenario: Varios mensajes seguidos."""
    fake = FakeProvider(text="E")
    at = make_app(monkeypatch, fake)
    send(at, "T")
    fake.result = FakeProvider(text="E2").result
    fake.fragments = ["E2"]
    send(at, "ajuste")
    shown = [(m.name, m.markdown[0].value) for m in at.chat_message]
    assert shown == [("user", "T"), ("assistant", "E"), ("user", "ajuste"), ("assistant", "E2")]


def test_new_session_starts_with_empty_history(monkeypatch):
    """Requirement: Historial de la conversación / Scenario: Sesión nueva."""
    fake = FakeProvider()
    send(make_app(monkeypatch, fake), "T")
    fresh = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    assert fresh.session_state["messages"] == []
    assert len(fresh.chat_message) == 0


# --- Requirement: Conversación multi-turno con el LLM ---


def test_first_message_sends_a_single_user_message_with_endpoint_prompt(monkeypatch):
    """Requirement: Conversación multi-turno / Scenario: Primer mensaje (prompt del endpoint)."""
    fake = FakeProvider()
    send(make_app(monkeypatch, fake), "T")
    system, messages = fake.calls[0]
    assert messages == [{"role": "user", "content": "T"}]
    assert system == _system_prompt()


def test_adjustment_sends_the_three_messages_in_order(monkeypatch):
    """Requirement: Conversación multi-turno / Scenario: Ajuste (prompt de conversación)."""
    fake = FakeProvider(text="E")
    at = make_app(monkeypatch, fake)
    send(at, "T")
    send(at, "quita la sección de blog")
    assert fake.call_count == 2
    assert fake.calls[1][1] == [
        {"role": "user", "content": "T"},
        {"role": "assistant", "content": "E"},
        {"role": "user", "content": "quita la sección de blog"},
    ]
    assert fake.calls[1][0] == _chat_system_prompt()


# --- Requirement: Respuesta en streaming ---


def test_saved_message_is_the_concatenation_of_all_fragments(monkeypatch):
    """Requirement: Respuesta en streaming (el guardado es la concatenación de los fragmentos).

    The incremental rendering itself is manual (task 7.2): AppTest only sees the final text.
    """
    fake = FakeProvider(fragments=["## Esti", "mación: ", "web"])
    at = send(make_app(monkeypatch, fake), "T")
    assert history(at)[1] == ("assistant", "## Estimación: web")


# --- Requirement: Nueva conversación ---


def test_new_conversation_clears_history_and_metrics_and_next_send_is_single(monkeypatch):
    """Requirement: Nueva conversación / Scenario: Empezar de cero."""
    fake = FakeProvider(text="E")
    at = make_app(monkeypatch, fake)
    send(at, "T")
    assert at.session_state["last_metrics"] is not None
    calls_before = fake.call_count

    at.sidebar.button[0].click().run()

    assert at.sidebar.button[0].label == "Nueva conversación"
    assert at.session_state["messages"] == []
    assert at.session_state["last_metrics"] is None
    assert len(at.chat_message) == 0
    assert "Aún no hay métricas" in sidebar_text(at)
    assert fake.call_count == calls_before  # no LLM call on reset

    send(at, "T2")
    assert fake.calls[-1][1] == [{"role": "user", "content": "T2"}]


# --- Requirement: Validación del mensaje del usuario ---


def test_message_is_trimmed_before_validation_and_sending(monkeypatch):
    """Requirement: Validación del mensaje / Scenario: Espacios alrededor del mensaje."""
    fake = FakeProvider()
    at = send(make_app(monkeypatch, fake), "  T  \n")
    assert fake.calls[0][1] == [{"role": "user", "content": "T"}]
    assert history(at)[0] == ("user", "T")


def test_message_over_limit_is_rejected_without_calling_provider(monkeypatch):
    """Requirement: Validación del mensaje / Scenario: Mensaje demasiado largo."""
    fake = FakeProvider()
    at = send(make_app(monkeypatch, fake), "x" * (MAX_TRANSCRIPTION_CHARS + 1))
    assert fake.call_count == 0
    assert at.session_state["messages"] == []
    assert len(at.warning) == 1
    assert str(MAX_TRANSCRIPTION_CHARS) in at.warning[0].value


def test_message_at_exact_limit_is_accepted(monkeypatch):
    """Requirement: Validación del mensaje (el límite es inclusivo, igual que la API)."""
    fake = FakeProvider()
    at = send(make_app(monkeypatch, fake), "x" * MAX_TRANSCRIPTION_CHARS)
    assert fake.call_count == 1
    assert len(at.session_state["messages"]) == 2


def test_message_over_limit_after_prior_turn_keeps_history_unchanged(monkeypatch):
    """Requirement: Validación del mensaje / Scenario: Mensaje demasiado largo (con historial)."""
    fake = FakeProvider()
    at = make_app(monkeypatch, fake)
    send(at, "T")
    before = history(at)
    send(at, "x" * (MAX_TRANSCRIPTION_CHARS + 1))
    assert fake.call_count == 1
    assert history(at) == before


@pytest.mark.parametrize("blank", ["   ", "\n\t  \n"])
def test_blank_message_is_rejected_without_calling_provider(monkeypatch, blank):
    """Requirement: Validación del mensaje / Scenario: Mensaje en blanco."""
    fake = FakeProvider()
    at = send(make_app(monkeypatch, fake), blank)
    assert fake.call_count == 0
    assert at.session_state["messages"] == []
    assert len(at.warning) == 1


# --- Requirement: Configuración y API key desde el entorno ---


def test_missing_active_provider_key_shows_error_and_no_chat(monkeypatch):
    """Requirement: Configuración y API key / Scenario: Falta la API key del proveedor activo."""
    built: list[str] = []
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        llm_service, "build_provider", lambda name, settings: built.append(name) or FakeProvider()
    )
    at = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    assert not at.exception
    assert "ANTHROPIC_API_KEY" in conversation_error(at)
    assert len(at.chat_input) == 0
    assert built == []


def test_missing_openai_key_error_names_variable(monkeypatch):
    """Requirement: Configuración y API key (clave del proveedor por defecto ausente)."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    assert "OPENAI_API_KEY" in conversation_error(at)
    assert len(at.chat_input) == 0


def test_invalid_config_error_does_not_leak_other_key_values(monkeypatch):
    """Requirement: Configuración y API key (el error no incluye valores de keys)."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    at = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    assert FAKE_KEY not in all_text(at)


def test_no_api_key_value_appears_in_the_rendered_ui(monkeypatch):
    """Requirement: Configuración y API key / Scenario: Key fuera de la interfaz."""
    at = send(make_app(monkeypatch), "T")
    text = all_text(at)
    assert text  # the walker really collected the UI
    assert FAKE_KEY not in text
    assert FAKE_ANTHROPIC_KEY not in text
    assert "SECRET" not in text


def test_app_source_has_no_hardcoded_api_keys():
    """Requirement: Configuración y API key (ninguna key en el código)."""
    source = APP_PATH.read_text(encoding="utf-8")
    assert "sk-" not in source


# --- Requirement: Errores del proveedor en la interfaz ---


def test_provider_down_shows_generic_error_and_keeps_history(monkeypatch):
    """Requirement: Errores del proveedor en la interfaz / Scenario: Proveedor caído."""
    fake = FakeProvider(error=LLMProviderError("openai"), error_after=0)
    at = send(make_app(monkeypatch, fake), "T")
    assert not at.exception
    assert at.session_state["messages"] == []
    assert len(at.error) == 1
    assert "openai" not in at.error[0].value.lower()
    assert "Traceback" not in at.error[0].value
    # still operational: a later message works once the provider recovers
    fake.error = None
    send(at, "T")
    assert history(at) == [("user", "T"), ("assistant", fake.result.text)]


def test_failure_mid_stream_saves_neither_user_message_nor_partial_text(monkeypatch):
    """Requirement: Errores del proveedor en la interfaz / Scenario: Fallo a mitad del streaming."""
    fake = FakeProvider(
        fragments=["parcial-1 ", "parcial-2 ", "parcial-3"],
        error=LLMProviderError("openai"),
        error_after=2,
    )
    at = send(make_app(monkeypatch, fake), "T")
    assert not at.exception
    assert at.session_state["messages"] == []
    assert at.session_state["last_metrics"] is None
    assert len(at.error) == 1
    assert "Traceback" not in at.error[0].value


@pytest.mark.parametrize("error_after", [0, 1])
def test_failed_call_keeps_previous_history_and_metrics(monkeypatch, error_after):
    """Requirement: Métricas / Scenario: Llamada fallida (también: historial intacto)."""
    fake = FakeProvider(text="E", fragments=["E", "E-more"])
    at = make_app(monkeypatch, fake)
    send(at, "T")
    metrics = at.session_state["last_metrics"]
    before = history(at)
    shown_before = sidebar_text(at)

    fake.error = LLMProviderError("openai")
    fake.error_after = error_after
    send(at, "ajuste")

    assert len(at.error) == 1
    assert history(at) == before
    assert at.session_state["last_metrics"] == metrics
    assert sidebar_text(at) == shown_before


class EagerFailingProvider(FakeProvider):
    """Provider whose `stream()` raises at call time (not on iteration) once `down` is set."""

    down = False

    def stream(self, system, messages):
        if self.down:
            self.calls.append((system, list(messages)))
            raise LLMProviderError("openai")
        return super().stream(system, messages)


def test_eager_stream_failure_shows_generic_error_and_keeps_state(monkeypatch):
    """Requirement: Errores del proveedor en la interfaz / Scenario: Proveedor caído.

    `stream()` raising at call time (not on iteration) must be handled like any provider
    error: generic message, history and previous metrics untouched, no exception escapes.
    """
    fake = EagerFailingProvider(text="E")
    at = make_app(monkeypatch, fake)
    send(at, "T")
    metrics = at.session_state["last_metrics"]
    before = history(at)

    fake.down = True
    send(at, "ajuste")

    assert not at.exception
    assert len(at.error) == 1
    assert "openai" not in at.error[0].value.lower()
    assert "Traceback" not in at.error[0].value
    assert history(at) == before
    assert at.session_state["last_metrics"] == metrics


# --- Requirement: Panel de contexto CAG ---


def test_sidebar_shows_endpoint_prompt_read_only_with_empty_history(monkeypatch):
    """Requirement: Panel de contexto CAG / Scenario: Contexto visible (system prompt)."""
    at = make_app(monkeypatch)
    prompts = [t for t in at.sidebar.text_area if t.value == _system_prompt()]
    assert len(prompts) == 1
    assert prompts[0].disabled
    assert not [t for t in at.sidebar.text_area if t.value == _chat_system_prompt()]


def test_sidebar_shows_conversation_prompt_after_first_reply(monkeypatch):
    """Requirement: Panel de contexto CAG / Scenario: Prompt tras la primera respuesta."""
    at = make_app(monkeypatch)
    send(at, "T")
    assert [t for t in at.sidebar.text_area if t.value == _chat_system_prompt()]
    assert not [t for t in at.sidebar.text_area if t.value == _system_prompt()]


def test_sidebar_returns_to_endpoint_prompt_on_new_conversation(monkeypatch):
    """Requirement: Panel de contexto CAG / Nueva conversación vuelve al prompt del endpoint."""
    at = make_app(monkeypatch)
    send(at, "T")
    at.sidebar.button[0].click().run()
    assert [t for t in at.sidebar.text_area if t.value == _system_prompt()]
    assert not [t for t in at.sidebar.text_area if t.value == _chat_system_prompt()]


def test_sidebar_shows_each_cag_example(monkeypatch):
    """Requirement: Panel de contexto CAG / Scenario: Contexto visible (ejemplos)."""
    at = make_app(monkeypatch)
    text = sidebar_text(at)
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] in text
        assert example["estimation"] in text


@pytest.mark.parametrize(
    ("env", "provider", "model"),
    [
        ({}, "openai", "gpt-4o-mini"),
        ({"LLM_PROVIDER": "anthropic"}, "anthropic", "claude-haiku-4-5"),
        ({"LLM_MODEL": "custom-model-x"}, "openai", "custom-model-x"),
    ],
)
def test_sidebar_shows_active_provider_and_model(monkeypatch, env, provider, model):
    """Requirement: Panel de contexto CAG / Scenario: Contexto visible (proveedor y modelo)."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    at = make_app(monkeypatch)
    text = sidebar_text(at)
    assert provider in text
    assert model in text
    assert Settings(_env_file=None).effective_model == model


# --- Requirement: Métricas de la última llamada ---


def test_sidebar_says_no_metrics_before_first_call(monkeypatch):
    """Requirement: Métricas / Scenario: Antes de la primera llamada."""
    at = make_app(monkeypatch)
    assert "Aún no hay métricas" in sidebar_text(at)


def test_sidebar_shows_metrics_after_a_response(monkeypatch):
    """Requirement: Métricas / Scenario: Métricas tras una respuesta."""
    fake = FakeProvider(
        model="gpt-4o-mini", provider="openai", input_tokens=1200, output_tokens=300
    )
    at = send(make_app(monkeypatch, fake), "T")
    metrics = at.session_state["last_metrics"]
    text = sidebar_text(at)
    assert "Aún no hay métricas" not in text
    assert "gpt-4o-mini" in text
    assert "openai" in text
    assert "1200" in text
    assert "300" in text
    assert "Tiempo total" in text
    assert "primer fragmento" in text
    expected_cost = estimate_cost("gpt-4o-mini", 1200, 300)
    assert expected_cost is not None
    assert metrics.cost_usd == pytest.approx(expected_cost)
    assert f"${expected_cost:.6f}" in text
    assert metrics.total_s >= metrics.ttft_s >= 0


def test_metrics_show_nd_when_model_has_no_known_price(monkeypatch):
    """Requirement: Métricas / coste "n/d" si el modelo no tiene precio conocido."""
    fake = FakeProvider(model="mystery-model")
    at = send(make_app(monkeypatch, fake), "T")
    assert at.session_state["last_metrics"].cost_usd is None
    assert "n/d" in sidebar_text(at)


def test_metrics_are_kept_between_reruns_until_next_call(monkeypatch):
    """Requirement: Métricas (se conservan entre reruns hasta la siguiente llamada)."""
    at = send(make_app(monkeypatch, FakeProvider(input_tokens=1200, output_tokens=300)), "T")
    first = sidebar_text(at)
    at.run()  # a plain rerun without input
    assert sidebar_text(at) == first


class Interrupted(BaseException):
    """Stand-in for the user pressing Stop (Streamlit stops a script via a BaseException)."""


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_sidebar_is_rendered_before_the_stream_so_it_survives_an_interruption(monkeypatch):
    """Requirement: Panel de contexto CAG / Scenario: Panel visible tras interrumpir.

    AppTest cannot press Stop (the scenario stays MANUAL). Approximation: the stream raises a
    BaseException mid-flight; the sidebar, rendered at the start of the script, already shows
    the context and the metrics of the last completed call, and nothing partial is persisted.
    """
    fake = FakeProvider(text="E", fragments=["E", "E-more"], input_tokens=1234)
    at = make_app(monkeypatch, fake)
    send(at, "T")
    before = history(at)

    def interrupted_stream(system, messages):
        def gen():
            yield "parcial"
            raise Interrupted

        return gen()

    monkeypatch.setattr(fake, "stream", interrupted_stream)
    send(at, "ajuste")

    text = sidebar_text(at)
    assert "1234" in text
    assert [t for t in at.sidebar.text_area if t.value == _chat_system_prompt()]
    assert history(at) == before
