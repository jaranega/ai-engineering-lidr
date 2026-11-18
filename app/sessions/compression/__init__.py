"""Memory compression of the conversation history (design s05-memory D2-D5).

Pure: the anchor detector (heuristic, ES+EN) and the compression policy, which receives
the LLM-backed callables from `app.services.memory`. Never imports `app.services`,
`app.prompts`, LiteLLM nor Instructor.
"""

from app.sessions.compression.anchors import (
    ANCHOR_RULE_IDS,
    ANCHOR_RULES,
    AnchorDecision,
    AnchorSource,
    detect_anchor,
    matching_rules,
)
from app.sessions.compression.policy import (
    AnchoredTurn,
    CompressionReport,
    Detect,
    Summarize,
    compress_history,
)

__all__ = [
    "ANCHOR_RULES",
    "ANCHOR_RULE_IDS",
    "AnchorDecision",
    "AnchorSource",
    "AnchoredTurn",
    "CompressionReport",
    "Detect",
    "Summarize",
    "compress_history",
    "detect_anchor",
    "matching_rules",
]
