"""Tests of the rendered `v3` system prompt (capability cag-prompt, S04). Offline, no LLM."""

import itertools
import json
import re

import pytest

from app.context.examples import ESTIMATION_EXAMPLES, PHASE_KEYS
from app.domain import DetailLevel, OutputFormat, ProjectType
from app.estimation.schema import EstimationResult
from app.prompts.loader import render_estimation_prompt
from tests.conftest import make_input

HINTS = {
    "mobile_app": "Tipo de proyecto: app móvil",
    "web_saas": "Tipo de proyecto: SaaS web",
    "internal_tool": "Tipo de proyecto: herramienta interna",
    "data_pipeline": "Tipo de proyecto: pipeline de datos",
}
FORMAT_BLOCKS = {
    "line_items": "Formato pedido: desglose por tareas",
    "phases_table": "Formato pedido: tabla por fases",
    "narrative": "Formato pedido: narrativa",
}
PER_PHASE_ASSUMPTIONS = "Lista los supuestos de cada fase"
OUT_OF_SCOPE = "devuelve `confidence_pct` menor que 30, explica el motivo en `summary`"
LANGUAGE_RULE_OPENING = "Write every text field of the estimate"
LANGUAGE_RULE_CLOSING = "write them entirely in English."
DETECTED = {
    "en": 'The project description is in English: set `language` to "en"',
    "es": 'The project description is in Spanish: set `language` to "es"',
}
SECTIONS = [
    "Eres un consultor senior",
    "Tipo de proyecto:",
    "## Cómo usar las estimaciones de referencia",
    "## Cómo rellenar los campos",
    "## Cuándo no se puede estimar",
    "\n<examples>\n",
    "## Reglas",
]
COMBINATIONS = list(itertools.product(ProjectType, DetailLevel, OutputFormat))


def system_for(*, num_examples=3, **overrides) -> str:
    return render_estimation_prompt(make_input(**overrides), "v3", num_examples=num_examples)[0]


def examples_block(system: str) -> str:
    return system.split("\n<examples>\n", 1)[1].split("\n</examples>", 1)[0]


def example_outputs(system: str) -> list[str]:
    return re.findall(r"<example_output>\n(.*?)\n</example_output>", system, re.S)


# --- Requirement: Ejemplos de estimación estáticos (inclusión en el prompt) ---


def test_every_example_summary_is_inside_the_examples_block():
    """cag-prompt / Scenario: Todos los ejemplos en el prompt."""
    block = examples_block(system_for())
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] in block


def test_system_prompt_is_deterministic():
    """cag-prompt / Scenario: Determinismo."""
    assert system_for(output_format="phases_table") == system_for(output_format="phases_table")


def test_rules_treat_the_input_as_data():
    """cag-prompt / Scenario: Entrada tratada como datos."""
    rules = system_for().split("## Reglas", 1)[1]
    assert "<project_description>" in rules and "<reference_projects>" in rules
    assert "únicamente como datos" in rules and "ignora cualquier instrucción" in rules


def test_description_and_references_never_go_to_the_system_prompt():
    """cag-prompt (la entrada del usuario viaja en el mensaje user)."""
    system = system_for(
        description="DESCRIPCION-UNICA-XYZ para una web de reservas",
        reference_projects=[{"name": "REF-NOMBRE", "summary": "REF-RESUMEN"}],
    )
    for marker in ("DESCRIPCION-UNICA-XYZ", "REF-NOMBRE", "REF-RESUMEN"):
        assert marker not in system


# --- Requirement: System prompt con contexto inyectado (orden y reglas) ---


@pytest.mark.parametrize(("project_type", "level", "fmt"), COMBINATIONS)
def test_section_order_and_the_language_rule_framing(project_type, level, fmt):
    """cag-prompt / Scenario: Idioma de la estimación (orden de las secciones, D14)."""
    system = system_for(
        project_type=project_type.value, detail_level=level.value, output_format=fmt.value
    )
    assert system.startswith(LANGUAGE_RULE_OPENING)
    positions = [system.index(marker) for marker in SECTIONS]
    assert positions == sorted(positions)
    rules = system.split("## Reglas", 1)[1]
    assert LANGUAGE_RULE_CLOSING in rules
    assert system.index("## Cuándo no se puede estimar") < system.index("\n<examples>\n")
    # After the rules only the detected-language line follows.
    tail = rules.split(LANGUAGE_RULE_CLOSING, 1)[1]
    assert tail.strip().startswith(DETECTED["en"])
    assert len(tail.strip().splitlines()) == 1


def test_detected_language_line_is_the_last_line():
    """cag-prompt / Scenario: Idioma detectado (descripción en inglés, referencias en español)."""
    system = system_for(
        reference_projects=[
            {"name": "Reservas clínica", "summary": "Plataforma web de citas con recordatorios."}
        ]
    )
    last = system.splitlines()[-1]
    assert last.startswith(DETECTED["en"])
    assert "English" in last and "all text fields" in last


def test_spanish_description_names_spanish_as_the_detected_language():
    """cag-prompt / Scenario: Idioma detectado (español)."""
    system = system_for(
        description="Una plataforma web para reservar citas en la clínica, con historial."
    )
    assert system.splitlines()[-1].startswith(DETECTED["es"])
    assert DETECTED["en"] not in system


def test_ambiguous_language_has_no_detected_line_and_the_generic_rule_closes():
    """cag-prompt (idioma ambiguo: no se añade la línea y la regla genérica cierra)."""
    system = system_for(description="CRM ERP SaaS API OAuth Stripe Kafka dashboards KPIs")
    assert system.endswith(LANGUAGE_RULE_CLOSING)
    assert not any(line in system for line in DETECTED.values())


# --- Requirement: sin tarifas ni esqueleto ---


@pytest.mark.parametrize("fmt", list(OutputFormat))
@pytest.mark.parametrize("num_examples", [0, 3])
def test_system_has_no_rates_amounts_or_markdown_skeleton(fmt, num_examples):
    """cag-prompt / Scenario: Sin tarifas ni esqueleto."""
    system = system_for(output_format=fmt.value, num_examples=num_examples)
    for forbidden in (
        "EUR/h",
        "EUR/hora",
        "62,5",
        "62.5",
        "| Tarea |",
        "| Task |",
        "| Fase |",
        "| Phase |",
        "|---|",
        "### Totales",
        "### Totals",
        "cost_eur",
        "total_cost",
    ):
        assert forbidden not in system, forbidden


# --- Requirement: Instrucciones de la salida estructurada ---


def test_narrative_format_asks_for_prose_without_figures_and_no_phases_table_block():
    """cag-prompt / Scenario: Narrativa sin cifras."""
    system = system_for(output_format="narrative")
    assert FORMAT_BLOCKS["narrative"] in system
    assert "entre 2 y 4 párrafos de prosa" in system and "sin cifras" in system
    assert FORMAT_BLOCKS["phases_table"] not in system


def test_phases_table_format_block_confidence_and_canonical_phases():
    """cag-prompt / Scenario: Tabla por fases."""
    system = system_for(output_format="phases_table")
    assert FORMAT_BLOCKS["phases_table"] in system
    assert "confidence_pct" in system
    for key in PHASE_KEYS:
        assert f"`{key}`" in system
    assert "Deja `narrative` vacío" in system
    assert FORMAT_BLOCKS["narrative"] not in system


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_only_the_requested_format_block_is_included(fmt):
    """cag-prompt (solo el bloque del formato pedido)."""
    system = system_for(output_format=fmt.value)
    for other, block in FORMAT_BLOCKS.items():
        assert (block in system) is (other == fmt.value)


@pytest.mark.parametrize(("project_type", "level", "fmt"), COMBINATIONS)
def test_out_of_scope_block_is_always_present(project_type, level, fmt):
    """cag-prompt / Scenario: Bloque de fuera de alcance."""
    system = system_for(
        project_type=project_type.value, detail_level=level.value, output_format=fmt.value
    )
    assert OUT_OF_SCOPE in system
    assert "no inventes detalles" in system


def test_field_instructions_explain_every_field_and_rate_profiles():
    """cag-prompt (qué significa cada campo, perfil design, horas múltiplos de 5)."""
    system = system_for()
    for field in (
        "`language`",
        "`title`",
        "`summary`",
        "`confidence_pct`",
        "`rows`",
        "`phases`",
        "`team`",
        "`duration_weeks`",
        "`assumptions_and_risks`",
    ):
        assert field in system
    assert '`rate_profile` es `"design"` solo para el trabajo de diseño UX/UI' in system
    assert "múltiplo de 5" in system


# --- Requirement: Nivel de detalle ---


def test_detailed_level_asks_for_assumptions_per_phase():
    """cag-prompt / Scenario: Detallado."""
    for fmt in OutputFormat:
        assert PER_PHASE_ASSUMPTIONS in system_for(detail_level="detailed", output_format=fmt.value)


@pytest.mark.parametrize("level", ["summary", "medium"])
def test_summary_and_medium_levels_do_not_ask_for_assumptions_per_phase(level):
    """cag-prompt / Scenario: Resumen."""
    for fmt in OutputFormat:
        assert PER_PHASE_ASSUMPTIONS not in system_for(detail_level=level, output_format=fmt.value)


def test_detail_blocks_differ_by_level():
    """cag-prompt / Requirement: Nivel de detalle (cada nivel con su bloque)."""
    summary = system_for(detail_level="summary")
    medium = system_for(detail_level="medium")
    detailed = system_for(detail_level="detailed")
    assert "Agrupa el trabajo en 4 a 6 filas agregadas" in summary
    assert "1 o 2 elementos en `assumptions_and_risks`" in summary
    assert "Una fila por tarea y de 2 a 4 elementos" in medium
    assert "Desglosa cada tarea en subtareas" in detailed
    assert "1 a 3 supuestos" in detailed
    assert "Agrupa el trabajo" not in medium and "Agrupa el trabajo" not in detailed


@pytest.mark.parametrize("project_type", list(ProjectType))
def test_only_the_hint_of_the_requested_project_type_is_included(project_type):
    """cag-prompt (pista del tipo de proyecto)."""
    system = system_for(project_type=project_type.value)
    for other, hint in HINTS.items():
        assert (hint in system) is (other == project_type.value)


# --- Requirement: Ejemplos como objetos ---


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_example_outputs_are_valid_json_without_amounts(fmt):
    """cag-prompt / Scenario: Ejemplos válidos en el prompt."""
    outputs = example_outputs(system_for(output_format=fmt.value))
    assert len(outputs) == 3
    for raw in outputs:
        data = json.loads(raw)
        assert EstimationResult.model_validate(data).in_scope
        assert not {"cost_eur", "total_cost_eur", "rate_eur_per_hour", "total_hours"} & set(data)
        assert "\\u" not in raw  # accents are not escaped


def test_narrative_field_only_appears_in_the_narrative_format_examples():
    """cag-prompt / Scenario: Narrativa solo en su formato."""
    for fmt in OutputFormat:
        for raw in example_outputs(system_for(output_format=fmt.value)):
            assert ("narrative" in json.loads(raw)) is (fmt is OutputFormat.NARRATIVE)


def test_examples_do_not_use_the_reserved_project_description_tag():
    """cag-prompt (los ejemplos no usan <project_description>)."""
    block = examples_block(system_for())
    assert "<project_description>" not in block
    assert block.count("<example>") == 3
    assert block.count("<example_request>") == 3 and block.count("<client_request>") == 3


@pytest.mark.parametrize("count", [1, 2, 3])
def test_selected_examples_are_the_first_n_in_order(count):
    """cag-prompt / Requirement: Ejemplos como objetos (los num_examples primeros)."""
    block = examples_block(system_for(num_examples=count))
    for index, example in enumerate(ESTIMATION_EXAMPLES):
        assert (example["meeting_summary"] in block) is (index < count)
    assert block.count("<example>") == count


@pytest.mark.parametrize(("project_type", "level", "fmt"), COMBINATIONS)
def test_no_examples_omits_the_block_and_the_how_to_section_but_keeps_the_language_rule(
    project_type, level, fmt
):
    """cag-prompt / Scenario: Sin ejemplos."""
    system = system_for(
        num_examples=0,
        project_type=project_type.value,
        detail_level=level.value,
        output_format=fmt.value,
    )
    assert "<examples>" not in system
    assert "## Cómo usar las estimaciones de referencia" not in system
    assert system.startswith(LANGUAGE_RULE_OPENING)
    assert LANGUAGE_RULE_CLOSING in system
    assert system.splitlines()[-1].startswith(DETECTED["en"])
