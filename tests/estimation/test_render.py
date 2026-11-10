"""Deterministic Markdown render (spec: structured-estimation, "Render Markdown
determinista").
"""

import re

import pytest

from app.domain import DetailLevel, OutputFormat
from app.estimation.compute import compute_estimate
from app.estimation.render import LABELS, format_amount, render_markdown
from app.estimation.schema import EstimationResult
from tests.conftest import out_of_scope_payload, valid_result

COMBINATIONS = [(f, d, lang) for f in OutputFormat for d in DetailLevel for lang in ("es", "en")]

# Fragments that exist in one language only (the content of the fixture is English).
ONLY_ES = [
    "Estimación",
    "Totales",
    "Total de horas",
    "Coste",
    "Equipo recomendado",
    "Duración estimada",
    "semanas",
    "Supuestos",
    "Desarrollo",
    "Descubrimiento",
    "| Tarea |",
    "| Fase |",
    "Confianza",
    "Fuera de alcance",
]
ONLY_EN = [
    "Estimate:",
    "Totals",
    "Total hours",
    "Total cost",
    "Recommended team",
    "Estimated duration",
    "weeks",
    "Assumptions",
    "Development",
    "Discovery",
    "| Task |",
    "| Phase |",
    "Confidence",
    "Out of scope",
]


def _render(fmt, level="medium", language="en", **overrides) -> str:
    return render_markdown(compute_estimate(valid_result(**overrides), language), fmt, level)


def test_line_items_spanish_structure():
    """Requirement: Render (título, tabla de tareas, totales, supuestos y riesgos)."""
    text = _render("line_items", language="es")
    assert text.startswith("## Estimación: Clinic booking platform")
    assert "| Tarea | Horas | Coste (EUR) |" in text
    assert "### Totales" in text and "### Supuestos y riesgos" in text
    assert "- **Total de horas:** 320" in text
    assert "- **Coste total:** 19500 EUR" in text
    assert "- **Equipo recomendado:** 2 engineers + 1 engineers (part-time)" in text
    assert "- **Duración estimada:** 6-7 semanas" in text


def test_phases_table_english_has_english_labels_only():
    """Scenario: Etiquetas en inglés (phases_table, sin etiquetas en español)."""
    text = _render("phases_table", language="en")
    assert "## Estimate: Clinic booking platform" in text
    assert "| Phase | Hours | Cost (EUR) | Confidence (%) |" in text
    assert "### Totals" in text
    for fragment in ONLY_ES:
        assert fragment not in text, fragment


def test_phases_table_has_no_total_row():
    """Requirement: Render (la tabla por fases no tiene fila de total)."""
    text = _render("phases_table", language="es")
    table = [line for line in text.splitlines() if line.startswith("|")]
    assert len(table) == 2 + 5  # header, separator and the five phases
    assert not any("total" in line.lower() for line in table)


def test_line_items_table_has_one_row_per_task_without_total_row():
    """Requirement: Render (line_items: una fila por tarea)."""
    result = valid_result()
    text = render_markdown(compute_estimate(result, "en"), "line_items", "medium")
    table = [line for line in text.splitlines() if line.startswith("|")]
    assert len(table) == 2 + len(result.rows)


@pytest.mark.parametrize(("fmt", "level", "language"), COMBINATIONS)
def test_no_labels_of_the_other_language(fmt, level, language):
    """Requirement: Render (etiquetas en estimate.language, sin mezclar idiomas)."""
    text = _render(fmt, level, language)
    forbidden = ONLY_EN if language == "es" else ONLY_ES
    for fragment in forbidden:
        assert fragment not in text, (fmt, level, language, fragment)


def test_narrative_has_no_tables_and_figures_come_from_the_totals():
    """Scenario: Narrativa sin tablas."""
    result = valid_result()
    estimate = compute_estimate(result, "en")
    text = render_markdown(estimate, "narrative", "medium")
    assert "|" not in text
    for paragraph in result.narrative:
        assert paragraph in text
    assert "The work is organized in 5 phases:" in text
    assert "Development, 210 hours (13125 EUR)" in text
    assert "- **Total hours:** 320" in text and "- **Total cost:** 19500 EUR" in text
    assert "Main risks and assumptions: The SMS provider is pay per use." in text


def test_narrative_phases_sentence_in_spanish_and_singular():
    """Requirement: Render (párrafo de fases generado por el código, es y singular)."""
    es = _render("narrative", language="es")
    assert "El trabajo se organiza en 5 fases: Descubrimiento y diseño, 40 horas (2000 EUR)" in es
    data = valid_result(
        rows=[{"task": "a", "phase": "development", "rate_profile": "dev", "hours": 10}],
        phases=[{"phase": "development", "confidence_pct": 70}],
    )
    text = render_markdown(compute_estimate(data, "es"), "narrative", "medium")
    assert "El trabajo se organiza en una fase: Desarrollo, 10 horas (625 EUR)." in text


@pytest.mark.parametrize(
    ("value", "language", "expected"),
    [
        (19500.0, "es", "19500"),
        (19500.0, "en", "19500"),
        (937.5, "es", "937,50"),
        (937.5, "en", "937.50"),
        (1234567.25, "es", "1234567,25"),
        (1234567.25, "en", "1234567.25"),
        (0, "es", "0"),
    ],
)
def test_amount_separators(value, language, expected):
    """Requirement: Render (sin separador de miles; coma en es y punto en en)."""
    assert format_amount(value, language) == expected


def test_amounts_in_tables_follow_the_language_decimal_separator():
    """Requirement: Render (importes con coma decimal en español y punto en inglés)."""
    rows = [{"task": "a", "phase": "development", "rate_profile": "dev", "hours": 15}]
    phases = [{"phase": "development", "confidence_pct": 70}]
    result = valid_result(rows=rows, phases=phases)
    es = render_markdown(compute_estimate(result, "es"), "line_items", "medium")
    en = render_markdown(compute_estimate(result, "en"), "line_items", "medium")
    assert "| a | 15 | 937,50 |" in es and "- **Coste total:** 937,50 EUR" in es
    assert "| a | 15 | 937.50 |" in en and "- **Total cost:** 937.50 EUR" in en


def test_detailed_adds_assumptions_by_phase_only_when_detailed():
    """Requirement: Render (con detailed, ### Supuestos por fase)."""
    detailed = _render("line_items", "detailed", "es")
    assert "### Supuestos por fase" in detailed
    assert "  - Assumption of discovery" in detailed
    for level in ("summary", "medium"):
        assert "### Supuestos por fase" not in _render("line_items", level, "es")
    assert "### Assumptions by phase" in _render("phases_table", "detailed", "en")


def test_table_cells_escape_pipes_and_line_breaks():
    """Requirement: Render (el texto del modelo no rompe la tabla)."""
    data = valid_result()
    payload = data.model_dump(mode="json")
    payload["rows"][0]["task"] = "a | b\nc"
    estimate = compute_estimate(EstimationResult.model_validate(payload), "en")
    text = render_markdown(estimate, "line_items", "medium")
    assert "| a / b c |" in text


def test_render_is_deterministic():
    """Requirement: Render (determinista, sin LLM)."""
    assert _render("narrative", "detailed", "es") == _render("narrative", "detailed", "es")


def test_unknown_format_or_level_raises_value_error():
    """Requirement: Render (formato o nivel desconocido)."""
    estimate = compute_estimate(valid_result(), "en")
    with pytest.raises(ValueError):
        render_markdown(estimate, "pdf", "medium")
    with pytest.raises(ValueError):
        render_markdown(estimate, "line_items", "huge")


@pytest.mark.parametrize("fmt", list(OutputFormat))
@pytest.mark.parametrize("language", ["es", "en"])
def test_out_of_scope_text_has_notice_without_tables_or_amounts(fmt, language):
    """Requirement: Render (fuera de alcance: título, aviso con confianza, summary y supuestos)."""
    estimate = compute_estimate(valid_result(confidence_pct=15), language)
    text = render_markdown(estimate, fmt, "detailed")
    assert text.startswith(f"## {LABELS[language]['title']}: Clinic booking platform")
    assert LABELS[language]["out_of_scope"] in text
    assert "15 %)" in text if language == "es" else "15%)" in text
    assert estimate.summary in text
    assert estimate.assumptions_and_risks[0] in text
    assert "|" not in text and "EUR" not in text
    assert not re.search(r"\d+ (hours|horas)", text)


def test_out_of_scope_without_assumptions_omits_the_section():
    """Requirement: Render (fuera de alcance sin supuestos)."""
    result = EstimationResult.model_validate(out_of_scope_payload(assumptions_and_risks=[]))
    text = render_markdown(compute_estimate(result, "en"), "line_items", "medium")
    assert "Assumptions" not in text
