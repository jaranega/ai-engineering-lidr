"""offline-evals: golden dataset (Golden dataset tipado). Offline, no LLM."""

import json
from collections import Counter

import pytest
from pydantic import ValidationError

from app.guardrails.input import screen_texts
from app.sessions import ProjectMetadata
from app.sessions.tier import Tier, resolve_tier
from evals.dataset import DatasetError, GoldenCase, load_dataset

TRANSCRIPT = "We need a booking platform for a small clinic with reminders by SMS."


def case_dict(**overrides) -> dict:
    return {
        "id": "case-001-ok",
        "language": "en",
        "transcript": TRANSCRIPT,
        "project_type": "web_saas",
        **overrides,
    }


def write(tmp_path, entries) -> str:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return str(path)


# --- Dataset por defecto ---


def test_default_dataset_has_16_cases_with_unique_ids_and_8_per_language():
    """Scenario: Dataset por defecto."""
    cases = load_dataset()
    assert len(cases) == 16
    assert len({c.id for c in cases}) == 16
    assert Counter(c.language for c in cases) == {"es": 8, "en": 8}


def test_default_dataset_covers_the_four_project_types():
    assert {c.project_type.value for c in load_dataset()} == {
        "mobile_app",
        "web_saas",
        "internal_tool",
        "data_pipeline",
    }


def test_default_dataset_covers_the_required_tiers_and_flags():
    cases = load_dataset()
    executive = [c for c in cases if c.expected_tier == Tier.EXECUTIVE]
    assert len(executive) >= 2
    nda = [c for c in executive if "nda" in c.transcript.lower()]
    regulation = [c for c in executive if c not in nda]
    assert nda and regulation  # NDA and regulation cases
    assert any(c.expected_tier == Tier.DEVELOPER for c in cases)
    assert any(c.expected_tier == Tier.PM for c in cases)
    assert sum(c.expected_out_of_scope for c in cases) >= 1
    assert sum(c.expected_blocked for c in cases) >= 1


def test_default_dataset_ids_follow_the_pattern_and_the_default_fields():
    for case in load_dataset():
        assert case.id.startswith("case-")
        assert case.detail_level.value in {"summary", "medium", "detailed"}


def test_expected_tiers_agree_with_resolve_tier_on_a_single_turn():
    """The expected tier of each case is what the rules give for one turn (empty metadata,
    explicit tier when the case sets one)."""
    checked = 0
    for case in load_dataset():
        if case.expected_tier is None:
            continue
        explicit = None if case.tier == "auto" else Tier(case.tier)
        decision = resolve_tier(case.transcript, ProjectMetadata(), explicit)
        assert decision.tier == case.expected_tier, case.id
        checked += 1
    assert checked >= 4


def test_no_golden_case_except_the_blocked_one_trips_the_input_guardrails():
    for case in load_dataset():
        hit = screen_texts([("transcript", case.transcript)])
        if case.expected_blocked:
            assert hit is not None and hit.reason == "prompt_injection", case.id
        else:
            assert hit is None, f"{case.id} is blocked by {hit}"


def test_ranges_of_the_default_dataset_are_consistent():
    for case in load_dataset():
        for name in (
            "expected_hours_range",
            "expected_cost_range_eur",
            "expected_duration_weeks_range",
            "expected_phase_count_range",
        ):
            value = getattr(case, name)
            if value is not None:
                assert value[0] <= value[1], (case.id, name)
        if case.expected_blocked or case.expected_out_of_scope:
            assert case.expected_hours_range is None and case.expected_cost_range_eur is None


# --- Validación ---


def test_inverted_range_is_invalid():
    """Scenario: Rango invertido."""
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(case_dict(expected_hours_range=[500, 100]))


@pytest.mark.parametrize(
    "name",
    [
        "expected_hours_range",
        "expected_cost_range_eur",
        "expected_duration_weeks_range",
        "expected_phase_count_range",
    ],
)
@pytest.mark.parametrize("flag", ["expected_blocked", "expected_out_of_scope"])
def test_ranges_are_forbidden_with_blocked_or_out_of_scope(name, flag):
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(case_dict(**{flag: True, name: [1, 2]}))


def test_blocked_and_out_of_scope_without_ranges_are_valid():
    assert GoldenCase.model_validate(case_dict(expected_blocked=True)).expected_blocked
    assert GoldenCase.model_validate(case_dict(expected_out_of_scope=True)).expected_out_of_scope


def test_defaults_of_a_minimal_case():
    case = GoldenCase.model_validate(case_dict())
    assert case.detail_level.value == "medium" and case.output_format.value == "line_items"
    assert case.tier == "auto" and case.expected_tier is None
    assert (case.expected_blocked, case.expected_out_of_scope) == (False, False)
    assert case.expected_in_summary == [] and case.expected_technologies_any_of == []


@pytest.mark.parametrize("bad_id", ["case-1-x", "CASE-001-x", "case-001", "x-001-y", "case-001-A"])
def test_id_must_match_the_pattern(bad_id):
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(case_dict(id=bad_id))


@pytest.mark.parametrize("transcript", ["short", "x" * 20_001])
def test_transcript_length_limits(transcript):
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(case_dict(transcript=transcript))


def test_tier_accepts_auto_or_a_tier_only():
    assert GoldenCase.model_validate(case_dict(tier="pm")).tier == Tier.PM
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(case_dict(tier="ceo"))


# --- load_dataset ---


def test_duplicate_id_fails_naming_the_id(tmp_path):
    """Scenario: Id repetido."""
    path = write(tmp_path, [case_dict(), case_dict(language="es")])
    with pytest.raises(DatasetError) as caught:
        load_dataset(path)
    assert "case-001-ok" in str(caught.value)


def test_invalid_case_fails_naming_the_case(tmp_path):
    path = write(tmp_path, [case_dict(), case_dict(id="case-002-bad", expected_hours_range=[9, 1])])
    with pytest.raises(DatasetError) as caught:
        load_dataset(path)
    assert "case-002-bad" in str(caught.value)


def test_valid_custom_dataset_loads(tmp_path):
    cases = load_dataset(write(tmp_path, [case_dict(), case_dict(id="case-002-two")]))
    assert [c.id for c in cases] == ["case-001-ok", "case-002-two"]


@pytest.mark.parametrize("content", ["not json", '{"a": 1}'])
def test_unreadable_or_non_list_dataset_is_a_dataset_error(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(DatasetError):
        load_dataset(path)


def test_missing_file_is_a_dataset_error(tmp_path):
    with pytest.raises(DatasetError):
        load_dataset(tmp_path / "missing.json")


def test_unknown_fields_are_rejected(tmp_path):
    with pytest.raises(DatasetError):
        load_dataset(write(tmp_path, [case_dict(surprise=1)]))
