"""actor-critic-boss: `CriticFeedback` validators and the `Critic` call (fail-open).
Offline: the provider is a double.
"""

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.estimation.compute import compute_estimate
from app.services.critic import (
    CRITIC_MAX_TOKENS,
    Critic,
    CriticFeedback,
    CriticIssue,
)
from app.services.llm_service import LLMCallOptions, LLMProviderError, StructuredOutputError
from app.sessions import ProjectMetadata
from app.sessions.tier import Tier
from tests.conftest import ReviewProvider, critic_issue, critic_payload, valid_result


def feedback(verdict: str, *issues: dict, confidence: int = 80) -> dict:
    return critic_payload(verdict, issues, confidence)


# --- CriticIssue ---


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "arithmetic"},
        {"severity": "blocker"},
        {"field_path": ""},
        {"field_path": "x" * 121},
        {"description": "four"},
        {"description": "x" * 301},
        {"suggested_fix": "x" * 201},
    ],
    ids=["category", "severity", "path-empty", "path-long", "desc-short", "desc-long", "fix-long"],
)
def test_issue_rejects_out_of_range_values(overrides):
    with pytest.raises(ValidationError):
        CriticIssue.model_validate({**critic_issue(), **overrides})


@pytest.mark.parametrize(
    "category",
    [
        "hallucination",
        "scope_mismatch",
        "constraint_conflict",
        "phase_imbalance",
        "missing_assumption",
        "unrealistic_estimate",
        "tier_mismatch",
    ],
)
def test_issue_accepts_the_seven_categories(category):
    assert CriticIssue.model_validate(critic_issue(category=category)).category == category


def test_issue_boundaries_and_optional_fix():
    issue = CriticIssue.model_validate(
        critic_issue(field_path="x" * 120, description="x" * 300, suggested_fix="y" * 200)
    )
    assert len(issue.field_path) == 120 and len(issue.suggested_fix or "") == 200
    assert CriticIssue.model_validate(critic_issue()).suggested_fix is None


def test_blank_suggested_fix_becomes_none_instead_of_failing():
    assert CriticIssue.model_validate(critic_issue(suggested_fix="   ")).suggested_fix is None


# --- CriticFeedback ---


def test_needs_iteration_with_only_a_minor_issue_fails():
    """Scenario: Reintento solo con problemas menores."""
    with pytest.raises(ValidationError):
        CriticFeedback.model_validate(feedback("needs_iteration", critic_issue("minor")))


def test_needs_iteration_without_issues_fails():
    with pytest.raises(ValidationError):
        CriticFeedback.model_validate(feedback("needs_iteration"))


@pytest.mark.parametrize("severity", ["major", "critical"])
def test_needs_iteration_with_a_blocking_issue_passes(severity):
    result = CriticFeedback.model_validate(
        feedback("needs_iteration", critic_issue("minor"), critic_issue(severity))
    )
    assert result.verdict == "needs_iteration"


def test_reject_without_issues_fails():
    """Scenario: Rechazo sin problemas."""
    with pytest.raises(ValidationError):
        CriticFeedback.model_validate(feedback("reject"))


def test_reject_with_a_minor_issue_passes():
    assert CriticFeedback.model_validate(feedback("reject", critic_issue("minor"))).verdict


def test_clean_accept_passes():
    """Scenario: Aceptación limpia."""
    result = CriticFeedback.model_validate(feedback("accept", confidence=80))
    assert result.issues == [] and result.confidence_in_review == 80


def test_accept_may_carry_minor_issues():
    assert len(CriticFeedback.model_validate(feedback("accept", critic_issue("minor"))).issues) == 1


@pytest.mark.parametrize("confidence", [-1, 101])
def test_confidence_must_be_between_0_and_100(confidence):
    with pytest.raises(ValidationError):
        CriticFeedback.model_validate(feedback("accept", confidence=confidence))


def test_at_most_twelve_issues():
    twelve = [critic_issue("minor")] * 12
    assert len(CriticFeedback.model_validate(feedback("accept", *twelve)).issues) == 12
    with pytest.raises(ValidationError):
        CriticFeedback.model_validate(feedback("accept", *twelve, critic_issue("minor")))


def test_validator_message_has_no_values():
    """The message goes back to the model on a retry: fixed English, no input values."""
    secret = critic_issue("minor", description="DESCRIPCION-SECRETA")
    with pytest.raises(ValidationError) as caught:
        CriticFeedback.model_validate(feedback("needs_iteration", secret))
    assert "requires at least one critical or major issue" in str(caught.value)


# --- Critic.review ---


def estimate():
    return compute_estimate(valid_result(), "en")


def review(critic: Critic, **overrides):
    kwargs = {
        "transcript": "TRANSCRIPCION-SECRETA-123",
        "project_metadata": ProjectMetadata(explicit_constraints=["NDA firmado"]),
        "conversation_summary": "RESUMEN-SECRETO",
        "tier": Tier.PM,
        "language": "en",
        "iteration": 1,
    }
    return critic.review(estimate(), **{**kwargs, **overrides})


def test_review_returns_the_feedback_and_the_call_and_logs_without_content():
    issue = critic_issue("major", "hallucination", "rows[7].hours", "DESCRIPCION-SECRETA-123")
    provider = ReviewProvider(critics=[feedback("needs_iteration", issue, critic_issue("minor"))])
    options = LLMCallOptions(max_tokens=CRITIC_MAX_TOKENS)
    with capture_logs() as logs:
        result = review(Critic(provider, options), iteration=2)
    assert result.failed is False
    assert result.feedback.verdict == "needs_iteration"
    assert result.call is not None and result.call.input_tokens == 400
    assert provider.response_models == [CriticFeedback]
    assert provider.options == [options]
    (done,) = [e for e in logs if e["event"] == "critic_completed"]
    assert done["iteration"] == 2 and done["verdict"] == "needs_iteration"
    assert (done["issues"], done["critical"], done["major"], done["minor"]) == (2, 0, 1, 1)
    assert done["categories"] == ["hallucination", "scope_mismatch"]
    assert done["confidence"] == 80 and done["latency_ms"] >= 0
    dumped = str(logs)
    for secret in ("DESCRIPCION-SECRETA-123", "rows[7]", "TRANSCRIPCION-SECRETA-123"):
        assert secret not in dumped


def test_review_binds_the_critic_purpose_during_the_call():
    provider = ReviewProvider()
    review(Critic(provider, LLMCallOptions()))
    assert provider.contexts[0]["llm_purpose"] == "critic"


def test_review_sends_transcript_metadata_summary_tier_language_and_the_draft_only():
    provider = ReviewProvider()
    review(Critic(provider, LLMCallOptions()), language="es")
    (system, messages) = provider.calls[0]
    assert "independent auditor" in system
    (message,) = messages
    content = message["content"]
    assert message["role"] == "user"
    assert "TRANSCRIPCION-SECRETA-123" in content and "RESUMEN-SECRETO" in content
    assert "NDA firmado" in content
    assert "<resolved_tier>pm</resolved_tier>" in content
    assert "<response_language>Spanish</response_language>" in content
    assert "<estimation_under_review>" in content


@pytest.mark.parametrize(
    "error",
    [LLMProviderError("openai"), StructuredOutputError("openai", "invalid", 3)],
    ids=["provider-error", "structured-output-error"],
)
def test_critic_failure_is_fail_open_accept_with_zero_confidence(error):
    """Scenario: Critic caído (a nivel de Critic)."""
    provider = ReviewProvider(critics=[error])
    with capture_logs() as logs:
        result = review(Critic(provider, LLMCallOptions()), iteration=2)
    assert result.failed is True and result.call is None
    assert result.feedback.verdict == "accept"
    assert result.feedback.issues == [] and result.feedback.confidence_in_review == 0
    (failed,) = [e for e in logs if e["event"] == "critic_failed"]
    assert failed["iteration"] == 2 and failed["error_type"] == type(error).__name__
    assert not [e for e in logs if e["event"] == "critic_completed"]


def test_non_provider_errors_are_not_swallowed():
    provider = ReviewProvider(critics=[RuntimeError("bug")])
    with pytest.raises(RuntimeError):
        review(Critic(provider, LLMCallOptions()))
