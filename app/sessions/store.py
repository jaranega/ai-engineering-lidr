"""In-process session store with an LRU cap and a non-blocking per-session lock (D2)."""

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import structlog

from app.sessions.models import ConversationHistory, Session

logger = structlog.get_logger(__name__)

# Logged prefix of the session id: the full id is a bearer capability (D11).
SESSION_LOG_CHARS = 8


def short_id(session_id: str) -> str:
    """The first characters of a session id, the only part ever logged."""
    return session_id[:SESSION_LOG_CHARS]


class SessionNotFoundError(LookupError):
    """No session with that id (never created, evicted or lost on restart)."""

    def __init__(self, session_id: str) -> None:
        super().__init__("session not found")
        self.session_id = session_id


class SessionBusyError(RuntimeError):
    """Another turn of the same session is in progress."""

    def __init__(self, session_id: str) -> None:
        super().__init__("session busy")
        self.session_id = session_id


class SessionStore:
    """`OrderedDict` of sessions by id, guarded by a store lock.

    `get` and `turn` refresh recency; `create` evicts the least recently used session
    while the store is full (`max_sessions`). Volatile: nothing survives the process.
    """

    def __init__(
        self,
        *,
        max_sessions: int,
        max_turns: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        self._max_sessions = max_sessions
        self._max_turns = max_turns
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def max_turns(self) -> int:
        return self._max_turns

    def create(self) -> Session:
        now = self._clock()
        session = Session(
            session_id=str(uuid.uuid4()),
            history=ConversationHistory(self._max_turns),
            created_at=now,
            updated_at=now,
        )
        evicted: list[Session] = []
        with self._lock:
            while len(self._sessions) >= self._max_sessions:
                _, old = self._sessions.popitem(last=False)
                evicted.append(old)
            self._sessions[session.session_id] = session
            total = len(self._sessions)
        for old in evicted:
            logger.info(
                "session_evicted",
                session=short_id(old.session_id),
                turn_count=old.turn_count,
                idle_s=round(now - old.updated_at, 1),
            )
        logger.info("session_created", session=short_id(session.session_id), sessions=total)
        return session

    def get(self, session_id: str) -> Session:
        """The session, marked as most recently used; `SessionNotFoundError` if absent."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                self._sessions.move_to_end(session_id)
        if session is None:
            logger.info("session_not_found", session=short_id(session_id))
            raise SessionNotFoundError(session_id)
        return session

    @contextmanager
    def turn(self, session_id: str) -> Iterator[Session]:
        """The session with its turn lock held; `SessionBusyError` without waiting if
        another turn holds it. The lock is released on exit, also after an exception.
        """
        session = self.get(session_id)
        if not session.lock.acquire(blocking=False):
            logger.info("session_busy", session=short_id(session_id))
            raise SessionBusyError(session_id)
        try:
            yield session
        finally:
            session.lock.release()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
