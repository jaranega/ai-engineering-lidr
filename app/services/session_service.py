"""Conversation sessions use case (S05 design D6): the state around `estimate_turn`.

`SessionService` finds the session, takes its turn lock, extracts the attachments, runs
the stateless turn pipeline of `EstimationService.estimate_turn` with the session's
history and metadata, and only when everything succeeded appends the turn and updates
the project metadata. Any exception leaves the session untouched (failed turns leave no
trace). Sessions are volatile: in process memory, lost on restart, one store per process.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import structlog
from fastapi import Depends

from app.attachments import AttachmentLimits, UploadedFile, extract_attachments
from app.config import Settings, get_settings
from app.domain import SessionTurnInput
from app.services.llm_service import (
    DEFAULT_SESSION_PROMPT_VERSION,
    EstimationOutcome,
    EstimationService,
    GenerationOptions,
    LLMProvider,
    Moderator,
    get_llm_provider,
    get_moderator,
)
from app.sessions.metadata import update_project_metadata
from app.sessions.models import ProjectMetadata
from app.sessions.store import SessionStore, short_id

logger = structlog.get_logger(__name__)

__all__ = [
    "SessionService",
    "SessionSnapshot",
    "SessionTurnOutcome",
    "get_session_service",
    "get_session_store",
]


@dataclass(frozen=True)
class SessionSnapshot:
    """Read-only view of a session for `GET /api/v1/sessions/{id}`."""

    session_id: str
    turn_count: int  # successful turns since creation
    history_turns: int  # turns currently in the window (<= max_turns)
    max_turns: int
    project_metadata: ProjectMetadata


@dataclass(frozen=True)
class SessionTurnOutcome:
    """Result of a successful session turn: the estimation plus the session state after it.

    `turn` is the ordinal of this turn (== `turn_count` after it).
    """

    outcome: EstimationOutcome
    session_id: str
    turn: int
    project_metadata: ProjectMetadata


class SessionService:
    """Stateful side of the session turns; the LLM pipeline stays in `EstimationService`."""

    def __init__(
        self,
        store: SessionStore,
        estimation: EstimationService,
        limits: AttachmentLimits,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._estimation = estimation
        self._limits = limits
        self._clock = clock

    def create(self) -> str:
        """Create an empty session and return its id (canonical lowercase UUID4)."""
        return self._store.create().session_id

    def info(self, session_id: str) -> SessionSnapshot:
        """Snapshot of the session (refreshes its recency); `SessionNotFoundError`."""
        session = self._store.get(session_id)
        return SessionSnapshot(
            session_id=session.session_id,
            turn_count=session.turn_count,
            history_turns=len(session.history),
            max_turns=session.history.max_turns,
            project_metadata=session.project_metadata,
        )

    def estimate(
        self,
        session_id: str,
        turn: SessionTurnInput,
        uploads: Sequence[UploadedFile] = (),
        options: GenerationOptions | None = None,
    ) -> SessionTurnOutcome:
        """Run one turn of the session.

        Order (D6): turn lock (`SessionNotFoundError`, `SessionBusyError`), attachment
        extraction inside the lock (`AttachmentError`), `estimate_turn` with the history,
        metadata and language of the session (`InvalidOptionError`,
        `InputGuardrailViolation`, `LLMProviderError`) and, only on success, append the
        turn, update the metadata (not when out of scope), the language and the counters.
        Every event of the turn carries the short session id.
        """
        options = options or GenerationOptions(prompt_version=DEFAULT_SESSION_PROMPT_VERSION)
        with (
            self._store.turn(session_id) as session,
            structlog.contextvars.bound_contextvars(session=short_id(session_id)),
        ):
            attachments = extract_attachments(uploads, self._limits)
            history = session.history.to_messages()
            result = self._estimation.estimate_turn(
                turn,
                attachments=attachments,
                history=history,
                project_metadata=session.project_metadata,
                session_language=session.language,
                options=options,
            )

            # Success only from here on: nothing above mutates the session.
            outcome = result.outcome
            session.history.append(user=result.history_user, assistant=outcome.text)
            out_of_scope = outcome.result.out_of_scope
            if not out_of_scope:
                session.project_metadata = update_project_metadata(
                    session.project_metadata, outcome.result
                )
            session.language = outcome.result.language
            session.turn_count += 1
            session.updated_at = self._clock()
            metadata = session.project_metadata
            logger.info(
                "session_turn_completed",
                turn=session.turn_count,
                history_messages=len(history),
                history_turns=len(session.history),
                attachments=len(attachments),
                out_of_scope=out_of_scope,
                language=session.language,
                metadata_updated=not out_of_scope,
                metadata_fields=metadata.filled_fields(),
                technologies=len(metadata.mentioned_technologies),
            )
            return SessionTurnOutcome(
                outcome=outcome,
                session_id=session.session_id,
                turn=session.turn_count,
                project_metadata=metadata,
            )


@lru_cache
def get_session_store() -> SessionStore:
    """The single session store of the process (tests override this dependency)."""
    settings = get_settings()
    return SessionStore(
        max_sessions=settings.max_sessions, max_turns=settings.max_conversation_turns
    )


def get_session_service(
    store: Annotated[SessionStore, Depends(get_session_store)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    moderator: Annotated[Moderator, Depends(get_moderator)],
) -> SessionService:
    """FastAPI dependency: a service over the process store, provider and moderator."""
    estimation = EstimationService(
        provider, resolve_model=settings.resolve_request_model, moderator=moderator
    )
    limits = AttachmentLimits(
        max_files=settings.max_attachments,
        max_bytes=settings.max_attachment_bytes,
        max_chars=settings.max_attachment_chars,
    )
    return SessionService(store, estimation, limits)
