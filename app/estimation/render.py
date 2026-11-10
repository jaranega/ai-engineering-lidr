"""Deterministic Markdown of an estimate, per output format and language (design D4).

The server writes every label, figure and table; the LLM only contributes the free text
(title, tasks, roles, assumptions and narrative paragraphs). The structure of each
format is the one of S04 design D5, so the structural evaluation of S04
(`app.services.evaluation`) keeps scoring it unchanged:

- `line_items`: title, task table, totals, assumptions and risks.
- `phases_table`: title, phase table with confidence, totals, assumptions and risks.
- `narrative`: title, the model's paragraphs, a phases paragraph written by the code
  (the only figures in the prose), the main risks and the totals.
- `detailed` adds the per-phase assumptions at the end.
- Out of scope, in every format: title, a fixed notice with the confidence, the summary
  and the assumptions; no tables nor amounts.

Pure: no I/O, no LLM.
"""

from typing import TypedDict

from app.context.examples import PHASES, PhaseKey
from app.domain import DetailLevel, Language, OutputFormat
from app.estimation.compute import ComputedEstimate, PhaseBreakdown
from app.estimation.schema import DurationRange, TeamMember


class Labels(TypedDict):
    title: str
    task_breakdown: str
    phase_breakdown: str
    task_table_header: str
    phase_table_header: str
    totals: str
    total_hours: str
    total_cost: str
    team: str
    duration: str
    weeks: str
    assumptions_and_risks: str
    phase_assumptions: str
    out_of_scope: str
    out_of_scope_notice: str  # {confidence}
    phases_one: str  # {phases}
    phases_many: str  # {count}, {phases}
    phase_item: str  # {label}, {hours}, {cost}
    main_risks: str
    part_time: str


LABELS: dict[Language, Labels] = {
    "es": {
        "title": "Estimación",
        "task_breakdown": "Desglose de tareas",
        "phase_breakdown": "Desglose por fases",
        "task_table_header": "| Tarea | Horas | Coste (EUR) |\n|---|---|---|",
        "phase_table_header": "| Fase | Horas | Coste (EUR) | Confianza (%) |\n|---|---|---|---|",
        "totals": "Totales",
        "total_hours": "Total de horas",
        "total_cost": "Coste total",
        "team": "Equipo recomendado",
        "duration": "Duración estimada",
        "weeks": "semanas",
        "assumptions_and_risks": "Supuestos y riesgos",
        "phase_assumptions": "Supuestos por fase",
        "out_of_scope": "Fuera de alcance",
        "out_of_scope_notice": (
            "No se puede estimar este proyecto con la información disponible "
            "(confianza {confidence} %)."
        ),
        "phases_one": "El trabajo se organiza en una fase: {phases}.",
        "phases_many": "El trabajo se organiza en {count} fases: {phases}.",
        "phase_item": "{label}, {hours} horas ({cost} EUR)",
        "main_risks": "Riesgos y supuestos principales",
        "part_time": "part-time",
    },
    "en": {
        "title": "Estimate",
        "task_breakdown": "Task breakdown",
        "phase_breakdown": "Phase breakdown",
        "task_table_header": "| Task | Hours | Cost (EUR) |\n|---|---|---|",
        "phase_table_header": "| Phase | Hours | Cost (EUR) | Confidence (%) |\n|---|---|---|---|",
        "totals": "Totals",
        "total_hours": "Total hours",
        "total_cost": "Total cost",
        "team": "Recommended team",
        "duration": "Estimated duration",
        "weeks": "weeks",
        "assumptions_and_risks": "Assumptions and risks",
        "phase_assumptions": "Assumptions by phase",
        "out_of_scope": "Out of scope",
        "out_of_scope_notice": (
            "This project cannot be estimated with the available information "
            "(confidence {confidence}%)."
        ),
        "phases_one": "The work is organized in one phase: {phases}.",
        "phases_many": "The work is organized in {count} phases: {phases}.",
        "phase_item": "{label}, {hours} hours ({cost} EUR)",
        "main_risks": "Main risks and assumptions",
        "part_time": "part-time",
    },
}

_PHASE_LABELS: dict[Language, dict[PhaseKey, str]] = {
    "es": {phase["key"]: phase["label_es"] for phase in PHASES},
    "en": {phase["key"]: phase["label_en"] for phase in PHASES},
}


def format_amount(value: float, language: Language) -> str:
    """Amount without thousands separator; 2 decimals only if not whole.

    Decimal comma in Spanish, point in English: `19500.0` -> `19500`, `937.5` ->
    `937,50` (es) / `937.50` (en).
    """
    if float(value).is_integer():
        return str(int(value))
    text = f"{value:.2f}"
    return text.replace(".", ",") if language == "es" else text


def _team(team: list[TeamMember], labels: Labels) -> str:
    parts = []
    for member in team:
        part = f"{member.count} {member.role}"
        if member.part_time:
            part += f" ({labels['part_time']})"
        parts.append(part)
    return " + ".join(parts)


def _duration(duration: DurationRange | None, labels: Labels) -> str:
    if duration is None:  # unreachable in scope (validated); kept total
        return "-"
    if duration.min_weeks == duration.max_weeks:
        return f"{duration.min_weeks} {labels['weeks']}"
    return f"{duration.min_weeks}-{duration.max_weeks} {labels['weeks']}"


def _totals(estimate: ComputedEstimate, labels: Labels) -> list[str]:
    cost = format_amount(estimate.total_cost_eur, estimate.language)
    return [
        f"### {labels['totals']}",
        f"- **{labels['total_hours']}:** {estimate.total_hours}",
        f"- **{labels['total_cost']}:** {cost} EUR",
        f"- **{labels['team']}:** {_team(estimate.team, labels)}",
        f"- **{labels['duration']}:** {_duration(estimate.duration_weeks, labels)}",
    ]


def _cell(text: str) -> str:
    """Model text inside a table cell: a `|` or a line break would split the row."""
    return " ".join(text.replace("|", "/").split())


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items]


def _phases_sentence(phases: list[PhaseBreakdown], language: Language) -> str:
    labels = LABELS[language]
    items = "; ".join(
        labels["phase_item"].format(
            label=_PHASE_LABELS[language][phase.phase],
            hours=phase.hours,
            cost=format_amount(phase.cost_eur, language),
        )
        for phase in phases
    )
    if len(phases) == 1:
        return labels["phases_one"].format(phases=items)
    return labels["phases_many"].format(count=len(phases), phases=items)


def _phase_assumptions(estimate: ComputedEstimate, labels: Labels) -> list[str]:
    """`### Assumptions by phase` block; empty when no phase has assumptions."""
    phases = [phase for phase in estimate.phases if phase.assumptions]
    if not phases:
        return []
    lines = [f"### {labels['phase_assumptions']}"]
    for phase in phases:
        lines.append(f"- {_PHASE_LABELS[estimate.language][phase.phase]}")
        lines.extend(f"  - {assumption}" for assumption in phase.assumptions)
    return lines


def _join(blocks: list[list[str]]) -> str:
    """Blocks separated by a blank line; lines of a block on consecutive lines."""
    return "\n\n".join("\n".join(block) for block in blocks if block)


def _out_of_scope(estimate: ComputedEstimate, labels: Labels) -> str:
    notice = labels["out_of_scope_notice"].format(confidence=estimate.confidence_pct)
    blocks = [
        [f"## {labels['title']}: {estimate.title}"],
        [f"**{labels['out_of_scope']}:** {notice}"],
        [estimate.summary],
    ]
    if estimate.assumptions_and_risks:
        blocks.append(
            [f"### {labels['assumptions_and_risks']}", *_bullets(estimate.assumptions_and_risks)]
        )
    return _join(blocks)


def render_markdown(
    estimate: ComputedEstimate,
    output_format: OutputFormat | str,
    detail_level: DetailLevel | str,
) -> str:
    """Markdown `text` of the estimate in `estimate.language`, for the requested format.

    Raise `ValueError` for an unknown format or level.
    """
    fmt = OutputFormat(output_format)
    detailed = DetailLevel(detail_level) is DetailLevel.DETAILED
    language = estimate.language
    labels = LABELS[language]
    if estimate.out_of_scope:
        return _out_of_scope(estimate, labels)

    blocks: list[list[str]] = [[f"## {labels['title']}: {estimate.title}"]]
    if fmt is OutputFormat.NARRATIVE:
        blocks.extend([paragraph] for paragraph in estimate.narrative)
        blocks.append([_phases_sentence(estimate.phases, language)])
        risks = " ".join(estimate.assumptions_and_risks)
        if risks:
            blocks.append([f"{labels['main_risks']}: {risks}"])
        blocks.append(_totals(estimate, labels))
    else:
        if fmt is OutputFormat.LINE_ITEMS:
            table = [f"### {labels['task_breakdown']}", labels["task_table_header"]]
            table.extend(
                f"| {_cell(row.task)} | {row.hours} | {format_amount(row.cost_eur, language)} |"
                for row in estimate.rows
            )
        else:
            table = [f"### {labels['phase_breakdown']}", labels["phase_table_header"]]
            table.extend(
                f"| {_PHASE_LABELS[language][phase.phase]} | {phase.hours} | "
                f"{format_amount(phase.cost_eur, language)} | {phase.confidence_pct} |"
                for phase in estimate.phases
            )
        blocks.append(table)
        blocks.append(_totals(estimate, labels))
        blocks.append(
            [f"### {labels['assumptions_and_risks']}", *_bullets(estimate.assumptions_and_risks)]
        )
    if detailed:
        blocks.append(_phase_assumptions(estimate, labels))
    return _join(blocks)
