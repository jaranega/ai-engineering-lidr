"""cag-stress-test / Métricas de presupuesto y deriva de memoria. No LLM, no network."""

import pytest

from app.sessions import ProjectMetadata
from evals.metrics import MetricResult
from evals.stress.metrics import CostBudgetMetric, LatencyBudgetMetric, MemoryDriftMetric


def snapshot(summary=None, anchors=(), **metadata):
    return {
        "summary": summary,
        "anchors": list(anchors),
        "project_metadata": ProjectMetadata(**metadata).model_dump(),
    }


def test_latency_at_the_limit_passes():
    """Scenario: Latencia en el límite."""
    result = LatencyBudgetMetric(10000).evaluate({"latency_ms": 10000})
    assert isinstance(result, MetricResult) and result.passed and result.score == 1.0


def test_latency_above_the_budget_fails():
    result = LatencyBudgetMetric(10000).evaluate({"latency_ms": 10001})
    assert not result.passed and result.score == 0.0
    assert "10001" in result.details and "10000" in result.details


def test_latency_default_budget_is_ten_seconds():
    assert LatencyBudgetMetric().budget_ms == 10000


def test_cost_above_the_budget_fails_and_mentions_both_values():
    """Scenario: Coste por encima."""
    result = CostBudgetMetric(0.003).evaluate({"cost_usd": 0.0041})
    assert not result.passed and result.score == 0.0
    assert "0.0041" in result.details and "0.003" in result.details


@pytest.mark.parametrize("cost", [0.003, 0.001, 0.0])
def test_cost_within_the_budget_passes(cost):
    assert CostBudgetMetric(0.003).evaluate({"cost_usd": cost}).passed


def test_unknown_cost_scores_zero_with_unknown_cost_details():
    """Requirement: cost_usd nulo da 0.0 con details "unknown cost"."""
    result = CostBudgetMetric().evaluate({"cost_usd": None})
    assert result.score == 0.0 and not result.passed and result.details == "unknown cost"


def test_budget_metrics_read_objects_with_attributes_too():
    from app.sessions import TurnObservation

    obs = TurnObservation(1, "abcd1234", 1, 0, 2, 0, 0, 1, 1, 0.001, 5, "none", "default")
    assert LatencyBudgetMetric(10).evaluate(obs).passed
    assert CostBudgetMetric(0.003).evaluate(obs).passed


def test_fact_only_in_metadata_is_found_there_and_the_other_is_missing():
    """Scenario: Hecho solo en la metadata."""
    result = MemoryDriftMetric(["nimbus", "Flutter"]).evaluate(snapshot(project_name="Nimbus"))
    assert result.score == 0.5 and not result.passed
    assert "nimbus: metadata" in result.details and "Flutter: missing" in result.details


def test_fact_outside_the_requested_sources_counts_as_missing():
    """Scenario: Hecho fuera de las fuentes pedidas."""
    snap = snapshot(summary="Proyecto Nimbus con Stripe")
    result = MemoryDriftMetric(["Stripe"], where=("metadata",)).evaluate(snap)
    assert result.score == 0.0 and "Stripe: missing" in result.details
    assert MemoryDriftMetric(["Stripe"], where=("summary",)).evaluate(snap).passed


def test_matching_ignores_case_and_accents():
    snap = snapshot(summary="Integración con PASARELA de pago")
    assert MemoryDriftMetric(["integracion", "pasarela"]).evaluate(snap).passed


def test_each_source_is_searched_and_reported():
    snap = snapshot(
        summary="Stripe", anchors=["Presupuesto 30.000 €"], mentioned_technologies=["React"]
    )
    result = MemoryDriftMetric(["Stripe", "30.000", "React"]).evaluate(snap)
    assert result.passed and result.score == 1.0
    assert "Stripe: summary" in result.details
    assert "30.000: anchors" in result.details
    assert "React: metadata" in result.details


def test_metadata_flattens_scope_constraints_and_rejected_options():
    snap = snapshot(
        agreed_scope="módulo de informes",
        explicit_constraints=["presupuesto máximo"],
        rejected_options=["Angular"],
    )
    assert MemoryDriftMetric(["informes", "presupuesto", "angular"]).evaluate(snap).passed


def test_no_facts_scores_one():
    """Requirement: Sin hechos, score = 1.0."""
    result = MemoryDriftMetric([]).evaluate(snapshot())
    assert result.score == 1.0 and result.passed
