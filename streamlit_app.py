"""Streamlit UI: typed estimation form, HTTP client of the estimation API."""

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import streamlit as st
import structlog
from dotenv import dotenv_values

logger = structlog.get_logger(__name__)

BASE_URL_KEY = "ESTIMATOR_API_BASE_URL"
DEFAULT_BASE_URL = "http://localhost:8000"
ESTIMATE_PATH = "/api/v1/estimate"
GET_TIMEOUT = httpx.Timeout(3.0)
ESTIMATE_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0)

# Own copies of the API enums and limits (a test compares them with `app.domain`).
PROJECT_TYPE_LABELS = {
    "mobile_app": "App móvil",
    "web_saas": "SaaS web",
    "internal_tool": "Herramienta interna",
    "data_pipeline": "Pipeline de datos",
}
DETAIL_LEVEL_LABELS = {"summary": "Resumen", "medium": "Medio", "detailed": "Detallado"}
OUTPUT_FORMAT_LABELS = {
    "phases_table": "Tabla por fases",
    "line_items": "Partidas",
    "narrative": "Narrativa",
}
PROMPT_VERSIONS = ("v1", "v2")
DEFAULT_DETAIL_LEVEL = "medium"
DEFAULT_OUTPUT_FORMAT = "line_items"
DEFAULT_PROMPT_VERSION = "v1"
MIN_DESCRIPTION_CHARS = 20
MAX_DESCRIPTION_CHARS = 2000

PROVIDER_ERROR_MESSAGE = "No se pudo generar la estimación. Inténtalo de nuevo en unos instantes."
TRUNCATED_WARNING = (
    "La estimación está incompleta: se cortó por el límite de longitud. "
    "Prueba con un nivel de detalle menor."
)


class ApiUnreachable(Exception):
    """The API did not accept the connection."""


class ValidationRejected(Exception):
    """The API answered 422; carries the rejected field names."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


class RequestFailed(Exception):
    """The request did not end in a valid 200 response."""

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class EstimateForm:
    description: str
    project_type: str
    detail_level: str
    output_format: str
    prompt_version: str

    def payload(self) -> dict[str, str]:
        return {
            "description": self.description,
            "project_type": self.project_type,
            "detail_level": self.detail_level,
            "output_format": self.output_format,
        }


def get_base_url() -> str:
    """API base URL from the environment or, for that single key only, from `.env`."""
    value = os.environ.get(BASE_URL_KEY) or dotenv_values(".env").get(BASE_URL_KEY)
    return (value or DEFAULT_BASE_URL).rstrip("/")


def api_unavailable_message(base_url: str) -> str:
    return (
        f"No se puede conectar con el servicio de estimación (`{base_url}`). "
        "Arráncalo con `uv run uvicorn app.main:app` o `docker compose up`."
    )


def fetch_json(base_url: str, path: str) -> dict | None:
    """GET a JSON object from the API, or None if it is unreachable or answers badly."""
    try:
        with httpx.Client(base_url=base_url, timeout=GET_TIMEOUT) as client:
            response = client.get(path)
        data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def validate_description(description: str) -> str | None:
    """Return a Spanish warning for an invalid (already trimmed) description, else None."""
    if len(description) < MIN_DESCRIPTION_CHARS:
        return (
            f"La descripción debe tener al menos {MIN_DESCRIPTION_CHARS} caracteres "
            f"({len(description)})."
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        return (
            f"La descripción no puede superar los {MAX_DESCRIPTION_CHARS} caracteres "
            f"({len(description)})."
        )
    return None


def rejected_fields(response: httpx.Response) -> list[str]:
    """Field names from a 422 body (`loc` only, never `input`)."""
    try:
        detail = response.json().get("detail", [])
        names = [str(item["loc"][-1]) for item in detail if item.get("loc")]
    except (ValueError, AttributeError, KeyError, TypeError):
        return []
    return list(dict.fromkeys(names))


def post_estimate(base_url: str, form: EstimateForm) -> dict[str, Any]:
    """POST the form; return the response JSON plus the client-side total time."""
    start = time.perf_counter()
    try:
        with httpx.Client(base_url=base_url, timeout=ESTIMATE_TIMEOUT) as client:
            response = client.post(
                ESTIMATE_PATH,
                params={"prompt_version": form.prompt_version},
                json=form.payload(),
            )
    except httpx.ConnectError as exc:
        raise ApiUnreachable(type(exc).__name__) from exc
    except httpx.ConnectTimeout as exc:
        raise ApiUnreachable(type(exc).__name__) from exc
    except httpx.TimeoutException as exc:
        raise RequestFailed("timeout") from exc
    except httpx.TransportError as exc:
        raise RequestFailed("transport_error") from exc
    if response.status_code == 422:
        raise ValidationRejected(rejected_fields(response))
    if response.status_code != 200:
        raise RequestFailed("http_status", response.status_code)
    try:
        data = response.json()
    except ValueError as exc:
        raise RequestFailed("invalid_payload") from exc
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise RequestFailed("invalid_payload")
    return {"data": data, "client_total_s": time.perf_counter() - start}


def run_estimate(base_url: str, form: EstimateForm) -> None:
    """Call the API; store the result in session state or show a generic error."""
    try:
        with st.spinner("Generando la estimación..."):
            result = post_estimate(base_url, form)
    except ApiUnreachable as exc:
        logger.error("api_unreachable", error_type=str(exc))
        st.warning(api_unavailable_message(base_url))
        return
    except ValidationRejected as exc:
        logger.warning("api_request_failed", reason="http_status", status_code=422)
        names = ", ".join(f"`{name}`" for name in exc.fields) or "desconocidos"
        st.warning(f"Revisa los campos: {names}.")
        return
    except RequestFailed as exc:
        extra = {"status_code": exc.status_code} if exc.status_code is not None else {}
        logger.warning("api_request_failed", reason=exc.reason, **extra)
        st.error(PROVIDER_ERROR_MESSAGE)
        return
    st.session_state.last_result = result


def render_health(health: dict) -> None:
    fallback = health.get("fallback_model")
    fallback_text = "Sin respaldo" if fallback is None else f"`{fallback}`"
    cache_text = "disponible" if health.get("cache") == "ok" else "no disponible"
    st.markdown(
        f"**Modelo primario:** `{health.get('primary_model')}`  \n"
        f"**Modelo de respaldo:** {fallback_text}  \n"
        f"**Caché:** {cache_text}"
    )


def render_sidebar(base_url: str) -> None:
    health = fetch_json(base_url, "/health")
    with st.sidebar:
        if health is None:
            st.warning(api_unavailable_message(base_url))
        else:
            render_health(health)


def render_form() -> EstimateForm | None:
    """Paint the form; return the submitted values, or None if it was not submitted."""
    with st.form("estimation_form"):
        description = st.text_area(
            "Descripción del proyecto",
            max_chars=MAX_DESCRIPTION_CHARS,
            height=200,
            placeholder="Describe el proyecto: objetivo, funcionalidades, plazos, equipo...",
        )
        project_type = st.selectbox(
            "Tipo de proyecto",
            list(PROJECT_TYPE_LABELS),
            format_func=PROJECT_TYPE_LABELS.__getitem__,
        )
        detail_level = st.radio(
            "Nivel de detalle",
            list(DETAIL_LEVEL_LABELS),
            index=list(DETAIL_LEVEL_LABELS).index(DEFAULT_DETAIL_LEVEL),
            format_func=DETAIL_LEVEL_LABELS.__getitem__,
            horizontal=True,
        )
        output_format = st.selectbox(
            "Formato de salida",
            list(OUTPUT_FORMAT_LABELS),
            index=list(OUTPUT_FORMAT_LABELS).index(DEFAULT_OUTPUT_FORMAT),
            format_func=OUTPUT_FORMAT_LABELS.__getitem__,
        )
        with st.expander("Opciones avanzadas"):
            prompt_version = st.selectbox(
                "Versión del prompt",
                PROMPT_VERSIONS,
                index=PROMPT_VERSIONS.index(DEFAULT_PROMPT_VERSION),
            )
        submitted = st.form_submit_button("Estimar")
    if not submitted:
        return None
    return EstimateForm(
        description.strip(), project_type, detail_level, output_format, prompt_version
    )


def format_cost(cost_usd: float | None) -> str:
    return "no disponible" if cost_usd is None else f"${cost_usd:.6f}"


def format_score(validation: dict) -> str:
    total = int(validation.get("checks_total", 0))
    passed = round(float(validation.get("score", 0.0)) * total)
    return f"{passed}/{total}"


def render_metrics(data: dict, client_total_s: float) -> None:
    st.markdown(
        f"- Modelo: `{data.get('model')}`\n"
        f"- Proveedor: `{data.get('provider')}`\n"
        f"- Respaldo: {'sí' if data.get('fallback_used') else 'no'}\n"
        f"- Desde caché: {'sí' if data.get('cache_hit') else 'no'}\n"
        f"- Tokens de entrada: {data.get('input_tokens')}\n"
        f"- Tokens de salida: {data.get('output_tokens')}\n"
        f"- Coste estimado: {format_cost(data.get('cost_usd'))}\n"
        f"- Latencia del servidor: {data.get('latency_ms')} ms\n"
        f"- Tiempo total en el cliente: {client_total_s:.2f} s"
    )


def render_validation(validation: dict | None) -> None:
    st.subheader("Validación estructural")
    if validation is None:
        st.caption("Sin validación")
        return
    st.markdown(f"Comprobaciones superadas: **{format_score(validation)}**")
    for issue in validation.get("issues", []):
        st.warning(issue)
    with st.expander("Detalle de la validación"):
        st.json(validation)


def render_result(result: dict) -> None:
    data = result["data"]
    st.divider()
    st.markdown(data["text"])
    if data.get("truncated"):
        st.warning(TRUNCATED_WARNING)
    st.caption(f"Versión del prompt: {data.get('prompt_version')}")
    st.subheader("Métricas")
    render_metrics(data, result["client_total_s"])
    render_validation(data.get("validation"))


def main() -> None:
    st.set_page_config(page_title="Estimador de software", page_icon=":material/calculate:")
    st.title("Estimador de software")
    base_url = get_base_url()
    st.session_state.setdefault("last_result", None)
    render_sidebar(base_url)
    form = render_form()
    if form is not None:
        warning = validate_description(form.description)
        if warning:
            st.warning(warning)
        else:
            run_estimate(base_url, form)
    if st.session_state.last_result is not None:
        render_result(st.session_state.last_result)


main()
