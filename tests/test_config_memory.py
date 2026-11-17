"""app-config: memory compression settings (Configuración de la compresión de la memoria)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, format_config_error
from app.services import memory as memory_module
from app.services.memory import MemoryCompression, get_memory_compression
from app.services.session_service import get_session_store
from app.sessions.compression import detect_anchor
from tests.conftest import FakeProvider, make_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(monkeypatch, **env) -> Settings:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    return Settings(_env_file=None)


def test_defaults(monkeypatch):
    """Scenario: Valores por defecto."""
    settings = load(monkeypatch)
    assert settings.anchor_detection_mode == "heuristic"
    assert settings.compression_model is None
    assert settings.max_anchored_turns == 3


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("MAX_ANCHORED_TURNS", 11),
        ("MAX_ANCHORED_TURNS", -1),
        ("ANCHOR_DETECTION_MODE", "regex"),
    ],
)
def test_out_of_range_values_name_the_variable_without_secrets(monkeypatch, var, value):
    """Scenario: Tope de anclas fuera de rango."""
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, **{var: value})
    message = format_config_error(caught.value)
    assert var in message
    assert "sk-test" not in message


@pytest.mark.parametrize("value", [0, 1, 10])
def test_anchor_cap_range_is_inclusive_and_zero_disables_anchors(monkeypatch, value):
    assert load(monkeypatch, MAX_ANCHORED_TURNS=value).max_anchored_turns == value


@pytest.mark.parametrize("mode", ["heuristic", "llm"])
def test_detection_modes_are_accepted(monkeypatch, mode):
    assert load(monkeypatch, ANCHOR_DETECTION_MODE=mode).anchor_detection_mode == mode


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_compression_model_is_none(monkeypatch, value):
    """Scenario: Modelo de compresión vacío."""
    assert load(monkeypatch, COMPRESSION_MODEL=value).compression_model is None


def test_compression_model_with_its_key_is_valid(monkeypatch):
    settings = load(
        monkeypatch, COMPRESSION_MODEL="claude-haiku-4-5", ANTHROPIC_API_KEY="sk-ant-test"
    )
    assert settings.compression_model == "claude-haiku-4-5"


def test_compression_model_without_its_key_names_the_variable_and_hides_keys(monkeypatch):
    """Scenario: Modelo de compresión sin key."""
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, PRIMARY_MODEL="gpt-4o-mini", COMPRESSION_MODEL="claude-haiku-4-5")
    message = format_config_error(caught.value)
    assert "COMPRESSION_MODEL" in message
    assert "sk-test" not in message


def test_unknown_compression_model_names_the_variable(monkeypatch):
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, COMPRESSION_MODEL="not-a-model")
    assert "COMPRESSION_MODEL" in format_config_error(caught.value)


def test_env_example_documents_the_three_variables():
    """Scenario: Variables documentadas."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in ("ANCHOR_DETECTION_MODE", "COMPRESSION_MODEL", "MAX_ANCHORED_TURNS"):
        assert var in text


def test_session_store_is_built_with_the_anchor_cap(monkeypatch):
    """Requirement: el almacén se construye con MAX_ANCHORED_TURNS como max_anchors."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MAX_ANCHORED_TURNS", "4")
    store = get_session_store()
    assert store.max_anchors == 4
    assert store.create().history.max_anchors == 4


def test_default_memory_compression_uses_the_heuristic_detector_and_three_anchors():
    """Scenario: Modo heurístico por defecto."""
    compression = get_memory_compression(FakeProvider(), make_settings())
    assert isinstance(compression, MemoryCompression)
    assert compression.detect is detect_anchor
    assert compression.max_anchors == 3


def test_llm_mode_builds_the_classifier_and_follows_the_cap():
    compression = get_memory_compression(
        FakeProvider(), make_settings(anchor_detection_mode="llm", max_anchored_turns=5)
    )
    assert isinstance(compression.detect, memory_module.LLMAnchorClassifier)
    assert compression.max_anchors == 5
