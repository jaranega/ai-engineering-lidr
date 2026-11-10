"""Structural evaluation of an estimation (design D6).

Pure: regular expressions over the Markdown, no LLM and no network. Labels are accepted
in Spanish and English (also "Hours total" / "Cost total"), case-insensitive and ignoring
bold/italic markers, without detecting the language. `issues` are fixed English phrases
with numbers only, never text from the estimation, so they can be logged.
"""

import re
from dataclasses import dataclass

# Number of checks behind `score`.
CHECK_COUNT = 8
# Tolerances of the arithmetic checks: absolute hours, and a fraction of the declared cost
# (the reference implementation's 2 %).
HOURS_TOLERANCE = 1.0
COST_TOLERANCE_RATIO = 0.02


@dataclass(frozen=True)
class StructureCheck:
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
    issues: tuple[str, ...]


_FLAGS = re.IGNORECASE | re.MULTILINE

_TITLE = re.compile(r"^\s*#{1,3}\s*(estimaci[oó]n|estimate)\s*:", _FLAGS)
# A number: digits with optional thousands/decimal separators and inner spaces.
_NUM = r"(\d[\d.,  ]*)"
# Up to 20 non-digit characters between a label and its number (": ", "€", "(h): ").
_GAP = r"[^\d\n]{0,20}?"
# English labels also in the calqued order gpt-4o-mini sometimes writes ("Cost total").
_TOTAL_HOURS = re.compile(
    rf"(total de horas|total horas|total hours|hours total|total estimado){_GAP}{_NUM}", _FLAGS
)
_TOTAL_COST = re.compile(rf"(coste total|costo total|total cost|cost total){_GAP}{_NUM}", _FLAGS)
_TEAM = re.compile(r"(equipo recomendado|recommended team)", _FLAGS)
_DURATION = re.compile(
    r"(duraci[oó]n estimada|estimated duration)[^\n]*?\d[^\n]*?(semanas?|weeks?)\b", _FLAGS
)

_TASK_HEADER = re.compile(r"^(tareas?|tasks?)\b", re.IGNORECASE)
_HOURS_HEADER = re.compile(r"^(horas|hours)\b", re.IGNORECASE)
_COST_HEADER = re.compile(r"^(coste|costo|cost)\b", re.IGNORECASE)
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_TOTAL_ROW = re.compile(r"^total", re.IGNORECASE)
_NUMBER_NOISE = re.compile(r"(eur|€|h|\s| |\*)", re.IGNORECASE)


def parse_number(raw: str) -> float | None:
    """Read an hours or amount value written in Spanish or English style.

    Ignores `EUR`, `€`, `h`, spaces (NBSP included) and `*`. With both `.` and `,` the
    last one is the decimal separator; with a single separator followed by exactly three
    digits it is a thousands separator; otherwise it is decimal. `None` if unreadable.
    """
    text = _NUMBER_NOISE.sub("", raw).rstrip(".,")
    if not text:
        return None
    if "." in text and "," in text:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "." in text or "," in text:
        sep = "." if "." in text else ","
        parts = text.split(sep)
        is_thousands = len(parts) > 2 or len(parts[1]) == 3
        text = "".join(parts) if is_thousands else text.replace(sep, ".")
    try:
        return float(text)
    except ValueError:
        return None


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _header_columns(cells: list[str]) -> tuple[int, int, int] | None:
    """Column indexes of task, hours and cost, or `None` if this is not the header."""
    indexes: list[int | None] = [None, None, None]
    for index, cell in enumerate(cells):
        for slot, pattern in enumerate((_TASK_HEADER, _HOURS_HEADER, _COST_HEADER)):
            if indexes[slot] is None and pattern.match(cell):
                indexes[slot] = index
    if any(index is None for index in indexes):
        return None
    task, hours, cost = indexes
    return task, hours, cost  # type: ignore[return-value]


def _parse_table(lines: list[str]) -> tuple[bool, list[tuple[float, float]]]:
    """Find the breakdown table; return whether it exists and its (hours, cost) rows."""
    for index, line in enumerate(lines[:-1]):
        if not line.strip().startswith("|"):
            continue
        columns = _header_columns(_cells(line))
        if columns is None or not _SEPARATOR.match(lines[index + 1].strip()):
            continue
        task_col, hours_col, cost_col = columns
        rows: list[tuple[float, float]] = []
        for row_line in lines[index + 2 :]:
            if not row_line.strip().startswith("|"):
                break
            cells = _cells(row_line)
            if max(columns) >= len(cells) or _TOTAL_ROW.match(cells[task_col]):
                continue
            hours, cost = parse_number(cells[hours_col]), parse_number(cells[cost_col])
            if hours is not None and cost is not None:
                rows.append((hours, cost))
        return bool(rows), rows
    return False, []


def _declared(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return parse_number(match.group(2)) if match else None


def _fmt(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def evaluate_estimation(text: str, truncated: bool) -> StructureCheck:
    """Check the structure and arithmetic of an estimation in Markdown.

    Eight checks: title, breakdown table, both totals, team, duration, hours sum, cost
    sum and not truncated. `score` is passed / 8 rounded to 3 decimals; an arithmetic
    check without data (`None`) counts as failed. One issue per failed check.
    """
    # Bold/italic markers removed so labels match with or without `**`.
    plain = re.sub(r"[*_]", "", text)
    lines = plain.splitlines()

    has_title = bool(_TITLE.search(plain))
    has_table, rows = _parse_table(lines)
    declared_hours = _declared(_TOTAL_HOURS, plain)
    declared_cost = _declared(_TOTAL_COST, plain)
    has_totals = declared_hours is not None and declared_cost is not None
    has_team = bool(_TEAM.search(plain))
    has_duration = bool(_DURATION.search(plain))

    sum_hours = sum(hours for hours, _ in rows) if rows else None
    sum_cost = round(sum(cost for _, cost in rows), 2) if rows else None
    hours_issue = cost_issue = None
    hours_match: bool | None = None
    if sum_hours is not None and declared_hours is not None:
        hours_match = abs(sum_hours - declared_hours) <= HOURS_TOLERANCE
        hours_issue = (
            f"row hours sum {_fmt(sum_hours)} does not match declared total {_fmt(declared_hours)}"
        )
    cost_match: bool | None = None
    if sum_cost is not None and declared_cost is not None:
        cost_match = abs(sum_cost - declared_cost) <= COST_TOLERANCE_RATIO * declared_cost
        cost_issue = (
            f"row cost sum {_fmt(sum_cost)} does not match declared total {_fmt(declared_cost)}"
        )
    finish_ok = not truncated

    issues: list[str] = []
    if not has_title:
        issues.append("missing title ('Estimación:' / 'Estimate:' heading)")
    if not has_table:
        issues.append("missing breakdown table with task, hours and cost columns")
    if not has_totals:
        missing = [
            name
            for name, value in (("hours", declared_hours), ("cost", declared_cost))
            if value is None
        ]
        issues.append(f"missing declared total {' and '.join(missing)}")
    if not has_team:
        issues.append("missing recommended team")
    if not has_duration:
        issues.append("missing estimated duration in weeks")
    if hours_match is None:
        issues.append("hours check not possible: missing table rows or declared total hours")
    elif not hours_match and hours_issue:
        issues.append(hours_issue)
    if cost_match is None:
        issues.append("cost check not possible: missing table rows or declared total cost")
    elif not cost_match and cost_issue:
        issues.append(cost_issue)
    if not finish_ok:
        issues.append("response truncated at the output token limit")

    checks = (
        has_title,
        has_table,
        has_totals,
        has_team,
        has_duration,
        hours_match is True,
        cost_match is True,
        finish_ok,
    )
    return StructureCheck(
        has_title=has_title,
        has_breakdown_table=has_table,
        has_totals=has_totals,
        has_team=has_team,
        has_duration=has_duration,
        rows_parsed=len(rows),
        declared_total_hours=declared_hours,
        sum_row_hours=sum_hours,
        hours_match=hours_match,
        declared_total_cost=declared_cost,
        sum_row_cost=sum_cost,
        cost_match=cost_match,
        finish_ok=finish_ok,
        score=round(sum(checks) / CHECK_COUNT, 3),
        issues=tuple(issues),
    )
