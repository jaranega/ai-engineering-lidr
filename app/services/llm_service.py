"""LLM service: LLM providers (LiteLLM with fallback, structured output with Instructor,
response cache) and the estimation use case: input guardrails, prompt render, structured
generation, costs computed in code, Markdown render and evaluation.

The use case has two entry points sharing one generation step (`_generate`): `estimate`
(one typed description, prompt v3) and `estimate_turn` (one stateless turn of a session,
prompt v4, with the history and the project metadata passed in as data; the session state
lives in `app.services.session_service`, S05 design D6)."""

import hashlib
import json
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import asdict, dataclass, replace
from functools import lru_cache, partial
from typing import Any, Generic, Literal, Protocol, TypeVar

import instructor
import structlog
from instructor.core import (
    IncompleteOutputException,
    InstructorRetryException,
    ResponseParsingError,
)
from litellm import Router
from pydantic import BaseModel, ValidationError

from app.attachments import ExtractedAttachment
from app.config import Deployment, Settings, get_settings
from app.domain import (
    ChatMessage,
    DetailLevel,
    EstimationInput,
    Language,
    OutputFormat,
    SessionTurnInput,
)
from app.estimation.compute import ComputedEstimate, compute_estimate
from app.estimation.render import render_markdown
from app.estimation.schema import EstimationResult, response_model_for
from app.guardrails.input import (
    GuardrailReason,
    InputGuardrailViolation,
    mask_pii,
    screen_texts,
)
from app.prompts.language import detect_language
from app.prompts.loader import (
    DEFAULT_PROMPT_VERSION,
    DEFAULT_SESSION_PROMPT_VERSION,
    NUM_EXAMPLES,
    PROMPT_VERSIONS,
    SESSION_PROMPT_VERSIONS,
    PromptVersion,
    SessionPromptVersion,
    UnknownPromptVersionError,
    render_estimation_prompt,
    render_session_prompt,
)
from app.services.cache import ResponseCache, cache_key, get_response_cache, key_prefix
from app.services.evaluation import StructureCheck, evaluate_estimation
from app.services.moderation import Moderator, NullModerator, get_moderator
from app.services.pricing import estimate_cost
from app.sessions.models import ProjectMetadata

logger = structlog.get_logger(__name__)

# Re-exported for the HTTP layer, so routers and schemas never import `app.prompts`
# (S04 design D8).
__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "DEFAULT_SESSION_PROMPT_VERSION",
    "MAX_MODEL_NAME_CHARS",
    "MAX_OUTPUT_TOKENS",
    "MAX_TOKENS",
    "NUM_EXAMPLES",
    "PROMPT_VERSIONS",
    "SESSION_PROMPT_VERSIONS",
    "ChatMessage",
    "ComputedEstimate",
    "EstimationOutcome",
    "EstimationService",
    "GenerationOptions",
    "GuardrailReason",
    "InputGuardrailViolation",
    "InvalidOptionError",
    "LLMCallOptions",
    "LLMProvider",
    "LLMProviderError",
    "LLMResult",
    "LLMStream",
    "Moderator",
    "PromptVersion",
    "SessionPromptVersion",
    "StructuredOutputError",
    "StructuredResult",
    "TurnOutcome",
    "get_llm_provider",
    "get_moderator",
]

# Default output token limit (S02 D7), overridable per request.
# Timeout and retries come from `Settings` (`LLM_TIMEOUT`, `LLM_RETRIES`).
MAX_TOKENS = 2048

# Per-request generation limits, shared with the HTTP schema.
MAX_OUTPUT_TOKENS = 16_384  # gpt-4o-mini maximum output
MAX_MODEL_NAME_CHARS = 100

# Validation retries of a structured generation (`STRUCTURED_MAX_RETRIES`), design D6.
DEFAULT_STRUCTURED_MAX_RETRIES = 2

T = TypeVar("T", bound=BaseModel)


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
class StructuredResult(Generic[T]):
    """Provider-agnostic structured generation result (structured output design D6).

    `value` is the validated `response_model` instance. Tokens are summed over every
    attempt (validation retries included); `model`/`provider` are the effective ones of
    the last response, the one that produced the valid object.
    """

    value: T
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    attempts: int = 1
    fallback_used: bool = False
    cache_hit: bool = False
    latency_ms: int = 0


@dataclass(frozen=True)
class LLMCallOptions:
    """Per-call options of a provider (design D4). `None` options mean these defaults.

    `model`: target deployment that replaces the primary and disables the fallback.
    `max_tokens`: requested output limit. Extended thinking was retired: it is not
    compatible with the forced tool call of the structured output (design D1-8).
    """

    model: Deployment | None = None
    max_tokens: int = MAX_TOKENS


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

    def generate_structured(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        options: LLMCallOptions | None = None,
    ) -> StructuredResult[T]: ...


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


def _call_structured(
    provider: LLMProvider,
    system: str,
    messages: Sequence[ChatMessage],
    response_model: type[T],
    options: LLMCallOptions | None,
) -> StructuredResult[T]:
    if options is None or options == LLMCallOptions():
        return provider.generate_structured(system, messages, response_model)
    return provider.generate_structured(system, messages, response_model, options)


class LLMProviderError(Exception):
    """Domain error for any LLM failure. The original exception is kept as `__cause__`."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"LLM provider '{provider}' failed")
        self.provider = provider


StructuredFailure = Literal["truncated", "invalid"]


class StructuredOutputError(LLMProviderError):
    """The structured output was cut at the token limit (`truncated`) or is still invalid
    after every validation retry (`invalid`); `attempts` calls were made.
    """

    def __init__(self, provider: str, reason: StructuredFailure, attempts: int) -> None:
        super().__init__(provider)
        self.reason: StructuredFailure = reason
        self.attempts = attempts


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _log_completed(
    result: "LLMResult | StructuredResult[Any]", mode: str, finish_reason: str | None
) -> None:
    extra: dict[str, Any] = (
        {"attempts": result.attempts}
        if isinstance(result, StructuredResult)
        else {"truncated": result.truncated}
    )
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
        **extra,
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


@dataclass
class _AttemptStats:
    """Per-call accumulator fed by Instructor hooks (structured output design D6).

    One instance per `generate_structured` call: FastAPI runs sync endpoints in a thread
    pool, so a shared client with hooks would mix counters between requests.
    """

    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str | None = None
    finish_reason: str | None = None
    parse_errors: int = 0

    def on_response(self, response: Any) -> None:
        self.attempts += 1
        usage = getattr(response, "usage", None)
        if usage:
            self.input_tokens += getattr(usage, "prompt_tokens", None) or 0
            self.output_tokens += getattr(usage, "completion_tokens", None) or 0
        self.model_id = _model_id(response)
        choices = getattr(response, "choices", None) or []
        self.finish_reason = getattr(choices[0], "finish_reason", None) if choices else None

    def on_parse_error(self, error: Exception, **metadata: Any) -> None:
        self.parse_errors += 1
        # Never the error text: a Pydantic message includes the invalid values.
        errors = error.errors() if isinstance(error, ValidationError) else None
        logger.warning(
            "structured_output_retry",
            attempt=metadata.get("attempt_number", self.attempts),
            error_type=type(error).__name__,
            error_count=len(errors) if errors is not None else 1,
            # Field paths and error types only (e.g. "phases.2.assumptions:too_short"): no values.
            error_locs=sorted(
                {".".join(str(p) for p in e["loc"]) + ":" + e["type"] for e in errors or []}
            ),
            # Root-level errors come from our coherence validators, whose messages are fixed
            # text naming fields, never values (app/estimation/schema.py).
            coherence_errors=sorted({e["msg"] for e in errors or [] if not e["loc"]}),
            last_attempt=bool(metadata.get("is_last_attempt", False)),
        )


# Exceptions Instructor retries (and wraps in `InstructorRetryException` when the retries
# run out): the object did not parse or validate.
_INVALID_OUTPUT_ERRORS: tuple[type[Exception], ...] = (
    ValidationError,
    json.JSONDecodeError,
    ResponseParsingError,
)


def _structured_failure(exc: Exception) -> StructuredFailure | None:
    """`truncated`/`invalid` for an output failure, `None` for any other error."""
    if isinstance(exc, IncompleteOutputException):
        return "truncated"
    if isinstance(exc, InstructorRetryException):
        # Instructor 1.17 wraps every final failure, transport errors included; the
        # original exception is the cause.
        cause = exc.__cause__
        if isinstance(cause, IncompleteOutputException):
            return "truncated"
        return "invalid" if isinstance(cause, _INVALID_OUTPUT_ERRORS) else None
    if isinstance(exc, _INVALID_OUTPUT_ERRORS):
        return "invalid"
    return None


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
        structured_max_retries: int = DEFAULT_STRUCTURED_MAX_RETRIES,
    ) -> None:
        self._router = router
        self._primary = primary
        self._fallback = fallback
        self._router_factory = router_factory
        self._structured_max_retries = structured_max_retries

    def _plan(
        self, system: str, messages: Sequence[ChatMessage], options: LLMCallOptions | None
    ) -> _CallPlan:
        """Arguments shared by every mode (model, messages and limits)."""
        options = options or LLMCallOptions()
        override = options.model is not None
        target = options.model or self._primary
        max_tokens = options.max_tokens
        kwargs: dict[str, Any] = {
            "model": PRIMARY_GROUP,
            # LiteLLM moves the system message to Anthropic's `system` parameter.
            "messages": [{"role": "system", "content": system}, *(dict(m) for m in messages)],
            "max_tokens": max_tokens,
        }
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

    def _log_started(
        self, mode: str, messages: Sequence[ChatMessage], plan: _CallPlan, **extra: Any
    ) -> None:
        logger.info(
            "llm_call_started",
            mode=mode,
            primary_model=self._primary.model,
            model_override=plan.target.model if plan.override else None,
            max_tokens=plan.max_tokens,
            messages=len(messages),
            **extra,
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

    def _fail_structured(
        self, exc: Exception, start: float, target: Deployment, attempts: int
    ) -> LLMProviderError:
        # An exhausted retry wraps the real error: log the type of the original.
        cause = exc.__cause__ if isinstance(exc, InstructorRetryException) else None
        original = cause if isinstance(cause, Exception) else exc
        logger.error(
            "llm_call_failed",
            error_type=type(original).__name__,
            status_code=getattr(original, "status_code", None),
            mode="structured",
            latency_ms=_elapsed_ms(start),
            attempts=attempts,
        )
        reason = _structured_failure(exc)
        if reason is None:
            return LLMProviderError(provider=target.provider)
        logger.warning("structured_output_failed", reason=reason, attempts=attempts)
        return StructuredOutputError(target.provider, reason, attempts)

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

    def generate_structured(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        options: LLMCallOptions | None = None,
    ) -> StructuredResult[T]:
        """Validated `response_model` instance via Instructor (`Mode.TOOLS`) over the Router.

        Instructor retries only parse/validation errors (at most `structured_max_retries`
        times, sending the error back to the model); transport errors, retries per model
        and the fallback stay in the Router. No `context`/`validation_context` is ever
        passed, so message content is never rendered as a template, and no `timeout`
        (it would cap the whole retry loop; the per-call timeout is in the Router).
        Raise `StructuredOutputError` (`truncated`/`invalid`) or `LLMProviderError`.
        """
        mode = "structured"
        start = time.perf_counter()
        target = (options.model if options else None) or self._primary
        stats = _AttemptStats()
        try:
            plan = self._plan(system, messages, options)
            self._log_started(mode, messages, plan, max_retries=self._structured_max_retries)
            # Per call: the hooks write into this call's accumulator only.
            client = instructor.from_litellm(plan.router.completion, mode=instructor.Mode.TOOLS)
            client.on("completion:response", stats.on_response)
            client.on("parse:error", stats.on_parse_error)
            value, _ = client.chat.completions.create_with_completion(
                model=PRIMARY_GROUP,
                messages=plan.kwargs["messages"],
                max_tokens=plan.max_tokens,
                response_model=response_model,
                max_retries=self._structured_max_retries,
                # litellm 1.106.x imports its MCP handler whenever `tools` is present, which
                # needs the optional `mcp` package; we never send MCP tools, so skip it.
                _skip_mcp_handler=True,
            )
        except Exception as exc:
            raise self._fail_structured(exc, start, target, stats.attempts) from exc

        deployment, fallback_used = self._effective(stats.model_id, mode, plan)
        result = StructuredResult(
            value=value,
            model=deployment.model,
            provider=deployment.provider,
            input_tokens=stats.input_tokens,
            output_tokens=stats.output_tokens,
            attempts=stats.attempts,
            fallback_used=fallback_used,
            latency_ms=_elapsed_ms(start),
        )
        _log_completed(result, mode, stats.finish_reason)
        return result


def _schema_id(response_model: type[BaseModel]) -> str:
    """Name plus a short hash of the JSON Schema: a field or description change yields a
    new cache key without a manual version (structured output design D7).
    """
    schema = json.dumps(response_model.model_json_schema(), sort_keys=True)
    return f"{response_model.__name__}:{hashlib.sha256(schema.encode()).hexdigest()[:12]}"


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
        return cache_key(self._key_params(system, messages, options))

    def _key_params(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        options: LLMCallOptions | None,
    ) -> dict[str, Any]:
        options = options or LLMCallOptions()
        return {
            "system": system,
            "messages": [dict(m) for m in messages],
            "model": options.model.model if options.model else self._key_model,
            "model_override": options.model is not None,
            "max_tokens": options.max_tokens,
        }

    def _structured_key(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[BaseModel],
        options: LLMCallOptions | None,
    ) -> str:
        # `kind` keeps structured keys apart from text ones; the requirements (D2) change
        # which answers are valid, so they are part of the key. The retry count is not.
        params = {
            **self._key_params(system, messages, options),
            "kind": "structured",
            "schema": _schema_id(response_model),
            "requirements": {
                "narrative": bool(getattr(response_model, "require_narrative", False)),
                "phase_assumptions": bool(
                    getattr(response_model, "require_phase_assumptions", False)
                ),
            },
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

    def generate_structured(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        options: LLMCallOptions | None = None,
    ) -> StructuredResult[T]:
        """Cached structured generation (structured output design D7).

        Stores the validated object as returned by the LLM (before any computation), and
        validates it again on a hit with the `response_model` of the request: an entry
        that no longer validates is logged as `cache_entry_invalid` and treated as a
        miss. Failures raise before the `set`, so nothing invalid is ever stored.
        """
        start = time.perf_counter()
        cache = self._cache
        if cache is None:
            result = _call_structured(self._inner, system, messages, response_model, options)
            return replace(result, latency_ms=_elapsed_ms(start))
        key = self._structured_key(system, messages, response_model, options)
        stored, available = cache.lookup(key)
        if stored is not None and "value" in stored:
            try:
                value = response_model.model_validate(stored["value"])
            except ValidationError:
                logger.warning("cache_entry_invalid", key_prefix=key_prefix(key))
            else:
                hit = StructuredResult(
                    value=value,
                    model=stored["model"],
                    provider=stored["provider"],
                    input_tokens=stored["input_tokens"],
                    output_tokens=stored["output_tokens"],
                    attempts=stored["attempts"],
                    fallback_used=stored["fallback_used"],
                    cache_hit=True,
                    latency_ms=_elapsed_ms(start),
                )
                _log_completed(hit, "structured", finish_reason=None)
                return hit
        result = _call_structured(self._inner, system, messages, response_model, options)
        if available:  # skip the set when Redis already failed for this request
            cache.set(
                key,
                {
                    "value": result.value.model_dump(mode="json"),
                    "model": result.model,
                    "provider": result.provider,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "fallback_used": result.fallback_used,
                    "attempts": result.attempts,
                },
            )
        return replace(result, latency_ms=_elapsed_ms(start))


def build_provider(settings: Settings) -> LLMProvider:
    """Compose the cache over the LiteLLM provider (no network at build time).

    Override Routers are built on first use and kept per deployment (design D4).
    """
    router_factory = lru_cache(maxsize=8)(partial(build_override_router, settings))
    return CachedProvider(
        LiteLLMProvider(
            build_router(settings),
            settings.primary,
            settings.fallback,
            router_factory,
            structured_max_retries=settings.structured_max_retries,
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

    Defaults reproduce the behaviour without options: prompt `v3`, primary model with
    fallback, `max_tokens = 2048`, every example and structural evaluation on.
    `use_examples=False` is equivalent to `num_examples=0`.
    """

    prompt_version: str = DEFAULT_PROMPT_VERSION
    num_examples: int = NUM_EXAMPLES
    use_examples: bool = True
    model: str | None = None
    max_tokens: int = MAX_TOKENS
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


def _prompt_version_message(allowed: Sequence[str]) -> str:
    return f"must be one of {', '.join(allowed)}"


def _check_options(options: GenerationOptions, allowed: Sequence[str] = PROMPT_VERSIONS) -> None:
    """Range checks, duplicated from the HTTP layer for non-HTTP callers.

    `allowed` are the prompt versions of the entry point (`PROMPT_VERSIONS` for
    `estimate`, `SESSION_PROMPT_VERSIONS` for `estimate_turn`).
    """
    if options.prompt_version not in allowed:
        raise InvalidOptionError("prompt_version", _prompt_version_message(allowed))
    if not 0 <= options.num_examples <= NUM_EXAMPLES:
        raise InvalidOptionError("num_examples", f"must be between 0 and {NUM_EXAMPLES}")
    if options.model is not None and not 1 <= len(options.model.strip()) <= MAX_MODEL_NAME_CHARS:
        raise InvalidOptionError("model", f"must have 1 to {MAX_MODEL_NAME_CHARS} characters")
    if not 1 <= options.max_tokens <= MAX_OUTPUT_TOKENS:
        raise InvalidOptionError("max_tokens", f"must be between 1 and {MAX_OUTPUT_TOKENS}")


@dataclass(frozen=True)
class EstimationOutcome:
    """Domain result of an estimation; the router maps it to the HTTP response.

    `result` is the validated estimate with the costs and totals computed by the code,
    and `text` its server-side Markdown render. `prompt_version` is the version actually
    rendered for this request (never cached: a cache hit reports the version of the
    current request). Tokens are summed over every structured attempt.
    """

    text: str
    result: ComputedEstimate
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    # Always False: a structured output cut at the token limit is an error (D6).
    truncated: bool = False
    fallback_used: bool = False
    cache_hit: bool = False
    # Total server time of the use case (guardrails, render, LLM call and evaluation).
    latency_ms: int = 0
    prompt_version: str = DEFAULT_PROMPT_VERSION
    # None when `evaluate` is off or the estimate is out of scope (D9).
    validation: StructureCheck | None = None


def _default_resolve_model(name: str) -> Deployment:
    return get_settings().resolve_request_model(name)


def _input_texts(request: EstimationInput) -> list[tuple[str, str]]:
    """Every free text of the request with its field, for the guardrails (D8)."""
    texts = [("description", request.description)]
    for project in request.reference_projects or []:
        texts.append(("reference_projects", project.name))
        texts.append(("reference_projects", project.summary))
    return texts


def _block(
    reason: GuardrailReason, field: str, rule: str, index: int | None = None
) -> InputGuardrailViolation:
    # Reason, field, rule id and attachment index only: never the matched fragment, the
    # description, the transcript nor an attachment name.
    logger.warning("input_guardrail_blocked", reason=reason, field=field, rule=rule, index=index)
    return InputGuardrailViolation(reason, field=field, rule=rule, index=index)


@dataclass(frozen=True)
class TurnOutcome:
    """Result of one session turn (S05 design D6): the estimation outcome plus the `user`
    message to store in the history (each attachment's text replaced by `[…]`).
    """

    outcome: EstimationOutcome
    history_user: str


def _masked(attachments: Sequence[ExtractedAttachment]) -> list[tuple[str, str]]:
    """`(name, text)` of each attachment with its personal data masked (D8).

    Logs `attachment_pii_masked` with the counts per kind (name and text together), only
    when something was masked; never the name nor the data.
    """
    masked: list[tuple[str, str]] = []
    for position, attachment in enumerate(attachments):
        name, name_counts = mask_pii(attachment.name)
        text, text_counts = mask_pii(attachment.text)
        counts = {kind: name_counts[kind] + text_counts[kind] for kind in text_counts}
        if any(counts.values()):
            logger.info("attachment_pii_masked", index=position, **counts)
        masked.append((name, text))
    return masked


class EstimationService:
    """Input guardrails, prompt render, structured generation, costs, render, evaluation.

    `resolve_model(name) -> Deployment` resolves a model override and raises `ValueError`
    (without secrets) if it is unusable; by default `Settings.resolve_request_model`.
    `moderator` checks the input after the regex guardrails; by default none
    (`NullModerator`), so callers and tests without it keep working.
    """

    def __init__(
        self,
        provider: LLMProvider,
        resolve_model: Callable[[str], Deployment] | None = None,
        moderator: Moderator | None = None,
    ) -> None:
        self._provider = provider
        self._resolve_model = resolve_model or _default_resolve_model
        self._moderator = moderator or NullModerator()

    def _target(self, options: GenerationOptions) -> Deployment | None:
        if options.model is None:
            return None
        try:
            return self._resolve_model(options.model)
        except ValueError as exc:
            raise InvalidOptionError("model", str(exc)) from None

    def _screen(self, request: EstimationInput) -> None:
        """Regex guardrails, then moderation (D8): cheap first, and personal data is
        rejected before any text is sent to the moderation service.
        """
        texts = _input_texts(request)
        hit = screen_texts(texts)
        if hit is not None:
            raise _block(hit.reason, hit.field, hit.rule)
        if self._moderator.flagged([text for _, text in texts]) is True:
            raise _block("moderation", "input", "moderation")

    def estimate(
        self, request: EstimationInput, options: GenerationOptions | None = None
    ) -> EstimationOutcome:
        """Estimate the project described by `request`.

        Order (D8): option checks, input guardrails and moderation, prompt render,
        structured generation (cache, LLM with validation retries), costs and totals in
        code, Markdown render, evaluation. Sends the rendered system prompt and a single
        `user` message. Raise `InvalidOptionError` before any LLM call if an option is
        unusable, `InputGuardrailViolation` if the input is blocked (no cache nor LLM),
        and `LLMProviderError` (`StructuredOutputError` included) if the provider fails.
        """
        start = time.perf_counter()
        options = options or GenerationOptions()
        _check_options(options)
        target = self._target(options)
        num_examples = options.effective_num_examples
        logger.info(
            "estimation_requested",
            mode="estimate",
            prompt_version=options.prompt_version,
            output_format=request.output_format.value,
            detail_level=request.detail_level.value,
            project_type=request.project_type.value,
            num_examples=num_examples,
            model_override=target.model if target else None,
            max_tokens=options.max_tokens,
            evaluate=options.evaluate,
        )
        self._screen(request)

        try:
            system, user = render_estimation_prompt(
                request, options.prompt_version, num_examples=num_examples
            )
        except UnknownPromptVersionError:
            # Unreachable after `_check_options`; never a 500 for a version.
            raise InvalidOptionError(
                "prompt_version", _prompt_version_message(PROMPT_VERSIONS)
            ) from None
        # Labels follow the detected language of the description; the model's own
        # `language` decides only when detection is ambiguous (D5).
        return self._generate(
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=request.output_format,
            detail_level=request.detail_level,
            language=detect_language(request.description),
            language_source="detected",
            target=target,
            options=options,
            start=start,
        )

    def _generate(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        output_format: OutputFormat,
        detail_level: DetailLevel,
        language: Language | None,
        language_source: str,
        target: Deployment | None,
        options: GenerationOptions,
        start: float,
    ) -> EstimationOutcome:
        """Shared generation step of `estimate` and `estimate_turn` (S05 design D6).

        Structured call (cache, LLM with validation retries) -> costs and totals in code ->
        Markdown render -> evaluation. `language` is the effective language of the labels
        when known; otherwise the model's `language` decides (`language_source="llm"`).
        """
        call_options = LLMCallOptions(model=target, max_tokens=options.max_tokens)
        response_model: type[EstimationResult] = response_model_for(output_format, detail_level)
        structured = _call_structured(
            self._provider, system, messages, response_model, call_options
        )

        effective = language or structured.value.language
        computed = compute_estimate(structured.value, effective)
        text = render_markdown(computed, output_format, detail_level)
        logger.info(
            "estimation_rendered",
            output_format=output_format.value,
            language=effective,
            language_source=language_source if language else "llm",
            out_of_scope=computed.out_of_scope,
        )

        validation = None
        if options.evaluate:
            if computed.out_of_scope:
                # A notice without tables nor totals cannot be scored (D9).
                logger.info(
                    "estimation_evaluated",
                    output_format=output_format.value,
                    skipped="out_of_scope",
                )
            else:
                validation = evaluate_estimation(text, False, output_format)
                logger.info(
                    "estimation_evaluated",
                    output_format=validation.output_format,
                    score=validation.score,
                    issues=len(validation.issues),
                )

        return EstimationOutcome(
            text=text,
            result=computed,
            model=structured.model,
            provider=structured.provider,
            input_tokens=structured.input_tokens,
            output_tokens=structured.output_tokens,
            cost_usd=estimate_cost(
                structured.model, structured.input_tokens, structured.output_tokens
            ),
            fallback_used=structured.fallback_used,
            cache_hit=structured.cache_hit,
            latency_ms=_elapsed_ms(start),
            prompt_version=options.prompt_version,
            validation=validation,
        )

    def _screen_turn(self, transcript: str, attachments: Sequence[tuple[str, str]]) -> None:
        """Session turn guardrails (S05 design D8), on already masked attachments.

        Transcript regex (injection and personal data block), then each attachment's name
        and text (injection; its personal data is already masked) with its `index`, then a
        single moderation call with the transcript and every attachment text.
        """
        hit = screen_texts([("transcript", transcript)])
        if hit is not None:
            raise _block(hit.reason, hit.field, hit.rule)
        for index, (name, text) in enumerate(attachments):
            hit = screen_texts([("attachments", f"{name}\n{text}")])
            if hit is not None:
                raise _block(hit.reason, hit.field, hit.rule, index=index)
        texts = [transcript, *(text for _, text in attachments)]
        if self._moderator.flagged(texts) is True:
            raise _block("moderation", "input", "moderation")

    def estimate_turn(
        self,
        turn: SessionTurnInput,
        *,
        attachments: Sequence[ExtractedAttachment] = (),
        history: Sequence[ChatMessage] = (),
        project_metadata: ProjectMetadata | None = None,
        session_language: Language | None = None,
        options: GenerationOptions | None = None,
    ) -> TurnOutcome:
        """Estimate one turn of a session, without state (S05 design D6).

        Order: option checks (session prompt versions), PII masking of the attachments,
        regex of the transcript and of each attachment, one moderation call, response
        language (detected in the transcript, else the session's; never in the
        attachments), v4 render with the project metadata, then the shared generation
        step with `history` followed by the new `user` message. Raise
        `InvalidOptionError`, `InputGuardrailViolation` (nothing sent to the moderation,
        the cache nor the LLM after a block) or `LLMProviderError`.
        """
        start = time.perf_counter()
        options = options or GenerationOptions(prompt_version=DEFAULT_SESSION_PROMPT_VERSION)
        _check_options(options, allowed=SESSION_PROMPT_VERSIONS)
        target = self._target(options)
        num_examples = options.effective_num_examples
        logger.info(
            "estimation_requested",
            mode="session",
            prompt_version=options.prompt_version,
            output_format=turn.output_format.value,
            detail_level=turn.detail_level.value,
            project_type=turn.project_type.value,
            num_examples=num_examples,
            model_override=target.model if target else None,
            max_tokens=options.max_tokens,
            evaluate=options.evaluate,
            attachments=len(attachments),
            history_messages=len(history),
        )
        masked = _masked(attachments)
        self._screen_turn(turn.transcript, masked)

        detected = detect_language(turn.transcript)
        language = detected or session_language
        try:
            prompt = render_session_prompt(
                turn,
                options.prompt_version,
                attachments=masked,
                project_metadata=project_metadata,
                response_language=language,
                num_examples=num_examples,
            )
        except UnknownPromptVersionError:
            # Unreachable after `_check_options`; never a 500 for a version.
            raise InvalidOptionError(
                "prompt_version", _prompt_version_message(SESSION_PROMPT_VERSIONS)
            ) from None
        outcome = self._generate(
            system=prompt.system,
            messages=[*history, {"role": "user", "content": prompt.user}],
            output_format=turn.output_format,
            detail_level=turn.detail_level,
            language=language,
            language_source="detected" if detected else "session",
            target=target,
            options=options,
            start=start,
        )
        return TurnOutcome(outcome=outcome, history_user=prompt.history_user)
