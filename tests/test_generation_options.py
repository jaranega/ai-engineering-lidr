"""HTTP and service tests for the per-request generation options (spec: generation-options,
estimation-api, cag-prompt and estimation-evaluation of `s04-add-typed-form-and-jinja-prompts`)."""

import pytest
from structlog.testing import capture_logs

from app.config import Deployment, get_settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.services import llm_service
from app.services.llm_service import (
    CachedProvider,
    EstimationService,
    GenerationOptions,
    InvalidOptionError,
    LLMCallOptions,
    LLMProviderError,
    LLMResult,
)
from tests.conftest import FakeProvider, example_estimates, make_input, make_settings, valid_body

ESTIMATE = "/api/v1/estimate"
HAIKU = Deployment("claude-haiku-4-5", "anthropic")


def post(client, **options):
    return client.post(ESTIMATE, json=valid_body(**options))


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
    expected_system, expected_user = render_estimation_prompt(make_input(), "v1", num_examples=3)
    assert system == expected_system
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] in system
    assert messages == [{"role": "user", "content": expected_user}]
    options = call_options(provider)
    assert options == LLMCallOptions()
    assert (options.model, options.max_tokens, options.thinking_budget) == (None, 2048, None)
    assert response.json()["validation"] is not None
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


@pytest.mark.parametrize(
    "removed",
    [{"preprocessing": "two_phase"}, {"example_format": "json"}, {"transcription": "texto viejo"}],
    ids=["preprocessing", "example_format", "transcription"],
)
def test_removed_fields_are_ignored(client, provider, removed):
    """Requirement: Parámetros de generación / Scenario: Campos retirados."""
    baseline = post(client)
    system_without = provider.calls[0][0]
    response = post(client, **removed)
    assert response.status_code == baseline.status_code == 200
    assert provider.call_count == 2  # one call each: no second phase
    assert provider.calls[1][0] == system_without
    assert provider.calls[1] == provider.calls[0]
    assert set(response.json()) == set(baseline.json())


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


# --- Requirement: Evaluación (API) ---

VALID_ESTIMATION = example_estimates("line_items")[0]


def test_evaluate_true_returns_the_full_validation_object(client, provider):
    """Requirement: Resultado de la evaluación (campos de `validation`)."""
    provider.result = llm_result(VALID_ESTIMATION)
    validation = post(client).json()["validation"]
    assert set(validation) == {
        "output_format",
        "checks_total",
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
    assert validation["output_format"] == "line_items"
    assert validation["checks_total"] == 8
    assert validation["rows_parsed"] == len(ESTIMATION_EXAMPLES[0]["rows"])


@pytest.mark.parametrize(
    ("fmt", "total", "table"), [("phases_table", 8, True), ("narrative", 5, None)]
)
def test_validation_follows_the_requested_output_format(client, provider, fmt, total, table):
    """Requirement: Evaluación estructural (por output_format) / Resultado de la evaluación."""
    provider.result = llm_result(example_estimates(fmt)[1])
    validation = post(client, output_format=fmt).json()["validation"]
    assert validation["output_format"] == fmt
    assert validation["checks_total"] == total
    assert validation["has_breakdown_table"] is table
    assert validation["score"] == 1.0
    if fmt == "narrative":
        assert (validation["rows_parsed"], validation["hours_match"]) == (0, None)


def test_estimation_evaluated_log_has_the_output_format(client, provider):
    """Requirement: Resultado de la evaluación (log con output_format, score e incidencias)."""
    provider.result = llm_result(example_estimates("narrative")[0])
    with capture_logs() as logs:
        post(client, output_format="narrative")
    logged = [e for e in logs if e["event"] == "estimation_evaluated"]
    assert [(e["output_format"], e["score"], e["issues"]) for e in logged] == [
        ("narrative", 1.0, 0)
    ]


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
    assert second["text"] == first["text"]
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


# --- Service level (callers without HTTP) ---


@pytest.mark.parametrize(
    ("options", "field"),
    [
        (GenerationOptions(max_tokens=0), "max_tokens"),
        (GenerationOptions(num_examples=4), "num_examples"),
        (GenerationOptions(thinking_budget=10), "thinking_budget"),
        (GenerationOptions(model=""), "model"),
        (GenerationOptions(prompt_version="v9"), "prompt_version"),
        (GenerationOptions(prompt_version="../v1"), "prompt_version"),
    ],
)
def test_service_rejects_invalid_options_before_any_llm_call(fake_provider, options, field):
    """Requirement: Parámetros de generación (validación antes de cualquier llamada al LLM)."""
    with pytest.raises(InvalidOptionError) as exc_info:
        EstimationService(fake_provider).estimate(make_input(), options)
    assert exc_info.value.field == field
    assert fake_provider.call_count == 0


def test_service_turns_a_resolver_error_into_an_invalid_model_option(fake_provider):
    """Requirement: Sobrescritura del modelo (error de resolución -> error en `model`)."""

    def resolve(name: str) -> Deployment:
        raise ValueError("model 'x' is unusable")

    service = EstimationService(fake_provider, resolve_model=resolve)
    with pytest.raises(InvalidOptionError) as exc_info:
        service.estimate(make_input(), GenerationOptions(model="x"))
    assert (exc_info.value.field, exc_info.value.message) == ("model", "model 'x' is unusable")
    assert fake_provider.call_count == 0


def test_service_sends_system_and_a_single_user_message(fake_provider):
    """Requirement: Endpoint de estimación / Scenario: Mensajes separados (servicio)."""
    request = make_input(output_format="phases_table")
    result = EstimationService(fake_provider).estimate(request, GenerationOptions("v2"))
    system, user = render_estimation_prompt(request, "v2")
    assert fake_provider.calls == [(system, [{"role": "user", "content": user}])]
    assert result.prompt_version == "v2"
    assert result.validation is not None


def test_service_logs_the_requested_event_without_content(fake_provider):
    """Requirement: Observabilidad (estimation_requested con los campos nuevos, sin contenido)."""
    with capture_logs() as logs:
        EstimationService(fake_provider).estimate(make_input(), GenerationOptions("v2"))
    event = next(e for e in logs if e["event"] == "estimation_requested")
    assert event["prompt_version"] == "v2"
    assert event["output_format"] == "line_items"
    assert event["detail_level"] == "medium"
    assert event["project_type"] == "web_saas"
    assert event["num_examples"] == 3
    assert "description" not in event
    assert "transcription_chars" not in event
