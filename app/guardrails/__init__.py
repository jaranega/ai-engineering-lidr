"""Input guardrails (prompt injection and personal data), pure and offline.

Depends only on the standard library; never on `app.services` nor `app.prompts`. The
moderation call uses LiteLLM, so it lives in `app.services.moderation` (design D4, D8).
"""

from app.guardrails.input import (
    GUARDRAIL_MESSAGES,
    PII_PLACEHOLDERS,
    GuardrailHit,
    GuardrailReason,
    InputGuardrailViolation,
    PiiKind,
    mask_pii,
    screen_texts,
)

__all__ = [
    "GUARDRAIL_MESSAGES",
    "PII_PLACEHOLDERS",
    "GuardrailHit",
    "GuardrailReason",
    "InputGuardrailViolation",
    "PiiKind",
    "mask_pii",
    "screen_texts",
]
