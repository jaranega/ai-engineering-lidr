"""Loader of the versioned estimation prompt templates (S04 design D3, D4, D9).

`v3` is the version of `POST /api/v1/estimate` (structured output design D14): it asks
for an `EstimationResult` and shows the examples as JSON objects; `v1`/`v2` (free
Markdown) were retired. `v4` is the version of the session turns (S05 design D5), with
its own render function (`render_session_prompt`): other inputs (transcript and
attachments instead of description and reference projects) and the project metadata in
the system prompt. Each list of versions only accepts its own.

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
from typing import Any, Literal, get_args

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.context.examples import ESTIMATION_EXAMPLES, PHASES, EstimationExample
from app.domain import DetailLevel, EstimationInput, Language, SessionTurnInput
from app.estimation.examples import example_payloads
from app.prompts.language import detect_language
from app.sessions.models import ProjectMetadata

logger = structlog.get_logger(__name__)

PromptVersion = Literal["v3"]
# Single source of truth of the published versions; must match the folders on disk.
PROMPT_VERSIONS: tuple[str, ...] = get_args(PromptVersion)
DEFAULT_PROMPT_VERSION: PromptVersion = "v3"

SessionPromptVersion = Literal["v4"]
SESSION_PROMPT_VERSIONS: tuple[str, ...] = get_args(SessionPromptVersion)
DEFAULT_SESSION_PROMPT_VERSION: SessionPromptVersion = "v4"

# Every published version; must match the folders of `estimation/` exactly.
PUBLISHED_PROMPT_VERSIONS: tuple[str, ...] = PROMPT_VERSIONS + SESSION_PROMPT_VERSIONS

# Number of available CAG examples, the default of `num_examples`.
NUM_EXAMPLES = len(ESTIMATION_EXAMPLES)

TEMPLATES_DIR = Path(__file__).parent / "estimation"
SYSTEM_TEMPLATE = "system.j2"
USER_TEMPLATE = "user.j2"
EXAMPLES_TEMPLATE = "examples.j2"

# Placeholder of an attachment's text in the user message stored in the history (D3).
ATTACHMENT_PLACEHOLDER = "[…]"

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
    """Jinja2 environment of one version, created once per process.

    Only called with a validated version, so the path never contains user text.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR / version),
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
) -> SessionPrompt:
    """Render the system prompt and the user messages of a session turn.

    `attachments` are `(sanitized name, masked text)` pairs; `project_metadata` (empty when
    `None`) is rendered into the system prompt. The language is decided by the caller,
    which knows the session (no detection here). Same `Environment` as
    `render_estimation_prompt`. Raise `UnknownPromptVersionError` for a version outside
    `SESSION_PROMPT_VERSIONS` (v3 included), `ValueError` for `num_examples` out of range.
    """
    _check_version(version, SESSION_PROMPT_VERSIONS)
    if not 0 <= num_examples <= NUM_EXAMPLES:
        raise ValueError(f"num_examples must be between 0 and {NUM_EXAMPLES}")
    metadata = project_metadata or ProjectMetadata()
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
        ),
        "phases": PHASES,
        "project_metadata": metadata,
        "response_language": response_language,
        "attachment_placeholder": ATTACHMENT_PLACEHOLDER,
    }
    system = env.get_template(SYSTEM_TEMPLATE).render(context).strip()
    user_template = env.get_template(USER_TEMPLATE)
    user = user_template.render(
        context, attachments=[{"name": name, "text": text} for name, text in attachments]
    ).strip()
    history_user = user_template.render(
        context, attachments=[{"name": name, "text": None} for name, _ in attachments]
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
        system_sha256=_sha256_prefix(system),
        user_sha256=_sha256_prefix(user),
        history_user_sha256=_sha256_prefix(history_user),
        system_chars=len(system),
        user_chars=len(user),
        history_user_chars=len(history_user),
    )
    return SessionPrompt(system=system, user=user, history_user=history_user)
