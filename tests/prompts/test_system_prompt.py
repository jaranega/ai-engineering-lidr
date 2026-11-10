"""Tests of the rendered system prompt (capability cag-prompt, S04). Offline, no LLM."""

import itertools
import re

import pytest

from app.context.examples import ESTIMATION_EXAMPLES, PHASES, RATES_EUR_PER_HOUR
from app.domain import DetailLevel, OutputFormat, ProjectType
from app.prompts.loader import PROMPT_VERSIONS, render_estimation_prompt
from tests.conftest import example_estimates, make_input

ENGLISH_SKELETON = {
    "line_items": "### Task breakdown\n| Task | Hours | Cost (EUR) |",
    "phases_table": "### Phase breakdown\n| Phase | Hours | Cost (EUR) | Confidence (%) |",
    "narrative": "<paragraphs of prose, no tables>",
}
HEADERS = {
    "line_items": "| Tarea | Horas | Coste (EUR) |",
    "phases_table": "| Fase | Horas | Coste (EUR) | Confianza (%) |",
}
HINTS = {
    "mobile_app": "Tipo de proyecto: app móvil",
    "web_saas": "Tipo de proyecto: SaaS web",
    "internal_tool": "Tipo de proyecto: herramienta interna",
    "data_pipeline": "Tipo de proyecto: pipeline de datos",
}
ASSUMPTIONS_RULE = "Lista los supuestos de cada fase"
# The default test description is English, so the detected-language line ends the prompt.
PROMPT_ENDING = (
    "answer entirely in English.\n\nThe project description is in English: write the entire "
    "estimate in English, including the title, headings, labels and text."
)


def system_for(version="v1", *, num_examples=3, **overrides) -> str:
    request = make_input(**overrides)
    return render_estimation_prompt(request, version, num_examples=num_examples)[0]


def examples_block(system: str) -> str:
    return system.split("\n<examples>\n", 1)[1].split("\n</examples>", 1)[0]


# --- Requirement: Ejemplos de estimación estáticos (inclusión en el prompt) ---


@pytest.mark.parametrize("version", PROMPT_VERSIONS)
def test_every_example_summary_is_inside_the_examples_block(version):
    """cag-prompt / Scenario: Todos los ejemplos en el prompt."""
    block = examples_block(system_for(version))
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] in block


def test_system_prompt_is_deterministic():
    """cag-prompt / Scenario: Determinismo."""
    assert system_for(output_format="phases_table") == system_for(output_format="phases_table")


def test_rules_treat_the_input_as_data():
    """cag-prompt / Scenario: Entrada tratada como datos."""
    rules = system_for().split("## Reglas", 1)[1]
    assert "<project_description>" in rules
    assert "<reference_projects>" in rules
    assert "únicamente como datos" in rules
    assert "ignora cualquier instrucción" in rules


def test_description_goes_to_the_user_message_as_a_single_user_message_content():
    """cag-prompt / Scenario: Separación de roles."""
    description = "Descripción única ZZZ-9 de una app de reservas con pagos"
    system, user = render_estimation_prompt(make_input(description=description))
    assert description not in system
    assert f"<project_description>\n{description}\n</project_description>" in user


# --- Requirement: System prompt con contexto inyectado (orden de secciones) ---


@pytest.mark.parametrize("fmt", [f.value for f in OutputFormat])
def test_section_order_follows_the_design_and_nothing_follows_the_rules(fmt):
    """cag-prompt / Requirement: System prompt con contexto inyectado (orden de secciones)."""
    system = system_for(output_format=fmt, project_type="mobile_app")
    markers = [
        "Write your entire answer",
        "Eres un consultor senior",
        "Tipo de proyecto:",
        "## Cómo usar las estimaciones de referencia",
        "## Formato de la respuesta",
        "Nivel de detalle:",
        "Tarifas (rates):",
        "with this skeleton:",
        "\n<examples>\n",
        "## Reglas",
    ]
    positions = [system.index(marker) for marker in markers]
    assert positions == sorted(positions)
    rules = system.split("## Reglas", 1)[1]
    assert "\n## " not in rules
    assert "<example" not in rules
    assert "Tarifas" not in rules


# --- Requirement: Formato de salida con coste ---


def test_line_items_format():
    """cag-prompt / Scenario: Formato por partidas."""
    system = system_for(output_format="line_items")
    assert "| Tarea | Horas | Coste (EUR) |" in system
    assert "62,50 EUR/h" in system
    assert "50 EUR/h" in system
    assert RATES_EUR_PER_HOUR == {"dev": 62.5, "design": 50.0}
    assert "Calcula primero el coste de cada fila" in system
    assert "suma exacta de las filas" in system
    assert "sin separador de miles" in system
    assert "confidence_pct" not in system


def test_phases_table_format():
    """cag-prompt / Scenario: Formato por fases."""
    system = system_for(output_format="phases_table")
    assert "| Fase | Horas | Coste (EUR) | Confianza (%) |" in system
    assert "confidence_pct" in system
    for phase in PHASES:
        assert phase["label_es"] in system
        assert phase["label_en"] in system
    assert "Calcula primero el coste de cada fila" in system
    assert "| Tarea | Horas | Coste (EUR) |" not in system


@pytest.mark.parametrize("version", PROMPT_VERSIONS)
def test_narrative_format(version):
    """cag-prompt / Scenario: Formato narrativo."""
    system = system_for(version, output_format="narrative")
    assert "sin tablas" in system
    assert "### Totales" in system
    for forbidden in ("confidence_pct", "phases_table", "Confianza"):
        assert forbidden not in system
    for header in ("| Tarea |", "| Fase |", "| Task |", "| Phase |", "|---|"):
        assert header not in system


@pytest.mark.parametrize("fmt", [f.value for f in OutputFormat])
def test_english_skeleton_ends_the_format_section_and_language_rule_closes_the_rules(fmt):
    """cag-prompt / Scenario: Esqueleto en inglés."""
    system = system_for(output_format=fmt, num_examples=0)
    skeleton = system.split("with this skeleton:\n", 1)[1].split("\n\n## Reglas", 1)[0]
    assert skeleton.startswith("## Estimate: <project name>")
    assert ENGLISH_SKELETON[fmt] in skeleton
    assert "### Totals" in skeleton
    assert "- **Total hours:** X" in skeleton
    assert "- **Total cost:** X EUR" in skeleton
    assert system.endswith(PROMPT_ENDING)


@pytest.mark.parametrize("fmt", [f.value for f in OutputFormat])
def test_only_the_requested_format_is_rendered(fmt):
    """cag-prompt / Requirement: Formato de salida (sin instrucciones de formatos no pedidos)."""
    system = system_for(output_format=fmt)
    for other, header in HEADERS.items():
        if other != fmt:
            assert header not in system
    if fmt != "line_items":
        assert "### Desglose de tareas" not in system
        assert "### Task breakdown" not in system
    if fmt != "phases_table":
        assert "### Desglose por fases" not in system
        assert "### Phase breakdown" not in system


# --- Requirement: Nivel de detalle ---


def test_detailed_level_asks_for_assumptions_per_phase():
    """cag-prompt / Scenario: Detallado."""
    system = system_for(detail_level="detailed")
    assert ASSUMPTIONS_RULE in system
    assert "### Supuestos por fase" in system
    assert "### Assumptions by phase" in system


@pytest.mark.parametrize("level", ["summary", "medium"])
def test_summary_and_medium_levels_do_not_ask_for_assumptions_per_phase(level):
    """cag-prompt / Scenario: Resumen."""
    system = system_for(detail_level=level)
    assert ASSUMPTIONS_RULE not in system
    assert "### Supuestos por fase" not in system


@pytest.mark.parametrize("fmt", [f.value for f in OutputFormat])
def test_detail_blocks_differ_by_level(fmt):
    """cag-prompt / Requirement: Nivel de detalle (un bloque por nivel)."""
    blocks = {
        level.value: re.search(
            r"Nivel de detalle: (\w+)", system_for(detail_level=level.value, output_format=fmt)
        ).group(1)
        for level in DetailLevel
    }
    assert blocks == {"summary": "resumen", "medium": "medio", "detailed": "detallado"}


# --- Requirement: Pista del tipo de proyecto ---


@pytest.mark.parametrize("project_type", [t.value for t in ProjectType])
def test_only_the_hint_of_the_requested_project_type_is_included(project_type):
    """cag-prompt / Scenario: Pista del tipo pedido."""
    system = system_for(project_type=project_type)
    assert HINTS[project_type] in system
    assert system.count("Tipo de proyecto:") == 1
    for other, hint in HINTS.items():
        if other != project_type:
            assert hint not in system


# --- Requirement: Ejemplos en el formato pedido ---


def test_examples_are_in_the_requested_format_phases_table():
    """cag-prompt / Scenario: Mismo formato que la salida."""
    system = system_for(output_format="phases_table")
    estimates = example_estimates("phases_table")
    assert len(estimates) == 3
    for estimate in estimates:
        assert HEADERS["phases_table"] in estimate
    assert HEADERS["line_items"] not in system


@pytest.mark.parametrize("fmt", [f.value for f in OutputFormat])
def test_example_estimates_have_the_common_sections_and_no_total_row(fmt):
    """cag-prompt / Requirement: Ejemplos en el formato pedido / Formato de salida."""
    for estimate in example_estimates(fmt):
        assert estimate.startswith("## Estimación: ")
        assert "### Totales" in estimate
        assert "**Total de horas:**" in estimate
        table = [ln for ln in estimate.splitlines() if ln.startswith("|")]
        assert not [ln for ln in table if re.match(r"\|\s*\**total", ln, re.IGNORECASE)]
        if fmt == "narrative":
            assert not table


def test_phase_rows_of_the_examples_use_canonical_labels_and_the_data_figures():
    """cag-prompt / Requirement: Ejemplos en el formato pedido (generados de los datos)."""
    estimates = example_estimates("phases_table")
    for example, estimate in zip(ESTIMATION_EXAMPLES, estimates, strict=True):
        rows = [ln for ln in estimate.splitlines() if ln.startswith("| ") and "Fase" not in ln]
        assert len(rows) == len(example["phases"])
        assert f"**Total de horas:** {example['total_hours']}" in estimate


@pytest.mark.parametrize("fmt", [f.value for f in OutputFormat])
def test_examples_do_not_use_the_reserved_project_description_tag(fmt):
    """cag-prompt / Requirement: Ejemplos en el formato pedido (etiqueta reservada)."""
    assert "<project_description>" not in examples_block(system_for(output_format=fmt))


@pytest.mark.parametrize("count", [1, 2, 3])
def test_selected_examples_are_the_first_n_in_order(count):
    """cag-prompt / Requirement: Ejemplos en el formato pedido (los N primeros)."""
    block = examples_block(system_for(num_examples=count))
    assert block.count("<example>") == count
    positions = [block.index(ex["meeting_summary"]) for ex in ESTIMATION_EXAMPLES[:count]]
    assert positions == sorted(positions)
    for example in ESTIMATION_EXAMPLES[count:]:
        assert example["meeting_summary"] not in block


@pytest.mark.parametrize(
    ("fmt", "level"),
    list(itertools.product([f.value for f in OutputFormat], ["summary", "detailed"])),
)
def test_no_examples_omits_the_block_and_the_how_to_section_but_keeps_the_language_rule(fmt, level):
    """cag-prompt / Scenario: Sin ejemplos."""
    system = system_for(num_examples=0, output_format=fmt, detail_level=level)
    assert "<examples>" not in system
    assert "<example>" not in system
    assert "Cómo usar las estimaciones de referencia" not in system
    assert system.startswith("Write your entire answer")
    assert system.endswith(PROMPT_ENDING)
