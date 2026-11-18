"""Domain errors of the LLM providers, without LiteLLM nor Instructor.

They live apart from `app.services.llm_service` (which re-exports them) so the pure parts
of the Actor-Critic-Boss review (`boss.py`, `critic.py`, design s05-acb D5) can handle a
provider failure without importing the SDK layer.
"""

from typing import Literal

__all__ = ["LLMProviderError", "StructuredFailure", "StructuredOutputError"]


class LLMProviderError(Exception):
    """Domain error for any LLM failure. The original exception is kept as `__cause__`.

    `input_tokens`, `output_tokens` and `model` are the usage accumulated by the paid
    attempts of the failed call, so a caller that survives the failure (the
    Actor-Critic-Boss review, design s05-fix D2) can add it to the turn's totals. Each is
    `None` when unknown: then the cost of the failure cannot be computed. They never
    carry content and are not part of the message.
    """

    def __init__(
        self,
        provider: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(f"LLM provider '{provider}' failed")
        self.provider = provider
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model

    @property
    def usage_known(self) -> bool:
        """Whether tokens and model of the failed attempts are known (cost computable)."""
        return (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.model is not None
        )


StructuredFailure = Literal["truncated", "invalid"]


class StructuredOutputError(LLMProviderError):
    """The structured output was cut at the token limit (`truncated`) or is still invalid
    after every validation retry (`invalid`); `attempts` calls were made.
    """

    def __init__(
        self,
        provider: str,
        reason: StructuredFailure,
        attempts: int,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(
            provider, input_tokens=input_tokens, output_tokens=output_tokens, model=model
        )
        self.reason: StructuredFailure = reason
        self.attempts = attempts
