"""memory-compression: Política de compresión, fallo del resumen y backlog (local callables)."""

import random
from collections.abc import Sequence

import pytest
from structlog.testing import capture_logs

from app.sessions.compression import AnchorDecision, compress_history
from app.sessions.models import ConversationHistory, Turn

NDA = "NDA"


class Recorder:
    """Local `detect` and `summarize`: anchor when the user text contains `NDA`."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.detected: list[int] = []
        self.summaries: list[tuple[str | None, list[int]]] = []

    def detect(self, turn: Turn) -> AnchorDecision:
        self.detected.append(turn.number)
        if NDA in turn.user:
            return AnchorDecision(is_anchor=True, rules=("nda",))
        return AnchorDecision(is_anchor=False)

    def summarize(self, previous: str | None, turns: Sequence[Turn]) -> str:
        numbers = [t.number for t in turns]
        self.summaries.append((previous, numbers))
        if self.fail:
            raise RuntimeError("boom")
        return f"{previous or 'S'}+{','.join(map(str, numbers))}"


def history_with(max_turns: int, users: Sequence[str], max_anchors: int = 3) -> ConversationHistory:
    history = ConversationHistory(max_turns, max_anchors)
    for user in users:
        history.append(user=user, assistant="a")
    return history


def run(history, rec: Recorder, max_anchors: int = 3):
    return compress_history(
        history, detect=rec.detect, summarize=rec.summarize, max_anchors=max_anchors
    )


def test_nothing_evicted_calls_neither_detect_nor_summarize():
    """Scenario: Nada expulsado."""
    rec = Recorder()
    history = history_with(3, ["a", "b"])
    report = run(history, rec)
    assert rec.detected == [] and rec.summaries == []
    assert report.evicted == 0 and not report.summary_updated


def test_an_evicted_anchor_becomes_an_anchor_without_summarizing():
    """Scenario: Expulsado que es ancla."""
    rec = Recorder()
    history = history_with(2, ["NDA firmado", "b", "c"])
    report = run(history, rec)
    assert [a.turn.number for a in history.anchors] == [1]
    assert history.anchors[0].rules == ("nda",)
    assert rec.summaries == [] and history.summary is None
    assert (report.evicted, report.anchored, report.summarized) == (1, 1, 0)
    assert [(e.turn, e.rules) for e in report.anchor_rules] == [(1, ("nda",))]


def test_an_evicted_non_anchor_is_summarized_with_the_previous_summary():
    """Scenario: Expulsado que no es ancla."""
    rec = Recorder()
    history = history_with(1, ["a", "b"])
    history.set_summary("P", covered_turns=5)
    report = run(history, rec)
    assert rec.summaries == [("P", [1])]
    assert history.summary == "P+1"
    assert history.summarized_turns == 6
    assert report.summary_updated and report.summarized == 1


def test_cap_demotes_the_oldest_anchor_into_the_summary():
    """Scenario: Tope de anclas (4 anclas con max_anchors=3: la 1.ª va a summarize)."""
    rec = Recorder()
    history = history_with(1, ["NDA 1", "NDA 2", "NDA 3", "NDA 4", "w"])
    with capture_logs() as logs:
        report = run(history, rec, max_anchors=3)
    assert [a.turn.number for a in history.anchors] == [2, 3, 4]
    assert rec.summaries == [(None, [1])]
    assert report.demoted == 1 and report.demoted_turns == (1,)
    assert [e["event"] for e in logs].count("anchor_demoted") == 1


def test_demoted_anchor_is_summarized_in_chronological_order_with_the_rest():
    rec = Recorder()
    history = history_with(1, ["NDA 1", "NDA 2", "x", "NDA 4", "w"])
    run(history, rec, max_anchors=2)
    assert rec.summaries == [(None, [1, 3])]  # demoted 1 and plain 3, ordered by number
    assert [a.turn.number for a in history.anchors] == [2, 4]


def test_anchors_disabled_sends_everything_to_summarize_without_detecting():
    """Scenario: Anclas desactivadas."""
    rec = Recorder()
    history = history_with(1, ["NDA 1", "b"], max_anchors=0)
    run(history, rec, max_anchors=0)
    assert history.anchors == ()
    assert rec.summaries == [(None, [1])]
    assert rec.detected == []


def test_the_effective_cap_is_the_smallest_of_policy_and_history():
    rec = Recorder()
    history = history_with(1, ["NDA 1", "NDA 2", "w"], max_anchors=1)
    run(history, rec, max_anchors=5)
    assert len(history.anchors) == 1


def test_failing_summarize_keeps_the_previous_summary_and_fills_the_backlog():
    """Scenario: Resumen que falla."""
    rec = Recorder(fail=True)
    history = history_with(1, ["a", "b"])
    history.set_summary("P", covered_turns=3)
    with capture_logs() as logs:
        report = run(history, rec)
    assert history.summary == "P" and history.summarized_turns == 3
    assert [t.number for t in history.backlog] == [1]
    assert report.summary_error == "RuntimeError" and not report.summary_updated
    assert any(e["event"] == "summary_failed" and e["error_type"] == "RuntimeError" for e in logs)


def test_retry_summarizes_backlog_and_new_evictions_together():
    """Scenario: Reintento en el turno siguiente."""
    failing = Recorder(fail=True)
    history = history_with(1, ["a", "b"])
    run(history, failing)
    history.append(user="c", assistant="a")
    ok = Recorder()
    report = run(history, ok)
    assert ok.summaries == [(None, [1, 2])]
    assert history.backlog == () and report.summary_updated and report.summarized == 2


def test_backlog_is_retried_even_when_nothing_new_was_evicted():
    failing = Recorder(fail=True)
    history = history_with(1, ["a", "b"])
    run(history, failing)
    ok = Recorder()
    report = run(history, ok)
    assert ok.summaries == [(None, [1])] and history.backlog == ()
    assert report.evicted == 0 and report.summarized == 1


def test_full_backlog_drops_the_oldest_and_logs():
    """Scenario: Backlog lleno."""
    rec = Recorder(fail=True)
    history = ConversationHistory(2, 3)
    with capture_logs() as logs:
        for n in range(5):  # evictions of turns 1, 2 and 3
            history.append(user=f"m{n}", assistant="a")
            run(history, rec)
    assert [t.number for t in history.backlog] == [2, 3]
    assert history.dropped_turns == 1
    assert [e for e in logs if e["event"] == "summary_backlog_dropped"] == [
        {"event": "summary_backlog_dropped", "dropped": 1, "log_level": "warning"}
    ]


def test_a_failing_detect_leaves_the_pending_turns_in_the_backlog_and_raises():
    history = history_with(1, ["a", "b"])

    def detect(turn: Turn) -> AnchorDecision:
        raise ValueError("bug")

    with pytest.raises(ValueError):
        compress_history(history, detect=detect, summarize=Recorder().summarize, max_anchors=3)
    assert [t.number for t in history.backlog] == [1]


def test_events_carry_no_turn_text():
    rec = Recorder()
    history = history_with(1, ["NDA CONTENIDO-SECRETO", "w"])
    with capture_logs() as logs:
        run(history, rec)
    assert "CONTENIDO-SECRETO" not in str(logs)
    assert [e["event"] for e in logs] == ["anchor_detected"]
    assert logs[0]["rules"] == ["nda"] and logs[0]["source"] == "heuristic"


# --- Invariante de conservación (test de propiedad, semillas fijas) ---


def check_invariants(history: ConversationHistory, appended: int, cap: int) -> None:
    accounted = (
        len(history)
        + len(history.anchors)
        + history.summarized_turns
        + len(history.backlog)
        + history.dropped_turns
    )
    assert accounted == appended
    assert len(history.anchors) <= cap
    numbers = [a.turn.number for a in history.anchors]
    assert numbers == sorted(numbers)
    window = [t.number for t in history.turns]
    if window and numbers:
        assert numbers[-1] < window[0]
    assert len(history.backlog) <= history.max_turns


@pytest.mark.parametrize("seed", range(40))
def test_conservation_invariant_over_random_sequences(seed):
    """Scenario: Invariante de conservación (secuencias aleatorias con semilla)."""
    rng = random.Random(seed)
    max_turns = rng.randint(1, 5)
    cap = rng.randint(0, 4)
    length = rng.randint(1, 40)
    history = ConversationHistory(max_turns, cap)
    anchor_p, fail_p = rng.random(), rng.random()

    def detect(turn: Turn) -> AnchorDecision:
        return AnchorDecision(is_anchor=rng.random() < anchor_p, rules=("nda",))

    def summarize(previous: str | None, turns: Sequence[Turn]) -> str:
        if rng.random() < fail_p:
            raise RuntimeError("random failure")
        return f"{previous or ''}|{len(turns)}"[-100:]

    for appended in range(1, length + 1):
        history.append(user=f"u{appended}", assistant="a")
        compress_history(history, detect=detect, summarize=summarize, max_anchors=cap)
        check_invariants(history, appended, cap)
