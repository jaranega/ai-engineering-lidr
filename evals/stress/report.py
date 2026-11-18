"""Tables of the CAG stress test report from the runner's CSV (design s06 D6).

    uv run python -m evals.stress.report evals/stress/results.csv > tables.md

Prints Markdown (headings in Spanish, like `REPORT.md`): the summary per scenario and
attachment size, the three canonical curves as tables (latency vs `tokens_in`,
cumulative cost vs `turn_index`, `memory_drift` vs N) and the per-scenario columns
(`stale_tech`, budgets, attachment markers). Deterministic; it never writes the reading.

Conventions: error rows count in `errores` and their known or estimated cost
(`cost_estimated_usd`, a failed turn that may have reached the LLM) in the total cost,
with the estimated part in its own column; they never count in latency, tokens, recall
nor rates. Percentiles are nearest-rank (P50 of 1, 2, 3
is 2). The cumulative cost averages the cost of each turn over the repetitions, then
accumulates those means.
"""

import csv
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from evals.stress.scenarios import ATTACHMENT_MARKERS, DRIFT_CHECKPOINTS, PROFILE_NAMES

Row = dict[str, str]

# Upper bounds (exclusive) of the `tokens_in` buckets of the latency curve.
TOKEN_BUCKETS: tuple[int, ...] = (2_000, 4_000, 8_000, 16_000, 32_000)


def load_rows(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile; None without values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _ok(row: Row) -> bool:
    return not row.get("error")


def _values(rows: Iterable[Row], column: str) -> list[float]:
    return [v for row in rows if _ok(row) and (v := _num(row.get(column))) is not None]


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _usd(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f} %"


def _table(header: Sequence[str], body: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cells) + " |" for cells in body]
    return lines


def _group_key(row: Row) -> tuple[int, str, int]:
    """Profiles first (in their order), then attachments by size."""
    scenario = row["scenario"]
    order = PROFILE_NAMES.index(scenario) if scenario in PROFILE_NAMES else len(PROFILE_NAMES)
    size = int(row["attachment_kb"]) if row.get("attachment_kb") else -1
    return order, scenario, size


def _recall(rows: list[Row]) -> float | None:
    if rows and rows[0]["scenario"] == "attachments":
        markers = _values(rows, "attachment_markers_recalled")
        return _mean([m / len(ATTACHMENT_MARKERS) for m in markers])
    return _mean(_values(rows, "memory_drift"))


def summary_table(rows: list[Row]) -> list[str]:
    groups: dict[tuple[int, str, int], list[Row]] = defaultdict(list)
    for row in rows:
        groups[_group_key(row)].append(row)
    body = []
    for (_, scenario, size), group in sorted(groups.items()):
        ok = [row for row in group if _ok(row)]
        latencies = _values(group, "latency_ms")
        costs = [v for row in group if (v := _num(row.get("cost_usd"))) is not None]
        estimated = [v for row in group if (v := _num(row.get("cost_estimated_usd"))) is not None]
        kinds = [row.get("cache_hit_kind") for row in ok]
        body.append(
            [
                scenario,
                str(size) if size >= 0 else "-",
                str(len(group)),
                str(len(group) - len(ok)),
                _fmt(percentile(latencies, 50)),
                _fmt(percentile(latencies, 95)),
                _usd(sum(costs) + sum(estimated) if costs or estimated else None),
                _usd(sum(estimated) if estimated else None),
                _pct(kinds.count("exact") / len(kinds) if kinds else None),
                _pct(kinds.count("semantic") / len(kinds) if kinds else None),
                _fmt(_recall(group), 2),
            ]
        )
    header = [
        "Escenario",
        "Adjunto (KB)",
        "Filas",
        "Errores",
        "P50 latencia (ms)",
        "P95 latencia (ms)",
        "Coste total (USD)",
        "de ello estimado (USD)",
        "Caché exacta",
        "Caché semántica",
        "Recall medio",
    ]
    return [
        "## Resumen",
        "",
        *_table(header, body),
        "",
        "Coste total: el conocido más el estimado de los turnos fallidos que pudieron llegar"
        " al LLM (`cost_estimated_usd`: intentos × el turno más caro visto hasta entonces).",
        "",
        "Recall medio: `memory_drift` en los perfiles; marcadores del adjunto recordados"
        f" / {len(ATTACHMENT_MARKERS)} en `attachments`.",
    ]


def _bucket_label(index: int) -> str:
    lower = TOKEN_BUCKETS[index - 1] if index else 0
    if index == len(TOKEN_BUCKETS):
        return f">= {lower}"
    return f"{lower}-{TOKEN_BUCKETS[index] - 1}"


def latency_vs_tokens(rows: list[Row]) -> list[str]:
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        tokens, latency = _num(row.get("tokens_in")), _num(row.get("latency_ms"))
        if not _ok(row) or tokens is None or latency is None:
            continue
        index = next((i for i, bound in enumerate(TOKEN_BUCKETS) if tokens < bound), None)
        buckets[len(TOKEN_BUCKETS) if index is None else index].append((tokens, latency))
    body = []
    for index in sorted(buckets):
        pairs = buckets[index]
        latencies = [latency for _, latency in pairs]
        body.append(
            [
                _bucket_label(index),
                str(len(pairs)),
                _fmt(_mean([tokens for tokens, _ in pairs])),
                _fmt(percentile(latencies, 50)),
                _fmt(percentile(latencies, 95)),
            ]
        )
    header = ["tokens_in", "Turnos", "tokens_in medio", "P50 latencia (ms)", "P95 latencia (ms)"]
    return ["## Curva 1: latencia frente a tokens de entrada", "", *_table(header, body)]


def _per_turn_mean(rows: list[Row], scenario: str, column: str) -> dict[int, float]:
    values: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        if row["scenario"] != scenario or not _ok(row):
            continue
        value = _num(row.get(column))
        if value is not None:
            values[int(row["turn_index"])].append(value)
    return {turn: statistics.fmean(items) for turn, items in values.items()}


def _profiles(rows: list[Row]) -> list[str]:
    present = {row["scenario"] for row in rows}
    return [name for name in PROFILE_NAMES if name in present]


def cumulative_cost(rows: list[Row]) -> list[str]:
    profiles = _profiles(rows)
    means = {name: _per_turn_mean(rows, name, "cost_usd") for name in profiles}
    turns = sorted({turn for per_turn in means.values() for turn in per_turn})
    totals = dict.fromkeys(profiles, 0.0)
    body = []
    for turn in turns:
        cells = [str(turn)]
        for name in profiles:
            if turn in means[name]:
                totals[name] += means[name][turn]
                cells += [_usd(means[name][turn]), _usd(totals[name])]
            else:
                cells += ["-", "-"]
        body.append(cells)
    header = ["Turno"]
    for name in profiles:
        header += [f"{name} turno", f"{name} acumulado"]
    return [
        "## Curva 2: coste acumulado frente al turno (USD, media de las repeticiones)",
        "",
        *_table(header, body),
    ]


def drift_vs_n(rows: list[Row]) -> list[str]:
    profiles = _profiles(rows)
    means = {name: _per_turn_mean(rows, name, "memory_drift") for name in profiles}
    body = [
        [str(n), *(_fmt(means[name].get(n), 2) for name in profiles)] for n in DRIFT_CHECKPOINTS
    ]
    return [
        "## Curva 3: `memory_drift` medio frente a N",
        "",
        *_table(["N", *profiles], body),
    ]


def _per_turn_sources(rows: list[Row], column: str) -> dict[int, str]:
    """Per turn of `contradiction`, the source combinations of a budget across the
    repetitions with their count, e.g. `metadata|summary ×2`; `-` when never found.
    """
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["scenario"] != "contradiction" or not _ok(row):
            continue
        counts[int(row["turn_index"])][row.get(column) or ""] += 1
    labels: dict[int, str] = {}
    for turn, counter in counts.items():
        found = sorted((sources, n) for sources, n in counter.items() if sources)
        labels[turn] = ", ".join(f"{sources} ×{n}" for sources, n in found) or "-"
    return labels


_HTTP_ERROR = re.compile(r"HTTP (\d{3})(?: (\S+))?")


def _error_label(error: str) -> str:
    """`rechazado (413 too_much_text)` for a 4xx of the turn, else the error as is."""
    match = _HTTP_ERROR.search(error)
    if match and error.startswith("turn:") and match.group(1).startswith("4"):
        reason = f" {match.group(2)}" if match.group(2) else ""
        return f"rechazado ({match.group(1)}{reason})"
    return error


def scenario_columns(rows: list[Row]) -> list[str]:
    lines: list[str] = []
    stale = _per_turn_mean(rows, "pivot", "stale_tech")
    budget_30k = _per_turn_mean(rows, "contradiction", "budget_30k_present")
    budget_80k = _per_turn_mean(rows, "contradiction", "budget_80k_present")
    sources_30k = _per_turn_sources(rows, "budget_30k_sources")
    sources_80k = _per_turn_sources(rows, "budget_80k_sources")
    turns = sorted({*stale, *budget_30k, *budget_80k})
    if turns:
        body = [
            [
                str(turn),
                _pct(stale.get(turn)),
                _pct(budget_30k.get(turn)),
                _pct(budget_80k.get(turn)),
                sources_30k.get(turn, "-"),
                sources_80k.get(turn, "-"),
            ]
            for turn in turns
        ]
        header = [
            "Turno",
            "pivot: React Native en tecnologías",
            "contradiction: 30.000 en memoria",
            "contradiction: 80.000 en memoria",
            "Fuentes de 30.000",
            "Fuentes de 80.000",
        ]
        lines += [
            "## Columnas de escenario: pivote y contradicción (% de repeticiones)",
            "",
            *_table(header, body),
            "",
        ]
    attachments = [row for row in rows if row["scenario"] == "attachments"]
    if attachments:
        sizes: dict[int, list[Row]] = defaultdict(list)
        for row in attachments:
            sizes[int(row["attachment_kb"] or 0)].append(row)
        body = []
        for size in sorted(sizes):
            group = sizes[size]
            ok = [row for row in group if _ok(row)]
            errors = sorted({_error_label(row["error"]) for row in group if not _ok(row)})
            body.append(
                [
                    str(size),
                    str(len(group)),
                    str(len(group) - len(ok)),
                    _fmt(_mean(_values(group, "attachments_total_chars"))),
                    _fmt(_mean(_values(group, "tokens_in"))),
                    _fmt(percentile(_values(group, "latency_ms"), 50)),
                    _usd(_mean(_values(group, "cost_usd"))),
                    _fmt(_mean(_values(group, "attachment_markers_recalled")), 2),
                    "; ".join(errors) or "-",
                ]
            )
        header = [
            "Adjunto (KB)",
            "Filas",
            "Errores",
            "Caracteres del adjunto",
            "tokens_in medio",
            "P50 latencia (ms)",
            "Coste medio (USD)",
            f"Marcadores (de {len(ATTACHMENT_MARKERS)})",
            "Error",
        ]
        lines += ["## Columnas de escenario: adjuntos por tamaño", "", *_table(header, body)]
    return lines


def render_report(rows: list[Row]) -> str:
    sections = [
        summary_table(rows),
        latency_vs_tokens(rows),
        cumulative_cost(rows),
        drift_vs_n(rows),
        scenario_columns(rows),
    ]
    return "\n\n".join("\n".join(section) for section in sections if section).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m evals.stress.report <results.csv>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    sys.stdout.write(render_report(load_rows(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
