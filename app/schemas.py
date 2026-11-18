"""HTTP contract schemas."""

from typing import Literal

from pydantic import BaseModel, Field

from app.attachments import AttachmentErrorReason
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
    MAX_TOKENS,
    NUM_EXAMPLES,
    BossDecision,
    ComputedEstimate,
    CriticCategory,
    CriticSeverity,
    CriticVerdict,
    FinalDecision,
    GuardrailReason,
    StopReason,
)
from app.sessions import ProjectMetadata, Tier, TierRule

__all__ = [
    "DetailLevel",
    "EstimationInput",
    "EstimationMetrics",
    "EstimationRequest",
    "EstimationResponse",
    "GuardrailError",
    "GuardrailErrorResponse",
    "HealthResponse",
    "OutputFormat",
    "ProjectMetadata",
    "ProjectType",
    "ReferenceProject",
    "ReviewIssue",
    "ReviewIteration",
    "ReviewSummary",
    "ReviewTrace",
    "AttachmentErrorDetail",
    "AttachmentErrorResponse",
    "SessionCreated",
    "SessionError",
    "SessionErrorResponse",
    "SessionEstimateResponse",
    "SessionGuardrailError",
    "SessionGuardrailErrorResponse",
    "SessionInfo",
    "StructureCheckResponse",
    "Tier",
    "TierRule",
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
        description="Always false on a 200: a structured output cut at the token limit is a 502."
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
    text: str = Field(description="Software estimation in Markdown (server-side render).")
    result: ComputedEstimate = Field(
        description="Validated structured estimate with costs and totals computed by the server."
    )
    prompt_version: str = Field(description="Prompt version effectively rendered.")
    validation: StructureCheckResponse | None = Field(
        description="Structural check of the estimation; null if evaluate is false or out of scope."
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


class GuardrailError(BaseModel):
    reason: GuardrailReason = Field(description="Why the input was blocked.")
    message: str = Field(description="Generic explanation; never echoes the input.")


class GuardrailErrorResponse(BaseModel):
    detail: GuardrailError


class SessionCreated(BaseModel):
    session_id: str


class ReviewIssue(BaseModel):
    category: CriticCategory = Field(description="Category of the defect found by the Critic.")
    severity: CriticSeverity = Field(description="Severity of the defect.")
    field_path: str = Field(description="Path of the estimate field the issue refers to.")
    description: str = Field(description="What is wrong.")


class ReviewIteration(BaseModel):
    iteration: int = Field(description="1-based iteration number.")
    verdict: CriticVerdict | None = Field(
        description="Critic verdict; null if the Critic was not called (out-of-scope draft)."
    )
    critic_confidence: int | None = Field(
        description="Critic confidence in its own review (0-100); null if not called."
    )
    critic_failed: bool = Field(description="True if the Critic call failed (verdict is accept).")
    decision: BossDecision = Field(description="Decision of the Boss for this iteration.")
    draft_confidence_pct: int = Field(description="Confidence of the draft of this iteration.")
    draft_total_hours: int = Field(description="Total hours of the draft of this iteration.")
    issues: list[ReviewIssue] = Field(description="Issues of the review, in order.")


class ReviewTrace(BaseModel):
    final_decision: FinalDecision = Field(description="Final decision of the Boss.")
    stop_reason: StopReason = Field(description="Why the review loop stopped.")
    iterations_run: int = Field(description="Iterations actually run.")
    max_iterations: int = Field(description="Iteration budget of the review.")
    iterations: list[ReviewIteration]


class ReviewSummary(BaseModel):
    final_decision: FinalDecision = Field(description="Final decision of the last review.")
    stop_reason: StopReason = Field(description="Why the last review stopped.")
    iterations_run: int = Field(description="Iterations run by the last review.")


class SessionInfo(BaseModel):
    session_id: str
    turn_count: int = Field(description="Successful turns since creation.")
    history_turns: int = Field(description="Turns currently in the window (<= max_turns).")
    max_turns: int
    project_metadata: ProjectMetadata
    anchors_count: int = Field(description="Anchored turns kept verbatim outside the window.")
    summary_chars: int = Field(description="Length of the cumulative summary; 0 if none.")
    last_tier: Tier | None = Field(
        description="Audience tier of the last successful turn; null if none."
    )
    last_tier_rule: TierRule | None = Field(
        description="Rule that resolved the tier of the last successful turn; null if none."
    )
    last_review: ReviewSummary | None = Field(
        default=None, description="Summary of the last review (turn with review); null if none."
    )


class TurnObservationOut(BaseModel):
    turn_index: int
    session_id: str
    enriched_transcript_chars: int
    attachments_total_chars: int
    messages_in_window: int
    anchors_count: int
    summary_chars: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    latency_ms: int
    cache_hit_kind: Literal["none", "exact", "semantic"]
    last_resolved_tier: Tier


class SessionDebug(BaseModel):
    session_id: str
    last_observation: TurnObservationOut | None
    summary: str | None
    anchors: list[str]
    project_metadata: ProjectMetadata


class SessionEstimateResponse(EstimationResponse):
    session_id: str
    turn: int = Field(description="Ordinal of this turn (== turn_count after it).")
    project_metadata: ProjectMetadata = Field(description="Project metadata after the update.")
    tier: Tier = Field(description="Audience tier of this turn.")
    tier_rule: TierRule = Field(description="Rule that resolved the tier of this turn.")
    acb: ReviewTrace | None = Field(
        default=None,
        description="Actor-Critic-Boss review trace; null unless the turn ran with review.",
    )


class SessionError(BaseModel):
    reason: Literal["session_not_found", "session_busy"]
    message: str


class SessionErrorResponse(BaseModel):
    detail: SessionError


class SessionGuardrailError(GuardrailError):
    field: Literal["transcript", "attachments", "input"]
    index: int | None = Field(description="Attachment position when `field` is `attachments`.")


class SessionGuardrailErrorResponse(BaseModel):
    detail: SessionGuardrailError


class AttachmentErrorDetail(BaseModel):
    reason: AttachmentErrorReason
    index: int | None
    message: str


class AttachmentErrorResponse(BaseModel):
    detail: AttachmentErrorDetail
