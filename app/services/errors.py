"""Domain errors of the LLM providers, without LiteLLM nor Instructor.

They live apart from `app.services.llm_service` (which re-exports them) so the pure parts
of the Actor-Critic-Boss review (`boss.py`, `critic.py`, design s05-acb D5) can handle a
provider failure without importing the SDK layer.
"""

from typing import Literal

__all__ = ["LLMProviderError", "StructuredFailure", "StructuredOutputError"]


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
