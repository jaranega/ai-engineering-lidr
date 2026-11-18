"""Offline evals of the estimator: golden dataset, deterministic metrics and runner.

Run with `uv run python -m evals.run` (real LLM calls, manual only). `evals.run` is not
re-exported here so that importing the dataset or the metrics never loads the FastAPI app.
`app` never imports this package.
"""

from evals.dataset import DEFAULT_DATASET_PATH, DatasetError, GoldenCase, load_dataset
from evals.metrics import (
    CaseOutcome,
    ContentRecallMetric,
    CostBoundsMetric,
    MetricResult,
    SchemaAdherenceMetric,
    TierMatchMetric,
    run_all_metrics,
)

__all__ = [
    "DEFAULT_DATASET_PATH",
    "CaseOutcome",
    "ContentRecallMetric",
    "CostBoundsMetric",
    "DatasetError",
    "GoldenCase",
    "MetricResult",
    "SchemaAdherenceMetric",
    "TierMatchMetric",
    "load_dataset",
    "run_all_metrics",
]
