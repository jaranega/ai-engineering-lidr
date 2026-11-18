"""Eval runner: send the golden cases to the session API and score them (design D12).

Usage::

    uv run python -m evals.run --mode actor
    uv run python -m evals.run --mode acb --limit 5 --output acb.json --max-cost-usd 0.25
    uv run python -m evals.run --case case-001-... --http http://localhost:8000

Without `--http` the run is in process: a `TestClient` over the app with an isolated
`SessionStore` (one for the whole run) overriding `get_session_store`; the provider,
moderation and memory are the real ones, so a run calls the real APIs and costs money.
Each case creates its own session and sends one turn (`review=true` in `acb` mode).

The runner never prints nor stores transcripts or full responses: only ids, metrics,
counters and the metrics' `details`. Exit codes: 0 all cases pass, 1 any case fails or
errors (or the cost cap stopped the run), 2 usage or dataset error.

With `--max-cost-usd`, a case whose cost is unknown (it may have called the LLM but
reports no `cost_usd`) stops the run (`stopped_by_unknown_cost`): the cap cannot be
guaranteed (design s05-fix D3). Without a cap, those cases are counted apart.
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from app.schemas import SessionEstimateResponse
from evals.dataset import DatasetError, GoldenCase, load_dataset
from evals.metrics import CaseOutcome, MetricResult, run_all_metrics

Mode = Literal["actor", "acb"]
MODES: tuple[Mode, ...] = ("actor", "acb")

SESSIONS_PATH = "/api/v1/sessions"
GUARDRAIL_REASONS = frozenset({"prompt_injection", "pii", "moderation"})
HTTP_TIMEOUT_SECONDS = 180.0


class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class EvalClient(Protocol):
    """What the runner needs from a client: `TestClient` and `httpx.Client` both fit."""

    def post(self, url: str, **kwargs: Any) -> _Response: ...


@dataclass
class CaseRow:
    """One line of the report. `error` is set when the turn failed (counts as failed).

    `cost_unknown` marks a case that may have called the LLM without a known cost: a 200
    with `cost_usd = null`, or a turn that failed with a 5xx, a client exception or an
    unreadable response. Guardrail blocks and other 4xx (rejected before any LLM call)
    and session-creation failures never count.
    """

    id: str
    language: str
    passed: bool
    latency_ms: int | None = None
    cost_usd: float | None = None
    cache_hit: bool | None = None
    tier: str | None = None
    blocked_reason: str | None = None
    error: str | None = None
    metrics: list[MetricResult] = field(default_factory=list)
    final_decision: str | None = None
    stop_reason: str | None = None
    iterations_run: int | None = None
    cost_unknown: bool = False


@dataclass
class EvalReport:
    mode: Mode
    cases_total: int
    rows: list[CaseRow] = field(default_factory=list)
    stopped_by_cost_cap: bool = False
    stopped_by_unknown_cost: bool = False
    max_cost_usd: float | None = None

    @property
    def total_cost_usd(self) -> float:
        return round(sum(row.cost_usd or 0.0 for row in self.rows), 6)

    @property
    def all_passed(self) -> bool:
        return (
            not self.stopped_by_cost_cap
            and not self.stopped_by_unknown_cost
            and len(self.rows) == self.cases_total
            and all(row.passed for row in self.rows)
        )

    def summary(self) -> dict[str, Any]:
        rows = self.rows
        latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
        metric_passes: dict[str, int] = {}
        for row in rows:
            for metric in row.metrics:
                metric_passes[metric.name] = metric_passes.get(metric.name, 0) + metric.passed
        summary: dict[str, Any] = {
            "cases_total": self.cases_total,
            "cases_run": len(rows),
            "cases_passed": sum(row.passed for row in rows),
            "errors": sum(row.error is not None for row in rows),
            "blocked": sum(row.blocked_reason is not None for row in rows),
            "metric_passes": metric_passes,
            "total_cost_usd": self.total_cost_usd,
            # Cases whose cost is not in `total_cost_usd` although they may have paid.
            "unknown_cost_cases": sum(row.cost_unknown for row in rows),
            "latency_ms_mean": round(statistics.fmean(latencies)) if latencies else None,
            "latency_ms_p50": round(statistics.median(latencies)) if latencies else None,
            "latency_ms_max": max(latencies) if latencies else None,
            "cache_hits": sum(bool(row.cache_hit) for row in rows),
            "stopped_by_cost_cap": self.stopped_by_cost_cap,
            "stopped_by_unknown_cost": self.stopped_by_unknown_cost,
        }
        if self.mode == "acb":
            reviewed = [row for row in rows if row.iterations_run is not None]
            summary["stop_reasons"] = dict(Counter(row.stop_reason for row in reviewed))
            summary["final_decisions"] = dict(Counter(row.final_decision for row in reviewed))
            summary["iterations_mean"] = (
                round(statistics.fmean(row.iterations_run for row in reviewed), 2)
                if reviewed
                else None
            )
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "max_cost_usd": self.max_cost_usd,
            "summary": self.summary(),
            "rows": [asdict(row) for row in self.rows],
        }


def form_data(case: GoldenCase, mode: Mode) -> dict[str, str]:
    """Multipart fields of the turn; `tier` only when not `auto`, `review` only in acb."""
    data = {
        "transcript": case.transcript,
        "project_type": case.project_type.value,
        "detail_level": case.detail_level.value,
        "output_format": case.output_format.value,
    }
    if case.tier != "auto":
        data["tier"] = str(case.tier)
    if mode == "acb":
        data["review"] = "true"
    return data


def _guardrail_reason(response: _Response) -> str | None:
    """`reason` of a guardrail 400, or `None` when the 400 is something else."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    if isinstance(detail, dict) and detail.get("reason") in GUARDRAIL_REASONS:
        return detail["reason"]
    return None


def run_case(case: GoldenCase, mode: Mode, client: EvalClient) -> CaseRow:
    """Create a session, send one turn and score it; failures become an error row."""
    row = CaseRow(id=case.id, language=case.language, passed=False)
    turn_sent = False
    try:
        created = client.post(SESSIONS_PATH)
        if created.status_code != 201:
            row.error = f"create session: HTTP {created.status_code}"
            return row
        session_id = created.json()["session_id"]
        start = time.perf_counter()
        turn_sent = True
        response = client.post(f"{SESSIONS_PATH}/{session_id}/estimate", data=form_data(case, mode))
        row.latency_ms = int((time.perf_counter() - start) * 1000)
    except Exception as exc:  # noqa: BLE001 - any client failure is an error row
        row.error = f"{type(exc).__name__}"
        # The turn may have reached the LLM before the client failed.
        row.cost_unknown = turn_sent
        return row

    if response.status_code == 400 and (reason := _guardrail_reason(response)) is not None:
        row.blocked_reason = reason
        outcome = CaseOutcome(blocked_reason=reason)
    elif response.status_code == 200:
        try:
            parsed = SessionEstimateResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            row.error = f"invalid response: {type(exc).__name__}"
            row.cost_unknown = True
            return row
        row.cost_usd = parsed.cost_usd
        row.cost_unknown = parsed.cost_usd is None
        row.cache_hit = parsed.cache_hit
        row.tier = parsed.tier.value
        if parsed.acb is not None:
            row.final_decision = parsed.acb.final_decision
            row.stop_reason = parsed.acb.stop_reason
            row.iterations_run = parsed.acb.iterations_run
        outcome = CaseOutcome(response=parsed)
    else:
        row.error = f"HTTP {response.status_code}"
        # 4xx are rejected before any LLM call; a 5xx (e.g. 502) may come after paid calls.
        row.cost_unknown = response.status_code >= 500
        return row

    row.metrics = run_all_metrics(case, outcome)
    row.passed = all(metric.passed for metric in row.metrics)
    return row


def run_cases(
    cases: Sequence[GoldenCase],
    mode: Mode,
    client: EvalClient,
    *,
    max_cost_usd: float | None = None,
    on_row: Any = None,
) -> EvalReport:
    """Run every case in order and return the report.

    Stops after the case that takes the accumulated `cost_usd` above `max_cost_usd`
    (`stopped_by_cost_cap`) or, with `max_cost_usd`, after the first case whose cost is
    unknown (`stopped_by_unknown_cost`, design s05-fix D3). `on_row(row)` is called after
    each case (the CLI prints it).
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    report = EvalReport(mode=mode, cases_total=len(cases), max_cost_usd=max_cost_usd)
    for case in cases:
        row = run_case(case, mode, client)
        report.rows.append(row)
        if on_row is not None:
            on_row(row)
        if max_cost_usd is None:
            continue
        if row.cost_unknown:
            report.stopped_by_unknown_cost = True
            break
        if report.total_cost_usd > max_cost_usd:
            report.stopped_by_cost_cap = True
            break
    return report


def format_row(row: CaseRow, mode: Mode) -> str:
    """One line per case: id, language, latency, cost, metrics and the ACB decision."""
    latency = f"{row.latency_ms} ms" if row.latency_ms is not None else "-"
    cost = f"${row.cost_usd:.4f}" if row.cost_usd is not None else "-"
    parts = [f"{row.id:<44}", row.language, f"{latency:>9}", f"{cost:>8}"]
    if row.error is not None:
        parts.append(f"ERROR {row.error}")
    else:
        if row.blocked_reason is not None:
            parts.append(f"blocked={row.blocked_reason}")
        parts.extend(f"{m.name}={'PASS' if m.passed else 'FAIL'}" for m in row.metrics)
        if mode == "acb" and row.final_decision is not None:
            parts.append(f"acb={row.final_decision}/{row.stop_reason}/it={row.iterations_run}")
    line = " | ".join(parts)
    failures = [f"    {m.name}: {m.details}" for m in row.metrics if not m.passed]
    return "\n".join([line, *failures])


def format_summary(report: EvalReport) -> str:
    summary = report.summary()
    lines = [
        "",
        f"Summary (mode={report.mode})",
        f"  cases passing all metrics: {summary['cases_passed']}/{summary['cases_total']}"
        f" (run {summary['cases_run']}, errors {summary['errors']})",
    ]
    lines += [
        f"  {name}: {passes}/{summary['cases_run'] - summary['errors']}"
        for name, passes in summary["metric_passes"].items()
    ]
    lines += [
        f"  total cost: ${summary['total_cost_usd']:.4f}"
        + (
            f" (+ {summary['unknown_cost_cases']} case(s) with unknown cost)"
            if summary["unknown_cost_cases"]
            else ""
        ),
        f"  latency ms: mean {summary['latency_ms_mean']}, p50 {summary['latency_ms_p50']},"
        f" max {summary['latency_ms_max']}",
        f"  cache hits: {summary['cache_hits']}",
    ]
    if report.mode == "acb":
        lines += [
            f"  stop reasons: {summary['stop_reasons']}",
            f"  final decisions: {summary['final_decisions']}",
            f"  mean iterations: {summary['iterations_mean']}",
        ]
    if report.stopped_by_cost_cap:
        lines.append(f"  STOPPED: cost cap of ${report.max_cost_usd} reached")
    if report.stopped_by_unknown_cost:
        lines.append(f"  STOPPED: cost unknown, cap of ${report.max_cost_usd} cannot be guaranteed")
    return "\n".join(lines)


def select_cases(
    cases: list[GoldenCase], case_ids: Sequence[str] | None, limit: int | None
) -> list[GoldenCase]:
    """Filter by `--case` ids (dataset order) and then apply `--limit`."""
    if case_ids:
        known = {case.id for case in cases}
        unknown = [case_id for case_id in case_ids if case_id not in known]
        if unknown:
            raise DatasetError(f"unknown case id(s): {', '.join(unknown)}")
        wanted = set(case_ids)
        cases = [case for case in cases if case.id in wanted]
    return cases[:limit] if limit is not None else cases


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Run the golden dataset against the session API (real LLM calls).",
    )
    parser.add_argument("--mode", choices=MODES, default="actor")
    parser.add_argument("--limit", type=_positive_int, default=None)
    parser.add_argument("--case", action="append", dest="cases", metavar="ID")
    parser.add_argument("--http", default=None, metavar="URL", help="Base URL of a server.")
    parser.add_argument("--output", type=Path, default=None, metavar="PATH")
    parser.add_argument("--max-cost-usd", type=_non_negative_float, default=None, metavar="X")
    parser.add_argument(
        "--dataset", type=Path, default=None, metavar="PATH", help="Golden dataset JSON."
    )
    return parser


def _run_in_process(cases: list[GoldenCase], mode: Mode, max_cost: float | None) -> EvalReport:
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app.services.session_service import get_session_store
    from app.sessions import SessionStore

    settings = get_settings()
    # One store for the whole run: the override is called per request, so a fresh store
    # per call would lose the session between `POST /sessions` and the turn.
    store = SessionStore(
        max_sessions=max(settings.max_sessions, len(cases)),
        max_turns=settings.max_conversation_turns,
        max_anchors=settings.max_anchored_turns,
    )
    app.dependency_overrides[get_session_store] = lambda: store
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            return run_cases(cases, mode, client, max_cost_usd=max_cost, on_row=_printer(mode))
    finally:
        app.dependency_overrides.pop(get_session_store, None)


def _run_over_http(
    cases: list[GoldenCase], mode: Mode, base_url: str, max_cost: float | None
) -> EvalReport:
    import httpx

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS) as client:
        return run_cases(cases, mode, client, max_cost_usd=max_cost, on_row=_printer(mode))


def _printer(mode: Mode) -> Any:
    def print_row(row: CaseRow) -> None:
        print(format_row(row, mode), flush=True)

    return print_row


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases = select_cases(load_dataset(args.dataset), args.cases, args.limit)
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2

    target = args.http or "in-process"
    print(f"Running {len(cases)} case(s), mode={args.mode}, target={target}", flush=True)
    if args.http:
        report = _run_over_http(cases, args.mode, args.http, args.max_cost_usd)
    else:
        report = _run_in_process(cases, args.mode, args.max_cost_usd)
    print(format_summary(report))

    if args.output is not None:
        args.output.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"Report written to {args.output}")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
