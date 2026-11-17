"""memory-compression: Detector heurístico de anclas (pure, no LLM, no network)."""

import dataclasses
import json
from pathlib import Path

import pytest

from app.sessions.compression import ANCHOR_RULE_IDS, AnchorDecision, detect_anchor
from app.sessions.models import Turn

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"


def turn(user: str, assistant: str = "Respuesta del asistente.") -> Turn:
    return Turn(number=1, user=user, assistant=assistant)


def rules_of(user: str) -> tuple[str, ...]:
    return detect_anchor(turn(user)).rules


def test_rule_ids_are_the_documented_ones_in_order():
    assert ANCHOR_RULE_IDS == (
        "nda",
        "contract_signed",
        "scope_frozen",
        "budget_fixed",
        "regulation",
        "hard_deadline",
        "client_commitment",
    )


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("nda", "Hemos firmado un acuerdo de confidencialidad con el cliente"),
        ("nda", "We signed the NDA last week"),
        ("nda", "Existe una cláusula de confidencialidad en el contrato marco"),
        ("nda", "There is a non-disclosure agreement in place"),
        ("contract_signed", "Ya hemos firmado el contrato con el proveedor"),
        ("contract_signed", "We signed the contract yesterday"),
        ("scope_frozen", "El alcance está cerrado y no cambiará"),
        ("scope_frozen", "The scope is frozen until launch"),
        ("budget_fixed", "El presupuesto está cerrado en 40 000 €"),
        ("budget_fixed", "No podemos pasar de 50.000 euros"),
        ("budget_fixed", "The budget is capped at $45k"),
        ("budget_fixed", "Costs cannot exceed $60k in total"),
        ("budget_fixed", "The total is not to exceed the approved figure"),
        ("regulation", "El sistema debe cumplir el RGPD"),
        ("regulation", "The platform must be HIPAA compliant"),
        ("regulation", "Necesitamos certificación ISO 27001"),
        ("hard_deadline", "La fecha límite es innegociable"),
        ("hard_deadline", "Tiene que estar en producción antes del 1 de marzo"),
        ("hard_deadline", "This is a hard deadline set by the board"),
        ("hard_deadline", "It must go live by June"),
        ("client_commitment", "Queda acordado que el cliente aporta los datos"),
        ("client_commitment", "We agreed that the client provides the data"),
        ("client_commitment", "This is a mandatory requirement for the client"),
    ],
)
def test_each_rule_fires_in_spanish_and_english(rule, text):
    """Scenarios: NDA en español, Presupuesto cerrado en inglés (un positivo por regla)."""
    decision = detect_anchor(turn(text))
    assert decision.is_anchor is True
    assert rule in decision.rules
    assert decision.source == "heuristic"


def test_nda_in_spanish_has_the_heuristic_source():
    """Scenario: NDA en español."""
    decision = detect_anchor(turn("Hemos firmado un acuerdo de confidencialidad con el cliente"))
    assert decision == AnchorDecision(is_anchor=True, rules=("nda",), source="heuristic")


def test_capped_budget_in_english_is_only_budget_fixed():
    """Scenario: Presupuesto cerrado en inglés."""
    decision = detect_anchor(turn("The budget is capped at $45k"))
    assert decision.is_anchor and decision.rules == ("budget_fixed",)


def test_several_rules_are_reported_in_rule_order():
    """Scenario: Varias reglas en orden."""
    text = "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €"
    assert rules_of(text) == ("nda", "budget_fixed")
    # Same rules, mentioned in the opposite order in the text: the report keeps rule order.
    assert rules_of("El presupuesto está cerrado en 40 000 € y hemos firmado un NDA") == (
        "nda",
        "budget_fixed",
    )


@pytest.mark.parametrize(
    "text", ["revisamos la agenda del proyecto", "nda", "la agenda de la nda", "Un ndaX"]
)
def test_acronyms_are_case_sensitive_with_word_boundaries(text):
    """Scenario: Siglas con mayúsculas."""
    assert detect_anchor(turn(text)).is_anchor is False


@pytest.mark.parametrize(
    "text", ["La ens es otra cosa", "the sox are socks", "dora la exploradora"]
)
def test_regulation_acronyms_are_case_sensitive(text):
    assert rules_of(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "presupuesto aproximado",
        "¿cuál sería el presupuesto?",
        "confidence level",
        "módulo para firmar contratos online",
        "users sign contracts in the app",
        "fecha de entrega estimada",
        "ISO 8601",
        "la app debe estar en inglés",
    ],
)
def test_negatives_corpus(text):
    """Scenario: Corpus de negativos."""
    assert detect_anchor(turn(text)).is_anchor is False


@pytest.mark.parametrize(
    "text",
    [
        "cannot exceed 200 ms of latency",
        "no podemos superar los 100 usuarios",
        "the budget is still open",
        "we will agree on dates later",
    ],
)
def test_qualifiers_are_required_besides_the_commitment_term(text):
    """Requirement: cada regla exige término más calificador (salvo regulation)."""
    assert detect_anchor(turn(text)).is_anchor is False


def test_accents_are_optional_and_case_is_ignored_outside_acronyms():
    assert "budget_fixed" in rules_of("EL PRESUPUESTO ESTA CERRADO")
    assert "scope_frozen" in rules_of("el alcance esta CONGELADO")


def test_the_assistant_text_does_not_count():
    """Scenario: El texto del asistente no cuenta."""
    decision = detect_anchor(turn("Quiero una app de reservas", "Riesgo: cumplimiento RGPD"))
    assert decision.is_anchor is False and decision.rules == ()


def test_decision_is_immutable():
    decision = detect_anchor(turn("We signed the NDA"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.is_anchor = False  # type: ignore[misc]


def test_detection_is_deterministic():
    text = "Hemos firmado un NDA y el RGPD aplica"
    assert detect_anchor(turn(text)) == detect_anchor(turn(text))


@pytest.mark.parametrize("name", ["reserva-restaurantes-es.json", "restaurant-booking-en.json"])
def test_samples_trigger_only_the_regulation_rule(name):
    """Scenario: Muestras del repositorio (RGPD / GDPR, nada más)."""
    description = json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))["description"]
    decision = detect_anchor(turn(description))
    assert decision.is_anchor is True
    assert decision.rules == ("regulation",)
