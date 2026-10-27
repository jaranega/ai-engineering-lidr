"""Streaming tests for the `llm-provider` capability (stubbed SDK chunks and events)."""

import logging
from types import SimpleNamespace

import anthropic
import openai
import pytest

from app.services.llm_service import (
    MAX_TOKENS,
    AnthropicProvider,
    ChatMessage,
    LLMProviderError,
    LLMResult,
    LLMStream,
    OpenAIProvider,
)
from tests.test_llm_providers import (
    ANTHROPIC_ERRORS,
    OPENAI_ERRORS,
    SECRET_MSG,
    AnthropicStub,
    OpenAIStub,
)

LOGGER = "app.services.llm_service"
MESSAGES: list[ChatMessage] = [
    {"role": "user", "content": "T"},
    {"role": "assistant", "content": "E"},
    {"role": "user", "content": "A"},
]


class FailingStream:
    """Iterable stub: yields `items`, then raises `error`; records `close()`."""

    def __init__(self, items, error=None):
        self._items = list(items)
        self._error = error
        self.closed = False

    def __iter__(self):
        yield from self._items
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True


# --- Chunk and event builders ---


def oa_chunk(content=None, finish_reason=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        usage=usage,
    )


def oa_usage_chunk(prompt=11, completion=22):
    return SimpleNamespace(
        choices=[], usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    )


def oa_chunks(*fragments, usage=True):
    chunks = [oa_chunk(content=None)]  # first chunk: role only
    chunks += [oa_chunk(content=f) for f in fragments]
    chunks.append(oa_chunk(content=None, finish_reason="stop"))
    if usage:
        chunks.append(oa_usage_chunk())
    return chunks


def an_start(input_tokens=33):
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(usage=SimpleNamespace(input_tokens=input_tokens)),
    )


def an_text(text):
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text)
    )


def an_delta(output_tokens, stop_reason=None):
    return SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(stop_reason=stop_reason),
        usage=SimpleNamespace(output_tokens=output_tokens),
    )


def an_events(*fragments, output_tokens=44, stop_reason="end_turn"):
    events = [an_start(), SimpleNamespace(type="content_block_start")]
    events += [an_text(f) for f in fragments]
    events.append(an_delta(output_tokens, stop_reason))
    events.append(SimpleNamespace(type="message_stop"))
    return events


def consume(stream: LLMStream) -> list[str]:
    return list(stream)


# --- Scenario: Streaming con OpenAI ---


def test_openai_stream_yields_fragments_and_result_with_usage():
    """Requirement: Generación en streaming / Scenario: Streaming con OpenAI."""
    stub = OpenAIStub(oa_chunks("Hola ", "mundo", "!"))
    stream = OpenAIProvider(stub, "gpt-4o-mini").stream("SYS", MESSAGES)
    assert consume(stream) == ["Hola ", "mundo", "!"]
    assert stream.result == LLMResult(
        text="Hola mundo!",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=11,
        output_tokens=22,
    )


def test_openai_stream_kwargs():
    """Requirement: Generación en streaming + Límites / kwargs de la llamada (OpenAI)."""
    stub = OpenAIStub(oa_chunks("a"))
    consume(OpenAIProvider(stub, "gpt-4o-mini").stream("SYS", MESSAGES))
    assert stub.kwargs == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "system", "content": "SYS"}, *MESSAGES],
        "max_completion_tokens": MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def test_openai_stream_is_lazy_the_call_happens_on_iteration():
    """Requirement: Generación en streaming / la llamada al SDK empieza al iterar (D3)."""
    stub = OpenAIStub(oa_chunks("a"))
    stream = OpenAIProvider(stub, "m").stream("s", MESSAGES)
    assert stub.kwargs is None
    consume(stream)
    assert stub.kwargs is not None


def test_openai_stream_skips_empty_fragments_and_empty_choices():
    """Requirement: Generación en streaming / fragmentos vacíos y chunks sin choices."""
    stub = OpenAIStub([oa_chunk(None), oa_chunk(""), oa_chunk("x"), oa_chunk(""), oa_usage_chunk()])
    stream = OpenAIProvider(stub, "m").stream("s", MESSAGES)
    assert consume(stream) == ["x"]
    assert stream.result.text == "x"


def test_openai_stream_missing_usage_reports_zero_tokens():
    """Requirement: Generación en streaming / Scenario: Uso de tokens ausente."""
    stream = OpenAIProvider(OpenAIStub(oa_chunks("a", "b", usage=False)), "m").stream("s", MESSAGES)
    consume(stream)
    assert (stream.result.input_tokens, stream.result.output_tokens) == (0, 0)
    assert stream.result.text == "ab"


def test_openai_stream_closes_the_response_when_done():
    """Requirement: Generación en streaming / se libera la conexión al terminar."""
    response = FailingStream(oa_chunks("a"))
    consume(OpenAIProvider(OpenAIStub(response), "m").stream("s", MESSAGES))
    assert response.closed


def test_openai_stream_closes_the_response_when_consumer_stops_early():
    """Requirement: Generación en streaming / se libera la conexión si el consumidor para."""
    response = FailingStream(oa_chunks("a", "b", "c"))
    stream = OpenAIProvider(OpenAIStub(response), "m").stream("s", MESSAGES)
    iterator = iter(stream)
    next(iterator)
    iterator.close()
    assert response.closed


# --- Scenario: Streaming con Anthropic ---


def test_anthropic_stream_yields_fragments_and_result_with_usage():
    """Requirement: Generación en streaming / Scenario: Streaming con Anthropic."""
    stub = AnthropicStub(an_events("Hola ", "mundo", output_tokens=44))
    stream = AnthropicProvider(stub, "claude-haiku-4-5").stream("SYS", MESSAGES)
    assert consume(stream) == ["Hola ", "mundo"]
    assert stream.result == LLMResult(
        text="Hola mundo",
        model="claude-haiku-4-5",
        provider="anthropic",
        input_tokens=33,
        output_tokens=44,
    )


def test_anthropic_stream_kwargs():
    """Requirement: Generación en streaming + Límites / kwargs de la llamada (Anthropic)."""
    stub = AnthropicStub(an_events("a"))
    consume(AnthropicProvider(stub, "claude-haiku-4-5").stream("SYS", MESSAGES))
    assert stub.kwargs == {
        "model": "claude-haiku-4-5",
        "system": "SYS",
        "messages": MESSAGES,
        "max_tokens": MAX_TOKENS,
        "stream": True,
    }


def test_anthropic_stream_output_tokens_are_assigned_not_summed():
    """Requirement: Generación en streaming / output_tokens es acumulado en message_delta."""
    events = [an_start(), an_text("a"), an_delta(10), an_text("b"), an_delta(25)]
    stream = AnthropicProvider(AnthropicStub(events), "m").stream("s", MESSAGES)
    consume(stream)
    assert stream.result.output_tokens == 25
    assert stream.result.input_tokens == 33


def test_anthropic_stream_ignores_non_text_deltas_and_skips_empty_fragments():
    """Requirement: Generación en streaming / solo fragmentos de texto no vacíos."""
    events = [
        an_start(),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="input_json_delta")),
        an_text(""),
        an_text("ok"),
        an_delta(5),
    ]
    stream = AnthropicProvider(AnthropicStub(events), "m").stream("s", MESSAGES)
    assert consume(stream) == ["ok"]


def test_anthropic_stream_closes_the_response_when_done():
    """Requirement: Generación en streaming / se libera la conexión al terminar."""
    response = FailingStream(an_events("a"))
    consume(AnthropicProvider(AnthropicStub(response), "m").stream("s", MESSAGES))
    assert response.closed


# --- Scenario: Resultado antes de terminar ---


@pytest.mark.parametrize("vendor", ["openai", "anthropic"])
def test_result_before_consumption_raises_runtime_error(vendor):
    """Requirement: Generación en streaming / Scenario: Resultado antes de terminar."""
    if vendor == "openai":
        stream = OpenAIProvider(OpenAIStub(oa_chunks("a", "b")), "m").stream("s", MESSAGES)
    else:
        stream = AnthropicProvider(AnthropicStub(an_events("a", "b")), "m").stream("s", MESSAGES)
    with pytest.raises(RuntimeError):
        stream.result  # noqa: B018
    iterator = iter(stream)
    next(iterator)  # partially consumed
    with pytest.raises(RuntimeError):
        stream.result  # noqa: B018


# --- Errors: at creation, mid-stream (SDK) and mid-stream (non-SDK) ---


@pytest.mark.parametrize("error", OPENAI_ERRORS, ids=lambda e: type(e).__name__)
def test_openai_stream_sdk_error_at_creation_is_wrapped(error):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible (stream OpenAI)."""
    stream = OpenAIProvider(OpenAIStub(error=error), "m").stream("s", MESSAGES)
    with pytest.raises(LLMProviderError) as exc_info:
        consume(stream)
    assert exc_info.value.provider == "openai"
    assert exc_info.value.__cause__ is error
    assert SECRET_MSG not in str(exc_info.value)


@pytest.mark.parametrize("error", ANTHROPIC_ERRORS, ids=lambda e: type(e).__name__)
def test_anthropic_stream_sdk_error_at_creation_is_wrapped(error):
    """Requirement: Errores del proveedor / Scenario: Proveedor no disponible (stream Anthropic)."""
    stream = AnthropicProvider(AnthropicStub(error=error), "m").stream("s", MESSAGES)
    with pytest.raises(LLMProviderError) as exc_info:
        consume(stream)
    assert exc_info.value.provider == "anthropic"
    assert exc_info.value.__cause__ is error


def test_openai_stream_sdk_error_mid_iteration_keeps_cause_and_fragments():
    """Requirement: Errores del proveedor / Scenario: Error durante el streaming (OpenAI)."""
    error = openai.APIConnectionError(request=OPENAI_ERRORS[0].request)
    response = FailingStream([oa_chunk("a"), oa_chunk("b")], error)
    stream = OpenAIProvider(OpenAIStub(response), "m").stream("s", MESSAGES)
    received = []
    with pytest.raises(LLMProviderError) as exc_info:
        for fragment in stream:
            received.append(fragment)
    assert received == ["a", "b"]
    assert exc_info.value.__cause__ is error
    assert response.closed


def test_anthropic_stream_sdk_error_mid_iteration_keeps_cause_and_fragments():
    """Requirement: Errores del proveedor / Scenario: Error durante el streaming (Anthropic)."""
    error = anthropic.APIConnectionError(request=ANTHROPIC_ERRORS[0].request)
    response = FailingStream([an_start(), an_text("a")], error)
    stream = AnthropicProvider(AnthropicStub(response), "m").stream("s", MESSAGES)
    received = []
    with pytest.raises(LLMProviderError) as exc_info:
        for fragment in stream:
            received.append(fragment)
    assert received == ["a"]
    assert exc_info.value.__cause__ is error
    assert response.closed


@pytest.mark.parametrize("vendor", ["openai", "anthropic"])
def test_non_sdk_error_mid_stream_becomes_domain_error_and_logs_type(vendor, caplog):
    """Requirement: Errores del proveedor / Scenario: Error de transporte no envuelto por el SDK."""
    error = RuntimeError(SECRET_MSG)
    if vendor == "openai":
        response = FailingStream([oa_chunk("a")], error)
        stream = OpenAIProvider(OpenAIStub(response), "m").stream("s", MESSAGES)
    else:
        response = FailingStream([an_start(), an_text("a")], error)
        stream = AnthropicProvider(AnthropicStub(response), "m").stream("s", MESSAGES)
    with caplog.at_level(logging.ERROR, logger=LOGGER), pytest.raises(LLMProviderError) as exc_info:
        consume(stream)
    assert exc_info.value.provider == vendor
    assert exc_info.value.__cause__ is error
    assert "error_type=RuntimeError" in caplog.text
    assert SECRET_MSG not in caplog.text
    assert response.closed


# --- Requirement: Límites / Scenario: Salida truncada en streaming ---


def test_openai_stream_truncated_logs_warning_and_keeps_text(caplog):
    """Requirement: Límites / Scenario: Salida truncada en streaming (OpenAI)."""
    chunks = [oa_chunk("par"), oa_chunk("tial", finish_reason="length"), oa_usage_chunk()]
    stream = OpenAIProvider(OpenAIStub(chunks), "m").stream("s", MESSAGES)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        consume(stream)
    assert stream.result.text == "partial"
    assert any("LLM output truncated" in r.getMessage() for r in caplog.records)


def test_anthropic_stream_truncated_logs_warning_and_keeps_text(caplog):
    """Requirement: Límites / Scenario: Salida truncada en streaming (Anthropic)."""
    stub = AnthropicStub(an_events("par", "tial", stop_reason="max_tokens"))
    stream = AnthropicProvider(stub, "m").stream("s", MESSAGES)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        consume(stream)
    assert stream.result.text == "partial"
    assert any("LLM output truncated" in r.getMessage() for r in caplog.records)


def test_streams_without_truncation_do_not_warn(caplog):
    """Requirement: Límites / sin warning cuando la salida no se trunca (streaming)."""
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        consume(OpenAIProvider(OpenAIStub(oa_chunks("a")), "m").stream("s", MESSAGES))
        consume(AnthropicProvider(AnthropicStub(an_events("a")), "m").stream("s", MESSAGES))
    assert not caplog.records
