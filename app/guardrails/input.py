"""Input guardrails: prompt injection and personal data, with regular expressions (D8).

Pure and offline: no network, no LLM. Runs before the cache and the LLM (and before the
moderation call, so personal data is never sent to a third party). Injection rules run
on a normalized copy of the text (casefolded, accents stripped, whitespace collapsed),
so "IGNORA LAS INSTRUCCIONES" and "ignorá las instrucciones" hit the same rule; personal
data rules run on the original text.

Rules pair a verb with its object ("ignore previous instructions") instead of single
words: descriptions of AI products legitimately mention a "system prompt" or a service
that "acts as" a gateway, and those must pass. A hit carries the reason, the field and a
rule id, never the matched fragment; the error message is a fixed phrase per reason.

Attachments of a session turn (S05 design D8) are masked instead of blocked for personal
data (`mask_pii`, same patterns), and still blocked for injection.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

GuardrailReason = Literal["prompt_injection", "pii", "moderation"]
PiiKind = Literal["email", "phone", "iban"]

# Placeholders of `mask_pii`; they match none of the personal data patterns, so masking
# is idempotent.
PII_PLACEHOLDERS: dict[PiiKind, str] = {"email": "[EMAIL]", "phone": "[PHONE]", "iban": "[IBAN]"}

# Fixed English messages (like the router's PROVIDER_ERROR_DETAIL): they never repeat
# any part of the input.
GUARDRAIL_MESSAGES: dict[GuardrailReason, str] = {
    "prompt_injection": (
        "The request contains instructions addressed to the assistant. Describe only the project."
    ),
    "pii": (
        "The request contains personal data (email, phone number or IBAN). Remove it and try again."
    ),
    "moderation": "The request content is not allowed.",
}


class InputGuardrailViolation(Exception):  # noqa: N818 - domain name fixed by the design
    """The input was blocked by a guardrail; `message` never contains user text.

    `field` and `rule` identify what fired (for logs). `index` is the position of the
    attachment that fired (session turns, `field == "attachments"`), else `None`.
    """

    def __init__(
        self,
        reason: GuardrailReason,
        field: str = "",
        rule: str = "",
        index: int | None = None,
    ) -> None:
        self.reason: GuardrailReason = reason
        self.message = GUARDRAIL_MESSAGES[reason]
        self.field = field
        self.rule = rule
        self.index = index
        super().__init__(self.message)


@dataclass(frozen=True)
class GuardrailHit:
    reason: GuardrailReason
    # "description" | "reference_projects" (estimate); "transcript" | "attachments" (session
    # turn); "input" (moderation, any endpoint).
    field: str
    rule: str  # rule id, e.g. "en_ignore_previous", "iban"


def _rules(*rules: tuple[str, str]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((rule_id, re.compile(pattern)) for rule_id, pattern in rules)


# Matched against normalized text: lowercase, no accents, single spaces.
INJECTION_RULES = _rules(
    (
        "en_ignore_previous",
        r"\bignore (all |any )?(of )?(the |your )?(previous|prior|above|earlier) "
        r"(instructions|prompts?|rules|messages)\b",
    ),
    (
        "en_disregard_previous",
        r"\bdisregard (all |any )?(of )?(the |your )?(previous|prior|above|earlier) "
        r"(instructions|prompts?|rules|messages)\b",
    ),
    (
        "en_forget_instructions",
        r"\bforget (all |everything )?(of )?(the |your )?(previous |prior )?(instructions|rules)\b",
    ),
    ("en_you_are_now", r"\byou are now (a|an|the|in)\b"),
    (
        "en_reveal_prompt",
        # "your" or "system" is required: "show the instructions" is a normal feature.
        r"\b(reveal|show|print|repeat|output) (me )?"
        r"(your (system )?(prompt|instructions)|the system (prompt|instructions))\b",
    ),
    ("en_new_instructions", r"\bnew instructions ?:"),
    ("en_special_mode", r"\b(developer|jailbreak) mode\b"),
    (
        "es_ignora_instrucciones",
        r"\bignora (todas )?(las |tus )?(instrucciones|indicaciones|reglas) "
        r"(anteriores|previas)\b",
    ),
    ("es_olvida_instrucciones", r"\bolvida (todas )?(las |tus )?(instrucciones|reglas)\b"),
    ("es_caso_omiso", r"\bhaz caso omiso (de|a)\b"),
    ("es_ahora_eres", r"\b(a partir de ahora|ahora) (eres|actua como)\b"),
    (
        "es_revela_prompt",
        r"\b(muestra|muestrame|revela|repite|imprime) (el |tu |tus )?"
        r"(prompt|mensaje|instrucciones) (del|de) sistema\b",
    ),
    ("es_nuevas_instrucciones", r"\bnuevas instrucciones ?:"),
    ("es_no_sigas", r"\bno sigas (las|tus) instrucciones\b"),
    # Tags of the prompt itself, opened or closed, in any language.
    (
        "prompt_tag",
        r"<\s*/?\s*(project_description|project_type|reference_projects?|examples?"
        r"|example_output|example_request|client_request|system|assistant"
        r"|transcript|attachments?|project_metadata)\s*>",
    ),
)

# Matched against the original text.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
# Phone shapes with 9 or more digits, so durations, amounts, years, versions and norms
# ("10-12 semanas", "20000 EUR", "2025-2026", "v2.3.1", "ISO 27001") never match.
PHONE_RULES = _rules(
    ("phone_international", r"\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d){6,10}"),
    ("phone_es", r"\b[6789]\d{2}(?:[ .-]?\d{3}){2}\b"),
    ("phone_es_pairs", r"\b[6789]\d{2}(?:[ .-]?\d{2}){3}\b"),
    ("phone_us", r"\(\d{3}\) ?\d{3}-\d{4}"),
    ("phone_us_dashes", r"\b\d{3}-\d{3}-\d{4}\b"),
)

_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Casefold, strip accents (NFKD without combining marks) and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SPACES.sub(" ", stripped).strip()


def is_valid_iban(candidate: str) -> bool:
    """ISO 13616 mod-97 checksum: rearranged and converted to digits, remainder 1."""
    compact = candidate.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34 or not compact.isalnum():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


def _injection_rule(text: str) -> str | None:
    normalized = normalize(text)
    for rule_id, pattern in INJECTION_RULES:
        if pattern.search(normalized):
            return rule_id
    return None


def _pii_rule(text: str) -> str | None:
    if _EMAIL.search(text):
        return "email"
    if any(is_valid_iban(match.group(0)) for match in _IBAN.finditer(text)):
        return "iban"
    for rule_id, pattern in PHONE_RULES:
        if pattern.search(text):
            return rule_id
    return None


def screen_texts(texts: Sequence[tuple[str, str]]) -> GuardrailHit | None:
    """First hit over `(field, text)` pairs, or `None` if every text passes.

    Injection is checked over all the texts before personal data, so a request with
    both is reported as injection.
    """
    for field, text in texts:
        rule = _injection_rule(text)
        if rule is not None:
            return GuardrailHit("prompt_injection", field, rule)
    for field, text in texts:
        rule = _pii_rule(text)
        if rule is not None:
            return GuardrailHit("pii", field, rule)
    return None


def mask_pii(text: str) -> tuple[str, dict[PiiKind, int]]:
    """Replace personal data with placeholders; return the text and the counts per kind.

    Same patterns as the blocking check, in this order: emails, IBANs that pass mod-97
    (before the phones, whose patterns could match their digits) and the phone rules.
    Idempotent: the placeholders match no pattern. Text without personal data (durations,
    amounts, years, versions, norms) is returned unchanged.
    """
    counts: dict[PiiKind, int] = {"email": 0, "phone": 0, "iban": 0}
    text, counts["email"] = _EMAIL.subn(PII_PLACEHOLDERS["email"], text)

    def iban(match: re.Match[str]) -> str:
        if not is_valid_iban(match.group(0)):
            return match.group(0)  # IBAN-shaped code with a wrong checksum: not personal data
        counts["iban"] += 1
        return PII_PLACEHOLDERS["iban"]

    text = _IBAN.sub(iban, text)
    for _, pattern in PHONE_RULES:
        text, found = pattern.subn(PII_PLACEHOLDERS["phone"], text)
        counts["phone"] += found
    return text, counts
