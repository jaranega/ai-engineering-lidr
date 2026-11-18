"""cag-stress-test / Informe del stress test, on a small hand-made CSV."""

import csv

import pytest

from evals.stress import report
from evals.stress.run import CSV_COLUMNS


def row(scenario, turn, latency, cost, tokens=1000, **extra):
    base = dict.fromkeys(CSV_COLUMNS, "")
    base.update(
        scenario=scenario,
        repeat=1,
        turn_index=turn,
        latency_ms=latency,
        cost_usd=cost,
        tokens_in=tokens,
        tokens_out=100,
        cache_hit_kind="none",
        memory_drift=1.0,
        error="",
    )
    base.update(extra)
    return base


SAMPLE = [
    row("growing", 1, 1000, 0.001),
    row("growing", 2, 2000, 0.002, tokens=3000),
    row("growing", 3, 3000, 0.003, tokens=9000, cache_hit_kind="exact"),
    row("pivot", 1, 4000, 0.01, stale_tech=0),
    row("pivot", 2, 5000, 0.01, stale_tech=1, memory_drift=0.5),
    row("pivot", 3, 6000, 0.01, stale_tech=1, memory_drift=0.5),
    row(
        "contradiction",
        3,
        1500,
        0.002,
        budget_30k_present=1,
        budget_80k_present=0,
        budget_30k_sources="summary|anchors",
    ),
    row("attachments", 1, 2500, 0.004, attachment_kb=5, attachment_markers_recalled=3),
    row(
        "attachments",
        1,
        "",
        "",
        attachment_kb=100,
        tokens_in="",
        error="turn: HTTP 413 too_much_text",
    ),
]


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "sample.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(SAMPLE)
    return path


def render(csv_path) -> str:
    return report.render_report(report.load_rows(csv_path))


def line_of(text: str, *needles: str) -> str:
    return next(ln for ln in text.splitlines() if all(n in ln for n in needles))


def test_summary_table_shows_expected_p50_p95_and_total_cost(csv_path):
    """Scenario: Informe de un CSV de ejemplo (P50, P95)."""
    text = render(csv_path)
    growing = [c.strip() for c in line_of(text, "| growing |").split("|")]
    assert growing[1] == "growing"
    assert growing[5:8] == ["2000", "3000", "0.0060"]  # P50, P95, total cost
    assert growing[8] == "-"  # nothing estimated
    assert growing[9] == "33 %"  # one exact hit out of three
    pivot = [c.strip() for c in line_of(text, "| pivot |").split("|")]
    assert pivot[5:8] == ["5000", "6000", "0.0300"]


def test_total_cost_includes_the_estimated_cost_of_failed_turns(tmp_path):
    """A 5xx row with `cost_estimated_usd` adds to the total and shows the estimated part."""
    path = tmp_path / "estimated.csv"
    rows = [
        row("growing", 1, 1000, 0.001),
        row("growing", 2, "", "", tokens="", cost_estimated_usd=0.003, error="turn: HTTP 502"),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    text = report.render_report(report.load_rows(path))
    growing = [c.strip() for c in line_of(text, "| growing |").split("|")]
    assert growing[3:5] == ["2", "1"]  # rows, errors
    assert growing[7:9] == ["0.0040", "0.0030"]  # total (known + estimated), estimated


def test_cumulative_cost_curve_adds_the_cost_per_turn(csv_path):
    """Scenario: Informe de un CSV de ejemplo (coste acumulado)."""
    text = render(csv_path)
    section = text.split("## Curva 2")[1].split("## Curva 3")[0]
    assert "| 1 | 0.0010 | 0.0010 |" in section
    assert "| 2 | 0.0020 | 0.0030 |" in section
    assert "| 3 | 0.0030 | 0.0060 |" in section


def test_latency_vs_tokens_buckets(csv_path):
    section = render(csv_path).split("## Curva 1")[1].split("## Curva 2")[0]
    assert "0-1999" in section and "2000-3999" in section and "8000-15999" in section


def test_drift_curve_uses_the_checkpoints(csv_path):
    section = render(csv_path).split("## Curva 3")[1].split("## Columnas")[0]
    assert [ln.split("|")[1].strip() for ln in section.splitlines()[4:] if ln.strip()] == [
        "1",
        "3",
        "6",
        "10",
        "20",
    ]
    assert "| 3 | 1.00 | 0.50 |" in section


def test_scenario_columns_show_stale_tech_budget_sources_and_rejected_attachment(csv_path):
    text = render(csv_path)
    assert "pivot: React Native en tecnologías" in text
    assert "summary|anchors ×1" in text
    assert "rechazado (413 too_much_text)" in text
    attach = line_of(text, "| 100 |")
    assert "| 1 | 1 |" in attach  # one row, one error


def test_report_is_deterministic_and_has_no_reading(csv_path):
    """Requirement: el generador es determinista y no escribe la lectura."""
    assert render(csv_path) == render(csv_path)
    assert "Lectura" not in render(csv_path)


def test_percentile_nearest_rank():
    assert report.percentile([1, 2, 3], 50) == 2
    assert report.percentile([1, 2, 3, 4], 95) == 4
    assert report.percentile([], 50) is None


def test_cli_prints_markdown_and_rejects_bad_usage(csv_path, capsys, tmp_path):
    assert report.main([str(csv_path)]) == 0
    assert "## Resumen" in capsys.readouterr().out
    assert report.main([]) == 2
    assert report.main([str(tmp_path / "missing.csv")]) == 2
