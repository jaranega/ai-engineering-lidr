"""HTTP tests for `estimation-api`, `app-config` (fail fast) and OpenAPI docs."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app
from app.schemas import MAX_TRANSCRIPTION_CHARS
from app.services.llm_service import LLMProviderError
from tests.conftest import CONFIG_ENV_VARS, FakeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
ESTIMATE = "/api/v1/estimate"
CONTRACT_FIELDS = {"estimation", "model", "provider", "input_tokens", "output_tokens", "cost_usd"}


# --- Requirement: Health check ---


def test_health_returns_ok_with_effective_config_and_no_llm_call(client, settings, provider):
    """Requirement: Health check / Scenario: Servicio levantado."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "env": settings.app_env,
        "provider": settings.llm_provider,
        "model": settings.effective_model,
    }
    assert response.json()["env"] == "staging"
    assert provider.call_count == 0


# --- Requirement: Endpoint de estimación ---


def test_estimate_returns_full_contract_matching_settings(client, settings, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación correcta."""
    response = client.post(ESTIMATE, json={"transcription": "Necesitamos una web de reservas."})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == CONTRACT_FIELDS
    assert body["estimation"] == provider.result.text
    assert body["provider"] == settings.llm_provider
    assert body["model"] == settings.effective_model == "custom-model-x"
    assert (body["input_tokens"], body["output_tokens"]) == (1000, 500)
    assert body["cost_usd"] is None  # unknown model: no price
    assert provider.call_count == 1
    assert provider.calls[0][1] == [{"role": "user", "content": "Necesitamos una web de reservas."}]


def test_estimate_cost_is_a_number_for_known_model(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación correcta (coste conocido)."""
    provider.result = FakeProvider(model="gpt-4o-mini").result
    body = client.post(ESTIMATE, json={"transcription": "hola"}).json()
    assert isinstance(body["cost_usd"], float)


@pytest.mark.parametrize(
    "payload",
    [{}, {"other": "x"}, {"transcription": ""}, {"transcription": "   \n\t "}],
    ids=["missing-body-field", "unknown-field-only", "empty", "whitespace-only"],
)
def test_estimate_rejects_missing_or_blank_transcription(client, provider, payload):
    """Requirement: Endpoint de estimación / Scenario: Transcripción ausente o vacía."""
    response = client.post(ESTIMATE, json=payload)
    assert response.status_code == 422
    assert provider.call_count == 0


def test_estimate_rejects_non_json_body(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Transcripción ausente o vacía (sin JSON)."""
    response = client.post(ESTIMATE, content="plain text")
    assert response.status_code == 422
    assert provider.call_count == 0


def test_estimate_rejects_transcription_over_limit(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Transcripción demasiado larga."""
    response = client.post(ESTIMATE, json={"transcription": "a" * (MAX_TRANSCRIPTION_CHARS + 1)})
    assert response.status_code == 422
    assert provider.call_count == 0


def test_estimate_accepts_transcription_at_exact_limit(client, provider):
    """Requirement: Endpoint de estimación / límite de 20 000 caracteres inclusive."""
    assert MAX_TRANSCRIPTION_CHARS == 20_000
    response = client.post(ESTIMATE, json={"transcription": "a" * 20_000})
    assert response.status_code == 200
    assert provider.call_count == 1


def test_estimate_provider_failure_returns_generic_502(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Fallo del proveedor."""
    secret = "sk-secret-detail"
    provider.error = LLMProviderError(provider="openai")
    provider.error.__cause__ = RuntimeError(secret)
    response = client.post(ESTIMATE, json={"transcription": "hola"})
    assert response.status_code == 502
    assert response.json() == {"detail": "LLM provider error"}
    assert "Traceback" not in response.text
    assert secret not in response.text


# --- Requirements: Endpoint (502 documentado) and Documentación OpenAPI ---


def test_openapi_documents_route_schemas_and_responses(client):
    """Requirement: Endpoint / Scenario: Error 502 documentado; Documentación OpenAPI."""
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][ESTIMATE]["post"]
    assert {"200", "422", "502"} <= set(operation["responses"])
    schemas = spec["components"]["schemas"]
    assert {"EstimationRequest", "EstimationResponse"} <= set(schemas)
    assert set(schemas["EstimationResponse"]["properties"]) == CONTRACT_FIELDS
    assert spec["info"]["title"]
    assert spec["info"]["description"]


def test_docs_is_served(client):
    """Requirement: Documentación OpenAPI / Scenario: Swagger accesible."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


# --- Requirement: Fail fast (app-config) ---


def test_startup_fails_fast_without_active_provider_key():
    """Requirement: app-config / Scenario: El servicio no arranca y avisa (sin key)."""
    with pytest.raises(RuntimeError) as exc_info, TestClient(app):
        pass
    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert ".env" in message


def test_startup_error_names_variable_and_leaks_no_key_value(monkeypatch):
    """Requirement: app-config / Scenario: El servicio no arranca y avisa (sin valores)."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-visible-123")
    with pytest.raises(RuntimeError) as exc_info, TestClient(app):
        pass
    message = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "sk-visible-123" not in message
    assert "sk-visible-123" not in repr(exc_info.value.__cause__)
    assert exc_info.value.__suppress_context__


def test_startup_with_blank_key_also_fails(monkeypatch):
    """Requirement: app-config / key vacía equivale a ausente."""
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"), TestClient(app):
        pass


def test_startup_succeeds_with_key_and_does_not_call_llm(monkeypatch):
    """Requirement: Arranque estándar (con configuración válida el lifespan no falla)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_importing_app_main_without_keys_does_not_fail(tmp_path):
    """Requirement: app-config / Scenario: Importar la aplicación no requiere configuración."""
    env = {k: v for k, v in os.environ.items() if k not in CONFIG_ENV_VARS}
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=tmp_path,  # empty dir: no .env can be picked up
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_isolation_relies_on_cwd_being_clean(tmp_path):
    """Isolation guard (task 6.3): `.env` is resolved from the cwd, which tests keep empty.

    Writing a `.env` in the (tmp) cwd shows it would be read, so the autouse chdir is what
    keeps the developer's real `.env` out of tests that call `get_settings()` directly.
    """
    assert Path.cwd() == tmp_path
    assert not (tmp_path / ".env").exists()
    with pytest.raises(ValidationError):
        Settings()
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv\n")
    assert Settings().openai_api_key is not None
