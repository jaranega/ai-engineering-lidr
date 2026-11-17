"""observability: eventos de la memoria y del tier (sin contenido). Offline."""

import json

import structlog
from structlog.testing import capture_logs

from app.main import app
from app.services import memory as memory_module
from app.services.llm_service import CachedProvider, LiteLLMProvider
from app.services.memory import MemoryCompression
from app.sessions.compression import detect_anchor
from tests.conftest import FakeProvider, turn_data, valid_payload
from tests.test_llm_provider import PRIMARY
from tests.test_structured_provider import FakeRouter, tool_response

BASE = "/api/v1/sessions"


def capture():
    return capture_logs(processors=[structlog.contextvars.merge_contextvars])


def new_session(client) -> str:
    return client.post(BASE).json()["session_id"]


def send(client, session_id, transcript="Keep the same project.", **extra):
    response = client.post(
        f"{BASE}/{session_id}/estimate", data=turn_data(transcript=transcript, **extra)
    )
    assert response.status_code == 200, response.text
    return response


def events(logs, name):
    return [e for e in logs if e["event"] == name]


def test_history_compressed_is_logged_when_the_third_turn_evicts_one(make_session_client):
    """Scenario: Compresión registrada."""
    client, *_ = make_session_client(max_conversation_turns=2)
    session_id = new_session(client)
    with capture() as logs:
        send(client, session_id, "uno")
        send(client, session_id, "dos")
        assert events(logs, "history_compressed") == []  # nothing left the window yet
        send(client, session_id, "tres")
    (event,) = events(logs, "history_compressed")
    assert event["evicted"] == 1 and event["summary_updated"] is True
    assert event["summary_chars"] > 0 and event["anchored"] == 0 and event["backlog"] == 0
    assert len(event["summary_sha256"]) == 12 and event["latency_ms"] >= 0
    assert event["session"] == session_id[:8]
    assert "S:1" not in str(event)


def test_turn_completed_carries_the_memory_and_tier_counters(make_session_client):
    provider = FakeProvider(
        structured=valid_payload(explicit_constraints=["NDA firmado", "B"], rejected_options=["C"])
    )
    client, *_ = make_session_client(provider=provider, max_conversation_turns=1)
    session_id = new_session(client)
    send(client, session_id, "We signed an NDA.")
    with capture() as logs:
        send(client, session_id, "dos")
    (done,) = events(logs, "session_turn_completed")
    assert done["anchors"] == 1 and done["summary_chars"] == 0
    assert (done["tier"], done["tier_rule"]) == ("executive", "nda_detected")
    assert (done["constraints"], done["rejected"]) == (2, 1)
    assert done["history_messages"] == 2
    assert "explicit_constraints" in done["metadata_fields"]


def test_anchor_events_carry_rules_and_never_the_matched_text(make_session_client):
    client, *_ = make_session_client(max_conversation_turns=1, max_anchored_turns=1)
    session_id = new_session(client)
    with capture() as logs:
        send(client, session_id, "El presupuesto está cerrado en 40000 € CIFRA-SECRETA")
        send(client, session_id, "We signed the NDA CLAUSULA-SECRETA")
        send(client, session_id, "tres")  # NDA turn is an anchor, the budget one is demoted
    (detected, *_) = events(logs, "anchor_detected")
    assert detected["rules"] == ["budget_fixed"] and detected["source"] == "heuristic"
    (demoted,) = events(logs, "anchor_demoted")
    assert demoted["turn"] == 1 and demoted["anchors"] == 1
    for secret in ("CIFRA-SECRETA", "CLAUSULA-SECRETA", "40000"):
        assert secret not in json.dumps(logs, default=str)


def test_tier_resolved_is_logged_with_the_explicit_flag(make_session_client):
    """Scenario: Tier registrado."""
    client, *_ = make_session_client()
    session_id = new_session(client)
    with capture() as logs:
        send(client, session_id, "We signed an NDA TEXTO-SECRETO")
        send(client, session_id, "otro", tier="pm")
    first, second = events(logs, "tier_resolved")
    assert (first["tier"], first["rule"], first["explicit"]) == ("executive", "nda_detected", False)
    assert (second["tier"], second["rule"], second["explicit"]) == ("pm", "explicit", True)
    assert first["prompt_version"] == "v5"
    assert "TEXTO-SECRETO" not in json.dumps(logs, default=str)


def test_the_memory_never_leaks_content_into_the_logs(make_session_client):
    """Scenario: Memoria sin contenido en los logs (resumidor que copia los turnos)."""
    secret_turn = "RESUMIDO-SECRETO-123"
    secret_constraint = "RESTRICCION-SECRETA-456"
    router = FakeRouter(
        [tool_response(valid_payload(explicit_constraints=[secret_constraint])) for _ in range(4)]
    )
    real = CachedProvider(
        LiteLLMProvider(router, PRIMARY, None, structured_max_retries=1), None, "x"
    )
    client, *_ = make_session_client(provider=real, max_conversation_turns=1, log_level="DEBUG")

    def copy_turns(previous, turns, language, project_metadata=None):
        return " ".join(t.user for t in turns)

    app.dependency_overrides[memory_module.get_memory_compression] = lambda: MemoryCompression(
        detect=detect_anchor, summarize=copy_turns, max_anchors=3
    )
    session_id = new_session(client)
    with capture() as logs:
        send(client, session_id, f"Mensaje con {secret_turn}")
        send(client, session_id, "dos")
        send(client, session_id, "tres")
    dumped = json.dumps(logs, default=str)
    assert secret_turn not in dumped and secret_constraint not in dumped
    (compressed, *_) = events(logs, "history_compressed")
    assert compressed["summary_chars"] > 0 and compressed["summary_updated"] is True
    assert all(e.get("session") == session_id[:8] for e in logs if e["event"] != "session_created")
