"""Structured generation with a Router double (spec: llm-provider "Generación estructurada",
"Opciones por llamada", "Límites de la llamada al LLM"; observability).

The double returns locally built `litellm.ModelResponse` objects with a forced tool call and
`usage`, so Instructor (`Mode.TOOLS`) runs for real but nothing touches the network.
"""

import copy
import json

import pytest
from litellm import ModelResponse
from structlog.testing import capture_logs

from app.config import Deployment
from app.estimation.schema import response_model_for
from app.services.llm_service import (
    FALLBACK_GROUP,
    MAX_TOKENS,
    PRIMARY_GROUP,
    LiteLLMProvider,
    LLMCallOptions,
    LLMProviderError,
    StructuredOutputError,
)
from tests.conftest import valid_payload

PRIMARY = Deployment("gpt-4o-mini", "openai")
FALLBACK = Deployment("claude-haiku-4-5", "anthropic")
RESPONSE_MODEL = response_model_for("line_items", "medium")
SECRET = "DESCRIPCION-SECRETA-123"
MESSAGES = [
    {
        "role": "user",
        "content": f"<project_description>\n{{{{ x }}}} {SECRET}\n</project_description>",
    }
]
VALID = valid_payload()
INVALID = {**VALID, "phases": VALID["phases"][:1], "title": SECRET}  # phases != rows


def tool_response(
    args, model_id=PRIMARY_GROUP, prompt_tokens=100, completion_tokens=50, finish="tool_calls"
) -> ModelResponse:
    arguments = args if isinstance(args, str) else json.dumps(args)
    response = ModelResponse(
        choices=[
            {
                "index": 0,
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "EstimationResult", "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )
    response._hidden_params = {"model_id": model_id}
    return response


class FakeRouter:
    """Returns the queued items in order (an `Exception` item is raised); records kwargs."""

    def __init__(self, items) -> None:
        self.items = list(items)
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(router, *, retries=2, fallback=FALLBACK, router_factory=None) -> LiteLLMProvider:
    return LiteLLMProvider(
        router, PRIMARY, fallback, router_factory=router_factory, structured_max_retries=retries
    )


def test_a_valid_object_is_returned_in_one_attempt():
    """Requirement: Generación estructurada (valor, modelo, proveedor, tokens, intentos)."""
    router = FakeRouter([tool_response(VALID, prompt_tokens=1200, completion_tokens=300)])
    result = provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert result.value.model_dump(mode="json") == RESPONSE_MODEL.model_validate(VALID).model_dump(
        mode="json"
    )
    assert isinstance(result.value, RESPONSE_MODEL)
    assert (result.model, result.provider) == ("gpt-4o-mini", "openai")
    assert (result.input_tokens, result.output_tokens, result.attempts) == (1200, 300, 1)
    assert (result.fallback_used, result.cache_hit) == (False, False)
    assert result.latency_ms >= 0
    assert len(router.calls) == 1


def test_validation_retry_sums_the_tokens_of_every_attempt():
    """Scenario: Reintento por validación (attempts = 2, tokens sumados)."""
    router = FakeRouter(
        [
            tool_response(INVALID, prompt_tokens=100, completion_tokens=40),
            tool_response(VALID, prompt_tokens=130, completion_tokens=60),
        ]
    )
    with capture_logs() as logs:
        result = provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert result.attempts == 2
    assert (result.input_tokens, result.output_tokens) == (230, 100)
    assert len(router.calls) == 2
    retry = next(e for e in logs if e["event"] == "structured_output_retry")
    assert retry["error_count"] >= 1
    # The validation error goes back to the model on the retry.
    assert len(router.calls[1]["messages"]) > len(router.calls[0]["messages"])
    # Retry events carry no Pydantic message (it would repeat the invalid values).
    assert SECRET not in repr([e for e in logs if e["event"] != "llm_call_started"])


def test_cost_uses_summed_tokens_and_the_effective_model_of_the_last_response():
    """Requirement: Generación estructurada (coste con los tokens sumados y el modelo efectivo)."""
    router = FakeRouter(
        [
            tool_response(INVALID, PRIMARY_GROUP, 500, 100),
            tool_response(VALID, FALLBACK_GROUP, 500, 400),
        ]
    )
    with capture_logs() as logs:
        result = provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert (result.model, result.provider, result.fallback_used) == (
        "claude-haiku-4-5",
        "anthropic",
        True,
    )
    completed = next(e for e in logs if e["event"] == "llm_call_completed")
    assert completed["mode"] == "structured" and completed["attempts"] == 2
    assert (completed["input_tokens"], completed["output_tokens"]) == (1000, 500)
    assert completed["cost_usd"] == 0.003500  # claude-haiku-4-5: 1000 in + 500 out


def test_exhausted_retries_make_three_calls_and_raise_invalid():
    """Scenario: Reintentos agotados (STRUCTURED_MAX_RETRIES = 2 -> 3 llamadas, invalid)."""
    router = FakeRouter([tool_response(INVALID)] * 3)
    with capture_logs() as logs, pytest.raises(StructuredOutputError) as caught:
        provider(router, retries=2).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert len(router.calls) == 3
    assert (caught.value.reason, caught.value.attempts) == ("invalid", 3)
    assert isinstance(caught.value, LLMProviderError)
    failed = next(e for e in logs if e["event"] == "structured_output_failed")
    assert (failed["reason"], failed["attempts"]) == ("invalid", 3)
    assert SECRET not in repr(logs)


def test_zero_retries_means_a_single_attempt():
    """Requirement: Generación estructurada (como máximo STRUCTURED_MAX_RETRIES reintentos)."""
    router = FakeRouter([tool_response(INVALID)] * 3)
    with pytest.raises(StructuredOutputError) as caught:
        provider(router, retries=0).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert len(router.calls) == 1 and caught.value.attempts == 1


def test_non_json_tool_arguments_count_as_invalid():
    """Requirement: Generación estructurada (los errores de parseo también se reintentan)."""
    router = FakeRouter([tool_response("{not json"), tool_response(VALID)])
    result = provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert result.attempts == 2


def test_truncated_output_raises_after_a_single_call():
    """Scenario: Salida cortada (finish_reason = length -> truncated, 1 llamada)."""
    router = FakeRouter([tool_response('{"language": "es", "title": "x', finish="length")] * 3)
    with capture_logs() as logs, pytest.raises(StructuredOutputError) as caught:
        provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert len(router.calls) == 1
    assert (caught.value.reason, caught.value.attempts) == ("truncated", 1)
    failed = next(e for e in logs if e["event"] == "structured_output_failed")
    assert failed["reason"] == "truncated"


def test_transport_error_is_a_domain_error_without_instructor_retry():
    """Scenario: Error de transporte sin reintento de Instructor (una sola llamada al Router)."""
    router = FakeRouter([ConnectionError("boom sk-secret-key")] * 3)
    with capture_logs() as logs, pytest.raises(LLMProviderError) as caught:
        provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert not isinstance(caught.value, StructuredOutputError)
    assert len(router.calls) == 1
    failed = next(e for e in logs if e["event"] == "llm_call_failed")
    assert failed["error_type"] == "ConnectionError" and failed["mode"] == "structured"
    assert "sk-secret-key" not in repr(logs)


def test_transport_error_after_a_validation_failure_is_not_an_output_error():
    """Requirement: Generación estructurada (los errores de transporte no son de salida)."""
    router = FakeRouter([tool_response(INVALID), ConnectionError("down")])
    with pytest.raises(LLMProviderError) as caught:
        provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    assert not isinstance(caught.value, StructuredOutputError)
    assert len(router.calls) == 2


def test_braces_in_the_message_reach_the_router_unmodified():
    """Scenario: Llaves en la descripción ({{ x }} no se interpreta como plantilla)."""
    router = FakeRouter([tool_response(VALID)])
    provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    sent = router.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "SYS"}
    assert sent[1] == MESSAGES[0]
    assert "{{ x }}" in sent[1]["content"]


def test_a_forced_tool_with_the_json_schema_is_sent():
    """Scenario: Herramienta forzada (tools con el JSON Schema y tool_choice forzado)."""
    router = FakeRouter([tool_response(VALID)])
    provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    kwargs = router.calls[0]
    (tool,) = kwargs["tools"]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "EstimationResult"
    assert tool["function"]["parameters"]["properties"].keys() == (
        RESPONSE_MODEL.model_json_schema()["properties"].keys()
    )
    assert kwargs["tool_choice"]["type"] == "function"
    assert kwargs["tool_choice"]["function"]["name"] == "EstimationResult"


def test_call_uses_primary_group_default_limit_and_no_context_nor_thinking():
    """Requirement: Opciones por llamada / Límites (max_tokens 2048, sin thinking ni context)."""
    router = FakeRouter([tool_response(VALID)])
    provider(router).generate_structured("SYS", MESSAGES, RESPONSE_MODEL)
    kwargs = router.calls[0]
    assert kwargs["model"] == PRIMARY_GROUP
    assert kwargs["max_tokens"] == MAX_TOKENS == 2048
    assert "thinking" not in kwargs and "context" not in kwargs and "timeout" not in kwargs


def test_requested_max_tokens_is_used():
    """Requirement: Límites de la llamada (límite de la petición, modo estructurado)."""
    router = FakeRouter([tool_response(VALID)])
    provider(router).generate_structured(
        "SYS", MESSAGES, RESPONSE_MODEL, LLMCallOptions(max_tokens=4000)
    )
    assert router.calls[0]["max_tokens"] == 4000


def test_model_override_uses_its_own_router_and_reports_its_model_without_fallback():
    """Scenario: Modelo destino (generate_structured con modelo destino)."""
    built = []
    override_router = FakeRouter([tool_response(VALID)])

    def factory(deployment):
        built.append(deployment)
        return override_router

    result = provider(FakeRouter([]), router_factory=factory).generate_structured(
        "SYS", MESSAGES, RESPONSE_MODEL, LLMCallOptions(model=FALLBACK, max_tokens=1000)
    )
    assert built == [FALLBACK]
    assert (result.model, result.provider, result.fallback_used) == (
        "claude-haiku-4-5",
        "anthropic",
        False,
    )
    assert override_router.calls[0]["max_tokens"] == 1000


def test_model_override_failure_is_a_domain_error_without_calling_the_fallback():
    """Scenario: Modelo destino (el modelo falla: error de dominio sin respaldo)."""
    primary_router = FakeRouter([tool_response(VALID)])
    override_router = FakeRouter([RuntimeError("override down")])
    with pytest.raises(LLMProviderError):
        provider(primary_router, router_factory=lambda d: override_router).generate_structured(
            "SYS", MESSAGES, RESPONSE_MODEL, LLMCallOptions(model=FALLBACK)
        )
    assert primary_router.calls == [] and len(override_router.calls) == 1


def test_llm_call_events_carry_mode_structured_and_attempts_without_content():
    """Requirement (observability): llm_call_started/completed con mode = structured."""
    router = FakeRouter([tool_response(VALID)])
    with capture_logs() as logs:
        provider(router).generate_structured("SYSTEM-SECRETO", MESSAGES, RESPONSE_MODEL)
    started = next(e for e in logs if e["event"] == "llm_call_started")
    completed = next(e for e in logs if e["event"] == "llm_call_completed")
    assert started["mode"] == completed["mode"] == "structured"
    assert completed["attempts"] == 1
    assert SECRET not in repr(logs) and "SYSTEM-SECRETO" not in repr(logs)


@pytest.mark.parametrize("narrative", [True, False])
def test_response_model_requirements_are_enforced_through_instructor(narrative):
    """Requirement: Schema (requisitos por formato y nivel provocan reintento)."""
    model = response_model_for("narrative" if narrative else "line_items", "detailed")
    without_assumptions = valid_payload()
    for phase in without_assumptions["phases"]:
        phase["assumptions"] = []
    router = FakeRouter([tool_response(without_assumptions), tool_response(VALID)])
    result = provider(router).generate_structured("SYS", MESSAGES, model)
    assert result.attempts == 2
