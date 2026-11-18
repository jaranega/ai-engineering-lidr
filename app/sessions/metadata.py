"""Deterministic update of the project metadata from the structured estimate.

S05 design D4; `explicit_constraints`, `rejected_options` and forgetting policy 1 from
design s05-memory D8. Pure functions: no LLM call, no I/O. The facts already come typed
in the `ComputedEstimate` of each turn.
"""

import re
from collections.abc import Iterable, Sequence

from app.estimation.compute import ComputedEstimate
from app.sessions.models import ProjectMetadata

# Caps of the accumulated lists; past them, the oldest are dropped (recent is relevant).
MAX_METADATA_TECHNOLOGIES = 30
MAX_METADATA_CONSTRAINTS = 15
MAX_METADATA_REJECTED_OPTIONS = 20


_WHITESPACE = re.compile(r"\s+")
# Trailing punctuation ignored when comparing ("Fixed budget of $45k" == "...$45k.").
_TRAILING_PUNCTUATION = ".;:"


def _key(text: str) -> str:
    """Comparison key of list items: exact match after trimming, collapsing whitespace and
    stripping the trailing `.`, `;` and `:`, ignoring case. Never a substring match.
    """
    collapsed = _WHITESPACE.sub(" ", text).strip()
    return collapsed.rstrip(_TRAILING_PUNCTUATION + " ").casefold()


def merge_unique(current: Sequence[str], new: Sequence[str], limit: int) -> list[str]:
    """`current` followed by `new`, in order, without duplicates (compared by `_key`:
    `casefold` after trimming, collapsing whitespace and stripping the trailing
    punctuation; the first spelling wins), keeping the last `limit` items.
    """
    seen: set[str] = set()
    merged: list[str] = []
    for item in (*current, *new):
        key = _key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-limit:] if limit > 0 else []


# S05 name, kept as an alias.
merge_technologies = merge_unique


def _without(items: Iterable[str], removed: Iterable[str]) -> list[str]:
    """`items` minus those equal (by `_key`, never by substring) to one of `removed`."""
    keys = {_key(item) for item in removed}
    return [item for item in items if _key(item) not in keys]


def update_project_metadata(
    current: ProjectMetadata, estimate: ComputedEstimate
) -> ProjectMetadata:
    """New metadata after a successful turn; unchanged when the estimate is out of scope.

    Scalars are overwritten (they mirror the estimate the user just saw): `title` ->
    `project_name`, the sum of `team.count` -> `assumed_team_size` (a part-time profile
    counts as its people), `summary` -> `agreed_scope`. Lists, in this order (design
    s05-memory D8):

    1. `explicit_constraints` accumulate (never retracted in this change);
    2. `rejected_options` accumulate;
    3. readoption: a rejected name that this turn lists in `technologies` and does not
       reject again leaves `rejected_options` (the current turn wins; if the same turn
       both uses and rejects it, the rejection wins);
    4. `mentioned_technologies` accumulate and then lose every rejected name (forgetting
       policy 1).

    Comparisons are exact by `_key` (`casefold` after trimming, collapsing whitespace and
    stripping the trailing `.`, `;`, `:`), never by substring: rejecting
    "app Android" does not remove "Android".
    """
    if estimate.out_of_scope:
        return current
    constraints = merge_unique(
        current.explicit_constraints, estimate.explicit_constraints, MAX_METADATA_CONSTRAINTS
    )
    rejected = merge_unique(
        current.rejected_options, estimate.rejected_options, MAX_METADATA_REJECTED_OPTIONS
    )
    readopted = _without(estimate.technologies, estimate.rejected_options)
    rejected = _without(rejected, readopted)
    technologies = merge_unique(
        current.mentioned_technologies, estimate.technologies, MAX_METADATA_TECHNOLOGIES
    )
    return ProjectMetadata(
        project_name=estimate.title,
        assumed_team_size=sum(member.count for member in estimate.team),
        mentioned_technologies=_without(technologies, rejected),
        agreed_scope=estimate.summary,
        explicit_constraints=constraints,
        rejected_options=rejected,
    )
