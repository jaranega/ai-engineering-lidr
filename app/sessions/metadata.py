"""Deterministic update of the project metadata from the structured estimate (D4).

Pure functions: no LLM call, no I/O. The facts already come typed in the
`ComputedEstimate` of each turn.
"""

from collections.abc import Sequence

from app.estimation.compute import ComputedEstimate
from app.sessions.models import ProjectMetadata

# Cap of accumulated technologies; past it, the oldest are dropped (recent is relevant).
MAX_METADATA_TECHNOLOGIES = 30


def merge_technologies(current: Sequence[str], new: Sequence[str], limit: int) -> list[str]:
    """`current` followed by `new`, in order, without `casefold` duplicates (the first
    spelling wins), keeping the last `limit` names.
    """
    seen: set[str] = set()
    merged: list[str] = []
    for name in (*current, *new):
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(name)
    return merged[-limit:] if limit > 0 else []


def update_project_metadata(
    current: ProjectMetadata, estimate: ComputedEstimate
) -> ProjectMetadata:
    """New metadata after a successful turn; unchanged when the estimate is out of scope.

    Scalars are overwritten (they mirror the estimate the user just saw): `title` ->
    `project_name`, the sum of `team.count` -> `assumed_team_size` (a part-time profile
    counts as its people), `summary` -> `agreed_scope`. Technologies accumulate.
    """
    if estimate.out_of_scope:
        return current
    return ProjectMetadata(
        project_name=estimate.title,
        assumed_team_size=sum(member.count for member in estimate.team),
        mentioned_technologies=merge_technologies(
            current.mentioned_technologies, estimate.technologies, MAX_METADATA_TECHNOLOGIES
        ),
        agreed_scope=estimate.summary,
    )
