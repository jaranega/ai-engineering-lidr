"""CAG examples as structured objects (spec: cag-prompt "Ejemplos de estimación estáticos",
structured-estimation "Render evaluable", estimation-evaluation "Ejemplos de referencia").
"""

import json

import pytest

from app.context.examples import ESTIMATION_EXAMPLES, PHASE_KEYS, PHASES, RATES_EUR_PER_HOUR
from app.domain import DetailLevel, OutputFormat, ProjectType
from app.estimation.compute import compute_estimate
from app.estimation.examples import example_payloads, example_result
from app.estimation.render import render_markdown
from app.estimation.schema import EstimationResult, response_model_for
from app.services.evaluation import evaluate_estimation

examples = pytest.mark.parametrize(
    "example", ESTIMATION_EXAMPLES, ids=[f"example-{i}" for i in range(len(ESTIMATION_EXAMPLES))]
)


def test_canonical_phases_in_order_with_both_labels():
    """Requirement: Ejemplos (lista canónica de fases en este orden)."""
    assert PHASE_KEYS == ("discovery", "setup", "development", "qa", "deployment")
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


def test_there_are_three_valid_in_scope_examples():
    """Scenario: Ejemplos válidos."""
    assert len(ESTIMATION_EXAMPLES) == 3
    valid_types = {t.value for t in ProjectType}
    assert {e["project_type"] for e in ESTIMATION_EXAMPLES} == {
        "web_saas",
        "mobile_app",
        "data_pipeline",
    }
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"].strip()
        assert example["project_type"] in valid_types
        assert example["project_type"] != ProjectType.INTERNAL_TOOL.value
        result = example_result(example)
        assert isinstance(result, EstimationResult) and result.in_scope


def test_examples_hold_data_only_no_amounts_or_rendered_text():
    """Requirement: Ejemplos (sin importes, totales ni texto renderizado)."""
    for example in ESTIMATION_EXAMPLES:
        assert not {"cost", "total_cost", "total_hours", "text", "estimate"} & set(example)
        assert len(example["narrative"]) == 2


@examples
def test_example_arithmetic(example):
    """Scenario: Aritmética de los ejemplos."""
    estimate = compute_estimate(example_result(example), "es")
    for row in estimate.rows:
        assert row.hours % 5 == 0
        assert row.cost_eur == row.hours * RATES_EUR_PER_HOUR[row.rate_profile]
    assert estimate.total_hours == sum(r.hours for r in estimate.rows)
    assert estimate.total_cost_eur == sum(r.cost_eur for r in estimate.rows)
    assert sum(p.hours for p in estimate.phases) == estimate.total_hours
    assert sum(p.cost_eur for p in estimate.phases) == estimate.total_cost_eur


@examples
def test_example_phases_and_confidences(example):
    """Scenario: Fases de los ejemplos."""
    used = {row["phase"] for row in example["rows"]}
    assert used <= set(PHASE_KEYS)
    assert set(example["phase_confidence"]) == used
    for confidence in example["phase_confidence"].values():
        assert 0 <= confidence <= 100 and confidence % 5 == 0


@examples
def test_example_narratives_have_no_figures(example):
    """Requirement: Ejemplos (narrativa en español sin cifras)."""
    assert not any(ch.isdigit() for text in example["narrative"] for ch in text)


def test_first_example_totals_are_320_hours_and_19500_eur():
    """Requirement: Verificar (totales del ejemplo 1: 320 h / 19500 EUR)."""
    estimate = compute_estimate(example_result(ESTIMATION_EXAMPLES[0]), "es")
    assert estimate.total_hours == 320
    assert estimate.total_cost_eur == 19500


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("level", list(DetailLevel))
@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_every_rendered_example_scores_one(fmt, level, language):
    """Scenario: Render evaluable / Ejemplos de referencia (score = 1.0 en cada combinación)."""
    for example in ESTIMATION_EXAMPLES:
        estimate = compute_estimate(example_result(example), language)
        text = render_markdown(estimate, fmt, level)
        check = evaluate_estimation(text, truncated=False, output_format=fmt)
        assert check.score == 1.0, (check.issues, text)
        assert check.issues == ()


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_example_payloads_json_validates_without_amounts(fmt):
    """Requirement: Ejemplos como objetos (JSON válido, sin importes)."""
    payloads = example_payloads(ESTIMATION_EXAMPLES, fmt)
    assert [p["project_type"] for p in payloads] == [e["project_type"] for e in ESTIMATION_EXAMPLES]
    for payload in payloads:
        data = json.loads(payload["output_json"])
        assert response_model_for("line_items", "medium").model_validate(data)
        assert not {"cost_eur", "total_cost_eur", "rate_eur_per_hour", "total_hours"} & set(data)
        assert "\\u" not in payload["output_json"]  # accents are not escaped
        assert ("narrative" in data) is (fmt is OutputFormat.NARRATIVE)


def test_example_payloads_omit_default_valued_fields():
    """Requirement: Ejemplos como objetos (sin los campos vacíos por defecto)."""
    data = json.loads(example_payloads(ESTIMATION_EXAMPLES[:1], "line_items")[0]["output_json"])
    assert all("assumptions" not in phase for phase in data["phases"])
    assert any("part_time" not in member for member in data["team"])
