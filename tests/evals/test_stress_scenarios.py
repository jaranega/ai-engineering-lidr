"""cag-stress-test / Escenarios de carga con fact-tracker. Pure data, no network."""

import pytest

from evals.stress.scenarios import (
    ATTACHMENTS,
    CONTRADICTION,
    GROWING,
    PIVOT,
    SCENARIOS,
    facts_at,
    get_scenario,
    select_turns,
)


def test_profiles_have_twenty_turns_indexed_one_to_twenty_and_attachments_one():
    """Scenario: Perfiles de 20 turnos."""
    for scenario in (GROWING, PIVOT, CONTRADICTION):
        assert [t.index for t in scenario.turns] == list(range(1, 21)), scenario.name
    assert len(ATTACHMENTS.turns) == 1
    assert set(SCENARIOS) == {"growing", "pivot", "contradiction", "attachments"}


def test_languages_follow_the_spec():
    """Requirement: growing/contradiction/attachments en español, pivot en inglés."""
    assert (GROWING.language, PIVOT.language) == ("es", "en")
    assert (CONTRADICTION.language, ATTACHMENTS.language) == ("es", "es")


def test_growing_first_turn_introduces_nimbus_react_postgres():
    """Requirement: growing (turno 1)."""
    first = GROWING.turns[0]
    assert {"Nimbus", "React", "Postgres"} <= set(first.facts)
    assert all(fact in first.transcript for fact in ("Nimbus", "React", "Postgres"))


def test_contradiction_facts_at_turn_ten_include_80k_and_not_30k():
    """Scenario: Hechos vigentes de la contradicción."""
    facts = facts_at(CONTRADICTION, 10)
    assert "80.000" in facts and "30.000" not in facts


@pytest.mark.parametrize("turn, expected", [(3, "30.000"), (7, "30.000"), (8, "80.000")])
def test_contradiction_budget_in_force_per_turn(turn, expected):
    """Requirement: 30.000 en los turnos 3-7 y 80.000 desde el 8."""
    facts = facts_at(CONTRADICTION, turn)
    other = "80.000" if expected == "30.000" else "30.000"
    assert expected in facts and other not in facts


def test_contradiction_has_no_budget_fact_before_turn_three():
    assert not {"30.000", "80.000"} & set(facts_at(CONTRADICTION, 2))
    assert "30.000 €" in CONTRADICTION.turns[2].transcript
    assert "80.000 €" in CONTRADICTION.turns[7].transcript


def test_pivot_turn_five_mentions_flutter_and_discards_react_native():
    """Scenario: Pivote."""
    turn = PIVOT.turns[4]
    assert turn.index == 5
    assert "Flutter" in turn.transcript and "React Native" in turn.transcript
    assert "Flutter" in turn.facts
    assert "React Native" in turn.retracts


def test_pivot_react_native_is_required_in_turns_one_to_four_only():
    """Requirement: pivot (React Native vigente 1-4, no exigido desde el 5)."""
    assert all("React Native" in facts_at(PIVOT, n) for n in (1, 2, 3, 4))
    assert all("React Native" not in facts_at(PIVOT, n) for n in (5, 10, 20))
    assert "Flutter" in facts_at(PIVOT, 20)


def test_facts_accumulate_in_growing():
    assert set(facts_at(GROWING, 1)) <= set(facts_at(GROWING, 20))


@pytest.mark.parametrize("n", [1, 3, 20])
def test_select_turns_returns_the_first_n(n):
    """Requirement: `select_turns(scenario, n)` devuelve los n primeros."""
    assert select_turns(GROWING, n) == GROWING.turns[:n]


def test_select_turns_rejects_zero_and_unknown_scenario_fails():
    with pytest.raises(ValueError):
        select_turns(GROWING, 0)
    with pytest.raises(KeyError):
        get_scenario("nope")
