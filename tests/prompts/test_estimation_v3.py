"""The three template tests of the S04 exercise statement, against the `v3` prompt.

They test the template, not the model: no LLM, no Redis and no network, in milliseconds.
Requirement: ci-pipeline "Tests de comportamiento del servicio" and prompt-templates.

Open Question #14: `confidence_pct` is a schema field, so it appears in the prompt of every
format; the test-2 keyword is the phases-table format block (which asks to take care of
each phase's `confidence_pct` because it is shown in the table).
"""

from app.prompts.loader import render_estimation_prompt
from tests.conftest import make_input

LITERAL = "App para reservar pistas de pádel con pagos <online> & avisos"
PHASES_TABLE_BLOCK = "Formato pedido: tabla por fases"
PER_PHASE_ASSUMPTIONS = "Lista los supuestos de cada fase"


def test_user_message_contains_the_description_literally_inside_project_description():
    """Exercise test 1 / prompt-templates, Scenario: Descripción literal."""
    request = make_input(description=LITERAL)
    system, user = render_estimation_prompt(request, "v3")
    assert f"<project_description>\n{LITERAL}\n</project_description>" in user
    assert LITERAL not in system


def test_phases_table_system_has_the_format_block_and_narrative_does_not():
    """Exercise test 2 / cag-prompt, Scenarios: Tabla por fases y Narrativa sin cifras (#14)."""
    phases, _ = render_estimation_prompt(make_input(output_format="phases_table"), "v3")
    narrative, _ = render_estimation_prompt(make_input(output_format="narrative"), "v3")
    assert PHASES_TABLE_BLOCK in phases
    assert PHASES_TABLE_BLOCK not in narrative
    # `confidence_pct` is a schema field: it is in every format's prompt.
    assert "confidence_pct" in phases
    assert "confidence_pct" in narrative


def test_detailed_system_asks_for_assumptions_per_phase_and_summary_does_not():
    """Exercise test 3 / cag-prompt, Scenarios: Detallado y Resumen."""
    detailed, _ = render_estimation_prompt(make_input(detail_level="detailed"), "v3")
    summary, _ = render_estimation_prompt(make_input(detail_level="summary"), "v3")
    assert PER_PHASE_ASSUMPTIONS in detailed
    assert PER_PHASE_ASSUMPTIONS not in summary
