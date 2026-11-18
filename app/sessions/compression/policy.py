"""Compression policy of the conversation history (design s05-memory D3, D4).

`compress_history` decides what happens to the turns that left the window: anchors are
kept verbatim (up to `max_anchors`, the oldest one demoted to the summary when full) and
the rest is folded into a single cumulative summary. It has no LLM dependency: the
detector and the summarizer are injected as callables (`detect`, `summarize`), so the
rule stays pure and is tested with local functions; `app.services.memory` provides the
real ones.

Failure semantics: a failing `summarize` never loses a turn. The previous summary is kept
and the turns that could not be summarized go to the backlog (capped at `max_turns`),
which is retried on the next compression. A failing `detect` (a bug) also leaves every
pending turn in the backlog before the exception propagates; the session service catches
it so the user's turn never fails because of the compression.

Logged here, without content: `anchor_detected`, `anchor_demoted`, `summary_failed` and
`summary_backlog_dropped`. The caller logs `history_compressed` from the report.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import structlog

from app.sessions.compression.anchors import AnchorDecision, AnchorSource
from app.sessions.models import Anchor, ConversationHistory, Turn

logger = structlog.get_logger(__name__)

Detect = Callable[[Turn], AnchorDecision]
# `summarize(previous summary, turns to integrate) -> new summary`; raises on failure.
Summarize = Callable[[str | None, Sequence[Turn]], str]


@dataclass(frozen=True)
class AnchoredTurn:
    """A turn anchored by this compression, for the logs (ids only, no text)."""

    turn: int
    rules: tuple[str, ...]
    source: AnchorSource


@dataclass(frozen=True)
class CompressionReport:
    """What one compression did; counts and ids only, never content."""

    evicted: int = 0  # turns taken out of the window by this compression
    anchored: int = 0  # evicted turns that became anchors
    demoted: int = 0  # anchors demoted to the summary because the cap was full
    summarized: int = 0  # turns integrated into the summary (0 if it failed)
    backlog: int = 0  # turns waiting in the backlog after this compression
    dropped: int = 0  # backlog turns discarded by this compression (cap)
    summary_updated: bool = False
    summary_error: str | None = None  # exception type of a failed `summarize`
    anchor_rules: tuple[AnchoredTurn, ...] = ()
    demoted_turns: tuple[int, ...] = ()


def compress_history(
    history: ConversationHistory,
    *,
    detect: Detect,
    summarize: Summarize,
    max_anchors: int,
) -> CompressionReport:
    """Turn the evicted turns into anchors, summary or backlog (design s05-memory D4).

    1. Take the evicted turns; with none and an empty backlog, return without calling
       `detect` or `summarize`.
    2. `detect` each evicted turn, oldest first (skipped when anchors are disabled,
       `max_anchors == 0`). Anchors are appended in order; past the cap, the oldest anchor
       is demoted to the turns to summarize. Non-anchors go to the turns to summarize.
    3. The backlog joins the turns to summarize, sorted by `number`.
    4. With turns to summarize, call `summarize(previous summary, turns)`: on success the
       summary is replaced and the backlog emptied; on any exception (or an invalid
       summary) the previous summary is kept and the turns go to the backlog.

    The effective cap is the smallest of `max_anchors` and `history.max_anchors`.
    """
    evicted = history.take_evicted()
    if not evicted and not history.backlog:
        return CompressionReport()
    cap = min(max_anchors, history.max_anchors)
    anchors = list(history.anchors)
    to_summarize: list[Turn] = []
    anchored: list[AnchoredTurn] = []
    demoted: list[int] = []
    try:
        for turn in evicted:
            decision = detect(turn) if cap > 0 else AnchorDecision(is_anchor=False)
            if not decision.is_anchor:
                to_summarize.append(turn)
                continue
            anchors.append(Anchor(turn=turn, rules=decision.rules))
            anchored.append(AnchoredTurn(turn.number, decision.rules, decision.source))
            if len(anchors) > cap:
                oldest = anchors.pop(0)
                to_summarize.append(oldest.turn)
                demoted.append(oldest.turn.number)
    except Exception:
        # Nothing is committed yet: keep every pending turn for the next compression.
        _to_backlog(history, [*history.backlog, *evicted])
        raise

    history.set_anchors(anchors)
    for event in anchored:
        if event.turn not in demoted:
            logger.info(
                "anchor_detected", turn=event.turn, rules=list(event.rules), source=event.source
            )
    for number in demoted:
        logger.info("anchor_demoted", turn=number, anchors=len(anchors))

    pending = sorted([*history.backlog, *to_summarize], key=lambda turn: turn.number)
    report = {
        "evicted": len(evicted),
        "anchored": len(anchored),
        "demoted": len(demoted),
        "anchor_rules": tuple(anchored),
        "demoted_turns": tuple(demoted),
    }
    if not pending:
        return CompressionReport(**report, backlog=len(history.backlog))
    try:
        text = summarize(history.summary, pending)
        history.set_summary(text, covered_turns=history.summarized_turns + len(pending))
    except Exception as exc:
        dropped = _to_backlog(history, pending)
        logger.warning(
            "summary_failed", error_type=type(exc).__name__, backlog=len(history.backlog)
        )
        return CompressionReport(
            **report,
            backlog=len(history.backlog),
            dropped=dropped,
            summary_error=type(exc).__name__,
        )
    history.set_backlog(())
    return CompressionReport(**report, summarized=len(pending), summary_updated=True)


def _to_backlog(history: ConversationHistory, turns: Sequence[Turn]) -> int:
    """Store `turns` as the backlog (deduplicated by number); log and return the drops."""
    unique = {turn.number: turn for turn in turns}
    dropped = history.set_backlog(list(unique.values()))
    if dropped:
        logger.warning("summary_backlog_dropped", dropped=dropped)
    return dropped
