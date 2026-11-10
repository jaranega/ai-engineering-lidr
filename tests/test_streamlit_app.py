"""Tests for the Streamlit estimation form (spec: estimation-form-ui), without a browser.

The UI is an HTTP client of the API, run with `streamlit.testing.v1.AppTest`. `httpx.Client` is
patched with a factory (design D12): `httpx.MockTransport` for the protocol cases and
`TestClient(app)` (ASGI transport) for the end-to-end ones. No network, no real server and no
real LLM.

Manual checks (not automatable with AppTest): scenario "Arranque de la interfaz" with
`uv run streamlit run streamlit_app.py` (layout, native character counter) and the optional
screenshot of task 7.5.
"""

import ast
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from structlog.testing import capture_logs

from app import domain
from app.config import get_settings
from app.main import app
from app.prompts.loader import DEFAULT_PROMPT_VERSION, PROMPT_VERSIONS
from app.services import llm_service
from app.services.cache import get_response_cache
from tests.conftest import FakeProvider, example_estimates, make_settings

APP_PATH = Path(__file__).resolve().parent.parent / "streamlit_app.py"
BASE_URL = "http://api.test"
ESTIMATE_URL = f"{BASE_URL}/api/v1/estimate"
GENERIC_ERROR = "No se pudo generar la estimación. Inténtalo de nuevo en unos instantes."
TRUNCATED = (
    "La estimación está incompleta: se cortó por el límite de longitud. "
    "Prueba con un nivel de detalle menor."
)
FAKE_OPENAI_KEY = "sk-fake-ui-SECRET-123"
FAKE_ANTHROPIC_KEY = "sk-ant-fake-ui-SECRET-456"
DESCRIPTION = "Una app móvil para reservar clases de un gimnasio y gestionar las cuotas."
SECRET = "DESCRIPCION-SECRETA-123"
REAL_CLIENT = httpx.Client

HEALTH = {
    "status": "ok",
    "env": "development",
    "primary_model": "gpt-4o-mini",
    "fallback_model": "claude-haiku-4-5",
    "cache": "ok",
}
VALIDATION = {
    "output_format": "line_items",
    "checks_total": 8,
    "has_title": True,
    "has_breakdown_table": True,
    "has_totals": True,
    "has_team": False,
    "has_duration": False,
    "rows_parsed": 3,
    "declared_total_hours": 120.0,
    "sum_row_hours": 120.0,
    "hours_match": True,
    "declared_total_cost": 7500.0,
    "sum_row_cost": 7500.0,
    "cost_match": True,
    "finish_ok": True,
    "score": 0.75,
    "issues": ["missing recommended team", "missing estimated duration in weeks"],
}
RESPONSE = {
    "text": "## Estimación: Gimnasio\n\nTEXTO-DE-LA-ESTIMACION",
    "prompt_version": "v1",
    "model": "gpt-4o-mini",
    "provider": "openai",
    "input_tokens": 1200,
    "output_tokens": 300,
    "cost_usd": 0.00054,
    "truncated": False,
    "fallback_used": False,
    "cache_hit": False,
    "latency_ms": 900,
    "validation": VALIDATION,
}


class FakeApi:
    """In-memory API behind `httpx.MockTransport`: records requests, configurable per test."""

    def __init__(self) -> None:
        self.health: dict | None = dict(HEALTH)
        self.down = False  # every request raises ConnectError
        self.estimate_down = False  # only POST /estimate raises ConnectError
        self.estimate_error: Exception | None = None  # raised by POST /estimate
        self.status = 200
        self.json_body: object = dict(RESPONSE)
        self.raw_body: bytes | None = None  # overrides `json_body`
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/health":
            if self.health is None:
                return httpx.Response(503)
            return httpx.Response(200, json=self.health)
        if request.url.path == "/api/v1/estimate":
            if self.estimate_down:
                raise httpx.ConnectError("connection refused", request=request)
            if self.estimate_error is not None:
                raise self.estimate_error
            if self.raw_body is not None:
                return httpx.Response(self.status, content=self.raw_body)
            return httpx.Response(self.status, json=self.json_body)
        return httpx.Response(404)

    @property
    def estimates(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/api/v1/estimate"]

    def last_body(self) -> dict:
        return json.loads(self.estimates[-1].content)


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


def submit(
    at: AppTest,
    description: str = DESCRIPTION,
    *,
    project_type: str | None = None,
    detail_level: str | None = None,
    output_format: str | None = None,
    prompt_version: str | None = None,
) -> AppTest:
    """Fill the form and press "Estimar" in the same run (a run in between loses the text)."""
    at.text_area[0].set_value(description)
    if project_type is not None:
        at.selectbox[0].set_value(project_type)
    if detail_level is not None:
        at.radio[0].set_value(detail_level)
    if output_format is not None:
        at.selectbox[1].set_value(output_format)
    if prompt_version is not None:
        at.selectbox[2].set_value(prompt_version)
    at.button[0].click().run()
    return at


def all_text(at: AppTest) -> str:
    chunks: list[str] = []

    def walk(block) -> None:
        for child in block.children.values():
            for attr in ("value", "label", "body"):
                value = getattr(child, attr, None)
                if isinstance(value, str):
                    chunks.append(value)
            if hasattr(child, "children"):
                walk(child)

    walk(at.main)
    walk(at.sidebar)
    return "\n".join(chunks)


def sidebar_text(at: AppTest) -> str:
    chunks = [e.value for e in at.sidebar.markdown] + [e.value for e in at.sidebar.warning]
    return "\n".join(chunks)


def warnings(at: AppTest) -> list[str]:
    return [e.value for e in at.warning]


def errors(at: AppTest) -> list[str]:
    return [e.value for e in at.error]


def unavailable(url: str = BASE_URL) -> str:
    return (
        f"No se puede conectar con el servicio de estimación (`{url}`). "
        "Arráncalo con `uv run uvicorn app.main:app` o `docker compose up`."
    )


def load_ui_namespace() -> dict:
    """Execute `streamlit_app.py` without its final `main()` call, to read constants and helpers."""
    source = APP_PATH.read_text(encoding="utf-8").rstrip()
    assert source.endswith("\nmain()")
    namespace: dict = {"__name__": "streamlit_app_under_test"}
    exec(compile(source.removesuffix("main()"), str(APP_PATH), "exec"), namespace)  # noqa: S102
    return namespace


# --- Requirement: Aplicación Streamlit con formulario tipado ---


def test_app_starts_with_the_form_defaults_and_no_chat(api):
    """Requirement: Aplicación Streamlit con formulario tipado / Scenario: Arranque (AppTest)."""
    at = run_app()
    assert not at.exception
    assert at.title[0].value == "Estimador de software"
    assert len(at.chat_input) == 0
    assert len(at.chat_message) == 0
    assert at.text_area[0].value == ""
    assert at.selectbox[0].label == "Tipo de proyecto"
    assert at.radio[0].label == "Nivel de detalle"
    assert at.radio[0].value == "medium"
    assert at.selectbox[1].label == "Formato de salida"
    assert at.selectbox[1].value == "line_items"
    assert at.button[0].label == "Estimar"
    assert at.expander[0].label == "Opciones avanzadas"
    assert at.selectbox[2].value == "v1"
    assert at.session_state["last_result"] is None
    assert api.estimates == []


def test_the_selectors_offer_the_expected_labels(api):
    """Requirement: Aplicación Streamlit con formulario tipado (opciones de los selectores)."""
    at = run_app()
    assert list(at.selectbox[0].options) == [
        "App móvil",
        "SaaS web",
        "Herramienta interna",
        "Pipeline de datos",
    ]
    assert list(at.radio[0].options) == ["Resumen", "Medio", "Detallado"]
    assert list(at.selectbox[1].options) == ["Tabla por fases", "Partidas", "Narrativa"]
    assert list(at.selectbox[2].options) == ["v1", "v2"]


def test_the_form_exposes_only_the_agreed_widgets(api):
    """Requirement: Aplicación Streamlit (sin reference_projects ni parámetros de generación)."""
    at = run_app()
    assert len(at.text_area) == 1
    assert len(at.selectbox) == 3
    assert len(at.radio) == 1
    assert len(at.button) == 1
    for empty in (at.text_input, at.number_input, at.slider, at.checkbox, at.toggle):
        assert len(empty) == 0


def test_submit_posts_the_typed_json_with_exact_enum_values_and_the_trimmed_text(api):
    """Requirement: Aplicación Streamlit con formulario tipado / Scenario: Envío tipado."""
    at = submit(
        run_app(),
        f"  {DESCRIPTION}\n ",
        project_type="mobile_app",
        detail_level="detailed",
        output_format="phases_table",
    )
    assert not at.exception
    assert len(api.estimates) == 1
    request = api.estimates[0]
    assert request.method == "POST"
    assert str(request.url) == f"{ESTIMATE_URL}?prompt_version=v1"
    assert json.loads(request.content) == {
        "description": DESCRIPTION,
        "project_type": "mobile_app",
        "detail_level": "detailed",
        "output_format": "phases_table",
    }


def test_defaults_are_sent_when_only_the_description_is_filled(api):
    """Requirement: Aplicación Streamlit con formulario tipado (valores por defecto)."""
    submit(run_app())
    assert api.last_body() == {
        "description": DESCRIPTION,
        "project_type": "mobile_app",
        "detail_level": "medium",
        "output_format": "line_items",
    }


def test_prompt_version_v2_goes_in_the_query(api):
    """Requirement: Aplicación Streamlit con formulario tipado / Scenario: Versión del prompt."""
    submit(run_app(), prompt_version="v2")
    request = api.estimates[0]
    assert request.url.params["prompt_version"] == "v2"
    assert "prompt_version" not in json.loads(request.content)


def test_request_never_carries_reference_projects_or_generation_parameters(api):
    """Requirement: Aplicación Streamlit (no expone reference_projects ni parámetros)."""
    submit(run_app())
    assert set(api.last_body()) == {"description", "project_type", "detail_level", "output_format"}


def test_streamlit_app_imports_nothing_from_the_service_nor_llm_libraries():
    """Requirement: Aplicación Streamlit con formulario tipado / Scenario: Sin dependencias."""
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
    """Design D12: only `httpx.Client` in a `with`, never `httpx.get`/`httpx.post`."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "httpx"
    }
    assert not used & {"get", "post", "stream", "request", "put", "delete"}


def test_ui_values_and_limits_match_the_api_domain():
    """Requirement: Aplicación Streamlit con formulario tipado / Scenario: Valores alineados."""
    ui = load_ui_namespace()
    assert list(ui["PROJECT_TYPE_LABELS"]) == [t.value for t in domain.ProjectType]
    assert list(ui["DETAIL_LEVEL_LABELS"]) == [t.value for t in domain.DetailLevel]
    assert list(ui["OUTPUT_FORMAT_LABELS"]) == [t.value for t in domain.OutputFormat]
    assert tuple(ui["PROMPT_VERSIONS"]) == tuple(PROMPT_VERSIONS)
    assert ui["DEFAULT_PROMPT_VERSION"] == DEFAULT_PROMPT_VERSION
    assert ui["MIN_DESCRIPTION_CHARS"] == domain.MIN_DESCRIPTION_CHARS == 20
    assert ui["MAX_DESCRIPTION_CHARS"] == domain.MAX_DESCRIPTION_CHARS == 2000
    assert ui["DEFAULT_DETAIL_LEVEL"] == domain.DetailLevel.MEDIUM.value
    assert ui["DEFAULT_OUTPUT_FORMAT"] == domain.OutputFormat.LINE_ITEMS.value


# --- Requirement: Validación en el cliente ---


@pytest.mark.parametrize(
    "description",
    ["0123456789", "   corta   ", "a" * 19, " " + "a" * 19 + "  ", "   \n  "],
    ids=["10", "padded-short", "19", "19-padded", "blank"],
)
def test_short_description_warns_and_sends_nothing(api, description):
    """Requirement: Validación en el cliente / Scenario: Descripción corta."""
    at = submit(run_app(), description)
    assert not at.exception
    assert api.estimates == []
    assert any("al menos 20 caracteres" in w for w in warnings(at))
    assert at.session_state["last_result"] is None
    assert at.text_area[0].value == description  # the form keeps its values


def test_long_description_is_limited_by_the_widget_and_the_client_message_names_the_limit(api):
    """Requirement: Validación en el cliente (máximo de 2000: lo impone el widget y el aviso)."""
    message = load_ui_namespace()["validate_description"]("a" * 2001)
    assert message is not None and "2000" in message
    # Streamlit truncates the text area to `max_chars`: more than 2000 never reaches the API.
    at = run_app()
    at.text_area[0].set_value("a" * 2001)
    at.button[0].click().run()
    assert all(len(json.loads(r.content)["description"]) <= 2000 for r in api.estimates)


@pytest.mark.parametrize("length", [20, 2000])
def test_description_at_the_limits_is_sent(api, length):
    """Requirement: Validación en el cliente (20 y 2000 caracteres tras recortar son válidos)."""
    padding = "  " if length == 20 else ""  # the widget itself caps the raw text at 2000
    submit(run_app(), padding + "a" * length + padding)
    assert len(api.estimates) == 1
    assert api.last_body()["description"] == "a" * length


def test_validate_description_is_the_mirror_of_the_api():
    """Requirement: Validación en el cliente (función pura)."""
    validate = load_ui_namespace()["validate_description"]
    assert validate("a" * 19) is not None
    assert validate("a" * 20) is None
    assert validate("a" * 2000) is None
    assert validate("a" * 2001) is not None


# --- Requirement: Resultado de la estimación ---


def test_full_result_shows_text_version_metrics_and_validation(api):
    """Requirement: Resultado de la estimación / Scenario: Resultado completo."""
    api.json_body = {**RESPONSE, "prompt_version": "v2"}
    at = submit(run_app(), prompt_version="v2")
    assert not at.exception
    text = all_text(at)
    assert "TEXTO-DE-LA-ESTIMACION" in text
    assert "Versión del prompt: v2" in text
    assert "Modelo: `gpt-4o-mini`" in text
    assert "Proveedor: `openai`" in text
    assert "Respaldo: no" in text
    assert "Desde caché: no" in text
    assert "Tokens de entrada: 1200" in text
    assert "Tokens de salida: 300" in text
    assert "Coste estimado: $0.000540" in text
    assert "Latencia del servidor: 900 ms" in text
    assert "Tiempo total en el cliente:" in text
    assert "Comprobaciones superadas: **6/8**" in text
    assert warnings(at) == VALIDATION["issues"]
    assert not at.error


def test_result_is_kept_in_session_state_and_survives_reruns(api):
    """Requirement: Resultado de la estimación (conservado en el estado de la sesión)."""
    at = submit(run_app())
    stored = at.session_state["last_result"]
    assert stored["data"] == RESPONSE
    assert stored["client_total_s"] >= 0
    at.run()  # an unrelated rerun
    assert "TEXTO-DE-LA-ESTIMACION" in all_text(at)
    assert len(api.estimates) == 1


def test_truncated_response_shows_the_incomplete_warning_with_the_text(api):
    """Requirement: Resultado de la estimación / Scenario: Respuesta truncada."""
    api.json_body = {**RESPONSE, "truncated": True}
    at = submit(run_app())
    assert TRUNCATED in warnings(at)
    assert "TEXTO-DE-LA-ESTIMACION" in all_text(at)


def test_untruncated_response_has_no_incomplete_warning(api):
    """Requirement: Resultado de la estimación (aviso solo si truncated)."""
    at = submit(run_app())
    assert TRUNCATED not in warnings(at)


def test_null_validation_shows_text_and_metrics_without_error(api):
    """Requirement: Resultado de la estimación / Scenario: Sin validación."""
    api.json_body = {**RESPONSE, "validation": None}
    at = submit(run_app())
    assert not at.exception
    text = all_text(at)
    assert "TEXTO-DE-LA-ESTIMACION" in text
    assert "Tokens de entrada: 1200" in text
    assert "Comprobaciones superadas" not in text
    assert not at.error


def test_unknown_cost_is_shown_as_not_available(api):
    """Requirement: Resultado de la estimación (coste `null` es "no disponible")."""
    api.json_body = {**RESPONSE, "cost_usd": None}
    at = submit(run_app())
    assert "Coste estimado: no disponible" in all_text(at)


def test_fallback_and_cache_flags_are_shown(api):
    """Requirement: Resultado de la estimación (respaldo y caché en las métricas)."""
    api.json_body = {**RESPONSE, "fallback_used": True, "cache_hit": True}
    text = all_text(submit(run_app()))
    assert "Respaldo: sí" in text
    assert "Desde caché: sí" in text


def test_a_new_result_replaces_the_previous_one(api):
    """Requirement: Resultado de la estimación (el último resultado sustituye al anterior)."""
    at = submit(run_app())
    api.json_body = {**RESPONSE, "text": "## Estimación: SEGUNDA"}
    submit(at)
    text = all_text(at)
    assert "SEGUNDA" in text
    assert "TEXTO-DE-LA-ESTIMACION" not in text


def test_pure_formatters():
    """Requirement: Resultado de la estimación (puntuación como aprobadas/total, coste)."""
    ui = load_ui_namespace()
    assert ui["format_score"]({"score": 0.75, "checks_total": 8}) == "6/8"
    assert ui["format_score"]({"score": 0.8, "checks_total": 5}) == "4/5"
    assert ui["format_score"]({"score": 1.0, "checks_total": 5}) == "5/5"
    assert ui["format_cost"](None) == "no disponible"
    assert ui["format_cost"](0.5) == "$0.500000"


# --- Requirement: Errores de la API en la interfaz ---


def validation_error(*locs: list) -> dict:
    return {"detail": [{"loc": loc, "msg": "bad", "type": "x", "input": SECRET} for loc in locs]}


def test_422_names_the_rejected_fields_and_never_the_input(api):
    """Requirement: Errores de la API en la interfaz / Scenario: Error de validación."""
    api.status = 422
    api.json_body = validation_error(["body", "description"], ["query", "prompt_version"])
    at = submit(run_app(), f"{SECRET} una app de reservas")
    assert not at.exception
    shown = warnings(at)
    assert len(shown) == 1
    assert "`description`" in shown[0] and "`prompt_version`" in shown[0]
    shown = "\n".join(
        e.value for e in [*at.warning, *at.error, *at.markdown, *at.caption, *at.info]
    )
    assert SECRET not in shown  # only the form field itself (kept below) holds the text
    assert at.text_area[0].value == f"{SECRET} una app de reservas"  # values are kept
    assert at.session_state["last_result"] is None


def test_422_with_an_unreadable_body_is_still_a_warning(api):
    """Requirement: Errores de la API en la interfaz (422 sin campos legibles)."""
    api.status = 422
    api.raw_body = b"not json"
    at = submit(run_app())
    assert not at.exception
    assert len(warnings(at)) == 1
    assert not at.error


def test_rejected_fields_uses_only_loc():
    """Requirement: Errores de la API en la interfaz (nombres de `loc`, nunca `input`)."""
    rejected = load_ui_namespace()["rejected_fields"]
    response = httpx.Response(422, json=validation_error(["body", "description"], ["body", "x"]))
    assert rejected(response) == ["description", "x"]
    assert rejected(httpx.Response(422, content=b"[")) == []


@pytest.mark.parametrize("status", [502, 500, 503, 404])
def test_provider_or_other_status_shows_the_generic_error_without_details(api, status):
    """Requirement: Errores de la API en la interfaz / Scenario: Fallo del proveedor."""
    api.status = status
    api.json_body = {"detail": "SECRET-DETAIL Traceback sk-leak"}
    at = submit(run_app())
    assert not at.exception
    assert errors(at) == [GENERIC_ERROR]
    assert "SECRET-DETAIL" not in all_text(at)
    assert "Traceback" not in all_text(at)
    assert at.session_state["last_result"] is None


def test_provider_failure_shows_no_new_result_and_keeps_the_previous_one(api):
    """Requirement: Errores de la API / Scenario: Fallo del proveedor (sin resultado nuevo)."""
    at = submit(run_app())
    api.status = 502
    submit(at)
    assert errors(at) == [GENERIC_ERROR]
    assert at.session_state["last_result"]["data"] == RESPONSE
    assert at.text_area[0].value == DESCRIPTION


@pytest.mark.parametrize(
    "body", [b"not json at all", b"[1, 2]", b'{"no_text": true}', b'{"text": 5}'], ids=str
)
def test_invalid_json_is_the_generic_error_and_is_logged(api, body):
    """Requirement: Errores de la API (JSON inválido) / Logs (reason invalid_payload)."""
    api.raw_body = body
    with capture_logs() as logs:
        at = submit(run_app())
    assert errors(at) == [GENERIC_ERROR]
    assert [(e["event"], e["reason"]) for e in logs if e["event"] == "api_request_failed"] == [
        ("api_request_failed", "invalid_payload")
    ]


def test_read_timeout_is_the_generic_error_and_is_logged(api):
    """Requirement: Errores de la API (tiempo de lectura agotado) / Logs (reason timeout)."""
    api.estimate_error = httpx.ReadTimeout("timed out")
    with capture_logs() as logs:
        at = submit(run_app())
    assert errors(at) == [GENERIC_ERROR]
    assert any(e["event"] == "api_request_failed" and e["reason"] == "timeout" for e in logs)
    assert not any(e["event"] == "api_unreachable" for e in logs)


def test_transport_error_is_the_generic_error_and_is_logged(api):
    """Requirement: Errores de la API / Logs (reason transport_error)."""
    api.estimate_error = httpx.RemoteProtocolError("peer closed")
    with capture_logs() as logs:
        at = submit(run_app())
    assert errors(at) == [GENERIC_ERROR]
    assert any(
        e["event"] == "api_request_failed" and e["reason"] == "transport_error" for e in logs
    )


def test_the_read_timeout_is_120_seconds():
    """Requirement: Errores de la API en la interfaz (tiempo de lectura de 120 segundos)."""
    timeout = load_ui_namespace()["ESTIMATE_TIMEOUT"]
    assert timeout.read == 120.0


# --- Requirement: URL del servicio por configuración ---


def test_default_url_is_localhost_8000(api, monkeypatch):
    """Requirement: URL del servicio por configuración / Scenario: URL por defecto."""
    monkeypatch.delenv("ESTIMATOR_API_BASE_URL")
    submit(run_app())
    assert str(api.estimates[0].url) == "http://localhost:8000/api/v1/estimate?prompt_version=v1"


def test_configured_url_from_the_environment(api, monkeypatch):
    """Requirement: URL del servicio por configuración / Scenario: URL configurada."""
    monkeypatch.setenv("ESTIMATOR_API_BASE_URL", "http://api.local:9000")
    submit(run_app())
    assert api.estimates[0].url.scheme == "http"
    assert (api.estimates[0].url.host, api.estimates[0].url.port) == ("api.local", 9000)
    assert api.estimates[0].url.path == "/api/v1/estimate"
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
    at = submit(run_app())
    assert api.estimates[0].url.host == "from-dotenv"
    text = all_text(at)
    assert FAKE_OPENAI_KEY not in text and FAKE_ANTHROPIC_KEY not in text
    assert "SECRET" not in text


def test_environment_wins_over_the_env_file(api, tmp_path):
    """Requirement: URL del servicio (el entorno tiene precedencia sobre `.env`)."""
    (tmp_path / ".env").write_text("ESTIMATOR_API_BASE_URL=http://from-dotenv:7000\n")
    submit(run_app())
    assert api.estimates[0].url.host == "api.test"


def test_app_does_not_load_provider_keys_into_the_environment(api, tmp_path):
    """Requirement: URL del servicio (la app no carga las API keys de los proveedores)."""
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FAKE_OPENAI_KEY}\n")
    run_app()
    assert "OPENAI_API_KEY" not in os.environ


# --- Requirement: API no disponible ---


def test_opening_with_the_api_down_warns_in_the_sidebar_and_keeps_the_form_visible(api):
    """Requirement: API no disponible / Scenario: Abrir la app con la API parada."""
    api.down = True
    at = run_app()
    assert not at.exception
    assert unavailable() in [w.value for w in at.sidebar.warning]
    assert len(at.text_area) == 1
    assert at.button[0].label == "Estimar"


def test_unavailable_warning_uses_the_configured_url(api, monkeypatch):
    """Requirement: API no disponible (aviso con la URL configurada)."""
    api.down = True
    monkeypatch.setenv("ESTIMATOR_API_BASE_URL", "http://api.local:9000/")
    at = run_app()
    assert unavailable("http://api.local:9000") in [w.value for w in at.sidebar.warning]


def test_sending_with_the_api_down_warns_keeps_values_and_recovers_without_reload(api):
    """Requirement: API no disponible / Scenario: Envío con la API parada."""
    api.down = True
    with capture_logs() as logs:
        at = submit(run_app(), project_type="data_pipeline", detail_level="summary")
    assert not at.exception
    assert unavailable() in warnings(at)
    assert not at.error
    assert at.session_state["last_result"] is None
    assert at.text_area[0].value == DESCRIPTION
    assert (at.selectbox[0].value, at.radio[0].value) == ("data_pipeline", "summary")
    assert any(e["event"] == "api_unreachable" for e in logs)

    api.down = False  # the API comes back; no page reload
    at.button[0].click().run()
    assert not at.exception
    assert "TEXTO-DE-LA-ESTIMACION" in all_text(at)
    assert api.last_body()["project_type"] == "data_pipeline"


def test_send_with_health_up_but_estimate_refused_shows_unavailable(api):
    """Requirement: API no disponible (conexión rechazada solo en el POST)."""
    api.estimate_down = True
    at = submit(run_app())
    assert unavailable() in warnings(at)
    assert not at.sidebar.warning


def test_connect_timeout_counts_as_unavailable(api):
    """Requirement: API no disponible (timeout de conexión)."""
    api.estimate_error = httpx.ConnectTimeout("connect timed out")
    at = submit(run_app())
    assert unavailable() in warnings(at)


def test_sidebar_shows_the_health_of_the_api(api):
    """Requirement: API no disponible (panel lateral con /health: modelos y caché)."""
    at = run_app()
    text = sidebar_text(at)
    assert "**Modelo primario:** `gpt-4o-mini`" in text
    assert "**Modelo de respaldo:** `claude-haiku-4-5`" in text
    assert "**Caché:** disponible" in text
    assert not at.sidebar.warning


def test_sidebar_says_there_is_no_fallback_and_the_cache_is_unavailable(api):
    """Requirement: API no disponible (estado de /health sin respaldo ni caché)."""
    api.health = {**HEALTH, "fallback_model": None, "cache": "unavailable"}
    text = sidebar_text(run_app())
    assert "Sin respaldo" in text
    assert "**Caché:** no disponible" in text


def test_unhealthy_health_endpoint_shows_the_unavailable_warning(api):
    """Requirement: API no disponible (si /health no responde 200 se muestra el aviso)."""
    api.health = None
    at = run_app()
    assert unavailable() in [w.value for w in at.sidebar.warning]


def test_sidebar_has_only_health_no_context_panel_history_or_new_conversation(api):
    """Requirement: API no disponible (la barra lateral solo tiene /health)."""
    at = run_app()
    assert len(at.sidebar.text_area) == 0
    assert len(at.sidebar.button) == 0
    assert [r.url.path for r in api.requests] == ["/health"]


# --- Requirement: Logs de la interfaz ---


def test_failure_is_logged_with_reason_and_status_without_content(api):
    """Requirement: Logs de la interfaz / Scenario: Fallo registrado sin contenido."""
    api.status = 502
    api.json_body = {"detail": "SECRET-DETAIL"}
    with capture_logs() as logs:
        at = submit(run_app(), f"{SECRET} app de reservas")
    assert errors(at) == [GENERIC_ERROR]
    failed = [e for e in logs if e["event"] == "api_request_failed"]
    assert len(failed) == 1
    assert (failed[0]["reason"], failed[0]["status_code"]) == ("http_status", 502)
    dumped = json.dumps(logs, default=str)
    assert SECRET not in dumped
    assert "SECRET-DETAIL" not in dumped


def test_422_is_logged_as_http_status_without_the_input(api):
    """Requirement: Logs de la interfaz (reason http_status con el código 422)."""
    api.status = 422
    api.json_body = validation_error(["body", "description"])
    with capture_logs() as logs:
        submit(run_app(), f"{SECRET} app de reservas")
    failed = [e for e in logs if e["event"] == "api_request_failed"]
    assert [(e["reason"], e["status_code"]) for e in failed] == [("http_status", 422)]
    assert SECRET not in json.dumps(logs, default=str)


def test_unreachable_is_logged_with_the_error_type_only(api):
    """Requirement: Logs de la interfaz (`api_unreachable` con el tipo de error)."""
    api.down = True
    with capture_logs() as logs:
        submit(run_app(), f"{SECRET} app de reservas")
    unreachable = [e for e in logs if e["event"] == "api_unreachable"]
    assert [e["error_type"] for e in unreachable] == ["ConnectError"]
    assert SECRET not in json.dumps(logs, default=str)


def test_successful_estimation_logs_no_failure_events(api):
    """Requirement: Logs de la interfaz (solo se registran fallos)."""
    with capture_logs() as logs:
        submit(run_app())
    assert not [e for e in logs if e["event"] in {"api_unreachable", "api_request_failed"}]


# --- End to end: UI -> API -> service (TestClient as httpx.Client) ---


@pytest.fixture
def e2e(monkeypatch: pytest.MonkeyPatch, response_cache):
    """Patch `httpx.Client` with an ASGI-backed TestClient over the real app and a FakeProvider."""
    provider = FakeProvider(
        text=example_estimates("line_items")[0], model="gpt-4o-mini", input_tokens=1200
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


def test_e2e_submit_reaches_the_service_with_rendered_prompts(e2e):
    """Requirement: Envío tipado y Resultado completo (de extremo a extremo con la API real)."""
    at = run_app()
    assert not at.exception
    assert "**Modelo de respaldo:** `claude-haiku-4-5`" in sidebar_text(at)
    submit(at, f"  {DESCRIPTION}  ", detail_level="detailed", output_format="line_items")
    assert not at.exception
    system, messages = e2e.calls[0]
    assert f"<project_description>\n{DESCRIPTION}\n</project_description>" in messages[0]["content"]
    assert "<project_type>mobile_app</project_type>" in messages[0]["content"]
    assert "Lista los supuestos de cada fase" in system
    assert DESCRIPTION not in system
    text = all_text(at)
    assert "Versión del prompt: v1" in text
    assert "Comprobaciones superadas: **8/8**" in text
    assert "Tokens de entrada: 1200" in text


def test_e2e_prompt_version_v2_reaches_the_service(e2e):
    """Requirement: Versión del prompt (de extremo a extremo)."""
    at = submit(run_app(), prompt_version="v2")
    assert "Versión del prompt: v2" in all_text(at)
    assert "concisas" in e2e.calls[0][0]


def test_e2e_provider_failure_reaches_the_ui_as_the_generic_error(e2e):
    """Requirement: Errores de la API / Scenario: Fallo del proveedor (de extremo a extremo)."""
    e2e.error = llm_service.LLMProviderError("openai")
    at = submit(run_app())
    assert errors(at) == [GENERIC_ERROR]
    assert at.session_state["last_result"] is None
    assert "Traceback" not in all_text(at)


def test_e2e_cache_hit_is_reported_in_the_ui(e2e, response_cache):
    """Requirement: Resultado de la estimación (métricas: caché) de extremo a extremo."""
    cached = llm_service.CachedProvider(e2e, response_cache, "gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: cached
    first = submit(run_app())
    assert "Desde caché: no" in all_text(first)
    second = submit(run_app())
    assert "Desde caché: sí" in all_text(second)
    assert e2e.call_count == 1


def test_e2e_logs_never_contain_the_description(e2e, capsys):
    """Requirement: Datos que nunca se registran (UI + API: la descripción no llega a los logs)."""
    e2e.error = llm_service.LLMProviderError("openai")
    with capture_logs() as logs:
        submit(run_app(), f"{SECRET} una app de reservas con pagos")
    assert SECRET not in json.dumps(logs, default=str)
    assert SECRET not in capsys.readouterr().err
