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
from app.prompts.loader import render_estimation_prompt
from app.services import llm_service
from app.services.cache import ResponseCache, get_response_cache
from app.services.llm_service import CachedProvider, LLMProviderError
from tests.conftest import (
    CONFIG_ENV_VARS,
    FakeProvider,
    example_estimates,
    make_input,
    make_settings,
    valid_body,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ESTIMATE = "/api/v1/estimate"
RESPONSE_FIELDS = {
    "text",
    "prompt_version",
    "model",
    "provider",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "truncated",
    "fallback_used",
    "cache_hit",
    "latency_ms",
    "validation",
}
EXAMPLE_TEXT = example_estimates("line_items")[0]


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
    response = client.post(ESTIMATE, json=valid_body())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESPONSE_FIELDS
    assert body["text"] == provider.result.text
    assert body["prompt_version"] == "v1"
    assert body["provider"] == settings.primary.provider == "openai"
    assert body["model"] == settings.primary.model == "gpt-4o"
    assert (body["input_tokens"], body["output_tokens"]) == (1000, 500)
    assert body["cost_usd"] is None  # no price for gpt-4o in the table
    assert body["truncated"] is False
    assert body["fallback_used"] is False
    assert body["cache_hit"] is False
    assert body["latency_ms"] >= 0
    assert body["validation"] is not None
    assert provider.call_count == 1


def test_estimate_sends_system_and_one_user_message_unconcatenated(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Mensajes separados."""
    description = "Descripción única QQ-77 de una plataforma de reservas con pagos"
    body = valid_body(description=f"  {description}  \n", output_format="phases_table")
    assert client.post(ESTIMATE, json=body).status_code == 200
    system, messages = provider.calls[0]
    expected_system, expected_user = render_estimation_prompt(
        make_input(description=description, output_format="phases_table"), "v1"
    )
    assert system == expected_system
    assert messages == [{"role": "user", "content": expected_user}]
    assert f"<project_description>\n{description}\n</project_description>" in expected_user
    assert description not in system


def test_estimate_with_prompt_version_v2_uses_the_v2_system_prompt(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Versión del prompt."""
    response = client.post(f"{ESTIMATE}?prompt_version=v2", json=valid_body())
    assert response.status_code == 200
    assert response.json()["prompt_version"] == "v2"
    assert provider.calls[0][0] == render_estimation_prompt(make_input(), "v2")[0]
    assert provider.calls[0][0] != render_estimation_prompt(make_input(), "v1")[0]


def test_estimate_default_prompt_version_is_v1(client, provider):
    """Requirement: Endpoint de estimación (query `prompt_version` por defecto v1)."""
    client.post(ESTIMATE, json=valid_body())
    assert provider.calls[0][0] == render_estimation_prompt(make_input(), "v1")[0]


@pytest.mark.parametrize("version", ["v9", "V1", "", "../v1"])
def test_estimate_unknown_prompt_version_is_422_on_the_query_without_llm_call(
    client, provider, version
):
    """Requirement: Endpoint de estimación / Scenario: Versión desconocida."""
    response = client.post(ESTIMATE, params={"prompt_version": version}, json=valid_body())
    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["query", "prompt_version"]]
    assert provider.call_count == 0


def test_estimate_served_by_the_fallback_reports_its_model_and_flag(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación servida por el respaldo."""
    provider.result = FakeProvider(
        model="claude-haiku-4-5", provider="anthropic", fallback_used=True
    ).result
    response = client.post(ESTIMATE, json=valid_body())
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_used"] is True
    assert (body["model"], body["provider"]) == ("claude-haiku-4-5", "anthropic")
    assert isinstance(body["cost_usd"], float)


def test_estimate_served_from_cache_reports_cache_hit_without_calling_the_llm(
    client, response_cache
):
    """Requirement: Endpoint de estimación / Scenario: Estimación desde la caché."""
    inner = FakeProvider(text=EXAMPLE_TEXT, model="gpt-4o-mini")
    cached = CachedProvider(inner, response_cache, "gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: cached
    first = client.post(f"{ESTIMATE}?prompt_version=v2", json=valid_body()).json()
    second = client.post(f"{ESTIMATE}?prompt_version=v2", json=valid_body()).json()
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["text"] == first["text"] == EXAMPLE_TEXT
    assert second["prompt_version"] == "v2"
    assert second["validation"]["score"] == 1.0  # computed on the cached text
    assert second["latency_ms"] >= 0
    assert inner.call_count == 1


def test_estimate_with_another_prompt_version_is_not_a_cache_hit(client, response_cache):
    """Requirement: Caché / Scenario: Opciones distintas (`prompt_version=v2`)."""
    inner = FakeProvider(model="gpt-4o-mini")
    cached = CachedProvider(inner, response_cache, "gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: cached
    client.post(ESTIMATE, json=valid_body())
    other = client.post(f"{ESTIMATE}?prompt_version=v2", json=valid_body()).json()
    assert other["cache_hit"] is False
    assert inner.call_count == 2


def test_estimate_cost_is_a_number_for_known_model(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación correcta (coste conocido)."""
    provider.result = FakeProvider(model="gpt-4o-mini").result
    body = client.post(ESTIMATE, json=valid_body()).json()
    assert isinstance(body["cost_usd"], float)


def test_estimate_truncated_true_when_provider_cuts_generation(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Estimación truncada."""
    provider.result = FakeProvider(text="parcial", truncated=True).result
    response = client.post(ESTIMATE, json=valid_body())
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "parcial"
    assert body["truncated"] is True
    assert body["validation"]["finish_ok"] is False


@pytest.mark.parametrize(
    "description",
    [None, "", "   \n\t ", "a" * 19, " " + "a" * 19 + " ", "a" * 2001, " " + "a" * 2001],
    ids=["missing", "empty", "whitespace", "19", "19-padded", "2001", "2001-padded"],
)
def test_estimate_rejects_description_out_of_limits(client, provider, description):
    """Requirement: Endpoint de estimación / Scenario: Descripción fuera de límites."""
    body = valid_body()
    if description is None:
        del body["description"]
    else:
        body["description"] = description
    response = client.post(ESTIMATE, json=body)
    assert response.status_code == 422
    assert ["body", "description"] in [e["loc"] for e in response.json()["detail"]]
    assert provider.call_count == 0


@pytest.mark.parametrize("length", [20, 2000])
def test_estimate_accepts_description_at_the_exact_limits_after_trimming(client, provider, length):
    """Requirement: Endpoint de estimación (límites 20 y 2000 inclusivos, tras recortar)."""
    body = valid_body(description="  " + "a" * length + "\n")
    assert client.post(ESTIMATE, json=body).status_code == 200
    assert provider.call_count == 1


def test_estimate_rejects_non_json_body(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Descripción fuera de límites (sin JSON)."""
    response = client.post(ESTIMATE, content="plain text")
    assert response.status_code == 422
    assert provider.call_count == 0


@pytest.mark.parametrize("field", ["project_type", "detail_level", "output_format"])
@pytest.mark.parametrize("value", [None, "MOBILE_APP", "unknown", ""], ids=lambda v: str(v))
def test_estimate_rejects_missing_or_invalid_enums(client, provider, field, value):
    """Requirement: Endpoint de estimación / Scenario: Enum inválido o ausente."""
    body = valid_body()
    if value is None:
        del body[field]
    else:
        body[field] = value
    response = client.post(ESTIMATE, json=body)
    assert response.status_code == 422
    assert ["body", field] in [e["loc"] for e in response.json()["detail"]]
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "reference_projects",
    [
        [{"name": f"P{i}", "summary": "Resumen"} for i in range(6)],
        [{"summary": "Resumen sin nombre"}],
        [{"name": "P", "summary": "s" * 501}],
        [{"name": "P", "summary": "Resumen", "client": "campo no definido"}],
        [{"name": "n" * 101, "summary": "Resumen"}],
        [{"name": "P", "summary": "Resumen", "actual_hours": 0}],
        [{"name": "P", "summary": "Resumen", "actual_hours": 20001}],
        [{"name": "P", "summary": "Resumen", "duration_weeks": 105}],
        [{"name": "  ", "summary": "Resumen"}],
    ],
    ids=[
        "six-items",
        "no-name",
        "long-summary",
        "extra-field",
        "long-name",
        "hours-0",
        "hours-20001",
        "weeks-105",
        "blank-name",
    ],
)
def test_estimate_rejects_invalid_reference_projects(client, provider, reference_projects):
    """Requirement: Endpoint de estimación / Scenario: Proyectos de referencia inválidos."""
    response = client.post(ESTIMATE, json=valid_body(reference_projects=reference_projects))
    assert response.status_code == 422
    assert all(e["loc"][:2] == ["body", "reference_projects"] for e in response.json()["detail"])
    assert provider.call_count == 0


def test_estimate_accepts_five_reference_projects_and_puts_them_in_the_user_message(
    client, provider
):
    """Requirement: Endpoint de estimación (reference_projects hasta 5) / Mensaje del usuario."""
    projects = [
        {
            "name": f"Ref-{i}",
            "summary": f"Resumen {i}",
            "actual_hours": 100 + i,
            "duration_weeks": i + 1,
        }
        for i in range(5)
    ]
    assert client.post(ESTIMATE, json=valid_body(reference_projects=projects)).status_code == 200
    system, messages = provider.calls[0]
    assert messages[0]["content"].count("<reference_project>") == 5
    assert "Ref-0" not in system


@pytest.mark.parametrize("references", [None, []], ids=["null", "empty"])
def test_estimate_without_reference_projects_has_no_block(client, provider, references):
    """Requirement: Endpoint de estimación (`[]` equivale a `null`)."""
    assert client.post(ESTIMATE, json=valid_body(reference_projects=references)).status_code == 200
    assert "<reference_projects>" not in provider.calls[0][1][0]["content"]


def test_estimate_unknown_top_level_fields_are_ignored(client, provider):
    """Requirement: Endpoint de estimación (campos no definidos de primer nivel se ignoran)."""
    body = valid_body(transcription="viejo", foo="bar")
    response = client.post(ESTIMATE, json=body)
    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_FIELDS


def test_estimate_invalid_parameter_is_422_with_the_field_and_no_llm_call(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Parámetro inválido."""
    response = client.post(ESTIMATE, json=valid_body(max_tokens=0))
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "max_tokens"]
    assert provider.call_count == 0


def test_estimate_provider_failure_returns_generic_502(client, provider):
    """Requirement: Endpoint de estimación / Scenario: Fallo del proveedor."""
    secret = "sk-secret-detail"
    provider.error = LLMProviderError(provider="openai")
    provider.error.__cause__ = RuntimeError(secret)
    response = client.post(ESTIMATE, json=valid_body())
    assert response.status_code == 502
    assert response.json() == {"detail": "LLM provider error"}
    assert "Traceback" not in response.text
    assert secret not in response.text


def test_the_retired_endpoints_are_gone(client):
    """Requirement (REMOVED): Endpoint de estimación en streaming y Contexto CAG de solo lectura."""
    stream = client.post(f"{ESTIMATE}/stream", json={"messages": []})
    assert stream.status_code in (404, 405)
    assert client.get("/api/v1/context").status_code == 404


# --- Requirements: Endpoint (502 documentado) y Documentación OpenAPI ---


def test_openapi_documents_route_schemas_and_responses(client):
    """Requirement: Endpoint de estimación / Scenario: Errores documentados."""
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert set(paths) == {ESTIMATE, "/health"}  # no /context, no /estimate/stream
    operation = paths[ESTIMATE]["post"]
    assert {"200", "422", "502"} <= set(operation["responses"])
    query = {p["name"]: p for p in operation["parameters"]}
    assert query["prompt_version"]["in"] == "query"
    assert query["prompt_version"]["schema"]["default"] == "v1"
    assert set(query["prompt_version"]["schema"]["enum"]) == {"v1", "v2"}

    schemas = spec["components"]["schemas"]
    assert {"EstimationRequest", "EstimationResponse", "ReferenceProject"} <= set(schemas)
    assert set(schemas["ProjectType"]["enum"]) == {
        "mobile_app",
        "web_saas",
        "internal_tool",
        "data_pipeline",
    }
    assert set(schemas["DetailLevel"]["enum"]) == {"summary", "medium", "detailed"}
    assert set(schemas["OutputFormat"]["enum"]) == {"phases_table", "line_items", "narrative"}

    request_schema = schemas["EstimationRequest"]
    assert set(request_schema["required"]) == {
        "description",
        "project_type",
        "detail_level",
        "output_format",
    }
    properties = request_schema["properties"]
    assert {"reference_projects", "num_examples", "use_examples", "model", "max_tokens"} <= set(
        properties
    )
    assert (properties["description"]["minLength"], properties["description"]["maxLength"]) == (
        20,
        2000,
    )
    reference = schemas["ReferenceProject"]
    assert reference["additionalProperties"] is False
    assert reference["properties"]["name"]["maxLength"] == 100

    properties = schemas["EstimationResponse"]["properties"]
    assert set(properties) == RESPONSE_FIELDS
    assert properties["truncated"]["type"] == "boolean"
    for field in ("fallback_used", "cache_hit"):
        assert properties[field]["type"] == "boolean"
    assert properties["latency_ms"]["type"] == "integer"
    assert properties["provider"]["type"] == "string"
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
