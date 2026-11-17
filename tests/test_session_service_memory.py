"""conversation-sessions: compresión del historial tras el turno y tier del turno, con un
compresor falso (sin LLM, sin red).
"""

import pytest
from structlog.testing import capture_logs

from app.attachments import AttachmentLimits
from app.domain import SessionTurnInput
from app.services import llm_service
from app.services.llm_service import (
    EstimationService,
    GenerationOptions,
    InvalidOptionError,
    LLMProviderError,
)
from app.services.memory import MemoryCompression
from app.services.session_service import SessionService
from app.sessions import SessionStore
from app.sessions.compression import detect_anchor
from app.sessions.tier import Tier, resolve_tier
from tests.conftest import (
    TRANSCRIPT,
    FakeModerator,
    FakeProvider,
    fake_summarize,
    make_fake_compression,
    valid_payload,
)

LIMITS = AttachmentLimits(max_files=5, max_bytes=10 * 1024 * 1024, max_chars=60_000)
NDA = "We signed an NDA with the client."


class SpyMemory:
    """Duck-typed `MemoryCompression` that records how it was called."""

    def __init__(self, inner: MemoryCompression) -> None:
        self.inner = inner
        self.calls: list[dict] = []
        self.session = None

    def compress(self, history, language, project_metadata=None):
        self.calls.append(
            {
                "lock_held": self.session.lock.locked(),
                "language": language,
                "window": len(history),
                "turn_count": self.session.turn_count,
                "project_metadata": project_metadata,
            }
        )
        return self.inner.compress(history, language, project_metadata)


class Rig:
    def __init__(self, memory=None, provider=None, max_turns: int = 3, max_anchors: int = 3):
        self.provider = provider or FakeProvider()
        self.memory = memory if memory is not None else make_fake_compression(max_anchors)
        self.store = SessionStore(max_sessions=10, max_turns=max_turns, max_anchors=max_anchors)
        self.service = SessionService(
            self.store,
            EstimationService(self.provider, moderator=FakeModerator(result=False)),
            LIMITS,
            memory=self.memory,
        )
        self.session_id = self.service.create()
        if isinstance(self.memory, SpyMemory):
            self.memory.session = self.store.get(self.session_id)

    @property
    def session(self):
        return self.store.get(self.session_id)

    def turn(self, transcript: str = TRANSCRIPT, version: str = "v5", tier: Tier | None = None):
        return self.service.estimate(
            self.session_id,
            SessionTurnInput(
                transcript=transcript,
                project_type="web_saas",
                detail_level="medium",
                output_format="line_items",
            ),
            (),
            GenerationOptions(prompt_version=version),
            tier=tier,
        )


def failing_summarize(previous, turns, language, project_metadata=None):
    raise RuntimeError("summarizer down")


def failing_detect(turn):
    raise ValueError("detector bug")


def test_compression_runs_after_the_turn_is_stored_and_inside_the_lock():
    """Scenario: Compresión dentro del lock (y tras actualizar el historial)."""
    memory = SpyMemory(make_fake_compression())
    rig = Rig(memory=memory, max_turns=2)
    for n in range(3):
        rig.turn(f"mensaje {n}")
    assert len(memory.calls) == 3
    assert all(call["lock_held"] for call in memory.calls)
    # Compression sees the state after the turn: the counter is already updated and the
    # window already holds the new turn (the third turn evicts the first).
    assert [c["turn_count"] for c in memory.calls] == [1, 2, 3]
    assert [c["window"] for c in memory.calls] == [1, 2, 2]
    assert rig.session.history.summary == "S:1"
    assert not rig.session.lock.locked()


def test_the_compressor_receives_the_session_language_of_the_turn():
    memory = SpyMemory(make_fake_compression())
    rig = Rig(memory=memory)
    rig.turn()
    assert memory.calls[0]["language"] == "en"  # the fake estimate reports `en`


def test_summarizer_failure_keeps_the_turn_and_fills_the_backlog():
    """Scenario: Resumidor que falla."""
    memory = MemoryCompression(detect=detect_anchor, summarize=failing_summarize, max_anchors=3)
    rig = Rig(memory=memory, max_turns=6)
    with capture_logs() as logs:
        for n in range(1, 8):
            outcome = rig.turn(f"mensaje {n}")
    assert outcome.turn == 7 and rig.session.turn_count == 7
    assert [t.number for t in rig.session.history.backlog] == [1]
    assert rig.session.history.summary is None
    assert len(rig.session.history) == 6
    assert any(e["event"] == "summary_failed" for e in logs)
    compressed = [e for e in logs if e["event"] == "history_compressed"][-1]
    assert compressed["summary_updated"] is False and compressed["backlog"] == 1


def test_the_response_does_not_change_when_the_summarizer_fails():
    ok = Rig(max_turns=1)
    ko = Rig(
        memory=MemoryCompression(detect=detect_anchor, summarize=failing_summarize, max_anchors=3),
        max_turns=1,
    )
    for rig in (ok, ko):
        rig.turn("uno")
    assert ok.turn("dos").outcome.text == ko.turn("dos").outcome.text


def test_detector_failure_is_logged_and_the_turn_succeeds():
    """Scenario: Detector que falla."""
    memory = MemoryCompression(detect=failing_detect, summarize=fake_summarize, max_anchors=3)
    rig = Rig(memory=memory, max_turns=1)
    rig.turn("uno")
    with capture_logs() as logs:
        outcome = rig.turn("dos")  # evicts turn 1 -> detect raises
    assert outcome.turn == 2 and rig.session.turn_count == 2
    (event,) = [e for e in logs if e["event"] == "history_compression_failed"]
    assert event["error_type"] == "ValueError"
    assert "detector bug" not in str(logs)
    assert not rig.session.lock.locked()


def test_a_failed_turn_neither_compresses_nor_touches_the_memory():
    """Scenario: Turno fallido."""
    memory = SpyMemory(make_fake_compression())
    rig = Rig(memory=memory, max_turns=1)
    rig.turn(NDA, tier=Tier.PM)
    calls_before = len(memory.calls)
    before = (
        rig.session.turn_count,
        rig.session.last_tier,
        rig.session.history.to_messages(),
        rig.session.history.anchors,
        rig.session.history.summary,
        rig.session.project_metadata,
        rig.session.language,
    )
    rig.provider.error = LLMProviderError(provider="openai")
    with pytest.raises(LLMProviderError):
        rig.turn("otro mensaje", tier=Tier.DEVELOPER)
    after = (
        rig.session.turn_count,
        rig.session.last_tier,
        rig.session.history.to_messages(),
        rig.session.history.anchors,
        rig.session.history.summary,
        rig.session.project_metadata,
        rig.session.language,
    )
    assert after == before
    assert len(memory.calls) == calls_before


def test_v4_sessions_are_compressed_too():
    rig = Rig(max_turns=1)
    rig.turn("uno", version="v4")
    rig.turn("dos", version="v4")
    assert rig.session.history.summary == "S:1"
    assert "</conversation_summary>" not in rig.provider.calls[1][0]


def test_anchors_are_detected_and_sent_in_the_history():
    rig = Rig(max_turns=2)
    rig.turn(NDA)
    rig.turn("dos")
    rig.turn("tres")  # evicts the NDA turn, which becomes an anchor
    assert [a.turn.number for a in rig.session.history.anchors] == [1]
    rig.turn("cuatro")
    _, messages = rig.provider.calls[3]
    assert len(messages) == 2 * (1 + 2) + 1
    assert NDA in messages[0]["content"]


# --- Tier ---


def test_last_tier_is_none_on_creation_and_set_after_a_success():
    """Requirement: Tier del turno en la sesión."""
    rig = Rig()
    assert rig.session.last_tier is None
    outcome = rig.turn(NDA)
    assert (outcome.tier, outcome.tier_rule) == (Tier.EXECUTIVE, "nda_detected")
    assert (rig.session.last_tier.tier, rig.session.last_tier.rule) == (
        Tier.EXECUTIVE,
        "nda_detected",
    )


def test_the_tier_reaches_render_session_prompt_and_the_system_has_the_audience(monkeypatch):
    """Scenario: Tier en el render."""
    seen = {}
    real = llm_service.render_session_prompt

    def spy(*args, **kwargs):
        seen.update(version=args[1], **kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(llm_service, "render_session_prompt", spy)
    rig = Rig()
    rig.turn(NDA, version="v5")
    assert seen["tier"] == Tier.EXECUTIVE and seen["version"] == "v5"
    assert "<audience>" in rig.provider.calls[0][0]


def test_the_summary_reaches_the_render_from_the_second_compression(monkeypatch):
    summaries = []
    real = llm_service.render_session_prompt

    def spy(*args, **kwargs):
        summaries.append(kwargs.get("conversation_summary"))
        return real(*args, **kwargs)

    monkeypatch.setattr(llm_service, "render_session_prompt", spy)
    rig = Rig(max_turns=1)
    for n in range(3):
        rig.turn(f"mensaje {n}")
    assert summaries == [None, None, "S:1"]


def test_tier_is_resolved_before_the_prompt_is_rendered_and_with_the_current_metadata():
    rig = Rig(provider=FakeProvider(structured=valid_payload(explicit_constraints=["NDA firmado"])))
    first = rig.turn("Hola")
    second = rig.turn("Añade un panel")
    assert first.tier_rule == "default" and second.tier_rule == "nda_detected"
    assert "<audience>" not in rig.provider.calls[0][0]
    assert "<audience>" in rig.provider.calls[1][0]


def test_small_team_uses_the_team_of_the_last_estimate():
    team = [{"role": "engineers", "count": 2, "part_time": False}]
    rig = Rig(provider=FakeProvider(structured=valid_payload(team=team)))
    assert rig.turn("Hola").tier_rule == "default"  # first turn: empty metadata
    assert rig.turn("Hola otra vez").tier_rule == "small_team"


def test_explicit_tier_with_v4_is_rejected_before_any_work():
    rig = Rig()
    with pytest.raises(InvalidOptionError) as caught:
        rig.turn("Hola", version="v4", tier=Tier.EXECUTIVE)
    assert caught.value.field == "tier"
    assert rig.provider.call_count == 0 and rig.session.turn_count == 0
    assert rig.session.last_tier is None and not rig.session.lock.locked()


def test_v4_without_explicit_tier_is_default_prompt_version_without_the_rules():
    rig = Rig()
    outcome = rig.turn(NDA, version="v4")
    assert (outcome.tier, outcome.tier_rule) == (Tier.DEFAULT, "prompt_version")
    assert rig.session.last_tier.rule == "prompt_version"
    assert "<audience>" not in rig.provider.calls[0][0]


def test_tier_resolved_event_has_no_text():
    """Scenario: Tier registrado."""
    rig = Rig()
    with capture_logs() as logs:
        rig.turn("Hemos firmado un NDA con TEXTO-SECRETO-1")
    (event,) = [e for e in logs if e["event"] == "tier_resolved"]
    assert (event["tier"], event["rule"], event["explicit"]) == ("executive", "nda_detected", False)
    assert event["prompt_version"] == "v5"
    assert "TEXTO-SECRETO-1" not in str(logs)


def test_the_compressor_receives_the_metadata_updated_by_the_turn():
    """Scenario: Contexto del proyecto (the compression runs after the metadata update)."""
    memory = SpyMemory(make_fake_compression())
    rig = Rig(memory=memory)
    rig.turn()
    seen = memory.calls[0]["project_metadata"]
    assert seen is rig.session.project_metadata
    assert seen.project_name is not None and seen.agreed_scope is not None


def test_an_nda_in_the_window_keeps_the_tier_without_the_constraint():
    """Scenario: NDA recordado por el historial (the model leaves the constraint empty)."""
    rig = Rig(max_turns=3)
    assert rig.turn(NDA).tier_rule == "nda_detected"
    assert rig.session.project_metadata.explicit_constraints == []
    outcome = rig.turn("Add Stripe payments")
    assert (outcome.tier, outcome.tier_rule) == (Tier.EXECUTIVE, "nda_detected")


def test_an_anchored_nda_keeps_the_tier_after_leaving_the_window():
    rig = Rig(max_turns=1)
    rig.turn(NDA)
    rig.turn("dos")  # evicts the NDA turn, which becomes an anchor
    assert len(rig.session.history.anchors) == 1
    assert rig.turn("tres").tier_rule == "nda_detected"


def test_an_nda_summarized_away_no_longer_counts():
    """Only anchors and window: neither the summary nor discarded turns feed the tier."""
    rig = Rig(max_turns=1, max_anchors=0)
    rig.turn(NDA)
    rig.turn("dos")  # evicts the NDA turn into the summary (anchors disabled)
    assert rig.session.history.anchors == ()
    assert rig.turn("tres").tier_rule == "default"


def test_the_tier_reads_only_user_messages_of_the_history(monkeypatch):
    seen = {}

    def spy(transcript, metadata, explicit, history_messages=()):
        seen["history"] = list(history_messages)
        return resolve_tier(transcript, metadata, explicit, history_messages)

    monkeypatch.setattr("app.services.session_service.resolve_tier", spy)
    rig = Rig(max_turns=3)
    rig.turn("uno")
    rig.turn("dos")
    assert seen["history"] == [rig.session.history.turns[0].user]


def test_snapshot_exposes_memory_counters_without_text():
    rig = Rig(max_turns=1)
    rig.turn(NDA)
    rig.turn("dos")
    rig.turn("tres")
    snapshot = rig.service.info(rig.session_id)
    assert snapshot.anchors_count == 1
    assert snapshot.summary_chars == len(rig.session.history.summary) > 0
    # The anchored NDA turn is still in the history: the tier keeps it (D16 correction 2).
    assert (snapshot.last_tier, snapshot.last_tier_rule) == (Tier.EXECUTIVE, "nda_detected")


def test_service_without_memory_discards_the_evicted_turns():
    store = SessionStore(max_sessions=2, max_turns=1)
    service = SessionService(
        store, EstimationService(FakeProvider(), moderator=FakeModerator()), LIMITS
    )
    session_id = service.create()
    for n in range(3):
        service.estimate(
            session_id,
            SessionTurnInput(
                transcript=f"mensaje {n}",
                project_type="web_saas",
                detail_level="medium",
                output_format="line_items",
            ),
        )
    assert store.get(session_id).history.take_evicted() == []
