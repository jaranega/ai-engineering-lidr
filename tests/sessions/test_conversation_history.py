"""Requirement: Historial con ventana deslizante (conversation-sessions)."""

import pytest

from app.sessions import ConversationHistory


def test_window_is_kept_after_every_append_and_numbers_survive():
    """Scenario: Ventana tras cada turno."""
    history = ConversationHistory(max_turns=3)
    for n in range(1, 6):
        turn = history.append(user=f"u{n}", assistant=f"a{n}")
        assert turn.number == n
        assert len(history) <= 3
    assert [(t.number, t.user, t.assistant) for t in history.turns] == [
        (3, "u3", "a3"),
        (4, "u4", "a4"),
        (5, "u5", "a5"),
    ]


def test_to_messages_alternates_roles_without_system():
    """Scenario: Mensajes alternos sin system."""
    history = ConversationHistory(max_turns=6)
    history.append(user="first", assistant="reply 1")
    history.append(user="second", assistant="reply 2")
    messages = history.to_messages()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in messages] == ["first", "reply 1", "second", "reply 2"]
    assert all(m["role"] != "system" for m in messages)


def test_window_drops_whole_pairs_so_the_history_always_starts_with_user():
    """Requirement: guarda turnos completos, nunca mensajes sueltos."""
    history = ConversationHistory(max_turns=2)
    for n in range(5):
        history.append(user=f"u{n}", assistant=f"a{n}")
    messages = history.to_messages()
    assert len(messages) == 4
    assert messages[0]["role"] == "user"


def test_empty_history_has_no_messages():
    assert ConversationHistory(max_turns=2).to_messages() == []


@pytest.mark.parametrize("max_turns", [0, -1])
def test_invalid_window_raises_value_error(max_turns):
    """Scenario: Ventana inválida."""
    with pytest.raises(ValueError):
        ConversationHistory(max_turns=max_turns)


@pytest.mark.parametrize(
    ("user", "assistant"), [("", "reply"), ("question", ""), ("   ", "reply"), ("q", "\n")]
)
def test_empty_user_or_assistant_raises_value_error(user, assistant):
    """Requirement: un `user` o `assistant` vacío produce ValueError."""
    history = ConversationHistory(max_turns=2)
    with pytest.raises(ValueError):
        history.append(user=user, assistant=assistant)
    assert len(history) == 0
