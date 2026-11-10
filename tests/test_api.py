"""HTTP tests for `estimation-api`, `app-config` (fail fast) and OpenAPI docs."""

import os
import subprocess
import sys
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import app
from app.schemas import MAX_TRANSCRIPTION_CHARS
from app.services import llm_service
from app.services.cache import ResponseCache, get_response_cache
from app.services.llm_service import CachedProvider, EstimationService, LLMProviderError
from tests.conftest import CONFIG_ENV_VARS, FakeProvider, make_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
ESTIMATE = "/api/v1/estimate"
CONTRACT_FIELDS = {
    "estimation",
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
GENERATION_FIELDS = {
    "preprocessing",
    "extracted_requirements",
    "preprocessing_input_tokens",
    "preprocessing_output_tokens",
    "preprocessing_cost_usd",
    "validation",
}
RESPONSE_FIELDS = CONTRACT_FIELDS | GENERATION_FIELDS


# --- Requirement: Health check ---


def test_health_returns_ok_with_configured_models_and_no_llm_call(
    client, settings, provider, response_cache
):
    """Requirement: Health check / Scenario: Servicio levantado."""
    app.dependency_overrides[get_response_cache] = lambda: response_cache
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "env": "staging",
        "primary_model": "gpt-4o",
        "fallback_model": "claude-haiku-4-5",
        "cache": "ok",
    }
    assert provider.call_count == 0


def test_health_reports_cache_unavailable_when_redis_is_down(client, provider):
    """Requirement: Health check / Scenario: Redis caído."""
    server = fakeredis.FakeServer()
    server.connected = False
    down = ResponseCache(fakeredis.FakeRedis(server=server, decode_responses=True), ttl=60)
    app.dependency_overrides[get_response_cache] = lambda: down
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["cache"] == "unavailable"
    assert provider.call_count == 0


def test_health_reports_cache_unavailable_when_cache_is_disabled(client):
    """Requirement: Health check / Redis caído (REDIS_URL vacío: sin caché configurada)."""
    response = client.get("/health")  # REDIS_URL is empty in tests: no cache
    assert response.status_code == 200
    assert response.json()["cache"] == "unavailable"


@pytest.mark.parametrize(
    "overrides",
    [{}, {"fallback_model": ""}],
    ids=["no-fallback-key", "fallback-model-empty"],
)
def test_health_fallback_model_is_null_without_fallback(client, overrides):
    """Requirement: Health check / Scenario: Sin fallback."""
    no_fallback = make_settings(**overrides)  # no ANTHROPIC_API_KEY
    app.dependency_overrides[get_settings] = lambda: no_fallback
    body = client.get("/health").json()
    assert body["fallback_model"] is None
    assert body["primary_model"] == "gpt-4o-mini"


def test_health_reports_derived_fallback_for_anthropic_primary(client):
    """Requirement: Health check (fallback por defecto según el primario)."""
    derived = make_settings(
        primary_model="claude-haiku-4-5", anthropic_api_key="sk-ant-fake"
    )  # FALLBACK_MODEL unset -> gpt-4o-mini, OPENAI_API_KEY present
    app.dependency_overrides[get_settings] = lambda: derived
    body = client.get("/health").json()
    assert body["primary_model"] == "claude-haiku-4-5"
    assert body["fallback_model"] == "gpt-4o-mini"


# --- Requirement: Endpoint de estimación ---


def test_estimate_returns_full_contract_matching_settings(client, settings, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación correcta."""
    response = client.post(ESTIMATE, json={"transcription": "Necesitamos una web de reservas."})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESPONSE_FIELDS
    assert body["estimation"] == provider.result.text
    assert body["provider"] == settings.primary.provider == "openai"
    assert body["model"] == settings.primary.model == "gpt-4o"
    assert (body["input_tokens"], body["output_tokens"]) == (1000, 500)
    assert body["cost_usd"] is None  # no price for gpt-4o in the table
    assert body["truncated"] is False
    assert body["fallback_used"] is False
    assert body["cache_hit"] is False
    assert body["latency_ms"] >= 0
    assert body["preprocessing"] == "none"
    assert body["extracted_requirements"] is None
    assert (body["preprocessing_input_tokens"], body["preprocessing_output_tokens"]) == (0, 0)
    assert body["preprocessing_cost_usd"] is None
    assert body["validation"] is not None
    assert provider.call_count == 1
    assert provider.calls[0][1] == [{"role": "user", "content": "Necesitamos una web de reservas."}]


def test_estimate_served_by_the_fallback_reports_its_model_and_flag(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación servida por el respaldo."""
    provider.result = FakeProvider(
        model="claude-haiku-4-5", provider="anthropic", fallback_used=True
    ).result
    response = client.post(ESTIMATE, json={"transcription": "hola"})
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is True
    assert (body["model"], body["provider"]) == ("claude-haiku-4-5", "anthropic")
    assert isinstance(body["cost_usd"], float)


def test_estimate_served_from_cache_reports_cache_hit_without_calling_the_llm(
    client, response_cache
):
    """Requirement: Endpoint de estimación / Scenario: Estimación desde la caché."""
    inner = FakeProvider(text="estimación cacheada", model="gpt-4o-mini")
    cached = CachedProvider(inner, response_cache, "gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: cached
    first = client.post(ESTIMATE, json={"transcription": "misma transcripción"}).json()
    second = client.post(ESTIMATE, json={"transcription": "misma transcripción"}).json()
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["estimation"] == first["estimation"] == "estimación cacheada"
    assert second["latency_ms"] >= 0
    assert inner.call_count == 1


def test_estimate_stored_by_the_api_is_a_hit_for_the_chat_first_turn(client, response_cache):
    """Requirement: Caché en streaming / Scenario: Compartida entre modos (API y Streamlit)."""
    inner = FakeProvider(text="estimación", fragments=["estimación"], model="gpt-4o-mini")
    shared = CachedProvider(inner, response_cache, "gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: shared
    transcription = "Necesitamos una web"
    assert client.post(ESTIMATE, json={"transcription": transcription}).json()["cache_hit"] is False
    # What the Streamlit chat sends on its first turn, through the same service layer.
    stream = EstimationService(shared).stream_chat([{"role": "user", "content": transcription}])
    assert "".join(stream) == "estimación"
    assert stream.result.cache_hit is True
    assert inner.call_count == 1


def test_estimate_cost_is_a_number_for_known_model(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación correcta (coste conocido)."""
    provider.result = FakeProvider(model="gpt-4o-mini").result
    body = client.post(ESTIMATE, json={"transcription": "hola"}).json()
    assert isinstance(body["cost_usd"], float)


def test_estimate_truncated_true_when_provider_cuts_generation(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación truncada."""
    provider.result = FakeProvider(text="parcial", truncated=True).result
    response = client.post(ESTIMATE, json={"transcription": "hola"})
    assert response.status_code == 200
    body = response.json()
    assert body["estimation"] == "parcial"
    assert body["truncated"] is True


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
    properties = schemas["EstimationResponse"]["properties"]
    assert set(properties) == RESPONSE_FIELDS
    assert properties["truncated"]["type"] == "boolean"
    for field in ("fallback_used", "cache_hit"):
        assert properties[field]["type"] == "boolean"
    assert properties["latency_ms"]["type"] == "integer"
    assert properties["provider"]["type"] == "string"  # a free string: no longer an enum
    assert set(schemas["EstimationResponse"]["required"]) >= RESPONSE_FIELDS
    health = schemas["HealthResponse"]["properties"]
    assert {"status", "env", "primary_model", "fallback_model", "cache"} == set(health)
    assert spec["info"]["title"]
    assert spec["info"]["description"]


def test_docs_is_served(client):
    """Requirement: Documentación OpenAPI / Scenario: Swagger accesible."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


# --- Requirement: Fail fast (app-config) ---


def test_startup_fails_fast_without_primary_key():
    """Requirement: app-config / Scenario: El servicio no arranca y avisa (sin key)."""
    with pytest.raises(RuntimeError) as exc_info, TestClient(app):
        pass
    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "PRIMARY_MODEL" in message
    assert ".env" in message


def test_startup_error_names_variable_and_leaks_no_key_value(monkeypatch):
    """Requirement: app-config / Scenario: El servicio no arranca y avisa (sin valores)."""
    monkeypatch.setenv("PRIMARY_MODEL", "claude-haiku-4-5")
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


def test_startup_without_redis_still_serves_requests(monkeypatch):
    """Requirement: Degradación sin Redis / Scenario: Arranque sin Redis.

    The Redis client is lazy: startup never connects (the network guard would fail the test).
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200  # no Redis call on this path


def test_startup_succeeds_with_key_and_does_not_call_llm(monkeypatch):
    """Requirement: Arranque estándar (con configuración válida el lifespan no falla)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200  # REDIS_URL empty: no cache, no Redis


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
