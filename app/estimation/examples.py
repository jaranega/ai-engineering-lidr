"""CAG examples as structured objects (design D4).

`app.context.examples` holds the data; here each example becomes an `EstimationResult`
(validated like any LLM answer) and, for the prompt, a JSON payload. The JSON is built in
Python because Jinja's `tojson` escapes accents (`ó` -> `\\u00f3`): more tokens and worse
reading for the model.
"""

import json
from collections.abc import Sequence
from typing import Any, TypedDict

from app.context.examples import PHASE_KEYS, EstimationExample
from app.domain import OutputFormat
from app.estimation.schema import (
    DurationRange,
    EstimateRow,
    EstimationResult,
    PhaseAssessment,
    TeamMember,
)


class ExamplePayload(TypedDict):
    project_type: str
    meeting_summary: str
    output_json: str  # the example's `EstimationResult`, compact JSON


def example_result(
    example: EstimationExample, *, phase_assumptions: bool = False
) -> EstimationResult:
    """The example as the object the model is expected to return (in Spanish).

    `technologies` are always loaded. Per-phase assumptions only with
    `phase_assumptions=True` (the `detailed` level in prompt v4, S05 design D5): the data
    are `medium` granularity, so by default the phases carry none.
    """
    used = {row["phase"] for row in example["rows"]}
    min_weeks, max_weeks = example["duration_weeks"]
    return EstimationResult(
        language="es",
        title=example["title"],
        summary=example["summary"],
        confidence_pct=example["confidence_pct"],
        technologies=list(example["technologies"]),
        rows=[EstimateRow(**row) for row in example["rows"]],
        phases=[
            PhaseAssessment(
                phase=key,
                confidence_pct=example["phase_confidence"][key],
                assumptions=list(example["phase_assumptions"][key]) if phase_assumptions else [],
            )
            for key in PHASE_KEYS
            if key in used
        ],
        team=[TeamMember(**member) for member in example["team"]],
        duration_weeks=DurationRange(min_weeks=min_weeks, max_weeks=max_weeks),
        assumptions_and_risks=list(example["assumptions_and_risks"]),
        narrative=list(example["narrative"]),
    )


def example_payloads(
    examples: Sequence[EstimationExample],
    output_format: OutputFormat | str,
    *,
    include_technologies: bool = False,
    include_phase_assumptions: bool = False,
) -> list[ExamplePayload]:
    """Request and expected JSON output of each example, for `examples.j2`.

    Fields equal to their defaults are left out (`part_time: false`, empty per-phase
    assumptions), and `narrative` only appears in the narrative format, so each example
    shows exactly what the model should fill for the requested format.

    Both flags are off for prompt v3, whose text must not change by a byte (S05 design
    D4). Prompt v4 passes `include_technologies=True`: the key is then always present,
    right after `confidence_pct` as in the schema, also when empty (`[]` teaches to leave
    it empty); and `include_phase_assumptions=True` at the `detailed` level (D5).
    """
    exclude: set[str] = {"technologies"}
    if OutputFormat(output_format) is not OutputFormat.NARRATIVE:
        exclude.add("narrative")
    payloads: list[ExamplePayload] = []
    for example in examples:
        result = example_result(example, phase_assumptions=include_phase_assumptions)
        data = result.model_dump(mode="json", exclude_defaults=True, exclude=exclude)
        if include_technologies:
            data = _with_technologies(data, list(result.technologies))
        payloads.append(
            {
                "project_type": example["project_type"],
                "meeting_summary": example["meeting_summary"],
                "output_json": json.dumps(data, ensure_ascii=False),
            }
        )
    return payloads


def _with_technologies(data: dict[str, Any], technologies: list[str]) -> dict[str, Any]:
    """`data` with `technologies` inserted right after `confidence_pct` (schema order)."""
    ordered: dict[str, Any] = {}
    for key, value in data.items():
        ordered[key] = value
        if key == "confidence_pct":
            ordered["technologies"] = technologies
    return ordered
