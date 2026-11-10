"""HTTP contract schemas."""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain import (
    DetailLevel,
    EstimationInput,
    OutputFormat,
    ProjectType,
    ReferenceProject,
)
from app.services.llm_service import (
    MAX_MODEL_NAME_CHARS,
    MAX_OUTPUT_TOKENS,
    MAX_THINKING_BUDGET,
    MAX_TOKENS,
    MIN_THINKING_BUDGET,
    NUM_EXAMPLES,
)

__all__ = [
    "DetailLevel",
    "EstimationInput",
    "EstimationMetrics",
    "EstimationRequest",
    "EstimationResponse",
    "HealthResponse",
    "OutputFormat",
    "ProjectType",
    "ReferenceProject",
    "StructureCheckResponse",
]


class EstimationRequest(EstimationInput):
    num_examples: int = Field(
        default=NUM_EXAMPLES,
        ge=0,
        le=NUM_EXAMPLES,
        description="Number of CAG examples to include.",
    )
    use_examples: bool = Field(
        default=True, description="If false, no examples are included (as num_examples = 0)."
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MODEL_NAME_CHARS,
        description="Override the model (OpenAI or Anthropic with a configured API key); "
        "no fallback is used. Null uses the primary model with fallback.",
    )
    max_tokens: int = Field(
        default=MAX_TOKENS,
        ge=1,
        le=MAX_OUTPUT_TOKENS,
        description="Output token limit of the estimation call.",
    )
    thinking_budget: int | None = Field(
        default=None,
        ge=MIN_THINKING_BUDGET,
        le=MAX_THINKING_BUDGET,
        description="Extended thinking budget (Anthropic only; ignored for OpenAI).",
    )
    evaluate: bool = Field(
        default=True, description="If false, the structural validation is skipped."
    )


class EstimationMetrics(BaseModel):
    model: str = Field(
        description="Effective model: the configured model of the deployment that answered.",
        examples=["gpt-4o-mini"],
    )
    provider: str = Field(
        description="Effective provider: the one that answered (primary or fallback).",
        examples=["openai"],
    )
    input_tokens: int = Field(description="Prompt tokens consumed.", examples=[3200])
    output_tokens: int = Field(description="Completion tokens generated.", examples=[650])
    cost_usd: float | None = Field(
        description="Approximate cost in USD; null if the model has no known price.",
        examples=[0.00087],
    )
    truncated: bool = Field(
        description="True if the estimation was cut off by the output token limit."
    )
    fallback_used: bool = Field(description="True if the fallback model answered.")
    cache_hit: bool = Field(description="True if the response was served from the cache.")
    latency_ms: int = Field(ge=0, description="Total latency in milliseconds.", examples=[1450])


class StructureCheckResponse(BaseModel):
    output_format: str = Field(description="Output format the check was applied to.")
    checks_total: int = Field(description="Number of checks applicable to the format.")
    has_title: bool
    has_breakdown_table: bool | None = Field(
        description="Null when the format has no breakdown table (narrative)."
    )
    has_totals: bool
    has_team: bool
    has_duration: bool
    rows_parsed: int
    declared_total_hours: float | None
    sum_row_hours: float | None
    hours_match: bool | None
    declared_total_cost: float | None
    sum_row_cost: float | None
    cost_match: bool | None
    finish_ok: bool
    score: float
    issues: list[str]


class EstimationResponse(EstimationMetrics):
    text: str = Field(description="Software estimation in Markdown.")
    prompt_version: str = Field(description="Prompt version effectively rendered.")
    validation: StructureCheckResponse | None = Field(
        description="Structural check of the estimation; null if evaluate is false."
    )


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    env: str = Field(examples=["development"])
    primary_model: str = Field(examples=["gpt-4o-mini"])
    fallback_model: str | None = Field(
        description="Fallback model, or null if the fallback is disabled.",
        examples=["claude-haiku-4-5"],
    )
    cache: Literal["ok", "unavailable"] = Field(description="Redis cache status.")
