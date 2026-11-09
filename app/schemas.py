"""HTTP contract schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.services.llm_service import MAX_TRANSCRIPTION_CHARS


class EstimationRequest(BaseModel):
    transcription: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TRANSCRIPTION_CHARS),
    ] = Field(
        description="Transcript of the client meeting (1 to 20 000 characters, after trimming).",
        examples=["Cliente: queremos una web para gestionar reservas de nuestras pistas de pádel."],
    )


class EstimationResponse(BaseModel):
    estimation: str = Field(description="Software estimation in Markdown.")
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


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    env: str = Field(examples=["development"])
    primary_model: str = Field(examples=["gpt-4o-mini"])
    fallback_model: str | None = Field(
        description="Fallback model, or null if the fallback is disabled.",
        examples=["claude-haiku-4-5"],
    )
    cache: Literal["ok", "unavailable"] = Field(description="Redis cache status.")
