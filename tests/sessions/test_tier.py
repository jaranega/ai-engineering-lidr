"""audience-tier: `resolve_tier` (pure rules, no LLM, no network)."""

import ast
import json
from pathlib import Path
from typing import get_args

import pytest

from app.sessions.models import ProjectMetadata
from app.sessions.tier import Tier, TierDecision, TierRule, resolve_tier

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
EMPTY = ProjectMetadata()


def resolve(transcript: str, metadata: ProjectMetadata = EMPTY, explicit: Tier | None = None):
    return resolve_tier(transcript, metadata, explicit)


def test_enum_and_rule_names():
    assert [t.value for t in Tier] == ["executive", "pm", "developer", "default"]
    assert get_args(TierRule) == (
        "explicit",
        "nda_detected",
        "regulatory_context",
        "technical_audience",
        "small_team",
        "default",
        "prompt_version",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Hemos firmado un NDA con el cliente",
        "Hay un acuerdo de confidencialidad con el cliente",
        "We signed an NDA with the client",
    ],
)
def test_nda_in_the_turn_is_executive(text):
    """Scenario: NDA en el turno (ES y EN)."""
    assert resolve(text) == TierDecision(Tier.EXECUTIVE, "nda_detected")


@pytest.mark.parametrize(
    "text",
    [
        "The platform must be HIPAA compliant",
        "El sistema debe cumplir el RGPD",
        "We need ISO 27001 certification",
    ],
)
def test_regulation_is_executive_with_regulatory_context(text):
    """Scenario: Normativa en inglés (y en español)."""
    assert resolve(text) == TierDecision(Tier.EXECUTIVE, "regulatory_context")


def test_an_nda_remembered_by_the_metadata_counts():
    """Scenario: NDA recordado por el metadata."""
    metadata = ProjectMetadata(explicit_constraints=["NDA firmado con el cliente"])
    assert resolve("Añade un panel de informes", metadata) == TierDecision(
        Tier.EXECUTIVE, "nda_detected"
    )


def test_a_regulation_remembered_by_the_metadata_counts():
    metadata = ProjectMetadata(explicit_constraints=["Cumplir GDPR"])
    assert resolve("Add a reports panel", metadata) == TierDecision(
        Tier.EXECUTIVE, "regulatory_context"
    )


def test_an_nda_remembered_by_the_history_counts():
    """Scenario: NDA recordado por el historial."""
    history = ["Build a booking web app", "We have signed an NDA with the client"]
    assert resolve_tier("Add Stripe payments", EMPTY, None, history) == TierDecision(
        Tier.EXECUTIVE, "nda_detected"
    )


def test_a_regulation_remembered_by_the_history_counts():
    history = ["La plataforma debe cumplir el RGPD"]
    assert resolve_tier("Añade pagos con Stripe", EMPTY, None, history) == TierDecision(
        Tier.EXECUTIVE, "regulatory_context"
    )


def test_technical_terms_in_the_history_do_not_make_a_developer_audience():
    history = ["We need a REST API on Kubernetes with Kafka"]
    assert resolve_tier("Add a reports panel", EMPTY, None, history) == TierDecision(
        Tier.DEFAULT, "default"
    )


def test_history_messages_default_to_none():
    assert resolve_tier("Add a reports panel", EMPTY, None) == TierDecision(Tier.DEFAULT, "default")


def test_explicit_wins_over_an_nda_in_the_history():
    history = ["We signed an NDA"]
    assert resolve_tier("x", EMPTY, Tier.PM, history) == TierDecision(Tier.PM, "explicit")


def test_nda_wins_over_regulation_and_technical_terms():
    """Scenario: Precedencia."""
    text = "Hay un NDA, aplica el RGPD y usamos kubernetes y graphql"
    assert resolve(text) == TierDecision(Tier.EXECUTIVE, "nda_detected")


def test_regulation_wins_over_technical_terms_and_small_team():
    metadata = ProjectMetadata(assumed_team_size=1)
    assert resolve("GDPR, kubernetes y graphql", metadata).rule == "regulatory_context"


def test_technical_terms_win_over_small_team():
    metadata = ProjectMetadata(assumed_team_size=1)
    assert resolve("Exponemos un endpoint GraphQL", metadata).rule == "technical_audience"


def test_no_signals_is_default():
    """Scenario: Sin señales."""
    text = "Queremos una app para reservar mesas en restaurantes"
    assert resolve(text) == TierDecision(Tier.DEFAULT, "default")


def test_explicit_override_wins_over_a_rule():
    """Scenario: Override sobre una regla."""
    assert resolve("Hemos firmado un NDA", explicit=Tier.DEVELOPER) == TierDecision(
        Tier.DEVELOPER, "explicit"
    )


def test_explicit_default_forces_no_adaptation():
    """Scenario: Forzar sin adaptación."""
    assert resolve("Hemos firmado un NDA", explicit=Tier.DEFAULT) == TierDecision(
        Tier.DEFAULT, "explicit"
    )


@pytest.mark.parametrize("tier", list(Tier))
def test_every_explicit_tier_is_returned_as_is(tier):
    assert resolve("cualquier texto", explicit=tier) == TierDecision(tier, "explicit")


# --- Vocabulario técnico ---


def test_one_technical_term_is_not_developer():
    """Scenario: Un término."""
    assert resolve("Necesitamos una API para el catálogo").tier is not Tier.DEVELOPER


def test_two_distinct_technical_terms_are_developer():
    """Scenario: Dos términos distintos."""
    assert resolve("Exponemos un endpoint GraphQL detrás de Kubernetes") == TierDecision(
        Tier.DEVELOPER, "technical_audience"
    )


def test_the_same_term_repeated_counts_once():
    """Scenario: El mismo término repetido."""
    assert resolve("API pública y API interna").tier is not Tier.DEVELOPER
    assert resolve("api api API apis endpoint").tier is Tier.DEVELOPER  # api + endpoint


def test_product_names_do_not_count():
    """Scenario: Nombres de producto."""
    assert resolve("App iOS con pagos Stripe y tienda Shopify").tier is not Tier.DEVELOPER
    assert resolve("app web con datos de la tienda").tier is Tier.DEFAULT


@pytest.mark.parametrize(
    "text",
    [
        "Tenemos microservicios y latencia alta",
        "Hay que refactorizar el esquema de base de datos",
        "We run postgres and redis behind a docker setup",
        "Our CI/CD pipeline uses terraform",
    ],
)
def test_vocabulary_is_spanish_and_english(text):
    assert resolve(text) == TierDecision(Tier.DEVELOPER, "technical_audience")


@pytest.mark.parametrize(
    ("text", "developer"),
    [
        ("A REST API for the shop", False),  # "REST API" is one group
        ("RESTful services and an endpoint", True),
        ("REST and an API", False),  # REST and API are one group too
        ("REST and graphql", True),
        ("We will do the rest of the app later and use graphql", False),
        ("the rest of the app, plus a webhook and an sdk", True),
        ("rest of the backend", False),
    ],
)
def test_rest_counts_only_as_uppercase_or_restful_and_groups_with_api(text, developer):
    assert (resolve(text).tier is Tier.DEVELOPER) is developer


def test_terms_use_word_boundaries_and_ignore_case():
    assert resolve("a SPARKling kafkaesque story").tier is not Tier.DEVELOPER
    assert resolve("KUBERNETES y Docker").tier is Tier.DEVELOPER


# --- Equipo pequeño ---


def test_team_sizes_none_two_and_three():
    """Scenario: Tamaños de equipo."""
    text = "Queremos ampliar el catálogo"
    decisions = [resolve(text, ProjectMetadata(assumed_team_size=size)) for size in (None, 2, 3)]
    assert decisions == [
        TierDecision(Tier.DEFAULT, "default"),
        TierDecision(Tier.PM, "small_team"),
        TierDecision(Tier.DEFAULT, "default"),
    ]


def test_a_team_of_one_is_small():
    assert resolve("hola", ProjectMetadata(assumed_team_size=1)).rule == "small_team"


def test_first_turn_with_empty_metadata_never_applies_small_team():
    assert resolve("hola").rule == "default"


# --- Fuentes ---


def test_the_tier_is_recomputed_on_every_call_from_its_inputs_only():
    first = resolve("Hemos firmado un NDA")
    second = resolve("Añade un panel")
    assert (first.tier, second.tier) == (Tier.EXECUTIVE, Tier.DEFAULT)


@pytest.mark.parametrize("name", ["reserva-restaurantes-es.json", "restaurant-booking-en.json"])
def test_samples_resolve_executive_by_regulatory_context(name):
    """Scenario: Muestras del repositorio (RGPD / GDPR)."""
    description = json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))["description"]
    assert resolve(description) == TierDecision(Tier.EXECUTIVE, "regulatory_context")


def test_decision_is_immutable():
    decision = resolve("hola")
    with pytest.raises(AttributeError):
        decision.tier = Tier.PM  # type: ignore[misc]


def test_tier_module_does_not_import_services_nor_prompts():
    """Requirement: tier.py no importa app.services ni app.prompts."""
    path = Path(__file__).resolve().parents[2] / "app/sessions/tier.py"
    modules = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
    assert not [m for m in modules if m.startswith(("app.services", "app.prompts"))]


def test_nda_and_regulation_patterns_are_the_same_objects_as_the_anchor_detector():
    """Requirement: una sola fuente de patrones."""
    from app.sessions import tier
    from app.sessions.compression import ANCHOR_RULES

    assert tier.NDA_PATTERNS is ANCHOR_RULES["nda"]
    assert tier.REGULATION_PATTERNS is ANCHOR_RULES["regulation"]
