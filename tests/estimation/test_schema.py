"""Schema of the structured estimation (spec: structured-estimation, "Schema de la
estimación estructurada").
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain import DetailLevel, OutputFormat
from app.estimation.schema import EstimationResult, response_model_for
from tests.conftest import out_of_scope_payload, valid_payload

COMBINATIONS = [(f, d) for f in OutputFormat for d in DetailLevel]


def _payload(**overrides) -> dict:
    return valid_payload(**overrides)


def test_hours_and_confidences_are_normalised_without_retry():
    """Scenario: Normalización sin reintento (12 h -> 10 h, confianza 72 -> 70)."""
    data = _payload()
    data["rows"][0]["hours"] = 12
    data["phases"][0]["confidence_pct"] = 72
    result = EstimationResult.model_validate(data)
    assert result.rows[0].hours == 10
    assert result.phases[0].confidence_pct == 70


def test_minimum_hours_after_normalisation_is_five():
    """Requirement: Schema (horas mínimas de 5 tras la normalización)."""
    data = _payload()
    data["rows"][0]["hours"] = 1
    assert EstimationResult.model_validate(data).rows[0].hours == 5


def test_normalisation_is_idempotent():
    """Requirement: Schema (la normalización es idempotente)."""
    data = _payload()
    data["rows"][0]["hours"] = 13
    data["confidence_pct"] = 67
    first = EstimationResult.model_validate(data)
    second = EstimationResult.model_validate(first.model_dump(mode="json"))
    assert second == first


def test_blank_texts_are_dropped_and_others_trimmed():
    """Requirement: Schema (se recortan los espacios y se quitan los textos vacíos)."""
    result = EstimationResult.model_validate(
        _payload(
            title="  Title  ",
            assumptions_and_risks=["  one  ", "   ", ""],
            narrative=["  paragraph  ", " "],
        )
    )
    assert result.title == "Title"
    assert result.assumptions_and_risks == ["one"]
    assert result.narrative == ["paragraph"]


def test_incoherent_phases_fail_validation():
    """Scenario: Fases incoherentes (filas en development y qa, phases solo development)."""
    data = _payload()
    data["phases"] = [p for p in data["phases"] if p["phase"] == "development"]
    with pytest.raises(ValidationError, match="exactly one entry per phase"):
        EstimationResult.model_validate(data)


def test_repeated_phases_fail_validation():
    """Requirement: Schema (phases sin fases repetidas)."""
    data = _payload()
    first, *rest = data["phases"]
    data["phases"] = [first, dict(first), *rest[:2]]
    with pytest.raises(ValidationError, match="must not repeat"):
        EstimationResult.model_validate(data)


def test_in_scope_without_rows_fails_validation():
    """Scenario: En alcance sin filas (confidence 60 y rows vacío)."""
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(
            _payload(confidence_pct=60, rows=[], phases=[], team=[], duration_weeks=None)
        )


@pytest.mark.parametrize("field", ["team", "assumptions_and_risks", "duration_weeks"])
def test_in_scope_requires_team_assumptions_and_duration(field):
    """Requirement: Schema (en alcance: rows, team, assumptions_and_risks, duration_weeks)."""
    empty = None if field == "duration_weeks" else []
    with pytest.raises(ValidationError, match=field):
        EstimationResult.model_validate(_payload(**{field: empty}))


def test_out_of_scope_without_breakdown_is_valid():
    """Scenario: Fuera de alcance sin desglose (confidence 10)."""
    result = EstimationResult.model_validate(out_of_scope_payload())
    assert not result.in_scope
    assert result.rows == [] and result.duration_weeks is None


def test_threshold_30_is_in_scope():
    """Requirement: Schema (si confidence_pct >= 30 se exige el desglose)."""
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(out_of_scope_payload(confidence_pct=30))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("title",), ""),
        (("title",), "x" * 121),
        (("summary",), "x" * 601),
        (("confidence_pct",), 101),
        (("language",), "fr"),
        (("rows", 0, "phase"), "marketing"),
        (("rows", 0, "rate_profile"), "senior"),
        (("rows", 0, "hours"), 0),
        (("rows", 0, "hours"), 2001),
        (("rows", 0, "task"), "x" * 201),
        (("team", 0, "count"), 0),
        (("team", 0, "count"), 21),
        (("duration_weeks",), {"min_weeks": 0, "max_weeks": 3}),
        (("duration_weeks",), {"min_weeks": 5, "max_weeks": 3}),
        (("duration_weeks",), {"min_weeks": 1, "max_weeks": 105}),
    ],
)
def test_range_length_and_value_violations_fail_validation(path, value):
    """Requirement: Schema (rangos, longitudes y valores admitidos provocan reintento)."""
    data = _payload()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(data)


def test_list_size_limits():
    """Requirement: Schema (máximos de filas, equipo, supuestos y narrativa)."""
    base = _payload()
    too_many_rows = [dict(base["rows"][0]) for _ in range(61)]
    with pytest.raises(ValidationError):
        EstimationResult.model_validate({**base, "rows": too_many_rows})
    with pytest.raises(ValidationError):
        EstimationResult.model_validate({**base, "team": [base["team"][0]] * 9})
    with pytest.raises(ValidationError):
        EstimationResult.model_validate({**base, "assumptions_and_risks": ["a"] * 9})
    with pytest.raises(ValidationError):
        EstimationResult.model_validate({**base, "narrative": ["a"] * 7})
    data = _payload()
    data["phases"][0]["assumptions"] = ["a", "b", "c", "d"]
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(data)


def _property_names(node) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        names.update(node.get("properties", {}))
        for value in node.values():
            names |= _property_names(value)
    elif isinstance(node, list):
        for value in node:
            names |= _property_names(value)
    return names


def test_schema_has_no_amounts_rates_or_totals():
    """Requirement: Schema (no contiene importes, tarifas ni totales)."""
    names = _property_names(EstimationResult.model_json_schema())
    assert not {n for n in names if any(w in n for w in ("cost", "rate_eur", "total", "amount"))}


def test_fields_are_declared_in_generation_order():
    """Requirement: Schema (campos en este orden)."""
    assert list(EstimationResult.model_fields) == [
        "language",
        "title",
        "summary",
        "confidence_pct",
        "technologies",
        "rows",
        "phases",
        "team",
        "duration_weeks",
        "assumptions_and_risks",
        "narrative",
    ]


def test_schema_module_imports_no_instructor_litellm_or_services():
    """Requirement: Schema (el módulo no importa Instructor, LiteLLM ni app.services)."""
    import ast

    import app.estimation.schema as module

    tree = ast.parse(Path(module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in ("instructor", "litellm", "app.services", "app.prompts"):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in imported)


def test_narrative_detailed_requires_narrative_and_phase_assumptions():
    """Scenario: Requisitos por formato y nivel (falla sin narrative o sin supuestos)."""
    strict = response_model_for("narrative", "detailed")
    with pytest.raises(ValidationError, match="narrative"):
        strict.model_validate(_payload(narrative=[]))
    data = _payload()
    data["phases"][0]["assumptions"] = []
    with pytest.raises(ValidationError, match="assumptions"):
        strict.model_validate(data)
    assert strict.model_validate(_payload()).narrative


def test_same_result_passes_with_line_items_medium():
    """Scenario: Requisitos por formato y nivel (el mismo resultado pasa con line_items)."""
    data = _payload(narrative=[])
    for phase in data["phases"]:
        phase["assumptions"] = []
    with pytest.raises(ValidationError):
        response_model_for("narrative", "detailed").model_validate(data)
    assert response_model_for("line_items", "medium").model_validate(data)


def test_narrative_requirement_does_not_apply_out_of_scope():
    """Requirement: Schema (los requisitos solo se exigen en alcance)."""
    model = response_model_for("narrative", "detailed")
    assert model.model_validate(out_of_scope_payload()).in_scope is False


def test_all_nine_combinations_share_name_and_json_schema():
    """Scenario: Mismo JSON Schema (las 9 combinaciones son idénticas)."""
    models = [response_model_for(f, d) for f, d in COMBINATIONS]
    assert len(models) == 9
    assert {m.__name__ for m in models} == {"EstimationResult"}
    schemas = [m.model_json_schema() for m in models]
    assert all(s == schemas[0] for s in schemas)
    assert all(issubclass(m, EstimationResult) for m in models)


def test_response_model_for_accepts_strings_and_caches_per_requirement():
    """Requirement: Schema (response_model_for cacheado)."""
    assert response_model_for("line_items", "medium") is response_model_for(
        OutputFormat.LINE_ITEMS, DetailLevel.SUMMARY
    )
    with pytest.raises(ValueError):
        response_model_for("nope", "medium")
