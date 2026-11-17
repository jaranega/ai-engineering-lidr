"""conversation-sessions: Metadata del proyecto, reglas de memoria (s05-memory D8)."""

import pytest

from app.estimation.compute import compute_estimate
from app.sessions import ProjectMetadata, update_project_metadata
from app.sessions.metadata import (
    MAX_METADATA_CONSTRAINTS,
    MAX_METADATA_REJECTED_OPTIONS,
    merge_technologies,
    merge_unique,
)
from tests.conftest import out_of_scope_payload, valid_result


def estimate(**overrides):
    return compute_estimate(valid_result(**overrides), "en")


def test_metadata_fields_are_in_the_documented_order():
    assert list(ProjectMetadata.model_fields) == [
        "project_name",
        "assumed_team_size",
        "mentioned_technologies",
        "agreed_scope",
        "explicit_constraints",
        "rejected_options",
    ]
    assert ProjectMetadata().filled_fields() == []
    assert ProjectMetadata(explicit_constraints=["x"]).filled_fields() == ["explicit_constraints"]


def test_merge_technologies_is_an_alias_of_merge_unique():
    assert merge_technologies is merge_unique


def test_first_turn_has_empty_new_lists():
    """Scenario: Primer turno."""
    metadata = update_project_metadata(ProjectMetadata(), estimate(technologies=["Twilio"]))
    assert metadata.explicit_constraints == [] and metadata.rejected_options == []


def test_constraints_accumulate_without_duplicates():
    """Scenario: Restricciones acumuladas."""
    current = ProjectMetadata(explicit_constraints=["NDA firmado"])
    updated = update_project_metadata(
        current, estimate(explicit_constraints=["Presupuesto máximo de 40 000 €", "nda firmado"])
    )
    assert updated.explicit_constraints == ["NDA firmado", "Presupuesto máximo de 40 000 €"]


def test_duplicates_with_trailing_punctuation_are_merged():
    """Scenario: Duplicados con puntuación final."""
    current = ProjectMetadata(explicit_constraints=["Fixed budget of $45k"])
    updated = update_project_metadata(
        current, estimate(explicit_constraints=["Fixed budget of $45k."])
    )
    assert updated.explicit_constraints == ["Fixed budget of $45k"]


@pytest.mark.parametrize(
    "duplicate",
    [
        "Fixed budget of $45k.",
        "Fixed budget of $45k;",
        "Fixed budget of $45k:",
        "  fixed   budget of $45k . ",
        "FIXED BUDGET\tOF\n$45K",
    ],
)
def test_merge_unique_normalizes_whitespace_case_and_trailing_punctuation(duplicate):
    assert merge_unique(["Fixed budget of $45k"], [duplicate], 15) == ["Fixed budget of $45k"]


def test_merge_unique_keeps_the_first_spelling():
    assert merge_unique([], ["NDA signed.", "nda signed"], 15) == ["NDA signed."]


def test_merge_unique_keeps_inner_punctuation_and_other_trailing_marks():
    merged = merge_unique(["Budget: 40k"], ["Budget 40k", "Deadline?", "Deadline"], 15)
    assert merged == ["Budget: 40k", "Budget 40k", "Deadline?", "Deadline"]


def test_rejected_and_technologies_use_the_same_normalization():
    current = ProjectMetadata(mentioned_technologies=["Firebase", "Stripe"])
    updated = update_project_metadata(
        current, estimate(technologies=["Stripe"], rejected_options=["Firebase."])
    )
    assert updated.rejected_options == ["Firebase."]
    assert updated.mentioned_technologies == ["Stripe"]


def test_readoption_uses_the_same_normalization():
    current = ProjectMetadata(rejected_options=["Firebase."])
    updated = update_project_metadata(
        current, estimate(technologies=["firebase"], rejected_options=[])
    )
    assert updated.rejected_options == []


def test_constraints_are_capped_at_15_dropping_the_oldest():
    """Scenario: Tope de restricciones."""
    current = ProjectMetadata(explicit_constraints=[f"R{n}" for n in range(1, 16)])
    updated = update_project_metadata(current, estimate(explicit_constraints=["Nueva"]))
    assert MAX_METADATA_CONSTRAINTS == 15
    assert len(updated.explicit_constraints) == 15
    assert "R1" not in updated.explicit_constraints and updated.explicit_constraints[-1] == "Nueva"


def test_rejected_options_are_capped_at_20():
    current = ProjectMetadata(rejected_options=[f"O{n}" for n in range(1, 21)])
    updated = update_project_metadata(current, estimate(rejected_options=["Nueva"]))
    assert MAX_METADATA_REJECTED_OPTIONS == 20
    assert len(updated.rejected_options) == 20 and "O1" not in updated.rejected_options


def test_forgetting_policy_removes_rejected_technologies_by_casefold():
    """Scenario: Política de olvido."""
    current = ProjectMetadata(mentioned_technologies=["Firebase", "Stripe"])
    updated = update_project_metadata(
        current, estimate(technologies=["Supabase", "Stripe"], rejected_options=["firebase"])
    )
    assert updated.rejected_options == ["firebase"]
    assert updated.mentioned_technologies == ["Stripe", "Supabase"]


def test_rejecting_never_removes_by_substring():
    """Scenario: Sin retirar por subcadena."""
    current = ProjectMetadata(mentioned_technologies=["Android"])
    updated = update_project_metadata(current, estimate(rejected_options=["app Android"]))
    assert "Android" in updated.mentioned_technologies


def test_readoption_removes_the_name_from_rejected_options():
    """Scenario: Readopción."""
    current = ProjectMetadata(rejected_options=["Firebase"])
    updated = update_project_metadata(
        current, estimate(technologies=["Firebase"], rejected_options=[])
    )
    assert updated.rejected_options == []
    assert "Firebase" in updated.mentioned_technologies


def test_using_and_rejecting_in_the_same_turn_rejection_wins():
    """Scenario: Descarte y adopción en el mismo turno."""
    updated = update_project_metadata(
        ProjectMetadata(), estimate(technologies=["Firebase"], rejected_options=["Firebase"])
    )
    assert updated.rejected_options == ["Firebase"]
    assert "Firebase" not in updated.mentioned_technologies


def test_out_of_scope_changes_nothing():
    """Scenario: Fuera de alcance no actualiza."""
    current = ProjectMetadata(project_name="P", explicit_constraints=["A"], rejected_options=["B"])
    result = compute_estimate(
        valid_result(
            **out_of_scope_payload(
                confidence_pct=10, explicit_constraints=["Z"], rejected_options=["Y"]
            )
        ),
        "en",
    )
    assert update_project_metadata(current, result) == current


@pytest.mark.parametrize(
    ("current", "new", "limit", "expected"),
    [
        (["a"], ["A", "b"], 5, ["a", "b"]),
        (["a", "b"], ["c"], 2, ["b", "c"]),
        ([" x "], ["X"], 5, [" x "]),
        (["a"], ["b"], 0, []),
    ],
)
def test_merge_unique(current, new, limit, expected):
    assert merge_unique(current, new, limit) == expected
