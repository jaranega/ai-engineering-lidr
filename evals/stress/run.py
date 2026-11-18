"""Runner of the CAG stress test (design s06 D5).

Usage::

    uv run python -m evals.stress.run [--http URL] \\
        --scenarios growing,pivot,contradiction --attachment-sizes 0,5,20,50,100 \\
        --repeats 3 --turns 20 --output evals/stress/results.csv --max-cost-usd 0.60

Without `--http` the run is in process: a `TestClient` over the app with an isolated
`SessionStore` and the debug endpoint switched on (`DEBUG_ENDPOINTS`); the provider,
moderation and memory are the real ones, so a run calls the real APIs and costs money.
With `--http` the server must run with `DEBUG_ENDPOINTS=true`.

Each repetition of each multi-turn profile is a new session that sends the first
`--turns` turns; each attachment size is a new one-turn session of the `attachments`
scenario (0 = no attachment). After every turn the runner reads
`GET /api/v1/sessions/{id}/debug` and writes one CSV row (flushed) before going on, so a
cut never loses what was measured.

The runner never writes transcripts nor responses (CSV or stdout): only ids, numbers,
metric scores and error codes. A failed turn becomes an error row and abandons that
conversation, not the run.

A turn that failed after it may have reached the LLM (5xx, invalid response or a client
error) has no known cost: instead of stopping the run, the runner adds a conservative
estimate to the running total, `(STRUCTURED_MAX_RETRIES + 1) x` the highest per-turn
`cost_usd` seen so far in the run (`FALLBACK_TURN_COST_USD` before any), and records it
in `cost_estimated_usd` (its `cost_usd` stays empty). A successful turn without
`cost_usd` is a pricing gap and still stops a capped run.

Exit codes: 0 run completed (error rows are data), 1 stopped by `--max-cost-usd`
(exceeded, or a successful turn with unknown cost), 2 usage error or the debug endpoint
is off.
"""

import argparse
import csv
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO

from evals.metrics import normalize
from evals.stress.fixtures import build_pdfs
from evals.stress.metrics import (
    DEFAULT_COST_BUDGET_USD,
    DEFAULT_LATENCY_BUDGET_MS,
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
    fact_sources,
    source_texts,
)
from evals.stress.scenarios import (
    ATTACHMENT_MARKERS,
    ATTACHMENTS,
    PROFILE_NAMES,
    Scenario,
    ScenarioTurn,
    facts_at,
    get_scenario,
    select_turns,
)

SESSIONS_PATH = "/api/v1/sessions"
HTTP_TIMEOUT_SECONDS = 180.0
DEFAULT_OUTPUT = Path("evals/stress/results.csv")
DEFAULT_ATTACHMENT_SIZES: tuple[int, ...] = (0, *build_pdfs.SIZES_KB)
DETAIL_LEVEL = "medium"
OUTPUT_FORMAT = "phases_table"
PDF_MIME = "application/pdf"
SHORT_ID_CHARS = 8
# Estimate of a failed turn's cost (see the module docstring).
FALLBACK_TURN_COST_USD = 0.005
DEFAULT_ESTIMATE_ATTEMPTS = 3  # STRUCTURED_MAX_RETRIES default (2) + 1

STALE_TECH = "React Native"
BUDGET_30K = "30.000"
BUDGET_80K = "80.000"

DEBUG_DISABLED_MESSAGE = (
    "The session debug endpoint answered 404: start the server with DEBUG_ENDPOINTS=true "
    "(never in production) and run again."
)

OBSERVATION_FIELDS: tuple[str, ...] = (
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "cache_hit_kind",
    "last_resolved_tier",
)
CSV_COLUMNS: tuple[str, ...] = (
    "scenario",
    "repeat",
    "attachment_kb",
    *OBSERVATION_FIELDS,
    "cost_estimated_usd",
    "latency_budget",
    "cost_budget",
    "memory_drift",
    "facts_total",
    "facts_recalled",
    "stale_tech",
    "budget_30k_present",
    "budget_80k_present",
    "budget_30k_sources",
    "budget_80k_sources",
    "attachment_markers_recalled",
    "error",
)


class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class StressClient(Protocol):
    """What the runner needs from a client: `TestClient` and `httpx.Client` both fit."""

    def post(self, url: str, **kwargs: Any) -> _Response: ...

    def get(self, url: str, **kwargs: Any) -> _Response: ...


class DebugEndpointDisabledError(RuntimeError):
    """The debug route answered 404 on the first read: `DEBUG_ENDPOINTS` is off."""


@dataclass(frozen=True)
class StressConfig:
    scenarios: tuple[str, ...] = PROFILE_NAMES
    attachment_sizes: tuple[int, ...] = DEFAULT_ATTACHMENT_SIZES
    repeats: int = 3
    turns: int = 20
    max_cost_usd: float | None = None
    latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS
    cost_budget_usd: float = DEFAULT_COST_BUDGET_USD
    fixtures_dir: Path = build_pdfs.FIXTURES_DIR
    # Attempts a failed turn may have paid for: STRUCTURED_MAX_RETRIES + 1.
    estimate_attempts: int = DEFAULT_ESTIMATE_ATTEMPTS


@dataclass
class StressReport:
    rows: int = 0
    errors: int = 0
    total_cost_usd: float = 0.0  # includes `estimated_cost_usd`
    estimated_cost_usd: float = 0.0
    stopped_by_cost_cap: bool = False
    stopped_by_unknown_cost: bool = False
    sessions: set[str] = field(default_factory=set)

    @property
    def stopped(self) -> bool:
        return self.stopped_by_cost_cap or self.stopped_by_unknown_cost


@dataclass
class _TurnResult:
    """One measured turn: the CSV row and how its cost is accounted.

    `cost_unknown`: a successful turn without cost (stops a capped run).
    `estimate_cost`: a failed turn that may have reached the LLM (cost estimated).
    """

    row: dict[str, Any]
    cost_unknown: bool = False
    estimate_cost: bool = False
    abandon: bool = False


class _CostCapReached(Exception):
    pass


def _short(session_id: str) -> str:
    return session_id[:SHORT_ID_CHARS]


def _detail_reason(response: _Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    if isinstance(detail, dict):
        reason = detail.get("reason")
        return str(reason) if reason is not None else None
    return None


def _http_error(prefix: str, response: _Response) -> str:
    reason = _detail_reason(response)
    return f"{prefix}: HTTP {response.status_code}" + (f" {reason}" if reason else "")


def _empty_row(scenario: str, repeat: int, attachment_kb: int | None) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(CSV_COLUMNS)
    row.update(scenario=scenario, repeat=repeat, attachment_kb=attachment_kb)
    return row


def form_data(scenario: Scenario, turn: ScenarioTurn) -> dict[str, str]:
    return {
        "transcript": turn.transcript,
        "project_type": scenario.project_type.value,
        "detail_level": DETAIL_LEVEL,
        "output_format": OUTPUT_FORMAT,
    }


def markers_recalled(result: Any) -> int:
    """Markers of the attachment found in the response's title, summary, tasks and
    assumptions (global and per phase), without case nor accents.
    """
    if not isinstance(result, dict):
        return 0
    parts: list[str] = [str(result.get("title") or ""), str(result.get("summary") or "")]
    parts += [str(row.get("task") or "") for row in result.get("rows") or ()]
    parts += [str(item) for item in result.get("assumptions_and_risks") or ()]
    for phase in result.get("phases") or ():
        parts += [str(item) for item in phase.get("assumptions") or ()]
    haystack = normalize("\n".join(parts))
    return sum(
        any(normalize(spelling) in haystack for spelling in spellings)
        for spellings in ATTACHMENT_MARKERS
    )


class StressRunner:
    """Runs the configured sessions against `client` and hands each row to `on_row`."""

    def __init__(
        self,
        client: StressClient,
        config: StressConfig,
        on_row: Callable[[dict[str, Any]], None],
    ) -> None:
        self._client = client
        self._config = config
        self._on_row = on_row
        self._latency = LatencyBudgetMetric(config.latency_budget_ms)
        self._cost = CostBudgetMetric(config.cost_budget_usd)
        self._debug_checked = False
        self._max_turn_cost: float | None = None
        self.report = StressReport()

    def run(self) -> StressReport:
        try:
            for name in self._config.scenarios:
                scenario = get_scenario(name)
                for repeat in range(1, self._config.repeats + 1):
                    self._conversation(scenario, repeat)
            for size_kb in self._config.attachment_sizes:
                for repeat in range(1, self._config.repeats + 1):
                    self._attachment_turn(size_kb, repeat)
        except _CostCapReached:
            pass
        return self.report

    # Sessions.

    def _create_session(self, row: dict[str, Any]) -> str | None:
        try:
            created = self._client.post(SESSIONS_PATH)
        except Exception as exc:  # noqa: BLE001 - any client failure is an error row
            row["error"] = f"create session: {type(exc).__name__}"
            return None
        if created.status_code != 201:
            row["error"] = _http_error("create session", created)
            return None
        session_id = str(created.json()["session_id"])
        self.report.sessions.add(session_id)
        if not self._debug_checked:
            # Probe the debug route before paying for any turn (empty session: 200).
            probe = self._client.get(f"{SESSIONS_PATH}/{session_id}/debug")
            if probe.status_code == 404 and _detail_reason(probe) != "session_not_found":
                raise DebugEndpointDisabledError(DEBUG_DISABLED_MESSAGE)
            self._debug_checked = True
        return session_id

    def _conversation(self, scenario: Scenario, repeat: int) -> None:
        first = select_turns(scenario, self._config.turns)
        row = _empty_row(scenario.name, repeat, None)
        session_id = self._create_session(row)
        if session_id is None:
            row["turn_index"] = first[0].index
            self._emit(_TurnResult(row))
            return
        for turn in first:
            result = self._turn(scenario, repeat, None, session_id, turn, files=None)
            self._emit(result)
            if result.abandon:
                return

    def _attachment_turn(self, size_kb: int, repeat: int) -> None:
        scenario = ATTACHMENTS
        (turn,) = select_turns(scenario, 1)
        row = _empty_row(scenario.name, repeat, size_kb)
        session_id = self._create_session(row)
        if session_id is None:
            row["turn_index"] = turn.index
            self._emit(_TurnResult(row))
            return
        files = None
        if size_kb:
            path = build_pdfs.path_for(size_kb, self._config.fixtures_dir)
            files = [("attachments", (path.name, path.read_bytes(), PDF_MIME))]
        self._emit(self._turn(scenario, repeat, size_kb, session_id, turn, files=files))

    # One turn.

    def _turn(
        self,
        scenario: Scenario,
        repeat: int,
        size_kb: int | None,
        session_id: str,
        turn: ScenarioTurn,
        *,
        files: list[tuple[str, tuple[str, bytes, str]]] | None,
    ) -> _TurnResult:
        row = _empty_row(scenario.name, repeat, size_kb)
        row["turn_index"] = turn.index
        row["session_id"] = _short(session_id)
        try:
            response = self._client.post(
                f"{SESSIONS_PATH}/{session_id}/estimate",
                data=form_data(scenario, turn),
                files=files,
            )
        except Exception as exc:  # noqa: BLE001 - the turn may have reached the LLM
            row["error"] = f"turn: {type(exc).__name__}"
            return _TurnResult(row, estimate_cost=True, abandon=True)
        if response.status_code != 200:
            row["error"] = _http_error("turn", response)
            # 4xx are rejected before any LLM call; a 5xx may come after paid calls.
            return _TurnResult(row, estimate_cost=response.status_code >= 500, abandon=True)
        try:
            body = response.json()
        except ValueError:
            row["error"] = "turn: invalid response"
            return _TurnResult(row, estimate_cost=True, abandon=True)

        try:
            debug = self._client.get(f"{SESSIONS_PATH}/{session_id}/debug")
        except Exception as exc:  # noqa: BLE001
            debug = None
            row["error"] = f"debug: {type(exc).__name__}"
        if debug is not None and debug.status_code != 200:
            row["error"] = _http_error("debug", debug)
        if row["error"] is not None:
            # The turn succeeded and was paid: keep its cost from the response.
            row["cost_usd"] = body.get("cost_usd")
            return _TurnResult(row, cost_unknown=row["cost_usd"] is None, abandon=True)

        snapshot = debug.json()
        observation = snapshot.get("last_observation") or {}
        for name in OBSERVATION_FIELDS:
            row[name] = observation.get(name)
        self._score(row, scenario, turn, snapshot, observation, body, size_kb)
        return _TurnResult(row, cost_unknown=row["cost_usd"] is None)

    def _score(
        self,
        row: dict[str, Any],
        scenario: Scenario,
        turn: ScenarioTurn,
        snapshot: dict[str, Any],
        observation: dict[str, Any],
        body: dict[str, Any],
        size_kb: int | None,
    ) -> None:
        row["latency_budget"] = self._latency.evaluate(observation).score
        row["cost_budget"] = self._cost.evaluate(observation).score
        facts = facts_at(scenario, turn.index)
        drift = MemoryDriftMetric(facts).evaluate(snapshot)
        row["memory_drift"] = round(drift.score, 4)
        row["facts_total"] = len(facts)
        texts = source_texts(snapshot)
        row["facts_recalled"] = sum(bool(fact_sources(texts, fact)) for fact in facts)
        if scenario.name == "pivot":
            technologies = (snapshot.get("project_metadata") or {}).get(
                "mentioned_technologies"
            ) or ()
            stale = any(normalize(STALE_TECH) in normalize(str(t)) for t in technologies)
            row["stale_tech"] = int(stale)
        elif scenario.name == "contradiction":
            for column, budget in (("budget_30k", BUDGET_30K), ("budget_80k", BUDGET_80K)):
                sources = fact_sources(texts, budget)
                row[f"{column}_present"] = int(bool(sources))
                # Where it survives (summary|anchors|metadata); empty when absent.
                row[f"{column}_sources"] = "|".join(sources) or None
        elif scenario.name == "attachments" and size_kb is not None:
            row["attachment_markers_recalled"] = markers_recalled(body.get("result"))

    # Bookkeeping.

    def _estimate(self) -> float:
        """Conservative cost of a failed turn: every attempt at the dearest turn so far."""
        per_attempt = (
            self._max_turn_cost if self._max_turn_cost is not None else FALLBACK_TURN_COST_USD
        )
        return round(self._config.estimate_attempts * per_attempt, 6)

    def _emit(self, result: _TurnResult) -> None:
        row = result.row
        self.report.rows += 1
        self.report.errors += row["error"] is not None
        cost = row.get("cost_usd")
        if cost is not None:
            self.report.total_cost_usd = round(self.report.total_cost_usd + cost, 6)
            self._max_turn_cost = max(cost, self._max_turn_cost or 0.0)
        elif result.estimate_cost:
            estimate = self._estimate()
            row["cost_estimated_usd"] = estimate
            self.report.estimated_cost_usd = round(self.report.estimated_cost_usd + estimate, 6)
            self.report.total_cost_usd = round(self.report.total_cost_usd + estimate, 6)
        self._on_row(row)
        cap = self._config.max_cost_usd
        if cap is None:
            return
        if result.cost_unknown:
            self.report.stopped_by_unknown_cost = True
            raise _CostCapReached
        if self.report.total_cost_usd > cap:
            self.report.stopped_by_cost_cap = True
            raise _CostCapReached


# CSV and console output.


@contextmanager
def csv_writer(path: Path) -> Iterator[Callable[[dict[str, Any]], None]]:
    """A callable that writes one row and flushes it (the header first)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        handle.flush()

        def write(row: dict[str, Any]) -> None:
            writer.writerow({key: "" if value is None else value for key, value in row.items()})
            handle.flush()

        yield write


def format_row(row: dict[str, Any]) -> str:
    """One console line per row: ids and numbers only."""
    size = f" {row['attachment_kb']}KB" if row.get("attachment_kb") is not None else ""
    head = f"{row['scenario']}{size} r{row['repeat']} t{row.get('turn_index')}"
    if row.get("error"):
        estimate = row.get("cost_estimated_usd")
        suffix = f" (estimated ${estimate:.5f})" if estimate is not None else ""
        return f"{head:<28} ERROR {row['error']}{suffix}"
    cost = row.get("cost_usd")
    cost_text = f"${cost:.5f}" if cost is not None else "$?"
    return (
        f"{head:<28} {row.get('latency_ms')} ms {cost_text} in={row.get('tokens_in')}"
        f" win={row.get('messages_in_window')} anchors={row.get('anchors_count')}"
        f" summary={row.get('summary_chars')} drift={row.get('memory_drift')}"
    )


def _printer(output: Callable[[dict[str, Any]], None], stream: TextIO) -> Callable:
    def on_row(row: dict[str, Any]) -> None:
        output(row)
        print(format_row(row), file=stream, flush=True)

    return on_row


def format_summary(report: StressReport, config: StressConfig) -> str:
    lines = [
        "",
        f"Rows: {report.rows} (errors {report.errors}), sessions: {len(report.sessions)}",
        f"Total cost: ${report.total_cost_usd:.4f}"
        f" (of which estimated: ${report.estimated_cost_usd:.4f})",
    ]
    if report.stopped_by_cost_cap:
        lines.append(f"STOPPED: cost cap of ${config.max_cost_usd} reached")
    if report.stopped_by_unknown_cost:
        lines.append(f"STOPPED: cost unknown, cap of ${config.max_cost_usd} cannot be guaranteed")
    return "\n".join(lines)


# CLI.


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


def _scenario_list(value: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    unknown = [name for name in names if name not in PROFILE_NAMES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown scenario(s) {unknown}; expected some of {list(PROFILE_NAMES)}"
            " (attachments run through --attachment-sizes)"
        )
    return names


def _size_list(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(item) for item in value.split(",") if item.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("sizes must be integers (KB)") from None
    unknown = [size for size in sizes if size not in DEFAULT_ATTACHMENT_SIZES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown size(s) {unknown}; expected some of {list(DEFAULT_ATTACHMENT_SIZES)}"
        )
    return sizes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.stress.run",
        description="CAG stress test against the session API (real LLM calls).",
    )
    parser.add_argument("--http", default=None, metavar="URL", help="Base URL of a server.")
    parser.add_argument("--scenarios", type=_scenario_list, default=PROFILE_NAMES, metavar="A,B")
    parser.add_argument(
        "--attachment-sizes", type=_size_list, default=DEFAULT_ATTACHMENT_SIZES, metavar="KB,KB"
    )
    parser.add_argument("--repeats", type=_positive_int, default=3)
    parser.add_argument("--turns", type=_positive_int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, metavar="PATH")
    parser.add_argument("--max-cost-usd", type=_non_negative_float, default=None, metavar="X")
    parser.add_argument(
        "--latency-budget-ms", type=_positive_int, default=DEFAULT_LATENCY_BUDGET_MS
    )
    parser.add_argument(
        "--cost-budget-usd", type=_non_negative_float, default=DEFAULT_COST_BUDGET_USD
    )
    return parser


def estimate_attempts() -> int:
    """`STRUCTURED_MAX_RETRIES + 1` from the local settings; the default when they do not
    load. With `--http` the server's value may differ: the local one is the best guess.
    """
    try:
        from app.config import get_settings

        return get_settings().structured_max_retries + 1
    except Exception:  # noqa: BLE001 - missing keys or a bad value: keep the default
        return DEFAULT_ESTIMATE_ATTEMPTS


def config_from_args(args: argparse.Namespace) -> StressConfig:
    return StressConfig(
        scenarios=args.scenarios,
        attachment_sizes=args.attachment_sizes,
        repeats=args.repeats,
        turns=args.turns,
        max_cost_usd=args.max_cost_usd,
        latency_budget_ms=args.latency_budget_ms,
        cost_budget_usd=args.cost_budget_usd,
        estimate_attempts=estimate_attempts(),
    )


def run_with_client(
    client: StressClient, config: StressConfig, output: Path, stream: TextIO = sys.stdout
) -> StressReport:
    """Build the missing PDFs, run every session and write the CSV row by row."""
    # httpx logs every request URL (full session ids) at INFO; the app's logging setup
    # runs at startup, so this goes after the client is up.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if any(config.attachment_sizes) and build_pdfs.missing(config.fixtures_dir):
        build_pdfs.build_all(config.fixtures_dir)
    with csv_writer(output) as write:
        runner = StressRunner(client, config, _printer(write, stream))
        return runner.run()


@contextmanager
def in_process_client(sessions_needed: int) -> Iterator[StressClient]:
    """`TestClient` over the app with an isolated store and `DEBUG_ENDPOINTS` on.

    The variable is set before the app is imported (in case the route is mounted at
    import time) and `get_settings` is overridden too (in case it is checked per request).
    """
    os.environ["DEBUG_ENDPOINTS"] = "true"
    from fastapi.testclient import TestClient

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.services.session_service import get_session_store
    from app.sessions import SessionStore

    settings = get_settings().model_copy(update={"debug_endpoints": True})
    # One store for the whole run: the override is called per request.
    store = SessionStore(
        max_sessions=max(settings.max_sessions, sessions_needed),
        max_turns=settings.max_conversation_turns,
        max_anchors=settings.max_anchored_turns,
    )
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session_store, None)
        app.dependency_overrides.pop(get_settings, None)


@contextmanager
def http_client(base_url: str) -> Iterator[StressClient]:
    import httpx

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS) as client:
        yield client


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    sessions = config.repeats * (len(config.scenarios) + len(config.attachment_sizes))
    target = args.http or "in-process"
    print(
        f"Stress test: {sessions} session(s), scenarios={','.join(config.scenarios) or '-'},"
        f" attachment sizes={','.join(map(str, config.attachment_sizes)) or '-'},"
        f" turns={config.turns}, target={target}",
        flush=True,
    )
    start = time.perf_counter()
    context = http_client(args.http) if args.http else in_process_client(sessions)
    try:
        with context as client:
            report = run_with_client(client, config, args.output)
    except DebugEndpointDisabledError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(format_summary(report, config))
    print(f"CSV written to {args.output} in {time.perf_counter() - start:.0f} s")
    return 1 if report.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
