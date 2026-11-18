"""Conversation sessions use case (S05 design D6): the state around `estimate_turn`.

`SessionService` finds the session, takes its turn lock, resolves the audience tier,
extracts the attachments, runs the stateless turn pipeline of
`EstimationService.estimate_turn` with the session's history (anchors and window),
metadata and summary, and only when everything succeeded appends the turn, updates the
project metadata, language, tier, review and counters, and compresses the history
(design s05-memory D4, D9). With `review=True` the turn goes through the Actor-Critic-Boss
review inside `estimate_turn` (design s05-acb D2): the session is still updated and
compressed once, with the final result. Any exception before that leaves the session
untouched (failed turns leave no trace); a failing compression never fails the turn.
Sessions are volatile: in process memory, lost on restart, one store per process.
"""

import hashlib
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
    MEMORY_PROMPT_VERSIONS,
    REVIEW_PROMPT_VERSIONS,
    REVIEW_REQUIRES_V6,
    SESSION_PROMPT_VERSIONS,
    BossTrace,
    EstimationOutcome,
    EstimationService,
    GenerationOptions,
    InvalidOptionError,
    LLMProvider,
    Moderator,
    ReviewConfig,
    get_llm_provider,
    get_moderator,
)
from app.services.memory import MemoryCompression, get_memory_compression
from app.sessions.metadata import update_project_metadata
from app.sessions.models import LastReview, ProjectMetadata, Session
from app.sessions.store import SessionStore, short_id
from app.sessions.tier import Tier, TierDecision, TierRule, resolve_tier

logger = structlog.get_logger(__name__)

__all__ = [
    "LastReview",
    "SessionService",
    "SessionSnapshot",
    "SessionTurnOutcome",
    "Tier",
    "TierRule",
    "get_session_service",
    "get_session_store",
]

# Message of the 422 for an explicit tier with a prompt version without `<audience>`.
TIER_REQUIRES_V5 = "requires prompt_version v5"
# Logged prefix of the summary hash (`history_compressed.summary_sha256`).
_SUMMARY_HASH_CHARS = 12


@dataclass(frozen=True)
class SessionSnapshot:
    """Read-only view of a session for `GET /api/v1/sessions/{id}`."""

    session_id: str
    turn_count: int  # successful turns since creation
    history_turns: int  # turns currently in the window (<= max_turns)
    max_turns: int
    project_metadata: ProjectMetadata
    anchors_count: int = 0  # anchored turns kept verbatim out of the window
    summary_chars: int = 0  # length of the cumulative summary; 0 without one (never the text)
    last_tier: Tier | None = None  # tier of the last successful turn
    last_tier_rule: TierRule | None = None
    # Review of the last successful turn; None when it was not reviewed (or no turn yet).
    last_review: LastReview | None = None


@dataclass(frozen=True)
class SessionTurnOutcome:
    """Result of a successful session turn: the estimation plus the session state after it.

    `turn` is the ordinal of this turn (== `turn_count` after it); `tier` and `tier_rule`
    are the audience applied to this turn and the rule that decided it.
    """

    outcome: EstimationOutcome
    session_id: str
    turn: int
    project_metadata: ProjectMetadata
    tier: Tier = Tier.DEFAULT
    tier_rule: TierRule = "default"

    @property
    def review(self) -> BossTrace | None:
        """Trace of the Actor-Critic-Boss review of this turn; None without review."""
        return self.outcome.review


class SessionService:
    """Stateful side of the session turns; the LLM pipeline stays in `EstimationService`."""

    def __init__(
        self,
        store: SessionStore,
        estimation: EstimationService,
        limits: AttachmentLimits,
        clock: Callable[[], float] = time.monotonic,
        memory: MemoryCompression | None = None,
        review: ReviewConfig | None = None,
    ) -> None:
        """`memory` compresses the history after each successful turn; without it (direct
        callers and tests that do not need the memory) evicted turns are discarded, as in
        S05. `review` is the Actor-Critic-Boss configuration applied to the turns that ask
        for it (`BOSS_MAX_ITERATIONS`, `CRITIC_MODEL`); by default 3 iterations and the
        primary model with fallback for the Critic.
        """
        self._store = store
        self._estimation = estimation
        self._limits = limits
        self._clock = clock
        self._memory = memory
        self._review = review or ReviewConfig()

    def create(self) -> str:
        """Create an empty session and return its id (canonical lowercase UUID4)."""
        return self._store.create().session_id

    def info(self, session_id: str) -> SessionSnapshot:
        """Snapshot of the session (refreshes its recency); `SessionNotFoundError`."""
        session = self._store.get(session_id)
        history = session.history
        last_tier = session.last_tier
        return SessionSnapshot(
            session_id=session.session_id,
            turn_count=session.turn_count,
            history_turns=len(history),
            max_turns=history.max_turns,
            project_metadata=session.project_metadata,
            anchors_count=len(history.anchors),
            summary_chars=len(history.summary or ""),
            last_tier=last_tier.tier if last_tier else None,
            last_tier_rule=last_tier.rule if last_tier else None,
            last_review=session.last_review,
        )

    def estimate(
        self,
        session_id: str,
        turn: SessionTurnInput,
        uploads: Sequence[UploadedFile] = (),
        options: GenerationOptions | None = None,
        *,
        tier: Tier | None = None,
        review: bool = False,
    ) -> SessionTurnOutcome:
        """Run one turn of the session.

        Order (S05 D6, design s05-memory D4/D9): turn lock (`SessionNotFoundError`,
        `SessionBusyError`), audience tier (`InvalidOptionError` on `tier` for an explicit
        tier with a prompt version without `<audience>`), attachment extraction inside the
        lock (`AttachmentError`), `estimate_turn` with the history (anchors and window),
        metadata, language, summary and tier of the session (`InvalidOptionError`,
        `InputGuardrailViolation`, `LLMProviderError`) and, only on success, append the
        turn, update the metadata (not when out of scope), the language, `last_tier` and
        the counters, then compress the history (never failing the turn). `tier` is the
        explicit audience of the turn; `None` resolves it by rules. `review=True` runs the
        Actor-Critic-Boss review with the service's `ReviewConfig` (`InvalidOptionError` on
        `review` with a prompt version without `<critic_feedback>`, before the tier and
        the attachments); the session stores `last_review` (None for a turn without
        review). Every event of the turn carries the short session id.
        """
        options = options or GenerationOptions(prompt_version=DEFAULT_SESSION_PROMPT_VERSION)
        with (
            self._store.turn(session_id) as session,
            structlog.contextvars.bound_contextvars(session=short_id(session_id)),
        ):
            # Anchors and window before the new turn: the history sent to the LLM and the
            # stored client messages the tier reads (NDA and regulation).
            history = session.history.to_messages()
            # An unknown version is left to `estimate_turn`, which rejects it.
            if (
                review
                and options.prompt_version in SESSION_PROMPT_VERSIONS
                and options.prompt_version not in REVIEW_PROMPT_VERSIONS
            ):
                raise InvalidOptionError("review", REVIEW_REQUIRES_V6)
            decision = _resolve_tier(
                turn.transcript,
                session,
                tier,
                options.prompt_version,
                [message["content"] for message in history if message["role"] == "user"],
            )
            attachments = extract_attachments(uploads, self._limits)
            result = self._estimation.estimate_turn(
                turn,
                attachments=attachments,
                history=history,
                project_metadata=session.project_metadata,
                session_language=session.language,
                options=options,
                tier=decision.tier,
                conversation_summary=session.history.summary,
                review=self._review if review else None,
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
            session.last_tier = decision
            trace = outcome.review
            session.last_review = (
                LastReview(
                    final_decision=trace.final_decision,
                    stop_reason=trace.stop_reason,
                    iterations_run=trace.iterations_run,
                )
                if trace is not None
                else None
            )
            session.turn_count += 1
            session.updated_at = self._clock()
            self._compress(session)
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
                anchors=len(session.history.anchors),
                summary_chars=len(session.history.summary or ""),
                tier=decision.tier.value,
                tier_rule=decision.rule,
                review=review,
                review_decision=trace.final_decision if trace is not None else None,
                constraints=len(metadata.explicit_constraints),
                rejected=len(metadata.rejected_options),
            )
            return SessionTurnOutcome(
                outcome=outcome,
                session_id=session.session_id,
                turn=session.turn_count,
                project_metadata=metadata,
                tier=decision.tier,
                tier_rule=decision.rule,
            )

    def _compress(self, session: Session) -> None:
        """Compress the history after a successful turn, inside the lock (D4).

        Best effort: the turn is already stored, so any exception is logged as
        `history_compression_failed` and swallowed. Without a `MemoryCompression`, the
        evicted turns are discarded (S05 behaviour).
        """
        history = session.history
        if self._memory is None:
            history.take_evicted()
            return
        start = time.perf_counter()
        try:
            # After the metadata update of the turn: the project context is up to date.
            report = self._memory.compress(history, session.language, session.project_metadata)
        except Exception as exc:
            logger.error("history_compression_failed", error_type=type(exc).__name__)
            return
        if not report.evicted and not report.summarized and not report.summary_error:
            return  # nothing left the window and there was no backlog to retry
        summary = history.summary or ""
        logger.info(
            "history_compressed",
            evicted=report.evicted,
            anchored=report.anchored,
            demoted=report.demoted,
            summarized=report.summarized,
            backlog=report.backlog,
            dropped=report.dropped,
            anchors=len(history.anchors),
            summary_updated=report.summary_updated,
            summary_chars=len(summary),
            summary_sha256=(
                hashlib.sha256(summary.encode()).hexdigest()[:_SUMMARY_HASH_CHARS]
                if summary
                else None
            ),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )


def _resolve_tier(
    transcript: str,
    session: Session,
    explicit: Tier | None,
    prompt_version: str,
    history_messages: Sequence[str] = (),
) -> TierDecision:
    """Audience tier of the turn, before rendering the prompt (design s05-memory D9).

    `history_messages` are the stored `user` messages of the anchors and the window
    before this turn (attachments as `[…]`), read only by the NDA and regulation rules.

    Only versions with an `<audience>` block (v5) resolve the rules; with v4 the decision
    is `default`/`prompt_version` and an explicit tier is a 422 (`InvalidOptionError` on
    `tier`). An unknown version is left to `estimate_turn`, which rejects it.
    """
    if prompt_version in MEMORY_PROMPT_VERSIONS:
        decision = resolve_tier(transcript, session.project_metadata, explicit, history_messages)
    elif explicit is not None and prompt_version == "v4":
        raise InvalidOptionError("tier", TIER_REQUIRES_V5)
    else:
        decision = TierDecision(Tier.DEFAULT, "prompt_version")
    logger.info(
        "tier_resolved",
        tier=decision.tier.value,
        rule=decision.rule,
        explicit=explicit is not None,
        prompt_version=prompt_version,
    )
    return decision


@lru_cache
def get_session_store() -> SessionStore:
    """The single session store of the process (tests override this dependency)."""
    settings = get_settings()
    return SessionStore(
        max_sessions=settings.max_sessions,
        max_turns=settings.max_conversation_turns,
        max_anchors=settings.max_anchored_turns,
    )


def get_session_service(
    store: Annotated[SessionStore, Depends(get_session_store)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    moderator: Annotated[Moderator, Depends(get_moderator)],
    memory: Annotated[MemoryCompression, Depends(get_memory_compression)],
) -> SessionService:
    """FastAPI dependency: a service over the process store, provider, moderator and
    memory compression (tests override `get_memory_compression` with a deterministic one),
    with the Actor-Critic-Boss configuration of the settings (`BOSS_MAX_ITERATIONS` and
    `CRITIC_MODEL`, unset: the primary with fallback). No network at build time.
    """
    estimation = EstimationService(
        provider, resolve_model=settings.resolve_request_model, moderator=moderator
    )
    limits = AttachmentLimits(
        max_files=settings.max_attachments,
        max_bytes=settings.max_attachment_bytes,
        max_chars=settings.max_attachment_chars,
    )
    review = ReviewConfig(
        max_iterations=settings.boss_max_iterations,
        critic_target=(
            settings.resolve_request_model(settings.critic_model) if settings.critic_model else None
        ),
    )
    return SessionService(store, estimation, limits, memory=memory, review=review)
