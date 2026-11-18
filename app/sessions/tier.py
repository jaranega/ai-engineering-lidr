"""Audience tier of a session turn, resolved by pure rules (design s05-memory D9).

The tier adapts what content goes into each field of the single estimation schema
(prompt v5, `<audience>`); it never changes the schema, the model or the cost. It is
resolved on every turn, before rendering the prompt, from the turn's transcript, the
stored client messages of the anchors and the window (`history_messages`) and the
accumulated project metadata (never from the attachments' text nor the summary), with a
chain of deterministic rules where the first match wins. An NDA or a regulation stays in
force even when the client stops naming it: the history keeps it counting while its turn
is anchored or in the window, and `explicit_constraints` afterwards, so the tier does not
depend on the model filling that field (D16, first measurement). `technical_audience`
only reads the current transcript.

The NDA and regulation patterns are the very objects of the anchor detector (single
source). No LLM, no network; never imports `app.services` nor `app.prompts`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.sessions.compression.anchors import NDA_PATTERNS, REGULATION_PATTERNS, matches_any
from app.sessions.models import ProjectMetadata


class Tier(StrEnum):
    """Audience of the estimate; `default` means no adaptation."""

    EXECUTIVE = "executive"
    PM = "pm"
    DEVELOPER = "developer"
    DEFAULT = "default"


# Rule that decided the tier. `prompt_version` is set by the session service when the
# prompt version (v4) has no audience block, without evaluating the rules.
TierRule = Literal[
    "explicit",
    "nda_detected",
    "regulatory_context",
    "technical_audience",
    "small_team",
    "default",
    "prompt_version",
]


@dataclass(frozen=True)
class TierDecision:
    tier: Tier
    rule: TierRule


# Distinct technical terms needed for `technical_audience` (the live session's threshold).
TECHNICAL_TERMS_THRESHOLD = 2
# `small_team`: recommended team of at most this many people.
SMALL_TEAM_MAX = 2

# Technical vocabulary, Spanish and English; each group of synonyms counts once. Case is
# ignored and words are bounded. General product names any client uses (Stripe, iOS,
# Shopify) and words such as "app", "web" or "datos" are deliberately absent.
_TECHNICAL_TERMS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # api/REST is one group ("REST API" counts once). REST only as the uppercase
        # acronym, "RESTful" or next to "api", so that "the rest of the app" never counts.
        r"\bapis?\b|(?-i:\bREST\b)|\brestful\b",
        r"\bgraphql\b",
        r"\bendpoints?\b",
        r"\bwebhooks?\b",
        r"\bsdks?\b",
        r"\bmicroservici(?:o|os)\b|\bmicroservices?\b",
        r"\bkubernetes\b|\bk8s\b",
        r"\bdocker\b",
        r"\bci/cd\b",
        r"\bterraform\b",
        r"\bserverless\b",
        r"\blambdas?\b",
        r"\boauth2?\b",
        r"\bjwt\b",
        r"\borm\b",
        r"\bbackend\b",
        r"\bfrontend\b",
        r"\bpostgres(?:ql)?\b",
        r"\bmysql\b",
        r"\bmongodb\b",
        r"\bredis\b",
        r"\bkafka\b",
        r"\btypescript\b",
        r"\blatencia\b|\blatency\b",
        r"\bthroughput\b",
        r"\bescalado horizontal\b|\bhorizontal scaling\b",
        r"\besquema de (?:la )?base de datos\b|\bdatabase schema\b",
        r"\brefactor(?:izar)?\b",
        r"\biac\b",
        r"\bhelm\b",
        r"\bgrpc\b",
        r"\bairflow\b",
        r"\bspark\b",
        r"\brabbitmq\b",
    )
)


def technical_terms_count(text: str) -> int:
    """Number of distinct technical term groups in `text`."""
    return sum(1 for pattern in _TECHNICAL_TERMS if pattern.search(text))


def resolve_tier(
    transcript: str,
    metadata: ProjectMetadata,
    explicit: Tier | None,
    history_messages: Sequence[str] = (),
) -> TierDecision:
    """First matching rule of the chain (design s05-memory D9):

    0. `explicit`: the requested tier, also `default` (forces no adaptation);
    1. `nda_detected` -> executive: NDA patterns in the transcript, in any of
       `history_messages` (the stored `user` messages of the anchors and the window,
       attachments as `[…]`) or in any of the metadata's `explicit_constraints`;
    2. `regulatory_context` -> executive: regulation patterns, same sources;
    3. `technical_audience` -> developer: at least 2 distinct technical terms in the
       transcript;
    4. `small_team` -> pm: the recommended team (`assumed_team_size`) is known and <= 2;
    5. `default`.
    """
    if explicit is not None:
        return TierDecision(Tier(explicit), "explicit")
    sources = (transcript, *history_messages, *metadata.explicit_constraints)
    if any(matches_any(NDA_PATTERNS, text) for text in sources):
        return TierDecision(Tier.EXECUTIVE, "nda_detected")
    if any(matches_any(REGULATION_PATTERNS, text) for text in sources):
        return TierDecision(Tier.EXECUTIVE, "regulatory_context")
    if technical_terms_count(transcript) >= TECHNICAL_TERMS_THRESHOLD:
        return TierDecision(Tier.DEVELOPER, "technical_audience")
    size = metadata.assumed_team_size
    if size is not None and size <= SMALL_TEAM_MAX:
        return TierDecision(Tier.PM, "small_team")
    return TierDecision(Tier.DEFAULT, "default")
