"""Costs, totals and the out-of-scope output guardrail, computed in code (design D3).

`compute_estimate` is pure apart from one log event: the LLM never provides an amount,
so the arithmetic cannot be wrong. `ComputedEstimate` is also the schema of the `result`
field of the HTTP response (no mirror model in `app.schemas`).
"""

import structlog
from pydantic import BaseModel, Field

from app.context.examples import PHASE_KEYS, RATES_EUR_PER_HOUR
from app.domain import Language
from app.estimation.schema import (
    DurationRange,
    EstimateRow,
    EstimationResult,
    PhaseKey,
    TeamMember,
)

logger = structlog.get_logger(__name__)


class CostedRow(EstimateRow):
    """A row of the estimate with its rate and cost."""

    rate_eur_per_hour: float = Field(description="Rate of the row's profile, EUR per hour.")
    cost_eur: float = Field(description="hours x rate, rounded to 2 decimals.")


class PhaseBreakdown(BaseModel):
    """Aggregation of the rows of one phase."""

    phase: PhaseKey
    hours: int
    cost_eur: float
    confidence_pct: int
    assumptions: list[str]


class ComputedEstimate(BaseModel):
    """The validated estimate with costs and totals computed by the server."""

    language: Language = Field(description="Language of the labels of `text`.")
    title: str
    summary: str
    confidence_pct: int
    technologies: list[str] = Field(
        description="Technologies named by the client, as returned by the model; kept "
        "also when out of scope (they are mentioned facts, not invented amounts)."
    )
    out_of_scope: bool = Field(
        description="True when the overall confidence is too low to estimate; then rows, "
        "phases, team and narrative are empty and the totals are 0."
    )
    rows: list[CostedRow]
    phases: list[PhaseBreakdown] = Field(
        description="Phases with rows, in canonical order (discovery, setup, development, "
        "qa, deployment)."
    )
    team: list[TeamMember]
    duration_weeks: DurationRange | None
    assumptions_and_risks: list[str]
    narrative: list[str]
    total_hours: int
    total_cost_eur: float


def _cost(row: EstimateRow) -> CostedRow:
    rate = RATES_EUR_PER_HOUR[row.rate_profile]
    return CostedRow(
        **row.model_dump(), rate_eur_per_hour=rate, cost_eur=round(row.hours * rate, 2)
    )


def _phases(rows: list[CostedRow], result: EstimationResult) -> list[PhaseBreakdown]:
    assessments = {phase.phase: phase for phase in result.phases}
    breakdown: list[PhaseBreakdown] = []
    for key in PHASE_KEYS:
        phase_rows = [row for row in rows if row.phase == key]
        if not phase_rows:
            continue
        # Validation guarantees one assessment per phase used in rows; a hand-built
        # result without it degrades to confidence 0 instead of failing.
        assessment = assessments.get(key)
        breakdown.append(
            PhaseBreakdown(
                phase=key,
                hours=sum(row.hours for row in phase_rows),
                cost_eur=round(sum(row.cost_eur for row in phase_rows), 2),
                confidence_pct=assessment.confidence_pct if assessment else 0,
                assumptions=list(assessment.assumptions) if assessment else [],
            )
        )
    return breakdown


def compute_estimate(result: EstimationResult, language: Language) -> ComputedEstimate:
    """Cost each row, aggregate the phases in canonical order and add the totals.

    `language` is the effective language of the labels (detected in the description, or
    the model's when detection is ambiguous, D5). Output guardrail (filter policy): with
    `confidence_pct` below `OUT_OF_SCOPE_CONFIDENCE` the estimate is out of scope: rows,
    phases, team and narrative are emptied, the duration is `None` and the totals are 0;
    title, summary (the reason), confidence and assumptions are kept. Never raises.
    """
    common = {
        "language": language,
        "title": result.title,
        "summary": result.summary,
        "confidence_pct": result.confidence_pct,
        "technologies": list(result.technologies),
        "assumptions_and_risks": list(result.assumptions_and_risks),
    }
    if not result.in_scope:
        logger.info("estimation_out_of_scope", confidence_pct=result.confidence_pct)
        return ComputedEstimate(
            **common,
            out_of_scope=True,
            rows=[],
            phases=[],
            team=[],
            duration_weeks=None,
            narrative=[],
            total_hours=0,
            total_cost_eur=0.0,
        )
    rows = [_cost(row) for row in result.rows]
    return ComputedEstimate(
        **common,
        out_of_scope=False,
        rows=rows,
        phases=_phases(rows, result),
        team=list(result.team),
        duration_weeks=result.duration_weeks,
        narrative=list(result.narrative),
        total_hours=sum(row.hours for row in rows),
        total_cost_eur=round(sum(row.cost_eur for row in rows), 2),
    )
