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
_LAT2CYR = {
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к",
    "m": "м", "t": "т", "b": "в", "h": "н", "i": "і",
    "A": "А", "E": "Е", "O": "О", "P": "Р", "C": "С", "X": "Х", "Y": "У", "K": "К",
    "M": "М", "T": "Т", "B": "В", "H": "Н", "I": "І",
}
HOMOGLYPHS = str.maketrans(_LAT2CYR)
_CYR2LAT = str.maketrans({v: k for k, v in _LAT2CYR.items()})
# Kazakh Latin (2021) and other diacritics → plain ASCII, so «daŋğyly», «köşesi», «päter» hit the lexicon
_LAT_DIACRITICS = str.maketrans({"ŋ": "n", "ñ": "n", "ğ": "g", "ş": "sh", "ä": "a", "ö": "o", "ü": "u", "ı": "i", "ū": "u",
                                 "ç": "ch", "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ý": "y", "ǵ": "g", "ń": "n"})

_WS = re.compile(r"\s+")


_ESCAPES = re.compile(r"\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[ntr])")   # literal «\u00a0», «\n» from broken exports


def clean(raw: str) -> str:
    """Same-length copy of `raw` with invisible chars, exotic spaces and literal escape sequences → ' '."""
    raw = _ESCAPES.sub(lambda m: " " * len(m.group(0)), raw)
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
    piece = "".join(" " if ch in SPACES else ch for ch in raw[start:end]
                    if ch in SPACES or (ch not in INVISIBLE and unicodedata.category(ch) not in ("Cc", "Cf")))
    piece = _WS.sub(" ", piece.replace(" ", " ")).strip()
    return piece or None


def match_view(text: str) -> str:
    """Normalization for dictionary / gazetteer matching only.

    lowercase, ё→е, Latin lookalikes → Cyrillic, dots/quotes removed,
    hyphens kept (they are meaningful in 'Самал-2'), whitespace collapsed.
    """
    t = text.lower().replace("ё", "е").translate(_LAT_DIACRITICS)
    t = t.translate(HOMOGLYPHS)
    t = re.sub(r"[.\"'«»`’]", "", t)
    t = _WS.sub(" ", t).strip()
    return t


def norm_token(text: str) -> str:
    """Normalize a single token for lexicon lookup (keeps Latin for Latin keywords).
    Latin diacritics are folded; a token mixing scripts («аvenue», «Aлматы») is folded to its majority script."""
    t = text.lower().replace("ё", "е").strip(".").translate(_LAT_DIACRITICS)
    lat = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    cyr = sum(1 for ch in t if "а" <= ch <= "я" or ch in "әғқңөұүһі")
    if lat and cyr:
        t = t.translate(_CYR2LAT) if lat >= cyr else t.translate(HOMOGLYPHS)
    return t
