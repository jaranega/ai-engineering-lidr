"""Unit tests for the `app-config` capability (app/config.py)."""

import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.config import DEFAULT_MODELS, Settings, format_config_error, get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "APP_ENV",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch):
    """Remove config env vars and clear the settings cache around every test."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_settings() -> Settings:
    """Instantiate Settings ignoring any local `.env`."""
    return Settings(_env_file=None)


# --- Requirement: Configuración desde variables de entorno ---


def test_load_with_default_values(monkeypatch):
    """Requirement: Configuración desde variables de entorno / Carga con valores por defecto."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = make_settings()
    assert settings.llm_provider == "openai"
    assert settings.app_env == "development"
    assert settings.log_level == "DEBUG"
    assert settings.llm_model is None
    assert settings.anthropic_api_key is None


def test_env_var_takes_precedence_over_dotenv(monkeypatch, tmp_path):
    """Requirement: Configuración desde entorno / La variable de entorno prevalece sobre .env."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_PROVIDER=openai\nOPENAI_API_KEY=sk-file\nANTHROPIC_API_KEY=sk-ant-file\n"
    )
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    settings = Settings(_env_file=env_file)
    assert settings.llm_provider == "anthropic"


def test_dotenv_is_loaded_when_env_var_absent(tmp_path):
    """Requirement: Configuración desde variables de entorno (carga complementaria desde .env)."""
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-ant-file\n")
    settings = Settings(_env_file=env_file)
    assert settings.llm_provider == "anthropic"
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-file"


def test_unsupported_provider(monkeypatch):
    """Requirement: Configuración desde variables de entorno / Proveedor no soportado."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValidationError) as exc_info:
        make_settings()
    text = str(exc_info.value)
    assert "openai" in text
    assert "anthropic" in text
    assert "gemini" not in format_config_error(exc_info.value)


def test_invalid_log_level_is_rejected(monkeypatch):
    """Requirement: Configuración desde variables de entorno (LOG_LEVEL restringido a Literal)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        make_settings()


# --- Requirement: Modelo por defecto según el proveedor ---


def test_default_model_openai(monkeypatch):
    """Requirement: Modelo por defecto según el proveedor / Default de OpenAI."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert make_settings().effective_model == "gpt-4o-mini"
    assert DEFAULT_MODELS["openai"] == "gpt-4o-mini"


def test_default_model_anthropic(monkeypatch):
    """Requirement: Modelo por defecto según el proveedor / Default de Anthropic."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert make_settings().effective_model == "claude-haiku-4-5"
    assert DEFAULT_MODELS["anthropic"] == "claude-haiku-4-5"


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_llm_model_uses_default(monkeypatch, blank):
    """Requirement: Modelo por defecto según el proveedor / LLM_MODEL vacío usa el default."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("LLM_MODEL", blank)
    settings = make_settings()
    assert settings.llm_model is None
    assert settings.effective_model == "claude-haiku-4-5"


def test_explicit_model(monkeypatch):
    """Requirement: Modelo por defecto según el proveedor / Modelo explícito."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    assert make_settings().effective_model == "gpt-4o"


# --- Requirement: API key obligatoria para el proveedor activo ---


def test_missing_active_provider_key(monkeypatch):
    """Requirement: API key obligatoria / Falta la key del proveedor activo."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ValidationError) as exc_info:
        make_settings()
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)
    message = format_config_error(exc_info.value)
    assert "ANTHROPIC_API_KEY" in message
    # The message tells how to fix it (define in .env or change LLM_PROVIDER).
    assert ".env" in message
    assert "LLM_PROVIDER" in message


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_key_equals_missing(monkeypatch, blank):
    """Requirement: API key obligatoria / Key vacía equivale a ausente."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValidationError) as missing:
        make_settings()

    monkeypatch.setenv("OPENAI_API_KEY", blank)
    with pytest.raises(ValidationError) as blank_exc:
        make_settings()

    assert "OPENAI_API_KEY" in str(blank_exc.value)
    assert format_config_error(blank_exc.value) == format_config_error(missing.value)


def test_error_does_not_show_key_values(monkeypatch):
    """Requirement: API key obligatoria / El error no muestra valores de keys."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-visible-value")
    with pytest.raises(ValidationError) as exc_info:
        make_settings()
    assert "sk-test-visible-value" not in format_config_error(exc_info.value)
    assert "sk-test-visible-value" not in str(exc_info.value)
    assert "sk-test-visible-value" not in repr(exc_info.value)


def test_other_provider_key_absent_is_valid(monkeypatch):
    """Requirement: API key obligatoria / Key del otro proveedor ausente."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = make_settings()
    assert settings.anthropic_api_key is None


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
