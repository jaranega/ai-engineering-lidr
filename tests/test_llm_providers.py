"""Unit tests for the `llm-provider` capability (app/services/llm_service.py)."""

import logging
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from app.config import DEFAULT_MODELS, Settings
from app.services import llm_service
from app.services.llm_service import (
    MAX_TOKENS,
    AnthropicProvider,
    LLMProviderError,
    OpenAIProvider,
    build_provider,
    get_llm_provider,
)

LOGGER = "app.services.llm_service"
SECRET_MSG = "sk-secret-in-msg"


def make_settings(**kwargs) -> Settings:
    kwargs.setdefault("openai_api_key", "sk-fake-openai")
    return Settings(_env_file=None, **kwargs)


def use_settings(monkeypatch, **kwargs) -> None:
    """Make `get_llm_provider()` read these settings instead of the cached, `.env`-based ones."""
    settings = make_settings(**kwargs)
    monkeypatch.setattr(llm_service, "get_settings", lambda: settings)


# --- Stubs ---


class OpenAIStub:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self._response = response
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kwargs = kw
        if self._error:
            raise self._error
        return self._response


class AnthropicStub:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self._response = response
        self._error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.kwargs = kw
        if self._error:
            raise self._error
        return self._response


def openai_response(content="hello", finish_reason="stop", prompt=11, completion=22, usage=True):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
        if usage
        else None,
    )


def anthropic_response(blocks=None, stop_reason="end_turn", input_tokens=33, output_tokens=44):
    if blocks is None:
        blocks = [SimpleNamespace(type="text", text="hello")]
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _req() -> httpx2.Request:
    return httpx2.Request("POST", "https://x")


def _resp(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=_req())


OPENAI_ERRORS = [
    openai.APIConnectionError(request=_req()),
    openai.APITimeoutError(request=_req()),
    openai.AuthenticationError(SECRET_MSG, response=_resp(401), body=None),
    openai.RateLimitError(SECRET_MSG, response=_resp(429), body=None),
    openai.InternalServerError(SECRET_MSG, response=_resp(500), body=None),
    openai.OpenAIError(SECRET_MSG),
]
ANTHROPIC_ERRORS = [
    anthropic.APIConnectionError(request=_req()),
    anthropic.APITimeoutError(request=_req()),
    anthropic.AuthenticationError(SECRET_MSG, response=_resp(401), body=None),
    anthropic.RateLimitError(SECRET_MSG, response=_resp(429), body=None),
    anthropic.InternalServerError(SECRET_MSG, response=_resp(500), body=None),
    anthropic.AnthropicError(SECRET_MSG),
]


# --- Requirement: Interfaz mínima de proveedor ---


def test_openai_call_sends_system_and_user_and_reads_result():
    """Requirement: Interfaz mínima / Scenario: Llamada con OpenAI."""
    stub = OpenAIStub(openai_response(content="texto", prompt=11, completion=22))
    result = OpenAIProvider(stub, "gpt-4o-mini").generate("SYS", "USR")
    assert stub.kwargs == {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ],
        "max_completion_tokens": 2048,
    }
    assert (result.text, result.model, result.provider) == ("texto", "gpt-4o-mini", "openai")
    assert (result.input_tokens, result.output_tokens) == (11, 22)


def test_openai_none_content_yields_empty_text():
    """Requirement: Interfaz mínima / Scenario: Llamada con OpenAI (content=None)."""
    result = OpenAIProvider(OpenAIStub(openai_response(content=None)), "m").generate("s", "u")
    assert result.text == ""


def test_openai_missing_usage_reports_zero_tokens():
    """Requirement: Interfaz mínima / Scenario: Llamada con OpenAI (usage=None)."""
    result = OpenAIProvider(OpenAIStub(openai_response(usage=False)), "m").generate("s", "u")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_anthropic_call_sends_system_param_and_user_message():
    """Requirement: Interfaz mínima / Scenario: Llamada con Anthropic."""
    stub = AnthropicStub(anthropic_response(input_tokens=33, output_tokens=44))
    result = AnthropicProvider(stub, "claude-haiku-4-5").generate("SYS", "USR")
    assert stub.kwargs == {
        "model": "claude-haiku-4-5",
        "system": "SYS",
        "messages": [{"role": "user", "content": "USR"}],
        "max_tokens": 2048,
    }
    assert (result.text, result.model, result.provider) == (
        "hello",
        "claude-haiku-4-5",
        "anthropic",
    )
    assert (result.input_tokens, result.output_tokens) == (33, 44)


def test_anthropic_concatenates_only_text_blocks():
    """Requirement: Interfaz mínima / Scenario: Llamada con Anthropic (bloques de texto)."""
    blocks = [
        SimpleNamespace(type="text", text="foo"),
        SimpleNamespace(type="thinking", thinking="ignored"),
        SimpleNamespace(type="text", text="bar"),
    ]
    stub = AnthropicStub(anthropic_response(blocks=blocks))
    assert AnthropicProvider(stub, "m").generate("s", "u").text == "foobar"


def test_max_tokens_constant_is_2048():
    """Requirement: Límites de la llamada / constantes en código."""
    assert MAX_TOKENS == 2048
    assert llm_service.LLM_TIMEOUT_S == 60
    assert llm_service.LLM_MAX_RETRIES == 2


# --- Requirement: Selección de proveedor por configuración ---


def test_get_llm_provider_openai_by_config(monkeypatch):
    """Requirement: Selección de proveedor / Scenario: Cambio de proveedor (openai)."""
    use_settings(monkeypatch, llm_provider="openai")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)


def test_get_llm_provider_switches_to_anthropic_without_code_change(monkeypatch):
    """Requirement: Selección de proveedor / Scenario: Cambio de proveedor sin tocar código."""
    use_settings(monkeypatch, llm_provider="anthropic", anthropic_api_key="sk-ant-fake")
    provider = get_llm_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider._model == DEFAULT_MODELS["anthropic"]


def test_get_llm_provider_is_cached(monkeypatch):
    """Requirement: Selección de proveedor / provider built once per process."""
    use_settings(monkeypatch)
    assert get_llm_provider() is get_llm_provider()


def test_build_provider_openai_client_limits():
    """Requirement: Límites de la llamada / timeout 60 s y 2 reintentos (OpenAI)."""
    provider = build_provider("openai", make_settings())
    assert isinstance(provider, OpenAIProvider)
    assert provider._client.timeout == 60
    assert provider._client.max_retries == 2


def test_build_provider_anthropic_client_limits():
    """Requirement: Límites de la llamada / timeout 60 s y 2 reintentos (Anthropic)."""
    settings = make_settings(llm_provider="anthropic", anthropic_api_key="sk-ant-fake")
    provider = build_provider("anthropic", settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider._client.timeout == 60
    assert provider._client.max_retries == 2


def test_build_provider_active_uses_effective_model():
    """Requirement: Selección de proveedor / el activo usa LLM_MODEL."""
    settings = make_settings(llm_model="gpt-4o-mini-2024-07-18")
    assert build_provider("openai", settings)._model == "gpt-4o-mini-2024-07-18"


def test_build_provider_non_active_uses_default_model():
    """Requirement: Selección de proveedor / el no activo usa su modelo por defecto."""
    settings = make_settings(llm_model="gpt-4o-mini-2024-07-18", anthropic_api_key="sk-ant-fake")
    assert build_provider("anthropic", settings)._model == DEFAULT_MODELS["anthropic"]


def test_build_provider_unknown_name_raises():
    """Requirement: Selección de proveedor / nombre desconocido."""
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        build_provider("gemini", make_settings())


def test_build_provider_missing_key_for_non_active_raises():
    """Requirement: Selección de proveedor / clave ausente del proveedor no activo."""
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_provider("anthropic", make_settings())


# --- Requirement: Errores del proveedor ---


@pytest.mark.parametrize("error", OPENAI_ERRORS, ids=lambda e: type(e).__name__)
def test_openai_sdk_errors_are_wrapped(error):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible (OpenAI)."""
    provider = OpenAIProvider(OpenAIStub(error=error), "gpt-4o-mini")
    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate("s", "u")
    assert exc_info.value.provider == "openai"
    assert exc_info.value.__cause__ is error
    assert SECRET_MSG not in str(exc_info.value)


@pytest.mark.parametrize("error", ANTHROPIC_ERRORS, ids=lambda e: type(e).__name__)
def test_anthropic_sdk_errors_are_wrapped(error):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible (Anthropic)."""
    provider = AnthropicProvider(AnthropicStub(error=error), "claude-haiku-4-5")
    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate("s", "u")
    assert exc_info.value.provider == "anthropic"
    assert exc_info.value.__cause__ is error
    assert SECRET_MSG not in str(exc_info.value)


# --- Requirement: Límites de la llamada al LLM ---


def test_openai_truncated_output_logs_warning_and_returns_text(caplog):
    """Requirement: Límites / Scenario: Salida truncada (OpenAI finish_reason=length)."""
    stub = OpenAIStub(openai_response(content="partial", finish_reason="length"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = OpenAIProvider(stub, "m").generate("s", "u")
    assert result.text == "partial"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("LLM output truncated" in r.getMessage() for r in warnings)


def test_anthropic_truncated_output_logs_warning_and_returns_text(caplog):
    """Requirement: Límites / Scenario: Salida truncada (Anthropic stop_reason=max_tokens)."""
    blocks = [SimpleNamespace(type="text", text="partial")]
    stub = AnthropicStub(anthropic_response(blocks=blocks, stop_reason="max_tokens"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = AnthropicProvider(stub, "m").generate("s", "u")
    assert result.text == "partial"
    assert any("LLM output truncated" in r.getMessage() for r in caplog.records)


def test_normal_completion_does_not_warn(caplog):
    """Requirement: Límites / sin warning cuando la salida no se trunca."""
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        OpenAIProvider(OpenAIStub(openai_response()), "m").generate("s", "u")
        AnthropicProvider(AnthropicStub(anthropic_response()), "m").generate("s", "u")
    assert not caplog.records


@pytest.mark.parametrize(
    ("provider_cls", "stub_cls", "error", "expected_status"),
    [
        (
            OpenAIProvider,
            OpenAIStub,
            openai.RateLimitError(SECRET_MSG, response=_resp(429), body=None),
            "429",
        ),
        (
            AnthropicProvider,
            AnthropicStub,
            anthropic.InternalServerError(SECRET_MSG, response=_resp(500), body=None),
            "500",
        ),
        (OpenAIProvider, OpenAIStub, openai.OpenAIError(SECRET_MSG), "None"),
        (AnthropicProvider, AnthropicStub, anthropic.AnthropicError(SECRET_MSG), "None"),
    ],
    ids=["openai-429", "anthropic-500", "openai-non-api-error", "anthropic-non-api-error"],
)
def test_error_log_has_type_and_status_but_no_sdk_message(
    caplog, provider_cls, stub_cls, error, expected_status
):
    """Requirement: Límites / Scenario: Errores sin datos sensibles en el log."""
    provider = provider_cls(stub_cls(error=error), "m")
    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(LLMProviderError):
        provider.generate("s", "u")
    records = [r for r in caplog.records if r.levelno == logging.ERROR and r.name == LOGGER]
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"error_type={type(error).__name__}" in message
    assert f"status={expected_status}" in message
    assert SECRET_MSG not in message
    assert SECRET_MSG not in caplog.text
