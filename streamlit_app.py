"""Streamlit UI: conversational client of the estimation API (sessions, memory, attachments)."""

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
SESSIONS_PATH = "/api/v1/sessions"
GET_TIMEOUT = httpx.Timeout(3.0)
SESSION_ESTIMATE_TIMEOUT = httpx.Timeout(connect=3.0, read=180.0, write=30.0, pool=3.0)

# Own copies of the API enums and limits (a test compares them with `app`).
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
SESSION_PROMPT_VERSIONS = ("v4",)
DEFAULT_DETAIL_LEVEL = "medium"
DEFAULT_OUTPUT_FORMAT = "line_items"
DEFAULT_PROMPT_VERSION = "v4"
MAX_TRANSCRIPT_CHARS = 20_000
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ATTACHMENT_TYPES = ("pdf", "docx")
ATTACHMENT_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
TRANSCRIPT_PREVIEW_CHARS = 500

PROVIDER_ERROR_MESSAGE = "No se pudo generar la estimación. Inténtalo de nuevo en unos instantes."
TRUNCATED_WARNING = (
    "La estimación está incompleta: se cortó por el límite de longitud. "
    "Prueba con un nivel de detalle menor."
)
OUT_OF_SCOPE_NOTICE = (
    "Con la información disponible no se puede estimar este proyecto. "
    "Añade más detalle a la transcripción y vuelve a intentarlo."
)
SESSION_LOST_NOTICE = (
    "La conversación anterior ya no existe en el servidor (se reinició o caducó). Empieza de nuevo."
)
SESSION_LOST_ON_SEND_NOTICE = (
    "La conversación anterior se ha perdido en el servidor. Se ha creado una nueva: "
    "tu transcripción sigue en el formulario, vuelve a enviarla."
)
SESSION_BUSY_MESSAGE = "Ya hay una estimación en curso para esta conversación."
NO_METADATA_MESSAGE = "Aún no hay datos del proyecto"
GUARDRAIL_MESSAGES = {
    "prompt_injection": (
        "La transcripción contiene instrucciones dirigidas al asistente. Describe solo el proyecto."
    ),
    "pii": (
        "La transcripción contiene datos personales (email, teléfono o IBAN). "
        "Elimínalos y vuelve a intentarlo."
    ),
    "moderation": "El contenido de la transcripción no está permitido.",
}
ATTACHMENT_GUARDRAIL_MESSAGES = {
    "prompt_injection": "El adjunto {name} contiene instrucciones dirigidas al asistente.",
    "pii": "El adjunto {name} contiene datos personales (email, teléfono o IBAN).",
    "moderation": "El contenido del adjunto {name} no está permitido.",
}
ATTACHMENT_MESSAGES = {
    "too_many_files": f"Hay demasiados adjuntos (máximo {MAX_ATTACHMENTS}).",
    "file_too_large": "El adjunto {name} es demasiado grande (máximo 10 MiB).",
    "too_much_text": "El adjunto {name} tiene demasiado texto.",
    "unsupported_type": "El adjunto {name} no es un PDF ni un DOCX válido.",
    "unreadable": "No se ha podido leer el adjunto {name}.",
    "encrypted": "El adjunto {name} está protegido con contraseña.",
    "no_text": "El adjunto {name} no contiene texto (¿es un escaneo?).",
}
UNKNOWN_ATTACHMENT = "un adjunto"


class ApiUnreachable(Exception):
    """The API did not accept the connection."""


class SessionLost(Exception):
    """The API answered 404: the session no longer exists."""


class SessionBusy(Exception):
    """The API answered 409: a turn is already running in this session."""


class ValidationRejected(Exception):
    """The API answered 422; carries the rejected field names."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


class GuardrailRejected(Exception):
    """The API answered 400 with a guardrail reason."""

    def __init__(self, reason: str, field: str | None, index: int | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field
        self.index = index


class AttachmentRejected(Exception):
    """The API answered 413/415/422 with an attachment reason."""

    def __init__(self, reason: str, index: int | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.index = index


class RequestFailed(Exception):
    """The request did not end in a valid 200 response."""

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class TurnForm:
    transcript: str
    project_type: str
    detail_level: str
    output_format: str
    prompt_version: str
    uploads: list[Any]

    def fields(self) -> dict[str, str]:
        return {
            "transcript": self.transcript,
            "project_type": self.project_type,
            "detail_level": self.detail_level,
            "output_format": self.output_format,
        }

    def files(self) -> list[tuple[str, tuple[str, bytes, str]]]:
        return [("attachments", (f.name, f.getvalue(), mime_type(f.name))) for f in self.uploads]


def mime_type(filename: str) -> str:
    return ATTACHMENT_MIME.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")


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


def create_session(base_url: str) -> str | None:
    """POST a new session; return its id, or None if the API is unreachable or answers badly."""
    try:
        with httpx.Client(base_url=base_url, timeout=GET_TIMEOUT) as client:
            response = client.post(SESSIONS_PATH)
        session_id = response.json()["session_id"] if response.status_code == 201 else None
    except httpx.TransportError as exc:
        logger.error("api_unreachable", error_type=type(exc).__name__)
        return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.warning("api_request_failed", reason="invalid_payload")
        return None
    if not isinstance(session_id, str):
        logger.warning("api_request_failed", reason="invalid_payload")
        return None
    return session_id


def fetch_session_info(base_url: str, session_id: str) -> dict | None:
    """GET the session; None if unreachable. Raises SessionLost on 404."""
    try:
        with httpx.Client(base_url=base_url, timeout=GET_TIMEOUT) as client:
            response = client.get(f"{SESSIONS_PATH}/{session_id}")
        if response.status_code == 404:
            raise SessionLost
        data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def validate_submission(transcript: str, uploads: list[Any]) -> str | None:
    """Return a Spanish warning for an invalid (already trimmed) turn, else None."""
    if not transcript:
        return "La transcripción no puede estar vacía."
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        return (
            f"La transcripción no puede superar los {MAX_TRANSCRIPT_CHARS} caracteres "
            f"({len(transcript)})."
        )
    if len(uploads) > MAX_ATTACHMENTS:
        return f"Puedes adjuntar como máximo {MAX_ATTACHMENTS} ficheros ({len(uploads)})."
    for upload in uploads:
        if upload.size > MAX_ATTACHMENT_BYTES:
            mib = MAX_ATTACHMENT_BYTES // (1024 * 1024)
            return f"El adjunto {upload.name} supera el tamaño máximo de {mib} MiB."
    return None


def error_detail(response: httpx.Response) -> Any:
    """`detail` of an error body, or None if unreadable."""
    try:
        return response.json().get("detail")
    except (ValueError, AttributeError):
        return None


def rejected_fields(detail: Any) -> list[str]:
    """Field names from a 422 `detail` list (`loc` only, never `input`)."""
    try:
        names = [str(item["loc"][-1]) for item in detail if item.get("loc")]
    except (AttributeError, KeyError, TypeError):
        return []
    return list(dict.fromkeys(names))


def raise_for_error(response: httpx.Response) -> None:
    """Map a non-200 turn response to a domain exception."""
    status = response.status_code
    detail = error_detail(response)
    reason = detail.get("reason") if isinstance(detail, dict) else None
    index = detail.get("index") if isinstance(detail, dict) else None
    if status == 404:
        raise SessionLost
    if status == 409:
        raise SessionBusy
    if status == 400 and reason in GUARDRAIL_MESSAGES:
        raise GuardrailRejected(reason, detail.get("field"), index)
    if status in (413, 415, 422) and reason in ATTACHMENT_MESSAGES:
        raise AttachmentRejected(reason, index)
    if status == 422 and isinstance(detail, list):
        raise ValidationRejected(rejected_fields(detail))
    raise RequestFailed("http_status", status)


def post_turn(base_url: str, session_id: str, form: TurnForm) -> dict[str, Any]:
    """POST the turn as multipart; return the response JSON plus the client-side total time."""
    start = time.perf_counter()
    try:
        with httpx.Client(base_url=base_url, timeout=SESSION_ESTIMATE_TIMEOUT) as client:
            response = client.post(
                f"{SESSIONS_PATH}/{session_id}/estimate",
                params={"prompt_version": form.prompt_version},
                # Every field as a multipart part, so the request is multipart even without
                # attachments (httpx falls back to urlencoded when `files` is empty).
                files=[(key, (None, value)) for key, value in form.fields().items()] + form.files(),
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise ApiUnreachable(type(exc).__name__) from exc
    except httpx.TimeoutException as exc:
        raise RequestFailed("timeout") from exc
    except httpx.TransportError as exc:
        raise RequestFailed("transport_error") from exc
    if response.status_code != 200:
        raise_for_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise RequestFailed("invalid_payload") from exc
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise RequestFailed("invalid_payload")
    return {"data": data, "client_total_s": time.perf_counter() - start}


def attachment_name(names: list[str], index: int | None) -> str:
    if isinstance(index, int) and 0 <= index < len(names):
        return f"`{names[index]}`"
    return UNKNOWN_ATTACHMENT


def guardrail_message(exc: GuardrailRejected, names: list[str]) -> str:
    if exc.field == "attachments":
        template = ATTACHMENT_GUARDRAIL_MESSAGES[exc.reason]
        return template.format(name=attachment_name(names, exc.index))
    return GUARDRAIL_MESSAGES[exc.reason]


def reset_conversation(base_url: str) -> bool:
    """Start a new session and clear the conversation; False if the API is unreachable."""
    session_id = create_session(base_url)
    st.session_state.session_id = session_id
    st.session_state.turns = []
    st.session_state.uploader_key += 1
    return session_id is not None


def run_turn(base_url: str, form: TurnForm) -> None:
    """Send the turn; append it to the conversation or show a generic error."""
    names = [f.name for f in form.uploads]
    try:
        with st.spinner("Generando la estimación..."):
            result = post_turn(base_url, st.session_state.session_id, form)
    except ApiUnreachable as exc:
        logger.error("api_unreachable", error_type=str(exc))
        st.warning(api_unavailable_message(base_url))
        return
    except SessionLost:
        logger.warning("api_request_failed", reason="http_status", status_code=404)
        reset_conversation(base_url)
        st.session_state.notice = SESSION_LOST_ON_SEND_NOTICE
        st.rerun()
    except SessionBusy:
        logger.warning("api_request_failed", reason="http_status", status_code=409)
        st.warning(SESSION_BUSY_MESSAGE)
        return
    except ValidationRejected as exc:
        logger.warning("api_request_failed", reason="http_status", status_code=422)
        fields = ", ".join(f"`{name}`" for name in exc.fields) or "desconocidos"
        st.warning(f"Revisa los campos: {fields}.")
        return
    except GuardrailRejected as exc:
        logger.warning("api_request_failed", reason="guardrail", guardrail_reason=exc.reason)
        st.warning(guardrail_message(exc, names))
        return
    except AttachmentRejected as exc:
        logger.warning("api_request_failed", reason="http_status", status_code=422)
        template = ATTACHMENT_MESSAGES[exc.reason]
        st.warning(template.format(name=attachment_name(names, exc.index)))
        return
    except RequestFailed as exc:
        extra = {"status_code": exc.status_code} if exc.status_code is not None else {}
        logger.warning("api_request_failed", reason=exc.reason, **extra)
        st.error(PROVIDER_ERROR_MESSAGE)
        return
    st.session_state.turns.append({"transcript": form.transcript, "attachments": names, **result})
    st.session_state.uploader_key += 1
    st.session_state.transcript_key += 1
    st.rerun()


def render_health(health: dict) -> None:
    fallback = health.get("fallback_model")
    fallback_text = "Sin respaldo" if fallback is None else f"`{fallback}`"
    cache_text = "disponible" if health.get("cache") == "ok" else "no disponible"
    st.markdown(
        f"**Modelo primario:** `{health.get('primary_model')}`  \n"
        f"**Modelo de respaldo:** {fallback_text}  \n"
        f"**Caché:** {cache_text}"
    )


def render_memory(info: dict | None) -> None:
    st.subheader("Memoria del proyecto")
    if info is None:
        st.caption(NO_METADATA_MESSAGE)
        return
    metadata = info.get("project_metadata") or {}
    lines = []
    if metadata.get("project_name"):
        lines.append(f"**Proyecto:** {metadata['project_name']}")
    if metadata.get("assumed_team_size"):
        lines.append(f"**Equipo asumido:** {metadata['assumed_team_size']} personas")
    if metadata.get("mentioned_technologies"):
        lines.append("**Tecnologías:** " + ", ".join(metadata["mentioned_technologies"]))
    if metadata.get("agreed_scope"):
        lines.append(f"**Alcance acordado:** {metadata['agreed_scope']}")
    st.markdown("  \n".join(lines) if lines else NO_METADATA_MESSAGE)
    st.caption(
        f"Turnos: {info.get('turn_count', 0)} · en la ventana: "
        f"{info.get('history_turns', 0)}/{info.get('max_turns', 0)} · "
        f"sesión `{str(info.get('session_id', ''))[:8]}`"
    )


def render_sidebar(base_url: str, info: dict | None) -> None:
    health = fetch_json(base_url, "/health")
    with st.sidebar:
        if health is None:
            st.warning(api_unavailable_message(base_url))
        else:
            render_health(health)
        render_memory(info)
        if st.button("Nueva conversación"):
            reset_conversation(base_url)
            st.rerun()


def render_form() -> TurnForm | None:
    """Paint the form; return the submitted values, or None if it was not submitted."""
    with st.form("estimation_form"):
        transcript = st.text_area(
            "Transcripción",
            key=f"transcript-{st.session_state.transcript_key}",
            max_chars=MAX_TRANSCRIPT_CHARS,
            height=200,
            placeholder="Pega la transcripción o describe el proyecto o el cambio a estimar...",
        )
        project_type = st.selectbox(
            "Tipo de proyecto",
            list(PROJECT_TYPE_LABELS),
            format_func=PROJECT_TYPE_LABELS.__getitem__,
            key="project_type",
        )
        detail_level = st.radio(
            "Nivel de detalle",
            list(DETAIL_LEVEL_LABELS),
            index=list(DETAIL_LEVEL_LABELS).index(DEFAULT_DETAIL_LEVEL),
            format_func=DETAIL_LEVEL_LABELS.__getitem__,
            horizontal=True,
            key="detail_level",
        )
        output_format = st.selectbox(
            "Formato de salida",
            list(OUTPUT_FORMAT_LABELS),
            index=list(OUTPUT_FORMAT_LABELS).index(DEFAULT_OUTPUT_FORMAT),
            format_func=OUTPUT_FORMAT_LABELS.__getitem__,
            key="output_format",
        )
        uploads = st.file_uploader(
            "Adjuntos",
            type=list(ATTACHMENT_TYPES),
            accept_multiple_files=True,
            key=f"uploads-{st.session_state.uploader_key}",
        )
        with st.expander("Opciones avanzadas"):
            prompt_version = st.selectbox(
                "Versión del prompt",
                SESSION_PROMPT_VERSIONS,
                index=SESSION_PROMPT_VERSIONS.index(DEFAULT_PROMPT_VERSION),
            )
        submitted = st.form_submit_button("Enviar")
    if not submitted:
        return None
    return TurnForm(
        transcript.strip(),
        project_type,
        detail_level,
        output_format,
        prompt_version,
        list(uploads or []),
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


def render_user_turn(turn: dict) -> None:
    transcript = turn["transcript"]
    with st.chat_message("user"):
        st.markdown(transcript[:TRANSCRIPT_PREVIEW_CHARS])
        if len(transcript) > TRANSCRIPT_PREVIEW_CHARS:
            with st.expander("Ver transcripción completa"):
                st.markdown(transcript[TRANSCRIPT_PREVIEW_CHARS:])
        if turn["attachments"]:
            st.caption("Adjuntos: " + ", ".join(turn["attachments"]))


def render_assistant_turn(turn: dict) -> None:
    data = turn["data"]
    with st.chat_message("assistant"):
        result_info = data.get("result")
        if isinstance(result_info, dict) and result_info.get("out_of_scope") is True:
            st.info(OUT_OF_SCOPE_NOTICE)
        if data.get("truncated"):
            st.warning(TRUNCATED_WARNING)
        st.markdown(data["text"])
        with st.expander("Métricas y validación", expanded=False):
            st.caption(f"Versión del prompt: {data.get('prompt_version')}")
            render_metrics(data, turn["client_total_s"])
            render_validation(data.get("validation"))


def init_state() -> None:
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("turns", [])
    st.session_state.setdefault("uploader_key", 0)
    st.session_state.setdefault("transcript_key", 0)
    st.session_state.setdefault("notice", None)


def sync_session(base_url: str) -> dict | None:
    """Ensure a live session exists and return its info (None if unavailable)."""
    if st.session_state.session_id is None:
        st.session_state.session_id = create_session(base_url)
    session_id = st.session_state.session_id
    if session_id is None:
        return None
    try:
        return fetch_session_info(base_url, session_id)
    except SessionLost:
        reset_conversation(base_url)
        st.session_state.notice = SESSION_LOST_NOTICE
        new_id = st.session_state.session_id
        return fetch_session_info(base_url, new_id) if new_id else None


def main() -> None:
    st.set_page_config(page_title="Estimador de software", page_icon=":material/calculate:")
    st.title("Estimador de software")
    base_url = get_base_url()
    init_state()
    info = sync_session(base_url)
    render_sidebar(base_url, info)
    if st.session_state.notice:
        st.warning(st.session_state.notice)
        st.session_state.notice = None
    for turn in st.session_state.turns:
        render_user_turn(turn)
        render_assistant_turn(turn)
    if st.session_state.session_id is None:
        st.warning(api_unavailable_message(base_url))
        return
    form = render_form()
    if form is None:
        return
    warning = validate_submission(form.transcript, form.uploads)
    if warning:
        st.warning(warning)
    else:
        run_turn(base_url, form)


main()
