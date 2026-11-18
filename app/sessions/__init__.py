"""Conversation sessions kept in process memory (S05 design D2-D4; design s05-memory).

Volatile by design: sessions are lost on restart and not shared between workers.
Depends on pydantic, structlog, the standard library, `app.domain` and `app.estimation`;
never on LiteLLM, Instructor, `app.services` nor `app.prompts` (no import cycles).
"""

from app.sessions.metadata import (
    MAX_METADATA_CONSTRAINTS,
    MAX_METADATA_REJECTED_OPTIONS,
    MAX_METADATA_TECHNOLOGIES,
    merge_technologies,
    merge_unique,
    update_project_metadata,
)
from app.sessions.models import (
    SUMMARY_MAX_CHARS,
    Anchor,
    ConversationHistory,
    LastReview,
    ProjectMetadata,
    Session,
    Turn,
)
from app.sessions.store import SessionBusyError, SessionNotFoundError, SessionStore
from app.sessions.tier import Tier, TierDecision, TierRule, resolve_tier

__all__ = [
    "MAX_METADATA_CONSTRAINTS",
    "MAX_METADATA_REJECTED_OPTIONS",
    "MAX_METADATA_TECHNOLOGIES",
    "SUMMARY_MAX_CHARS",
    "Anchor",
    "ConversationHistory",
    "LastReview",
    "ProjectMetadata",
    "Session",
    "SessionBusyError",
    "SessionNotFoundError",
    "SessionStore",
    "Tier",
    "TierDecision",
    "TierRule",
    "Turn",
    "merge_technologies",
    "merge_unique",
    "resolve_tier",
    "update_project_metadata",
]
