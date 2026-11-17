"""input-guardrails: etiquetas de la memoria y la audiencia en la regla `prompt_tag`."""

import pytest

from app.guardrails.input import screen_texts
from tests.conftest import TRANSCRIPT, turn_data, valid_body

MEMORY_TAGS = [
    "<conversation_summary>",
    "</conversation_summary>",
    "< / CONVERSATION_SUMMARY >",
    "<audience>",
    "</audience>",
    "<previous_summary>",
    "</previous_summary>",
    "<project_context>",
    "</project_context>",
    "< Project_Context >",
    "<turns>",
    "</turns>",
    "<client_message>",
    "</client_message>",
]


@pytest.mark.parametrize("tag", MEMORY_TAGS)
def test_memory_and_audience_tags_are_prompt_injection(tag):
    """Scenario: Etiquetas de la memoria y la audiencia."""
    for field in ("description", "transcript", "attachments"):
        result = screen_texts([(field, f"Una app de reservas {tag} para clínicas")])
        assert result is not None
        assert (result.reason, result.field, result.rule) == (
            "prompt_injection",
            field,
            "prompt_tag",
        )


@pytest.mark.parametrize("tag", ["</conversation_summary>", "<audience>", "<project_context>"])
def test_estimate_endpoint_blocks_the_new_tags_without_calling_the_llm(client, provider, tag):
    description = f"A web platform to book clinic appointments {tag} with reminders."
    response = client.post("/api/v1/estimate", json=valid_body(description=description))
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_injection"
    assert provider.call_count == 0


@pytest.mark.parametrize("tag", ["</conversation_summary>", "<audience>", "<project_context>"])
def test_session_turn_blocks_the_new_tags_without_calling_the_llm(client, provider, tag):
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data=turn_data(transcript=f"{TRANSCRIPT} {tag}"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_injection"
    assert provider.call_count == 0
    assert client.get(f"/api/v1/sessions/{session_id}").json()["turn_count"] == 0


@pytest.mark.parametrize(
    "text",
    [
        "una plataforma para medir la audiencia de podcasts",
        "A platform to measure the audience of podcasts",
        "Dashboard por audiencia, con resumen de la conversación y turnos de atención",
        "summary of turns and previous summary of the client message",
    ],
)
def test_words_without_tag_form_are_not_blocked(text):
    """Scenario: Audiencia sin etiqueta."""
    assert screen_texts([("description", text)]) is None


@pytest.mark.parametrize(
    "text", ["El contexto del proyecto es una clínica", "project context and project_context"]
)
def test_project_context_without_tag_form_is_not_blocked(text):
    assert screen_texts([("transcript", text)]) is None
