"""Unit tests for the `app-config` capability (app/config.py)."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

from app.config import Deployment, Settings, format_config_error, get_settings
from app.main import app
from tests.conftest import CONFIG_ENV_VARS, make_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_VARS = CONFIG_ENV_VARS


def settings_error(**kwargs) -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(**kwargs)
    return exc_info.value


# --- Requirement: Configuración desde variables de entorno ---


def test_load_with_default_values(monkeypatch):
    """Requirement: Configuración desde variables de entorno / Carga con valores por defecto."""
    monkeypatch.delenv("REDIS_URL")  # the autouse fixture empties it; here we want the default
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(_env_file=None)
    assert settings.primary_model == "gpt-4o-mini"
    assert settings.fallback_model == "claude-haiku-4-5"
    assert settings.llm_timeout == 60
    assert settings.llm_retries == 2
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cache_ttl == 86400
    assert settings.app_env == "development"
    assert settings.log_level == "DEBUG"
    assert settings.anthropic_api_key is None
    assert settings.primary == Deployment("gpt-4o-mini", "openai")


def test_env_var_takes_precedence_over_dotenv(monkeypatch, tmp_path):
    """Requirement: Configuración desde entorno / La variable de entorno prevalece sobre .env."""
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "PRIMARY_MODEL=gpt-4o-mini\nOPENAI_API_KEY=sk-file\nANTHROPIC_API_KEY=sk-ant-file\n"
    )
    monkeypatch.setenv("PRIMARY_MODEL", "claude-haiku-4-5")
    settings = Settings(_env_file=env_file)
    assert settings.primary_model == "claude-haiku-4-5"
    assert settings.primary.provider == "anthropic"


def test_dotenv_is_loaded_when_env_var_absent(tmp_path):
    """Requirement: Configuración desde variables de entorno (carga complementaria desde .env)."""
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "PRIMARY_MODEL=claude-haiku-4-5\nFALLBACK_MODEL=\nANTHROPIC_API_KEY=sk-ant-file\n"
    )
    settings = Settings(_env_file=env_file)
    assert settings.primary_model == "claude-haiku-4-5"
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-file"


@pytest.mark.parametrize(
    ("var", "value", "field"),
    [
        ("LLM_TIMEOUT", "0", "LLM_TIMEOUT"),
        ("LLM_RETRIES", "-1", "LLM_RETRIES"),
        ("CACHE_TTL", "0", "CACHE_TTL"),
    ],
)
def test_invalid_limits_name_the_variable(monkeypatch, var, value, field):
    """Requirement: Configuración desde variables de entorno / Scenario: Límites inválidos."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert field in format_config_error(exc_info.value)


def test_structured_output_and_moderation_defaults(monkeypatch):
    """Requirement: Configuración de la salida estructurada / Scenario: Valores por defecto."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(_env_file=None)
    assert settings.structured_max_retries == 2
    assert settings.moderation_enabled is True


@pytest.mark.parametrize("value", ["9", "-1", "6"])
def test_structured_max_retries_out_of_range_names_the_variable(monkeypatch, value):
    """Requirement: Configuración de la salida estructurada / Reintentos fuera de rango."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("STRUCTURED_MAX_RETRIES", value)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    message = format_config_error(exc_info.value)
    assert "STRUCTURED_MAX_RETRIES" in message
    assert "sk-secret-value" not in message


@pytest.mark.parametrize("value", ["0", "5"])
def test_structured_max_retries_limits_are_inclusive(monkeypatch, value):
    """Requirement: Configuración de la salida estructurada (rango 0..5 inclusivo)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("STRUCTURED_MAX_RETRIES", value)
    assert Settings(_env_file=None).structured_max_retries == int(value)


def test_moderation_can_be_disabled_from_the_environment(monkeypatch):
    """Requirement: Configuración de la moderación / Scenario: Moderación desactivada."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MODERATION_ENABLED", "false")
    assert Settings(_env_file=None).moderation_enabled is False


def test_env_example_documents_the_structured_output_and_moderation_variables():
    """Requirement: Configuración de la salida estructurada (.env.example las documenta)."""
    content = (REPO_ROOT / ".env.example").read_text()
    assert "STRUCTURED_MAX_RETRIES" in content and "MODERATION_ENABLED" in content


def test_zero_retries_is_valid():
    """Requirement: Configuración desde variables de entorno (LLM_RETRIES >= 0)."""
    assert make_settings(llm_retries=0).llm_retries == 0


def test_retired_variables_are_ignored(tmp_path):
    """Requirement: Configuración desde variables de entorno / Scenario: Variables retiradas."""
    env_file = tmp_path / "old.env"
    env_file.write_text(
        "LLM_PROVIDER=anthropic\nLLM_MODEL=claude-haiku-4-5\nOPENAI_API_KEY=sk-file\n"
    )
    settings = Settings(_env_file=env_file)
    assert settings.primary_model == "gpt-4o-mini"
    assert settings.fallback_model == "claude-haiku-4-5"
    assert not hasattr(settings, "llm_provider")
    assert not hasattr(settings, "llm_model")


def test_invalid_log_level_is_rejected(monkeypatch):
    """Requirement: Configuración desde variables de entorno (LOG_LEVEL restringido a Literal)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- Requirement: Modelos soportados y su proveedor ---


def test_unsupported_provider_model_names_variable_and_supported_providers():
    """Requirement: Modelos soportados / Scenario: Modelo de proveedor no soportado."""
    message = format_config_error(settings_error(primary_model="gemini-2.0-flash"))
    assert "PRIMARY_MODEL" in message
    assert "openai" in message
    assert "anthropic" in message


def test_unsupported_fallback_model_names_its_variable():
    """Requirement: Modelos soportados y su proveedor (también aplica a FALLBACK_MODEL)."""
    message = format_config_error(settings_error(fallback_model="gemini-2.0-flash"))
    assert "FALLBACK_MODEL" in message
    assert "anthropic" in message


def test_unknown_model_names_variable():
    """Requirement: Modelos soportados y su proveedor (modelo no reconocido)."""
    message = format_config_error(settings_error(primary_model="not-a-real-model"))
    assert "PRIMARY_MODEL" in message


def test_fallback_equal_to_primary_is_rejected():
    """Requirement: Modelos soportados / Scenario: Respaldo igual al primario."""
    message = format_config_error(
        settings_error(primary_model="gpt-4o-mini", fallback_model="gpt-4o-mini")
    )
    assert "FALLBACK_MODEL" in message


def test_fallback_equal_to_primary_after_prefix_resolution_is_rejected():
    """Requirement: Modelos soportados (la comparación se hace tras resolver el prefijo)."""
    message = format_config_error(
        settings_error(primary_model="openai/gpt-4o-mini", fallback_model="gpt-4o-mini")
    )
    assert "FALLBACK_MODEL" in message


def test_provider_is_deduced_without_network_and_prefix_is_stripped():
    """Requirement: Modelos soportados (proveedor deducido; `Deployment` sin prefijo)."""
    settings = make_settings(
        primary_model="anthropic/claude-haiku-4-5",
        fallback_model="gpt-4o-mini",
        anthropic_api_key="sk-ant-fake",
    )
    assert settings.primary == Deployment("claude-haiku-4-5", "anthropic")
    assert settings.fallback == Deployment("gpt-4o-mini", "openai")


@pytest.mark.parametrize(
    ("primary_model", "expected"),
    [
        ("gpt-4o-mini", Deployment("claude-haiku-4-5", "anthropic")),
        ("claude-haiku-4-5", Deployment("gpt-4o-mini", "openai")),
    ],
    ids=["openai-primary", "anthropic-primary"],
)
def test_unset_fallback_defaults_to_other_provider_model(primary_model, expected):
    """Requirement: Modelos soportados / Scenario: Respaldo por defecto según el primario."""
    settings = make_settings(
        primary_model=primary_model,
        openai_api_key="sk-fake-openai",
        anthropic_api_key="sk-ant-fake",
    )
    assert settings.fallback_model == expected.model
    assert settings.fallback == expected


def test_defined_fallback_equal_to_primary_still_errors():
    """Requirement: Modelos soportados (un FALLBACK_MODEL definido igual al primario falla)."""
    message = format_config_error(
        settings_error(
            primary_model="claude-haiku-4-5",
            fallback_model="claude-haiku-4-5",
            anthropic_api_key="sk-ant-fake",
        )
    )
    assert "FALLBACK_MODEL" in message


# --- Requirement: API keys según los modelos configurados ---


def test_missing_primary_key_names_variable_and_primary_model():
    """Requirement: API keys / Scenario: Falta la key del primario."""
    error = settings_error(primary_model="claude-haiku-4-5", fallback_model="")
    assert "ANTHROPIC_API_KEY" in str(error)
    message = format_config_error(error)
    assert "ANTHROPIC_API_KEY" in message
    assert "PRIMARY_MODEL" in message
    assert ".env" in message  # tells how to fix it
    assert "sk-fake-openai" not in message


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_key_equals_missing(blank):
    """Requirement: API keys / Scenario: Key vacía equivale a ausente."""
    missing = settings_error(openai_api_key=None)
    blank_error = settings_error(openai_api_key=blank)
    assert "OPENAI_API_KEY" in str(blank_error)
    assert format_config_error(blank_error) == format_config_error(missing)


def test_error_does_not_show_key_values():
    """Requirement: API keys / Scenario: El error no muestra valores de keys."""
    error = settings_error(primary_model="claude-haiku-4-5", openai_api_key="sk-test-visible-value")
    assert "sk-test-visible-value" not in format_config_error(error)
    assert "sk-test-visible-value" not in str(error)
    assert "sk-test-visible-value" not in repr(error)


def test_fallback_without_provider_key_is_disabled():
    """Requirement: API keys / Scenario: Sin key del respaldo (configuración válida)."""
    settings = make_settings()  # only OPENAI_API_KEY
    assert settings.fallback is None
    assert "ANTHROPIC_API_KEY" in settings.fallback_disabled_reason
    assert settings.fallback_model == "claude-haiku-4-5"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_fallback_model_disables_fallback(blank):
    """Requirement: API keys / Scenario: Fallback desactivado a propósito."""
    settings = make_settings(fallback_model=blank, anthropic_api_key="sk-ant-fake")
    assert settings.fallback_model is None
    assert settings.fallback is None
    assert "FALLBACK_MODEL" in settings.fallback_disabled_reason


def test_fallback_is_active_with_its_key():
    """Requirement: API keys (la key del respaldo opcional activa el fallback)."""
    settings = make_settings(anthropic_api_key="sk-ant-fake")
    assert settings.fallback == Deployment("claude-haiku-4-5", "anthropic")
    assert settings.fallback_disabled_reason is None


def test_api_key_accessor_returns_raw_value_only_when_asked():
    """Requirement: Secrets protegidos (la key en claro solo sale por `api_key`)."""
    settings = make_settings(anthropic_api_key="sk-ant-fake")
    assert settings.api_key("openai") == "sk-fake-openai"
    assert settings.api_key("anthropic") == "sk-ant-fake"
    assert make_settings().api_key("anthropic") is None


@pytest.fixture
def _start_app(monkeypatch):
    """Run the lifespan and return the structlog events it emitted.

    `configure_logging` (called by the lifespan) would replace the structlog processors that
    `capture_logs` installs, so it is neutralized here; its behavior has its own tests.
    """
    monkeypatch.setattr("app.main.configure_logging", lambda settings: None)

    def start() -> list[dict]:
        with capture_logs() as logs, TestClient(app):
            pass
        return logs

    return start


def test_startup_without_fallback_key_warns(monkeypatch, _start_app):
    """Requirement: API keys / Scenario: Sin key del respaldo (arranque + warning)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    logs = _start_app()
    warnings = [e for e in logs if e["event"] == "llm_fallback_disabled"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert "ANTHROPIC_API_KEY" in warnings[0]["reason"]


def test_startup_with_blank_fallback_model_warns(monkeypatch, _start_app):
    """Requirement: API keys / Scenario: Fallback desactivado a propósito (warning)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("FALLBACK_MODEL", "  ")
    warnings = [e for e in _start_app() if e["event"] == "llm_fallback_disabled"]
    assert len(warnings) == 1
    assert "FALLBACK_MODEL" in warnings[0]["reason"]


def test_startup_with_fallback_active_does_not_warn(monkeypatch, _start_app):
    """Requirement: API keys (con respaldo activo no hay warning)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    assert not [e for e in _start_app() if e["event"] == "llm_fallback_disabled"]


# --- Requirement: Secrets protegidos ---


def test_settings_representation_masks_keys(monkeypatch):
    """Requirement: Secrets protegidos / Representación de la configuración."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-anthropic")
    settings = make_settings()
    assert isinstance(settings.openai_api_key, SecretStr)
    assert isinstance(settings.anthropic_api_key, SecretStr)
    rendered = [
        str(settings),
        repr(settings),
        str(settings.model_dump()),
        settings.model_dump_json(),
    ]
    for text in rendered:
        assert "sk-secret-openai" not in text
        assert "sk-secret-anthropic" not in text
    assert "**********" in str(settings)
    assert "**********" in repr(settings)


def test_dotenv_is_gitignored_but_example_is_not():
    """Requirement: Secrets protegidos / .env fuera del control de versiones."""
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO_ROOT)
    assert ignored.returncode == 0, ".env must be ignored by git"
    example = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO_ROOT)
    assert example.returncode == 1, ".env.example must not be ignored by git"


def test_env_example_lists_all_variables():
    """Requirement: Secrets protegidos (.env.example con todas las variables)."""
    content = (REPO_ROOT / ".env.example").read_text()
    for var in ENV_VARS:
        assert var in content
    for secret_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        line = next(ln for ln in content.splitlines() if ln.startswith(f"{secret_var}="))
        assert line.strip() == f"{secret_var}="


# --- get_settings cache ---


def test_get_settings_is_cached(monkeypatch):
    """Requirement: Configuración desde variables de entorno (get_settings cacheado, D4)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_settings() is get_settings()
