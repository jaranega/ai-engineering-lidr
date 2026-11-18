"""Deterministic metrics over a golden case and the outcome of its turn (design D12).

No LLM and no network: each metric tells exactly why a case failed. Every metric has a
`name` and `evaluate(case, outcome) -> MetricResult` with a binary score (1.0 or 0.0). A
metric that does not apply is "skipped": it passes with score 1.0 and `details` starting
with `skipped`. `details` never quote the transcript nor the free text of the answer.
"""

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.schemas import SessionEstimateResponse
from evals.dataset import GoldenCase


@dataclass(frozen=True)
class MetricResult:
    name: str
    score: float
    passed: bool
    details: str


@dataclass(frozen=True)
class CaseOutcome:
    """What the turn produced: a guardrail block (`blocked_reason`, the `reason` of the
    400) or a validated response; both `None` means the turn produced nothing usable.
    """

    blocked_reason: str | None = None
    response: SessionEstimateResponse | None = None


class Metric(Protocol):
    name: str

    def evaluate(self, case: GoldenCase, outcome: CaseOutcome) -> MetricResult: ...


def normalize(text: str) -> str:
    """Case- and accent-insensitive form of `text` ("Línea" -> "linea")."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold()


def _result(name: str, problems: list[str], ok_details: str) -> MetricResult:
    passed = not problems
    return MetricResult(
        name=name,
        score=1.0 if passed else 0.0,
        passed=passed,
        details=ok_details if passed else "; ".join(problems),
    )


def _skipped(name: str, why: str) -> MetricResult:
    return MetricResult(name=name, score=1.0, passed=True, details=f"skipped: {why}")


def _fmt(value: float) -> str:
    return f"{value:g}"


def _block_check(name: str, case: GoldenCase, outcome: CaseOutcome) -> MetricResult | None:
    """Shared verdict when the turn was blocked, a block was expected or nothing came back;
    `None` when there is a response to evaluate and no block was expected.
    """
    if outcome.blocked_reason is not None:
        if case.expected_blocked:
            return _result(name, [], f"blocked as expected ({outcome.blocked_reason})")
        return _result(name, [f"unexpected block ({outcome.blocked_reason})"], "")
    if outcome.response is None:
        return _result(name, ["no response"], "")
    if case.expected_blocked:
        return _result(name, ["expected a guardrail block, got a 200"], "")
    return None


class SchemaAdherenceMetric:
    """Block expectations, rows adding up to `total_hours`, a perfect structure check and
    the number of phases in range."""

    name = "schema_adherence"

    def evaluate(self, case: GoldenCase, outcome: CaseOutcome) -> MetricResult:
        verdict = _block_check(self.name, case, outcome)
        if verdict is not None:
            return verdict
        response = outcome.response
        assert response is not None
        result = response.result
        problems: list[str] = []
        rows_hours = sum(row.hours for row in result.rows)
        if rows_hours != result.total_hours:
            problems.append(f"rows sum {rows_hours} h != total_hours {result.total_hours}")
        if response.validation is not None and response.validation.score != 1.0:
            issues = ", ".join(response.validation.issues) or "no issues listed"
            problems.append(f"validation score {response.validation.score:g} < 1.0 ({issues})")
        if case.expected_phase_count_range is not None:
            lo, hi = case.expected_phase_count_range
            count = len(result.phases)
            if not lo <= count <= hi:
                problems.append(f"phase count {count} outside [{lo}, {hi}]")
        return _result(self.name, problems, "schema ok")


class CostBoundsMetric:
    """Out-of-scope envelope when expected; otherwise in scope with hours, cost and
    duration inside their ranges (the duration passes when the ranges overlap)."""

    name = "cost_bounds"

    def evaluate(self, case: GoldenCase, outcome: CaseOutcome) -> MetricResult:
        verdict = _block_check(self.name, case, outcome)
        if verdict is not None:
            return verdict
        response = outcome.response
        assert response is not None
        result = response.result
        problems: list[str] = []
        if case.expected_out_of_scope:
            if not result.out_of_scope:
                problems.append(f"expected out of scope, got an estimate of {result.total_hours} h")
            elif result.total_hours != 0:
                problems.append(f"out of scope with total_hours {result.total_hours} != 0")
            return _result(self.name, problems, "out of scope as expected")

        if result.out_of_scope:
            return _result(self.name, ["unexpected out of scope"], "")
        if case.expected_hours_range is not None:
            lo, hi = case.expected_hours_range
            if not lo <= result.total_hours <= hi:
                problems.append(f"hours {result.total_hours} outside [{lo}, {hi}]")
        if case.expected_cost_range_eur is not None:
            lo, hi = case.expected_cost_range_eur
            cost = result.total_cost_eur
            if not lo <= cost <= hi:
                problems.append(f"cost {_fmt(cost)} EUR outside [{_fmt(lo)}, {_fmt(hi)}]")
        if case.expected_duration_weeks_range is not None:
            lo, hi = case.expected_duration_weeks_range
            duration = result.duration_weeks
            if duration is None:
                problems.append(f"no duration, expected weeks overlapping [{lo}, {hi}]")
            elif duration.max_weeks < lo or duration.min_weeks > hi:
                problems.append(
                    f"duration [{duration.min_weeks}, {duration.max_weeks}] weeks does not "
                    f"overlap [{lo}, {hi}]"
                )
        return _result(self.name, problems, "hours, cost and duration within bounds")


def _contains(haystack: str, term: str) -> bool:
    return normalize(term) in haystack


class ContentRecallMetric:
    """Expected terms in the title, summary or tasks; one expected technology among
    `result.technologies`; and the answer in the case's language. Matching ignores case
    and accents; a technology matches when the expected name is contained in a returned
    one ("Stripe" matches "Stripe Connect")."""

    name = "content_recall"

    def evaluate(self, case: GoldenCase, outcome: CaseOutcome) -> MetricResult:
        if case.expected_blocked or outcome.blocked_reason is not None:
            return _skipped(self.name, "blocked case")
        if case.expected_out_of_scope:
            return _skipped(self.name, "out-of-scope case")
        if outcome.response is None:
            return _result(self.name, ["no response"], "")
        result = outcome.response.result
        problems: list[str] = []
        if result.language != case.language:
            problems.append(f"language {result.language} != expected {case.language}")
        haystack = normalize(
            " ".join([result.title, result.summary, *(row.task for row in result.rows)])
        )
        missing = [term for term in case.expected_in_summary if not _contains(haystack, term)]
        if missing:
            problems.append(f"missing in title/summary/tasks: {missing}")
        expected_tech = case.expected_technologies_any_of
        if expected_tech and not _any_technology(expected_tech, result.technologies):
            problems.append(f"none of {expected_tech} in technologies")
        return _result(self.name, problems, "expected content present")


def _any_technology(expected: Iterable[str], returned: Iterable[str]) -> bool:
    returned_norm = [normalize(tech) for tech in returned]
    return any(normalize(term) in tech for term in expected for tech in returned_norm)


class TierMatchMetric:
    """The resolved audience tier equals `expected_tier`."""

    name = "tier_match"

    def evaluate(self, case: GoldenCase, outcome: CaseOutcome) -> MetricResult:
        if case.expected_tier is None:
            return _skipped(self.name, "no expected tier")
        if case.expected_blocked or outcome.blocked_reason is not None:
            return _skipped(self.name, "blocked case")
        if outcome.response is None:
            return _result(self.name, ["no response"], "")
        actual = outcome.response.tier
        expected = case.expected_tier
        if actual != expected:
            rule = outcome.response.tier_rule
            return _result(
                self.name, [f"tier {actual.value} ({rule}) != expected {expected.value}"], ""
            )
        return _result(self.name, [], f"tier {actual.value} ({outcome.response.tier_rule})")


METRICS: tuple[Metric, ...] = (
    SchemaAdherenceMetric(),
    CostBoundsMetric(),
    ContentRecallMetric(),
    TierMatchMetric(),
)


def run_all_metrics(case: GoldenCase, outcome: CaseOutcome) -> list[MetricResult]:
    """The four metrics, in order: schema, cost bounds, content recall and tier."""
    return [metric.evaluate(case, outcome) for metric in METRICS]
