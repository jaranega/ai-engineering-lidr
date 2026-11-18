"""Critic of the Actor-Critic-Boss review (design s05-acb D3, D7).

The Critic audits one computed draft with a structured call (`CriticFeedback`) and the
versioned `critic/v1` prompt. It sees the transcript of the turn, the session's project
metadata, the cumulative summary, the resolved tier, the effective language and the
draft; never the attachments' text nor the history, so its cost does not grow with the
conversation.

It is fail-open: any `LLMProviderError` (including `StructuredOutputError`) becomes an
`accept` with `confidence_in_review = 0` marked as failed, so a Critic that is down never
fails the turn. The verdict rules are validated (`needs_iteration` needs a `critical` or
`major` issue, `reject` needs an issue), so a broken review triggers Instructor's
validation retries like the estimation.

This module does not import LiteLLM, Instructor nor `app.services.llm_service`: the
caller passes the provider and the call options (`CRITIC_MODEL` or the primary with
fallback). Events never carry text: no description, fix, field path nor estimate.
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

import structlog
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.domain import Language
from app.estimation.compute import ComputedEstimate
from app.prompts.loader import render_critic_prompt
from app.services.errors import LLMProviderError
from app.sessions.models import ProjectMetadata
from app.sessions.tier import Tier

if TYPE_CHECKING:  # annotations only: `llm_service` imports this module
    from app.services.llm_service import LLMCallOptions, LLMProvider, StructuredResult

logger = structlog.get_logger(__name__)

__all__ = [
    "BLOCKING_SEVERITIES",
    "CRITIC_MAX_TOKENS",
    "MAX_CRITIC_ISSUES",
    "Critic",
    "CriticCategory",
    "CriticFeedback",
    "CriticIssue",
    "CriticReview",
    "CriticSeverity",
    "CriticVerdict",
]

CriticCategory = Literal[
    "hallucination",
    "scope_mismatch",
    "constraint_conflict",
    "phase_imbalance",
    "missing_assumption",
    "unrealistic_estimate",
    "tier_mismatch",
]
CriticSeverity = Literal["critical", "major", "minor"]
CriticVerdict = Literal["accept", "needs_iteration", "reject"]

MAX_CRITIC_ISSUES = 12
# Output limit of the Critic's tool call: up to 12 issues of ~100 tokens each.
CRITIC_MAX_TOKENS = 2048
# Severities that justify a retry of the Actor.
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"critical", "major"})

FieldPath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
IssueText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300)]
FixText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class CriticIssue(BaseModel):
    """One defect of the estimate under review."""

    category: CriticCategory = Field(description="Category of the defect.")
    severity: CriticSeverity = Field(
        description="critical or major only when the defect would lead to a materially "
        "worse decision; minor otherwise or when in doubt."
    )
    field_path: FieldPath = Field(
        description="Field of the estimate, e.g. summary, rows[3].hours, phases[1], team, "
        "duration_weeks, assumptions_and_risks, technologies."
    )
    description: IssueText = Field(
        description="One or two sentences explaining the defect, in the requested language."
    )
    suggested_fix: FixText | None = Field(
        default=None,
        description="Optional concrete fix in one sentence, in the requested language.",
    )

    @field_validator("suggested_fix", mode="before")
    @classmethod
    def _blank_fix_to_none(cls, value: Any) -> Any:
        # An empty optional field never justifies a paid retry.
        return None if isinstance(value, str) and not value.strip() else value


class CriticFeedback(BaseModel):
    """Structured review of an estimate by an independent auditor."""

    # Issues before the verdict: the model decides after listing what it found.
    issues: list[CriticIssue] = Field(
        default=[],
        max_length=MAX_CRITIC_ISSUES,
        description="Defects found, one per defect; empty if the estimate is correct.",
    )
    verdict: CriticVerdict = Field(
        description="accept without critical or major issues; needs_iteration only with a "
        "critical or major issue a new attempt can fix; reject only when the estimate is "
        "fundamentally wrong. When in doubt, accept."
    )
    confidence_in_review: int = Field(
        ge=0, le=100, description="How sure you are of this review (0-100)."
    )

    @model_validator(mode="after")
    def _check_verdict(self) -> Self:
        # Messages go back to the model on a retry: fixed English, no values.
        if self.verdict == "needs_iteration" and not any(
            issue.severity in BLOCKING_SEVERITIES for issue in self.issues
        ):
            raise ValueError(
                "needs_iteration requires at least one critical or major issue; "
                "otherwise use accept"
            )
        if self.verdict == "reject" and not self.issues:
            raise ValueError("reject requires at least one issue")
        return self


@dataclass(frozen=True)
class CriticReview:
    """Result of one Critic call: the feedback, whether the Critic failed (then the
    feedback is the fail-open `accept` with confidence 0) and the provider call, for the
    turn metrics (`None` when it failed).
    """

    feedback: CriticFeedback
    failed: bool = False
    call: "StructuredResult[CriticFeedback] | None" = None


def _failed_feedback() -> CriticFeedback:
    return CriticFeedback(issues=[], verdict="accept", confidence_in_review=0)


class Critic:
    """Structured review of a computed draft (design s05-acb D3, D7).

    `options` carry the `CRITIC_MODEL` deployment (an override without fallback) or
    `None` for the primary with fallback, and `CRITIC_MAX_TOKENS`. The provider events of
    the call carry `llm_purpose="critic"`.
    """

    def __init__(self, provider: "LLMProvider", options: "LLMCallOptions") -> None:
        self._provider = provider
        self._options = options

    def review(
        self,
        estimate: ComputedEstimate,
        *,
        transcript: str,
        project_metadata: ProjectMetadata | None,
        conversation_summary: str | None,
        tier: Tier,
        language: Language | None,
        iteration: int,
    ) -> CriticReview:
        """Review `estimate`; never raises for a provider failure (fail-open).

        Logs `critic_completed` (counts, verdict, categories, confidence, latency) or
        `critic_failed` (`error_type`), with the `iteration` of the draft.
        """
        start = time.perf_counter()
        system, user = render_critic_prompt(
            transcript=transcript,
            project_metadata=project_metadata,
            conversation_summary=conversation_summary,
            tier=tier,
            language=language,
            estimate=estimate,
        )
        try:
            with structlog.contextvars.bound_contextvars(llm_purpose="critic"):
                call = self._provider.generate_structured(
                    system, [{"role": "user", "content": user}], CriticFeedback, self._options
                )
        except LLMProviderError as exc:
            logger.warning("critic_failed", iteration=iteration, error_type=type(exc).__name__)
            return CriticReview(feedback=_failed_feedback(), failed=True)
        feedback = call.value
        severities = [issue.severity for issue in feedback.issues]
        logger.info(
            "critic_completed",
            iteration=iteration,
            verdict=feedback.verdict,
            issues=len(feedback.issues),
            critical=severities.count("critical"),
            major=severities.count("major"),
            minor=severities.count("minor"),
            categories=sorted({issue.category for issue in feedback.issues}),
            confidence=feedback.confidence_in_review,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        return CriticReview(feedback=feedback, call=call)
