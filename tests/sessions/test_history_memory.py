"""conversation-sessions: anchors, summary and backlog of `ConversationHistory` (s05-memory D3)."""

import pytest

from app.sessions.models import SUMMARY_MAX_CHARS, Anchor, ConversationHistory, Turn


def filled(max_turns: int, count: int, max_anchors: int = 3) -> ConversationHistory:
    history = ConversationHistory(max_turns, max_anchors)
    for n in range(1, count + 1):
        history.append(user=f"u{n}", assistant=f"a{n}")
    return history


def test_take_evicted_delivers_once_oldest_first():
    """Scenario: Expulsados entregados una vez."""
    history = filled(3, 5)
    assert [t.number for t in history.take_evicted()] == [1, 2]
    assert history.take_evicted() == []


def test_nothing_is_evicted_while_the_window_is_not_full():
    assert filled(3, 3).take_evicted() == []


def test_anchors_go_before_the_window_in_to_messages():
    """Scenario: Anclas antes de la ventana."""
    history = filled(2, 6)
    evicted = history.take_evicted()
    history.set_anchors([Anchor(turn=evicted[1], rules=("nda",))])  # turn 2
    messages = history.to_messages()
    assert [m["role"] for m in messages] == ["user", "assistant"] * 3
    assert [m["content"] for m in messages] == ["u2", "a2", "u5", "a5", "u6", "a6"]
    assert len(history) == 2  # `len` counts only the window


def test_the_summary_is_never_part_of_the_messages():
    history = filled(2, 4)
    history.take_evicted()
    history.set_summary("RESUMEN-UNICO", covered_turns=2)
    assert all("RESUMEN-UNICO" not in m["content"] for m in history.to_messages())
    assert all(m["role"] != "system" for m in history.to_messages())


@pytest.mark.parametrize(("max_turns", "max_anchors"), [(0, 0), (3, -1)])
def test_invalid_arguments_raise_value_error(max_turns, max_anchors):
    """Scenario: Ventana inválida."""
    with pytest.raises(ValueError):
        ConversationHistory(max_turns, max_anchors)


def test_default_max_anchors_is_zero():
    history = ConversationHistory(3)
    assert history.max_anchors == 0
    with pytest.raises(ValueError):
        history.set_anchors([Anchor(Turn(1, "u", "a"), ("nda",))])


def test_empty_messages_are_rejected():
    history = ConversationHistory(3)
    with pytest.raises(ValueError):
        history.append(user=" ", assistant="a")
    with pytest.raises(ValueError):
        history.append(user="u", assistant="")


def test_set_anchors_rejects_more_than_the_cap_and_disorder():
    history = filled(2, 6, max_anchors=2)
    evicted = history.take_evicted()
    anchors = [Anchor(t, ("nda",)) for t in evicted]
    with pytest.raises(ValueError):
        history.set_anchors(anchors[:3])
    with pytest.raises(ValueError):
        history.set_anchors([anchors[1], anchors[0]])
    history.set_anchors(anchors[:2])
    assert [a.turn.number for a in history.anchors] == [1, 2]


def test_anchors_must_be_older_than_the_window():
    history = filled(2, 4)
    with pytest.raises(ValueError):
        history.set_anchors([Anchor(history.turns[0], ("nda",))])


def test_set_summary_validates_the_text():
    history = filled(2, 2)
    with pytest.raises(ValueError):
        history.set_summary("  ", covered_turns=1)
    with pytest.raises(ValueError):
        history.set_summary("x" * (SUMMARY_MAX_CHARS + 1), covered_turns=1)
    with pytest.raises(ValueError):
        history.set_summary("ok", covered_turns=-1)
    history.set_summary("x" * SUMMARY_MAX_CHARS, covered_turns=3)
    assert history.summarized_turns == 3 and len(history.summary) == SUMMARY_MAX_CHARS


def test_views_are_replaced_whole_and_read_only():
    history = filled(2, 5)
    evicted = history.take_evicted()
    history.set_anchors([Anchor(evicted[0], ("nda",))])
    before = history.anchors
    history.set_anchors([])
    assert isinstance(before, tuple) and before != history.anchors
    assert isinstance(history.backlog, tuple) and isinstance(history.turns, tuple)


def test_backlog_is_capped_at_max_turns_dropping_the_oldest():
    history = filled(2, 6)
    turns = history.take_evicted()  # 1..4
    dropped = history.set_backlog(turns)
    assert dropped == 2
    assert [t.number for t in history.backlog] == [3, 4]
    assert history.dropped_turns == 2


def test_backlog_is_sorted_by_turn_number():
    history = filled(5, 3)
    turns = list(reversed(history.turns))
    history.set_backlog(turns)
    assert [t.number for t in history.backlog] == [1, 2, 3]
