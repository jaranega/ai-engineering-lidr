"""Session state: turns, the sliding-window history and the project metadata (D2-D4)."""

import threading
from collections import deque
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.domain import ChatMessage, Language


@dataclass(frozen=True)
class Turn:
    """One successful exchange: the stored `user` message and the `assistant` reply.

    `number` is the 1-based ordinal of the turn in the session; it survives the window,
    so it can anchor a later memory compression (D15).
    """

    number: int
    user: str
    assistant: str


class ConversationHistory:
    """Sliding window of the last `max_turns` turns (pairs, never loose messages).

    Storing pairs guarantees that `to_messages()` always starts with `user` and
    alternates, as Anthropic requires. The system prompt is not part of the history: the
    service renders it on every turn with the current project metadata (D3).
    """

    def __init__(self, max_turns: int) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        self._max_turns = max_turns
        self._turns: deque[Turn] = deque()
        self._last_number = 0

    def append(self, *, user: str, assistant: str) -> Turn:
        """Add a turn, then drop the oldest ones while there are more than `max_turns`."""
        if not user.strip() or not assistant.strip():
            raise ValueError("user and assistant messages must not be empty")
        self._last_number += 1
        turn = Turn(number=self._last_number, user=user, assistant=assistant)
        self._turns.append(turn)
        while len(self._turns) > self._max_turns:
            self._turns.popleft()
        return turn

    def to_messages(self) -> list[ChatMessage]:
        """`user`/`assistant` messages of the window, oldest first, without system."""
        messages: list[ChatMessage] = []
        for turn in self._turns:
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})
        return messages

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    @property
    def max_turns(self) -> int:
        return self._max_turns

    def __len__(self) -> int:
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

    def filled_fields(self) -> list[str]:
        """Names of the fields with a value (for logs and the prompt), in field order."""
        return [
            name for name in type(self).model_fields if getattr(self, name) not in (None, "", [])
        ]


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
    turn_count: int = 0  # successful turns, including those out of the window
    created_at: float = 0.0  # time.monotonic()
    updated_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
