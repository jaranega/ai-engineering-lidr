"""Deterministic metrics of the CAG stress test (design s06 D4).

They live apart from `evals.metrics` because they evaluate a turn observation
(`turn_observed` / `last_observation`) or the debug snapshot of a session, not a golden
case and its response; they reuse its `MetricResult` and `normalize`. No LLM, no network.

Observations and snapshots are read either as mappings (the JSON of
`GET /api/v1/sessions/{id}/debug`) or as objects with attributes (`TurnObservation`,
`SessionDebugSnapshot`). `details` carry numbers, fact names and source names only.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from evals.metrics import MetricResult, normalize

Source = Literal["summary", "anchors", "metadata"]
SOURCES: tuple[Source, ...] = ("summary", "anchors", "metadata")
MISSING = "missing"

DEFAULT_LATENCY_BUDGET_MS = 10_000
DEFAULT_COST_BUDGET_USD = 0.003


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _verdict(name: str, passed: bool, details: str) -> MetricResult:
    return MetricResult(name=name, score=1.0 if passed else 0.0, passed=passed, details=details)


class LatencyBudgetMetric:
    """1.0 if the turn's `latency_ms <= budget_ms`, else 0.0: the SLA as a test."""

    name = "latency_budget"

    def __init__(self, budget_ms: int = DEFAULT_LATENCY_BUDGET_MS) -> None:
        if budget_ms < 0:
            raise ValueError("budget_ms must not be negative")
        self.budget_ms = budget_ms

    def evaluate(self, observation: Any) -> MetricResult:
        latency = _get(observation, "latency_ms")
        if latency is None:
            return _verdict(self.name, False, "unknown latency")
        passed = latency <= self.budget_ms
        sign = "<=" if passed else ">"
        return _verdict(
            self.name, passed, f"latency {latency} ms {sign} budget {self.budget_ms} ms"
        )


class CostBudgetMetric:
    """1.0 if the turn's `cost_usd <= budget_usd`, else 0.0; an unknown cost fails."""

    name = "cost_budget"

    def __init__(self, budget_usd: float = DEFAULT_COST_BUDGET_USD) -> None:
        if budget_usd < 0:
            raise ValueError("budget_usd must not be negative")
        self.budget_usd = budget_usd

    def evaluate(self, observation: Any) -> MetricResult:
        cost = _get(observation, "cost_usd")
        if cost is None:
            return _verdict(self.name, False, "unknown cost")
        passed = cost <= self.budget_usd
        sign = "<=" if passed else ">"
        return _verdict(self.name, passed, f"cost ${cost:g} {sign} budget ${self.budget_usd:g}")


def metadata_text(metadata: Any) -> str:
    """The project metadata flattened to text: name, scope, technologies, constraints
    and rejected options (the fields a fact can survive in).
    """
    if metadata is None:
        return ""
    parts: list[str] = []
    for field in ("project_name", "agreed_scope"):
        value = _get(metadata, field)
        if value:
            parts.append(str(value))
    for field in ("mentioned_technologies", "explicit_constraints", "rejected_options"):
        parts.extend(str(item) for item in _get(metadata, field) or ())
    return "\n".join(parts)


def source_texts(snapshot: Any, where: Iterable[Source] = SOURCES) -> dict[Source, str]:
    """Normalized text of each requested source of a debug snapshot."""
    texts: dict[Source, str] = {}
    for source in where:
        if source == "summary":
            raw = _get(snapshot, "summary") or ""
        elif source == "anchors":
            raw = "\n".join(str(anchor) for anchor in _get(snapshot, "anchors") or ())
        elif source == "metadata":
            raw = metadata_text(_get(snapshot, "project_metadata"))
        else:
            raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
        texts[source] = normalize(raw)
    return texts


def fact_sources(texts: Mapping[Source, str], fact: str) -> tuple[Source, ...]:
    """Sources whose text contains `fact` (case- and accent-insensitive substring)."""
    needle = normalize(fact)
    return tuple(source for source, text in texts.items() if needle and needle in text)


class MemoryDriftMetric:
    """Fraction of `facts` still present in the session memory (summary, anchors and
    flattened metadata, as chosen by `where`); passes only when all are. No facts: 1.0.

    `details` lists every fact with the sources where it was found (joined with `+`) or
    `missing`, e.g. `Nimbus: metadata; Flutter: missing`.
    """

    name = "memory_drift"

    def __init__(self, facts: Sequence[str], where: Sequence[Source] = SOURCES) -> None:
        unknown = [source for source in where if source not in SOURCES]
        if unknown:
            raise ValueError(f"unknown source(s) {unknown}; expected some of {SOURCES}")
        self.facts = tuple(facts)
        self.where = tuple(where)

    def evaluate(self, snapshot: Any) -> MetricResult:
        if not self.facts:
            return _verdict(self.name, True, "no facts to recall")
        texts = source_texts(snapshot, self.where)
        found = 0
        notes: list[str] = []
        for fact in self.facts:
            sources = fact_sources(texts, fact)
            found += bool(sources)
            notes.append(f"{fact}: {'+'.join(sources) if sources else MISSING}")
        score = found / len(self.facts)
        return MetricResult(
            name=self.name, score=score, passed=score == 1.0, details="; ".join(notes)
        )
