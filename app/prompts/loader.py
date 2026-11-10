"""Loader of the versioned estimation prompt templates (S04 design D3, D4, D9).

Each version lives in `estimation/<version>/` with `system.j2`, `user.j2` and
`examples.j2`, and gets its own Jinja2 `Environment` rooted at that folder, so
`{% include "examples.j2" %}` always resolves inside the same version. Versions are a
closed list (`PromptVersion`): an unknown version is rejected before any path is built.

Rendering is deterministic and makes no network calls; it reads the templates from disk
(compiled once per version) and logs a `prompt_rendered` event with hashes and lengths,
never the content.
"""

import hashlib
from functools import cache
from pathlib import Path
from typing import Any, Literal, get_args

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.context.examples import (
    ESTIMATION_EXAMPLES,
    PHASES,
    RATES_EUR_PER_HOUR,
    EstimationExample,
    format_amount,
)
from app.domain import EstimationInput
from app.prompts.language import detect_language

logger = structlog.get_logger(__name__)

PromptVersion = Literal["v1", "v2"]
# Single source of truth of the published versions; must match the folders on disk.
PROMPT_VERSIONS: tuple[str, ...] = get_args(PromptVersion)
DEFAULT_PROMPT_VERSION: PromptVersion = "v1"

# Number of available CAG examples, the default of `num_examples`.
NUM_EXAMPLES = len(ESTIMATION_EXAMPLES)

TEMPLATES_DIR = Path(__file__).parent / "estimation"
SYSTEM_TEMPLATE = "system.j2"
USER_TEMPLATE = "user.j2"
EXAMPLES_TEMPLATE = "examples.j2"

_HASH_CHARS = 12


class UnknownPromptVersionError(ValueError):
    """The requested prompt version is not one of `PROMPT_VERSIONS`."""

    def __init__(self, version: str) -> None:
        super().__init__(f"unknown prompt version; must be one of {', '.join(PROMPT_VERSIONS)}")
        self.version = version


def _check_version(version: str) -> None:
    """Reject anything outside the closed list before touching the filesystem."""
    if version not in PROMPT_VERSIONS:
        raise UnknownPromptVersionError(version)


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
    env.filters["amount"] = format_amount
    return env


def _render_context(request: EstimationInput, examples: list[EstimationExample]) -> dict[str, Any]:
    """Single render context for both templates (S04 design D4).

    Enums are passed as their `str` values so templates compare plain strings and never
    print a Python enum representation. `response_language` is the detected language of
    the description (`"es"`, `"en"` or `None`), named explicitly at the end of the system
    prompt (design D14).
    """
    return {
        "description": request.description,
        "project_type": request.project_type.value,
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
        "reference_projects": [
            project.model_dump() for project in request.reference_projects or []
        ],
        "examples": examples,
        "phases": PHASES,
        "rates": RATES_EUR_PER_HOUR,
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
