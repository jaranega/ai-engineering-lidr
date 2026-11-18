"""Boss of the Actor-Critic-Boss review (design s05-acb D5, D6): pure decision logic.

`run_review(actor, critic, *, max_iterations)` asks the Actor for a draft, lets the
Critic review it and decides, per iteration, whether to accept it, ask for another draft
with the feedback, or synthesize the last draft with the open issues as warnings. It
makes no LLM call and no I/O (only the `boss_iteration` log event): the Actor and the
Critic are callables wired by `EstimationService.estimate_turn`, so every branch can be
tested with doubles.

- An out-of-scope draft (confidence below 30) is accepted without calling the Critic.
- `accept` (or a failed Critic, fail-open) accepts; `reject` synthesizes;
  `needs_iteration` asks for another draft while the budget lasts, then synthesizes.
- A failure of the first draft propagates (the turn fails as without review); an
  `LLMProviderError` of a later draft synthesizes the last valid draft.

Synthesizing never calls the LLM: the last draft gets the open issues as warnings at the
start of `assumptions_and_risks` and a reduced confidence that never drops out of scope.
This module imports neither LiteLLM nor Instructor.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import structlog

from app.domain import Language
from app.estimation.compute import ComputedEstimate
from app.estimation.schema import (
    MAX_ASSUMPTIONS,
    OUT_OF_SCOPE_CONFIDENCE,
    ROUNDING_STEP,
    EstimationResult,
)
from app.services.critic import (
    BLOCKING_SEVERITIES,
    CriticFeedback,
    CriticIssue,
    CriticReview,
    CriticVerdict,
)
from app.services.errors import LLMProviderError

logger = structlog.get_logger(__name__)

__all__ = [
    "SYNTHESIS_LABELS",
    "BossDecision",
    "BossIteration",
    "BossTrace",
    "Draft",
    "FinalDecision",
    "ReviewRun",
    "StopReason",
    "run_review",
    "synthesize",
]

BossDecision = Literal["accept", "iterate", "synthesize"]
FinalDecision = Literal["accept", "synthesize"]
StopReason = Literal[
    "accepted", "out_of_scope", "critic_failed", "rejected", "budget_exhausted", "actor_failed"
]

# Label of the synthesis warnings, by the effective language of the turn (D6).
SYNTHESIS_LABELS: dict[Language, str] = {
    "es": "Revisión independiente",
    "en": "Independent review",
}
# Maximum length of an item of `assumptions_and_risks` (`ShortItem`).
MAX_WARNING_CHARS = 300


@dataclass(frozen=True)
class Draft:
    """One draft of the Actor: the result as returned by the LLM (what the synthesis
    edits) and its computed version (what the Critic and the trace read).
    """

    result: EstimationResult
    estimate: ComputedEstimate


@dataclass(frozen=True)
class BossIteration:
    """Trace of one iteration: the draft, the Critic's review and the Boss' decision.

    `verdict` and `critic_confidence` are `None` when the Critic was not called (an
    out-of-scope draft); a failed Critic has `critic_failed=True`, verdict `accept` and
    confidence 0. `issues` are those of the review, in order (empty without a review).
    """

    iteration: int  # 1-based
    verdict: CriticVerdict | None
    critic_confidence: int | None
    critic_failed: bool
    decision: BossDecision
    draft_confidence_pct: int
    draft_total_hours: int
    issues: tuple[CriticIssue, ...] = ()


@dataclass(frozen=True)
class BossTrace:
    """Trace of a whole review, the domain view of the HTTP `acb` field.

    `actor_calls` and `critic_calls` count every call made, failed ones included.
    """

    final_decision: FinalDecision
    stop_reason: StopReason
    max_iterations: int
    iterations: tuple[BossIteration, ...]
    actor_calls: int
    critic_calls: int

    @property
    def iterations_run(self) -> int:
        return len(self.iterations)

    @property
    def llm_calls(self) -> int:
        return self.actor_calls + self.critic_calls


@dataclass(frozen=True)
class ReviewRun:
    """Outcome of `run_review`: the final result (synthesized or not), the draft it comes
    from and the trace.
    """

    final: EstimationResult
    draft: Draft
    trace: BossTrace

    @property
    def final_decision(self) -> FinalDecision:
        return self.trace.final_decision

    @property
    def stop_reason(self) -> StopReason:
        return self.trace.stop_reason


# `actor(feedback)`: a new draft, with the last review's feedback from the second on.
Actor = Callable[[CriticFeedback | None], Draft]
# `critic(draft)`: its review; a bare `CriticFeedback` counts as a review that did not fail.
Critic = Callable[[Draft], CriticReview | CriticFeedback]


def _as_review(value: CriticReview | CriticFeedback) -> CriticReview:
    return value if isinstance(value, CriticReview) else CriticReview(feedback=value)


def _synthesized_confidence(confidence_pct: int) -> int:
    """Half the confidence, rounded down to a multiple of 5, never below the out-of-scope
    threshold (D6): a synthesized draft is never out of scope.
    """
    halved = confidence_pct // 2
    return max(OUT_OF_SCOPE_CONFIDENCE, halved - halved % ROUNDING_STEP)


def _warning(label: str, description: str) -> str:
    return f"{label}: {description}"[:MAX_WARNING_CHARS].rstrip()


def synthesize(
    result: EstimationResult, feedback: CriticFeedback | None, language: Language
) -> EstimationResult:
    """The draft with the open issues as warnings and a reduced confidence (D6).

    One warning per `critical`/`major` issue (the `minor` ones only when there is none),
    `"<label>: <description>"` cut to 300 characters, first in `assumptions_and_risks`,
    followed by the draft's own items that fit in `MAX_ASSUMPTIONS`. No LLM, no
    validation (`model_copy`): the class of the draft (its format requirements) is kept.
    """
    issues = list(feedback.issues) if feedback is not None else []
    blocking = [issue for issue in issues if issue.severity in BLOCKING_SEVERITIES]
    label = SYNTHESIS_LABELS.get(language, SYNTHESIS_LABELS["en"])
    warnings = [_warning(label, issue.description) for issue in blocking or issues]
    assumptions = [*warnings, *result.assumptions_and_risks][:MAX_ASSUMPTIONS]
    return result.model_copy(
        update={
            "assumptions_and_risks": assumptions,
            "confidence_pct": _synthesized_confidence(result.confidence_pct),
        }
    )


def _log_iteration(entry: BossIteration, out_of_scope: bool) -> None:
    logger.info(
        "boss_iteration",
        iteration=entry.iteration,
        decision=entry.decision,
        verdict=entry.verdict,
        issues=len(entry.issues),
        draft_out_of_scope=out_of_scope,
    )


def _decide(
    verdict: CriticVerdict, failed: bool, remaining: int
) -> tuple[BossDecision, StopReason | None]:
    if verdict == "accept":
        return "accept", "critic_failed" if failed else "accepted"
    if verdict == "reject":
        return "synthesize", "rejected"
    if remaining > 0:
        return "iterate", None
    return "synthesize", "budget_exhausted"


def run_review(actor: Actor, critic: Critic, *, max_iterations: int) -> ReviewRun:
    """Run the Actor-Critic-Boss loop (D5). Raise `ValueError` if `max_iterations < 1`;
    any exception of the first draft propagates.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    entries: list[BossIteration] = []
    actor_calls = critic_calls = 0
    feedback: CriticFeedback | None = None

    def finish(draft: Draft, decision: FinalDecision, reason: StopReason) -> ReviewRun:
        final = draft.result
        if decision == "synthesize":
            final = synthesize(draft.result, feedback, draft.estimate.language)
        trace = BossTrace(
            final_decision=decision,
            stop_reason=reason,
            max_iterations=max_iterations,
            iterations=tuple(entries),
            actor_calls=actor_calls,
            critic_calls=critic_calls,
        )
        return ReviewRun(final=final, draft=draft, trace=trace)

    actor_calls += 1
    draft = actor(None)  # a failure here is the turn's failure
    for iteration in range(1, max_iterations + 1):
        estimate = draft.estimate
        if estimate.out_of_scope:
            entry = BossIteration(
                iteration=iteration,
                verdict=None,
                critic_confidence=None,
                critic_failed=False,
                decision="accept",
                draft_confidence_pct=estimate.confidence_pct,
                draft_total_hours=estimate.total_hours,
            )
            entries.append(entry)
            _log_iteration(entry, out_of_scope=True)
            return finish(draft, "accept", "out_of_scope")

        critic_calls += 1
        review = _as_review(critic(draft))
        feedback = review.feedback
        decision, reason = _decide(feedback.verdict, review.failed, max_iterations - iteration)
        entry = BossIteration(
            iteration=iteration,
            verdict=feedback.verdict,
            critic_confidence=feedback.confidence_in_review,
            critic_failed=review.failed,
            decision=decision,
            draft_confidence_pct=estimate.confidence_pct,
            draft_total_hours=estimate.total_hours,
            issues=tuple(feedback.issues),
        )
        entries.append(entry)
        _log_iteration(entry, out_of_scope=False)
        if decision != "iterate":
            assert reason is not None
            return finish(draft, decision, reason)

        actor_calls += 1
        try:
            draft = actor(feedback)
        except LLMProviderError:
            # Trying to improve never fails the turn: keep the last valid draft (D5).
            return finish(draft, "synthesize", "actor_failed")
    # Unreachable: the last iteration never decides "iterate".
    raise AssertionError("review loop ended without a decision")
