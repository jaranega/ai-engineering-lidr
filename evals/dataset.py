"""Typed golden cases and their JSON loader (design s05-acb D12).

A `GoldenCase` is the input of one session turn plus the envelope its answer must fall
in. The numeric expectations are generous ranges (about +/-50 % around expert judgement):
the goal is to catch catastrophic regressions (out of budget, wrong tier, a missed
block), not to pin every figure. `app` never imports this package.
"""

import json
import re
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.domain import DetailLevel, Language, OutputFormat, ProjectType
from app.sessions import Tier

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "golden_dataset.json"

CASE_ID_PATTERN = re.compile(r"^case-\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*$")
MIN_TRANSCRIPT_CHARS = 20
MAX_TRANSCRIPT_CHARS = 20_000

IntRange = tuple[int, int]
FloatRange = tuple[float, float]

_RANGE_FIELDS = (
    "expected_hours_range",
    "expected_cost_range_eur",
    "expected_duration_weeks_range",
    "expected_phase_count_range",
)


class DatasetError(ValueError):
    """The golden dataset cannot be loaded; the message names the offending case."""


class GoldenCase(BaseModel):
    """One golden case: the turn to send and what its answer must satisfy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    language: Language
    transcript: str = Field(min_length=MIN_TRANSCRIPT_CHARS, max_length=MAX_TRANSCRIPT_CHARS)
    project_type: ProjectType
    detail_level: DetailLevel = DetailLevel.MEDIUM
    output_format: OutputFormat = OutputFormat.LINE_ITEMS
    # `auto` sends no tier (the rules resolve it); a `Tier` is sent as an explicit tier.
    tier: Literal["auto"] | Tier = "auto"

    expected_blocked: bool = False
    expected_out_of_scope: bool = False
    expected_in_summary: list[str] = Field(default_factory=list)
    expected_technologies_any_of: list[str] = Field(default_factory=list)
    expected_hours_range: IntRange | None = None
    expected_cost_range_eur: FloatRange | None = None
    expected_duration_weeks_range: IntRange | None = None
    expected_phase_count_range: IntRange | None = None
    expected_tier: Tier | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not CASE_ID_PATTERN.fullmatch(value):
            raise ValueError("id must match 'case-NNN-slug' (lowercase, digits and hyphens)")
        return value

    @field_validator(*_RANGE_FIELDS)
    @classmethod
    def _check_range(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is not None:
            lo, hi = value
            if lo < 0:
                raise ValueError(f"range [{lo}, {hi}] must not be negative")
            if lo > hi:
                raise ValueError(f"range [{lo}, {hi}] has lo > hi")
        return value

    @model_validator(mode="after")
    def _check_expectations(self) -> Self:
        if self.expected_blocked or self.expected_out_of_scope:
            with_ranges = [name for name in _RANGE_FIELDS if getattr(self, name) is not None]
            if with_ranges:
                kind = "blocked" if self.expected_blocked else "out-of-scope"
                raise ValueError(f"a {kind} case must not carry ranges: {', '.join(with_ranges)}")
        return self


def _case_label(entry: object, index: int) -> str:
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return f"case '{entry['id']}' (#{index})"
    return f"case #{index}"


def load_dataset(path: Path | str | None = None) -> list[GoldenCase]:
    """Load and validate the golden dataset (`golden_dataset.json` by default).

    Raises `DatasetError` naming the case when one does not validate or an id repeats,
    and when the file is missing, is not JSON or its root is not a list.
    """
    target = Path(path) if path is not None else DEFAULT_DATASET_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DatasetError(f"cannot read dataset {target}: {exc.strerror or exc}") from None
    except json.JSONDecodeError as exc:
        raise DatasetError(f"dataset {target} is not valid JSON: {exc}") from None
    if not isinstance(raw, list):
        raise DatasetError(f"dataset {target} must be a JSON list of cases")

    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        try:
            case = GoldenCase.model_validate(entry)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'case'}: {error['msg']}"
                for error in exc.errors()
            )
            raise DatasetError(f"{_case_label(entry, index)} is invalid: {problems}") from None
        if case.id in seen:
            raise DatasetError(f"duplicate case id '{case.id}' (#{index})")
        seen.add(case.id)
        cases.append(case)
    return cases
