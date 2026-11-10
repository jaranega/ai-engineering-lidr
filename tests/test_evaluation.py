"""Tests for the structural evaluation (spec: estimation-evaluation). Pure: no LLM, no network."""

import pytest

from app.context.examples import ESTIMATION_EXAMPLES
from app.services.evaluation import (
    CHECK_COUNT,
    COST_TOLERANCE_RATIO,
    HOURS_TOLERANCE,
    evaluate_estimation,
    parse_number,
)

ES = """\
## Estimación: Web de reservas

### Desglose de tareas
| Tarea | Horas | Coste (EUR) |
|---|---|---|
| Diseño UI | 40 | 2000 |
| Backend | 50 | 3125 |
| Frontend y QA | 30 | 2375 |

### Totales
- **Total de horas:** 120
- **Coste total:** 7500 EUR
- **Equipo recomendado:** 2 desarrolladores
- **Duración estimada:** 4-5 semanas

### Supuestos y riesgos
- Sin migración de datos.
"""

EN = """\
## Estimate: Booking website

### Task breakdown
| Task | Hours | Cost (EUR) |
|---|---|---|
| UI design | 40 | 2000.00 |
| Backend | 50 | 3125.00 |
| Frontend and QA | 30 | 2375.00 |

### Totals
- **Total hours:** 120
- **Total cost:** 7500.00 EUR
- **Recommended team:** 2 developers
- **Estimated duration:** 4-5 weeks

### Assumptions and risks
- No data migration.
"""


def test_correct_spanish_estimation_passes_every_check():
    """Requirement: Evaluación estructural / Scenario: Estimación correcta en español."""
    check = evaluate_estimation(ES, truncated=False)
    assert check.score == 1.0
    assert check.issues == ()
    assert (check.has_title, check.has_breakdown_table, check.has_totals) == (True, True, True)
    assert (check.has_team, check.has_duration, check.finish_ok) == (True, True, True)
    assert (check.hours_match, check.cost_match) == (True, True)
    assert check.rows_parsed == 3
    assert (check.declared_total_hours, check.sum_row_hours) == (120, 120)
    assert (check.declared_total_cost, check.sum_row_cost) == (7500, 7500)


def test_correct_english_estimation_passes_every_check():
    """Requirement: Evaluación estructural / Scenario: Estimación correcta en inglés."""
    check = evaluate_estimation(EN, truncated=False)
    assert check.score == 1.0
    assert check.issues == ()


def test_labels_are_case_insensitive_and_ignore_bold_markers():
    """Requirement: Evaluación estructural (sin distinguir mayúsculas ni negrita)."""
    shouted = ES.replace("**", "").upper()
    assert evaluate_estimation(shouted, truncated=False).score == 1.0
    assert evaluate_estimation(ES.replace("**", ""), truncated=False).score == 1.0


def test_totals_that_do_not_add_up_fail_the_hours_check_and_name_both_numbers():
    """Requirement: Evaluación estructural / Scenario: Totales que no cuadran."""
    check = evaluate_estimation(ES.replace("Total de horas:** 120", "Total de horas:** 130"), False)
    assert check.hours_match is False
    assert check.cost_match is True
    assert check.score < 1.0
    assert len(check.issues) == 1
    assert "120" in check.issues[0]
    assert "130" in check.issues[0]


def test_cost_total_that_does_not_add_up_fails_the_cost_check():
    """Requirement: Evaluación estructural (suma de costes igual al total declarado)."""
    check = evaluate_estimation(ES.replace("Coste total:** 7500", "Coste total:** 9000"), False)
    assert check.cost_match is False
    assert check.hours_match is True
    assert any("7500" in i and "9000" in i for i in check.issues)


def test_numbered_list_without_table_fails_table_and_arithmetic_checks():
    """Requirement: Evaluación estructural / Scenario: Sin tabla."""
    text = (
        "## Estimación: Web\n"
        "1. Diseño: 40 horas\n2. Backend: 50 horas\n"
        "- **Total de horas:** 90\n- **Coste total:** 5625 EUR\n"
        "- **Equipo recomendado:** 2 devs\n- **Duración estimada:** 3 semanas\n"
    )
    check = evaluate_estimation(text, truncated=False)
    assert check.has_breakdown_table is False
    assert check.rows_parsed == 0
    assert check.hours_match is None
    assert check.cost_match is None
    assert check.sum_row_hours is None
    assert check.sum_row_cost is None
    # Missing arithmetic data counts as not passed: 5 of 8 pass.
    assert check.score == round(5 / CHECK_COUNT, 3)
    assert len(check.issues) == 3


def test_truncated_response_fails_finish_check_and_reports_it():
    """Requirement: Evaluación estructural / Scenario: Respuesta truncada."""
    check = evaluate_estimation(ES, truncated=True)
    assert check.finish_ok is False
    assert check.score == round(7 / CHECK_COUNT, 3)
    assert len(check.issues) == 1
    assert "truncated" in check.issues[0]


def test_partial_score_is_passed_checks_over_eight():
    """Requirement: Resultado de la evaluación / Scenario: Puntuación parcial (6 de 8)."""
    text = "\n".join(
        line
        for line in ES.splitlines()
        if "Equipo recomendado" not in line and "Duración" not in line
    )
    check = evaluate_estimation(text, truncated=False)
    assert (check.has_team, check.has_duration) == (False, False)
    assert check.score == 0.75
    assert len(check.issues) == 2


def test_empty_text_scores_only_the_finish_check():
    """Requirement: Resultado de la evaluación (score = aprobadas entre 8, redondeado a 3)."""
    check = evaluate_estimation("", truncated=False)
    assert check.score == round(1 / CHECK_COUNT, 3) == 0.125
    assert len(check.issues) == 7


def test_total_rows_in_the_table_do_not_count():
    """Requirement: Evaluación estructural (las filas cuya tarea empieza por Total no cuentan)."""
    text = ES.replace("\n\n### Totales", "\n| **Total** | 120 | 7500 |\n\n### Totales")
    check = evaluate_estimation(text, truncated=False)
    assert check.rows_parsed == 3
    assert check.sum_row_hours == 120
    assert check.score == 1.0


def test_columns_are_identified_by_their_header_not_their_position():
    """Requirement: Evaluación estructural (columnas identificadas por la cabecera)."""
    text = (
        ES.replace("| Tarea | Horas | Coste (EUR) |", "| Coste (EUR) | Tarea | Horas |")
        .replace("| Diseño UI | 40 | 2000 |", "| 2000 | Diseño UI | 40 |")
        .replace("| Backend | 50 | 3125 |", "| 3125 | Backend | 50 |")
        .replace("| Frontend y QA | 30 | 2375 |", "| 2375 | Frontend y QA | 30 |")
    )
    check = evaluate_estimation(text, truncated=False)
    assert (check.sum_row_hours, check.sum_row_cost) == (120, 7500)
    assert check.score == 1.0


def test_hours_tolerance_is_one_hour_and_cost_tolerance_is_two_percent():
    """Requirement: Evaluación estructural (tolerancias de 1 hora y del 2 % del total)."""
    assert HOURS_TOLERANCE == 1.0
    assert COST_TOLERANCE_RATIO == 0.02
    within = ES.replace("Total de horas:** 120", "Total de horas:** 121").replace(
        "Coste total:** 7500", "Coste total:** 7600"
    )
    assert evaluate_estimation(within, False).score == 1.0
    over_hours = ES.replace("Total de horas:** 120", "Total de horas:** 122")
    assert evaluate_estimation(over_hours, False).hours_match is False
    over_cost = ES.replace("Coste total:** 7500", "Coste total:** 7700")
    assert evaluate_estimation(over_cost, False).cost_match is False


def test_duration_needs_a_number_and_weeks():
    """Requirement: Evaluación estructural (duración con un número y semanas)."""
    no_number = ES.replace("4-5 semanas", "unas semanas")
    no_unit = ES.replace("4-5 semanas", "4-5 meses")
    assert evaluate_estimation(no_number, False).has_duration is False
    assert evaluate_estimation(no_unit, False).has_duration is False


def test_issues_are_english_and_never_reproduce_estimation_text():
    """Requirement: Resultado de la evaluación (issues en inglés sin texto de la estimación)."""
    secret = "SECRETO-UNICO-XYZ"
    text = ES.replace("Backend", secret).replace("Web de reservas", secret)
    text = text.replace("Total de horas:** 120", "Total de horas:** 130").replace(
        "Equipo recomendado:** 2 desarrolladores", f"Equipo:** {secret}"
    )
    check = evaluate_estimation(text, truncated=True)
    assert check.issues
    assert all(secret not in issue for issue in check.issues)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19500", 19500),
        ("19.500", 19500),
        ("19,500", 19500),
        ("937,50", 937.5),
        ("937.5", 937.5),
        ("1.937,50 EUR", 1937.5),
    ],
)
def test_number_formats(raw, expected):
    """Requirement: Lectura de números / Scenario: Formatos de número."""
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**120 h**", 120),
        ("7 500 €", 7500),
        ("1,937.50", 1937.5),
        ("3.125,00 EUR", 3125),
        ("1.234.567", 1234567),
    ],
)
def test_number_reading_ignores_units_spaces_and_asterisks(raw, expected):
    """Requirement: Lectura de números (ignora EUR, €, h, espacios y asteriscos)."""
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "EUR"])
def test_unreadable_number_is_none(raw):
    """Requirement: Lectura de números (valor ilegible)."""
    assert parse_number(raw) is None


@pytest.mark.parametrize("example", ESTIMATION_EXAMPLES, ids=lambda e: e["title"][:30])
def test_reference_examples_score_one(example):
    """Requirement: Resultado de la evaluación / Scenario: Ejemplos de referencia."""
    check = evaluate_estimation(example["estimation"], truncated=False)
    assert check.score == 1.0
    assert check.issues == ()


@pytest.mark.parametrize(
    ("hours_label", "cost_label"),
    [
        ("Hours total", "Cost total"),
        ("Total hours", "Cost total"),
        ("Hours total", "Total cost"),
        ("Total de horas", "Coste total"),
    ],
)
def test_calqued_total_labels_are_accepted(hours_label, cost_label):
    """Requirement: Evaluación estructural / Scenario: Etiquetas de totales (Cost/Hours total)."""
    text = EN.replace("Total hours:", f"{hours_label}:").replace("Total cost:", f"{cost_label}:")
    check = evaluate_estimation(text, truncated=False)
    assert (check.declared_total_hours, check.declared_total_cost) == (120, 7500)
    assert (check.has_totals, check.hours_match, check.cost_match) == (True, True, True)
    assert check.score == 1.0


def test_cost_total_label_with_european_decimal_number_parses():
    """Requirement: Lectura de números / Scenario: Formatos de número (Cost total)."""
    text = "## Estimate: x\n### Totals\n- **Hours total:** 100\n- **Cost total:** 25312,50 EUR\n"
    check = evaluate_estimation(text, truncated=False)
    assert check.declared_total_cost == 25312.5
    assert check.declared_total_hours == 100
