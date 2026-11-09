"""Unit tests for `LiteLLMProvider` / `build_router` with a Router double (spec: llm-provider).

The double records the kwargs of every `completion` call and returns canned responses, so
nothing here touches LiteLLM's HTTP layer. The real Router is covered in `test_llm_contract.py`.
"""

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.config import Deployment
from app.services.llm_service import (
    FALLBACK_GROUP,
    MAX_TOKENS,
    PRIMARY_GROUP,
    ChatMessage,
    EstimationService,
    LiteLLMProvider,
    LLMProviderError,
    LLMResult,
    LLMStream,
    _to_estimation_result,
    build_router,
)
from tests.conftest import make_settings

PRIMARY = Deployment("gpt-4o-mini", "openai")
FALLBACK = Deployment("claude-haiku-4-5", "anthropic")
MESSAGES: list[ChatMessage] = [{"role": "user", "content": "Necesitamos una web"}]
SYSTEM = "SYSTEM-PROMPT"


def response(
    text: str | None = "estimación",
    finish_reason: str | None = "stop",
    usage: tuple[int, int] | None = (1200, 300),
    model_id: str | None = PRIMARY_GROUP,
) -> SimpleNamespace:
    """Non-streaming response in the shape of a LiteLLM `ModelResponse`."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text), finish_reason=finish_reason, index=0
            )
        ],
        usage=SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
        if usage
        else None,
        _hidden_params={"model_id": model_id} if model_id else {},
    )


def chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    model_id: str | None = PRIMARY_GROUP,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        usage=None,
        _hidden_params={"model_id": model_id} if model_id else {},
    )


def usage_chunk(prompt: int = 1200, completion: int = 300, model_id: str | None = PRIMARY_GROUP):
    """Final chunk of `include_usage`: usage set and empty `choices`."""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
        _hidden_params={"model_id": model_id} if model_id else {},
    )


class FakeStream:
    """Iterable of chunks; an `Exception` item is raised when reached. Tracks `close()`."""

    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)
        self.closed = False

    def __iter__(self):
        for item in self._items:
            if isinstance(item, Exception):
                raise item
            yield item

    def close(self) -> None:
        self.closed = True


class FakeRouter:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def make_provider(router: FakeRouter, fallback: Deployment | None = FALLBACK) -> LiteLLMProvider:
    return LiteLLMProvider(router, PRIMARY, fallback)


def consume(stream: LLMStream) -> list[str]:
    return list(stream)


def standard_stream(model_id: str = PRIMARY_GROUP) -> FakeStream:
    return FakeStream(
        [
            chunk(model_id=model_id),  # role-only first chunk: no content
            chunk("uno ", model_id=model_id),
            chunk("dos ", model_id=model_id),
            chunk("tres", finish_reason="stop", model_id=model_id),
            usage_chunk(model_id=model_id),
        ]
    )


# --- Requirement: Interfaz mínima de proveedor ---


def test_generate_sends_system_then_messages_to_primary_group_and_reads_result():
    """Requirement: Interfaz mínima / Scenario: Llamada con un modelo de OpenAI."""
    router = FakeRouter(response())
    result = make_provider(router).generate(SYSTEM, MESSAGES)
    assert len(router.calls) == 1
    kwargs = router.calls[0]
    assert kwargs["model"] == PRIMARY_GROUP
    assert kwargs["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Necesitamos una web"},
    ]
    assert kwargs["max_tokens"] == MAX_TOKENS == 2048
    assert result == LLMResult(
        text="estimación",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=1200,
        output_tokens=300,
        truncated=False,
        fallback_used=False,
        cache_hit=False,
        latency_ms=result.latency_ms,
    )
    assert result.latency_ms >= 0


def test_anthropic_primary_reports_anthropic_provider_and_tokens():
    """Requirement: Interfaz mínima / Scenario: Llamada con un modelo de Anthropic."""
    router = FakeRouter(response(usage=(10, 5)))
    result = LiteLLMProvider(router, FALLBACK, None).generate(SYSTEM, MESSAGES)
    assert (result.provider, result.model) == ("anthropic", "claude-haiku-4-5")
    assert (result.input_tokens, result.output_tokens) == (10, 5)
    # The system prompt travels as the first system message: LiteLLM moves it to
    # Anthropic's `system` parameter (checked against the Router in the contract tests).
    assert router.calls[0]["messages"][0] == {"role": "system", "content": SYSTEM}


def test_multi_turn_conversation_is_sent_unmodified_and_not_inside_the_system_prompt():
    """Requirement: Interfaz mínima / Scenario: Conversación de varios turnos."""
    conversation: list[ChatMessage] = [
        {"role": "user", "content": "turno-uno-transcripcion"},
        {"role": "assistant", "content": "turno-dos-estimacion"},
        {"role": "user", "content": "turno-tres-ajuste"},
    ]
    router = FakeRouter(response())
    make_provider(router).generate(SYSTEM, conversation)
    sent = router.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": SYSTEM}
    assert sent[1:] == conversation
    assert all(m["content"] not in SYSTEM for m in conversation)
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]


def test_generate_and_stream_send_the_same_params_plus_streaming_ones():
    """Requirement: Interfaz mínima / Scenario: Mismos parámetros en ambos modos."""
    generate_router = FakeRouter(response())
    stream_router = FakeRouter(standard_stream())
    make_provider(generate_router).generate(SYSTEM, MESSAGES)
    consume(make_provider(stream_router).stream(SYSTEM, MESSAGES))
    generate_kwargs, stream_kwargs = generate_router.calls[0], stream_router.calls[0]
    streaming_only = {"stream", "stream_options"}
    assert set(stream_kwargs) - set(generate_kwargs) == streaming_only
    assert {k: v for k, v in stream_kwargs.items() if k not in streaming_only} == generate_kwargs
    assert stream_kwargs["stream"] is True
    assert stream_kwargs["stream_options"] == {"include_usage": True}


def test_generate_none_content_yields_empty_text_and_missing_usage_zero_tokens():
    """Requirement: Interfaz mínima (texto vacío) / Scenario: Uso de tokens ausente (generate)."""
    result = make_provider(FakeRouter(response(text=None, usage=None))).generate(SYSTEM, MESSAGES)
    assert result.text == ""
    assert (result.input_tokens, result.output_tokens) == (0, 0)


# --- Requirement: Coste estimado (efectivo, sin prefijos) ---


def test_prefixed_model_resolves_to_unprefixed_name_and_prices_with_it():
    """Requirement: Coste estimado / Scenario: Modelo configurado con prefijo de proveedor."""
    settings = make_settings(
        primary_model="anthropic/claude-haiku-4-5",
        fallback_model="",
        anthropic_api_key="sk-ant-fake",
    )
    provider = LiteLLMProvider(FakeRouter(response(usage=(1000, 500))), settings.primary, None)
    estimation = _to_estimation_result(provider.generate(SYSTEM, MESSAGES))
    assert estimation.model == "claude-haiku-4-5"
    assert estimation.cost_usd == 0.0035


def test_snapshot_model_is_priced_like_the_alias():
    """Requirement: Coste estimado / Scenario: Snapshot configurado."""
    settings = make_settings(
        primary_model="claude-haiku-4-5-20251001", fallback_model="", anthropic_api_key="sk-ant"
    )
    provider = LiteLLMProvider(FakeRouter(response(usage=(1000, 500))), settings.primary, None)
    estimation = _to_estimation_result(provider.generate(SYSTEM, MESSAGES))
    assert estimation.cost_usd == 0.0035


def test_fallback_answer_is_priced_with_fallback_model():
    """Requirement: Coste estimado / Scenario: Coste del respaldo."""
    router = FakeRouter(response(usage=(1000, 500), model_id=FALLBACK_GROUP))
    estimation = EstimationService(make_provider(router)).estimate("T")
    assert estimation.model == "claude-haiku-4-5"
    assert estimation.cost_usd == 0.0035


def test_unknown_effective_model_has_null_cost():
    """Requirement: Coste estimado / Scenario: Modelo desconocido."""
    provider = LiteLLMProvider(FakeRouter(response()), Deployment("gpt-4o", "openai"), None)
    estimation = _to_estimation_result(provider.generate(SYSTEM, MESSAGES))
    assert estimation.cost_usd is None
    assert estimation.text == "estimación"


# --- Requirement: Fallback entre modelo primario y de respaldo (identificación) ---


def test_primary_answer_reports_no_fallback_and_primary_model():
    """Requirement: Fallback / Scenario: Primario disponible (generate)."""
    result = make_provider(FakeRouter(response(model_id=PRIMARY_GROUP))).generate(SYSTEM, MESSAGES)
    assert result.fallback_used is False
    assert (result.model, result.provider) == ("gpt-4o-mini", "openai")


def test_fallback_answer_reports_fallback_model_provider_and_flag():
    """Requirement: Fallback / Scenario: Primario caído (generate, por `model_id`)."""
    result = make_provider(FakeRouter(response(model_id=FALLBACK_GROUP))).generate(SYSTEM, MESSAGES)
    assert result.fallback_used is True
    assert (result.model, result.provider) == ("claude-haiku-4-5", "anthropic")


def test_missing_model_id_reports_primary_and_warns():
    """Requirement: Fallback (id ausente: se informa el primario y se avisa)."""
    with capture_logs() as logs:
        result = make_provider(FakeRouter(response(model_id=None))).generate(SYSTEM, MESSAGES)
    assert result.fallback_used is False
    assert result.model == "gpt-4o-mini"
    warnings = [e for e in logs if e["event"] == "llm_deployment_unknown"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"


def test_fallback_model_id_without_configured_fallback_is_treated_as_unknown():
    """Requirement: Fallback (id del respaldo sin respaldo configurado: primario + warning)."""
    with capture_logs() as logs:
        provider = make_provider(FakeRouter(response(model_id=FALLBACK_GROUP)), fallback=None)
        result = provider.generate(SYSTEM, MESSAGES)
    assert result.fallback_used is False
    assert any(e["event"] == "llm_deployment_unknown" for e in logs)


# --- Requirement: Errores del proveedor ---


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), ConnectionError("down"), TimeoutError("slow"), ValueError("x")],
    ids=lambda e: type(e).__name__,
)
def test_any_router_exception_becomes_domain_error_with_cause(error):
    """Requirement: Errores del proveedor / Scenario: Proveedores no disponibles (generate)."""
    with pytest.raises(LLMProviderError) as exc_info:
        make_provider(FakeRouter(error=error)).generate(SYSTEM, MESSAGES)
    assert exc_info.value.__cause__ is error
    assert exc_info.value.provider == "openai"


def test_error_opening_the_stream_is_wrapped_on_iteration_not_at_creation():
    """Requirement: Generación en streaming / Errores al abrir (llamada diferida y envuelta)."""
    error = RuntimeError("open failed")
    router = FakeRouter(error=error)
    stream = make_provider(router).stream(SYSTEM, MESSAGES)
    assert router.calls == []  # lazy: nothing happens until iteration
    with pytest.raises(LLMProviderError) as exc_info:
        consume(stream)
    assert exc_info.value.__cause__ is error


def test_error_mid_stream_keeps_cause_and_delivered_fragments():
    """Requirement: Errores del proveedor / Scenario: Error durante el streaming."""
    original = RuntimeError("read failed")
    stream_obj = FakeStream([chunk("a"), chunk("b"), original])
    stream = make_provider(FakeRouter(stream_obj)).stream(SYSTEM, MESSAGES)
    received: list[str] = []
    with pytest.raises(LLMProviderError) as exc_info:
        for fragment in stream:
            received.append(fragment)
    assert received == ["a", "b"]
    assert exc_info.value.__cause__ is original
    assert stream_obj.closed  # connection released on failure too


def test_non_litellm_transport_error_mid_stream_is_wrapped_and_logs_its_type():
    """Requirement: Errores del proveedor / Scenario: Error de transporte no envuelto."""

    class ReadTimeout(Exception):  # stands for an httpx transport error, not a LiteLLM one
        pass

    stream_obj = FakeStream([chunk("a"), ReadTimeout("secret-body-sk-123")])
    stream = make_provider(FakeRouter(stream_obj)).stream(SYSTEM, MESSAGES)
    with capture_logs() as logs, pytest.raises(LLMProviderError):
        consume(stream)
    failed = [e for e in logs if e["event"] == "llm_call_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == "ReadTimeout"
    assert failed[0]["fragments"] == 1
    assert "secret-body-sk-123" not in repr(logs)


def test_failure_mid_stream_does_not_fall_back_to_a_second_call():
    """Requirement: Fallback / Scenario: Fallo a mitad de stream (el proveedor no reintenta)."""
    router = FakeRouter(FakeStream([chunk("a"), RuntimeError("x")]))
    with pytest.raises(LLMProviderError):
        consume(make_provider(router).stream(SYSTEM, MESSAGES))
    assert len(router.calls) == 1  # fallback logic lives in the Router, not the provider


def test_both_models_failing_raises_domain_error():
    """Requirement: Fallback / Scenario: Ambos caídos (el Router relanza la del primario)."""
    router = FakeRouter(error=RuntimeError("primary down"))
    with pytest.raises(LLMProviderError):
        make_provider(router).generate(SYSTEM, MESSAGES)


# --- Requirement: Límites de la llamada al LLM (truncado) ---


@pytest.mark.parametrize(
    ("finish_reason", "expected"), [("length", True), ("stop", False), (None, False)]
)
def test_generate_truncated_flag_follows_finish_reason(finish_reason, expected):
    """Requirement: Límites / Scenarios: Salida truncada y Salida completa (generate)."""
    with capture_logs() as logs:
        result = make_provider(FakeRouter(response(finish_reason=finish_reason))).generate(
            SYSTEM, MESSAGES
        )
    assert result.truncated is expected
    warnings = [e for e in logs if e["event"] == "llm_output_truncated"]
    assert len(warnings) == (1 if expected else 0)
    if expected:
        assert result.text == "estimación"
        assert warnings[0]["log_level"] == "warning"


@pytest.mark.parametrize(("finish_reason", "expected"), [("length", True), ("stop", False)])
def test_stream_truncated_flag_follows_finish_reason_and_keeps_text(finish_reason, expected):
    """Requirement: Límites / Scenario: Salida truncada en streaming."""
    items = [chunk("a"), chunk("b", finish_reason=finish_reason), usage_chunk()]
    stream = make_provider(FakeRouter(FakeStream(items))).stream(SYSTEM, MESSAGES)
    with capture_logs() as logs:
        consume(stream)
    assert stream.result.truncated is expected
    assert stream.result.text == "ab"
    warnings = [e for e in logs if e["event"] == "llm_output_truncated"]
    assert len(warnings) == (1 if expected else 0)


def test_router_limits_come_from_settings():
    """Requirement: Límites / Scenario: Límites configurados (LLM_TIMEOUT=30, LLM_RETRIES=1)."""
    settings = make_settings(llm_timeout=30, llm_retries=1, anthropic_api_key="sk-ant-fake")
    router = build_router(settings)
    assert router.num_retries == 1
    assert router.timeout == 30
    for deployment in router.model_list:
        assert deployment["litellm_params"]["timeout"] == 30


# --- build_router (design D3) ---


def test_build_router_has_two_single_deployment_groups_with_fallback_chain():
    """Requirement: Fallback (primario siempre primero: dos grupos y cadena de fallbacks)."""
    settings = make_settings(anthropic_api_key="sk-ant-fake")
    router = build_router(settings)
    groups = {d["model_name"]: d for d in router.model_list}
    assert set(groups) == {PRIMARY_GROUP, FALLBACK_GROUP}
    assert groups[PRIMARY_GROUP]["litellm_params"]["model"] == "openai/gpt-4o-mini"
    assert groups[PRIMARY_GROUP]["litellm_params"]["api_key"] == "sk-fake-openai"
    assert groups[FALLBACK_GROUP]["litellm_params"]["model"] == "anthropic/claude-haiku-4-5"
    assert groups[FALLBACK_GROUP]["litellm_params"]["api_key"] == "sk-ant-fake"
    assert groups[PRIMARY_GROUP]["model_info"]["id"] == PRIMARY_GROUP
    assert groups[FALLBACK_GROUP]["model_info"]["id"] == FALLBACK_GROUP
    assert router.fallbacks == [{PRIMARY_GROUP: [FALLBACK_GROUP]}]


def test_build_router_without_fallback_has_single_group_and_no_fallbacks():
    """Requirement: Fallback / Scenario: Sin key del respaldo (no hay despliegue de respaldo)."""
    router = build_router(make_settings())  # no ANTHROPIC_API_KEY
    assert [d["model_name"] for d in router.model_list] == [PRIMARY_GROUP]
    assert not router.fallbacks  # Router normalizes `[]` to None/empty


def test_build_router_with_anthropic_primary_uses_anthropic_prefix_and_key():
    """Requirement: Interfaz mínima / Scenario: Llamada con un modelo de Anthropic (Router)."""
    settings = make_settings(
        primary_model="claude-haiku-4-5", fallback_model="", anthropic_api_key="sk-ant-fake"
    )
    router = build_router(settings)
    params = router.model_list[0]["litellm_params"]
    assert params["model"] == "anthropic/claude-haiku-4-5"
    assert params["api_key"] == "sk-ant-fake"


# --- Requirement: Generación en streaming ---


def test_stream_yields_fragments_in_order_and_result_with_usage():
    """Requirement: Generación en streaming / Scenario: Streaming con varios fragmentos."""
    stream_obj = standard_stream()
    stream = make_provider(FakeRouter(stream_obj)).stream(SYSTEM, MESSAGES)
    assert consume(stream) == ["uno ", "dos ", "tres"]  # the empty first chunk is skipped
    result = stream.result
    assert result.text == "uno dos tres"
    assert (result.input_tokens, result.output_tokens) == (1200, 300)
    assert (result.model, result.provider, result.fallback_used) == (
        "gpt-4o-mini",
        "openai",
        False,
    )
    assert result.truncated is False
    assert stream_obj.closed


def test_stream_usage_chunk_with_empty_choices_is_safe_and_missing_usage_is_zero():
    """Requirement: Generación en streaming / Scenario: Uso de tokens ausente."""
    stream = make_provider(FakeRouter(FakeStream([chunk("a"), chunk("b", "stop")]))).stream(
        SYSTEM, MESSAGES
    )
    assert consume(stream) == ["a", "b"]
    assert (stream.result.input_tokens, stream.result.output_tokens) == (0, 0)


def test_stream_result_before_consumption_raises_runtime_error():
    """Requirement: Generación en streaming / Scenario: Resultado antes de terminar."""
    stream = make_provider(FakeRouter(standard_stream())).stream(SYSTEM, MESSAGES)
    with pytest.raises(RuntimeError):
        stream.result  # noqa: B018
    iterator = iter(stream)
    next(iterator)
    with pytest.raises(RuntimeError):  # still partial
        stream.result  # noqa: B018


def test_stream_second_iteration_yields_nothing_and_does_not_call_again():
    """Requirement: Generación en streaming / Scenario: Segunda iteración."""
    router = FakeRouter(standard_stream())
    stream = make_provider(router).stream(SYSTEM, MESSAGES)
    assert consume(stream) == ["uno ", "dos ", "tres"]
    first = stream.result
    assert consume(stream) == []
    assert consume(stream) == []
    assert len(router.calls) == 1
    assert stream.result is first


def test_llmstream_second_iteration_does_not_touch_the_generator():
    """Requirement: Generación en streaming / Scenario: Segunda iteración (LLMStream)."""
    final = LLMResult("x", "m", "openai", 1, 2, True)

    def gen():
        yield "x"
        return final

    stream = LLMStream(gen())
    assert consume(stream) == ["x"]
    assert consume(stream) == []
    assert stream.result is final


def test_stream_closes_the_response_when_consumer_stops_early():
    """Requirement: Generación en streaming (Stop de Streamlit libera la conexión)."""
    stream_obj = standard_stream()
    stream = make_provider(FakeRouter(stream_obj)).stream(SYSTEM, MESSAGES)
    iterator = iter(stream)
    assert next(iterator) == "uno "
    assert not stream_obj.closed
    iterator.close()  # GeneratorExit propagates through the wrapper
    assert stream_obj.closed


def test_stream_without_close_method_is_supported():
    """Requirement: Generación en streaming (respuestas sin `close` no fallan)."""
    stream = make_provider(FakeRouter([chunk("a", "stop"), usage_chunk()])).stream(SYSTEM, MESSAGES)
    assert consume(stream) == ["a"]


@pytest.mark.parametrize(
    ("model_id", "fallback_used", "model"),
    [
        (PRIMARY_GROUP, False, "gpt-4o-mini"),
        (FALLBACK_GROUP, True, "claude-haiku-4-5"),
    ],
)
def test_stream_model_id_decides_effective_model_and_fallback_flag(model_id, fallback_used, model):
    """Requirement: Generación en streaming / Scenario: Fallo antes del primer fragmento."""
    stream = make_provider(FakeRouter(standard_stream(model_id))).stream(SYSTEM, MESSAGES)
    consume(stream)
    assert stream.result.fallback_used is fallback_used
    assert stream.result.model == model


def test_stream_last_chunk_model_id_wins():
    """Requirement: Generación en streaming (tras un fallback el Router estampa el respaldo)."""
    items = [chunk("a", model_id=PRIMARY_GROUP), chunk("b", model_id=FALLBACK_GROUP)]
    stream = make_provider(FakeRouter(FakeStream(items))).stream(SYSTEM, MESSAGES)
    consume(stream)
    assert stream.result.fallback_used is True


def test_stream_missing_model_id_reports_primary_and_warns():
    """Requirement: Fallback (id ausente en streaming: primario + warning)."""
    items = [chunk("a", model_id=None), chunk("b", "stop", model_id=None)]
    stream = make_provider(FakeRouter(FakeStream(items))).stream(SYSTEM, MESSAGES)
    with capture_logs() as logs:
        consume(stream)
    assert stream.result.fallback_used is False
    assert any(e["event"] == "llm_deployment_unknown" for e in logs)
