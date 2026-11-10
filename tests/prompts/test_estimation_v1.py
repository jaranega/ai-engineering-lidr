"""The three template tests of the exercise statement (part 4), against the `v1` prompt.

They test the template, not the model: no LLM, no Redis and no network, in milliseconds.
Requirement: ci-pipeline "Tests de comportamiento del servicio" and prompt-templates.
"""

from app.prompts.loader import render_estimation_prompt
from tests.conftest import make_input

LITERAL = "App para reservar pistas de pádel con pagos <online> & avisos"


def test_user_message_contains_the_description_literally_inside_project_description():
    """Exercise test 1 / prompt-templates, Scenario: Descripción literal."""
    request = make_input(description=LITERAL)
    system, user = render_estimation_prompt(request, "v1")
    assert f"<project_description>\n{LITERAL}\n</project_description>" in user
    assert LITERAL not in system


def test_phases_table_system_has_the_format_keyword_and_narrative_does_not():
    """Exercise test 2 / cag-prompt, Scenarios: Formato por fases y Formato narrativo."""
    phases, _ = render_estimation_prompt(make_input(output_format="phases_table"), "v1")
    narrative, _ = render_estimation_prompt(make_input(output_format="narrative"), "v1")
    assert "confidence_pct" in phases
    assert "confidence_pct" not in narrative


def test_detailed_system_asks_for_assumptions_per_phase_and_summary_does_not():
    """Exercise test 3 / cag-prompt, Scenarios: Detallado y Resumen."""
    detailed, _ = render_estimation_prompt(make_input(detail_level="detailed"), "v1")
    summary, _ = render_estimation_prompt(make_input(detail_level="summary"), "v1")
    assert "Lista los supuestos de cada fase" in detailed
    assert "Lista los supuestos de cada fase" not in summary
