"""Tests of the description language detection and its line in the prompt (S04 D14)."""

import json
from pathlib import Path

import pytest

from app.prompts.language import detect_language
from app.prompts.loader import PROMPT_VERSIONS, render_estimation_prompt
from tests.conftest import make_input

SAMPLES = Path(__file__).parents[2] / "samples"
DETECTED = {
    "en": 'The project description is in English: set `language` to "en"',
    "es": 'The project description is in Spanish: set `language` to "es"',
}
SPANISH_REFERENCES = [
    {
        "name": "Reservas clínica dental",
        "summary": "Plataforma web de citas online con pagos y recordatorios por SMS.",
    }
]


@pytest.mark.parametrize(
    ("sample", "language"),
    [("restaurant-booking-en.json", "en"), ("reserva-restaurantes-es.json", "es")],
)
def test_detects_the_language_of_the_samples(sample, language):
    description = json.loads((SAMPLES / sample).read_text())["description"]
    assert detect_language(description) == language


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("A web platform to book clinic appointments, with patient records and reminders.", "en"),
        ("Una plataforma web para reservar citas en la clínica, con historial y avisos.", "es"),
        ("ÚNICA APP DE RESERVAS PARA LOS RESTAURANTES DEL GRUPO Y SUS CLIENTES", "es"),
    ],
)
def test_detects_short_descriptions(text, language):
    assert detect_language(text) == language


@pytest.mark.parametrize(
    "text",
    [
        "CRM ERP SaaS API OAuth Stripe Kafka dashboards KPIs",  # no function words
        "Plataforma de reservas, the booking platform para clientes and partners",  # mixed
        "",
    ],
)
def test_ambiguous_text_has_no_language(text):
    assert detect_language(text) is None


@pytest.mark.parametrize("version", PROMPT_VERSIONS)
@pytest.mark.parametrize("language", ["en", "es"])
def test_system_prompt_ends_naming_the_detected_language(version, language):
    sample = "restaurant-booking-en.json" if language == "en" else "reserva-restaurantes-es.json"
    body = json.loads((SAMPLES / sample).read_text())
    system, _ = render_estimation_prompt(make_input(**body), version)
    assert system.splitlines()[-1].startswith(DETECTED[language])
    other = "es" if language == "en" else "en"
    assert DETECTED[other] not in system


def test_spanish_references_do_not_change_the_detected_language():
    """Only the description decides: Spanish reference projects keep an English answer."""
    system, user = render_estimation_prompt(make_input(reference_projects=SPANISH_REFERENCES))
    assert system.splitlines()[-1].startswith(DETECTED["en"])
    assert "Reservas clínica dental" in user


def test_ambiguous_description_keeps_only_the_generic_rule():
    description = "CRM ERP SaaS API OAuth Stripe Kafka dashboards KPIs integration"
    system, _ = render_estimation_prompt(make_input(description=description))
    assert system.endswith(
        "if the project description is in English, write them entirely in English."
    )
    assert all(line not in system for line in DETECTED.values())
