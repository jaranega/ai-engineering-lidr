"""Requirement: Metadata del proyecto (conversation-sessions)."""

import pytest
from pydantic import ValidationError

from app.estimation.compute import compute_estimate
from app.sessions import (
    MAX_METADATA_TECHNOLOGIES,
    ProjectMetadata,
    merge_technologies,
    update_project_metadata,
)
from tests.conftest import out_of_scope_payload, valid_result

SUMMARY = "Web platform to book appointments."


def estimate(**overrides):
    return compute_estimate(valid_result(**overrides), "en")


def test_first_turn_fills_every_field_from_the_estimate():
    """Scenario: Primer turno (equipo 2 + 1 a tiempo parcial = 3 personas)."""
    result = estimate(title="Clinic booking platform", technologies=["Twilio"], summary=SUMMARY)
    metadata = update_project_metadata(ProjectMetadata(), result)
    assert metadata == ProjectMetadata(
        project_name="Clinic booking platform",
        assumed_team_size=3,
        mentioned_technologies=["Twilio"],
        agreed_scope=SUMMARY,
    )


def test_technologies_accumulate_without_casefold_duplicates_first_spelling_wins():
    """Scenario: Acumulación sin duplicados."""
    current = ProjectMetadata(mentioned_technologies=["Twilio"])
    updated = update_project_metadata(current, estimate(technologies=["Stripe", "twilio"]))
    assert updated.mentioned_technologies == ["Twilio", "Stripe"]


def test_technologies_cap_drops_the_oldest():
    """Scenario: Tope de tecnologías."""
    current = ProjectMetadata(mentioned_technologies=[f"T{n}" for n in range(1, 31)])
    updated = update_project_metadata(current, estimate(technologies=["Nueva"]))
    assert len(updated.mentioned_technologies) == MAX_METADATA_TECHNOLOGIES == 30
    assert "T1" not in updated.mentioned_technologies
    assert updated.mentioned_technologies[-1] == "Nueva"
    assert updated.mentioned_technologies[0] == "T2"


def test_out_of_scope_returns_the_same_metadata():
    """Scenario: Fuera de alcance no actualiza."""
    current = ProjectMetadata(project_name="Old", assumed_team_size=2, agreed_scope="S")
    result = compute_estimate(
        valid_result(**out_of_scope_payload(technologies=["SAP"], confidence_pct=10)), "en"
    )
    assert result.out_of_scope
    assert update_project_metadata(current, result) == current


def test_scalars_are_overwritten_each_turn():
    current = ProjectMetadata(project_name="Old", assumed_team_size=9, agreed_scope="Old scope")
    team = [{"role": "engineers", "count": 4, "part_time": False}]
    updated = update_project_metadata(current, estimate(title="New title", team=team))
    assert (updated.project_name, updated.assumed_team_size) == ("New title", 4)
    assert updated.agreed_scope != "Old scope"


def test_update_is_pure_and_deterministic():
    current = ProjectMetadata(mentioned_technologies=["A"])
    result = estimate(technologies=["B"])
    assert update_project_metadata(current, result) == update_project_metadata(current, result)
    assert current.mentioned_technologies == ["A"]


def test_metadata_is_immutable():
    metadata = ProjectMetadata(project_name="x")
    with pytest.raises(ValidationError):
        metadata.project_name = "y"


def test_filled_fields_lists_only_fields_with_value_in_order():
    assert ProjectMetadata().filled_fields() == []
    assert ProjectMetadata(assumed_team_size=0, agreed_scope="s").filled_fields() == [
        "assumed_team_size",
        "agreed_scope",
    ]


def test_merge_technologies_limit_and_order():
    assert merge_technologies(["a", "b"], ["B", "c"], 10) == ["a", "b", "c"]
    assert merge_technologies(["a", "b"], ["c"], 2) == ["b", "c"]
