"""Costs, totals and out-of-scope filter (spec: structured-estimation, "Cálculo de costes y
totales en el código" and "Guardrail de salida de fuera de alcance").
"""

import pytest
from structlog.testing import capture_logs

from app.context.examples import PHASE_KEYS, RATES_EUR_PER_HOUR
from app.estimation.compute import compute_estimate
from app.estimation.schema import EstimationResult
from tests.conftest import out_of_scope_payload, valid_result


def _result(rows, phases, **overrides) -> EstimationResult:
    return valid_result(
        rows=[
            {"task": f"t{i}", "phase": phase, "rate_profile": profile, "hours": hours}
            for i, (phase, profile, hours) in enumerate(rows)
        ],
        phases=[{"phase": p, "confidence_pct": 80} for p in phases],
        **overrides,
    )


def test_rates_are_the_agreed_ones():
    """Requirement: Cálculo de costes (62,50 EUR/h dev y 50 EUR/h design)."""
    assert RATES_EUR_PER_HOUR == {"dev": 62.5, "design": 50.0}


def test_exact_totals():
    """Scenario: Totales exactos (40 h design y 60 h dev)."""
    result = _result(
        [("discovery", "design", 40), ("development", "dev", 60)], ["discovery", "development"]
    )
    estimate = compute_estimate(result, "en")
    assert [r.cost_eur for r in estimate.rows] == [2000, 3750]
    assert [r.rate_eur_per_hour for r in estimate.rows] == [50, 62.5]
    assert estimate.total_hours == 100
    assert estimate.total_cost_eur == 5750
    assert estimate.out_of_scope is False


def test_total_cost_is_rounded_to_two_decimals():
    """Requirement: Cálculo (total_cost_eur redondeado a 2 decimales)."""
    result = _result([("development", "dev", 5)], ["development"])
    estimate = compute_estimate(result, "en")
    assert estimate.rows[0].cost_eur == 312.5
    assert estimate.total_cost_eur == 312.5


def test_phase_aggregation_is_in_canonical_order_and_sums_match_totals():
    """Scenario: Agregación por fase (qa antes que discovery)."""
    result = _result(
        [("qa", "dev", 40), ("discovery", "design", 20), ("qa", "dev", 10)],
        ["qa", "discovery"],
    )
    estimate = compute_estimate(result, "en")
    assert [p.phase for p in estimate.phases] == ["discovery", "qa"]
    assert [p.phase for p in estimate.phases] == [k for k in PHASE_KEYS if k in {"discovery", "qa"}]
    qa = estimate.phases[1]
    assert (qa.hours, qa.cost_eur) == (50, 3125)
    assert sum(p.hours for p in estimate.phases) == estimate.total_hours
    assert sum(p.cost_eur for p in estimate.phases) == estimate.total_cost_eur


def test_phases_without_rows_are_not_listed_and_keep_confidence_and_assumptions():
    """Requirement: Cálculo (solo las fases que tienen filas, con confianza y supuestos)."""
    result = valid_result()
    estimate = compute_estimate(result, "en")
    assert [p.phase for p in estimate.phases] == [p.phase for p in result.phases]
    assert all(p.assumptions for p in estimate.phases)
    assert estimate.phases[0].confidence_pct == result.phases[0].confidence_pct


def test_estimate_carries_language_text_team_and_duration():
    """Requirement: Cálculo (language, title, team, duration_weeks, narrative...)."""
    result = valid_result()
    estimate = compute_estimate(result, "es")
    assert estimate.language == "es"
    assert estimate.title == result.title and estimate.summary == result.summary
    assert estimate.team == result.team
    assert estimate.duration_weeks == result.duration_weeks
    assert estimate.narrative == result.narrative
    assert estimate.assumptions_and_risks == result.assumptions_and_risks


def test_low_confidence_is_out_of_scope_and_drops_invented_rows():
    """Scenario: Confianza baja (confidence 20 con filas inventadas)."""
    result = valid_result(confidence_pct=20)
    assert result.rows  # the model "invented" a breakdown
    with capture_logs() as logs:
        estimate = compute_estimate(result, "en")
    assert estimate.out_of_scope is True
    assert estimate.rows == [] and estimate.phases == [] and estimate.team == []
    assert estimate.narrative == [] and estimate.duration_weeks is None
    assert (estimate.total_hours, estimate.total_cost_eur) == (0, 0)
    assert estimate.title == result.title
    assert estimate.summary == result.summary
    assert estimate.confidence_pct == 20
    assert estimate.assumptions_and_risks == result.assumptions_and_risks
    events = [e for e in logs if e["event"] == "estimation_out_of_scope"]
    assert [e["confidence_pct"] for e in events] == [20]


@pytest.mark.parametrize(("confidence", "out_of_scope"), [(25, True), (30, False), (35, False)])
def test_threshold_is_30(confidence, out_of_scope):
    """Scenario: Umbral (confianza 30 no es fuera de alcance)."""
    estimate = compute_estimate(valid_result(confidence_pct=confidence), "en")
    assert estimate.out_of_scope is out_of_scope
    assert bool(estimate.rows) is not out_of_scope


def test_out_of_scope_without_breakdown_never_raises():
    """Requirement: Guardrail de salida (la política es de filtro: nunca lanza)."""
    estimate = compute_estimate(EstimationResult.model_validate(out_of_scope_payload()), "es")
    assert estimate.out_of_scope and estimate.total_cost_eur == 0


def test_in_scope_does_not_log_out_of_scope():
    """Requirement: Guardrail de salida (solo se registra fuera de alcance)."""
    with capture_logs() as logs:
        compute_estimate(valid_result(), "en")
    assert not [e for e in logs if e["event"] == "estimation_out_of_scope"]
