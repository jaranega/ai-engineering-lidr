"""app-config: sessions and attachments settings (Configuración de sesiones y adjuntos)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, format_config_error
from app.services.session_service import get_session_store

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(monkeypatch, **env) -> Settings:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    return Settings(_env_file=None)


def test_defaults(monkeypatch):
    """Scenario: Valores por defecto."""
    settings = load(monkeypatch)
    assert settings.max_conversation_turns == 6
    assert settings.max_sessions == 1000
    assert settings.max_attachments == 5
    assert settings.max_attachment_bytes == 10_485_760
    assert settings.max_attachment_chars == 60_000


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("MAX_CONVERSATION_TURNS", 0),
        ("MAX_CONVERSATION_TURNS", 51),
        ("MAX_SESSIONS", 0),
        ("MAX_SESSIONS", 100_001),
        ("MAX_ATTACHMENTS", -1),
        ("MAX_ATTACHMENTS", 21),
        ("MAX_ATTACHMENT_BYTES", 1023),
        ("MAX_ATTACHMENT_BYTES", 52_428_801),
        ("MAX_ATTACHMENT_CHARS", 999),
        ("MAX_ATTACHMENT_CHARS", 200_001),
    ],
)
def test_out_of_range_values_name_the_variable_without_secrets(monkeypatch, var, value):
    """Scenario: Ventana fuera de rango (y el resto de rangos)."""
    with pytest.raises(ValidationError) as caught:
        load(monkeypatch, **{var: value})
    message = format_config_error(caught.value)
    assert var in message
    assert "sk-test" not in message


@pytest.mark.parametrize(
    ("var", "value", "attribute"),
    [
        ("MAX_CONVERSATION_TURNS", 1, "max_conversation_turns"),
        ("MAX_CONVERSATION_TURNS", 50, "max_conversation_turns"),
        ("MAX_SESSIONS", 100_000, "max_sessions"),
        ("MAX_ATTACHMENTS", 0, "max_attachments"),
        ("MAX_ATTACHMENTS", 20, "max_attachments"),
        ("MAX_ATTACHMENT_BYTES", 1024, "max_attachment_bytes"),
        ("MAX_ATTACHMENT_CHARS", 200_000, "max_attachment_chars"),
    ],
)
def test_range_limits_are_inclusive(monkeypatch, var, value, attribute):
    assert getattr(load(monkeypatch, **{var: value}), attribute) == value


def test_env_example_documents_the_five_variables_with_their_defaults():
    """Scenario: Variables documentadas."""
    content = (REPO_ROOT / ".env.example").read_text()
    for line in (
        "# MAX_CONVERSATION_TURNS=6",
        "# MAX_SESSIONS=1000",
        "# MAX_ATTACHMENTS=5",
        "# MAX_ATTACHMENT_BYTES=10485760",
        "# MAX_ATTACHMENT_CHARS=60000",
    ):
        assert line in content


def test_the_process_store_is_built_once_from_the_settings(monkeypatch):
    """Requirement: el almacén se construye una sola vez con `MAX_SESSIONS` y la ventana."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MAX_SESSIONS", "2")
    monkeypatch.setenv("MAX_CONVERSATION_TURNS", "3")
    store = get_session_store()
    assert get_session_store() is store
    assert store.max_turns == 3
    ids = [store.create().session_id for _ in range(3)]
    assert len(store) == 2 and ids[0] not in {s for s in ids[1:]}


def test_the_docx_limits_are_code_constants_not_settings():
    for name in ("max_docx_uncompressed_bytes", "max_docx_entries"):
        assert name not in Settings.model_fields
