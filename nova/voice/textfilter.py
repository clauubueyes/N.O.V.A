from __future__ import annotations

import re

#: Common profanity (es/en); matched as whole words, case-insensitive.
_PROFANITY = {
    "mierda",
    "joder",
    "jodido",
    "coño",
    "conho",
    "puta",
    "puto",
    "cabron",
    "cabrón",
    "gilipollas",
    "gilipolla",
    "hijo de puta",
    "hija de puta",
    "joputa",
    "hijoeputa",
    "hijueputa",
    "malparido",
    "maricon",
    "maricón",
    "pendejo",
    "pendeja",
    "polla",
    "culo",
    "conchetumare",
    "concha tu madre",
    "huevon",
    "huevón",
    "pajero",
    "verga",
    "fuck",
    "fucking",
    "shit",
    "bitch",
    "asshole",
    "bastard",
    "dick",
    "dumbass",
    "cunt",
    "motherfucker",
    "son of a bitch",
}

_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_RE = re.compile(r"(?:^|\n)[ \t]*#{1,6}[ \t]+")
_EMPHASIS_RE = re.compile(r"\*{1,3}|_{1,3}")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\uFE0F\u2B00-\u2BFF\u2190-\u21FF]"
)
_WS_RE = re.compile(r"\s+")

_PROF_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_PROFANITY, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def scrub_profanity(text: str, replacement: str = "...") -> str:
    """Replace profanity (es/en) with `replacement`, whole-word insensitive."""
    return _PROF_RE.sub(replacement, text)


def clean_for_tts(text: str) -> str:
    """Make an LLM answer pleasant to read aloud: strips Markdown/code, URLs,
    images, emojis and profanity, then collapses whitespace."""
    t = _MD_IMAGE_RE.sub(" ", text)
    t = _CODE_FENCE_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _INLINE_CODE_RE.sub(r"\1", t)
    t = _HEADING_RE.sub(" ", t)
    t = _EMPHASIS_RE.sub(" ", t)
    t = _EMOJI_RE.sub(" ", t)
    t = scrub_profanity(t)
    return _WS_RE.sub(" ", t).strip()