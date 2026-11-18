"""actor-critic-boss: `run_review` (every branch) and the synthesis. Pure logic: the Actor
and the Critic are doubles, no LLM and no I/O.
"""

import pytest
from structlog.testing import capture_logs

from app.estimation.compute import compute_estimate
from app.estimation.schema import EstimationResult
from app.services.boss import SYNTHESIS_LABELS, Draft, run_review, synthesize
from app.services.critic import CriticFeedback, CriticReview
from app.services.llm_service import LLMProviderError
from tests.conftest import critic_issue, critic_payload, out_of_scope_payload, valid_payload


def make_draft(**overrides) -> Draft:
    result = EstimationResult.model_validate(valid_payload(**overrides))
    return Draft(result=result, estimate=compute_estimate(result, result.language))


def fb(verdict: str = "accept", *issues: dict, confidence: int = 80) -> CriticFeedback:
    return CriticFeedback.model_validate(critic_payload(verdict, issues, confidence))


MAJOR = critic_issue("major", "scope_mismatch", "rows[2].hours", "Falta la integración con el TPV")
NEEDS = fb("needs_iteration", MAJOR)


class Actor:
    """Scripted Actor: returns the n-th draft (the last repeats) or raises an exception."""

    def __init__(self, *drafts: Draft | Exception) -> None:
        self.drafts = drafts or (make_draft(),)
        self.received: list[CriticFeedback | None] = []

    def __call__(self, feedback: CriticFeedback | None) -> Draft:
        self.received.append(feedback)
        item = self.drafts[min(len(self.received), len(self.drafts)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


class Critic:
    def __init__(self, *reviews: CriticFeedback | CriticReview) -> None:
        self.reviews = reviews
        self.drafts: list[Draft] = []

    def __call__(self, draft: Draft) -> CriticFeedback | CriticReview:
        self.drafts.append(draft)
        return self.reviews[min(len(self.drafts), len(self.reviews)) - 1]


# --- Bucle ---


def test_accept_on_the_first_draft():
    """Scenario: Aceptación a la primera."""
    actor, critic = Actor(), Critic(fb("accept"))
    run = run_review(actor, critic, max_iterations=3)
    assert actor.received == [None] and len(critic.drafts) == 1
    assert (run.final_decision, run.stop_reason) == ("accept", "accepted")
    assert [it.decision for it in run.trace.iterations] == ["accept"]
    assert run.final is run.draft.result
    assert (run.trace.actor_calls, run.trace.critic_calls, run.trace.llm_calls) == (1, 1, 2)


def test_retry_then_accept_passes_the_feedback_to_the_second_draft():
    """Scenario: Reintento y aceptación."""
    first, second = make_draft(title="First"), make_draft(title="Second")
    actor, critic = Actor(first, second), Critic(NEEDS, fb("accept"))
    run = run_review(actor, critic, max_iterations=3)
    assert actor.received == [None, NEEDS]
    assert critic.drafts == [first, second]
    assert (run.final_decision, run.stop_reason) == ("accept", "accepted")
    assert [it.decision for it in run.trace.iterations] == ["iterate", "accept"]
    assert run.draft is second and run.final is second.result
    assert run.trace.iterations_run == 2 and run.trace.max_iterations == 3


def test_budget_exhausted_synthesizes_the_last_draft():
    """Scenario: Presupuesto agotado."""
    actor, critic = Actor(), Critic(NEEDS)
    run = run_review(actor, critic, max_iterations=3)
    assert len(actor.received) == 3 and len(critic.drafts) == 3
    assert (run.final_decision, run.stop_reason) == ("synthesize", "budget_exhausted")
    assert [it.decision for it in run.trace.iterations] == ["iterate", "iterate", "synthesize"]
    assert run.final.assumptions_and_risks[0].startswith("Independent review: ")


def test_reject_synthesizes_on_the_first_review():
    """Scenario: Rechazo."""
    actor = Actor()
    run = run_review(actor, Critic(fb("reject", MAJOR)), max_iterations=3)
    assert len(actor.received) == 1
    assert (run.final_decision, run.stop_reason) == ("synthesize", "rejected")
    assert run.trace.iterations[0].decision == "synthesize"


def test_out_of_scope_draft_is_accepted_without_calling_the_critic():
    """Scenario: Borrador fuera de alcance."""
    draft = make_draft(**out_of_scope_payload(confidence_pct=10))
    assert draft.estimate.out_of_scope
    critic = Critic(NEEDS)
    run = run_review(Actor(draft), critic, max_iterations=3)
    assert critic.drafts == []
    assert (run.final_decision, run.stop_reason) == ("accept", "out_of_scope")
    (entry,) = run.trace.iterations
    assert entry.verdict is None and entry.critic_confidence is None
    assert entry.critic_failed is False and entry.decision == "accept"
    assert run.trace.critic_calls == 0 and run.final is draft.result


def test_out_of_scope_after_a_retry_stops_without_a_second_critic_call():
    oos = make_draft(**out_of_scope_payload(confidence_pct=10))
    critic = Critic(NEEDS)
    run = run_review(Actor(make_draft(), oos), critic, max_iterations=3)
    assert len(critic.drafts) == 1
    assert run.stop_reason == "out_of_scope" and run.final_decision == "accept"
    assert [it.decision for it in run.trace.iterations] == ["iterate", "accept"]


def test_actor_failure_in_the_retry_synthesizes_the_first_draft():
    """Scenario: Fallo del Actor en el reintento."""
    first = make_draft()
    actor = Actor(first, LLMProviderError("openai"))
    run = run_review(actor, Critic(NEEDS), max_iterations=3)
    assert (run.final_decision, run.stop_reason) == ("synthesize", "actor_failed")
    assert run.draft is first
    assert run.final == synthesize(first.result, NEEDS, "en")
    assert [it.decision for it in run.trace.iterations] == ["iterate"]
    assert (run.trace.actor_calls, run.trace.critic_calls) == (2, 1)


def test_actor_failure_in_the_first_draft_propagates():
    with pytest.raises(LLMProviderError):
        run_review(Actor(LLMProviderError("openai")), Critic(fb()), max_iterations=3)


def test_non_provider_errors_of_the_retry_propagate():
    with pytest.raises(RuntimeError):
        run_review(Actor(make_draft(), RuntimeError("bug")), Critic(NEEDS), max_iterations=3)


def test_single_iteration_budget_is_exhausted_after_one_draft():
    """Scenario: Una sola iteración."""
    actor = Actor()
    run = run_review(actor, Critic(NEEDS), max_iterations=1)
    assert len(actor.received) == 1
    assert run.stop_reason == "budget_exhausted" and run.final_decision == "synthesize"


@pytest.mark.parametrize("value", [0, -1])
def test_max_iterations_must_be_at_least_one(value):
    with pytest.raises(ValueError):
        run_review(Actor(), Critic(fb()), max_iterations=value)


def test_a_failed_critic_is_an_accept_with_stop_reason_critic_failed():
    failed = CriticReview(feedback=fb("accept", confidence=0), failed=True)
    run = run_review(Actor(), Critic(failed), max_iterations=3)
    assert (run.final_decision, run.stop_reason) == ("accept", "critic_failed")
    (entry,) = run.trace.iterations
    assert entry.critic_failed is True and entry.verdict == "accept"
    assert entry.critic_confidence == 0


def test_the_trace_records_the_draft_numbers_and_the_issues_per_iteration():
    first = make_draft(confidence_pct=70)
    second = make_draft(confidence_pct=85, title="Second")
    run = run_review(Actor(first, second), Critic(NEEDS, fb("accept")), max_iterations=3)
    one, two = run.trace.iterations
    assert (one.iteration, two.iteration) == (1, 2)
    assert one.verdict == "needs_iteration" and one.critic_confidence == 80
    assert one.draft_confidence_pct == 70 and two.draft_confidence_pct == 85
    assert one.draft_total_hours == first.estimate.total_hours
    assert [i.field_path for i in one.issues] == ["rows[2].hours"] and two.issues == ()


def test_a_bare_feedback_counts_as_a_review_that_did_not_fail():
    run = run_review(Actor(), Critic(fb("accept")), max_iterations=2)
    assert run.trace.iterations[0].critic_failed is False


def test_boss_iteration_events_have_no_issue_text():
    with capture_logs() as logs:
        run_review(Actor(), Critic(NEEDS, fb("accept")), max_iterations=3)
    events = [e for e in logs if e["event"] == "boss_iteration"]
    assert [(e["iteration"], e["decision"], e["verdict"]) for e in events] == [
        (1, "iterate", "needs_iteration"),
        (2, "accept", "accept"),
    ]
    assert events[0]["issues"] == 1 and events[0]["draft_out_of_scope"] is False
    assert "TPV" not in str(logs) and "rows[2]" not in str(logs)


# --- Síntesis ---


def spanish_draft(**overrides):
    return make_draft(
        language="es",
        confidence_pct=80,
        assumptions_and_risks=["Supuesto uno.", "Supuesto dos."],
        **overrides,
    )


def test_synthesis_in_spanish_puts_the_warning_first_and_halves_the_confidence():
    """Scenario: Avisos en español."""
    draft = spanish_draft()
    issue = critic_issue(
        "major", "scope_mismatch", "summary", "No incluye la integración con el TPV"
    )
    out = synthesize(draft.result, fb("needs_iteration", issue), "es")
    assert out.assumptions_and_risks == [
        "Revisión independiente: No incluye la integración con el TPV",
        "Supuesto uno.",
        "Supuesto dos.",
    ]
    assert out.confidence_pct == 40
    assert draft.result.confidence_pct == 80  # the draft is not mutated
    assert draft.result.assumptions_and_risks == ["Supuesto uno.", "Supuesto dos."]


def test_synthesis_label_follows_the_language():
    assert SYNTHESIS_LABELS == {"es": "Revisión independiente", "en": "Independent review"}
    out = synthesize(make_draft().result, fb("needs_iteration", MAJOR), "en")
    assert out.assumptions_and_risks[0] == ("Independent review: Falta la integración con el TPV")


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(45, 30), (30, 30), (60, 30), (65, 30), (70, 35), (80, 40), (95, 45), (100, 50)],
)
def test_confidence_floor_and_rounding(confidence, expected):
    """Scenario: Suelo de confianza."""
    out = synthesize(
        make_draft(confidence_pct=confidence).result, fb("needs_iteration", MAJOR), "en"
    )
    assert out.confidence_pct == expected
    assert compute_estimate(out, "en").out_of_scope is False


def test_synthesis_caps_assumptions_at_eight_warnings_first():
    """Scenario: Tope de supuestos."""
    own = [f"Supuesto {n}." for n in range(1, 8)]
    issues = [
        critic_issue("major", "scope_mismatch", f"rows[{n}].hours", f"Problema grave {n}.")
        for n in range(3)
    ]
    out = synthesize(
        make_draft(assumptions_and_risks=own).result, fb("needs_iteration", *issues), "en"
    )
    assert len(out.assumptions_and_risks) == 8
    assert out.assumptions_and_risks[:3] == [
        f"Independent review: Problema grave {n}." for n in range(3)
    ]
    assert out.assumptions_and_risks[3:] == own[:5]


def test_synthesis_uses_only_blocking_issues_when_there_are_some():
    minor = critic_issue("minor", "missing_assumption", "summary", "Detalle menor sin importancia.")
    critical = critic_issue("critical", "hallucination", "summary", "Dato inventado grave.")
    out = synthesize(make_draft().result, fb("reject", minor, MAJOR, critical), "en")
    warnings = out.assumptions_and_risks[:2]
    assert warnings == [
        "Independent review: Falta la integración con el TPV",
        "Independent review: Dato inventado grave.",
    ]
    assert not any("menor" in item for item in out.assumptions_and_risks)


def test_synthesis_with_only_minor_issues_warns_each_minor():
    minors = [
        critic_issue("minor", "missing_assumption", "summary", f"Detalle menor número {n}.")
        for n in range(2)
    ]
    out = synthesize(make_draft().result, fb("accept", *minors), "en")
    assert out.assumptions_and_risks[:2] == [
        "Independent review: Detalle menor número 0.",
        "Independent review: Detalle menor número 1.",
    ]


def test_synthesis_without_feedback_or_issues_only_reduces_the_confidence():
    draft = make_draft(confidence_pct=80)
    for feedback in (None, fb("reject", MAJOR).model_copy(update={"issues": []})):
        out = synthesize(draft.result, feedback, "en")
        assert out.assumptions_and_risks == draft.result.assumptions_and_risks
        assert out.confidence_pct == 40


def test_warnings_are_cut_to_300_characters():
    issue = critic_issue("major", "scope_mismatch", "summary", "x" * 300)
    out = synthesize(make_draft().result, fb("needs_iteration", issue), "es")
    assert len(out.assumptions_and_risks[0]) <= 300
    assert out.assumptions_and_risks[0].startswith("Revisión independiente: xxx")
