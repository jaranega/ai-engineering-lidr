"""LLM side of the conversation memory (design s05-memory D5, D6, D12, D13).

The compression rule itself is pure (`app.sessions.compression.policy`); this module
provides the two LLM-backed callables it receives and assembles them:

- `ConversationSummarizer`: folds the turns that left the window (and are not anchors)
  into the session's single cumulative summary, in the session language, with a
  structured call (`ConversationSummary`) and the versioned `conversation_summary/v1`
  prompt, with the project (name and agreed scope) as context. Errors propagate: the
  policy keeps the previous summary and the backlog.
- `LLMAnchorClassifier` (`ANCHOR_DETECTION_MODE=llm`): heuristic first; the LLM is asked
  only when no rule fired, and any error falls back to the heuristic (negative) result.
- `MemoryCompression` and the FastAPI dependency `get_memory_compression`.

Both calls reuse `generate_structured` (cache, fallback, retries and `llm_call_*` logs)
without changes in the provider; their logs carry `llm_purpose` through structlog's
contextvars. Nothing here logs content: never the turns nor the summary text.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

import structlog
from fastapi import Depends
from pydantic import BaseModel, Field, field_validator

from app.config import Deployment, Settings, get_settings
from app.domain import Language
from app.prompts.loader import render_anchor_prompt, render_summary_prompt
from app.services.llm_service import (
    LLMCallOptions,
    LLMProvider,
    get_llm_provider,
)
from app.sessions.compression import (
    ANCHOR_RULE_IDS,
    AnchorDecision,
    CompressionReport,
    Detect,
    compress_history,
    detect_anchor,
)
from app.sessions.models import SUMMARY_MAX_CHARS, ConversationHistory, ProjectMetadata, Turn

logger = structlog.get_logger(__name__)

__all__ = [
    "ANCHOR_MAX_TOKENS",
    "SUMMARY_MAX_TOKENS",
    "AnchorVerdict",
    "ConversationSummarizer",
    "ConversationSummary",
    "LLMAnchorClassifier",
    "MemoryCompression",
    "SummarizeTurns",
    "get_memory_compression",
    "normalize_summary",
]

# Output limits of the memory calls. The summary is at most ~500 tokens (D6); the
# classifier's tool call (a boolean and a category) needs a few dozen.
SUMMARY_MAX_TOKENS = 800
ANCHOR_MAX_TOKENS = 200

# Tags of the estimation prompt the summary must never open or close (it is rendered
# inside <conversation_summary> in the system prompt, D7).
_PROMPT_TAG = re.compile(
    r"<\s*/?\s*(?:conversation_summary|project_metadata|transcript|attachments|audience)\s*>",
    re.IGNORECASE,
)
# End of a sentence: the punctuation is kept, the following whitespace is not.
_SENTENCE_END = re.compile(r"[.!?](?=\s)")


def normalize_summary(text: str) -> str:
    """Normalize a summary without a retry (D6): strip, remove the prompt's own tags and,
    past `SUMMARY_MAX_CHARS`, cut at the last end of sentence before the limit (or at the
    limit when there is none). Idempotent.
    """
    previous = None
    while previous != text:  # removing a tag could join the halves of another one
        previous, text = text, _PROMPT_TAG.sub("", text)
    text = text.strip()
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    # `[.!?]` followed by whitespace, with the punctuation within the limit.
    ends = [m.end() for m in _SENTENCE_END.finditer(text, 0, SUMMARY_MAX_CHARS + 1)]
    cut = ends[-1] if ends else SUMMARY_MAX_CHARS
    return text[:cut].rstrip()


class ConversationSummary(BaseModel):
    """Cumulative summary of the earlier turns of the conversation."""

    summary: str = Field(
        min_length=1,
        description="The complete updated summary of what the client said in the earlier "
        "turns, as short sentences in plain prose, in the requested language.",
    )

    @field_validator("summary", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        # Before validation, no retry: a long or tagged summary is fixed, not re-requested.
        return normalize_summary(value) if isinstance(value, str) else value


# `summarize(previous summary, turns, session language, project metadata) -> new summary`;
# raises on failure. The metadata (if any) only gives the project as context.
SummarizeTurns = Callable[
    [str | None, Sequence[Turn], Language | None, ProjectMetadata | None], str
]


class ConversationSummarizer:
    """Fold turns into the cumulative summary with one structured call (D6).

    `target` is the `COMPRESSION_MODEL` deployment (an override without fallback) or
    `None` for the primary model with fallback. Provider errors propagate.
    """

    def __init__(self, provider: LLMProvider, target: Deployment | None = None) -> None:
        self._provider = provider
        self._options = LLMCallOptions(model=target, max_tokens=SUMMARY_MAX_TOKENS)

    def __call__(
        self,
        previous: str | None,
        turns: Sequence[Turn],
        language: Language | None,
        project_metadata: ProjectMetadata | None = None,
    ) -> str:
        system, user = render_summary_prompt(
            previous, turns, language, project_metadata=project_metadata
        )
        with structlog.contextvars.bound_contextvars(llm_purpose="conversation_summary"):
            result = self._provider.generate_structured(
                system, [{"role": "user", "content": user}], ConversationSummary, self._options
            )
        return result.value.summary


AnchorCategory = Literal[
    "nda",
    "contract_signed",
    "scope_frozen",
    "budget_fixed",
    "regulation",
    "hard_deadline",
    "client_commitment",
]
if get_args(AnchorCategory) != ANCHOR_RULE_IDS:  # single source: the heuristic rules
    raise RuntimeError("AnchorCategory must list the anchor rule ids in order")


class AnchorVerdict(BaseModel):
    """Whether a client message holds a durable commitment (binary classifier, D5)."""

    is_anchor: bool = Field(
        description="True only if the message contains a durable commitment of the client."
    )
    category: AnchorCategory | None = Field(
        default=None, description="Category of the commitment when is_anchor is true; else null."
    )

    @field_validator("category", mode="before")
    @classmethod
    def _unknown_category_to_none(cls, value: Any) -> Any:
        # A secondary field never justifies a paid retry: an unknown category is dropped.
        return value if value in ANCHOR_RULE_IDS else None


class LLMAnchorClassifier:
    """Anchor detector of `ANCHOR_DETECTION_MODE=llm` (D5).

    1. The heuristic first: a match is an anchor without calling the LLM.
    2. Otherwise one structured call with the `anchor_classifier/v1` prompt; a positive
       verdict is `AnchorDecision(True, ("llm",), "llm")`.
    3. Any error: the heuristic (negative) result, `anchor_classifier_failed`, no raise.
    """

    def __init__(self, provider: LLMProvider, target: Deployment | None = None) -> None:
        self._provider = provider
        self._options = LLMCallOptions(model=target, max_tokens=ANCHOR_MAX_TOKENS)

    def __call__(self, turn: Turn) -> AnchorDecision:
        heuristic = detect_anchor(turn)
        if heuristic.is_anchor:
            return heuristic
        try:
            system, user = render_anchor_prompt(turn)
            with structlog.contextvars.bound_contextvars(llm_purpose="anchor_classifier"):
                result = self._provider.generate_structured(
                    system, [{"role": "user", "content": user}], AnchorVerdict, self._options
                )
        except Exception as exc:
            logger.warning(
                "anchor_classifier_failed", turn=turn.number, error_type=type(exc).__name__
            )
            return heuristic
        if result.value.is_anchor:
            return AnchorDecision(is_anchor=True, rules=("llm",), source="llm")
        return heuristic


@dataclass(frozen=True)
class MemoryCompression:
    """The detector, the summarizer and the anchor cap of the session memory (D12).

    `compress` runs the pure policy on `history` with the summarizer bound to the
    session language and the project metadata (the project as context of the summary;
    the caller passes the metadata already updated by the turn). Tests replace the whole
    object (FastAPI dependency) with a deterministic one built from local callables.
    """

    detect: Detect
    summarize: SummarizeTurns
    max_anchors: int

    def compress(
        self,
        history: ConversationHistory,
        language: Language | None,
        project_metadata: ProjectMetadata | None = None,
    ) -> CompressionReport:
        def summarize(previous: str | None, turns: Sequence[Turn]) -> str:
            return self.summarize(previous, turns, language, project_metadata)

        return compress_history(
            history, detect=self.detect, summarize=summarize, max_anchors=self.max_anchors
        )


def get_memory_compression(
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MemoryCompression:
    """FastAPI dependency: heuristic or LLM detector (`ANCHOR_DETECTION_MODE`), the
    summarizer on `COMPRESSION_MODEL` (unset: the primary with fallback) and
    `MAX_ANCHORED_TURNS`. No network at build time.
    """
    target = (
        settings.resolve_request_model(settings.compression_model)
        if settings.compression_model
        else None
    )
    detect: Detect = (
        LLMAnchorClassifier(provider, target)
        if settings.anchor_detection_mode == "llm"
        else detect_anchor
    )
    return MemoryCompression(
        detect=detect,
        summarize=ConversationSummarizer(provider, target),
        max_anchors=settings.max_anchored_turns,
    )
