"""Contract tests: a real LiteLLM `Router` built by `build_router`, with `mock_response`.

These guard the assumptions of the design (D3-D6, D11) about LiteLLM 1.106.x: primary always
first, fallback after failure, no fallback after the first fragment, `model_id` in
`_hidden_params`, usage chunk and normalized `finish_reason`. `mock_response` answers before
any HTTP call, and the autouse `_no_network` fixture proves that nothing opens a socket.
"""

import socket

import litellm
import pytest
from litellm import ModelResponse

from app.services.llm_service import ChatMessage, LLMProviderError
from tests.conftest import FlakyStream, LengthStream

SYSTEM = "SYSTEM-PROMPT"
MESSAGES: list[ChatMessage] = [{"role": "user", "content": "Necesitamos una web"}]

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


# --- Sin red ---


def test_network_guard_blocks_connections():
    """Requirement: Tests sin coste ni red / Scenario: Ejecución en CI (el guard funciona)."""
    with pytest.raises(AssertionError, match="Network access"):
        socket.create_connection(("api.openai.com", 443), timeout=1)
    with pytest.raises(AssertionError, match="Network access"):
        socket.socket().connect(("127.0.0.1", 9))


def test_litellm_cost_map_is_loaded_from_the_local_copy():
    """Requirement: Tests sin coste ni red / Scenario: Mapa de precios de LiteLLM local."""
    from litellm.litellm_core_utils.get_model_cost_map import get_model_cost_map_source_info

    info = get_model_cost_map_source_info()
    assert info["source"] == "local"
    assert info["is_env_forced"] is True
    assert info["url"] is None
    # The local map includes what `get_llm_provider` and the pricing table need.
    assert "gpt-4o-mini" in litellm.model_cost
    assert "claude-haiku-4-5" in litellm.model_cost


def test_importing_the_app_forces_the_local_cost_map_without_network(tmp_path):
    """Requirement: Tests sin coste ni red / Scenario: Mapa de precios de LiteLLM local (import)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    env["PYTHONPATH"] = str(repo_root)
    script = (
        "import socket\n"
        "def blocked(*a, **k): raise AssertionError('network access')\n"
        "socket.socket.connect = blocked\n"
        "socket.getaddrinfo = blocked\n"
        "import app.config, os\n"
        "from litellm.litellm_core_utils.get_model_cost_map import "
        "get_model_cost_map_source_info as info\n"
        "assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP'].lower() == 'true'\n"
        "assert info()['source'] == 'local' and info()['is_env_forced']\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


# --- generate ---


def test_primary_answers_first_every_time(make_provider):
    """Requirement: Fallback / Scenario: Primario disponible (sin reparto de carga)."""
    provider = make_provider()
    results = [provider.generate(SYSTEM, MESSAGES) for _ in range(12)]
    assert {r.fallback_used for r in results} == {False}
    assert {r.text for r in results} == {"respuesta del primario"}
    assert {(r.model, r.provider) for r in results} == {("gpt-4o-mini", "openai")}


def test_generate_reads_text_usage_and_model_id(make_provider):
    """Requirement: Interfaz mínima (generate con Router real) y D4: `model_id`."""
    result = make_provider().generate(SYSTEM, MESSAGES)
    assert result.text == "respuesta del primario"
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.truncated is False
    assert result.latency_ms >= 0


def test_generate_falls_back_when_primary_fails(make_provider):
    """Requirement: Fallback / Scenario: Primario caído."""
    provider = make_provider(primary="litellm.InternalServerError")
    result = provider.generate(SYSTEM, MESSAGES)
    assert result.text == "respuesta del respaldo"
    assert result.fallback_used is True
    assert (result.model, result.provider) == ("claude-haiku-4-5", "anthropic")


def test_generate_falls_back_after_retries_are_exhausted(make_provider):
    """Requirement: Fallback / Scenario: Primario caído (tras los reintentos del primario)."""
    provider = make_provider(primary="litellm.InternalServerError", retries=1)
    result = provider.generate(SYSTEM, MESSAGES)
    assert result.fallback_used is True


def test_generate_fails_with_domain_error_when_both_fail(make_provider):
    """Requirement: Fallback / Scenario: Ambos caídos; Errores / Proveedores no disponibles."""
    provider = make_provider(
        primary="litellm.InternalServerError", fallback="litellm.InternalServerError"
    )
    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate(SYSTEM, MESSAGES)
    assert isinstance(exc_info.value.__cause__, litellm.InternalServerError)
    assert exc_info.value.provider == "openai"


def test_generate_without_fallback_key_raises_without_calling_a_fallback(make_provider):
    """Requirement: Fallback / Scenario: Sin key del respaldo."""
    provider = make_provider(
        primary="litellm.InternalServerError",
        fallback="the fallback must never be called",
        with_fallback=False,
    )
    with pytest.raises(LLMProviderError):
        provider.generate(SYSTEM, MESSAGES)


def test_generate_rate_limit_error_also_falls_back(make_provider):
    """Requirement: Fallback / Scenario: Primario caído (429: un 4xx que sí pasa al respaldo)."""
    provider = make_provider(primary="litellm.RateLimitError")
    assert provider.generate(SYSTEM, MESSAGES).fallback_used is True


def test_generate_truncated_finish_reason_is_detected(make_provider):
    """Requirement: Límites / Scenario: Salida truncada (Router real, `finish_reason=length`)."""
    cut = ModelResponse(
        choices=[
            {"message": {"role": "assistant", "content": "cortada"}, "finish_reason": "length"}
        ]
    )
    result = make_provider(primary=cut).generate(SYSTEM, MESSAGES)
    assert result.truncated is True
    assert result.text == "cortada"


# --- stream ---


def test_stream_reads_fragments_usage_and_primary_model_id(make_provider):
    """Requirement: Generación en streaming / Scenario: Streaming con varios fragmentos."""
    stream = make_provider().stream(SYSTEM, MESSAGES)
    fragments = list(stream)
    assert len(fragments) > 1
    result = stream.result
    assert "".join(fragments) == result.text == "respuesta del primario"
    # Tokens come from the last chunk (`stream_options.include_usage`), whose `choices` is empty.
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.truncated is False
    assert result.fallback_used is False
    assert (result.model, result.provider) == ("gpt-4o-mini", "openai")


def test_stream_falls_back_when_primary_fails_before_the_first_fragment(make_provider):
    """Requirement: Generación en streaming / Scenario: Fallo antes del primer fragmento."""
    provider = make_provider(primary="Exception: mock_streaming_error")
    stream = provider.stream(SYSTEM, MESSAGES)
    fragments = list(stream)
    assert "".join(fragments) == "respuesta del respaldo"
    assert stream.result.fallback_used is True
    assert (stream.result.model, stream.result.provider) == ("claude-haiku-4-5", "anthropic")


def test_stream_fails_when_primary_errors_at_open_and_there_is_no_fallback(make_provider):
    """Requirement: Fallback / Scenario: Sin key del respaldo (streaming)."""
    provider = make_provider(primary="litellm.InternalServerError", with_fallback=False)
    with pytest.raises(LLMProviderError):
        list(provider.stream(SYSTEM, MESSAGES))


def test_stream_falls_back_when_primary_fails_at_open(make_provider):
    """Requirement: Generación en streaming / Fallo antes del primer fragmento (error al abrir)."""
    provider = make_provider(primary="litellm.InternalServerError")
    stream = provider.stream(SYSTEM, MESSAGES)
    assert "".join(stream) == "respuesta del respaldo"
    assert stream.result.fallback_used is True


def test_stream_both_failing_raises_domain_error(make_provider):
    """Requirement: Fallback / Scenario: Ambos caídos (streaming)."""
    provider = make_provider(
        primary="litellm.InternalServerError", fallback="litellm.InternalServerError"
    )
    with pytest.raises(LLMProviderError):
        list(provider.stream(SYSTEM, MESSAGES))


def test_no_fallback_after_the_first_fragment_was_delivered(make_provider):
    """Requirement: Fallback / Scenario: Fallo a mitad de stream."""
    provider = make_provider(
        primary={"model": "flaky/model"},
        fallback="el respaldo no debe llamarse",
        custom={"flaky": FlakyStream()},
    )
    stream = provider.stream(SYSTEM, MESSAGES)
    received: list[str] = []
    with pytest.raises(LLMProviderError) as exc_info:
        for fragment in stream:
            received.append(fragment)
    assert received == ["parcial"]  # nothing from the fallback after the first fragment
    assert exc_info.value.__cause__ is not None
    with pytest.raises(RuntimeError):
        stream.result  # noqa: B018


def test_anthropic_max_tokens_is_normalized_to_length_by_litellm():
    """Requirement: Límites / Scenario: Salida truncada (`stop_reason=max_tokens` en Anthropic)."""
    from litellm.litellm_core_utils.core_helpers import map_finish_reason

    assert map_finish_reason("max_tokens") == "length"
    assert map_finish_reason("end_turn") == "stop"


@pytest.mark.parametrize(("finish_reason", "truncated"), [("length", True), ("stop", False)])
def test_stream_finish_reason_length_marks_the_result_truncated(
    make_provider, finish_reason, truncated
):
    """Requirement: Límites / Scenario: Salida truncada en streaming."""
    provider = make_provider(
        primary={"model": "lengthy/model"},
        custom={"lengthy": LengthStream(finish_reason)},
    )
    stream = provider.stream(SYSTEM, MESSAGES)
    assert "".join(stream) == "cortada"
    assert stream.result.truncated is truncated


# --- Request shape against the real Router ---


def test_system_prompt_goes_to_the_anthropic_system_parameter():
    """Requirement: Interfaz mínima / Scenario: Llamada con un modelo de Anthropic."""
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "turno-uno"},
        {"role": "assistant", "content": "turno-dos"},
        {"role": "user", "content": "turno-tres"},
    ]
    data = AnthropicConfig().transform_request(
        model="claude-haiku-4-5",
        messages=messages,
        optional_params={"max_tokens": 2048},
        litellm_params={},
        headers={},
    )
    assert data["system"] == [{"type": "text", "text": SYSTEM}]
    assert [m["role"] for m in data["messages"]] == ["user", "assistant", "user"]
    assert data["max_tokens"] == 2048


def test_provider_resolution_matches_the_router_model_prefix():
    """Requirement: Modelos soportados (`get_llm_provider` sin red para ambos modelos)."""
    assert litellm.get_llm_provider("gpt-4o-mini")[1] == "openai"
    assert litellm.get_llm_provider("claude-haiku-4-5")[1] == "anthropic"
    assert litellm.get_llm_provider("anthropic/claude-haiku-4-5")[0] == "claude-haiku-4-5"


# --- Model override ---


def test_override_is_answered_by_its_own_router_with_the_requested_model(make_provider):
    """Requirement: Sobrescritura del modelo / Scenario: Modelo de otro proveedor (Router real)."""
    from app.config import Deployment
    from app.services.llm_service import LLMCallOptions

    provider = make_provider(primary="respuesta del override", fallback="no debe llamarse")
    options = LLMCallOptions(model=Deployment("claude-haiku-4-5", "anthropic"))
    result = provider.generate(SYSTEM, MESSAGES, options)
    assert result.text == "respuesta del override"
    assert (result.model, result.provider, result.fallback_used) == (
        "claude-haiku-4-5",
        "anthropic",
        False,
    )


def test_override_failure_does_not_fall_back_with_the_real_router(make_provider):
    """Requirement: Sobrescritura del modelo / Scenario: Override sin fallback (Router real)."""
    from app.config import Deployment
    from app.services.llm_service import LLMCallOptions

    provider = make_provider(
        primary="litellm.InternalServerError", fallback="el respaldo no debe llamarse"
    )
    options = LLMCallOptions(model=Deployment("claude-haiku-4-5", "anthropic"))
    with pytest.raises(LLMProviderError):
        provider.generate(SYSTEM, MESSAGES, options)
    with pytest.raises(LLMProviderError):
        list(provider.stream(SYSTEM, MESSAGES, options))
    # Without the override, the same provider does fall back (control).
    assert provider.generate(SYSTEM, MESSAGES).fallback_used is True


# --- generate_structured ---


def test_generate_structured_through_the_real_router(make_provider):
    """Requirement: Generación estructurada (Router real de LiteLLM 1.106.x con `tools`).

    `litellm.completion` imports its MCP handler whenever `tools` is present, and that handler
    needs the optional `mcp` package; the provider must skip it (`_skip_mcp_handler`). A fake
    Router never reaches that code, so only this contract test guards it.
    """
    from app.estimation.schema import response_model_for
    from tests.conftest import valid_payload
    from tests.test_structured_provider import tool_response

    model = response_model_for("line_items", "medium")
    provider = make_provider(primary=tool_response(valid_payload()))
    result = provider.generate_structured(SYSTEM, MESSAGES, model)
    assert result.value.model_dump() == model.model_validate(valid_payload()).model_dump()
    assert result.attempts == 1
    assert result.fallback_used is False
