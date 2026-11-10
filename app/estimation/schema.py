"""Structured estimation schema: what the LLM fills (design D2).

Only judgement goes here: tasks, phases, rate profiles, hours, confidences, team,
duration, assumptions and prose. Rates, costs, totals, labels and Markdown are computed
by the code (`compute.py`, `render.py`), so the schema carries no amounts at all.

Fields are declared in generation order (an autoregressive model writes them top to
bottom): `language` first, so the model commits to a language before any free text;
`summary` and `confidence_pct` before the breakdown, so a non-estimable project stops
early; `rows` before `phases`, so phase confidence is decided after seeing its tasks;
`narrative` last, as a summary of what is already structured.

Three enforcement levels (D2): normalizers (no retry, idempotent so a cached value can
be validated again), field constraints and coherence validators (Pydantic fails and
Instructor asks the model again). Field descriptions travel to the model inside the tool
definition, so they are written as instructions.

This module depends only on Pydantic, `app.domain` and `app.context`: never on
Instructor, LiteLLM, `app.services` or `app.prompts`.
"""

from functools import cache
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.context.examples import PhaseKey
from app.domain import DetailLevel, Language, OutputFormat

__all__ = [
    "OUT_OF_SCOPE_CONFIDENCE",
    "DurationRange",
    "EstimateRow",
    "MAX_TECHNOLOGIES",
    "EstimationResult",
    "PhaseAssessment",
    "PhaseKey",
    "RateProfile",
    "TeamMember",
    "response_model_for",
]

RateProfile = Literal["dev", "design"]

# Overall confidence below this value means the project cannot be estimated (D3).
OUT_OF_SCOPE_CONFIDENCE = 30

# Granularity of hours and confidences; the model is told to use it and the
# normalizers round to it instead of failing (a retry is a full paid call).
ROUNDING_STEP = 5

MAX_ROWS = 60
MAX_PHASES = 5
MAX_PHASE_ASSUMPTIONS = 3
MAX_TEAM_MEMBERS = 8
MAX_ASSUMPTIONS = 8
MAX_NARRATIVE_PARAGRAPHS = 6
MAX_TECHNOLOGIES = 15
MAX_TECH_NAME_CHARS = 60


# Trimmed, non-empty strings of bounded length.
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
TaskText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RoleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ShortItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Paragraph = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
TechName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TECH_NAME_CHARS)
]


def _round_to_step(value: int) -> int:
    return ROUNDING_STEP * round(value / ROUNDING_STEP)


def _clean_technologies(value: object) -> object:
    """Trim, drop blank and over-long names, dedupe by `casefold` keeping the first
    spelling, and keep the first `MAX_TECHNOLOGIES` (normalizer, no retry, idempotent).

    A secondary field never justifies a paid retry (S05 design D4); non-string items are
    left for the field validation to report.
    """
    if not isinstance(value, list):
        return value
    seen: set[str] = set()
    names: list[object] = []
    for item in value:
        if isinstance(item, str):
            item = item.strip()
            if not item or len(item) > MAX_TECH_NAME_CHARS:
                continue
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
        names.append(item)
    return names[:MAX_TECHNOLOGIES]


def _drop_blank(value: object) -> object:
    """Remove blank strings from a list before item validation (normalizer, no retry)."""
    if isinstance(value, list):
        return [item for item in value if not (isinstance(item, str) and not item.strip())]
    return value


class EstimateRow(BaseModel):
    """One line of work of the estimate."""

    task: TaskText = Field(
        description="Short description of the task, with a brief detail in parentheses "
        "if it helps.",
    )
    phase: PhaseKey = Field(description="Canonical phase the task belongs to.")
    rate_profile: RateProfile = Field(
        description='"design" only for UX/UI design work; "dev" for development, QA, '
        "deployment and management.",
    )
    hours: int = Field(ge=1, le=2000, description="Estimated hours, a multiple of 5.")

    @field_validator("hours", mode="after")
    @classmethod
    def _normalize_hours(cls, value: int) -> int:
        return max(ROUNDING_STEP, _round_to_step(value))


class PhaseAssessment(BaseModel):
    """Confidence (and, at detailed level, assumptions) of one phase used in `rows`."""

    phase: PhaseKey = Field(description="Canonical phase; one entry per phase used in rows.")
    confidence_pct: int = Field(
        ge=0,
        le=100,
        description="Probability (0-100, multiple of 5) of finishing the phase within its hours.",
    )
    assumptions: list[ShortItem] = Field(
        default=[],
        max_length=MAX_PHASE_ASSUMPTIONS,
        description="Assumptions of this phase; only when the detailed level is requested.",
    )

    @field_validator("confidence_pct", mode="after")
    @classmethod
    def _normalize_confidence(cls, value: int) -> int:
        return _round_to_step(value)

    @field_validator("assumptions", mode="before")
    @classmethod
    def _drop_blank_assumptions(cls, value: object) -> object:
        return _drop_blank(value)


class TeamMember(BaseModel):
    """A profile of the recommended team."""

    role: RoleText = Field(
        description='Profile, written to read after the count (e.g. "full-stack developers").',
    )
    count: int = Field(ge=1, le=20, description="Number of people with this profile.")
    part_time: bool = Field(default=False, description="True if the dedication is part-time.")


class DurationRange(BaseModel):
    """Estimated calendar duration, as a range of weeks."""

    min_weeks: int = Field(ge=1, le=104)
    max_weeks: int = Field(ge=1, le=104)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.min_weeks > self.max_weeks:
            raise ValueError("min_weeks must be less than or equal to max_weeks")
        return self


class EstimationResult(BaseModel):
    """Software project estimation. Costs and totals are computed from the hours."""

    # Per-request requirements, read by `_check_coherence`. The base model requires
    # nothing conditional; `response_model_for` returns subclasses that do (D2).
    require_narrative: ClassVar[bool] = False
    require_phase_assumptions: ClassVar[bool] = False

    language: Language = Field(
        description="Language of the project description; every text field is written in it."
    )
    title: Title = Field(description="Project name.")
    summary: Summary = Field(
        description="1-2 sentences with the estimated scope; when the project cannot be "
        "estimated, the reason.",
    )
    confidence_pct: int = Field(
        ge=0,
        le=100,
        description="Overall confidence (0-100, multiple of 5); below "
        f"{OUT_OF_SCOPE_CONFIDENCE} means the project cannot be estimated.",
    )
    # After `confidence_pct` (the early out-of-scope decision) and before `rows`, so the
    # breakdown follows the stack the client named (S05 design D4).
    technologies: list[TechName] = Field(
        default=[],
        max_length=MAX_TECHNOLOGIES,
        description="Technologies explicitly named by the client (languages, frameworks, "
        "platforms, cloud and third-party services) in the description, conversation or "
        "attachments; empty if none. Do not add your own recommendations.",
    )
    rows: list[EstimateRow] = Field(
        max_length=MAX_ROWS, description="Work breakdown; empty when it cannot be estimated."
    )
    phases: list[PhaseAssessment] = Field(
        max_length=MAX_PHASES,
        description="Exactly one entry per phase used in rows.",
    )
    team: list[TeamMember] = Field(max_length=MAX_TEAM_MEMBERS, description="Recommended team.")
    duration_weeks: DurationRange | None = Field(
        description="Duration consistent with the hours and the team; null when it cannot "
        "be estimated."
    )
    assumptions_and_risks: list[ShortItem] = Field(
        max_length=MAX_ASSUMPTIONS,
        description="Short assumptions and risks; when it cannot be estimated, the missing "
        "information.",
    )
    narrative: list[Paragraph] = Field(
        default=[],
        max_length=MAX_NARRATIVE_PARAGRAPHS,
        description="Prose paragraphs without figures; only for the narrative format.",
    )

    @field_validator("confidence_pct", mode="after")
    @classmethod
    def _normalize_confidence(cls, value: int) -> int:
        return _round_to_step(value)

    @field_validator("technologies", mode="before")
    @classmethod
    def _normalize_technologies(cls, value: object) -> object:
        return _clean_technologies(value)

    @field_validator("assumptions_and_risks", "narrative", mode="before")
    @classmethod
    def _drop_blank_items(cls, value: object) -> object:
        return _drop_blank(value)

    @property
    def in_scope(self) -> bool:
        return self.confidence_pct >= OUT_OF_SCOPE_CONFIDENCE

    @model_validator(mode="after")
    def _check_coherence(self) -> Self:
        # Messages go back to the model on a retry: actionable, English, no user values.
        assessed = [phase.phase for phase in self.phases]
        if len(assessed) != len(set(assessed)):
            raise ValueError("phases must not repeat a phase")
        used = {row.phase for row in self.rows}
        if set(assessed) != used:
            raise ValueError("phases must have exactly one entry per phase used in rows")
        if not self.in_scope:
            return self
        missing = [
            name
            for name, empty in (
                ("rows", not self.rows),
                ("team", not self.team),
                ("assumptions_and_risks", not self.assumptions_and_risks),
                ("duration_weeks", self.duration_weeks is None),
            )
            if empty
        ]
        if missing:
            raise ValueError(
                f"with confidence_pct >= {OUT_OF_SCOPE_CONFIDENCE} these fields must not be "
                f"empty: {', '.join(missing)}"
            )
        if self.require_narrative and not self.narrative:
            raise ValueError(
                f"narrative must have 1 to {MAX_NARRATIVE_PARAGRAPHS} paragraphs for the "
                "narrative format"
            )
        if self.require_phase_assumptions and any(not p.assumptions for p in self.phases):
            raise ValueError(
                f"each phase must have 1 to {MAX_PHASE_ASSUMPTIONS} assumptions at the "
                "detailed level"
            )
        return self


def response_model_for(
    output_format: OutputFormat | str, detail_level: DetailLevel | str
) -> type[EstimationResult]:
    """Subclass of `EstimationResult` with the requirements of a format and a level.

    Cached per requirement pair, so the 9 combinations share at most 4 classes. Every
    subclass keeps the name, docstring and fields of the base, so the JSON Schema (and the
    tool name) is identical: only the `ClassVar`s that the coherence validator reads
    change (D2). Raise `ValueError` for an unknown format or level.
    """
    return _subclass(
        OutputFormat(output_format) is OutputFormat.NARRATIVE,
        DetailLevel(detail_level) is DetailLevel.DETAILED,
    )


@cache
def _subclass(narrative: bool, detailed: bool) -> type[EstimationResult]:
    return type(
        EstimationResult.__name__,
        (EstimationResult,),
        {
            "__module__": EstimationResult.__module__,
            "__qualname__": EstimationResult.__qualname__,
            "__doc__": EstimationResult.__doc__,
            "require_narrative": narrative,
            "require_phase_assumptions": detailed,
        },
    )
