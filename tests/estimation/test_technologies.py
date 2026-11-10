"""Requirements: Tecnologías mencionadas por el cliente (structured-estimation) y
Tecnologías en los ejemplos (cag-prompt).
"""

import json

import pytest
from pydantic import ValidationError

from app.context.examples import ESTIMATION_EXAMPLES
from app.domain import OutputFormat
from app.estimation.compute import compute_estimate
from app.estimation.examples import example_payloads, example_result
from app.estimation.render import render_markdown
from app.estimation.schema import MAX_TECHNOLOGIES, EstimationResult
from tests.conftest import out_of_scope_payload, valid_payload, valid_result


def technologies(raw) -> list[str]:
    return EstimationResult.model_validate(valid_payload(technologies=raw)).technologies


def test_normaliser_trims_drops_blanks_and_long_texts_and_dedupes():
    """Scenario: Normalización de tecnologías."""
    assert technologies([" Stripe ", "", "stripe", "Twilio", "x" * 61]) == ["Stripe", "Twilio"]


def test_first_spelling_wins_on_duplicates():
    assert technologies(["Twilio", "TWILIO", "twilio"]) == ["Twilio"]


def test_a_text_of_exactly_60_characters_is_kept():
    assert technologies(["y" * 60]) == ["y" * 60]


def test_cap_of_15_keeps_the_first_ones():
    """Scenario: Tope por turno."""
    names = [f"Tech{n}" for n in range(20)]
    assert MAX_TECHNOLOGIES == 15
    assert technologies(names) == names[:15]


def test_missing_field_defaults_to_empty_list():
    """Scenario: Sin tecnologías."""
    payload = valid_payload()
    del payload["technologies"]
    assert EstimationResult.model_validate(payload).technologies == []


def test_normaliser_is_idempotent():
    once = technologies([" A ", "a", "B", ""])
    assert technologies(once) == once


def test_technologies_never_cause_a_validation_error():
    """Requirement: este campo nunca provoca un reintento de la llamada estructurada."""
    assert technologies(["", "  ", "z" * 500]) == []


def test_non_list_value_is_still_rejected():
    with pytest.raises(ValidationError):
        technologies("Stripe")


def test_technologies_sit_between_confidence_and_rows_and_are_optional():
    """Scenario: Posición de las tecnologías."""
    schema = EstimationResult.model_json_schema()
    names = list(schema["properties"])
    assert names.index("technologies") == names.index("confidence_pct") + 1
    assert names.index("technologies") < names.index("rows")
    assert "technologies" not in schema["required"]


def test_out_of_scope_keeps_the_technologies():
    """Scenario: Fuera de alcance conserva las tecnologías."""
    result = valid_result(**out_of_scope_payload(confidence_pct=10, technologies=["SAP"]))
    computed = compute_estimate(result, "en")
    assert computed.out_of_scope is True
    assert computed.technologies == ["SAP"]


def test_in_scope_copies_the_technologies_unchanged():
    computed = compute_estimate(valid_result(technologies=["Twilio", "Stripe"]), "en")
    assert computed.technologies == ["Twilio", "Stripe"]


@pytest.mark.parametrize("output_format", ["phases_table", "line_items", "narrative"])
@pytest.mark.parametrize("detail_level", ["summary", "medium", "detailed"])
def test_markdown_never_shows_technologies(output_format, detail_level):
    """Scenario: Sin render en el Markdown."""
    result = valid_result(technologies=["TECNOLOGIA-MARCADOR"])
    text = render_markdown(compute_estimate(result, "en"), output_format, detail_level)
    assert "TECNOLOGIA-MARCADOR" not in text


def test_markdown_is_identical_with_and_without_technologies():
    with_tech = compute_estimate(valid_result(technologies=["A", "B"]), "en")
    without = compute_estimate(valid_result(technologies=[]), "en")
    for fmt in ("phases_table", "line_items", "narrative"):
        assert render_markdown(with_tech, fmt, "medium") == render_markdown(without, fmt, "medium")


# --- Ejemplos ---


def by_type(project_type: str) -> dict:
    return next(e for e in ESTIMATION_EXAMPLES if e["project_type"] == project_type)


def test_example_technologies_per_project_type():
    """Scenario: Tecnologías del ejemplo."""
    assert by_type("data_pipeline")["technologies"] == ["Shopify", "Odoo", "Looker Studio"]
    assert by_type("web_saas")["technologies"] == []
    for example in ESTIMATION_EXAMPLES:
        result = example_result(example)
        assert isinstance(result, EstimationResult)
        assert result.technologies == example["technologies"]


def test_at_least_one_example_has_no_technologies():
    assert any(example["technologies"] == [] for example in ESTIMATION_EXAMPLES)


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_example_json_includes_technologies_only_when_asked(output_format):
    """Requirement: `technologies` solo en v4 (excluido por defecto, para v3)."""
    default = example_payloads(ESTIMATION_EXAMPLES, output_format)
    v4 = example_payloads(ESTIMATION_EXAMPLES, output_format, include_technologies=True)
    assert all("technologies" not in json.loads(p["output_json"]) for p in default)
    for payload in v4:
        data = json.loads(payload["output_json"])
        keys = list(data)
        assert keys[keys.index("confidence_pct") + 1] == "technologies"
