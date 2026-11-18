"""app-config: Actor-Critic-Boss settings (Configuración de la revisión Actor-Critic-Boss)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, format_config_error
from app.services.session_service import get_session_service
from tests.conftest import FakeModerator, FakeProvider, make_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(monkeypatch, **env) -> Settings:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    return Settings(_env_file=None)


def test_defaults(monkeypatch):
    """Scenario: Valores por defecto."""
    settings = load(monkeypatch)
    assert settings.critic_model is None
    assert settings.boss_max_iterations == 3


@pytest.mark.parametrize("value", [0, 6, -1])
def test_iterations_out_of_range_name_the_variable_without_secrets(monkeypatch, value):
    """Scenario: Iteraciones fuera de rango."""
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, BOSS_MAX_ITERATIONS=value)
    message = format_config_error(caught.value)
    assert "BOSS_MAX_ITERATIONS" in message
    assert "sk-test" not in message


@pytest.mark.parametrize("value", [1, 2, 5])
def test_iterations_range_is_inclusive(monkeypatch, value):
    assert load(monkeypatch, BOSS_MAX_ITERATIONS=value).boss_max_iterations == value


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_critic_model_is_none(monkeypatch, value):
    """Scenario: Modelo del Critic vacío."""
    assert load(monkeypatch, CRITIC_MODEL=value).critic_model is None


def test_critic_model_with_its_key_is_valid(monkeypatch):
    settings = load(monkeypatch, CRITIC_MODEL="claude-haiku-4-5", ANTHROPIC_API_KEY="sk-ant-test")
    assert settings.critic_model == "claude-haiku-4-5"


def test_critic_model_without_its_key_names_the_variable_and_hides_keys(monkeypatch):
    """Scenario: Modelo del Critic sin key."""
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, PRIMARY_MODEL="gpt-4o-mini", CRITIC_MODEL="claude-haiku-4-5")
    message = format_config_error(caught.value)
    assert "CRITIC_MODEL" in message
    assert "sk-test" not in message


def test_unknown_critic_model_names_the_variable(monkeypatch):
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, CRITIC_MODEL="not-a-model")
    assert "CRITIC_MODEL" in format_config_error(caught.value)


def test_env_example_documents_the_two_variables():
    """Scenario: Variables documentadas."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in ("CRITIC_MODEL", "BOSS_MAX_ITERATIONS"):
        assert var in text
    commented = [line for line in text.splitlines() if "BOSS_MAX_ITERATIONS" in line]
    assert any(line.lstrip().startswith("#") and "3" in line for line in commented)


def test_session_service_is_built_with_the_review_settings():
    """Requirement: la configuración llega al servicio de sesiones (iteraciones y modelo)."""
    settings = make_settings(
        boss_max_iterations=2, critic_model="claude-haiku-4-5", anthropic_api_key="sk-ant-fake"
    )
    service = get_session_service(
        store=None,  # type: ignore[arg-type]
        provider=FakeProvider(),
        settings=settings,
        moderator=FakeModerator(),
        memory=None,  # type: ignore[arg-type]
    )
    assert service._review.max_iterations == 2
    assert service._review.critic_target is not None
    assert service._review.critic_target.model == "claude-haiku-4-5"


def test_session_service_without_critic_model_uses_the_primary():
    service = get_session_service(
        store=None,  # type: ignore[arg-type]
        provider=FakeProvider(),
        settings=make_settings(),
        moderator=FakeModerator(),
        memory=None,  # type: ignore[arg-type]
    )
    assert service._review.max_iterations == 3
    assert service._review.critic_target is None
