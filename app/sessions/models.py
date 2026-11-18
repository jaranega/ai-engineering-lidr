"""Session state: turns, the history (window, anchors, summary) and the project metadata.

S05 design D2-D4; anchors, summary and backlog from design s05-memory D3.
"""

import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain import ChatMessage, Language

if TYPE_CHECKING:  # `tier` imports this module; the annotation only needs the name
    from app.sessions.tier import Tier, TierDecision

# Maximum length of the cumulative conversation summary (~500 tokens, design s05-memory D6).
# A constant, not configuration; the summarizer's normalizer cuts to it.
SUMMARY_MAX_CHARS = 2000


@dataclass(frozen=True)
class Turn:
    """One successful exchange: the stored `user` message and the `assistant` reply.

    `number` is the 1-based ordinal of the turn in the session; it survives the window,
    so it can anchor a later memory compression (D15).
    """

    number: int
    user: str
    assistant: str


@dataclass(frozen=True)
class Anchor:
    """A turn kept verbatim out of the window because it holds a durable client
    commitment; `rules` are the detector rules that fired (design s05-memory D3, D5).
    """

    turn: Turn
    rules: tuple[str, ...]


class ConversationHistory:
    """Sliding window of the last `max_turns` turns (pairs, never loose messages), plus
    the memory of what left it (design s05-memory D3).

    Storing pairs guarantees that `to_messages()` always starts with `user` and
    alternates, as Anthropic requires. The system prompt is not part of the history: the
    service renders it on every turn with the current project metadata (S05 D3).

    Turns pushed out of the window wait in an internal list until `take_evicted()`; the
    compression policy (`app.sessions.compression.policy`) then turns them into anchors,
    the cumulative summary or the backlog with `set_anchors`, `set_summary` and
    `set_backlog`. Every one of those values is replaced whole (tuples and `str`), so a
    reader without the session lock sees either the previous or the new state.

    Conservation invariant, after each compression: `len(self) + len(anchors) +
    summarized_turns + len(backlog) + dropped_turns` equals the number of appended turns.
    """

    def __init__(self, max_turns: int, max_anchors: int = 0) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if max_anchors < 0:
            raise ValueError("max_anchors must not be negative")
        self._max_turns = max_turns
        self._max_anchors = max_anchors
        self._turns: deque[Turn] = deque()
        self._evicted: list[Turn] = []
        self._last_number = 0
        self._anchors: tuple[Anchor, ...] = ()
        self._summary: str | None = None
        self._summarized_turns = 0
        self._backlog: tuple[Turn, ...] = ()
        self._dropped_turns = 0

    def append(self, *, user: str, assistant: str) -> Turn:
        """Add a turn, then move the oldest ones out of the window (to the evicted list)
        while there are more than `max_turns`.
        """
        if not user.strip() or not assistant.strip():
            raise ValueError("user and assistant messages must not be empty")
        self._last_number += 1
        turn = Turn(number=self._last_number, user=user, assistant=assistant)
        self._turns.append(turn)
        while len(self._turns) > self._max_turns:
            self._evicted.append(self._turns.popleft())
        return turn

    def take_evicted(self) -> list[Turn]:
        """Turns that left the window since the last call, oldest first; clears them."""
        evicted, self._evicted = self._evicted, []
        return evicted

    def to_messages(self) -> list[ChatMessage]:
        """`user`/`assistant` messages of the anchors and then of the window, oldest
        first, without system nor summary (the summary goes in the system prompt).
        """
        messages: list[ChatMessage] = []
        for turn in (*(anchor.turn for anchor in self._anchors), *self._turns):
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})
        return messages

    # State written by the compression policy (design s05-memory D4).

    def set_anchors(self, anchors: Sequence[Anchor]) -> None:
        """Replace the anchors; `ValueError` past `max_anchors` or out of order."""
        anchors = tuple(anchors)
        if len(anchors) > self._max_anchors:
            raise ValueError("too many anchors")
        numbers = [anchor.turn.number for anchor in anchors]
        if numbers != sorted(set(numbers)):
            raise ValueError("anchors must be in chronological order without repeats")
        if self._turns and numbers and numbers[-1] >= self._turns[0].number:
            raise ValueError("anchors must be older than every turn of the window")
        self._anchors = anchors

    def set_summary(self, text: str, *, covered_turns: int) -> None:
        """Replace the summary, which now covers `covered_turns` turns in total.

        `ValueError` for a blank text, one over `SUMMARY_MAX_CHARS` or a negative count.
        """
        if not text.strip():
            raise ValueError("summary must not be empty")
        if len(text) > SUMMARY_MAX_CHARS:
            raise ValueError(f"summary must be at most {SUMMARY_MAX_CHARS} characters")
        if covered_turns < 0:
            raise ValueError("covered_turns must not be negative")
        self._summary = text
        self._summarized_turns = covered_turns

    def set_backlog(self, turns: Sequence[Turn]) -> int:
        """Replace the backlog (turns still to be summarized), sorted by `number`.

        The backlog is capped at `max_turns`: the oldest turns past it are discarded and
        counted in `dropped_turns`. Return how many were discarded by this call.
        """
        ordered = sorted(turns, key=lambda turn: turn.number)
        dropped = max(0, len(ordered) - self._max_turns)
        self._backlog = tuple(ordered[dropped:])
        self._dropped_turns += dropped
        return dropped

    # Read-only views.

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    @property
    def anchors(self) -> tuple[Anchor, ...]:
        return self._anchors

    @property
    def summary(self) -> str | None:
        return self._summary

    @property
    def summarized_turns(self) -> int:
        return self._summarized_turns

    @property
    def backlog(self) -> tuple[Turn, ...]:
        return self._backlog

    @property
    def dropped_turns(self) -> int:
        return self._dropped_turns

    @property
    def max_turns(self) -> int:
        return self._max_turns

    @property
    def max_anchors(self) -> int:
        return self._max_anchors

    def __len__(self) -> int:
        """Turns in the window only (anchors, summary and backlog are not counted)."""
        return len(self._turns)


class ProjectMetadata(BaseModel):
    """Facts agreed in previous turns, injected into the system prompt (D4).

    Derived deterministically from each in-scope estimate (no extra LLM call) and
    replaced whole after every successful turn. Also the HTTP schema of the field.
    """

    model_config = ConfigDict(frozen=True)

    project_name: str | None = Field(default=None, description="Title of the last estimate.")
    assumed_team_size: int | None = Field(
        default=None,
        description="People in the recommended team (part-time profiles count as people).",
    )
    mentioned_technologies: list[str] = Field(
        default=[], description="Technologies named by the client, accumulated across turns."
    )
    agreed_scope: str | None = Field(default=None, description="Summary of the last estimate.")
    explicit_constraints: list[str] = Field(
        default=[],
        description="Binding constraints stated by the client, accumulated across turns "
        "(design s05-memory D8).",
    )
    rejected_options: list[str] = Field(
        default=[],
        description="Technologies or vendors the client discarded, accumulated across turns; "
        "never listed in mentioned_technologies.",
    )

    def filled_fields(self) -> list[str]:
        """Names of the fields with a value (for logs and the prompt), in field order."""
        return [
            name for name in type(self).model_fields if getattr(self, name) not in (None, "", [])
        ]


@dataclass(frozen=True)
class LastReview:
    """Outcome of the Actor-Critic-Boss review of a turn (design s05-acb D8): the final
    decision (`accept` | `synthesize`), the stop reason and the iterations run. Never the
    Critic's issues.
    """

    final_decision: str
    stop_reason: str
    iterations_run: int


# Where the result of a turn came from: the exact-match cache or a fresh generation.
# "semantic" is reserved: there is no semantic cache yet (design s06 D1).
CacheHitKind = Literal["none", "exact", "semantic"]


@dataclass(frozen=True)
class TurnObservation:
    """Observation of a successful session turn (design s06 D1): the 13 fields of the
    `turn_observed` event, with the same names. Numbers and identifiers only, never text.
    """

    turn_index: int  # 1-based ordinal of the turn in the session
    session_id: str  # short id (first 8 chars), never the full id: it grants access
    enriched_transcript_chars: int  # transcript + masked attachment text
    attachments_total_chars: int  # masked attachment text; 0 without attachments
    messages_in_window: int  # anchor + window messages after the compression
    anchors_count: int
    summary_chars: int  # 0 without a summary
    tokens_in: int  # summed over every call of the turn (review included)
    tokens_out: int
    cost_usd: float | None  # None when any call has no known price
    latency_ms: int  # whole `SessionService.estimate`, compression included
    cache_hit_kind: CacheHitKind
    last_resolved_tier: "Tier"  # tier applied to the turn


@dataclass(eq=False)
class Session:
    """In-process conversation state. Volatile by design (S05 exercise): lost on restart and
    not shared between workers; persistence belongs to the deployment module.

    Every mutation of a turn happens while `lock` is held (`SessionStore.turn`); readers
    see either the previous or the new state, because `project_metadata` is immutable and
    replaced whole.
    """

    session_id: str  # canonical lowercase UUID4
    history: ConversationHistory
    project_metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    language: Language | None = None  # effective language of the last successful turn
    last_tier: "TierDecision | None" = None  # tier of the last successful turn
    # Review of the last successful turn; None when that turn was not reviewed.
    last_review: LastReview | None = None
    # Observation of the last successful turn (design s06 D1); None before the first.
    last_observation: TurnObservation | None = None
    turn_count: int = 0  # successful turns, including those out of the window
    created_at: float = 0.0  # time.monotonic()
    updated_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
