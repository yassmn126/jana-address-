"""Offset-preserving preprocessing.

The whole system relies on one invariant: the *clean* string has exactly the same
length as the raw string, so any offset computed on `clean` is valid on `raw`.
Invisible / control characters are therefore replaced with a space, never deleted.

`extract(raw, start, end)` is the ONLY way values leave the pipeline: it cuts the
raw substring, drops invisible characters inside it and normalizes whitespace.
"""
from __future__ import annotations

import re
import unicodedata

# Zero-width and other format characters that users paste invisibly.
INVISIBLE = {
    "​", "‌", "‍", "‎", "‏", "⁠", "⁡", "⁢",
    "⁣", "⁤", "﻿", "­", "᠎", "؜", "‪", "‫",
    "‬", "‭", "‮",
}
# Exotic spaces → plain space
SPACES = {" ", " ", " ", " ", " ", " ", " ", " ",
          " ", " ", " ", "　", "\t", "\r", "\n", "\x0b", "\x0c"}

# Latin lookalikes → Cyrillic, used ONLY in the matching view (never in output).
HOMOGLYPHS = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к",
    "m": "м", "t": "т", "b": "в", "h": "н", "i": "і",
    "A": "А", "E": "Е", "O": "О", "P": "Р", "C": "С", "X": "Х", "Y": "У", "K": "К",
    "M": "М", "T": "Т", "B": "В", "H": "Н", "I": "І",
})

_WS = re.compile(r"\s+")


def clean(raw: str) -> str:
    """Same-length copy of `raw` with invisible chars and exotic spaces → ' '."""
    out = []
    for ch in raw:
        if ch in INVISIBLE or ch in SPACES:
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf"):
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    assert len(s) == len(raw)
    return s


def extract(raw: str, start: int, end: int) -> str | None:
    """Cut `raw[start:end]`, remove invisible chars, collapse whitespace."""
    if start is None or end is None or start >= end:
        return None
    piece = "".join(ch for ch in raw[start:end] if ch not in INVISIBLE
                    and unicodedata.category(ch) not in ("Cc", "Cf"))
    piece = _WS.sub(" ", piece.replace(" ", " ")).strip()
    return piece or None


def match_view(text: str) -> str:
    """Normalization for dictionary / gazetteer matching only.

    lowercase, ё→е, Latin lookalikes → Cyrillic, dots/quotes removed,
    hyphens kept (they are meaningful in 'Самал-2'), whitespace collapsed.
    """
    t = text.lower().replace("ё", "е")
    t = t.translate(HOMOGLYPHS)
    t = re.sub(r"[.\"'«»`’]", "", t)
    t = _WS.sub(" ", t).strip()
    return t


def norm_token(text: str) -> str:
    """Normalize a single token for lexicon lookup (keeps Latin for Latin keywords)."""
    t = text.lower().replace("ё", "е").strip(".")
    return t
