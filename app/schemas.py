"""HTTP contract schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.services.llm_service import (
    MAX_MODEL_NAME_CHARS,
    MAX_OUTPUT_TOKENS,
    MAX_THINKING_BUDGET,
    MAX_TOKENS,
    MAX_TRANSCRIPTION_CHARS,
    MIN_THINKING_BUDGET,
    NUM_EXAMPLES,
    ChatMessage,
    ExampleFormat,
    Preprocessing,
)

MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TRANSCRIPTION_CHARS),
]


class EstimationRequest(BaseModel):
    transcription: MessageText = Field(
        description="Transcript of the client meeting (1 to 20 000 characters, after trimming).",
        examples=["Cliente: queremos una web para gestionar reservas de nuestras pistas de pádel."],
    )
    preprocessing: Preprocessing = Field(
        default="none",
        description="Input strategy: none, inline_cleaning (prompt block) or two_phase "
        "(requirements extraction first).",
    )
    example_format: ExampleFormat = Field(
        default="markdown", description="Format of the CAG examples in the system prompt."
    )
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


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: MessageText = Field(
        description="Message text (1 to 20 000 characters, after trimming)."
    )


class StreamEstimationRequest(BaseModel):
    messages: list[ConversationMessage] = Field(
        description="Whole conversation: first and last message from `user`, roles alternating."
    )

    @model_validator(mode="after")
    def _check_structure(self) -> "StreamEstimationRequest":
        roles = [message.role for message in self.messages]
        if not roles:
            raise ValueError("messages must not be empty")
        if roles[0] != "user" or roles[-1] != "user":
            raise ValueError("the first and the last message must come from the user")
        if any(a == b for a, b in zip(roles, roles[1:], strict=False)):
            raise ValueError("roles must alternate between user and assistant")
        return self

    def chat_messages(self) -> list[ChatMessage]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


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
    has_title: bool
    has_breakdown_table: bool
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
    estimation: str = Field(description="Software estimation in Markdown.")
    preprocessing: Preprocessing = Field(description="Preprocessing strategy applied.")
    extracted_requirements: str | None = Field(
        description="Requirements extracted in the first phase; only with two_phase."
    )
    preprocessing_input_tokens: int = Field(description="Prompt tokens of the first phase.")
    preprocessing_output_tokens: int = Field(description="Completion tokens of the first phase.")
    preprocessing_cost_usd: float | None = Field(
        description="Approximate cost of the first phase in USD; not included in cost_usd."
    )
    validation: StructureCheckResponse | None = Field(
        description="Structural check of the estimation; null if evaluate is false."
    )


class ExampleContext(BaseModel):
    meeting_summary: str
    estimation: str


class ContextResponse(BaseModel):
    system_prompt: str = Field(description="System prompt a request with N messages would use.")
    examples: list[ExampleContext] = Field(description="Static CAG examples, in order.")
    max_message_chars: int = Field(description="Maximum characters per message, after trimming.")


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    env: str = Field(examples=["development"])
    primary_model: str = Field(examples=["gpt-4o-mini"])
    fallback_model: str | None = Field(
        description="Fallback model, or null if the fallback is disabled.",
        examples=["claude-haiku-4-5"],
    )
    cache: Literal["ok", "unavailable"] = Field(description="Redis cache status.")
