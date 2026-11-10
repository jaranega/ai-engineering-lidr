"""Content moderation of the input via LiteLLM (OpenAI `omni-moderation-latest`), design D8.

Runs after the regex guardrails (so personal data is never sent) and before the cache
and the LLM. Fail-open: if the moderation call fails, the request goes on and
`moderation_failed` is logged. Logs carry counts, latency and category names only.

`litellm.moderation` in 1.106.4 accepts no `timeout`; it accepts a ready `client`, so the
moderator passes an OpenAI client built with its own short timeout and no retries
(D8: "cliente con timeout en la llamada"). The client is created once, without network.
"""

import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

import litellm
import openai
import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

MODERATION_MODEL = "omni-moderation-latest"
MODERATION_TIMEOUT_S = 5


class Moderator(Protocol):
    def flagged(self, texts: Sequence[str]) -> bool | None:
        """True if any text is flagged, False if none, `None` if it could not check."""
        ...


class NullModerator:
    """Moderation disabled: never checks and logs nothing per request."""

    def flagged(self, texts: Sequence[str]) -> bool | None:
        return None


class LiteLLMModerator:
    """One `litellm.moderation` call with every text; any failure returns `None`."""

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        self._api_key = api_key
        self._client = client or openai.OpenAI(
            api_key=api_key, timeout=MODERATION_TIMEOUT_S, max_retries=0
        )

    def flagged(self, texts: Sequence[str]) -> bool | None:
        if not texts:
            return False
        start = time.perf_counter()
        try:
            response = litellm.moderation(
                input=list(texts),
                model=MODERATION_MODEL,
                api_key=self._api_key,
                client=self._client,
            )
            results = list(getattr(response, "results", None) or [])
            flagged = any(bool(getattr(result, "flagged", False)) for result in results)
        except Exception as exc:
            # Type and status only: error messages may carry the request body.
            logger.warning(
                "moderation_failed",
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
            )
            return None
        event: dict[str, Any] = {
            "flagged": flagged,
            "texts": len(texts),
            "latency_ms": int((time.perf_counter() - start) * 1000),
        }
        if flagged:
            event["categories"] = _flagged_categories(results)
        logger.info("moderation_checked", **event)
        return flagged


def _flagged_categories(results: Sequence[Any]) -> list[str]:
    """Sorted names of the categories flagged in any result (names only, no scores)."""
    names: set[str] = set()
    for result in results:
        categories = getattr(result, "categories", None)
        if isinstance(categories, dict):
            names.update(name for name, value in categories.items() if value is True)
    return sorted(names)


def build_moderator(settings: Settings) -> Moderator:
    """`LiteLLMModerator` if `MODERATION_ENABLED` and `OPENAI_API_KEY`, else a null one.

    Logs `moderation_disabled` once, at build time, with the reason.
    """
    if not settings.moderation_enabled:
        logger.info("moderation_disabled", reason="config")
        return NullModerator()
    api_key = settings.api_key("openai")
    if api_key is None:
        logger.info("moderation_disabled", reason="no_openai_key")
        return NullModerator()
    return LiteLLMModerator(api_key)


@lru_cache
def get_moderator() -> Moderator:
    """Moderator built once per process (FastAPI dependency)."""
    return build_moderator(get_settings())
