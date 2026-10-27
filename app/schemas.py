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
        description="Requested model, as set in the configuration.", examples=["gpt-4o-mini"]
    )
    provider: Literal["openai", "anthropic"] = Field(
        description="LLM provider that served the request."
    )
    input_tokens: int = Field(description="Prompt tokens consumed.", examples=[3200])
    output_tokens: int = Field(description="Completion tokens generated.", examples=[650])
    cost_usd: float | None = Field(
        description="Approximate cost in USD; null if the model has no known price.",
        examples=[0.00087],
    )
