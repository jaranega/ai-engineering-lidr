"""Domain vocabulary and typed estimation input.

This module depends only on pydantic and the standard library. It exists so that the
prompt loader and the services can use the typed input (enums, limits, reference projects)
without importing the HTTP schemas, which avoids an import cycle and keeps the services
independent of the HTTP contract. ``app.schemas`` extends ``EstimationInput`` and
re-exports the enums.
"""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MIN_DESCRIPTION_CHARS = 20
MAX_DESCRIPTION_CHARS = 2000
MAX_REFERENCE_PROJECTS = 5

MIN_REFERENCE_NAME_CHARS = 1
MAX_REFERENCE_NAME_CHARS = 100
MIN_REFERENCE_SUMMARY_CHARS = 1
MAX_REFERENCE_SUMMARY_CHARS = 500
MIN_REFERENCE_HOURS = 1
MAX_REFERENCE_HOURS = 20000
MIN_REFERENCE_WEEKS = 1
MAX_REFERENCE_WEEKS = 104


class ProjectType(str, Enum):  # noqa: UP042
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):  # noqa: UP042
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):  # noqa: UP042
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class ReferenceProject(BaseModel):
    """A past project the estimate can be calibrated against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=MIN_REFERENCE_NAME_CHARS,
            max_length=MAX_REFERENCE_NAME_CHARS,
        ),
    ] = Field(description="Short name of the reference project.", examples=["Clinic booking app"])
    summary: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=MIN_REFERENCE_SUMMARY_CHARS,
            max_length=MAX_REFERENCE_SUMMARY_CHARS,
        ),
    ] = Field(
        description="Brief summary of what the reference project delivered.",
        examples=["Web platform for online appointment booking with payments."],
    )
    actual_hours: int | None = Field(
        default=None,
        ge=MIN_REFERENCE_HOURS,
        le=MAX_REFERENCE_HOURS,
        description="Total hours the reference project actually took.",
        examples=[640],
    )
    duration_weeks: int | None = Field(
        default=None,
        ge=MIN_REFERENCE_WEEKS,
        le=MAX_REFERENCE_WEEKS,
        description="Actual duration of the reference project in weeks.",
        examples=[12],
    )


class EstimationInput(BaseModel):
    """Typed description of the project to estimate."""

    model_config = ConfigDict(frozen=True)

    description: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=MIN_DESCRIPTION_CHARS,
            max_length=MAX_DESCRIPTION_CHARS,
        ),
    ] = Field(
        description=(
            f"Project description ({MIN_DESCRIPTION_CHARS}-{MAX_DESCRIPTION_CHARS} characters "
            "after trimming whitespace)."
        ),
        examples=["A mobile app for a gym chain to book classes and manage memberships."],
    )
    project_type: ProjectType = Field(
        description="Kind of project, used to tailor the estimation approach.",
        examples=["mobile_app"],
    )
    detail_level: DetailLevel = Field(
        description="How detailed the estimate should be.",
        examples=["medium"],
    )
    output_format: OutputFormat = Field(
        description="Shape of the estimate: phases table, line items or narrative.",
        examples=["line_items"],
    )
    reference_projects: list[ReferenceProject] | None = Field(
        default=None,
        max_length=MAX_REFERENCE_PROJECTS,
        description=(
            f"Optional past projects (at most {MAX_REFERENCE_PROJECTS}) to calibrate against. "
            "An empty list is treated as absent."
        ),
    )

    @field_validator("reference_projects")
    @classmethod
    def _empty_to_none(cls, value: list[ReferenceProject] | None) -> list[ReferenceProject] | None:
        return value or None
