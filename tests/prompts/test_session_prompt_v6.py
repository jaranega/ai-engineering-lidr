"""prompt-templates and cag-prompt: `v6` session prompt (`<critic_feedback>` block) and
the `critic/v1` prompt. Offline and deterministic.
"""

import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from app.estimation.compute import compute_estimate
from app.estimation.schema import TeamMember
from app.prompts import loader
from app.prompts.loader import (
    CRITIC_PROMPT_VERSION,
    PUBLISHED_PROMPT_VERSIONS,
    REVIEW_PROMPT_VERSIONS,
    UnknownPromptVersionError,
    critic_figures,
    render_critic_prompt,
    render_estimation_prompt,
    render_session_prompt,
)
from app.services.critic import CriticFeedback
from app.sessions import ProjectMetadata
from app.sessions.tier import Tier
from tests.conftest import critic_issue, critic_payload, make_input, valid_payload, valid_result
from tests.prompts.test_session_prompt_v4 import make_turn

ESTIMATION_DIR = Path(loader.__file__).parent / "estimation"
CRITIC_DIR = Path(loader.__file__).parent / "critic"
FIXED_SENTENCE = (
    "A previous draft of this estimate was reviewed by an independent auditor. "
    "Produce a new estimate that fixes these issues and keeps everything else consistent:"
)
ATTACHMENTS = [("spec.pdf", "Texto del PDF."), ("anexo.docx", "Texto del anexo.")]
SUMMARY = "El cliente quiere reservas online."
FILLED = ProjectMetadata(
    project_name="Clinic booking platform",
    assumed_team_size=3,
    mentioned_technologies=["Twilio"],
    agreed_scope="Booking with reminders.",
    explicit_constraints=["NDA firmado", "Presupuesto máximo de 40 000 €"],
    rejected_options=["Firebase"],
)


def feedback(*issues: dict, verdict: str = "needs_iteration") -> CriticFeedback:
    return CriticFeedback.model_validate(critic_payload(verdict, issues))


MAJOR = critic_issue(
    "major",
    "constraint_conflict",
    "rows[4].hours",
    "Supera el presupuesto cerrado de 40 000 €",
    "Reducir alcance de informes",
)
MINOR = critic_issue("minor", "missing_assumption", "assumptions_and_risks", "Falta un supuesto.")


# --- Estructura y versiones ---


def test_v6_non_user_files_are_literal_copies_of_v5():
    """Scenario: Copias de v6."""
    for name in (
        "system.j2",
        "examples.j2",
        "project_metadata.j2",
        "conversation_summary.j2",
        "audience.j2",
    ):
        assert (ESTIMATION_DIR / "v6" / name).read_bytes() == (
            ESTIMATION_DIR / "v5" / name
        ).read_bytes(), name


def test_published_versions_and_review_versions():
    assert PUBLISHED_PROMPT_VERSIONS == ("v3", "v4", "v5", "v6")
    assert sorted(REVIEW_PROMPT_VERSIONS) == ["v6"]
    assert sorted(loader.MEMORY_PROMPT_VERSIONS) == ["v5", "v6"]
    assert loader.DEFAULT_SESSION_PROMPT_VERSION == "v6"


def test_critic_folder_contains_only_v1_with_its_two_templates():
    """Scenario: Carpeta del Critic."""
    folders = {p.name for p in CRITIC_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert folders == {"v1"}
    assert {p.name for p in (CRITIC_DIR / "v1").iterdir()} == {"system.j2", "user.j2"}
    assert CRITIC_PROMPT_VERSION == "v1"


@pytest.mark.parametrize("version", ["v4", "v5", "v6"])
def test_render_estimation_prompt_rejects_session_versions(version):
    """Scenario: Versión no admitida."""
    with pytest.raises(UnknownPromptVersionError):
        render_estimation_prompt(make_input(), version)


def test_render_session_prompt_rejects_v3():
    with pytest.raises(UnknownPromptVersionError):
        render_session_prompt(make_turn(), "v3")


# --- v6 sin feedback == v5 ---


@pytest.mark.parametrize("tier", [Tier.DEFAULT, Tier.EXECUTIVE, Tier.DEVELOPER])
@pytest.mark.parametrize("with_context", [False, True], ids=["bare", "full"])
def test_v6_without_feedback_is_byte_identical_to_v5(with_context, tier):
    """Scenario: v6 sin feedback igual a v5."""
    kwargs = (
        {
            "attachments": ATTACHMENTS,
            "project_metadata": FILLED,
            "conversation_summary": SUMMARY,
            "response_language": "es",
        }
        if with_context
        else {}
    )
    v5 = render_session_prompt(make_turn(), "v5", tier=tier, **kwargs)
    v6 = render_session_prompt(make_turn(), "v6", tier=tier, **kwargs)
    assert (v6.system, v6.user, v6.history_user) == (v5.system, v5.user, v5.history_user)


def test_default_render_is_v6_and_history_user_equals_user():
    """Scenario: Render por defecto."""
    prompt = render_session_prompt(make_turn())
    assert prompt == render_session_prompt(make_turn(), "v6")
    assert prompt.history_user == prompt.user
    assert "<critic_feedback>" not in prompt.user


# --- Feedback en v6 ---


def test_feedback_block_comes_after_the_transcript_with_the_fixed_sentence_and_the_line():
    """Scenario: Bloque con un problema mayor."""
    prompt = render_session_prompt(make_turn(), "v6", critic_feedback=feedback(MAJOR))
    line = (
        "- [major] constraint_conflict at rows[4].hours: "
        "Supera el presupuesto cerrado de 40 000 € (suggested fix: Reducir alcance de informes)"
    )
    assert prompt.user.endswith(f"<critic_feedback>\n{FIXED_SENTENCE}\n{line}\n</critic_feedback>")
    assert prompt.user.index("</transcript>") < prompt.user.index("<critic_feedback>")


def test_issue_without_a_fix_has_no_suggested_fix_suffix():
    issue = critic_issue("critical", "hallucination", "summary", "Menciona una integración falsa.")
    prompt = render_session_prompt(make_turn(), "v6", critic_feedback=feedback(issue))
    assert "- [critical] hallucination at summary: Menciona una integración falsa.\n" in prompt.user
    assert "suggested fix" not in prompt.user


def test_minor_issues_are_filtered_and_the_order_received_is_kept():
    """Scenario: Feedback en v6 (el problema menor no se pinta)."""
    first = critic_issue("major", "scope_mismatch", "rows[2].hours", "Primer problema grave.")
    second = critic_issue("critical", "hallucination", "summary", "Segundo problema grave.")
    prompt = render_session_prompt(
        make_turn(),
        "v6",
        attachments=[("spec.pdf", "Texto del PDF.")],
        critic_feedback=feedback(first, MINOR, second),
    )
    assert prompt.user.index("Primer problema") < prompt.user.index("Segundo problema")
    assert "Falta un supuesto" not in prompt.user
    assert "missing_assumption" not in prompt.user
    assert prompt.user.index("</transcript>") < prompt.user.index("<critic_feedback>")
    assert "<critic_feedback>" not in prompt.history_user
    assert "Texto del PDF." not in prompt.history_user


def test_only_minor_issues_paint_the_sentence_and_no_issue_line():
    """Scenario: Solo problemas menores."""
    minors = feedback(MINOR, MINOR, verdict="accept")
    prompt = render_session_prompt(make_turn(), "v6", critic_feedback=minors)
    block = prompt.user.split("<critic_feedback>")[1]
    assert FIXED_SENTENCE in block
    assert "- [" not in block


def test_feedback_text_is_printed_unescaped():
    issue = critic_issue("major", "scope_mismatch", "rows[1].hours", 'Coste < 5 & >= 3 "ok"')
    prompt = render_session_prompt(make_turn(), "v6", critic_feedback=feedback(issue))
    assert 'Coste < 5 & >= 3 "ok"' in prompt.user


def test_history_user_never_has_the_feedback():
    plain = render_session_prompt(make_turn(), "v6", attachments=ATTACHMENTS)
    with_feedback = render_session_prompt(
        make_turn(), "v6", attachments=ATTACHMENTS, critic_feedback=feedback(MAJOR)
    )
    assert with_feedback.history_user == plain.history_user
    assert with_feedback.system == plain.system
    assert "<critic_feedback>" not in with_feedback.history_user


@pytest.mark.parametrize("version", ["v4", "v5"])
def test_older_versions_ignore_the_feedback(version):
    """Scenario: v5 ignora el feedback (y v4)."""
    without = render_session_prompt(make_turn(), version, attachments=ATTACHMENTS)
    with_feedback = render_session_prompt(
        make_turn(), version, attachments=ATTACHMENTS, critic_feedback=feedback(MAJOR)
    )
    assert with_feedback == without


def test_v4_ignores_summary_and_tier_with_the_new_signature():
    """Scenario: v4 ignora resumen y tier."""
    plain = render_session_prompt(make_turn(), "v4")
    full = render_session_prompt(
        make_turn(), "v4", conversation_summary=SUMMARY, tier=Tier.EXECUTIVE
    )
    assert plain.system == full.system
    assert "<conversation_summary>" not in full.system and "<audience>" not in full.system


def test_prompt_rendered_reports_the_painted_issues_without_content():
    """Scenario: Evento sin contenido (critic_issues)."""
    secret_issue = critic_issue("major", "scope_mismatch", "summary", "FEEDBACK-SECRETO-000")
    with capture_logs() as logs:
        render_session_prompt(
            make_turn("TRANSCRIPCION-SECRETA-123"),
            "v6",
            attachments=[("spec.pdf", "ADJUNTO-SECRETO-456")],
            conversation_summary="RESUMEN-SECRETO-789",
            tier=Tier.PM,
            critic_feedback=feedback(secret_issue, MINOR),
        )
        render_session_prompt(make_turn(), "v6")
    with_feedback, without = [e for e in logs if e["event"] == "prompt_rendered"]
    assert with_feedback["prompt"] == "session" and with_feedback["version"] == "v6"
    assert with_feedback["attachments"] == 1
    assert with_feedback["tier"] == "pm" and with_feedback["summary_chars"] > 0
    assert with_feedback["critic_issues"] == 1  # the minor issue is not painted
    assert without["critic_issues"] == 0
    dumped = json.dumps(logs, default=str)
    for secret in (
        "TRANSCRIPCION-SECRETA-123",
        "ADJUNTO-SECRETO-456",
        "RESUMEN-SECRETO-789",
        "FEEDBACK-SECRETO-000",
    ):
        assert secret not in dumped


def test_v4_and_v5_report_zero_critic_issues():
    with capture_logs() as logs:
        render_session_prompt(make_turn(), "v5", critic_feedback=feedback(MAJOR))
    (event,) = [e for e in logs if e["event"] == "prompt_rendered"]
    assert event["critic_issues"] == 0


# --- Critic prompt ---


def three_row_estimate(language: str = "en"):
    payload = valid_payload()
    rows = [{**row, "hours": 40} for row in payload["rows"][:3]]
    used = {row["phase"] for row in rows}
    payload["rows"] = rows
    payload["phases"] = [p for p in payload["phases"] if p["phase"] in used]
    return compute_estimate(valid_result(**payload), language)


def critic_prompt(**overrides):
    kwargs = {
        "transcript": "We need a clinic booking platform.",
        "project_metadata": FILLED,
        "conversation_summary": SUMMARY,
        "tier": Tier.PM,
        "language": "en",
        "estimate": three_row_estimate(),
    }
    return render_critic_prompt(**{**kwargs, **overrides})


def test_critic_user_has_the_estimation_under_review_with_indexed_rows_and_totals():
    """Scenario: Estimación bajo revisión."""
    estimate = three_row_estimate()
    assert len(estimate.rows) == 3 and estimate.total_hours == 120
    _, user = critic_prompt(estimate=estimate)
    body = user.split("<estimation_under_review>")[1].split("</estimation_under_review>")[0]
    for index, row in enumerate(estimate.rows):
        assert f"- [{index}] {row.phase} | {row.rate_profile} | 40 h | {row.task}" in body
    assert "- [3]" not in body.split("phases:")[0]
    assert "total_hours: 120" in body
    assert f"total_cost_eur: {estimate.total_cost_eur}" in body
    assert body.count("total_hours:") == 1 and body.count("total_cost_eur:") == 1
    assert "title: Clinic booking platform" in body
    assert "confidence_pct:" in body and "team:" in body and "duration_weeks:" in body
    assert "assumptions_and_risks:" in body and "technologies:" in body


def test_critic_user_has_the_precomputed_figures():
    """Phase shares, FTE and implied weeks are computed by code, inside the block."""
    estimate = three_row_estimate()  # 3 phases x 40 h; team 2 + 1 part-time
    _, user = critic_prompt(estimate=estimate)
    body = user.split("<estimation_under_review>")[1].split("</estimation_under_review>")[0]
    for index, phase in enumerate(estimate.phases):
        assert f"- [{index}] {phase.phase} | 40 h (33.3% of total hours) |" in body
    figures = body.split("precomputed_figures")[1]
    assert "total_hours: 120\n" in figures
    assert f"total_cost_eur: {estimate.total_cost_eur}\n" in figures
    assert "largest_phase_share: 33.3% (threshold 75%)" in figures
    assert "team_fte: 2.5\n" in figures
    assert "implied_weeks: 1.5 at 32 h per FTE per week (declared duration_weeks: 6-7)" in figures


def test_critic_figures_compute_shares_fte_and_implied_weeks():
    estimate = three_row_estimate().model_copy(
        update={
            "total_hours": 320,
            "phases": [
                p.model_copy(update={"hours": h})
                for p, h in zip(three_row_estimate().phases, (40, 56, 224), strict=True)
            ],
            "team": [TeamMember(role="developers", count=2)],
        }
    )
    figures = critic_figures(estimate)
    assert figures["phase_shares"] == ["12.5", "17.5", "70"]
    assert figures["largest_phase_share"] == "70"
    assert figures["team_fte"] == "2" and figures["implied_weeks"] == "5"


def test_critic_figures_without_team_or_hours_are_none():
    empty = three_row_estimate().model_copy(
        update={"total_hours": 0, "phases": [], "team": [], "duration_weeks": None}
    )
    assert critic_figures(empty) == {
        "phase_shares": [],
        "largest_phase_share": None,
        "team_fte": None,
        "implied_weeks": None,
        "hours_per_fte_week": 32,
    }
    _, user = critic_prompt(estimate=empty)
    assert "largest_phase_share: none (threshold 75%)" in user
    assert "team_fte: none" in user
    assert "implied_weeks: none at 32 h per FTE per week (declared duration_weeks: none)" in user


def test_critic_user_blocks_and_values():
    _, user = critic_prompt(transcript="Quiero <b>reservas</b> & más", language="es")
    assert "<transcript>\nQuiero <b>reservas</b> & más\n</transcript>" in user
    assert "<project_metadata>" in user
    assert "explicit_constraints: NDA firmado; Presupuesto máximo de 40 000 €" in user
    assert "rejected_options: Firebase" in user
    assert f"<conversation_summary>\n{SUMMARY}\n</conversation_summary>" in user
    assert "<resolved_tier>pm</resolved_tier>" in user
    assert "<response_language>Spanish</response_language>" in user
    assert "<estimation_under_review>" in user


def test_critic_user_omits_the_summary_block_without_summary():
    _, user = critic_prompt(conversation_summary=None, project_metadata=None, language=None)
    assert "<conversation_summary>" not in user
    assert "<response_language>the language of the transcript</response_language>" in user


def test_critic_system_has_the_required_instructions():
    system, _ = critic_prompt()
    for category in (
        "hallucination",
        "scope_mismatch",
        "constraint_conflict",
        "phase_imbalance",
        "missing_assumption",
        "unrealistic_estimate",
        "tier_mismatch",
    ):
        assert category in system
    assert "independent auditor" in system
    assert "needs_iteration" in system and "accept" in system
    assert "rows[i].hours" in system and "phases[i]" in system
    assert "duration_weeks" in system and "assumptions_and_risks" in system
    assert "do NOT see" in system  # earlier turns and attachments not visible
    # Calibration: numeric rules on the precomputed figures, strict scope, length caps.
    assert "precomputed_figures" in system and "never redo that arithmetic" in system
    assert "under budget is never a conflict" in system
    assert "largest_phase_share` is above 75%" in system
    assert "absent from ALL the row tasks" in system
    assert "Always `minor`, unless a constraint or regulation" in system
    assert "Not an issue (do not raise these):" in system
    assert "at most 250 characters" in system and "at most 150 characters" in system
    assert "allowed with `accept`" in system


def test_critic_prompt_is_deterministic_and_never_has_attachments_or_history():
    assert critic_prompt() == critic_prompt()
    _, user = critic_prompt()
    assert "--- attachment" not in user and "<attachments>" not in user


def test_critic_prompt_event_has_hashes_and_no_content():
    """Scenario: Evento del Critic sin contenido."""
    with capture_logs() as logs:
        critic_prompt(
            transcript="TRANSCRIPCION-SECRETA-123", conversation_summary="RESUMEN-SECRETO"
        )
    (event,) = [e for e in logs if e["event"] == "prompt_rendered"]
    assert event["prompt"] == "critic" and event["version"] == "v1"
    assert event["system_sha256"] and event["user_sha256"]
    assert event["system_chars"] > 0 and event["user_chars"] > 0
    dumped = json.dumps(logs, default=str)
    assert "TRANSCRIPCION-SECRETA-123" not in dumped and "RESUMEN-SECRETO" not in dumped
