"""Structured response cache (spec: response-cache "Caché de la generación estructurada" and
the structured clauses of "Caché exact-match de respuestas"), with `fakeredis`.
"""

import contextlib
import json

from pydantic import Field
from structlog.testing import capture_logs

from app.estimation.schema import EstimationResult, response_model_for
from app.services.cache import ResponseCache
from app.services.llm_service import (
    CachedProvider,
    EstimationService,
    GenerationOptions,
    LLMCallOptions,
    StructuredOutputError,
)
from tests.conftest import FakeProvider, make_input, valid_payload

SYSTEM = "SYSTEM-PROMPT"
MESSAGES = [{"role": "user", "content": "Necesitamos una web de reservas"}]
MODEL = "gpt-4o-mini"
LINE_ITEMS = response_model_for("line_items", "medium")


def cached(inner: FakeProvider, cache: ResponseCache | None, model: str = MODEL) -> CachedProvider:
    return CachedProvider(inner, cache, model)


def key_for(response_model=LINE_ITEMS, options=None, model: str = MODEL) -> str:
    provider = CachedProvider(FakeProvider(), None, model)
    return provider._structured_key(SYSTEM, MESSAGES, response_model, options)


def events(logs, name):
    return [e for e in logs if e["event"] == name]


def test_hit_is_revalidated_and_keeps_original_tokens_without_calling_the_llm(response_cache):
    """Scenario: Acierto revalidado (objeto validado, cache_hit, tokens originales)."""
    inner = FakeProvider(
        model="claude-haiku-4-5",
        provider="anthropic",
        input_tokens=1200,
        output_tokens=300,
        fallback_used=True,
        attempts=2,
    )
    provider = cached(inner, response_cache)
    first = provider.generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    assert first.cache_hit is False
    with capture_logs() as logs:
        hit = provider.generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    assert inner.call_count == 1
    assert hit.cache_hit is True
    assert isinstance(hit.value, LINE_ITEMS)
    assert hit.value.model_dump() == first.value.model_dump()
    assert (hit.input_tokens, hit.output_tokens) == (1200, 300)
    assert (hit.model, hit.provider, hit.fallback_used, hit.attempts) == (
        "claude-haiku-4-5",
        "anthropic",
        True,
        2,
    )
    completed = events(logs, "llm_call_completed")[0]
    assert completed["mode"] == "structured" and completed["cache_hit"] is True


def test_the_stored_entry_is_the_llm_object_before_any_computation(fake_redis, response_cache):
    """Requirement: Caché estructurada (se guarda el objeto validado, no importes)."""
    provider = cached(FakeProvider(), response_cache)
    provider.generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    (raw,) = [fake_redis.get(k) for k in fake_redis.keys("*")]
    stored = json.loads(raw)
    assert set(stored) == {
        "value",
        "model",
        "provider",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "attempts",
    }
    assert "total_cost_eur" not in stored["value"] and "cost_eur" not in json.dumps(stored)
    assert EstimationResult.model_validate(stored["value"])


def test_corrupt_entry_logs_cache_entry_invalid_and_calls_the_llm(fake_redis, response_cache):
    """Scenario: Entrada corrupta (objeto que no valida contra el response_model)."""
    inner = FakeProvider()
    provider = cached(inner, response_cache)
    provider.generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    key = fake_redis.keys("*")[0]
    entry = json.loads(fake_redis.get(key))
    entry["value"] = {"bad": 1}
    fake_redis.set(key, json.dumps(entry))
    with capture_logs() as logs:
        result = provider.generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    assert inner.call_count == 2
    assert result.cache_hit is False
    assert len(events(logs, "cache_entry_invalid")) == 1


def test_entry_valid_for_the_base_schema_but_not_for_the_requested_requirements(response_cache):
    """Scenario: Entrada corrupta (la revalidación usa el response_model de la petición)."""
    inner = FakeProvider(structured=valid_payload(narrative=[]))
    provider = cached(inner, response_cache)
    # Stored under the narrative key with a value that fails its requirements.
    narrative = response_model_for("narrative", "medium")
    key = key_for(narrative)
    response_cache.set(
        key,
        {
            "value": valid_payload(narrative=[]),
            "model": MODEL,
            "provider": "openai",
            "input_tokens": 1,
            "output_tokens": 1,
            "fallback_used": False,
            "attempts": 1,
        },
    )
    with capture_logs() as logs, contextlib.suppress(Exception):
        # The fake LLM also returns an invalid object, so the call itself fails.
        provider.generate_structured(SYSTEM, MESSAGES, narrative)
    assert len(events(logs, "cache_entry_invalid")) == 1
    assert inner.call_count == 1


def test_a_structured_failure_is_not_stored_and_the_next_request_calls_the_llm_again(
    fake_redis, response_cache
):
    """Scenario: Error no guardado (StructuredOutputError)."""
    failing = FakeProvider(error=StructuredOutputError("openai", "invalid", 3))
    provider = cached(failing, response_cache)
    for _ in range(2):
        with contextlib.suppress(StructuredOutputError):
            provider.generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    assert failing.call_count == 2
    assert fake_redis.keys("*") == []


def test_provider_and_truncation_failures_are_not_stored_either(fake_redis, response_cache):
    """Requirement: Caché estructurada (nada se guarda si la llamada termina en error)."""
    for error in (StructuredOutputError("openai", "truncated", 1), RuntimeError("x")):
        inner = FakeProvider(error=error)  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            cached(inner, response_cache).generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    assert fake_redis.keys("*") == []


def test_key_is_deterministic_and_prefixed():
    """Requirement: Caché exact-match (clave determinista SHA-256)."""
    assert key_for() == key_for()
    assert key_for().startswith("estimator:llm:v1:")


def test_key_changes_with_the_schema():
    """Scenario: Cambio del schema (otro JSON Schema, otra clave)."""

    class Changed(EstimationResult):
        extra: str = Field(default="x", description="a new field")

    Changed.__name__ = "EstimationResult"
    assert Changed.model_json_schema() != LINE_ITEMS.model_json_schema()
    assert key_for(Changed) != key_for()


def test_key_changes_with_the_format_and_level_requirements():
    """Requirement: Caché exact-match (requisitos por formato y nivel en la clave)."""
    keys = {
        key_for(response_model_for("line_items", "medium")),
        key_for(response_model_for("narrative", "medium")),
        key_for(response_model_for("line_items", "detailed")),
        key_for(response_model_for("narrative", "detailed")),
    }
    assert len(keys) == 4
    # `summary` and `medium` share the requirements: same key for the same prompt.
    assert key_for(response_model_for("line_items", "summary")) == key_for(
        response_model_for("phases_table", "medium")
    )


def test_key_changes_with_model_override_and_max_tokens():
    """Requirement: Caché exact-match (modelo destino, override y max_tokens)."""
    base = key_for()
    assert key_for(model="gpt-4o") != base
    assert key_for(options=LLMCallOptions(max_tokens=4000)) != base
    assert key_for(options=LLMCallOptions()) == base


def test_structured_and_text_keys_never_collide():
    """Requirement: Caché exact-match (tipo de llamada: texto o estructurada)."""
    provider = CachedProvider(FakeProvider(), None, MODEL)
    assert provider._key(SYSTEM, MESSAGES) != key_for()


def test_changing_the_primary_model_does_not_reuse_the_stored_response(response_cache):
    """Scenario: Cambio de configuración (PRIMARY_MODEL distinto)."""
    inner = FakeProvider()
    cached(inner, response_cache, "gpt-4o-mini").generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    result = cached(inner, response_cache, "gpt-4o").generate_structured(
        SYSTEM, MESSAGES, LINE_ITEMS
    )
    assert result.cache_hit is False and inner.call_count == 2


def test_cache_none_only_delegates_and_sets_latency():
    """Requirement: Degradación sin Redis (sin caché solo delega)."""
    inner = FakeProvider()
    result = cached(inner, None).generate_structured(SYSTEM, MESSAGES, LINE_ITEMS)
    assert result.cache_hit is False and inner.call_count == 1 and result.latency_ms >= 0


def test_estimation_with_a_cache_hit_renders_and_evaluates_the_stored_object(response_cache):
    """Scenario (API): Estimación desde la caché (mismo result y text, validation calculada)."""
    inner = FakeProvider()
    service = EstimationService(cached(inner, response_cache))
    first = service.estimate(make_input())
    hit = service.estimate(make_input(), GenerationOptions(evaluate=True))
    assert (first.cache_hit, hit.cache_hit) == (False, True)
    assert hit.result == first.result and hit.text == first.text
    assert hit.validation is not None and hit.validation.score == 1.0
    assert hit.input_tokens == first.input_tokens
    assert inner.call_count == 1


def test_evaluation_is_not_part_of_the_structured_key(response_cache):
    """Scenario: Evaluación fuera de la clave (estructurada)."""
    inner = FakeProvider()
    service = EstimationService(cached(inner, response_cache))
    service.estimate(make_input(), GenerationOptions(evaluate=True))
    second = service.estimate(make_input(), GenerationOptions(evaluate=False))
    assert second.cache_hit is True and second.validation is None


def test_other_format_level_or_description_misses(response_cache):
    """Scenarios: Petición distinta / Opciones distintas (estructurada)."""
    inner = FakeProvider()
    service = EstimationService(cached(inner, response_cache))
    service.estimate(make_input())
    for overrides in (
        {"output_format": "narrative"},
        {"detail_level": "detailed"},
        {"project_type": "mobile_app"},
        {"description": "A different description for the same kind of project."},
    ):
        assert service.estimate(make_input(**overrides)).cache_hit is False
    assert inner.call_count == 5
