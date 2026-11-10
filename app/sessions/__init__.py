"""Conversation sessions kept in process memory (S05 design D2-D4).

Volatile by design: sessions are lost on restart and not shared between workers.
Depends on pydantic, the standard library, `app.domain` and `app.estimation`; never on
LiteLLM, Instructor, `app.services` nor `app.prompts` (no import cycles).
"""

from app.sessions.metadata import (
    MAX_METADATA_TECHNOLOGIES,
    merge_technologies,
    update_project_metadata,
)
from app.sessions.models import ConversationHistory, ProjectMetadata, Session, Turn
from app.sessions.store import SessionBusyError, SessionNotFoundError, SessionStore

__all__ = [
    "MAX_METADATA_TECHNOLOGIES",
    "ConversationHistory",
    "ProjectMetadata",
    "Session",
    "SessionBusyError",
    "SessionNotFoundError",
    "SessionStore",
    "Turn",
    "merge_technologies",
    "update_project_metadata",
]
