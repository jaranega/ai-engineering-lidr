"""offline-evals: deterministic metrics (Métricas deterministas). Offline, no LLM."""

import copy

import pytest

from app.schemas import SessionEstimateResponse
from evals.dataset import GoldenCase
from evals.metrics import (
    CaseOutcome,
    ContentRecallMetric,
    CostBoundsMetric,
    MetricResult,
    SchemaAdherenceMetric,
    TierMatchMetric,
    run_all_metrics,
)
from tests.conftest import (
    TRANSCRIPT,
    FakeProvider,
    out_of_scope_payload,
    turn_data,
    valid_payload,
)

BASE = "/api/v1/sessions"


def make_case(**overrides) -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "id": "case-001-test",
            "language": "en",
            "transcript": TRANSCRIPT,
            "project_type": "web_saas",
            **overrides,
        }
    )


@pytest.fixture
def make_response(make_session_client):
    """Factory of a real `SessionEstimateResponse` (from the API with a fake provider) with
    nested overrides: `result={...}`, `tier=...`, `validation={...}`.
    """

    def factory(payload: dict | None = None, *, result=None, validation=None, **top):
        client, *_ = make_session_client(
            provider=FakeProvider(structured=payload or valid_payload())
        )
        session_id = client.post(BASE).json()["session_id"]
        body = client.post(f"{BASE}/{session_id}/estimate", data=turn_data()).json()
        data = copy.deepcopy(body)
        data["result"].update(result or {})
        if validation is not None:
            data["validation"].update(validation)
        data.update(top)
        return SessionEstimateResponse.model_validate(data)

    return factory


def ok(response) -> CaseOutcome:
    return CaseOutcome(response=response)


# --- Estructura de las métricas ---


def test_metrics_have_names_and_binary_scores(make_response):
    response = make_response()
    results = run_all_metrics(make_case(), ok(response))
    assert [r.name for r in results] == [
        "schema_adherence",
        "cost_bounds",
        "content_recall",
        "tier_match",
    ]
    assert all(r.score in (0.0, 1.0) for r in results)
    assert all(isinstance(r, MetricResult) for r in results)


def test_metric_result_is_immutable():
    result = MetricResult("x", 1.0, True, "d")
    with pytest.raises(AttributeError):
        result.score = 0.0  # type: ignore[misc]


# --- SchemaAdherenceMetric ---


def test_schema_passes_for_a_consistent_answer(make_response):
    result = SchemaAdherenceMetric().evaluate(make_case(), ok(make_response()))
    assert result.passed and result.score == 1.0


def test_schema_fails_when_the_rows_do_not_add_up(make_response):
    response = make_response()
    broken = make_response(result={"total_hours": response.result.total_hours + 5})
    result = SchemaAdherenceMetric().evaluate(make_case(), ok(broken))
    assert not result.passed and result.score == 0.0
    assert "total_hours" in result.details


def test_schema_fails_with_a_validation_score_below_one(make_response):
    response = make_response(validation={"score": 0.75, "issues": ["missing recommended team"]})
    result = SchemaAdherenceMetric().evaluate(make_case(), ok(response))
    assert not result.passed and "0.75" in result.details


@pytest.mark.parametrize(("rng", "passes"), [((1, 10), True), ((100, 200), False)])
def test_schema_checks_the_phase_count_range(make_response, rng, passes):
    response = make_response()
    case = make_case(expected_phase_count_range=list(rng))
    assert SchemaAdherenceMetric().evaluate(case, ok(response)).passed is passes


def test_schema_unexpected_block_fails():
    """Scenario: Bloqueo no esperado."""
    result = SchemaAdherenceMetric().evaluate(make_case(), CaseOutcome(blocked_reason="pii"))
    assert not result.passed and "pii" in result.details


def test_schema_expected_block_passes_and_a_200_fails(make_response):
    case = make_case(expected_blocked=True)
    assert (
        SchemaAdherenceMetric()
        .evaluate(case, CaseOutcome(blocked_reason="prompt_injection"))
        .passed
    )
    assert not SchemaAdherenceMetric().evaluate(case, ok(make_response())).passed


def test_empty_outcome_fails_every_metric_that_needs_a_response():
    results = run_all_metrics(make_case(expected_tier="default"), CaseOutcome())
    assert [r.passed for r in results] == [False] * 4
    assert all("no response" in r.details for r in results)


# --- CostBoundsMetric ---


def test_cost_out_of_range_mentions_the_value_and_the_range(make_response):
    """Scenario: Coste fuera de rango."""
    response = make_response(result={"total_cost_eur": 55000})
    case = make_case(expected_cost_range_eur=[10000, 40000])
    result = CostBoundsMetric().evaluate(case, ok(response))
    assert not result.passed and result.score == 0.0
    assert "55000" in result.details and "10000" in result.details and "40000" in result.details


def test_cost_hours_and_duration_within_ranges_pass(make_response):
    response = make_response(
        result={
            "total_hours": 200,
            "total_cost_eur": 12000.0,
            "duration_weeks": {"min_weeks": 6, "max_weeks": 8},
        }
    )
    case = make_case(
        expected_hours_range=[100, 300],
        expected_cost_range_eur=[10000, 40000],
        expected_duration_weeks_range=[7, 12],
    )
    assert CostBoundsMetric().evaluate(case, ok(response)).passed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_hours_range", [1000, 2000]),
        ("expected_duration_weeks_range", [30, 40]),
    ],
)
def test_hours_and_duration_outside_their_ranges_fail(make_response, field, value):
    response = make_response(
        result={"total_hours": 200, "duration_weeks": {"min_weeks": 6, "max_weeks": 8}}
    )
    result = CostBoundsMetric().evaluate(make_case(**{field: value}), ok(response))
    assert not result.passed


def test_duration_overlap_at_the_edge_passes(make_response):
    response = make_response(result={"duration_weeks": {"min_weeks": 6, "max_weeks": 8}})
    case = make_case(expected_duration_weeks_range=[8, 12])
    assert CostBoundsMetric().evaluate(case, ok(response)).passed


def test_ranges_are_optional(make_response):
    assert CostBoundsMetric().evaluate(make_case(), ok(make_response())).passed


def test_unexpected_out_of_scope_fails(make_response):
    response = make_response(out_of_scope_payload(confidence_pct=10))
    result = CostBoundsMetric().evaluate(make_case(), ok(response))
    assert not result.passed and "out of scope" in result.details


def test_expected_out_of_scope_passes_with_zero_hours_and_fails_with_an_estimate(make_response):
    case = make_case(expected_out_of_scope=True)
    oos = make_response(out_of_scope_payload(confidence_pct=10))
    assert CostBoundsMetric().evaluate(case, ok(oos)).passed
    assert not CostBoundsMetric().evaluate(case, ok(make_response())).passed


# --- ContentRecallMetric ---


def test_terms_in_title_summary_or_tasks_ignore_case_and_accents(make_response):
    response = make_response(
        valid_payload(title="Gestión de RESERVAS", summary="Agenda y recordatorios.")
    )
    case = make_case(expected_in_summary=["reservas", "gestion", "RECORDATORIOS", "work item 1"])
    assert ContentRecallMetric().evaluate(case, ok(response)).passed


def test_missing_term_fails_naming_it(make_response):
    case = make_case(expected_in_summary=["blockchain"])
    result = ContentRecallMetric().evaluate(case, ok(make_response()))
    assert not result.passed and "blockchain" in result.details


def test_technology_with_other_casing_matches(make_response):
    """Scenario: Tecnología con otra grafía."""
    response = make_response(result={"technologies": ["postgresql", "React"]})
    case = make_case(expected_technologies_any_of=["PostgreSQL"])
    assert ContentRecallMetric().evaluate(case, ok(response)).passed


def test_technology_with_accents_matches(make_response):
    response = make_response(result={"technologies": ["Ñandú DB"]})
    case = make_case(expected_technologies_any_of=["nandu"])
    assert ContentRecallMetric().evaluate(case, ok(response)).passed


def test_none_of_the_technologies_fails(make_response):
    response = make_response(result={"technologies": ["React"]})
    case = make_case(expected_technologies_any_of=["Postgres", "MySQL"])
    result = ContentRecallMetric().evaluate(case, ok(response))
    assert not result.passed and "technologies" in result.details


def test_language_mismatch_fails(make_response):
    """Scenario: Idioma distinto."""
    response = make_response(result={"language": "en"})
    result = ContentRecallMetric().evaluate(make_case(language="es"), ok(response))
    assert not result.passed and "language" in result.details


# --- TierMatchMetric ---


def test_tier_match_passes_and_mismatch_names_both(make_response):
    """Scenario: Tier distinto."""
    response = make_response()  # default tier
    assert TierMatchMetric().evaluate(make_case(expected_tier="default"), ok(response)).passed
    result = TierMatchMetric().evaluate(make_case(expected_tier="executive"), ok(response))
    assert not result.passed and result.score == 0.0
    assert "default" in result.details and "executive" in result.details


# --- Saltos ---


def test_expected_block_passes_every_metric_and_skips_content_and_tier():
    """Scenario: Bloqueo esperado."""
    case = make_case(expected_blocked=True, expected_tier="executive")
    results = run_all_metrics(case, CaseOutcome(blocked_reason="prompt_injection"))
    assert all(r.passed and r.score == 1.0 for r in results)
    by_name = {r.name: r for r in results}
    assert by_name["content_recall"].details.startswith("skipped")
    assert by_name["tier_match"].details.startswith("skipped")
    assert not by_name["schema_adherence"].details.startswith("skipped")


def test_content_recall_is_skipped_for_out_of_scope_cases(make_response):
    case = make_case(expected_out_of_scope=True, expected_in_summary=["algo"])
    response = make_response(out_of_scope_payload(confidence_pct=10))
    result = ContentRecallMetric().evaluate(case, ok(response))
    assert result.passed and result.score == 1.0 and result.details.startswith("skipped")


def test_tier_match_is_skipped_without_an_expected_tier(make_response):
    result = TierMatchMetric().evaluate(make_case(), ok(make_response()))
    assert result.passed and result.score == 1.0 and result.details.startswith("skipped")
