"""observability / Evento `turn_observed` and conversation-sessions / Última observación
del turno (change s06-add-cag-stress-test). Offline: fake provider and fake compression.
"""

import json
from dataclasses import fields

import pytest
import structlog
from structlog.testing import capture_logs

from app.services.llm_service import LLMProviderError
from app.sessions import TurnObservation
from tests.attachments.factories import PDF_MIME, make_pdf
from tests.conftest import FakeProvider, attachment_parts, turn_data

BASE = "/api/v1/sessions"
SECRET = "TRANSCRIPCION-SECRETA-123"
FIELDS = {
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "cache_hit_kind",
    "last_resolved_tier",
}


def capture():
    return capture_logs(processors=[structlog.contextvars.merge_contextvars])


def observed(logs: list[dict]) -> list[dict]:
    return [e for e in logs if e["event"] == "turn_observed"]


def new_session(client) -> str:
    return client.post(BASE).json()["session_id"]


def send(client, session_id, *files, **overrides):
    return client.post(
        f"{BASE}/{session_id}/estimate",
        data=turn_data(**overrides),
        files=attachment_parts(*files) if files else None,
    )


@pytest.fixture
def rig(make_session_client):
    client, provider, _, store = make_session_client()
    return client, provider, store


def test_first_turn_without_attachments_is_observed(rig):
    """Scenario: Primer turno sin adjuntos (13 campos exactos, 120 caracteres)."""
    client, provider, _ = rig
    session_id = new_session(client)
    transcript = "x" * 120
    with capture() as logs:
        assert send(client, session_id, transcript=transcript).status_code == 200
    (event,) = observed(logs)
    assert {k for k in event if k not in {"event", "log_level", "session"}} == FIELDS
    assert event["turn_index"] == 1
    assert event["enriched_transcript_chars"] == 120
    assert event["attachments_total_chars"] == 0
    assert event["messages_in_window"] == 2
    assert (event["anchors_count"], event["summary_chars"]) == (0, 0)
    assert event["cache_hit_kind"] == "none"
    assert (event["tokens_in"], event["tokens_out"]) == (1000, 500)
    assert isinstance(event["cost_usd"], float) and event["cost_usd"] > 0
    assert isinstance(event["latency_ms"], int) and event["latency_ms"] >= 0
    assert event["last_resolved_tier"] == "default"


def test_turn_with_pdf_counts_attachment_and_enriched_chars(rig):
    """Scenario: Turno con adjunto (transcripción de 100 + PDF de 5000 caracteres)."""
    client, *_ = rig
    session_id = new_session(client)
    text = "a" * 5000
    with capture() as logs:
        response = send(
            client, session_id, ("spec.pdf", make_pdf(text), PDF_MIME), transcript="t" * 100
        )
    assert response.status_code == 200
    (event,) = observed(logs)
    assert event["attachments_total_chars"] == len(text) == 5000
    assert event["enriched_transcript_chars"] == 5100


def test_cache_hit_is_observed_as_exact(make_session_client):
    """Scenario: Acierto de caché."""
    client, *_ = make_session_client(provider=FakeProvider(cache_hit=True))
    session_id = new_session(client)
    with capture() as logs:
        assert send(client, session_id).status_code == 200
    assert observed(logs)[0]["cache_hit_kind"] == "exact"


def test_event_has_neither_client_text_nor_the_full_session_id(rig):
    """Scenario: Sin texto en el evento (el session_id son 8 caracteres)."""
    client, *_ = rig
    session_id = new_session(client)
    with capture() as logs:
        send(client, session_id, transcript=f"Need a booking app. {SECRET}")
    (event,) = observed(logs)
    dumped = json.dumps(event, default=str)
    assert SECRET not in dumped and session_id not in dumped
    assert event["turn_index"] == 1
    assert event["session_id"] == session_id[:8]


def test_failed_turn_emits_nothing_and_keeps_last_observation(rig):
    """Scenarios: Turno fallido (502) + Observación guardada (no se modifica con error)."""
    client, provider, store = rig
    session_id = new_session(client)
    send(client, session_id)
    before = store.get(session_id).last_observation
    provider.error = LLMProviderError("openai")
    with capture() as logs:
        response = send(client, session_id)
    assert response.status_code == 502
    assert observed(logs) == []
    assert store.get(session_id).last_observation is before
    assert before.turn_index == 1


def test_last_observation_matches_the_event_of_the_second_turn(rig):
    """Scenario: Observación guardada (segundo turno; mismos valores que el evento)."""
    client, _, store = rig
    session_id = new_session(client)
    send(client, session_id)
    with capture() as logs:
        assert send(client, session_id).status_code == 200
    (event,) = observed(logs)
    stored = store.get(session_id).last_observation
    assert stored.turn_index == 2
    assert {f.name for f in fields(TurnObservation)} == FIELDS
    for name in FIELDS:
        value = getattr(stored, name)
        value = getattr(value, "value", value)  # the tier is logged as its string value
        assert event[name] == value, name


def test_last_observation_is_immutable(rig):
    """Requirement: objeto inmutable."""
    client, _, store = rig
    session_id = new_session(client)
    send(client, session_id)
    with pytest.raises(AttributeError):
        store.get(session_id).last_observation.turn_index = 9
