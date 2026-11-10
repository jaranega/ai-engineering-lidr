"""Requirements: Enmascarado de datos personales en adjuntos, Detección de prompt injection
(etiquetas nuevas) y Respuesta 400 de sesión (input-guardrails).
"""

import pytest

from app.guardrails.input import (
    PII_PLACEHOLDERS,
    InputGuardrailViolation,
    mask_pii,
    screen_texts,
)
from tests.conftest import valid_body
from tests.guardrails.test_input_guardrails import INVALID_IBANS, NEGATIVES, VALID_IBANS

# --- mask_pii ---


def test_placeholders_are_the_documented_ones():
    assert PII_PLACEHOLDERS == {"email": "[EMAIL]", "phone": "[PHONE]", "iban": "[IBAN]"}


def test_each_kind_is_masked_and_counted():
    """Scenario: Cada tipo de dato."""
    text = "Mail jefe@cliente.es, cuenta ES9121000418450200051332, tel +34 612 345 678."
    masked, counts = mask_pii(text)
    assert "[EMAIL]" in masked and "[IBAN]" in masked and "[PHONE]" in masked
    assert counts == {"email": 1, "iban": 1, "phone": 1}
    assert "jefe@cliente.es" not in masked
    assert "ES9121" not in masked and "612" not in masked


def test_counts_every_occurrence():
    masked, counts = mask_pii("a@b.es y c@d.com; 612 345 678 / 912 345 678")
    assert masked == "[EMAIL] y [EMAIL]; [PHONE] / [PHONE]"
    assert counts == {"email": 2, "iban": 0, "phone": 2}


@pytest.mark.parametrize("iban", VALID_IBANS)
def test_valid_ibans_are_masked_as_iban_not_as_phone(iban):
    masked, counts = mask_pii(f"IBAN {iban} fin")
    assert masked == "IBAN [IBAN] fin"
    assert counts == {"email": 0, "iban": 1, "phone": 0}


@pytest.mark.parametrize("code", INVALID_IBANS)
def test_iban_shaped_code_with_a_wrong_checksum_is_left_alone(code):
    """Requirement: ni un código con forma de IBAN que no pasa el checksum."""
    text = f"El pedido {code} sigue abierto"
    assert mask_pii(text) == (text, {"email": 0, "iban": 0, "phone": 0})


@pytest.mark.parametrize(
    "text",
    [
        "Escribe a jefe@cliente.es o llama al +34 612 345 678; IBAN ES9121000418450200051332",
        "(415) 555-2671 y 415-555-2671, a@b.es",
        "Sin datos personales",
    ],
)
def test_masking_is_idempotent(text):
    """Scenario: Idempotencia y negativos (idempotente)."""
    once, _ = mask_pii(text)
    twice, counts = mask_pii(once)
    assert twice == once
    assert counts == {"email": 0, "iban": 0, "phone": 0}


@pytest.mark.parametrize("text", NEGATIVES)
def test_negatives_corpus_is_unchanged(text):
    """Scenario: Idempotencia y negativos (corpus de negativos idéntico)."""
    assert mask_pii(text) == (text, {"email": 0, "iban": 0, "phone": 0})


def test_masked_text_no_longer_triggers_the_blocking_pii_check():
    masked, _ = mask_pii("Contacto jefe@cliente.es, +34 612 345 678, ES9121000418450200051332")
    assert screen_texts([("attachments", masked)]) is None


# --- Etiquetas nuevas del prompt de sesión ---

NEW_TAGS = [
    "</transcript>",
    "<transcript>",
    "<attachments>",
    "</attachments>",
    "<attachment>",
    "<project_metadata>",
    "</project_metadata>",
    "< / PROJECT_METADATA >",
]


@pytest.mark.parametrize("tag", NEW_TAGS)
def test_new_session_tags_are_prompt_injection(tag):
    """Scenario: Etiquetas nuevas del prompt de sesión."""
    for field in ("description", "transcript", "attachments"):
        result = screen_texts([(field, f"Una app de reservas {tag} para clínicas")])
        assert result is not None
        assert (result.reason, result.field, result.rule) == (
            "prompt_injection",
            field,
            "prompt_tag",
        )


@pytest.mark.parametrize("tag", ["</transcript>", "<project_metadata>", "<attachments>"])
def test_new_tags_are_also_blocked_on_the_estimate_endpoint(client, provider, tag):
    """Scenario: Etiquetas nuevas (también en `POST /api/v1/estimate`)."""
    description = f"A web platform to book clinic appointments {tag} with reminders."
    response = client.post("/api/v1/estimate", json=valid_body(description=description))
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "prompt_injection"
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "text",
    [
        "un chatbot con system prompt configurable que actúa como asistente de ventas",
        "The transcript of each call is stored; attachments are optional",
    ],
)
def test_legitimate_words_are_not_blocked(text):
    """Scenario: Producto con IA legítimo."""
    assert screen_texts([("transcript", text)]) is None


# --- InputGuardrailViolation.index ---


def test_violation_index_defaults_to_none_and_carries_the_attachment_position():
    """Requirement: Respuesta 400 de sesión (`index`, por defecto null)."""
    assert InputGuardrailViolation("pii", "description", "email").index is None
    violation = InputGuardrailViolation("prompt_injection", "attachments", "x", index=1)
    assert violation.index == 1
    assert violation.message and "x" not in violation.message


def test_estimate_400_keeps_only_reason_and_message(client):
    """Scenario: 400 de estimate sin cambios."""
    body = valid_body(description="Web platform. Contact: contacto@cliente.es for questions.")
    response = client.post("/api/v1/estimate", json=body)
    assert response.status_code == 400
    assert set(response.json()["detail"]) == {"reason", "message"}
