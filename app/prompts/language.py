"""Language of the project description, for the explicit language line of the prompt.

S04 design D14 measured that gpt-4o-mini ignores a generic "answer in the language of
the description" rule when the user message carries anything besides an English
description (the project type tag, Spanish reference projects): it answers in the
Spanish of the system prompt. Naming the language explicitly fixes it, so the loader
detects it here with a pure, dependency-free heuristic: counting frequent function
words of each supported language. Ambiguous text returns `None` and the prompt keeps
only the generic rule.
"""

import re

from app.domain import Language

ResponseLanguage = Language

# Frequent function words exclusive to each language (words shared by both, such as
# "a" or "no", are left out so they never tip the balance).
_WORDS: dict[ResponseLanguage, frozenset[str]] = {
    "es": frozenset(
        {
            "de",
            "la",
            "el",
            "los",
            "las",
            "del",
            "que",
            "y",
            "en",
            "para",
            "con",
            "por",
            "una",
            "un",
            "se",
            "su",
            "sus",
            "es",
            "al",
            "como",
            "más",
            "pero",
            "este",
            "esta",
            "están",
            "hay",
            "desde",
            "cada",
        }
    ),
    "en": frozenset(
        {
            "the",
            "and",
            "of",
            "to",
            "for",
            "with",
            "is",
            "are",
            "an",
            "in",
            "on",
            "by",
            "from",
            "that",
            "this",
            "it",
            "be",
            "will",
            "should",
            "their",
            "has",
            "have",
            "or",
            "but",
            "each",
        }
    ),
}
# Minimum hits of the winning language, and how many times it must outnumber the other.
MIN_HITS = 3
MIN_RATIO = 2

_WORD_RE = re.compile(r"[a-záéíóúñü]+")


def detect_language(text: str) -> ResponseLanguage | None:
    """Return `"es"` or `"en"` when the text is clearly in that language, else `None`."""
    words = _WORD_RE.findall(text.lower())
    hits = {lang: sum(word in vocabulary for word in words) for lang, vocabulary in _WORDS.items()}
    (winner, top), (_, other) = sorted(hits.items(), key=lambda item: item[1], reverse=True)
    if top < MIN_HITS or top < MIN_RATIO * other:
        return None
    return winner
