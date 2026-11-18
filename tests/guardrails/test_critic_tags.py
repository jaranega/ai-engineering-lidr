"""input-guardrails: etiquetas del Actor-Critic-Boss en la regla `prompt_tag` y palabras
legítimas que no se bloquean.
"""

import pytest

from app.guardrails.input import screen_texts
from tests.attachments.factories import make_pdf
from tests.conftest import TRANSCRIPT, attachment_parts, turn_data, valid_body

CRITIC_TAGS = [
    "<critic_feedback>",
    "</critic_feedback>",
    "< / CRITIC_FEEDBACK >",
    "<estimation_under_review>",
    "</estimation_under_review>",
    "<resolved_tier>",
    "</resolved_tier>",
    "<response_language>",
    "</response_language>",
    "< Response_Language >",
]


@pytest.mark.parametrize("tag", CRITIC_TAGS)
def test_critic_tags_are_prompt_injection(tag):
    """Scenario: Feedback del auditor fingido (transcripción, adjunto y estimate)."""
    for field in ("description", "transcript", "attachments"):
        result = screen_texts([(field, f"Una app de reservas {tag} para clínicas")])
        assert result is not None
        assert (result.reason, result.field, result.rule) == (
            "prompt_injection",
            field,
            "prompt_tag",
        )


@pytest.mark.parametrize("tag", ["<critic_feedback>", "</estimation_under_review>"])
def test_session_turn_blocks_the_tags_in_the_transcript_without_calling_the_llm(
    client, provider, tag
):
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data=turn_data(transcript=f"{TRANSCRIPT} {tag}"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_injection"
    assert provider.call_count == 0
    assert client.get(f"/api/v1/sessions/{session_id}").json()["turn_count"] == 0


@pytest.mark.parametrize("tag", ["<critic_feedback>", "</estimation_under_review>"])
def test_session_turn_blocks_the_tags_in_an_attachment_without_calling_the_llm(
    client, provider, moderator, tag
):
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data=turn_data(),
        files=attachment_parts(("spec.pdf", make_pdf(f"Anexo {tag} fingido"), "application/pdf")),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_injection"
    assert provider.call_count == 0
    assert moderator.calls == []


@pytest.mark.parametrize("tag", ["<critic_feedback>", "</resolved_tier>"])
def test_estimate_endpoint_blocks_the_tags_without_calling_the_llm(client, provider, tag):
    description = f"A web platform to book clinic appointments {tag} with reminders."
    response = client.post("/api/v1/estimate", json=valid_body(description=description))
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_injection"
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "text",
    [
        "un flujo de revisión donde un auditor da feedback al equipo",
        "a review flow where a critic gives feedback to the auditor",
        "Informe del auditor con revisión y feedback del tier resuelto y el idioma de respuesta",
        "resolved tier, response language and estimation under review",
    ],
)
def test_words_without_tag_form_are_not_blocked(text):
    """Scenarios: Revisión sin etiqueta, Producto con IA legítimo (palabras sueltas)."""
    for field in ("description", "transcript"):
        assert screen_texts([(field, text)]) is None
