"""The samples in `samples/` must be valid `EstimationRequest` bodies (design D13)."""

import json
from pathlib import Path

import pytest

from app.domain import MAX_DESCRIPTION_CHARS, MIN_DESCRIPTION_CHARS
from app.schemas import EstimationRequest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLES = sorted(SAMPLES_DIR.glob("*.json"))
EXPECTED = {"reserva-restaurantes-es.json", "restaurant-booking-en.json"}


def test_samples_exist_and_the_transcriptions_are_gone():
    """Requirement: Muestras / los JSON de petición en español e inglés sustituyen a los .txt."""
    assert {p.name for p in SAMPLES} == EXPECTED
    assert not list(SAMPLES_DIR.glob("*.txt"))


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_sample_is_accepted_by_estimation_request(path):
    """Requirement: Endpoint de estimación / las muestras cumplen el contrato de entrada."""
    body = json.loads(path.read_text(encoding="utf-8"))
    request = EstimationRequest.model_validate(body)
    assert MIN_DESCRIPTION_CHARS <= len(request.description) <= MAX_DESCRIPTION_CHARS
    assert set(body) >= {"description", "project_type", "detail_level", "output_format"}


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_sample_is_accepted_by_the_endpoint(client, provider, path):
    """Requirement: Endpoint de estimación / las muestras se pueden enviar con curl -d @fichero."""
    response = client.post(
        "/api/v1/estimate", content=path.read_bytes(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert provider.call_count == 1


def test_both_samples_describe_the_same_project_with_the_same_options():
    """Requirement: Muestras (mismo proyecto en español e inglés)."""
    es, en = (
        json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8")) for name in sorted(EXPECTED)
    )
    for field in ("project_type", "detail_level", "output_format"):
        assert es[field] == en[field]
    assert es["description"] != en["description"]
