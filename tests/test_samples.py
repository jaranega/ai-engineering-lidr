"""The transcription samples in `samples/` must be valid `EstimationRequest` payloads."""

from pathlib import Path

import pytest

from app.schemas import EstimationRequest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLES = sorted(SAMPLES_DIR.glob("*.txt"))


def test_samples_exist():
    """Requirement: Documentación y muestras / al menos las dos transcripciones esperadas."""
    names = {p.name for p in SAMPLES}
    assert {"landing-hubspot-blog.txt", "reserva-restaurantes-en.txt"} <= names


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_sample_is_accepted_by_estimation_request(path):
    """Requirement: Endpoint de estimación / las muestras cumplen el contrato de entrada."""
    request = EstimationRequest(transcription=path.read_text(encoding="utf-8"))
    assert request.transcription
