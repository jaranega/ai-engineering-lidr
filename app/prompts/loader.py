"""Loader of the versioned estimation prompt templates (S04 design D3, D4, D9).

`v3` is the version of `POST /api/v1/estimate` (structured output design D14): it asks
for an `EstimationResult` and shows the examples as JSON objects; `v1`/`v2` (free
Markdown) were retired. `v4` is the version of the session turns (S05 design D5), with
its own render function (`render_session_prompt`): other inputs (transcript and
attachments instead of description and reference projects) and the project metadata in
the system prompt. `v5` (design s05-memory D10), the default of the session turns, adds
the cumulative conversation summary and the audience tier; `v4` ignores both. `v6`
(design s05-acb D4), the default of the session turns, is a copy of `v5` whose `user.j2`
adds the optional `<critic_feedback>` block of an Actor-Critic-Boss retry; without
feedback it renders byte for byte as `v5`. Each list of versions only accepts its own.

The auxiliary prompts of the memory (`conversation_summary/v1`, `anchor_classifier/v1`,
design s05-memory D5, D6) live in their own folders with their own `Environment`; their
version is a code constant, not an API parameter, and `PUBLISHED_PROMPT_VERSIONS` only
refers to `estimation/`. The same holds for the Critic of the Actor-Critic-Boss review
(`critic/v1`, design s05-acb D7).

Each version lives in `estimation/<version>/` with `system.j2`, `user.j2` and
`examples.j2`, and gets its own Jinja2 `Environment` rooted at that folder, so
`{% include "examples.j2" %}` always resolves inside the same version. Versions are a
closed list (`PromptVersion`): an unknown version is rejected before any path is built.

Rendering is deterministic and makes no network calls; it reads the templates from disk
(compiled once per version) and logs a `prompt_rendered` event with hashes and lengths,
never the content.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.context.examples import ESTIMATION_EXAMPLES, PHASES, EstimationExample
from app.domain import DetailLevel, EstimationInput, Language, SessionTurnInput
from app.estimation.compute import ComputedEstimate
from app.estimation.examples import example_payloads
from app.prompts.language import detect_language
from app.sessions.models import ProjectMetadata, Turn
from app.sessions.tier import Tier

if TYPE_CHECKING:  # `app.services.critic` imports this module; annotations only
    from app.services.critic import CriticFeedback

logger = structlog.get_logger(__name__)

PromptVersion = Literal["v3"]
# Single source of truth of the published versions; must match the folders on disk.
PROMPT_VERSIONS: tuple[str, ...] = get_args(PromptVersion)
DEFAULT_PROMPT_VERSION: PromptVersion = "v3"

SessionPromptVersion = Literal["v4", "v5", "v6"]
SESSION_PROMPT_VERSIONS: tuple[str, ...] = get_args(SessionPromptVersion)
DEFAULT_SESSION_PROMPT_VERSION: SessionPromptVersion = "v6"
# Session versions that render the conversation summary, the audience tier and the memory
# fields of the examples (design s05-memory D10); v4 ignores them.
MEMORY_PROMPT_VERSIONS: frozenset[str] = frozenset({"v5", "v6"})
# Session versions with the `<critic_feedback>` block, the only ones that accept the
# Actor-Critic-Boss review (design s05-acb D1, D4); v4 and v5 ignore the feedback.
REVIEW_PROMPT_VERSIONS: frozenset[str] = frozenset({"v6"})
# Severities of the Critic issues painted in `<critic_feedback>`: a `minor` issue never
# justifies a retry (design s05-acb D4).
FEEDBACK_SEVERITIES: frozenset[str] = frozenset({"critical", "major"})

# Every published version; must match the folders of `estimation/` exactly.
PUBLISHED_PROMPT_VERSIONS: tuple[str, ...] = PROMPT_VERSIONS + SESSION_PROMPT_VERSIONS

# Number of available CAG examples, the default of `num_examples`.
NUM_EXAMPLES = len(ESTIMATION_EXAMPLES)

PROMPTS_DIR = Path(__file__).parent
TEMPLATES_DIR = PROMPTS_DIR / "estimation"
SYSTEM_TEMPLATE = "system.j2"
USER_TEMPLATE = "user.j2"
EXAMPLES_TEMPLATE = "examples.j2"

# Placeholder of an attachment's text in the user message stored in the history (D3).
ATTACHMENT_PLACEHOLDER = "[…]"

# Auxiliary prompts of the memory (design s05-memory D5, D6): fixed versions.
SUMMARY_PROMPT_VERSION = "v1"
ANCHOR_PROMPT_VERSION = "v1"
SUMMARY_TEMPLATES_DIR = PROMPTS_DIR / "conversation_summary"
ANCHOR_TEMPLATES_DIR = PROMPTS_DIR / "anchor_classifier"
# Critic prompt of the Actor-Critic-Boss review (design s05-acb D7): fixed version.
CRITIC_PROMPT_VERSION = "v1"
CRITIC_TEMPLATES_DIR = PROMPTS_DIR / "critic"
# Project hours a full-time person delivers per week, for the implied duration that the
# Critic compares with the declared one (the system prompt states 30-35 h).
CRITIC_HOURS_PER_FTE_WEEK = 32
# A part-time team member counts as half a full-time equivalent.
CRITIC_PART_TIME_FTE = 0.5

_HASH_CHARS = 12


class UnknownPromptVersionError(ValueError):
    """The requested prompt version is not one of the allowed ones (`PROMPT_VERSIONS` for
    the estimation, `SESSION_PROMPT_VERSIONS` for the session turns).
    """

    def __init__(self, version: str, allowed: Sequence[str] = PROMPT_VERSIONS) -> None:
        super().__init__(f"unknown prompt version; must be one of {', '.join(allowed)}")
        self.version = version


def _check_version(version: str, allowed: Sequence[str] = PROMPT_VERSIONS) -> None:
    """Reject anything outside the closed list before touching the filesystem."""
    if version not in allowed:
        raise UnknownPromptVersionError(version, allowed)


@cache
def _environment(version: str) -> Environment:
    """Jinja2 environment of one estimation version, created once per process.

    Only called with a validated version, so the path never contains user text.
    """
    return _new_environment(TEMPLATES_DIR / version)


@cache
def _memory_environment(folder: Path) -> Environment:
    """Jinja2 environment of one auxiliary prompt folder (summary, anchor classifier or
    Critic), created once per process. Only called with code constants.
    """
    return _new_environment(folder)


def _new_environment(folder: Path) -> Environment:
    """Jinja2 environment rooted at `folder`, with the same options for every prompt."""
    env = Environment(
        loader=FileSystemLoader(folder),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        # Not HTML: escaping would alter `<`, `&` and quotes in the description.
        autoescape=False,
        keep_trailing_newline=False,
    )
    return env


def _render_context(request: EstimationInput, examples: list[EstimationExample]) -> dict[str, Any]:
    """Single render context for both templates (S04 design D4).

    Enums are passed as their `str` values so templates compare plain strings and never
    print a Python enum representation. `examples` are the JSON payloads of the selected
    examples for the requested format; no rates nor amounts reach the prompt (structured
    output design D14). `response_language` is the detected language of the description
    (`"es"`, `"en"` or `None`), named explicitly at the end of the system prompt (S04
    design D14).
    """
    return {
        "description": request.description,
        "project_type": request.project_type.value,
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
        "reference_projects": [
            project.model_dump() for project in request.reference_projects or []
        ],
        "examples": example_payloads(examples, request.output_format),
        "phases": PHASES,
        "response_language": detect_language(request.description),
    }


def _sha256_prefix(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_CHARS]


def render_estimation_prompt(
    request: EstimationInput,
    version: str = DEFAULT_PROMPT_VERSION,
    *,
    num_examples: int = NUM_EXAMPLES,
) -> tuple[str, str]:
    """Render `(system, user)` for an estimation with the given prompt version.

    Uses the first `num_examples` CAG examples (0 omits the examples block). Raise
    `UnknownPromptVersionError` for a version outside `PROMPT_VERSIONS`, `ValueError` for
    `num_examples` out of range, and `jinja2.UndefinedError` if a template uses a variable
    missing from the context.
    """
    _check_version(version)
    if not 0 <= num_examples <= NUM_EXAMPLES:
        raise ValueError(f"num_examples must be between 0 and {NUM_EXAMPLES}")
    env = _environment(version)
    context = _render_context(request, ESTIMATION_EXAMPLES[:num_examples])
    system = env.get_template(SYSTEM_TEMPLATE).render(context).strip()
    user = env.get_template(USER_TEMPLATE).render(context).strip()
    logger.info(
        "prompt_rendered",
        prompt="estimation",
        version=version,
        output_format=context["output_format"],
        detail_level=context["detail_level"],
        project_type=context["project_type"],
        num_examples=num_examples,
        reference_projects=len(context["reference_projects"]),
        response_language=context["response_language"],
        system_sha256=_sha256_prefix(system),
        user_sha256=_sha256_prefix(user),
        system_chars=len(system),
        user_chars=len(user),
    )
    return system, user


@dataclass(frozen=True)
class SessionPrompt:
    """Rendered prompt of a session turn (S05 design D5).

    `user` is the current turn with the attachments' text; `history_user` is the same
    message with each attachment's text replaced by `[…]`, the copy stored in the history.
    """

    system: str
    user: str
    history_user: str


def render_session_prompt(
    turn: SessionTurnInput,
    version: str = DEFAULT_SESSION_PROMPT_VERSION,
    *,
    attachments: Sequence[tuple[str, str]] = (),
    project_metadata: ProjectMetadata | None = None,
    response_language: Language | None = None,
    num_examples: int = NUM_EXAMPLES,
    conversation_summary: str | None = None,
    tier: Tier = Tier.DEFAULT,
    critic_feedback: "CriticFeedback | None" = None,
) -> SessionPrompt:
    """Render the system prompt and the user messages of a session turn.

    `attachments` are `(sanitized name, masked text)` pairs; `project_metadata` (empty when
    `None`) is rendered into the system prompt. The language is decided by the caller,
    which knows the session (no detection here). With `v5`, `conversation_summary` (the
    session's cumulative summary, if any) and `tier` (the audience of the turn) are
    rendered too; `v4` ignores them and renders exactly as without them. With `v6`,
    `critic_feedback` (the last Critic review of an Actor-Critic-Boss retry) adds the
    `<critic_feedback>` block with its `critical`/`major` issues at the end of `user`,
    never of `history_user`; `v4` and `v5` ignore it. Same `Environment` as
    `render_estimation_prompt`. Raise `UnknownPromptVersionError` for a
    version outside `SESSION_PROMPT_VERSIONS` (v3 included), `ValueError` for
    `num_examples` out of range.
    """
    _check_version(version, SESSION_PROMPT_VERSIONS)
    if not 0 <= num_examples <= NUM_EXAMPLES:
        raise ValueError(f"num_examples must be between 0 and {NUM_EXAMPLES}")
    metadata = project_metadata or ProjectMetadata()
    memory = version in MEMORY_PROMPT_VERSIONS
    # What the template actually renders (and the log reports): nothing in v4.
    summary = (conversation_summary or None) if memory else None
    applied_tier = Tier(tier) if memory else Tier.DEFAULT
    feedback = critic_feedback if version in REVIEW_PROMPT_VERSIONS else None
    critic_issues = _feedback_issues(feedback)
    env = _environment(version)
    context: dict[str, Any] = {
        "transcript": turn.transcript,
        "project_type": turn.project_type.value,
        "detail_level": turn.detail_level.value,
        "output_format": turn.output_format.value,
        "examples": example_payloads(
            ESTIMATION_EXAMPLES[:num_examples],
            turn.output_format,
            include_technologies=True,
            # S04 measurement (D13): at `detailed`, examples without per-phase assumptions
            # made the model leave a phase without them (S05 design D5).
            include_phase_assumptions=turn.detail_level is DetailLevel.DETAILED,
            include_memory_fields=memory,
        ),
        "phases": PHASES,
        "project_metadata": metadata,
        "response_language": response_language,
        "attachment_placeholder": ATTACHMENT_PLACEHOLDER,
        "conversation_summary": summary,
        "tier": applied_tier.value,
        "critic_issues": critic_issues,
    }
    system = env.get_template(SYSTEM_TEMPLATE).render(context).strip()
    user_template = env.get_template(USER_TEMPLATE)
    user = user_template.render(
        context,
        attachments=[{"name": name, "text": text} for name, text in attachments],
        critic_feedback=feedback is not None,
    ).strip()
    # The history keeps the turn as the client wrote it: never the Critic's feedback.
    history_user = user_template.render(
        context,
        attachments=[{"name": name, "text": None} for name, _ in attachments],
        critic_feedback=False,
    ).strip()
    logger.info(
        "prompt_rendered",
        prompt="session",
        version=version,
        output_format=context["output_format"],
        detail_level=context["detail_level"],
        project_type=context["project_type"],
        num_examples=num_examples,
        attachments=len(attachments),
        metadata_fields=metadata.filled_fields(),
        response_language=response_language,
        tier=applied_tier.value,
        summary_chars=len(summary or ""),
        critic_issues=len(critic_issues),
        system_sha256=_sha256_prefix(system),
        user_sha256=_sha256_prefix(user),
        history_user_sha256=_sha256_prefix(history_user),
        system_chars=len(system),
        user_chars=len(user),
        history_user_chars=len(history_user),
    )
    return SessionPrompt(system=system, user=user, history_user=history_user)


def _feedback_issues(feedback: "CriticFeedback | None") -> list[dict[str, str | None]]:
    """The `critical`/`major` issues of a Critic review, in the order received, as plain
    values for the `<critic_feedback>` block (duck-typed: no import of `app.services`).
    """
    if feedback is None:
        return []
    return [
        {
            "severity": issue.severity,
            "category": issue.category,
            "field_path": issue.field_path,
            "description": issue.description,
            "suggested_fix": issue.suggested_fix,
        }
        for issue in feedback.issues
        if issue.severity in FEEDBACK_SEVERITIES
    ]


def _render_pair(
    folder: Path, context: dict[str, Any], *, prompt: str, version: str, **log_fields: Any
) -> tuple[str, str]:
    """Render `(system, user)` from one folder and log `prompt_rendered` without content."""
    env = _memory_environment(folder)
    system = env.get_template(SYSTEM_TEMPLATE).render(context).strip()
    user = env.get_template(USER_TEMPLATE).render(context).strip()
    logger.info(
        "prompt_rendered",
        prompt=prompt,
        version=version,
        **log_fields,
        system_sha256=_sha256_prefix(system),
        user_sha256=_sha256_prefix(user),
        system_chars=len(system),
        user_chars=len(user),
    )
    return system, user


def render_summary_prompt(
    previous: str | None,
    turns: Sequence[Turn],
    language: Language | None,
    *,
    project_metadata: ProjectMetadata | None = None,
) -> tuple[str, str]:
    """Render `(system, user)` of the conversation summarizer (design s05-memory D6).

    The user message holds, in this order: the project (`project_name` and
    `agreed_scope` of `project_metadata`, only those present) in `<project_context>`, as
    context only, so that turns bringing only changes are summarized as changes to that
    project instead of inventing it (D16, first measurement); `previous` (if any) in
    `<previous_summary>`; and, in `<turns>`, only the stored `user` message of each turn
    (attachments as `[…]`) after a `--- turn N ---` separator, never the assistant
    replies. The last line of the system asks for the summary in `language` (the session
    language). Deterministic, no network.
    """
    metadata = project_metadata or ProjectMetadata()
    context = {
        "project_name": metadata.project_name or None,
        "agreed_scope": metadata.agreed_scope or None,
        "previous": previous or None,
        "turns": [{"number": turn.number, "user": turn.user} for turn in turns],
        "language": language,
    }
    return _render_pair(
        SUMMARY_TEMPLATES_DIR / SUMMARY_PROMPT_VERSION,
        context,
        prompt="conversation_summary",
        version=SUMMARY_PROMPT_VERSION,
        turns=len(turns),
        previous_chars=len(previous or ""),
        project_context=bool(context["project_name"] or context["agreed_scope"]),
        language=language,
    )


def render_anchor_prompt(turn: Turn) -> tuple[str, str]:
    """Render `(system, user)` of the LLM anchor classifier (design s05-memory D5): the
    stored `user` message of the turn only. Deterministic, no network.
    """
    return _render_pair(
        ANCHOR_TEMPLATES_DIR / ANCHOR_PROMPT_VERSION,
        {"turn": {"number": turn.number, "user": turn.user}},
        prompt="anchor_classifier",
        version=ANCHOR_PROMPT_VERSION,
        turn=turn.number,
    )


def _number(value: float) -> str:
    """`12` for whole values, `12.5` otherwise (one decimal): compact and deterministic."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"


def critic_figures(estimate: ComputedEstimate) -> dict[str, Any]:
    """Figures of the draft precomputed by code for the Critic (calibration of design
    s05-acb D7): the share of the total hours of each phase (in phase order, same index
    as `phases`), the largest share, the team in full-time equivalents (a part-time
    member counts as `CRITIC_PART_TIME_FTE`) and the weeks those hours imply at
    `CRITIC_HOURS_PER_FTE_WEEK` per FTE. The model gets these numbers wrong when it
    computes them itself; values are pre-formatted strings, `None` when not computable.
    """
    total = estimate.total_hours
    shares = [phase.hours * 100 / total if total else 0.0 for phase in estimate.phases]
    fte = sum(
        member.count * (CRITIC_PART_TIME_FTE if member.part_time else 1.0)
        for member in estimate.team
    )
    implied = total / (fte * CRITIC_HOURS_PER_FTE_WEEK) if fte else None
    return {
        "phase_shares": [_number(share) for share in shares],
        "largest_phase_share": _number(max(shares)) if shares else None,
        "team_fte": _number(fte) if fte else None,
        "implied_weeks": _number(implied) if implied is not None else None,
        "hours_per_fte_week": CRITIC_HOURS_PER_FTE_WEEK,
    }


def render_critic_prompt(
    *,
    transcript: str,
    project_metadata: ProjectMetadata | None,
    conversation_summary: str | None,
    tier: Tier,
    language: Language | None,
    estimate: ComputedEstimate,
) -> tuple[str, str]:
    """Render `(system, user)` of the Critic of the Actor-Critic-Boss review (design
    s05-acb D7).

    The user message holds the transcript of the turn (verbatim), the session's project
    metadata (constraints and rejected options included), the cumulative summary (if
    any), the resolved tier, the effective language of the turn and a compact view of the
    computed draft (`<estimation_under_review>`, rows with their index). Never the
    attachments' text nor the history: the system prompt warns that there are facts the
    Critic does not see. Own `Environment` rooted at `critic/v1`. Deterministic, no
    network; logs `prompt_rendered` without content.
    """
    metadata = project_metadata or ProjectMetadata()
    context = {
        "transcript": transcript,
        "project_metadata": metadata,
        "conversation_summary": conversation_summary or None,
        "tier": Tier(tier).value,
        "language": language,
        "estimate": estimate,
        "figures": critic_figures(estimate),
    }
    return _render_pair(
        CRITIC_TEMPLATES_DIR / CRITIC_PROMPT_VERSION,
        context,
        prompt="critic",
        version=CRITIC_PROMPT_VERSION,
        metadata_fields=metadata.filled_fields(),
        summary_chars=len(conversation_summary or ""),
        tier=Tier(tier).value,
        language=language,
        rows=len(estimate.rows),
    )
