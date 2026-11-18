"""conversation-sessions / Endpoint de depuración de la sesión and app-config / Endpoints de
depuración por configuración (change s06-add-cag-stress-test). Offline.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.config import Settings, get_settings
from app.main import app
from app.sessions import Anchor, Turn
from tests.conftest import make_settings, turn_data

BASE = "/api/v1/sessions"


def debug_url(session_id: str) -> str:
    return f"{BASE}/{session_id}/debug"


@pytest.fixture
def make_rig(make_session_client):
    def factory(enabled: bool):
        client, provider, _, store = make_session_client(max_conversation_turns=1)
        # The per-request dependency reads the (overridden) settings.
        cfg = make_settings(debug_endpoints=enabled, max_conversation_turns=1)
        app.dependency_overrides[get_settings] = lambda: cfg
        return client, store

    return factory


def new_session(client) -> str:
    return client.post(BASE).json()["session_id"]


def test_debug_disabled_answers_404_and_is_not_in_openapi(make_rig):
    """Scenario: Depuración desactivada."""
    client, _ = make_rig(enabled=False)
    session_id = new_session(client)
    assert client.get(debug_url(session_id)).status_code == 404
    assert "/debug" not in client.get("/openapi.json").text


def test_debug_disabled_does_not_touch_the_store(make_rig, monkeypatch):
    """Requirement: 404 sin consultar el almacén."""
    client, store = make_rig(enabled=False)
    session_id = new_session(client)

    def boom(*args, **kwargs):
        raise AssertionError("store consulted")

    monkeypatch.setattr(store, "get", boom)
    assert client.get(debug_url(session_id)).status_code == 404


def test_debug_enabled_returns_summary_anchors_metadata_and_last_observation(make_rig):
    """Scenario: Depuración activa."""
    client, store = make_rig(enabled=True)
    session_id = new_session(client)
    for _ in range(2):
        assert client.post(f"{BASE}/{session_id}/estimate", data=turn_data()).status_code == 200
    history = store.get(session_id).history
    history.set_anchors([Anchor(Turn(1, "Presupuesto 30.000 […]", "ok"), ("budget",))])
    history.set_summary("Proyecto Nimbus con React", covered_turns=1)

    response = client.get(debug_url(session_id))
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["summary"].startswith("Proyecto Nimbus")
    assert body["anchors"] == ["Presupuesto 30.000 […]"]
    assert "project_name" in body["project_metadata"]
    assert body["last_observation"]["turn_index"] == 2
    assert len(body["last_observation"]) == 13


def test_debug_of_a_new_session_is_empty(make_rig):
    """Scenario: Sesión sin turnos."""
    client, _ = make_rig(enabled=True)
    response = client.get(debug_url(new_session(client)))
    assert response.status_code == 200
    body = response.json()
    assert (body["last_observation"], body["summary"], body["anchors"]) == (None, None, [])


def test_debug_malformed_id_is_422(make_rig):
    """Requirement: Errores (`session_id` mal formado)."""
    client, _ = make_rig(enabled=True)
    assert client.get(debug_url("not-a-uuid")).status_code == 422


def test_debug_unknown_session_is_404_session_not_found(make_rig):
    """Requirement: Errores (sesión inexistente)."""
    client, _ = make_rig(enabled=True)
    response = client.get(debug_url("3f2b8c1e-5a47-4d0e-9a52-0c7d1e6b9a10"))
    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == "session_not_found"


def test_debug_does_not_modify_the_session(make_rig):
    """Requirement: Restricciones (el endpoint no modifica la sesión)."""
    client, store = make_rig(enabled=True)
    session_id = new_session(client)
    client.post(f"{BASE}/{session_id}/estimate", data=turn_data())
    session = store.get(session_id)
    before = (session.turn_count, session.last_observation, session.history.summary)
    client.get(debug_url(session_id))
    after = (session.turn_count, session.last_observation, session.history.summary)
    assert before == after


def test_regular_endpoints_still_expose_no_summary_or_anchor_text(make_rig):
    """Requirement: Restricciones (los demás endpoints no exponen texto)."""
    client, store = make_rig(enabled=True)
    session_id = new_session(client)
    client.post(f"{BASE}/{session_id}/estimate", data=turn_data())
    store.get(session_id).history.set_summary("TEXTO-DEL-RESUMEN", covered_turns=1)
    assert "TEXTO-DEL-RESUMEN" not in client.get(f"{BASE}/{session_id}").text


# --- DEBUG_ENDPOINTS ---


def test_debug_endpoints_default_is_false(monkeypatch):
    """Scenario: Valor por defecto."""
    monkeypatch.delenv("DEBUG_ENDPOINTS", raising=False)
    assert Settings(_env_file=None, openai_api_key="sk-fake").debug_endpoints is False
    assert "/debug" not in TestClient(app).get("/openapi.json").text


def test_debug_endpoints_invalid_value_fails_naming_the_variable(monkeypatch):
    """Scenario: Valor inválido."""
    monkeypatch.setenv("DEBUG_ENDPOINTS", "quizas")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, openai_api_key="sk-fake")
    assert "debug_endpoints" in str(caught.value).lower()


def start_app(monkeypatch) -> list[dict]:
    monkeypatch.setattr("app.main.configure_logging", lambda settings: None)
    with capture_logs() as logs, TestClient(app):
        pass
    return logs


def test_startup_warns_when_debug_endpoints_are_enabled(monkeypatch):
    """Scenario: Activada (warning `debug_endpoints_enabled`, sin más datos)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    warnings = [e for e in start_app(monkeypatch) if e["event"] == "debug_endpoints_enabled"]
    assert len(warnings) == 1 and warnings[0]["log_level"] == "warning"
    assert set(warnings[0]) == {"event", "log_level"}


def test_startup_does_not_warn_by_default(monkeypatch):
    """Requirement: sin la variable no hay warning."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.delenv("DEBUG_ENDPOINTS", raising=False)
    assert [e for e in start_app(monkeypatch) if e["event"] == "debug_endpoints_enabled"] == []


def test_env_example_documents_the_variable_and_compose_does_not_enable_it():
    """Requirement: `.env.example` comentada con advertencia; compose no la activa."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    lines = (root / ".env.example").read_text().splitlines()
    assert "# DEBUG_ENDPOINTS=false" in lines
    assert any("production" in line and "DEBUG_ENDPOINTS" in line for line in lines)
    assert "DEBUG_ENDPOINTS" not in (root / "docker-compose.yml").read_text()
