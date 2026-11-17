"""Heuristic anchor detector, Spanish and English (design s05-memory D5).

A turn that leaves the window is an anchor when its stored `user` message holds a durable
client commitment (NDA, signed contract, frozen scope, fixed budget, regulation, hard
deadline or explicit agreement). Pure and deterministic: regular expressions only, no LLM
and no network. The `assistant` text is never inspected: the estimator writes "risk: GDPR
compliance" or "tight budget" on its own, which would be false positives.

Bias towards few false positives: every rule needs a commitment term plus a qualifier
("presupuesto" alone never fires; "presupuesto cerrado" does), except `regulation`, where
naming the norm is enough. Acronyms are matched case-sensitively with word boundaries
("agenda" never fires `nda`); the rest ignores case and accepts missing accents.

The `nda` and `regulation` patterns are also used by the audience tier resolver
(`app.sessions.tier`), so both features share a single source.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.sessions.models import Turn

AnchorSource = Literal["heuristic", "llm"]

_I = re.IGNORECASE

# A money amount: currency sign before the figure, or a currency/thousands marker after it
# ("40 000 €", "$45k", "50.000 euros", "45k"). "Cannot exceed" and "no podemos pasar de"
# only fire with one, so "cannot exceed 200 ms" or "no superar los 100 usuarios" do not.
_MONEY = (
    r"(?:[$€£]\s?\d|\d[\d.,]*(?:\s\d{3})*\s?"
    r"(?:k\b|mil\b|€|\$|£|eur\b|euros?\b|usd\b|d[oó]lares\b|dollars?\b))"
)


def _patterns(*specs: tuple[str, int]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, flags) for pattern, flags in specs)


# Rules in report order (the order of `AnchorDecision.rules`).
ANCHOR_RULES: Mapping[str, tuple[re.Pattern[str], ...]] = {
    "nda": _patterns(
        (r"\bNDA\b", 0),
        (r"\bacuerdo de (?:confidencialidad|no divulgaci[oó]n)\b", _I),
        (r"\bcl[aá]usula de confidencialidad\b", _I),
        (r"\bnon[- ]disclosure(?: agreement)?\b", _I),
        (r"\bconfidentiality (?:agreement|clause)\b", _I),
    ),
    "contract_signed": _patterns(
        (r"\b(?:hemos|han|ha|ya) firmado (?:el|un|los) contratos?\b", _I),
        (r"\bcontrato (?:ya )?firmado\b", _I),
        (r"\bfirmamos (?:el|un) contrato\b", _I),
        (
            r"\b(?:we|we['’]ve|we have|they|they['’]ve|they have|(?:the )?client(?: has)?)"
            r" signed (?:the|a|an|our) (?:contract|agreement|sow|statement of work)\b",
            _I,
        ),
        (r"\b(?:contract|sow) (?:is|was|has been) signed\b", _I),
    ),
    "scope_frozen": _patterns(
        (r"\balcance (?:(?:est[aá]|es|queda) )?(?:cerrado|congelado|fijo|definitivo)\b", _I),
        (r"\bcongel(?:a|amos|ar|ado) el alcance\b", _I),
        (r"\bscope (?:is |was |has been )?(?:frozen|locked|fixed|final)\b", _I),
        (r"\b(?:freeze|lock) the scope\b", _I),
        (r"\bscope freeze\b", _I),
    ),
    "budget_fixed": _patterns(
        (r"\bpresupuesto (?:cerrado|fijo|m[aá]ximo|tope|innegociable)\b", _I),
        (r"\bpresupuesto (?:es|est[aá]) (?:cerrado|limitado a|fijado)\b", _I),
        (
            r"\bno (?:podemos|puede|pueden|debe|debemos) (?:pasar|superar|exceder) "
            r"(?:de )?(?:los |las )?" + _MONEY,
            _I,
        ),
        (r"\btope de " + _MONEY, _I),
        (r"\b(?:fixed|capped|hard|maximum|max\.?) budget\b", _I),
        (r"\bbudget (?:is |of )?(?:capped|fixed|limited|non-negotiable)\b", _I),
        (r"\bbudget cap\b", _I),
        (r"\b(?:cannot|can['’]t|must not|mustn['’]t) exceed (?:a budget of )?" + _MONEY, _I),
        (r"\bnot to exceed\b", _I),
    ),
    "regulation": _patterns(
        (r"\b(?:RGPD|LOPDGDD|LOPD|ENS)\b", 0),
        (r"\b(?:GDPR|HIPAA|PCI[- ]?DSS|SOC ?2|SOX|NIS ?2|DORA)\b", 0),
        (r"\bISO[ /-]?27001\b", 0),
    ),
    "hard_deadline": _patterns(
        (r"\bfecha (?:l[ií]mite|tope) (?:es )?(?:innegociable|fija|cerrada)\b", _I),
        (
            r"\b(?:tiene que|tenemos que|debe|debemos|ha de) estar "
            r"(?:listo|lista|en producci[oó]n|publicad[oa]|en marcha) "
            r"(?:antes de(?:l)?|para (?:el|la))\s",
            _I,
        ),
        # "innegociable" / "non-negotiable" only next to a deadline term: alone they also
        # describe budgets or features.
        (r"\b(?:fecha|plazo|entrega|lanzamiento)\b[^.\n]{0,40}\binnegociable\b", _I),
        (r"\bhard deadline\b", _I),
        (
            r"\b(?:must|has to|have to|needs to|need to) "
            r"(?:go live|launch|be live|ship|be ready) (?:by|before)\b",
            _I,
        ),
        (r"\b(?:deadline|date|launch|go-live|delivery)\b[^.\n]{0,40}\bnon-negotiable\b", _I),
        (r"\bno later than\b", _I),
    ),
    "client_commitment": _patterns(
        (r"\b(?:nos )?comprometemos a\b", _I),
        (r"\bqueda acordado\b", _I),
        (r"\b(?:hemos|han) acordado\b", _I),
        (r"\bacordamos que\b", _I),
        (r"\brequisito (?:obligatorio|imprescindible)\b", _I),
        (r"\bwe (?:have )?agreed (?:that|to|on)\b", _I),
        (r"\b(?:it['’]s|it is) agreed\b", _I),
        (r"\bwe commit to\b", _I),
        (r"\bmandatory requirement\b", _I),
    ),
}

ANCHOR_RULE_IDS: tuple[str, ...] = tuple(ANCHOR_RULES)

# Shared with the tier resolver (single source).
NDA_PATTERNS = ANCHOR_RULES["nda"]
REGULATION_PATTERNS = ANCHOR_RULES["regulation"]


@dataclass(frozen=True)
class AnchorDecision:
    """Whether a turn is an anchor, which rules fired (`("llm",)` in LLM mode) and who
    decided.
    """

    is_anchor: bool
    rules: tuple[str, ...] = ()
    source: AnchorSource = "heuristic"


def matches_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    """True if any of `patterns` matches `text`."""
    return any(pattern.search(text) for pattern in patterns)


def matching_rules(text: str) -> tuple[str, ...]:
    """Ids of every anchor rule that matches `text`, in rule order."""
    return tuple(rule for rule, patterns in ANCHOR_RULES.items() if matches_any(patterns, text))


def detect_anchor(turn: Turn) -> AnchorDecision:
    """Heuristic decision on the stored `user` message of `turn` (never the `assistant`)."""
    rules = matching_rules(turn.user)
    return AnchorDecision(is_anchor=bool(rules), rules=rules, source="heuristic")
