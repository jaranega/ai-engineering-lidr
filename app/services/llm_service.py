"""LLM service: LLM providers (LiteLLM with fallback, response cache) and the estimation
use case, which renders the versioned prompt templates (`app.prompts`) and evaluates the
result."""

import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import asdict, dataclass, replace
from functools import lru_cache, partial
from typing import Any, Literal, Protocol, TypedDict

import structlog
from litellm import Router

from app.config import Deployment, Settings, get_settings
from app.domain import EstimationInput
from app.prompts.loader import (
    DEFAULT_PROMPT_VERSION,
    NUM_EXAMPLES,
    PROMPT_VERSIONS,
    PromptVersion,
    UnknownPromptVersionError,
    render_estimation_prompt,
)
from app.services.cache import ResponseCache, cache_key, get_response_cache, key_prefix
from app.services.evaluation import StructureCheck, evaluate_estimation
from app.services.pricing import estimate_cost

logger = structlog.get_logger(__name__)

# Re-exported for the HTTP layer, so routers and schemas never import `app.prompts`
# (S04 design D8).
__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "MAX_MODEL_NAME_CHARS",
    "MAX_OUTPUT_TOKENS",
    "MAX_THINKING_BUDGET",
    "MAX_TOKENS",
    "MIN_THINKING_BUDGET",
    "NUM_EXAMPLES",
    "PROMPT_VERSIONS",
    "ChatMessage",
    "EstimationResult",
    "EstimationService",
    "GenerationOptions",
    "InvalidOptionError",
    "LLMCallOptions",
    "LLMProvider",
    "LLMProviderError",
    "LLMResult",
    "LLMStream",
    "PromptVersion",
    "get_llm_provider",
]

# Default output token limit (S02 D7), overridable per request.
# Timeout and retries come from `Settings` (`LLM_TIMEOUT`, `LLM_RETRIES`).
MAX_TOKENS = 2048

# Per-request generation limits, shared with the HTTP schema.
MAX_OUTPUT_TOKENS = 16_384  # gpt-4o-mini maximum output
MIN_THINKING_BUDGET = 1024  # Anthropic minimum
MAX_THINKING_BUDGET = 16_384
MAX_MODEL_NAME_CHARS = 100


class ChatMessage(TypedDict):
    """One conversation turn; the shape LiteLLM expects."""

    role: Literal["user", "assistant"]
    content: str


# --- Providers -------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResult:
    """Provider-agnostic generation result.

    `model`/`provider` are the *effective* ones: the configured deployment (primary or
    fallback) that answered, without provider prefix (design D4), never the API snapshot.
    """

    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    # Fields with defaults so test doubles stay valid without changes.
    # True when generation stopped at the effective output token limit.
    truncated: bool = False
    # True when the fallback deployment answered.
    fallback_used: bool = False
    # True when the result was served from the response cache.
    cache_hit: bool = False
    # End-to-end latency of this request, set by the outermost layer (design D8).
    latency_ms: int = 0


class LLMStream:
    """Streamed generation: iterate to get the text fragments, then read `.result`.

    Wraps a generator that yields the fragments and *returns* the `LLMResult`, so layers
    (cache) compose with `yield from`. The LLM call happens when iteration starts, not
    when the stream is built.
    """

    def __init__(self, gen: Generator[str, None, LLMResult]) -> None:
        self._gen = gen
        self._result: LLMResult | None = None

    def __iter__(self) -> Iterator[str]:
        # Already consumed: yield nothing and keep the result, without touching the
        # exhausted generator, so composed streams can re-iterate safely.
        if self._result is not None:
            return
        self._result = yield from self._gen

    @property
    def result(self) -> LLMResult:
        """Raise `RuntimeError` if the stream has not been fully consumed."""
        if self._result is None:
            raise RuntimeError("LLMStream.result is only available after full consumption")
        return self._result


@dataclass(frozen=True)
class LLMCallOptions:
    """Per-call options of a provider (design D4). `None` options mean these defaults.

    `model`: target deployment that replaces the primary and disables the fallback.
    `max_tokens`: requested output limit. `thinking_budget`: Anthropic extended thinking
    (effective limit `max_tokens + thinking_budget`); ignored with a warning on OpenAI.
    """

    model: Deployment | None = None
    max_tokens: int = MAX_TOKENS
    thinking_budget: int | None = None


class LLMProvider(Protocol):
    """Minimal provider interface: a system prompt and the conversation in, a result out.

    `options` is optional and last, so callers and doubles without it keep working.
    """

    def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMResult: ...

    def stream(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMStream: ...


def _call_generate(
    provider: LLMProvider,
    system: str,
    messages: Sequence[ChatMessage],
    options: LLMCallOptions | None,
) -> LLMResult:
    # Default options are not passed: providers without the parameter keep working.
    if options is None or options == LLMCallOptions():
        return provider.generate(system, messages)
    return provider.generate(system, messages, options)


def _call_stream(
    provider: LLMProvider,
    system: str,
    messages: Sequence[ChatMessage],
    options: LLMCallOptions | None,
) -> LLMStream:
    if options is None or options == LLMCallOptions():
        return provider.stream(system, messages)
    return provider.stream(system, messages, options)


class LLMProviderError(Exception):
    """Domain error for any LLM failure. The original exception is kept as `__cause__`."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"LLM provider '{provider}' failed")
        self.provider = provider


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _log_completed(result: LLMResult, mode: str, finish_reason: str | None) -> None:
    logger.info(
        "llm_call_completed",
        mode=mode,
        model=result.model,
        provider=result.provider,
        fallback_used=result.fallback_used,
        cache_hit=result.cache_hit,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=estimate_cost(result.model, result.input_tokens, result.output_tokens),
        latency_ms=result.latency_ms,
        finish_reason=finish_reason,
        truncated=result.truncated,
    )


# Router model groups; each holds one deployment whose `model_info.id` is the group name,
# so `_hidden_params["model_id"]` tells which one answered (design D3, D4).
PRIMARY_GROUP = "primary"
FALLBACK_GROUP = "fallback"


def _router_deployment(group: str, deployment: Deployment, settings: Settings) -> dict:
    litellm_params: dict[str, Any] = {
        # Explicit provider prefix from the config resolution (design D3).
        "model": f"{deployment.provider}/{deployment.model}",
        "api_key": settings.api_key(deployment.provider),
        "timeout": settings.llm_timeout,
    }
    if deployment.provider == "openai":
        # OpenAI rejects `thinking`: an Anthropic primary with thinking that falls back to
        # OpenAI answers without it instead of failing (this change, D4).
        litellm_params["additional_drop_params"] = ["thinking"]
    return {
        "model_name": group,
        "litellm_params": litellm_params,
        "model_info": {"id": group},
    }


def build_router(settings: Settings) -> Router:
    """LiteLLM Router: primary always first, fallback only after its retries (design D3).

    Two single-deployment groups instead of one shuffled group, so requests never
    load-balance to the fallback. No network at build time.
    """
    model_list = [_router_deployment(PRIMARY_GROUP, settings.primary, settings)]
    fallbacks: list[dict[str, list[str]]] = []
    if settings.fallback is not None:
        model_list.append(_router_deployment(FALLBACK_GROUP, settings.fallback, settings))
        fallbacks = [{PRIMARY_GROUP: [FALLBACK_GROUP]}]
    return Router(
        model_list=model_list,
        fallbacks=fallbacks,
        num_retries=settings.llm_retries,
        timeout=settings.llm_timeout,
    )


def build_override_router(settings: Settings, deployment: Deployment) -> Router:
    """Router for a per-request model override: a single `primary` group, no fallback.

    Same timeout and retries as `build_router`, so the override follows the same call
    path (model id, retries, errors, logs). No network at build time.
    """
    return Router(
        model_list=[_router_deployment(PRIMARY_GROUP, deployment, settings)],
        fallbacks=[],
        num_retries=settings.llm_retries,
        timeout=settings.llm_timeout,
    )


def _model_id(obj: object) -> str | None:
    hidden = getattr(obj, "_hidden_params", None)
    if isinstance(hidden, dict):
        model_id = hidden.get("model_id")
        if isinstance(model_id, str) and model_id:
            return model_id
    return None


def _close(response: object) -> None:
    # Release the HTTP connection when the consumer stops early (e.g. Streamlit's Stop).
    # Test doubles may be plain sequences without `close`.
    close = getattr(response, "close", None)
    if callable(close):
        close()


@dataclass(frozen=True)
class _CallPlan:
    """Resolved call: router, target deployment, request kwargs and effective limit."""

    router: Any
    target: Deployment
    override: bool
    kwargs: dict[str, Any]
    max_tokens: int


class LiteLLMProvider:
    """`LLMProvider` over a LiteLLM `Router` with a primary and an optional fallback.

    The Router is injected (a double or a real Router with `mock_response` in tests).
    `router_factory(deployment)` builds the single-deployment Router of a model override
    (design D4); without it, overrides fail with `LLMProviderError`.
    Any exception becomes `LLMProviderError`; logs never carry content or error messages.
    """

    def __init__(
        self,
        router: Any,
        primary: Deployment,
        fallback: Deployment | None,
        router_factory: Callable[[Deployment], Any] | None = None,
    ) -> None:
        self._router = router
        self._primary = primary
        self._fallback = fallback
        self._router_factory = router_factory

    def _plan(
        self, system: str, messages: Sequence[ChatMessage], options: LLMCallOptions | None
    ) -> _CallPlan:
        """Arguments shared by `generate` and `stream` (model, messages and limits)."""
        options = options or LLMCallOptions()
        override = options.model is not None
        target = options.model or self._primary
        max_tokens = options.max_tokens
        kwargs: dict[str, Any] = {
            "model": PRIMARY_GROUP,
            # LiteLLM moves the system message to Anthropic's `system` parameter.
            "messages": [{"role": "system", "content": system}, *(dict(m) for m in messages)],
        }
        budget = options.thinking_budget
        if budget is not None:
            # Decided on the target before calling: an OpenAI primary with an Anthropic
            # fallback does not get thinking either (design D4).
            if target.provider == "anthropic":
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
                max_tokens += budget
            else:
                logger.warning(
                    "thinking_budget_ignored", provider=target.provider, model=target.model
                )
        kwargs["max_tokens"] = max_tokens
        router = self._router
        if override:
            if self._router_factory is None:
                raise RuntimeError("model override requested without a router factory")
            router = self._router_factory(target)
        return _CallPlan(router, target, override, kwargs, max_tokens)

    def _effective(
        self, model_id: str | None, mode: str, plan: _CallPlan
    ) -> tuple[Deployment, bool]:
        """Deployment that answered and whether it was the fallback."""
        if plan.override:
            if model_id != PRIMARY_GROUP:
                logger.warning("llm_deployment_unknown", mode=mode, model_id=model_id)
            return plan.target, False
        if model_id == FALLBACK_GROUP and self._fallback is not None:
            return self._fallback, True
        if model_id != PRIMARY_GROUP:
            logger.warning("llm_deployment_unknown", mode=mode, model_id=model_id)
        return self._primary, False

    def _log_started(self, mode: str, messages: Sequence[ChatMessage], plan: _CallPlan) -> None:
        logger.info(
            "llm_call_started",
            mode=mode,
            primary_model=self._primary.model,
            model_override=plan.target.model if plan.override else None,
            max_tokens=plan.max_tokens,
            thinking="thinking" in plan.kwargs,
            messages=len(messages),
        )

    def _fail(
        self, exc: Exception, mode: str, start: float, fragments: int, target: Deployment
    ) -> LLMProviderError:
        # Type and status only: LiteLLM messages include the provider response body.
        logger.error(
            "llm_call_failed",
            error_type=type(exc).__name__,
            status_code=getattr(exc, "status_code", None),
            mode=mode,
            latency_ms=_elapsed_ms(start),
            fragments=fragments,
        )
        return LLMProviderError(provider=target.provider)

    def _finish(
        self,
        *,
        mode: str,
        plan: _CallPlan,
        text: str,
        model_id: str | None,
        input_tokens: int,
        output_tokens: int,
        finish_reason: str | None,
        start: float,
    ) -> LLMResult:
        deployment, fallback_used = self._effective(model_id, mode, plan)
        truncated = finish_reason == "length"
        if truncated:
            logger.warning(
                "llm_output_truncated",
                mode=mode,
                model=deployment.model,
                provider=deployment.provider,
                max_tokens=plan.max_tokens,
            )
        result = LLMResult(
            text=text,
            model=deployment.model,
            provider=deployment.provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            truncated=truncated,
            fallback_used=fallback_used,
            latency_ms=_elapsed_ms(start),
        )
        _log_completed(result, mode, finish_reason)
        return result

    def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMResult:
        mode = "generate"
        start = time.perf_counter()
        target = (options.model if options else None) or self._primary
        try:
            plan = self._plan(system, messages, options)
            self._log_started(mode, messages, plan)
            response = plan.router.completion(**plan.kwargs)
            choices = getattr(response, "choices", None) or []
            choice = choices[0] if choices else None
            message = getattr(choice, "message", None)
            text = getattr(message, "content", None) or ""
            finish_reason = getattr(choice, "finish_reason", None)
            usage = getattr(response, "usage", None)  # reported as 0 tokens when missing
            input_tokens = (getattr(usage, "prompt_tokens", None) or 0) if usage else 0
            output_tokens = (getattr(usage, "completion_tokens", None) or 0) if usage else 0
        except Exception as exc:
            raise self._fail(exc, mode, start, fragments=0, target=target) from exc
        return self._finish(
            mode=mode,
            plan=plan,
            text=text,
            model_id=_model_id(response),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            start=start,
        )

    def stream(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMStream:
        return LLMStream(self._stream(system, messages, options))

    def _stream(
        self, system: str, messages: Sequence[ChatMessage], options: LLMCallOptions | None
    ) -> Generator[str, None, LLMResult]:
        mode = "stream"
        start = time.perf_counter()
        target = (options.model if options else None) or self._primary
        response = None
        parts: list[str] = []
        input_tokens = output_tokens = 0
        finish_reason: str | None = None
        model_id: str | None = None
        try:
            plan = self._plan(system, messages, options)
            self._log_started(mode, messages, plan)
            response = plan.router.completion(
                **plan.kwargs,
                stream=True,
                # Adds a final chunk with `usage` (and possibly empty `choices`).
                stream_options={"include_usage": True},
            )
            for chunk in response:
                # After a pre-first-chunk fallback, the Router stamps the fallback's
                # hidden params on each chunk: the last chunk carrying an id wins.
                model_id = _model_id(chunk) or model_id
                usage = getattr(chunk, "usage", None)
                if usage:
                    input_tokens = getattr(usage, "prompt_tokens", None) or 0
                    output_tokens = getattr(usage, "completion_tokens", None) or 0
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                choice = choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None)
                # Empty fragments (e.g. the role-only first chunk) are skipped so the UI's
                # time-to-first-fragment is real.
                if content:
                    parts.append(content)
                    yield content
        except Exception as exc:  # open and mid-stream failures alike (design D5, D6)
            raise self._fail(exc, mode, start, fragments=len(parts), target=target) from exc
        finally:
            # GeneratorExit (consumer stopped early) is a BaseException: it is not wrapped
            # above and only runs this cleanup.
            if response is not None:
                _close(response)

        return self._finish(
            mode=mode,
            plan=plan,
            text="".join(parts),
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            start=start,
        )


class CachedProvider:
    """Exact-match cache decorator over any `LLMProvider` (design D2, D8).

    Checks the cache, delegates on a miss and stores complete, non-truncated, non-blank
    results. Sets the end-to-end `latency_ms`. With `cache=None` it only delegates.
    """

    def __init__(self, inner: LLMProvider, cache: ResponseCache | None, key_model: str) -> None:
        self._inner = inner
        self._cache = cache
        self._key_model = key_model

    def _key(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> str:
        # Every field that changes the request (this change, D7). Prompt options enter
        # through `system`/`messages`; `model_override` tells the same model requested as
        # an override (no fallback) from the configured primary (with fallback).
        options = options or LLMCallOptions()
        params = {
            "system": system,
            "messages": [dict(m) for m in messages],
            "model": options.model.model if options.model else self._key_model,
            "model_override": options.model is not None,
            "max_tokens": options.max_tokens,
            "thinking_budget": options.thinking_budget,
        }
        return cache_key(params)

    def _hit_result(self, stored: dict) -> LLMResult:
        return LLMResult(**stored, truncated=False, cache_hit=True)

    def _store(self, cache: ResponseCache, key: str, result: LLMResult) -> None:
        reason = "truncated" if result.truncated else "empty" if not result.text.strip() else None
        if reason is not None:
            logger.info("cache_skipped", reason=reason, key_prefix=key_prefix(key))
            return
        cache.set(key, asdict(result))

    def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMResult:
        start = time.perf_counter()
        cache = self._cache
        if cache is None:
            result = _call_generate(self._inner, system, messages, options)
            return replace(result, latency_ms=_elapsed_ms(start))
        key = self._key(system, messages, options)
        stored, available = cache.lookup(key)
        if stored is not None:
            result = replace(self._hit_result(stored), latency_ms=_elapsed_ms(start))
            _log_completed(result, "generate", finish_reason=None)
            return result
        result = _call_generate(self._inner, system, messages, options)
        if available:  # skip the set when Redis already failed for this request
            self._store(cache, key, result)
        return replace(result, latency_ms=_elapsed_ms(start))

    def stream(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None = None,
    ) -> LLMStream:
        return LLMStream(self._stream(system, messages, options))

    def _stream(
        self, system: str, messages: Sequence[ChatMessage], options: LLMCallOptions | None
    ) -> Generator[str, None, LLMResult]:
        start = time.perf_counter()
        cache = self._cache
        key, available = "", False
        if cache is not None:
            key = self._key(system, messages, options)
            stored, available = cache.lookup(key)
            if stored is not None:
                hit = self._hit_result(stored)
                if hit.text:
                    yield hit.text  # replayed as a single fragment
                result = replace(hit, latency_ms=_elapsed_ms(start))
                _log_completed(result, "stream", finish_reason=None)
                return result

        inner = _call_stream(self._inner, system, messages, options)
        # Exceptions and GeneratorExit leave here, so an interrupted stream is never stored.
        yield from inner
        result = inner.result
        if cache is not None and available:
            self._store(cache, key, result)
        return replace(result, latency_ms=_elapsed_ms(start))


def build_provider(settings: Settings) -> LLMProvider:
    """Compose the cache over the LiteLLM provider (no network at build time).

    Override Routers are built on first use and kept per deployment (design D4).
    """
    router_factory = lru_cache(maxsize=8)(partial(build_override_router, settings))
    return CachedProvider(
        LiteLLMProvider(
            build_router(settings), settings.primary, settings.fallback, router_factory
        ),
        get_response_cache(),
        settings.primary.model,
    )


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Provider built once per process (FastAPI dependency and Streamlit)."""
    return build_provider(get_settings())


# --- Estimation use case ---------------------------------------------------------------


@dataclass(frozen=True)
class GenerationOptions:
    """Per-request generation options, independent of HTTP.

    Defaults reproduce the behaviour without options: prompt `v1`, primary model with
    fallback, `max_tokens = 2048`, no thinking, every example and structural evaluation
    on. `use_examples=False` is equivalent to `num_examples=0`.
    """

    prompt_version: str = DEFAULT_PROMPT_VERSION
    num_examples: int = NUM_EXAMPLES
    use_examples: bool = True
    model: str | None = None
    max_tokens: int = MAX_TOKENS
    thinking_budget: int | None = None
    evaluate: bool = True

    @property
    def effective_num_examples(self) -> int:
        return self.num_examples if self.use_examples else 0


class InvalidOptionError(ValueError):
    """A generation option is not usable; raised before any LLM call.

    `field` is the option name (`prompt_version` comes from the query string, the rest
    from the body); `message` never contains secrets nor user text.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


_PROMPT_VERSION_MESSAGE = f"must be one of {', '.join(PROMPT_VERSIONS)}"


def _check_options(options: GenerationOptions) -> None:
    """Range checks, duplicated from the HTTP layer for non-HTTP callers."""
    if options.prompt_version not in PROMPT_VERSIONS:
        raise InvalidOptionError("prompt_version", _PROMPT_VERSION_MESSAGE)
    if not 0 <= options.num_examples <= NUM_EXAMPLES:
        raise InvalidOptionError("num_examples", f"must be between 0 and {NUM_EXAMPLES}")
    if options.model is not None and not 1 <= len(options.model.strip()) <= MAX_MODEL_NAME_CHARS:
        raise InvalidOptionError("model", f"must have 1 to {MAX_MODEL_NAME_CHARS} characters")
    if not 1 <= options.max_tokens <= MAX_OUTPUT_TOKENS:
        raise InvalidOptionError("max_tokens", f"must be between 1 and {MAX_OUTPUT_TOKENS}")
    budget = options.thinking_budget
    if budget is not None and not MIN_THINKING_BUDGET <= budget <= MAX_THINKING_BUDGET:
        raise InvalidOptionError(
            "thinking_budget",
            f"must be between {MIN_THINKING_BUDGET} and {MAX_THINKING_BUDGET}",
        )


@dataclass(frozen=True)
class EstimationResult:
    """Domain result of an estimation; the router maps it to the HTTP response.

    `prompt_version` is the version actually rendered for this request (never cached:
    a cache hit reports the version of the current request).
    """

    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    truncated: bool = False  # see LLMResult
    fallback_used: bool = False
    cache_hit: bool = False
    # Total server time of the use case (render, LLM call and evaluation).
    latency_ms: int = 0
    prompt_version: str = DEFAULT_PROMPT_VERSION
    validation: StructureCheck | None = None


def _to_estimation_result(result: LLMResult) -> EstimationResult:
    """Single place where the estimated cost is computed (from stored tokens on hits)."""
    return EstimationResult(
        text=result.text,
        model=result.model,
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=estimate_cost(result.model, result.input_tokens, result.output_tokens),
        truncated=result.truncated,
        fallback_used=result.fallback_used,
        cache_hit=result.cache_hit,
        latency_ms=result.latency_ms,
    )


def _default_resolve_model(name: str) -> Deployment:
    return get_settings().resolve_request_model(name)


class EstimationService:
    """Renders the prompt templates, calls the provider, evaluates and computes the cost.

    `resolve_model(name) -> Deployment` resolves a model override and raises `ValueError`
    (without secrets) if it is unusable; by default `Settings.resolve_request_model`.
    """

    def __init__(
        self,
        provider: LLMProvider,
        resolve_model: Callable[[str], Deployment] | None = None,
    ) -> None:
        self._provider = provider
        self._resolve_model = resolve_model or _default_resolve_model

    def _target(self, options: GenerationOptions) -> Deployment | None:
        if options.model is None:
            return None
        try:
            return self._resolve_model(options.model)
        except ValueError as exc:
            raise InvalidOptionError("model", str(exc)) from None

    def estimate(
        self, request: EstimationInput, options: GenerationOptions | None = None
    ) -> EstimationResult:
        """Estimate the project described by `request`.

        Sends the rendered system prompt and a single `user` message. Raise
        `InvalidOptionError` before any LLM call if an option is unusable (an unknown
        prompt version included), and `LLMProviderError` if the provider fails.
        """
        start = time.perf_counter()
        options = options or GenerationOptions()
        _check_options(options)
        target = self._target(options)
        num_examples = options.effective_num_examples
        logger.info(
            "estimation_requested",
            prompt_version=options.prompt_version,
            output_format=request.output_format.value,
            detail_level=request.detail_level.value,
            project_type=request.project_type.value,
            num_examples=num_examples,
            model_override=target.model if target else None,
            max_tokens=options.max_tokens,
            thinking_budget=options.thinking_budget,
            evaluate=options.evaluate,
        )

        try:
            system, user = render_estimation_prompt(
                request, options.prompt_version, num_examples=num_examples
            )
        except UnknownPromptVersionError:
            # Unreachable after `_check_options`; never a 500 for a version.
            raise InvalidOptionError("prompt_version", _PROMPT_VERSION_MESSAGE) from None
        messages: list[ChatMessage] = [{"role": "user", "content": user}]
        call_options = LLMCallOptions(
            model=target, max_tokens=options.max_tokens, thinking_budget=options.thinking_budget
        )
        llm_result = _call_generate(self._provider, system, messages, call_options)
        result = _to_estimation_result(llm_result)

        validation = None
        if options.evaluate:
            validation = evaluate_estimation(result.text, result.truncated, request.output_format)
            logger.info(
                "estimation_evaluated",
                output_format=validation.output_format,
                score=validation.score,
                issues=len(validation.issues),
            )

        return replace(
            result,
            latency_ms=_elapsed_ms(start),
            prompt_version=options.prompt_version,
            validation=validation,
        )
