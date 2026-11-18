"""actor-critic-boss and conversation-sessions: the session turn with `review`
(Pipeline del turno con revisión, observability events). Offline: the provider is a double
that answers drafts and Critic reviews from scripts.
"""

import json

import pytest
import structlog
from structlog.testing import capture_logs

from app.attachments import AttachmentLimits, UploadedFile
from app.domain import SessionTurnInput
from app.services.critic import CRITIC_MAX_TOKENS
from app.services.llm_service import (
    EstimationService,
    GenerationOptions,
    InvalidOptionError,
    LLMProviderError,
    ReviewConfig,
)
from app.services.pricing import estimate_cost
from app.services.session_service import SessionService
from app.sessions import SessionStore
from tests.attachments.factories import make_pdf
from tests.conftest import (
    TRANSCRIPT,
    FakeModerator,
    ReviewProvider,
    critic_issue,
    critic_payload,
    make_fake_compression,
    make_settings,
    valid_payload,
)

LIMITS = AttachmentLimits(max_files=5, max_bytes=10 * 1024 * 1024, max_chars=60_000)
ISSUE = critic_issue("major", "scope_mismatch", "rows[2].hours", "DESCRIPCION-SECRETA-123")
NEEDS = critic_payload("needs_iteration", [ISSUE])
ACCEPT = critic_payload("accept")


def capture():
    return capture_logs(processors=[structlog.contextvars.merge_contextvars])


class Rig:
    def __init__(self, provider: ReviewProvider, *, review: ReviewConfig | None = None, **kwargs):
        self.provider = provider
        self.moderator = FakeModerator(result=False)
        self.compressions: list = []
        self.store = SessionStore(max_sessions=10, max_turns=6)
        memory = make_fake_compression(3)
        self.service = SessionService(
            self.store,
            EstimationService(provider, moderator=self.moderator),
            LIMITS,
            memory=memory,
            review=review or ReviewConfig(max_iterations=3),
        )
        self.session_id = self.service.create()

    @property
    def session(self):
        return self.store.get(self.session_id)

    def turn(self, transcript: str = TRANSCRIPT, *files, review: bool = True, **options):
        options.setdefault("prompt_version", "v6")
        return self.service.estimate(
            self.session_id,
            SessionTurnInput(
                transcript=transcript,
                project_type="web_saas",
                detail_level="medium",
                output_format="line_items",
            ),
            [UploadedFile(name, data) for name, data in files],
            GenerationOptions(**options),
            review=review,
        )


def user_content(call) -> str:
    return call[1][-1]["content"]


# --- Pipeline ---


def test_guardrails_and_moderation_run_once_for_a_turn_with_retry():
    """Scenario: Guardrails una vez."""
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    rig.turn()
    assert len(rig.moderator.calls) == 1
    assert (rig.provider.count("draft"), rig.provider.count("critic")) == (2, 2)
    assert rig.provider.kinds == ["draft", "critic", "draft", "critic"]


def test_feedback_only_goes_in_the_retry_with_category_and_field_path():
    """Scenario: Feedback solo en el reintento."""
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    rig.turn()
    first, second = rig.provider.calls_of("draft")
    assert "<critic_feedback>" not in user_content(first)
    retry = user_content(second)
    assert "<critic_feedback>" in retry
    assert "scope_mismatch at rows[2].hours" in retry
    assert retry.index("</transcript>") < retry.index("<critic_feedback>")
    assert first[0] == second[0]  # same system prompt
    assert first[1][:-1] == second[1][:-1]  # same history


def test_history_is_stored_once_with_the_final_draft_and_without_feedback():
    """Scenario: Turno revisado guardado una vez."""
    first, second = valid_payload(title="First draft"), valid_payload(title="Second draft")
    rig = Rig(ReviewProvider(drafts=[first, second], critics=[NEEDS, ACCEPT]))
    outcome = rig.turn()
    messages = rig.session.history.to_messages()
    assert len(messages) == 2
    assert messages[1]["content"] == outcome.outcome.text
    assert "Second draft" in outcome.outcome.text and "First draft" not in outcome.outcome.text
    assert "<critic_feedback>" not in messages[0]["content"]
    assert messages[0]["content"].count("<transcript>") == 1
    assert rig.session.turn_count == 1


def test_metrics_are_summed_over_drafts_and_critic_calls():
    """Scenario: Métricas sumadas."""
    rig = Rig(ReviewProvider(critics=[ACCEPT]))  # 1000/200 per draft, 400/100 per review
    outcome = rig.turn().outcome
    assert (outcome.input_tokens, outcome.output_tokens) == (1400, 300)
    expected = estimate_cost("gpt-4o-mini", 1000, 200) + estimate_cost("gpt-4o-mini", 400, 100)
    assert outcome.cost_usd == pytest.approx(expected)


def test_metrics_with_a_retry_sum_all_four_calls():
    outcome = Rig(ReviewProvider(critics=[NEEDS, ACCEPT])).turn().outcome
    assert (outcome.input_tokens, outcome.output_tokens) == (2800, 600)


def test_cost_is_none_when_a_call_has_no_known_price():
    rig = Rig(ReviewProvider(critics=[ACCEPT], critic_model="model-without-price"))
    outcome = rig.turn().outcome
    assert outcome.cost_usd is None
    assert outcome.input_tokens == 1400


@pytest.mark.parametrize(
    ("draft_hit", "critic_hit", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_cache_hit_only_when_every_call_was_a_hit(draft_hit, critic_hit, expected):
    rig = Rig(ReviewProvider(critics=[ACCEPT], cache_hit=draft_hit, critic_cache_hit=critic_hit))
    assert rig.turn().outcome.cache_hit is expected


def test_model_and_provider_are_those_of_the_final_draft():
    rig = Rig(
        ReviewProvider(critics=[ACCEPT], critic_model="claude-haiku-4-5", model="gpt-4o-mini")
    )
    outcome = rig.turn().outcome
    assert outcome.model == "gpt-4o-mini" and outcome.provider == "openai"


def test_the_result_is_computed_rendered_and_evaluated_once():
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    with capture() as logs:
        rig.turn()
    for name in ("estimation_rendered", "estimation_evaluated", "session_turn_completed"):
        assert len([e for e in logs if e["event"] == name]) == 1, name
    assert len([e for e in logs if e["event"] == "estimation_requested"]) == 1


def test_the_trace_is_in_the_outcome_and_last_review_is_stored():
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    outcome = rig.turn()
    trace = outcome.review
    assert trace is not None and trace.iterations_run == 2
    assert [i.decision for i in trace.iterations] == ["iterate", "accept"]
    assert trace.iterations[0].issues[0].field_path == "rows[2].hours"
    last = rig.session.last_review
    assert (last.final_decision, last.stop_reason, last.iterations_run) == ("accept", "accepted", 2)
    assert rig.service.info(rig.session_id).last_review == last


def test_last_review_resets_after_a_turn_without_review_and_the_turn_has_no_trace():
    rig = Rig(ReviewProvider(critics=[ACCEPT]))
    rig.turn()
    assert rig.session.last_review is not None
    outcome = rig.turn("Otra cosa.", review=False)
    assert outcome.review is None and outcome.outcome.review is None
    assert rig.session.last_review is None
    assert rig.provider.count("critic") == 1  # none in the second turn
    assert rig.provider.count("draft") == 2


def test_budget_exhausted_synthesizes_and_reports_it():
    rig = Rig(ReviewProvider(critics=[NEEDS]), review=ReviewConfig(max_iterations=2))
    outcome = rig.turn()
    assert outcome.review.stop_reason == "budget_exhausted"
    assert outcome.review.final_decision == "synthesize"
    assert "Independent review: DESCRIPCION-SECRETA-123" in outcome.outcome.text
    assert outcome.outcome.result.confidence_pct == 40  # valid_payload is 80
    assert rig.provider.count("draft") == 2 and rig.provider.count("critic") == 2
    assert rig.session.turn_count == 1 and len(rig.session.history.to_messages()) == 2


# --- Fallos ---


def test_critic_failure_never_fails_the_turn():
    """Scenario: Critic caído."""
    rig = Rig(ReviewProvider(critics=[LLMProviderError("openai")]))
    with capture() as logs:
        outcome = rig.turn()
    assert outcome.review.final_decision == "accept"
    assert outcome.review.stop_reason == "critic_failed"
    assert outcome.review.iterations[0].critic_failed is True
    assert rig.session.turn_count == 1
    assert [e["error_type"] for e in logs if e["event"] == "critic_failed"] == ["LLMProviderError"]
    # a failed Critic adds no tokens
    assert outcome.outcome.input_tokens == 1000


def test_actor_failure_in_the_retry_synthesizes_the_first_draft():
    """Scenario: Fallo del Actor en el reintento (servicio)."""
    rig = Rig(ReviewProvider(drafts=[valid_payload(), LLMProviderError("openai")], critics=[NEEDS]))
    outcome = rig.turn()
    assert outcome.review.stop_reason == "actor_failed"
    assert outcome.review.final_decision == "synthesize"
    assert "Independent review" in outcome.outcome.text
    assert rig.session.turn_count == 1 and len(rig.session.history.to_messages()) == 2
    assert rig.session.last_review.stop_reason == "actor_failed"


def test_provider_error_in_the_first_draft_leaves_the_session_untouched():
    """Scenario: Error del proveedor en el primer borrador."""
    rig = Rig(ReviewProvider(critics=[ACCEPT]))
    rig.turn()
    before = (
        rig.session.turn_count,
        rig.session.history.to_messages(),
        rig.session.project_metadata,
        rig.session.language,
        rig.session.last_tier,
        rig.session.last_review,
    )
    rig.provider.drafts = [LLMProviderError("openai")]
    rig.provider.critics = [ACCEPT]
    with pytest.raises(LLMProviderError):
        rig.turn("Otro mensaje que falla.")
    after = (
        rig.session.turn_count,
        rig.session.history.to_messages(),
        rig.session.project_metadata,
        rig.session.language,
        rig.session.last_tier,
        rig.session.last_review,
    )
    assert after == before
    assert not rig.session.lock.locked()
    assert rig.provider.count("critic") == 1  # the Critic is not called after a failed draft


@pytest.mark.parametrize("version", ["v4", "v5"])
def test_review_with_a_version_without_feedback_is_rejected_before_any_call(version):
    """Scenario: Revisión con una versión sin feedback."""
    rig = Rig(ReviewProvider())
    with pytest.raises(InvalidOptionError) as caught:
        rig.turn(prompt_version=version)
    assert caught.value.field == "review"
    assert "requires prompt_version v6" in str(caught.value)
    assert rig.provider.call_count == 0 and rig.moderator.calls == []
    assert rig.session.turn_count == 0 and rig.session.history.to_messages() == []


def test_review_false_with_v5_still_works():
    rig = Rig(ReviewProvider())
    assert rig.turn(prompt_version="v5", review=False).review is None
    assert rig.provider.kinds == ["draft"]


# --- Modelo, opciones, adjuntos ---


def test_critic_uses_the_critic_model_and_the_drafts_the_primary():
    """Scenario: Modelo del Critic."""
    settings = make_settings(critic_model="claude-haiku-4-5", anthropic_api_key="sk-ant-fake")
    target = settings.resolve_request_model("claude-haiku-4-5")
    rig = Rig(ReviewProvider(critics=[ACCEPT]), review=ReviewConfig(3, target))
    rig.turn()
    draft_options, critic_options = rig.provider.options
    assert draft_options is None  # primary model with fallback, default limits
    assert critic_options.model == target and critic_options.model.model == "claude-haiku-4-5"
    assert critic_options.max_tokens == CRITIC_MAX_TOKENS


def test_critic_without_critic_model_uses_the_primary_with_its_own_token_limit():
    rig = Rig(ReviewProvider(critics=[ACCEPT]))
    rig.turn()
    assert rig.provider.options[1].model is None
    assert rig.provider.options[1].max_tokens == CRITIC_MAX_TOKENS


def test_critic_never_receives_the_attachment_text():
    """Scenario: Sin adjuntos en el Critic."""
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    rig.turn(TRANSCRIPT, ("spec.pdf", make_pdf("MARCADOR-PDF")))
    drafts = rig.provider.calls_of("draft")
    assert "MARCADOR-PDF" in user_content(drafts[0])
    for system, messages in rig.provider.calls_of("critic"):
        assert "MARCADOR-PDF" not in system
        assert all("MARCADOR-PDF" not in m["content"] for m in messages)
    assert "MARCADOR-PDF" not in json.dumps(rig.session.history.to_messages())


def test_critic_does_not_receive_the_history_messages():
    rig = Rig(ReviewProvider(critics=[ACCEPT]))
    rig.turn("PRIMER-MENSAJE-UNICO en la conversación.")
    rig.turn("Segundo mensaje.")
    _, messages = rig.provider.calls_of("critic")[1]
    assert len(messages) == 1 and "PRIMER-MENSAJE-UNICO" not in messages[0]["content"]
    assert len(rig.provider.calls_of("draft")[1][1]) == 3  # history pair + the new message


def test_critic_sees_the_session_metadata_and_the_summary():
    payload = valid_payload(explicit_constraints=["Presupuesto cerrado de 40000"])
    rig = Rig(ReviewProvider(drafts=[payload], critics=[ACCEPT]))
    rig.turn()
    rig.turn("Segundo mensaje.")
    content = rig.provider.calls_of("critic")[1][1][0]["content"]
    assert "Presupuesto cerrado de 40000" in content


# --- Eventos ---


def test_events_of_a_review_with_retry():
    """Scenario: Eventos de una revisión con reintento."""
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    with capture() as logs:
        rig.turn()
    critics = [e for e in logs if e["event"] == "critic_completed"]
    assert [(e["iteration"], e["verdict"]) for e in critics] == [
        (1, "needs_iteration"),
        (2, "accept"),
    ]
    boss = [e for e in logs if e["event"] == "boss_iteration"]
    assert [e["decision"] for e in boss] == ["iterate", "accept"]
    (done,) = [e for e in logs if e["event"] == "acb_completed"]
    assert done["iterations"] == 2 and done["llm_calls"] == 4
    assert (done["final_decision"], done["stop_reason"]) == ("accept", "accepted")
    assert (done["input_tokens"], done["output_tokens"]) == (2800, 600)
    assert done["latency_ms"] >= 0
    (turn,) = [e for e in logs if e["event"] == "session_turn_completed"]
    assert turn["review"] is True and turn["review_decision"] == "accept"
    assert all(e["session"] == rig.session_id[:8] for e in (*critics, *boss, done, turn))


def test_llm_purpose_and_review_iteration_are_bound_during_the_calls():
    rig = Rig(ReviewProvider(critics=[NEEDS, ACCEPT]))
    rig.turn()
    purposes = [
        (k, c.get("llm_purpose"), c.get("review_iteration"))
        for k, c in zip(rig.provider.kinds, rig.provider.contexts, strict=True)
    ]
    assert purposes == [
        ("draft", "estimation", 1),
        ("critic", "critic", None),
        ("draft", "estimation", 2),
        ("critic", "critic", None),
    ]


def test_no_event_carries_the_critic_text_or_the_field_path():
    """Scenario: Sin texto del Critic en los logs."""
    issue = critic_issue(
        "major", "scope_mismatch", "rows[7].hours", "DESCRIPCION-SECRETA-123", "ARREGLO-SECRETO"
    )
    rig = Rig(ReviewProvider(critics=[critic_payload("needs_iteration", [issue]), ACCEPT]))
    with capture() as logs:
        rig.turn("TRANSCRIPCION-SECRETA-123 We need a clinic booking platform.")
    dumped = json.dumps(logs, default=str)
    for secret in (
        "DESCRIPCION-SECRETA-123",
        "ARREGLO-SECRETO",
        "rows[7]",
        "TRANSCRIPCION-SECRETA",
    ):
        assert secret not in dumped


def test_turn_without_review_logs_no_review_events():
    """Scenario: Turno sin revisión."""
    rig = Rig(ReviewProvider())
    with capture() as logs:
        rig.turn(review=False)
    names = {e["event"] for e in logs}
    assert not {"critic_completed", "critic_failed", "boss_iteration", "acb_completed"} & names
    (turn,) = [e for e in logs if e["event"] == "session_turn_completed"]
    assert turn["review"] is False and turn["review_decision"] is None
    assert rig.provider.kinds == ["draft"]


def test_out_of_scope_draft_is_accepted_without_a_critic_call_and_keeps_the_metadata():
    from tests.conftest import out_of_scope_payload

    rig = Rig(ReviewProvider(drafts=[out_of_scope_payload(confidence_pct=10)]))
    outcome = rig.turn()
    assert outcome.review.stop_reason == "out_of_scope"
    assert rig.provider.kinds == ["draft"]
    assert rig.session.turn_count == 1
    assert rig.session.project_metadata.project_name is None
