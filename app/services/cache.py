"""Exact-match response cache in Redis (design D8; structured entries: S04 structured
output design D7).

Knows only plain dicts and Redis: the LLM types live in `llm_service`, which depends on
this module and never the other way round.
"""

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import redis
import structlog
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import get_settings

logger = structlog.get_logger(__name__)

# `v1` versions the stored value format; prompt changes invalidate keys on their own
# because the whole system prompt is part of the hashed parameters.
KEY_PREFIX = "estimator:llm:v1:"

# Short timeouts and no retries: redis 8.1 defaults (5 s, 10 retries with backoff) would
# add seconds to every request while Redis is down.
REDIS_TIMEOUT_S = 0.2

# Value fields common to both kinds of entry; `cache_hit` and `latency_ms` belong to
# each request, and truncated responses are never stored, so none of them is persisted.
_COMMON_FIELDS: dict[str, type] = {
    "model": str,
    "provider": str,
    "input_tokens": int,
    "output_tokens": int,
    "fallback_used": bool,
}
# Payload: a text generation stores `text`; a structured one stores the validated object
# as a JSON dict in `value`, plus its `attempts` (structured output design D7).
_TEXT_FIELDS: dict[str, type] = {"text": str}
_STRUCTURED_FIELDS: dict[str, type] = {"value": dict, "attempts": int}

# Number of hash characters logged to correlate events without exposing the full key.
_KEY_PREFIX_CHARS = 12


def cache_key(params: Mapping[str, Any]) -> str:
    """Deterministic key from the generation parameters (system, messages, model, ...).

    Built from a dict so later changes can add parameters (e.g. per-request knobs)
    without touching this function.
    """
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def key_prefix(key: str) -> str:
    """Short hash prefix for logs: correlates events without exposing the full key."""
    return key.removeprefix(KEY_PREFIX)[:_KEY_PREFIX_CHARS]


class ResponseCache:
    """Get/set of serialized LLM responses with a TTL. Redis failures never propagate."""

    def __init__(self, client: redis.Redis, ttl: int) -> None:
        self._client = client
        self._ttl = ttl

    @property
    def ttl(self) -> int:
        return self._ttl

    def get(self, key: str) -> dict[str, Any] | None:
        """Stored value, or `None` on a miss, an invalid entry or a Redis failure."""
        value, _ = self.lookup(key)
        return value

    def lookup(self, key: str) -> tuple[dict[str, Any] | None, bool]:
        """Return `(value, available)`: `available` is False when Redis failed.

        Lets the caller skip the `set` for a request whose `get` already failed, so a
        down Redis costs one short timeout per request instead of two.
        """
        try:
            raw = self._client.get(key)
        except redis.RedisError as exc:
            _log_unavailable("get", exc)
            return None, False
        if raw is None:
            logger.info("cache_miss", key_prefix=key_prefix(key))
            return None, True
        value = _decode(raw)
        if value is None:
            logger.warning("cache_entry_invalid", key_prefix=key_prefix(key))
            logger.info("cache_miss", key_prefix=key_prefix(key))
            return None, True
        logger.info("cache_hit", key_prefix=key_prefix(key))
        return value, True

    def set(self, key: str, value: Mapping[str, Any]) -> None:
        """Store the value with SET ... EX and the configured TTL; no-op if Redis fails."""
        payload = _STRUCTURED_FIELDS if "value" in value else _TEXT_FIELDS
        data = {name: value[name] for name in (*_COMMON_FIELDS, *payload)}
        try:
            self._client.set(key, json.dumps(data, ensure_ascii=False), ex=self._ttl)
        except redis.RedisError as exc:
            _log_unavailable("set", exc)
            return
        logger.info("cache_stored", key_prefix=key_prefix(key), ttl=self._ttl)

    def ping(self) -> bool:
        """True if Redis answers; never raises."""
        try:
            return bool(self._client.ping())
        except redis.RedisError as exc:
            _log_unavailable("ping", exc)
            return False


def _log_unavailable(op: str, exc: redis.RedisError) -> None:
    # Type only: Redis error messages may include the URL (and its password).
    logger.warning("cache_unavailable", op=op, error_type=type(exc).__name__)


def _decode(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    payload = _STRUCTURED_FIELDS if "value" in value else _TEXT_FIELDS
    fields = {**_COMMON_FIELDS, **payload}
    for name, expected in fields.items():
        field = value.get(name)
        # bool is a subclass of int: reject it for token counts.
        if not isinstance(field, expected) or (expected is int and isinstance(field, bool)):
            return None
    return {name: value[name] for name in fields}


def build_redis_client(url: str) -> redis.Redis:
    """Lazy Redis client (no connection until the first command)."""
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=REDIS_TIMEOUT_S,
        socket_timeout=REDIS_TIMEOUT_S,
        retry=Retry(NoBackoff(), 0),
    )


@lru_cache
def get_response_cache() -> ResponseCache | None:
    """Process-wide cache, or `None` when `REDIS_URL` is empty (cache disabled)."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    return ResponseCache(build_redis_client(settings.redis_url), settings.cache_ttl)
