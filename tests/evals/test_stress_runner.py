"""cag-stress-test / Runner del stress test. In process over the app with a fake provider;
the real CLI path (real LLM) is never run.
"""

import csv
import io
from contextlib import contextmanager

import pytest

from app.config import get_settings
from app.main import app
from app.services.llm_service import LLMProviderError
from evals.stress import run as stress_run
from evals.stress.fixtures import build_pdfs
from evals.stress.run import (
    CSV_COLUMNS,
    OBSERVATION_FIELDS,
    StressConfig,
    csv_writer,
    run_with_client,
)
from tests.conftest import FakeProvider, make_settings

SECRET_TRANSCRIPT_WORDS = ("Nimbus lanza", "Lumora")


@pytest.fixture(scope="module")
def pdf_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("stress_pdfs")
    build_pdfs.build_all(directory)
    return directory


@pytest.fixture
def rig(make_session_client):
    def factory(provider=None, debug=True, **settings):
        client, provider, _, store = make_session_client(provider=provider, **settings)
        cfg = make_settings(debug_endpoints=debug, **settings)
        app.dependency_overrides[get_settings] = lambda: cfg
        return client, provider, store

    return factory


def read_csv(path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def config(pdf_dir, **overrides) -> StressConfig:
    base = dict(
        scenarios=("growing",),
        attachment_sizes=(0, 5),
        repeats=2,
        turns=3,
        fixtures_dir=pdf_dir,
    )
    return StressConfig(**{**base, **overrides})


def test_in_process_run_with_a_fake_provider_writes_all_rows_and_columns(rig, pdf_dir, tmp_path):
    """Scenario: Ejecución en proceso con proveedor falso (10 filas, todas las columnas)."""
    client, *_ = rig()
    out = tmp_path / "results.csv"
    stream = io.StringIO()
    report = run_with_client(client, config(pdf_dir), out, stream)
    rows = read_csv(out)
    assert len(rows) == 10 and report.rows == 10 and report.errors == 0
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert [r["scenario"] for r in rows].count("growing") == 6
    assert [r["scenario"] for r in rows].count("attachments") == 4
    # One session per repetition: 2 growing + 4 attachment sessions, distinct short ids.
    growing = [r for r in rows if r["scenario"] == "growing"]
    assert {(r["repeat"], r["session_id"]) for r in growing}.__len__() == 2
    assert len({r["session_id"] for r in rows}) == 6 and len(report.sessions) == 6
    assert all(len(r["session_id"]) == 8 for r in rows)
    for row in rows:
        assert all(row[name] != "" for name in OBSERVATION_FIELDS if name != "cost_usd")
        assert row["error"] == ""
        assert row["latency_budget"] != "" and row["memory_drift"] != ""
    assert [r["turn_index"] for r in growing] == ["1", "2", "3", "1", "2", "3"]
    assert [r["attachment_kb"] for r in rows if r["scenario"] == "attachments"] == [
        "0",
        "0",
        "5",
        "5",
    ]
    assert all(r["attachment_markers_recalled"] != "" for r in rows[6:])
    assert all(r["attachment_markers_recalled"] == "" for r in growing)
    assert all(r["stale_tech"] == "" and r["budget_30k_present"] == "" for r in growing)


def test_attachment_row_records_attachment_chars(rig, pdf_dir, tmp_path):
    client, *_ = rig()
    out = tmp_path / "r.csv"
    run_with_client(client, config(pdf_dir, scenarios=(), attachment_sizes=(5,), repeats=1), out)
    (row,) = read_csv(out)
    assert 4250 <= int(row["attachments_total_chars"]) <= 5750
    assert int(row["enriched_transcript_chars"]) > int(row["attachments_total_chars"])


def test_pivot_and_contradiction_fill_their_own_columns(rig, pdf_dir, tmp_path):
    client, *_ = rig()
    out = tmp_path / "r.csv"
    run_with_client(
        client,
        config(pdf_dir, scenarios=("pivot", "contradiction"), attachment_sizes=(), repeats=1),
        out,
    )
    rows = read_csv(out)
    pivot = [r for r in rows if r["scenario"] == "pivot"]
    contra = [r for r in rows if r["scenario"] == "contradiction"]
    assert all(r["stale_tech"] in {"0", "1"} for r in pivot)
    assert all(r["budget_30k_present"] == "" for r in pivot)
    assert all(r["budget_30k_present"] in {"0", "1"} for r in contra)
    assert all(r["budget_80k_present"] in {"0", "1"} for r in contra)


def test_csv_and_output_never_contain_transcripts_or_responses(rig, pdf_dir, tmp_path):
    """Requirement: el runner no escribe transcripciones ni respuestas."""
    client, *_ = rig()
    out = tmp_path / "r.csv"
    stream = io.StringIO()
    report = run_with_client(client, config(pdf_dir, repeats=1), out, stream)
    text = (
        out.read_text(encoding="utf-8")
        + stream.getvalue()
        + stress_run.format_summary(report, config(pdf_dir))
    )
    assert "Nimbus" not in text and "plataforma" not in text
    assert "Clinic" not in text and "booking" not in text.lower()


def test_each_row_is_flushed_before_the_next_turn(tmp_path):
    """Requirement: una fila al CSV antes de seguir (un corte no pierde lo medido)."""
    out = tmp_path / "r.csv"
    with csv_writer(out) as write:
        write({"scenario": "growing", "repeat": 1})
        assert len(read_csv(out)) == 1  # readable while the file is still open


def test_debug_disabled_exits_2_naming_debug_endpoints(rig, pdf_dir, tmp_path, monkeypatch, capsys):
    """Scenario: Depuración desactivada en el servidor."""
    client, provider, _ = rig(debug=False)

    @contextmanager
    def fake_http(url):
        yield client

    monkeypatch.setattr(stress_run, "http_client", fake_http)
    code = stress_run.main(
        [
            "--http",
            "http://localhost:8000",
            "--scenarios",
            "growing",
            "--attachment-sizes",
            "0",
            "--turns",
            "2",
            "--repeats",
            "1",
            "--output",
            str(tmp_path / "r.csv"),
        ]
    )
    assert code == 2
    assert "DEBUG_ENDPOINTS" in capsys.readouterr().err
    assert provider.call_count == 0  # no paid turn before the probe


def test_cli_in_process_runs_and_exits_0(rig, pdf_dir, tmp_path, monkeypatch, capsys):
    client, *_ = rig()

    @contextmanager
    def fake_in_process(sessions):
        yield client

    monkeypatch.setattr(stress_run, "in_process_client", fake_in_process)
    monkeypatch.setattr(build_pdfs, "FIXTURES_DIR", pdf_dir)
    monkeypatch.setattr(stress_run.StressConfig, "fixtures_dir", pdf_dir, raising=False)
    out = tmp_path / "r.csv"
    code = stress_run.main(
        ["--scenarios", "growing", "--attachment-sizes", "0", "--turns", "2", "--repeats", "1",
         "--output", str(out)]
    )  # fmt: skip
    assert code == 0 and len(read_csv(out)) == 3
    assert "Rows: 3" in capsys.readouterr().out


def test_cost_cap_stops_after_the_first_row_and_says_so(rig, pdf_dir, tmp_path):
    """Scenario: Tope de coste (0.002 USD por turno con tope de 0.001)."""
    client, *_ = rig(provider=FakeProvider(input_tokens=10_000, output_tokens=1_000))
    out = tmp_path / "r.csv"
    report = run_with_client(client, config(pdf_dir, max_cost_usd=0.001), out)
    assert len(read_csv(out)) == 1 and report.rows == 1
    assert report.stopped_by_cost_cap and report.stopped
    assert "STOPPED" in stress_run.format_summary(report, config(pdf_dir, max_cost_usd=0.001))


def test_cost_below_the_cap_does_not_stop(rig, pdf_dir, tmp_path):
    client, *_ = rig()
    report = run_with_client(
        client, config(pdf_dir, max_cost_usd=5.0, attachment_sizes=()), tmp_path / "r.csv"
    )
    assert not report.stopped and report.rows == 6


def test_turn_error_is_recorded_and_abandons_only_that_conversation(rig, pdf_dir, tmp_path):
    """Requirement: un error en un turno se registra y abandona la conversación, no la
    ejecución.
    """
    client, provider, _ = rig(provider=FakeProvider(error=LLMProviderError("openai")))
    out = tmp_path / "r.csv"
    report = run_with_client(client, config(pdf_dir, attachment_sizes=()), out)
    rows = read_csv(out)
    assert len(rows) == 2 and report.errors == 2  # first turn of each repetition only
    assert all("502" in r["error"] for r in rows)


def test_5xx_turn_adds_a_conservative_estimate_and_the_capped_run_goes_on(rig, pdf_dir, tmp_path):
    """A failed turn that may have reached the LLM does not stop a capped run: it adds
    `estimate_attempts x` the dearest turn so far (0.005 USD before any) to the total.
    """
    client, *_ = rig(provider=FakeProvider(error=LLMProviderError("openai")))
    out = tmp_path / "r.csv"
    cfg = config(pdf_dir, attachment_sizes=(), max_cost_usd=5.0, estimate_attempts=3)
    report = run_with_client(client, cfg, out)
    rows = read_csv(out)
    assert len(rows) == 2 and report.errors == 2 and not report.stopped
    assert all(r["cost_usd"] == "" and float(r["cost_estimated_usd"]) == 0.015 for r in rows)
    assert report.estimated_cost_usd == pytest.approx(0.03)
    assert report.total_cost_usd == pytest.approx(0.03)
    assert "of which estimated: $0.0300" in stress_run.format_summary(report, cfg)


def test_estimate_uses_the_dearest_turn_seen_so_far(pdf_dir):
    runner = stress_run.StressRunner(None, config(pdf_dir, estimate_attempts=3), lambda _: None)
    for cost in (0.001, 0.004, 0.002):
        row = stress_run._empty_row("growing", 1, None)
        row["cost_usd"] = cost
        runner._emit(stress_run._TurnResult(row))
    failed = stress_run._empty_row("growing", 1, None)
    failed["error"] = "turn: HTTP 502"
    runner._emit(stress_run._TurnResult(failed, estimate_cost=True, abandon=True))
    assert failed["cost_estimated_usd"] == pytest.approx(0.012)
    assert runner.report.total_cost_usd == pytest.approx(0.019)
    assert runner.report.estimated_cost_usd == pytest.approx(0.012)


def test_successful_turn_with_unknown_cost_still_stops_a_capped_run(pdf_dir):
    """A 200 without `cost_usd` is a pricing gap, not a failure: the run stops."""
    runner = stress_run.StressRunner(None, config(pdf_dir, max_cost_usd=5.0), lambda _: None)
    row = stress_run._empty_row("growing", 1, None)
    with pytest.raises(stress_run._CostCapReached):
        runner._emit(stress_run._TurnResult(row, cost_unknown=True))
    assert runner.report.stopped_by_unknown_cost and row["cost_estimated_usd"] is None


def test_100kb_pdf_is_recorded_as_a_413_error_row(rig, pdf_dir, tmp_path):
    """Requirement: el de 100 KB se registra como fila de error (413 too_much_text)."""
    client, *_ = rig()
    out = tmp_path / "r.csv"
    report = run_with_client(
        client, config(pdf_dir, scenarios=(), attachment_sizes=(100,), repeats=1), out
    )
    (row,) = read_csv(out)
    assert "413" in row["error"] and "too_much_text" in row["error"]
    assert row["attachment_kb"] == "100" and report.errors == 1
    assert not report.stopped


def test_scenario_and_size_arguments_are_validated(capsys):
    for argv in (["--scenarios", "attachments"], ["--attachment-sizes", "7"], ["--repeats", "0"]):
        with pytest.raises(SystemExit) as caught:
            stress_run.main(argv)
        assert caught.value.code == 2
