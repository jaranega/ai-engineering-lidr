"""Golden hashes of the published prompt version v5 (session prompts with memory and tier).

v5 is published and immutable (prompt-templates). If this test fails, a change touched v5:
create a new prompt version instead of editing it.
"""

import hashlib

import pytest

from app.domain import SessionTurnInput
from app.prompts.loader import render_session_prompt
from app.sessions import ProjectMetadata
from app.sessions.tier import Tier

TRANSCRIPT = (
    "Necesitamos una app de reservas para clínicas con recordatorios por SMS y pagos online."
)

FILLED = ProjectMetadata(
    project_name="Clinic booking platform",
    assumed_team_size=3,
    mentioned_technologies=["Twilio", "Stripe"],
    agreed_scope="Booking with reminders.",
)

ATTACHMENTS = [
    ("spec.pdf", "Texto del PDF de especificaciones."),
    ("anexo.docx", "Texto del anexo."),
]

SUMMARY = "Resumen: el cliente quiere reservas y pagos."

USER_PLAIN = "7ba7c736ba98dbd9c86b79c03d33230038831a15b4a2b12099d8642828e77d25"
USER_ATTACH = "da5e23203af710227c9a1ade00121d10cc42e7116052ffb5c441483b93d30ac2"
HISTORY_ATTACH = "3c9466a694fe4f66f204cc32128b2936f8137d128a8957ef909756656b72c641"

# (id, metadata, attachments, summary, tier, system sha256, user sha256, history_user sha256)
CASES = [
    (
        "A-bare-default",
        None,
        [],
        None,
        Tier.DEFAULT,
        "757625f9066281090d44b278463127d2eb7932be93c5bf81e7abec37f0e56faf",
        USER_PLAIN,
        USER_PLAIN,
    ),
    (
        "B-metadata-default",
        FILLED,
        [],
        None,
        Tier.DEFAULT,
        "0be981ba59fb776062b03f5cf966ea8ebede80da007dd8849c1defa49c753e48",
        USER_PLAIN,
        USER_PLAIN,
    ),
    (
        "C-attachments-default",
        None,
        ATTACHMENTS,
        None,
        Tier.DEFAULT,
        "757625f9066281090d44b278463127d2eb7932be93c5bf81e7abec37f0e56faf",
        USER_ATTACH,
        HISTORY_ATTACH,
    ),
    (
        "D-summary-default",
        None,
        [],
        SUMMARY,
        Tier.DEFAULT,
        "a3775e969f075814b4884490b1f8d1e99c634178bbed61f9907314889d8a09b3",
        USER_PLAIN,
        USER_PLAIN,
    ),
    (
        "E-all-executive",
        FILLED,
        ATTACHMENTS,
        SUMMARY,
        Tier.EXECUTIVE,
        "dc0608957ca52b863f1461454ec29696e881c578fe0cd4904bf455258a94adee",
        USER_ATTACH,
        HISTORY_ATTACH,
    ),
    (
        "F-metadata-executive",
        FILLED,
        [],
        None,
        Tier.EXECUTIVE,
        "76c713fb766e74250fa414487635d66c978ab39de7e0f4cc41c7f1630d6e80ee",
        USER_PLAIN,
        USER_PLAIN,
    ),
    (
        "G-summary-executive",
        None,
        [],
        SUMMARY,
        Tier.EXECUTIVE,
        "8405a39061a1ea713e8c4b2f85c4174c46b2712f1b0043800930bb980c58361c",
        USER_PLAIN,
        USER_PLAIN,
    ),
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("case", "metadata", "attachments", "summary", "tier", "system_sha", "user_sha", "history_sha"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_v5_render_matches_golden_hashes(
    case, metadata, attachments, summary, tier, system_sha, user_sha, history_sha
):
    """prompt-templates: v5 is frozen; its rendered system and user match the golden hashes."""
    turn = SessionTurnInput(
        transcript=TRANSCRIPT,
        project_type="web_saas",
        detail_level="medium",
        output_format="line_items",
    )
    prompt = render_session_prompt(
        turn,
        "v5",
        attachments=attachments,
        project_metadata=metadata,
        response_language="es",
        conversation_summary=summary,
        tier=tier,
    )
    assert _sha(prompt.system) == system_sha
    assert _sha(prompt.user) == user_sha
    assert _sha(prompt.history_user) == history_sha
