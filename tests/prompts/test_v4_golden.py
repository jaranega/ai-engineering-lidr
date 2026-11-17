"""Golden hashes of the published prompt version v4 (session prompts).

v4 is published and immutable (prompt-templates). If this test fails, a change touched v4:
create a new prompt version instead of editing it.
"""

import hashlib

import pytest

from app.domain import SessionTurnInput
from app.prompts.loader import render_session_prompt
from app.sessions import ProjectMetadata

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

SYSTEM_EMPTY = "f2c1a3dd674340a72530f0fdb2061b24b5a65742a1094a0775cd88f9a0f70517"
SYSTEM_FILLED = "4a091b542696916c53787dc1dee9b918837246c11a09fa0c02b3400a1bdb0b2c"
USER_PLAIN = "7ba7c736ba98dbd9c86b79c03d33230038831a15b4a2b12099d8642828e77d25"
USER_ATTACH = "da5e23203af710227c9a1ade00121d10cc42e7116052ffb5c441483b93d30ac2"
HISTORY_ATTACH = "3c9466a694fe4f66f204cc32128b2936f8137d128a8957ef909756656b72c641"

# (id, project_metadata, attachments, system sha256, user sha256, history_user sha256)
CASES = [
    ("A-no-metadata-no-attachments", None, [], SYSTEM_EMPTY, USER_PLAIN, USER_PLAIN),
    ("B-metadata-no-attachments", FILLED, [], SYSTEM_FILLED, USER_PLAIN, USER_PLAIN),
    ("C-no-metadata-attachments", None, ATTACHMENTS, SYSTEM_EMPTY, USER_ATTACH, HISTORY_ATTACH),
    ("D-metadata-attachments", FILLED, ATTACHMENTS, SYSTEM_FILLED, USER_ATTACH, HISTORY_ATTACH),
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("case", "metadata", "attachments", "system_sha", "user_sha", "history_sha"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_v4_render_matches_golden_hashes(
    case, metadata, attachments, system_sha, user_sha, history_sha
):
    """prompt-templates: v4 is frozen; its rendered system and user match the golden hashes."""
    turn = SessionTurnInput(
        transcript=TRANSCRIPT,
        project_type="web_saas",
        detail_level="medium",
        output_format="line_items",
    )
    prompt = render_session_prompt(
        turn,
        "v4",
        attachments=attachments,
        project_metadata=metadata,
        response_language="es",
    )
    assert _sha(prompt.system) == system_sha
    assert _sha(prompt.user) == user_sha
    assert _sha(prompt.history_user) == history_sha
