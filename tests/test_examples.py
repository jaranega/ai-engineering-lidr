"""Tests for the static CAG examples (spec: cag-prompt, "Ejemplos de estimación estáticos")."""

import pytest

from app.context.examples import (
    ESTIMATION_EXAMPLES,
    PHASE_KEYS,
    PHASES,
    RATES_EUR_PER_HOUR,
    format_amount,
)
from app.domain import ProjectType

EXAMPLE_IDS = [f"example-{i}" for i in range(len(ESTIMATION_EXAMPLES))]
examples = pytest.mark.parametrize("example", ESTIMATION_EXAMPLES, ids=EXAMPLE_IDS)


def test_rates_are_the_agreed_ones():
    """Requirement: Ejemplos de estimación estáticos (tarifas 62,50 y 50 EUR/h)."""
    assert RATES_EUR_PER_HOUR == {"dev": 62.5, "design": 50.0}


def test_there_are_three_valid_examples():
    """Requirement: Ejemplos de estimación estáticos / Scenario: Ejemplos válidos."""
    assert len(ESTIMATION_EXAMPLES) == 3
    valid_types = {t.value for t in ProjectType}
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"].strip()
        assert example["project_type"] in valid_types
        assert example["project_type"] != ProjectType.INTERNAL_TOOL.value
        assert "estimation" not in example  # no rendered text is stored
    assert {e["project_type"] for e in ESTIMATION_EXAMPLES} == {
        "web_saas",
        "mobile_app",
        "data_pipeline",
    }


def test_canonical_phases_are_the_five_agreed_ones_in_order():
    """Requirement: Ejemplos de estimación estáticos (lista canónica de fases)."""
    assert [p["key"] for p in PHASES] == [
        "discovery",
        "setup",
        "development",
        "qa",
        "deployment",
    ]
    assert [p["label_es"] for p in PHASES] == [
        "Descubrimiento y diseño",
        "Arquitectura y setup",
        "Desarrollo",
        "QA y testing",
        "Despliegue y traspaso",
    ]
    assert [p["label_en"] for p in PHASES] == [
        "Discovery and design",
        "Architecture and setup",
        "Development",
        "QA and testing",
        "Deployment and handover",
    ]


@examples
def test_examples_have_structured_data(example):
    """Requirement: Ejemplos de estimación estáticos (datos estructurados de la estimación)."""
    assert example["title"].strip()
    assert example["team"].strip()
    assert example["rows"]
    assert len(example["duration_weeks"]) == 2
    assert 1 <= example["duration_weeks"][0] <= example["duration_weeks"][1]
    assert example["risks"]
    for row in example["rows"]:
        assert row["task"].strip()
        assert row["role"] in RATES_EUR_PER_HOUR


@examples
def test_example_hours_are_multiples_of_five(example):
    """Requirement: Ejemplos / Scenario: Aritmética de los ejemplos (horas múltiplo de 5)."""
    assert all(row["hours"] % 5 == 0 for row in example["rows"])


@examples
def test_example_row_cost_is_hours_times_rate(example):
    """Requirement: Ejemplos / Scenario: Aritmética de los ejemplos (coste = horas x tarifa)."""
    for row in example["rows"]:
        assert row["cost_eur"] == pytest.approx(row["hours"] * RATES_EUR_PER_HOUR[row["role"]])


@examples
def test_example_totals_are_the_sum_of_the_rows_and_of_the_phases(example):
    """Requirement: Ejemplos / Scenario: Aritmética de los ejemplos (totales = filas = fases)."""
    assert example["total_hours"] == sum(row["hours"] for row in example["rows"])
    assert example["total_cost_eur"] == pytest.approx(sum(r["cost_eur"] for r in example["rows"]))
    assert example["total_hours"] == sum(p["hours"] for p in example["phases"])
    assert example["total_cost_eur"] == pytest.approx(sum(p["cost_eur"] for p in example["phases"]))


@examples
def test_example_phase_aggregation_matches_its_rows(example):
    """Requirement: Ejemplos / Scenario: Aritmética de los ejemplos (agregación por fase)."""
    for summary in example["phases"]:
        rows = [r for r in example["rows"] if r["phase"] == summary["phase"]]
        assert rows
        assert summary["hours"] == sum(r["hours"] for r in rows)
        assert summary["cost_eur"] == pytest.approx(sum(r["cost_eur"] for r in rows))


@examples
def test_example_phases_are_canonical_and_ordered_with_confidence(example):
    """Requirement: Ejemplos / Scenario: Fases de los ejemplos."""
    assert all(row["phase"] in PHASE_KEYS for row in example["rows"])
    present = [p["phase"] for p in example["phases"]]
    assert present == [k for k in PHASE_KEYS if k in {r["phase"] for r in example["rows"]}]
    for summary in example["phases"]:
        assert 0 <= summary["confidence_pct"] <= 100
        assert summary["confidence_pct"] % 5 == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(19500.0, "19500"), (937.5, "937,50"), (0.0, "0"), (62.5, "62,50")],
)
def test_format_amount_has_no_thousands_separator_and_decimal_comma(value, expected):
    """Requirement: Formato de salida con coste (importes sin separador de miles)."""
    assert format_amount(value) == expected
