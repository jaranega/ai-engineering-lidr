"""HTTP and service tests for the per-request generation options (spec: generation-options,
estimation-api and cag-prompt deltas of `s02-add-generation-knobs-and-evaluation`)."""

import pytest
from structlog.testing import capture_logs

from app.config import Deployment, get_settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.main import app
from app.services import llm_service
from app.services.llm_service import (
    EXTRACTION_MAX_TOKENS,
    EXTRACTION_SYSTEM_PROMPT,
    CachedProvider,
    EstimationService,
    GenerationOptions,
    InvalidOptionError,
    LLMCallOptions,
    LLMProviderError,
    LLMResult,
)
from app.services.pricing import estimate_cost
from tests.conftest import FakeProvider, make_settings

ESTIMATE = "/api/v1/estimate"
STREAM = "/api/v1/estimate/stream"
T = "Cliente: queremos una web de reservas. TRANSCRIPCION-ZZ-1"
HAIKU = Deployment("claude-haiku-4-5", "anthropic")
CLEANING_HEADING = "## Limpieza de la transcripción"


def post(client, **options):
    return client.post(ESTIMATE, json={"transcription": T, **options})


def call_options(provider: FakeProvider, index: int = 0) -> LLMCallOptions:
    """Options the provider received, with `None` normalized to the defaults."""
    return provider.options[index] or LLMCallOptions()


def llm_result(text: str, **kwargs) -> LLMResult:
    return FakeProvider(text=text, **kwargs).result


# --- Requirement: Parámetros de generación por petición ---


def test_request_without_parameters_uses_the_defaults(client, provider):
    """Requirement: Parámetros de generación / Scenario: Petición sin parámetros."""
    response = post(client)
    assert response.status_code == 200
    system, messages = provider.calls[0]
    assert system == llm_service._system_prompt() == llm_service._system_prompt(3, "markdown")
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] in system
        assert example["estimation"] in system
    assert messages == [{"role": "user", "content": T}]
    options = call_options(provider)
    assert options == LLMCallOptions()
    assert (options.model, options.max_tokens, options.thinking_budget) == (None, 2048, None)
    body = response.json()
    assert body["preprocessing"] == "none"
    assert body["validation"] is not None
    assert provider.call_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("num_examples", 4),
        ("num_examples", -1),
        ("max_tokens", 0),
        ("max_tokens", 16385),
        ("thinking_budget", 500),
        ("thinking_budget", 1023),
        ("thinking_budget", 16385),
        ("preprocessing", "otro"),
        ("example_format", "xml"),
        ("model", ""),
        ("model", "m" * 101),
        ("evaluate", "quizá"),
    ],
)
def test_out_of_range_value_is_422_naming_the_field_without_calling_the_llm(
    client, provider, field, value
):
    """Requirement: Parámetros de generación / Scenario: Valor fuera de rango."""
    response = post(client, **{field: value})
    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["body", field]]
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "options",
    [
        {"num_examples": 0},
        {"num_examples": 3},
        {"max_tokens": 1},
        {"max_tokens": 16384},
        {"thinking_budget": 1024},
        {"thinking_budget": 16384},
        {"evaluate": False},
    ],
)
def test_limits_are_inclusive(client, provider, options):
    """Requirement: Parámetros de generación (rangos 0..3, 1..16384 y 1024..16384 inclusivos)."""
    assert post(client, **options).status_code == 200
    assert provider.call_count == 1


@pytest.mark.parametrize(
    "options",
    [{"use_examples": False}, {"num_examples": 0}, {"use_examples": False, "num_examples": 2}],
    ids=["use_examples-false", "num_examples-0", "use_examples-false-ignores-num_examples"],
)
def test_no_examples_in_the_prompt(client, provider, options):
    """Requirement: Parámetros de generación / Scenario: Sin ejemplos."""
    assert post(client, **options).status_code == 200
    system = provider.calls[0][0]
    assert "<examples>" not in system
    assert "Cómo usar las estimaciones de referencia" not in system
    assert system.startswith("Write your entire answer")
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] not in system


def test_num_examples_one_includes_only_the_first_example(client, provider):
    """Requirement: Parámetros de generación / Scenario: Número de ejemplos."""
    assert post(client, num_examples=1).status_code == 200
    system = provider.calls[0][0]
    assert ESTIMATION_EXAMPLES[0]["meeting_summary"] in system
    for example in ESTIMATION_EXAMPLES[1:]:
        assert example["meeting_summary"] not in system


@pytest.mark.parametrize("fmt", ["json", "narrative"])
def test_example_format_changes_the_prompt(client, provider, fmt):
    """Requirement: Formatos de los ejemplos (selección por petición)."""
    assert post(client, example_format=fmt).status_code == 200
    assert provider.calls[0][0] == llm_service._system_prompt(3, fmt)
    assert provider.calls[0][0] != llm_service._system_prompt()


def test_max_tokens_reaches_the_provider_and_a_cut_generation_is_truncated(client, provider):
    """Requirement: Límite de salida por petición / Scenario: Límite pequeño."""
    provider.result = llm_result("parcial", truncated=True)
    response = post(client, max_tokens=50)
    assert response.status_code == 200
    assert call_options(provider).max_tokens == 50
    body = response.json()
    assert body["truncated"] is True
    assert body["validation"]["finish_ok"] is False


def test_thinking_budget_reaches_the_provider_options(client, provider):
    """Requirement: Razonamiento extendido (el presupuesto llega al proveedor)."""
    assert post(client, thinking_budget=2048, max_tokens=2048).status_code == 200
    options = call_options(provider)
    assert (options.thinking_budget, options.max_tokens) == (2048, 2048)


# --- Requirement: Sobrescritura del modelo por petición ---


def test_model_override_from_another_provider(client, settings, provider):
    """Requirement: Sobrescritura del modelo / Scenario: Modelo de otro proveedor."""
    assert settings.primary.model == "gpt-4o"  # the override differs from the primary
    provider.result = llm_result("ok", model="claude-haiku-4-5", provider="anthropic")
    response = post(client, model="claude-haiku-4-5")
    assert response.status_code == 200
    assert call_options(provider).model == HAIKU
    body = response.json()
    assert (body["model"], body["provider"], body["fallback_used"]) == (
        "claude-haiku-4-5",
        "anthropic",
        False,
    )


def test_model_override_without_key_is_422_with_the_exact_pydantic_shape(client, provider):
    """Requirement: Sobrescritura del modelo / Scenario: Modelo sin key."""
    app.dependency_overrides[get_settings] = lambda: make_settings()  # no ANTHROPIC_API_KEY
    response = post(client, model="claude-haiku-4-5")
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "loc": ["body", "model"],
                "msg": "model 'claude-haiku-4-5' requires ANTHROPIC_API_KEY, which is not defined.",
                "type": "value_error",
            }
        ]
    }
    assert provider.call_count == 0


def test_model_override_error_never_contains_key_values(client, provider):
    """Requirement: Sobrescritura del modelo (mensaje sin valores de keys)."""
    app.dependency_overrides[get_settings] = lambda: make_settings(
        openai_api_key="sk-secret-openai-value"
    )
    for model in ("claude-haiku-4-5", "gemini-2.0-flash"):
        response = post(client, model=model)
        assert response.status_code == 422
        assert "sk-secret-openai-value" not in response.text
    assert provider.call_count == 0


def test_unsupported_model_is_422_naming_the_supported_providers(client, provider):
    """Requirement: Sobrescritura del modelo / Scenario: Modelo no soportado."""
    response = post(client, model="gemini-2.0-flash")
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert [error["loc"] for error in errors] == [["body", "model"]]
    assert "openai" in errors[0]["msg"]
    assert "anthropic" in errors[0]["msg"]
    assert provider.call_count == 0


def test_unrecognized_model_name_is_422(client, provider):
    """Requirement: Sobrescritura del modelo (modelo que no se reconoce)."""
    response = post(client, model="not-a-real-model")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "model"]
    assert provider.call_count == 0


def test_override_failure_is_a_generic_502_without_a_second_call(client, provider):
    """Requirement: Sobrescritura del modelo / Scenario: Override sin fallback (nivel API).

    The absence of a fallback call is proven against the real provider in `test_llm_provider`.
    """
    provider.error = LLMProviderError(provider="anthropic")
    response = post(client, model="claude-haiku-4-5")
    assert response.status_code == 502
    assert response.json() == {"detail": "LLM provider error"}
    assert provider.call_count == 1
    assert call_options(provider).model == HAIKU


# --- Requirement: Preprocesado de la transcripción ---


def test_inline_cleaning_block_and_unmodified_transcription(client, provider):
    """Requirement: Preprocesado / Scenario: Limpieza en línea."""
    response = post(client, preprocessing="inline_cleaning")
    assert response.status_code == 200
    system, messages = provider.calls[0]
    assert system == llm_service._system_prompt(3, "markdown", True)
    assert system.index("Eres un consultor") < system.index(CLEANING_HEADING)
    assert system.index(CLEANING_HEADING) < system.index("## Formato de la respuesta")
    assert messages == [{"role": "user", "content": T}]
    assert response.json()["preprocessing"] == "inline_cleaning"
    assert provider.call_count == 1


def two_phase_provider(
    extraction: LLMResult | LLMProviderError, estimation: LLMResult | None = None
):
    return FakeProvider(responses=[extraction, estimation or llm_result("## Estimación: x")])


def test_two_phase_makes_two_calls_and_exposes_the_extraction(client):
    """Requirement: Preprocesado / Scenario: Dos fases; Estimación en dos fases."""
    requirements = "REQ-ZZ-9: reservas online"
    provider = two_phase_provider(
        llm_result(requirements, input_tokens=800, output_tokens=200),
        llm_result("## Estimación: x", input_tokens=3000, output_tokens=600),
    )
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    response = post(client, preprocessing="two_phase")
    assert response.status_code == 200
    assert provider.call_count == 2
    (first_system, first_messages), (second_system, second_messages) = provider.calls
    # Extraction: the transcription goes unmodified; limited to 1500 output tokens.
    assert first_system == EXTRACTION_SYSTEM_PROMPT
    assert first_messages == [{"role": "user", "content": T}]
    assert call_options(provider, 0).max_tokens == EXTRACTION_MAX_TOKENS == 1500
    # Estimation: the extracted requirements are the only user message.
    assert second_messages == [{"role": "user", "content": requirements}]
    assert second_system == llm_service._system_prompt(two_phase=True)
    for system in (first_system, second_system):
        assert T not in system
        assert requirements not in system
    body = response.json()
    assert body["preprocessing"] == "two_phase"
    assert body["extracted_requirements"] == requirements
    assert (body["preprocessing_input_tokens"], body["preprocessing_output_tokens"]) == (800, 200)
    assert body["preprocessing_cost_usd"] == estimate_cost("gpt-4o-mini", 800, 200)
    assert (body["input_tokens"], body["output_tokens"]) == (3000, 600)


def test_two_phase_cost_usd_excludes_the_extraction_call(client):
    """Requirement: Endpoint de estimación (el coste de la fase previa no se suma a cost_usd)."""
    provider = two_phase_provider(
        llm_result("REQ", model="gpt-4o-mini", input_tokens=5000, output_tokens=1000),
        llm_result("## Estimación: x", model="gpt-4o-mini", input_tokens=3000, output_tokens=600),
    )
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    body = post(client, preprocessing="two_phase").json()
    assert body["cost_usd"] == estimate_cost("gpt-4o-mini", 3000, 600)
    assert body["preprocessing_cost_usd"] == estimate_cost("gpt-4o-mini", 5000, 1000)
    assert body["cost_usd"] != body["preprocessing_cost_usd"]


def test_without_two_phase_the_preprocessing_fields_are_neutral(client):
    """Requirement: Endpoint de estimación (fase previa: 0 tokens y null sin two_phase)."""
    for preprocessing in ("none", "inline_cleaning"):
        body = post(client, preprocessing=preprocessing).json()
        assert body["extracted_requirements"] is None
        assert (body["preprocessing_input_tokens"], body["preprocessing_output_tokens"]) == (0, 0)
        assert body["preprocessing_cost_usd"] is None


def test_two_phase_unknown_model_has_null_preprocessing_cost(client):
    """Requirement: Endpoint de estimación (preprocessing_cost_usd null con modelo sin precio)."""
    provider = two_phase_provider(llm_result("REQ", model="mystery-model"))
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    body = post(client, preprocessing="two_phase").json()
    assert body["preprocessing_cost_usd"] is None


def test_two_phase_extraction_failure_is_a_generic_502_and_skips_the_estimation(client):
    """Requirement: Preprocesado / Scenario: Fallo en la extracción."""
    provider = two_phase_provider(LLMProviderError(provider="openai"))
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    response = post(client, preprocessing="two_phase")
    assert response.status_code == 502
    assert response.json() == {"detail": "LLM provider error"}
    assert provider.call_count == 1


@pytest.mark.parametrize("blank", ["", "   \n\t "])
def test_two_phase_blank_extraction_is_a_generic_502(client, blank):
    """Requirement: Preprocesado / Scenario: Extracción vacía."""
    provider = two_phase_provider(llm_result(blank))
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    response = post(client, preprocessing="two_phase")
    assert response.status_code == 502
    assert response.json() == {"detail": "LLM provider error"}
    assert provider.call_count == 1


def test_two_phase_truncated_extraction_continues_with_the_received_text_and_warns(client):
    """Requirement: Preprocesado (extracción truncada: continúa y registra un warning)."""
    provider = two_phase_provider(llm_result("REQ-PARCIAL", truncated=True))
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    with capture_logs() as logs:
        response = post(client, preprocessing="two_phase")
    assert response.status_code == 200
    assert provider.calls[1][1] == [{"role": "user", "content": "REQ-PARCIAL"}]
    assert response.json()["extracted_requirements"] == "REQ-PARCIAL"
    warnings = [e for e in logs if e["event"] == "requirements_extraction_truncated"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"


def test_two_phase_estimation_failure_after_extraction_is_502(client):
    """Requirement: Endpoint de estimación / Scenario: Fallo del proveedor (segunda llamada)."""
    provider = two_phase_provider(llm_result("REQ"), LLMProviderError(provider="openai"))
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    assert post(client, preprocessing="two_phase").status_code == 502
    assert provider.call_count == 2


# --- Requirement: Evaluación (API) ---

VALID_ESTIMATION = ESTIMATION_EXAMPLES[0]["estimation"]


def test_evaluate_true_returns_the_full_validation_object(client, provider):
    """Requirement: Resultado de la evaluación (campos de `validation`)."""
    provider.result = llm_result(VALID_ESTIMATION)
    validation = post(client).json()["validation"]
    assert set(validation) == {
        "has_title",
        "has_breakdown_table",
        "has_totals",
        "has_team",
        "has_duration",
        "rows_parsed",
        "declared_total_hours",
        "sum_row_hours",
        "hours_match",
        "declared_total_cost",
        "sum_row_cost",
        "cost_match",
        "finish_ok",
        "score",
        "issues",
    }
    assert validation["score"] == 1.0
    assert validation["issues"] == []
    assert validation["rows_parsed"] == len(ESTIMATION_EXAMPLES[0]["rows"])


def test_unstructured_estimation_gets_a_partial_score_and_issues(client, provider):
    """Requirement: Resultado de la evaluación (la respuesta lleva score e issues)."""
    provider.result = llm_result("1. Diseño: 40 h\n2. Backend: 80 h")
    validation = post(client).json()["validation"]
    assert validation["score"] < 1.0
    assert validation["has_breakdown_table"] is False
    assert validation["hours_match"] is None
    assert validation["issues"]


def test_evaluate_false_returns_null_validation(client, provider):
    """Requirement: Evaluación estructural / Scenario: Evaluación desactivada."""
    provider.result = llm_result(VALID_ESTIMATION)
    with capture_logs() as logs:
        response = post(client, evaluate=False)
    assert response.status_code == 200
    assert response.json()["validation"] is None
    assert not [e for e in logs if e["event"] == "estimation_evaluated"]


def test_evaluation_is_logged_with_score_and_issue_count_only(client, provider):
    """Requirement: Resultado de la evaluación (log `estimation_evaluated`)."""
    provider.result = llm_result("1. Diseño: 40 h")
    with capture_logs() as logs:
        validation = post(client).json()["validation"]
    logged = [e for e in logs if e["event"] == "estimation_evaluated"]
    assert len(logged) == 1
    assert logged[0]["score"] == validation["score"]
    assert logged[0]["issues"] == len(validation["issues"])
    assert "1. Diseño" not in str(logged[0])


def test_cache_hit_is_evaluated_on_the_cached_text(client, response_cache):
    """Requirement: Endpoint de estimación / Scenario: Estimación desde la caché."""
    inner = FakeProvider(text=VALID_ESTIMATION, model="gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: CachedProvider(
        inner, response_cache, "gpt-4o-mini"
    )
    first = post(client).json()
    second = post(client).json()
    assert (first["cache_hit"], second["cache_hit"]) == (False, True)
    assert second["estimation"] == first["estimation"]
    assert second["validation"]["score"] == 1.0
    assert inner.call_count == 1


def test_evaluate_false_after_a_cached_evaluated_request_is_still_a_hit(client, response_cache):
    """Requirement: Caché / Scenario: Evaluación fuera de la clave."""
    inner = FakeProvider(text=VALID_ESTIMATION, model="gpt-4o-mini")
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: CachedProvider(
        inner, response_cache, "gpt-4o-mini"
    )
    assert post(client, evaluate=True).json()["cache_hit"] is False
    second = post(client, evaluate=False).json()
    assert second["cache_hit"] is True
    assert second["validation"] is None
    assert inner.call_count == 1


def test_latency_includes_a_value_for_two_phase(client):
    """Requirement: Endpoint de estimación (latency_ms total, también con fase previa)."""
    provider = two_phase_provider(llm_result("REQ"))
    app.dependency_overrides[llm_service.get_llm_provider] = lambda: provider
    assert post(client, preprocessing="two_phase").json()["latency_ms"] >= 0


# --- Service level (callers without HTTP) ---


@pytest.mark.parametrize(
    ("options", "field"),
    [
        (GenerationOptions(max_tokens=0), "max_tokens"),
        (GenerationOptions(num_examples=4), "num_examples"),
        (GenerationOptions(thinking_budget=10), "thinking_budget"),
        (GenerationOptions(model=""), "model"),
    ],
)
def test_service_rejects_invalid_options_before_any_llm_call(fake_provider, options, field):
    """Requirement: Parámetros de generación (validación antes de cualquier llamada al LLM)."""
    with pytest.raises(InvalidOptionError) as exc_info:
        EstimationService(fake_provider).estimate("T", options)
    assert exc_info.value.field == field
    assert fake_provider.call_count == 0


def test_service_turns_a_resolver_error_into_an_invalid_model_option(fake_provider):
    """Requirement: Sobrescritura del modelo (error de resolución -> error en `model`)."""

    def resolve(name: str) -> Deployment:
        raise ValueError("model 'x' is unusable")

    service = EstimationService(fake_provider, resolve_model=resolve)
    with pytest.raises(InvalidOptionError) as exc_info:
        service.estimate("T", GenerationOptions(model="x"))
    assert (exc_info.value.field, exc_info.value.message) == ("model", "model 'x' is unusable")
    assert fake_provider.call_count == 0


# --- Requirement: SSE y Streamlit no exponen los parámetros ---


def test_sse_endpoint_ignores_generation_parameters_and_uses_the_defaults(client, provider):
    """Requirement: Parámetros de generación (el endpoint SSE usa los valores por defecto)."""
    body = {
        "messages": [{"role": "user", "content": T}],
        "max_tokens": 5,
        "model": "claude-haiku-4-5",
        "num_examples": 0,
        "preprocessing": "two_phase",
    }
    response = client.post(STREAM, json=body)
    assert response.status_code == 200
    assert provider.call_count == 1
    assert provider.options == [None]
    assert provider.calls[0][0] == llm_service._system_prompt()
