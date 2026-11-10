"""Requirement: Almacén de sesiones en memoria (conversation-sessions)."""

import re
import threading
import uuid

import pytest
from structlog.testing import capture_logs

from app.sessions import ProjectMetadata, SessionBusyError, SessionNotFoundError, SessionStore

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def make_store(max_sessions: int = 10, max_turns: int = 6) -> SessionStore:
    return SessionStore(max_sessions=max_sessions, max_turns=max_turns)


def enter_turn(store: SessionStore, session_id: str) -> None:
    with store.turn(session_id):
        pytest.fail("the second turn must not start")


def test_create_and_get_returns_an_empty_session_with_a_lowercase_uuid4():
    """Scenario: Crear y recuperar."""
    store = make_store()
    created = store.create()
    session = store.get(created.session_id)
    assert UUID4.match(session.session_id)
    assert uuid.UUID(session.session_id).version == 4
    assert session.history.to_messages() == []
    assert session.project_metadata == ProjectMetadata()
    assert session.project_metadata.filled_fields() == []
    assert session.turn_count == 0
    assert session.language is None


def test_unknown_id_raises_session_not_found():
    """Scenario: Sesión desconocida."""
    with pytest.raises(SessionNotFoundError):
        make_store().get(str(uuid.uuid4()))


def test_least_recently_used_session_is_evicted():
    """Scenario: Desalojo LRU (consultar A refresca su uso)."""
    store = make_store(max_sessions=2)
    a, b = store.create(), store.create()
    store.get(a.session_id)
    c = store.create()
    assert len(store) == 2
    assert store.get(a.session_id) is a
    assert store.get(c.session_id) is c
    with pytest.raises(SessionNotFoundError):
        store.get(b.session_id)


def test_using_a_session_for_a_turn_refreshes_its_recency():
    """Requirement: consultar o usar una sesión refresca su uso."""
    store = make_store(max_sessions=2)
    a, b = store.create(), store.create()
    with store.turn(a.session_id):
        pass
    store.create()
    with pytest.raises(SessionNotFoundError):
        store.get(b.session_id)
    assert store.get(a.session_id) is a


def test_second_turn_on_a_busy_session_fails_immediately():
    """Scenario: Turno concurrente."""
    store = make_store()
    session = store.create()
    with store.turn(session.session_id), pytest.raises(SessionBusyError):
        enter_turn(store, session.session_id)


def test_another_session_is_not_blocked_by_a_busy_one():
    store = make_store()
    a, b = store.create(), store.create()
    with store.turn(a.session_id), store.turn(b.session_id):
        pass


def test_lock_is_released_after_an_error():
    """Scenario: Lock liberado tras un error."""
    store = make_store()
    session = store.create()
    with pytest.raises(RuntimeError), store.turn(session.session_id):
        raise RuntimeError("boom")
    with store.turn(session.session_id) as again:
        assert again is session


def test_lock_is_released_after_a_normal_turn():
    store = make_store()
    session = store.create()
    with store.turn(session.session_id):
        pass
    assert session.lock.acquire(blocking=False)
    session.lock.release()


def test_turn_on_an_unknown_session_raises_not_found_not_busy():
    with pytest.raises(SessionNotFoundError), make_store().turn(str(uuid.uuid4())):
        pass


def test_concurrent_creation_never_exceeds_the_cap():
    """Scenario: Creación concurrente (50 sesiones, tope 10)."""
    store = make_store(max_sessions=10)
    observed: list[int] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(5):
                store.create()
                observed.append(len(store))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(observed) == 50
    assert max(observed) <= 10
    assert len(store) == 10


def test_concurrent_turns_on_one_session_allow_only_one_at_a_time():
    """Requirement: lock no bloqueante por sesión (varios hilos, uno solo entra)."""
    store = make_store()
    session = store.create()
    inside = threading.Event()
    release = threading.Event()
    results: list[str] = []

    def holder() -> None:
        with store.turn(session.session_id):
            inside.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert inside.wait(timeout=5)
    for _ in range(5):
        try:
            with store.turn(session.session_id):
                results.append("entered")
        except SessionBusyError:
            results.append("busy")
    release.set()
    thread.join()
    assert results == ["busy"] * 5


@pytest.mark.parametrize(
    "kwargs", [{"max_sessions": 0, "max_turns": 6}, {"max_sessions": 1, "max_turns": 0}]
)
def test_invalid_sizes_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        SessionStore(**kwargs)


def test_events_carry_only_the_short_id():
    """observability / Requirement: `session_created`, `session_evicted`, `session_not_found`."""
    store = make_store(max_sessions=1)
    with capture_logs() as logs:
        first = store.create()
        second = store.create()
        with pytest.raises(SessionNotFoundError):
            store.get(first.session_id)
    by_event = {entry["event"]: entry for entry in logs}
    assert by_event["session_evicted"]["session"] == first.session_id[:8]
    assert by_event["session_evicted"]["turn_count"] == 0
    assert "idle_s" in by_event["session_evicted"]
    assert by_event["session_not_found"]["session"] == first.session_id[:8]
    created = [e for e in logs if e["event"] == "session_created"]
    assert [e["session"] for e in created] == [first.session_id[:8], second.session_id[:8]]
    assert all(e["sessions"] == 1 for e in created)
    assert first.session_id not in str(logs)


def test_busy_event_is_logged_with_the_short_id():
    store = make_store()
    session = store.create()
    with (
        store.turn(session.session_id),
        capture_logs() as logs,
        pytest.raises(SessionBusyError),
    ):
        enter_turn(store, session.session_id)
    assert {"event": "session_busy", "log_level": "info", "session": session.session_id[:8]} in logs
