"""Tests for the static CAG examples (spec: cag-prompt, "Ejemplos de estimación estáticos")."""

import re

import pytest

from app.context.examples import (
    ESTIMATION_EXAMPLES,
    RATES_EUR_PER_HOUR,
    format_amount,
)

EXAMPLE_IDS = [f"example-{i}" for i in range(len(ESTIMATION_EXAMPLES))]
examples = pytest.mark.parametrize("example", ESTIMATION_EXAMPLES, ids=EXAMPLE_IDS)


def test_rates_are_the_agreed_ones():
    """Requirement: Ejemplos de estimación estáticos (tarifas 62,50 y 50 EUR/h)."""
    assert RATES_EUR_PER_HOUR == {"dev": 62.5, "design": 50.0}


def test_there_are_three_valid_examples():
    """Requirement: Ejemplos de estimación estáticos / Scenario: Ejemplos válidos."""
    assert len(ESTIMATION_EXAMPLES) == 3
    for example in ESTIMATION_EXAMPLES:
        for field in ("meeting_summary", "estimation"):
            assert isinstance(example[field], str)
            assert example[field].strip()


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
def test_example_totals_are_the_sum_of_the_rows(example):
    """Requirement: Ejemplos / Scenario: Aritmética de los ejemplos (totales = suma)."""
    assert example["total_hours"] == sum(row["hours"] for row in example["rows"])
    assert example["total_cost_eur"] == pytest.approx(
        sum(row["cost_eur"] for row in example["rows"])
    )


@examples
def test_example_markdown_contains_every_row_and_the_totals(example):
    """Requirement: Ejemplos / Scenario: Markdown coherente con los datos."""
    markdown = example["estimation"]
    lines = markdown.splitlines()
    assert "| Tarea | Horas | Coste (EUR) |" in lines
    table_rows = [line for line in lines if line.startswith("| ") and "Tarea" not in line]
    assert len(table_rows) == len(example["rows"])
    for row, line in zip(example["rows"], table_rows, strict=True):
        assert line == f"| {row['task']} | {row['hours']} | {format_amount(row['cost_eur'])} |"
    assert f"**Total de horas:** {example['total_hours']}" in markdown
    assert f"**Coste total:** {format_amount(example['total_cost_eur'])} EUR" in markdown
    assert example["title"] in markdown
    assert example["team"] in markdown


@examples
def test_example_markdown_has_no_total_row_in_the_table(example):
    """Requirement: Formato de salida con coste (tabla sin fila de total)."""
    table = [line for line in example["estimation"].splitlines() if line.startswith("|")]
    assert not [line for line in table if re.match(r"\|\s*\**total", line, re.IGNORECASE)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(19500.0, "19500"), (937.5, "937,50"), (0.0, "0"), (62.5, "62,50")],
)
def test_format_amount_has_no_thousands_separator_and_decimal_comma(value, expected):
    """Requirement: Formato de salida con coste (importes sin separador de miles)."""
    assert format_amount(value) == expected
