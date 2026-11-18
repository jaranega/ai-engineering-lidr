"""structured-estimation "Restricciones explícitas y opciones descartadas" y cag-prompt
"Restricciones y descartes en los ejemplos".
"""

import json

import pytest
from pydantic import ValidationError

from app.context.examples import ESTIMATION_EXAMPLES
from app.domain import OutputFormat
from app.estimation.compute import compute_estimate
from app.estimation.examples import example_payloads, example_result
from app.estimation.render import render_markdown
from app.estimation.schema import EstimationResult, response_model_for
from tests.conftest import out_of_scope_payload, valid_payload, valid_result


def constraints(raw) -> list[str]:
    return EstimationResult.model_validate(
        valid_payload(explicit_constraints=raw)
    ).explicit_constraints


def rejected(raw) -> list[str]:
    return EstimationResult.model_validate(valid_payload(rejected_options=raw)).rejected_options


def test_constraints_are_normalised_without_a_retry():
    """Scenario: Normalización de restricciones."""
    raw = [" NDA firmado ", "", "nda firmado", "x" * 201, "Presupuesto máximo de 40 000 €"]
    assert constraints(raw) == ["NDA firmado", "Presupuesto máximo de 40 000 €"]


def test_a_constraint_of_exactly_200_characters_is_kept():
    assert constraints(["c" * 200]) == ["c" * 200]


def test_constraints_cap_at_eight_keeping_the_first_ones():
    names = [f"Restricción {n}" for n in range(12)]
    assert constraints(names) == names[:8]


def test_rejected_options_cap_at_ten_per_turn():
    """Scenario: Tope de descartes por turno."""
    names = [f"Opción{n}" for n in range(12)]
    assert rejected(names) == names[:10]


def test_rejected_options_drop_texts_over_60_characters_and_dedupe_by_casefold():
    assert rejected([" Firebase ", "firebase", "y" * 61, "Heroku"]) == ["Firebase", "Heroku"]


def test_missing_fields_default_to_empty_lists():
    """Scenario: Sin los campos."""
    payload = valid_payload()
    payload.pop("explicit_constraints", None)
    payload.pop("rejected_options", None)
    result = EstimationResult.model_validate(payload)
    assert (result.explicit_constraints, result.rejected_options) == ([], [])


@pytest.mark.parametrize("field", ["explicit_constraints", "rejected_options"])
def test_normalisers_are_idempotent(field):
    once = EstimationResult.model_validate(
        valid_payload(**{field: [" A ", "a", "B", ""]})
    ).model_dump()[field]
    again = EstimationResult.model_validate(valid_payload(**{field: once})).model_dump()[field]
    assert once == again == ["A", "B"]


@pytest.mark.parametrize("field", ["explicit_constraints", "rejected_options"])
def test_the_new_fields_never_cause_a_validation_error_but_non_lists_do(field):
    assert EstimationResult.model_validate(valid_payload(**{field: ["", "  ", "z" * 500]}))
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(valid_payload(**{field: "NDA"}))


def test_schema_order_and_optionality_hold_for_every_response_model():
    for fmt in OutputFormat:
        schema = response_model_for(fmt, "medium").model_json_schema()
        names = list(schema["properties"])
        start = names.index("confidence_pct")
        assert names[start + 1 : start + 4] == [
            "explicit_constraints",
            "rejected_options",
            "technologies",
        ]
        assert not {"explicit_constraints", "rejected_options"} & set(schema["required"])


def test_out_of_scope_keeps_both_fields():
    """Scenario: Fuera de alcance conserva los campos."""
    result = valid_result(
        **out_of_scope_payload(
            confidence_pct=10, explicit_constraints=["NDA firmado"], rejected_options=["Firebase"]
        )
    )
    computed = compute_estimate(result, "en")
    assert computed.out_of_scope is True
    assert computed.explicit_constraints == ["NDA firmado"]
    assert computed.rejected_options == ["Firebase"]


def test_in_scope_copies_both_fields_unchanged():
    computed = compute_estimate(
        valid_result(explicit_constraints=["A"], rejected_options=["B"]), "en"
    )
    assert (computed.explicit_constraints, computed.rejected_options) == (["A"], ["B"])


@pytest.mark.parametrize("output_format", ["phases_table", "line_items", "narrative"])
@pytest.mark.parametrize("detail_level", ["summary", "medium", "detailed"])
def test_markdown_never_shows_the_new_fields(output_format, detail_level):
    """Scenario: Sin render en el Markdown."""
    result = valid_result(
        explicit_constraints=["RESTRICCION-MARCADOR"], rejected_options=["DESCARTE-MARCADOR"]
    )
    text = render_markdown(compute_estimate(result, "en"), output_format, detail_level)
    assert "RESTRICCION-MARCADOR" not in text and "DESCARTE-MARCADOR" not in text


def test_markdown_is_identical_with_and_without_the_new_fields():
    full = compute_estimate(valid_result(explicit_constraints=["A"], rejected_options=["B"]), "en")
    empty = compute_estimate(valid_result(), "en")
    for fmt in ("phases_table", "line_items", "narrative"):
        assert render_markdown(full, fmt, "medium") == render_markdown(empty, fmt, "medium")


# --- Ejemplos ---


def test_examples_carry_constraints_and_one_is_empty_and_one_is_not():
    assert any(e["explicit_constraints"] for e in ESTIMATION_EXAMPLES)
    assert any(not e["explicit_constraints"] for e in ESTIMATION_EXAMPLES)


def test_every_example_is_a_valid_result_that_keeps_its_fields():
    """Scenario: Ejemplos válidos."""
    for example in ESTIMATION_EXAMPLES:
        result = example_result(example)
        assert isinstance(result, EstimationResult)
        assert result.explicit_constraints == example["explicit_constraints"]
        assert result.rejected_options == example["rejected_options"]


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_example_json_has_the_memory_fields_only_with_the_flag(output_format):
    default = example_payloads(ESTIMATION_EXAMPLES, output_format)
    v4 = example_payloads(ESTIMATION_EXAMPLES, output_format, include_technologies=True)
    for payload in (*default, *v4):
        data = json.loads(payload["output_json"])
        assert "explicit_constraints" not in data and "rejected_options" not in data
    v5 = example_payloads(
        ESTIMATION_EXAMPLES, output_format, include_technologies=True, include_memory_fields=True
    )
    for payload in v5:
        data = json.loads(payload["output_json"])
        keys = list(data)
        start = keys.index("confidence_pct")
        assert keys[start + 1 : start + 4] == [
            "explicit_constraints",
            "rejected_options",
            "technologies",
        ]
        assert isinstance(data["explicit_constraints"], list)  # present also when empty
        assert isinstance(data["rejected_options"], list)
