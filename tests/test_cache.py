"""Tests for the response cache (spec: response-cache), with `fakeredis` and no real Redis."""

import json
import time

import fakeredis
import pytest
import redis
from structlog.testing import capture_logs

from app.config import Deployment, get_settings
from app.prompts.loader import render_estimation_prompt
from app.services import cache as cache_module
from app.services.cache import KEY_PREFIX, ResponseCache, build_redis_client, cache_key
from app.services.llm_service import (
    MAX_TOKENS,
    CachedProvider,
    ChatMessage,
    EstimationService,
    GenerationOptions,
    LLMCallOptions,
    LLMProviderError,
)
from tests.conftest import FakeProvider, make_input

SYSTEM = "SYSTEM-PROMPT"
MESSAGES: list[ChatMessage] = [{"role": "user", "content": "Necesitamos una web de reservas"}]
MODEL = "gpt-4o-mini"


def cached(inner: FakeProvider, cache: ResponseCache | None, model: str = MODEL) -> CachedProvider:
    return CachedProvider(inner, cache, model)


def events(logs: list[dict], name: str) -> list[dict]:
    return [e for e in logs if e["event"] == name]


def key_for(system: str = SYSTEM, messages=MESSAGES, model: str = MODEL) -> str:
    return CachedProvider(FakeProvider(), None, model)._key(system, messages)


@pytest.fixture
def down_cache() -> ResponseCache:
    """A cache whose Redis refuses every connection."""
    server = fakeredis.FakeServer()
    server.connected = False
    return ResponseCache(fakeredis.FakeRedis(server=server, decode_responses=True), ttl=60)


# --- Key (design D8) ---


def test_key_has_versioned_prefix_and_sha256_digest():
    """Requirement: Caché exact-match (formato de clave `estimator:llm:v1:` + SHA-256)."""
    key = key_for()
    assert KEY_PREFIX == "estimator:llm:v1:"
    assert key.startswith(KEY_PREFIX)
    digest = key.removeprefix(KEY_PREFIX)
    assert len(digest) == 64
    int(digest, 16)  # hexadecimal


def test_key_is_deterministic_and_independent_of_param_order():
    """Requirement: Caché exact-match / Scenario: Segunda petición idéntica (clave estable)."""
    assert key_for() == key_for()
    assert cache_key({"a": 1, "b": 2}) == cache_key({"b": 2, "a": 1})


def test_key_includes_max_tokens():
    """Requirement: Caché exact-match (el límite de salida forma parte de la clave)."""
    assert key_for() != cache_key(
        {"system": SYSTEM, "messages": MESSAGES, "model": MODEL, "max_tokens": MAX_TOKENS + 1}
    )


@pytest.mark.parametrize(
    ("system", "messages", "model"),
    [
        ("OTHER-SYSTEM", MESSAGES, MODEL),
        (SYSTEM, [{"role": "user", "content": "Necesitamos una web de reservas."}], MODEL),
        (
            SYSTEM,
            [
                {"role": "user", "content": "Necesitamos una web de reservas"},
                {"role": "assistant", "content": "E"},
                {"role": "user", "content": "ajuste"},
            ],
            MODEL,
        ),
        (SYSTEM, MESSAGES, "claude-haiku-4-5"),
    ],
    ids=["system", "one-char-transcription", "other-history", "model"],
)
def test_key_changes_with_system_messages_or_model(system, messages, model):
    """Requirement: Caché / Scenarios: Petición distinta y Cambio de configuración."""
    assert key_for(system, messages, model) != key_for()


# --- ResponseCache ---


def test_set_and_get_roundtrip_with_ttl(fake_redis, response_cache):
    """Requirement: Caché / Scenario: Caducidad (SETEX con el TTL configurado)."""
    value = {
        "text": "hola",
        "model": MODEL,
        "provider": "openai",
        "input_tokens": 10,
        "output_tokens": 5,
        "fallback_used": False,
        "cache_hit": True,  # per-request fields must not be persisted
        "latency_ms": 99,
    }
    response_cache.set("k", value)
    assert 0 < fake_redis.ttl("k") <= 60
    assert response_cache.ttl == 60
    stored = json.loads(fake_redis.get("k"))
    assert set(stored) == {
        "text",
        "model",
        "provider",
        "input_tokens",
        "output_tokens",
        "fallback_used",
    }
    assert response_cache.get("k") == stored


def test_get_returns_none_on_miss(response_cache):
    """Requirement: Caché / Scenario: Petición distinta (fallo de caché)."""
    assert response_cache.get("absent") is None
    assert response_cache.lookup("absent") == (None, True)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2]",
        json.dumps({"text": "x"}),
        json.dumps(
            {
                "text": "x",
                "model": "m",
                "provider": "p",
                "input_tokens": True,
                "output_tokens": 1,
                "fallback_used": False,
            }
        ),
    ],
    ids=["not-json", "not-a-dict", "missing-fields", "bool-as-int"],
)
def test_invalid_entry_counts_as_miss_with_warning(fake_redis, response_cache, raw):
    """Requirement: Caché / D8: entrada inválida = fallo de caché con `cache_entry_invalid`."""
    fake_redis.set("k", raw)
    with capture_logs() as logs:
        assert response_cache.lookup("k") == (None, True)
    assert len(events(logs, "cache_entry_invalid")) == 1
    assert len(events(logs, "cache_miss")) == 1


def test_ping_ok_and_ping_failure_never_raises(response_cache, down_cache):
    """Requirement: Degradación sin Redis (ping seguro, usado por /health)."""
    assert response_cache.ping() is True
    with capture_logs() as logs:
        assert down_cache.ping() is False
    assert events(logs, "cache_unavailable")[0]["op"] == "ping"


def test_get_response_cache_is_none_when_redis_url_is_empty(monkeypatch):
    """Requirement: Degradación sin Redis (REDIS_URL vacío desactiva la caché)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    assert cache_module.get_response_cache() is None


def test_get_response_cache_builds_lazy_client_with_short_timeouts_and_no_retries(monkeypatch):
    """Requirement: Degradación sin Redis (timeouts de 0,2 s y sin reintentos, sin conectar)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CACHE_TTL", "123")
    get_settings.cache_clear()
    cache = cache_module.get_response_cache()
    assert cache is not None
    assert cache.ttl == 123
    assert cache_module.get_response_cache() is cache  # lru_cache
    client = build_redis_client("redis://localhost:6379/0")  # no I/O: the network guard is on
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == 0.2
    assert kwargs["socket_timeout"] == 0.2
    assert client.get_retry().get_retries() == 0


# --- CachedProvider.generate ---


def test_second_identical_request_is_served_from_cache_without_calling_the_llm(response_cache):
    """Requirement: Caché / Scenario: Segunda petición idéntica."""
    inner = FakeProvider(text="estimación", input_tokens=1200, output_tokens=300)
    provider = cached(inner, response_cache)
    first = provider.generate(SYSTEM, MESSAGES)
    second = provider.generate(SYSTEM, MESSAGES)
    assert inner.call_count == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.text == first.text == "estimación"
    assert second.latency_ms >= 0


def test_different_request_calls_the_llm_and_is_a_miss(response_cache):
    """Requirement: Caché / Scenario: Petición distinta (un carácter o historial distinto)."""
    inner = FakeProvider()
    provider = cached(inner, response_cache)
    provider.generate(SYSTEM, MESSAGES)
    one_char = [{"role": "user", "content": MESSAGES[0]["content"] + "."}]
    other_history = [
        *MESSAGES,
        {"role": "assistant", "content": "E"},
        {"role": "user", "content": "a"},
    ]
    for messages in (one_char, other_history):
        result = provider.generate(SYSTEM, messages)
        assert result.cache_hit is False
    assert inner.call_count == 3


def test_changing_primary_model_does_not_reuse_the_stored_response(response_cache):
    """Requirement: Caché / Scenario: Cambio de configuración."""
    inner = FakeProvider()
    cached(inner, response_cache, "gpt-4o-mini").generate(SYSTEM, MESSAGES)
    result = cached(inner, response_cache, "claude-haiku-4-5").generate(SYSTEM, MESSAGES)
    assert result.cache_hit is False
    assert inner.call_count == 2


def test_entry_expires_after_ttl(fake_redis, response_cache):
    """Requirement: Caché / Scenario: Caducidad."""
    inner = FakeProvider()
    provider = cached(inner, response_cache)
    provider.generate(SYSTEM, MESSAGES)
    assert fake_redis.ttl(key_for()) > 0
    fake_redis.pexpire(key_for(), 1)
    time.sleep(0.05)
    assert provider.generate(SYSTEM, MESSAGES).cache_hit is False
    assert inner.call_count == 2


def test_hit_keeps_original_tokens_model_provider_and_fallback_flag(response_cache):
    """Requirement: Contenido del acierto / Scenario: Acierto con los datos originales."""
    inner = FakeProvider(
        text="del respaldo",
        model="claude-haiku-4-5",
        provider="anthropic",
        input_tokens=1200,
        output_tokens=300,
        fallback_used=True,
    )
    provider = cached(inner, response_cache)
    provider.generate(SYSTEM, MESSAGES)
    hit = provider.generate(SYSTEM, MESSAGES)
    assert (hit.text, hit.input_tokens, hit.output_tokens) == ("del respaldo", 1200, 300)
    assert (hit.model, hit.provider) == ("claude-haiku-4-5", "anthropic")
    assert hit.fallback_used is True
    assert hit.cache_hit is True
    assert hit.truncated is False


def test_estimation_service_reports_cache_hit_and_cost_from_stored_tokens(response_cache):
    """Requirement: Contenido del acierto (coste con los tokens guardados)."""
    inner = FakeProvider(model="claude-haiku-4-5", input_tokens=1000, output_tokens=500)
    service = EstimationService(cached(inner, response_cache))
    request = make_input()
    service.estimate(request)
    hit = service.estimate(request)
    assert hit.cache_hit is True
    assert hit.cost_usd == 0.0035
    assert inner.call_count == 1


def test_truncated_response_is_not_stored(fake_redis, response_cache):
    """Requirement: Qué no se guarda / Scenario: Respuesta truncada."""
    inner = FakeProvider(text="parcial", truncated=True)
    provider = cached(inner, response_cache)
    with capture_logs() as logs:
        first = provider.generate(SYSTEM, MESSAGES)
        second = provider.generate(SYSTEM, MESSAGES)
    assert first.truncated is True
    assert second.cache_hit is False
    assert inner.call_count == 2
    assert fake_redis.keys("*") == []
    assert {e["reason"] for e in events(logs, "cache_skipped")} == {"truncated"}


@pytest.mark.parametrize("text", ["", "   \n"])
def test_empty_or_blank_response_is_not_stored(fake_redis, response_cache, text):
    """Requirement: Qué no se guarda (texto vacío o en blanco, D8)."""
    inner = FakeProvider(text=text)
    with capture_logs() as logs:
        cached(inner, response_cache).generate(SYSTEM, MESSAGES)
    assert fake_redis.keys("*") == []
    assert events(logs, "cache_skipped")[0]["reason"] == "empty"


def test_failed_generation_is_not_stored_and_propagates(fake_redis, response_cache):
    """Requirement: Qué no se guarda (los errores no se guardan)."""
    inner = FakeProvider(error=LLMProviderError("openai"))
    with pytest.raises(LLMProviderError):
        cached(inner, response_cache).generate(SYSTEM, MESSAGES)
    assert fake_redis.keys("*") == []


def test_cache_none_only_delegates_and_sets_latency():
    """Requirement: Degradación sin Redis (REDIS_URL vacío: sin caché, respuesta correcta)."""
    inner = FakeProvider(text="ok")
    result = cached(inner, None).generate(SYSTEM, MESSAGES)
    assert (result.text, result.cache_hit) == ("ok", False)
    assert result.latency_ms >= 0
    stream = cached(inner, None).stream(SYSTEM, MESSAGES)
    assert list(stream) == ["ok"]
    assert stream.result.cache_hit is False


# --- CachedProvider.stream ---


def test_stream_stores_complete_response_and_next_stream_is_a_single_fragment_hit(
    fake_redis, response_cache
):
    """Requirement: Caché en streaming / Scenario: Acierto en streaming."""
    inner = FakeProvider(fragments=["uno ", "dos ", "tres"], input_tokens=1200, output_tokens=300)
    provider = cached(inner, response_cache)
    first = provider.stream(SYSTEM, MESSAGES)
    assert list(first) == ["uno ", "dos ", "tres"]
    assert first.result.cache_hit is False
    assert len(fake_redis.keys("*")) == 1

    second = provider.stream(SYSTEM, MESSAGES)
    assert list(second) == ["uno dos tres"]  # stored text replayed as one fragment
    assert second.result.cache_hit is True
    assert second.result.text == "uno dos tres"
    assert (second.result.input_tokens, second.result.output_tokens) == (1200, 300)
    assert inner.call_count == 1


def test_stream_result_is_available_after_a_hit_and_second_iteration_is_empty(response_cache):
    """Requirement: Caché en streaming (el acierto respeta el contrato de `LLMStream`)."""
    provider = cached(FakeProvider(fragments=["a", "b"]), response_cache)
    list(provider.stream(SYSTEM, MESSAGES))
    stream = provider.stream(SYSTEM, MESSAGES)
    with pytest.raises(RuntimeError):
        stream.result  # noqa: B018
    assert list(stream) == ["ab"]
    assert list(stream) == []
    assert stream.result.cache_hit is True


def test_response_is_shared_between_generate_and_stream(response_cache):
    """Requirement: Caché en streaming / Scenario: Compartida entre modos."""
    inner = FakeProvider(text="estimación de la API", fragments=["estimación de la API"])
    provider = cached(inner, response_cache)
    # `EstimationService.estimate` sends the rendered system and one user message.
    request = make_input()
    EstimationService(provider).estimate(request)
    system, user = render_estimation_prompt(request, "v1")
    stream = provider.stream(system, [{"role": "user", "content": user}])
    assert "".join(stream) == "estimación de la API"
    assert stream.result.cache_hit is True
    assert inner.call_count == 1


def test_generate_after_stream_is_also_a_hit(response_cache):
    """Requirement: Caché en streaming (compartida en el otro sentido)."""
    inner = FakeProvider(fragments=["a", "b"])
    provider = cached(inner, response_cache)
    list(provider.stream(SYSTEM, MESSAGES))
    assert provider.generate(SYSTEM, MESSAGES).cache_hit is True
    assert inner.call_count == 1


def test_truncated_stream_is_not_stored(fake_redis, response_cache):
    """Requirement: Qué no se guarda / Scenario: Respuesta truncada (streaming)."""
    provider = cached(FakeProvider(fragments=["a", "b"], truncated=True), response_cache)
    list(provider.stream(SYSTEM, MESSAGES))
    assert fake_redis.keys("*") == []


def test_empty_stream_is_not_stored(fake_redis, response_cache):
    """Requirement: Qué no se guarda (stream sin texto)."""
    provider = cached(FakeProvider(fragments=[]), response_cache)
    list(provider.stream(SYSTEM, MESSAGES))
    assert fake_redis.keys("*") == []


def test_stream_abandoned_by_the_consumer_is_not_stored(fake_redis, response_cache):
    """Requirement: Qué no se guarda / Scenario: Stream interrumpido (consumidor)."""
    provider = cached(FakeProvider(fragments=["a", "b", "c"]), response_cache)
    iterator = iter(provider.stream(SYSTEM, MESSAGES))
    assert next(iterator) == "a"
    iterator.close()
    assert fake_redis.keys("*") == []


def test_stream_failing_mid_way_is_not_stored(fake_redis, response_cache):
    """Requirement: Qué no se guarda / Scenario: Stream interrumpido (fallo del proveedor)."""
    inner = FakeProvider(fragments=["a", "b"], error=LLMProviderError("openai"), error_after=1)
    with pytest.raises(LLMProviderError):
        list(cached(inner, response_cache).stream(SYSTEM, MESSAGES))
    assert fake_redis.keys("*") == []


def test_stream_failing_before_first_fragment_is_not_stored(fake_redis, response_cache):
    """Requirement: Qué no se guarda (error antes del primer fragmento)."""
    inner = FakeProvider(fragments=[], error=LLMProviderError("openai"))
    with pytest.raises(LLMProviderError):
        list(cached(inner, response_cache).stream(SYSTEM, MESSAGES))
    assert fake_redis.keys("*") == []


# --- Degradación sin Redis ---


def test_redis_down_generate_succeeds_without_cache_and_warns_once(down_cache):
    """Requirement: Degradación sin Redis / Scenario: Redis caído."""
    inner = FakeProvider(text="correcta")
    with capture_logs() as logs:
        result = cached(inner, down_cache).generate(SYSTEM, MESSAGES)
    assert result.text == "correcta"
    assert result.cache_hit is False
    unavailable = events(logs, "cache_unavailable")
    # The failed `get` skips the `set`: a single failure per request (design D8).
    assert [e["op"] for e in unavailable] == ["get"]
    assert unavailable[0]["log_level"] == "warning"
    assert unavailable[0]["error_type"] == "ConnectionError"


def test_redis_down_stream_succeeds_without_cache(down_cache):
    """Requirement: Degradación sin Redis / Scenario: Redis caído (streaming)."""
    inner = FakeProvider(fragments=["a", "b"])
    stream = cached(inner, down_cache).stream(SYSTEM, MESSAGES)
    with capture_logs() as logs:
        assert list(stream) == ["a", "b"]
    assert stream.result.cache_hit is False
    assert [e["op"] for e in events(logs, "cache_unavailable")] == ["get"]


def test_redis_failing_on_set_only_still_returns_the_response(monkeypatch, response_cache):
    """Requirement: Degradación sin Redis (fallo del `set` tras un `get` correcto)."""

    def failing_set(*args, **kwargs):
        raise redis.ConnectionError("down")

    monkeypatch.setattr(response_cache._client, "set", failing_set)
    with capture_logs() as logs:
        result = cached(FakeProvider(text="ok"), response_cache).generate(SYSTEM, MESSAGES)
    assert result.text == "ok"
    assert [e["op"] for e in events(logs, "cache_unavailable")] == ["set"]
    assert not events(logs, "cache_stored")


def test_redis_down_does_not_leak_connection_error_message(down_cache):
    """Requirement: Degradación sin Redis (solo el tipo del error, sin URL ni mensaje)."""
    with capture_logs() as logs:
        down_cache.get("k")
    assert set(events(logs, "cache_unavailable")[0]) == {
        "event",
        "log_level",
        "op",
        "error_type",
    }


# --- Change s02-add-generation-knobs-and-evaluation: options in the key (response-cache delta) ---

HAIKU = Deployment("claude-haiku-4-5", "anthropic")
SAME_AS_PRIMARY = Deployment(MODEL, "openai")


def key_with(options: LLMCallOptions | None, system: str = SYSTEM, model: str = MODEL) -> str:
    return CachedProvider(FakeProvider(), None, model)._key(system, MESSAGES, options)


def test_default_options_and_no_options_share_the_key():
    """Requirement: Caché exact-match (sin opciones = valores por defecto)."""
    assert key_with(None) == key_with(LLMCallOptions()) == key_for()


@pytest.mark.parametrize(
    "options",
    [
        LLMCallOptions(max_tokens=4000),
        LLMCallOptions(thinking_budget=2048),
        LLMCallOptions(thinking_budget=4096),
        LLMCallOptions(model=HAIKU),
        LLMCallOptions(model=SAME_AS_PRIMARY),
    ],
    ids=["max_tokens", "thinking_budget", "other-thinking_budget", "other-model", "same-model"],
)
def test_key_changes_with_each_call_option(options):
    """Requirement: Caché / Scenario: Opciones distintas (max_tokens, thinking, modelo)."""
    assert key_with(options) != key_with(None)


def test_same_model_as_override_has_a_different_key_than_the_configured_primary():
    """Requirement: Caché / Scenario: Opciones distintas (`model` = primario, como override)."""
    # Same model name, but an override has no fallback: the response must not be shared.
    override = LLMCallOptions(model=SAME_AS_PRIMARY)
    assert key_with(override) != key_with(None)
    assert key_with(override) == key_with(LLMCallOptions(model=SAME_AS_PRIMARY))


def test_key_changes_with_the_rendered_system_prompt_of_each_prompt_option():
    """Requirement: Caché / Scenario: Opciones distintas (prompt y ejemplos)."""
    variants = {
        "base": ("v1", 3, {}),
        "v2": ("v2", 3, {}),
        "phases_table": ("v1", 3, {"output_format": "phases_table"}),
        "narrative": ("v1", 3, {"output_format": "narrative"}),
        "detailed": ("v1", 3, {"detail_level": "detailed"}),
        "summary": ("v1", 3, {"detail_level": "summary"}),
        "mobile_app": ("v1", 3, {"project_type": "mobile_app"}),
        "one-example": ("v1", 1, {}),
        "no-examples": ("v1", 0, {}),
    }
    keys = {}
    for name, (version, count, overrides) in variants.items():
        system, user = render_estimation_prompt(
            make_input(**overrides), version, num_examples=count
        )
        keys[name] = CachedProvider(FakeProvider(), None, MODEL)._key(
            system, [{"role": "user", "content": user}]
        )
    assert len(set(keys.values())) == len(variants)


@pytest.mark.parametrize(
    "other",
    [
        LLMCallOptions(max_tokens=100),
        LLMCallOptions(thinking_budget=1024),
        LLMCallOptions(model=SAME_AS_PRIMARY),
    ],
    ids=["max_tokens", "thinking_budget", "override-of-the-primary"],
)
def test_other_options_miss_and_identical_options_hit(response_cache, other):
    """Requirement: Caché / Scenarios: Segunda petición idéntica y Opciones distintas."""
    inner = FakeProvider(model=MODEL)
    provider = cached(inner, response_cache)
    base = LLMCallOptions(max_tokens=200)
    provider.generate(SYSTEM, MESSAGES, base)
    assert provider.generate(SYSTEM, MESSAGES, base).cache_hit is True
    assert inner.call_count == 1
    assert provider.generate(SYSTEM, MESSAGES, other).cache_hit is False
    assert inner.call_count == 2


def test_stream_respects_the_options_in_the_key(response_cache):
    """Requirement: Caché / Scenario: Opciones distintas (streaming)."""
    inner = FakeProvider(model=MODEL)
    provider = cached(inner, response_cache)
    list(provider.stream(SYSTEM, MESSAGES, LLMCallOptions(max_tokens=100)))
    hit = provider.stream(SYSTEM, MESSAGES, LLMCallOptions(max_tokens=100))
    list(hit)
    assert hit.result.cache_hit is True
    miss = provider.stream(SYSTEM, MESSAGES, LLMCallOptions(max_tokens=101))
    list(miss)
    assert miss.result.cache_hit is False
    assert inner.call_count == 2


def test_evaluation_is_not_part_of_the_key(response_cache):
    """Requirement: Caché / Scenario: Evaluación fuera de la clave."""
    inner = FakeProvider(model=MODEL)
    service = EstimationService(cached(inner, response_cache))
    request = make_input()
    first = service.estimate(request, GenerationOptions(evaluate=True))
    second = service.estimate(request, GenerationOptions(evaluate=False))
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert first.validation is not None
    assert second.validation is None
    assert inner.call_count == 1


@pytest.mark.parametrize(
    ("options", "overrides"),
    [
        (GenerationOptions(max_tokens=100), {}),
        (GenerationOptions(thinking_budget=1024), {}),
        (GenerationOptions(num_examples=1), {}),
        (GenerationOptions(use_examples=False), {}),
        (GenerationOptions(model=MODEL), {}),
        (GenerationOptions(prompt_version="v2"), {}),
        (GenerationOptions(), {"output_format": "phases_table"}),
        (GenerationOptions(), {"detail_level": "detailed"}),
        (GenerationOptions(), {"project_type": "mobile_app"}),
        (GenerationOptions(), {"description": "Una descripción distinta de la base"}),
        (
            GenerationOptions(),
            {"reference_projects": [{"name": "Ref", "summary": "Resumen de la referencia"}]},
        ),
    ],
    ids=[
        "max_tokens",
        "thinking_budget",
        "num_examples",
        "use_examples",
        "override-of-the-primary",
        "prompt_version-v2",
        "output_format",
        "detail_level",
        "project_type",
        "description",
        "reference_projects",
    ],
)
def test_estimation_with_other_options_is_not_served_from_the_cache(
    response_cache, options, overrides
):
    """Requirement: Caché / Scenarios: Petición distinta y Opciones distintas (servicio)."""
    inner = FakeProvider(model=MODEL)
    service = EstimationService(
        cached(inner, response_cache), resolve_model=lambda name: Deployment(name, "openai")
    )
    assert service.estimate(make_input()).cache_hit is False
    other = make_input(**overrides)
    assert service.estimate(other, options).cache_hit is False
    assert service.estimate(other, options).cache_hit is True  # same request: now a hit
    assert inner.call_count == 2


def test_description_differing_in_one_character_is_a_miss(response_cache):
    """Requirement: Caché / Scenario: Petición distinta (un solo carácter)."""
    inner = FakeProvider(model=MODEL)
    service = EstimationService(cached(inner, response_cache))
    assert service.estimate(make_input(description="x" * 30)).cache_hit is False
    assert service.estimate(make_input(description="x" * 29 + "y")).cache_hit is False
    assert inner.call_count == 2


def test_cache_hit_reports_the_prompt_version_of_the_current_request(response_cache):
    """Requirement: Endpoint de estimación / Scenario: Estimación desde la caché (versión)."""
    inner = FakeProvider(model=MODEL)
    service = EstimationService(cached(inner, response_cache))
    request = make_input()
    service.estimate(request, GenerationOptions(prompt_version="v1"))
    hit = service.estimate(request, GenerationOptions(prompt_version="v1"))
    assert (hit.cache_hit, hit.prompt_version) == (True, "v1")
