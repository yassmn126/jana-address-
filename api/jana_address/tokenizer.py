"""Regex tokenizer that keeps character offsets.

Token kinds:
  WORD   letters, optionally hyphen-joined with letters/digits:  Нур-Султан, Самал-2, пр-т, Аксай-3а
  NUM    digits:                                                 66, 55, 239
  ALNUM  digits + ONE trailing letter (house literal):            12а, 3а, 97б
  PUNCT  everything else, one char per token:                    , / . - № ( )

Glued writing is split at letter/digit boundaries:  66квартал → 66 | квартал,
Назарбаева8 → Назарбаева | 8,  4подьезд → 4 | подьезд.
A digit run followed by exactly one letter (and then a non-letter) stays one ALNUM
token, so "12а" and "Аксай 3а" survive as written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LETTERS = "A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі" + "ÄäĞğİıÑñÖöŞşŪūÜüÇçÁáÉéÍíÓóÚúÝýǴǵŃń"   # + Kazakh Latin (2018/2021)
_LET = f"[{LETTERS}]"
_TOKEN_RE = re.compile(
    rf"(?P<word>(?<![0-9]){_LET}+(?:-(?:{_LET}|[0-9])+)*|{_LET}+)"   # letters, hyphen-joined chunks (not after a digit: «52а-18»)
    rf"|(?P<num>[0-9]+)"
    rf"|(?P<punct>[^\s])"
)


@dataclass
class Token:
    text: str
    start: int
    end: int
    kind: str                      # WORD | NUM | ALNUM | PUNCT
    space_before: bool = False
    label: str | None = None       # field label assigned by the parser
    role: str | None = None        # lexicon role (keyword class) if any
    meta: dict = field(default_factory=dict)

    @property
    def is_sep(self) -> bool:
        return self.kind == "PUNCT" and self.text in {",", ";", "|"}

    @property
    def is_slash(self) -> bool:
        return self.kind == "PUNCT" and self.text == "/"

    @property
    def is_number_like(self) -> bool:
        return self.kind in ("NUM", "ALNUM")

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.kind}({self.text!r}@{self.start}:{self.end}{'/'+self.label if self.label else ''})"


def tokenize(clean: str) -> list[Token]:
    toks: list[Token] = []
    for m in _TOKEN_RE.finditer(clean):
        kind = "WORD" if m.group("word") else "NUM" if m.group("num") else "PUNCT"
        text = m.group(0)
        space_before = m.start() > 0 and clean[m.start() - 1].isspace()
        # Hyphen-joined WORD that starts with digits can't happen (regex starts with letters),
        # but a WORD like "Самал-2" is kept whole: good for microdistrict names.
        toks.append(Token(text, m.start(), m.end(), kind, space_before))

    # Merge NUM + single letter (no space) → ALNUM when followed by a non-letter / end.
    merged: list[Token] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if (
            t.kind == "NUM"
            and i + 1 < len(toks)
            and toks[i + 1].kind == "WORD"
            and len(toks[i + 1].text) == 1
            and not toks[i + 1].space_before
            and (i + 2 >= len(toks) or toks[i + 2].kind != "WORD" or toks[i + 2].space_before)
        ):
            n = toks[i + 1]
            merged.append(Token(t.text + n.text, t.start, n.end, "ALNUM", t.space_before))
            i += 2
            continue
        merged.append(t)
        i += 1

    # Merge 1-letter WORD / 1-letter WORD patterns like ж/к, ж/м, р/н into one WORD.
    out: list[Token] = []
    i = 0
    while i < len(merged):
        t = merged[i]
        if (
            t.kind == "WORD" and len(t.text) <= 2 and i + 2 < len(merged)
            and merged[i + 1].is_slash and not merged[i + 1].space_before
            and merged[i + 2].kind == "WORD" and len(merged[i + 2].text) <= 2
            and not merged[i + 2].space_before
        ):
            b = merged[i + 2]
            out.append(Token(t.text + "/" + b.text, t.start, b.end, "WORD", t.space_before))
            i += 3
            continue
        out.append(t)
        i += 1
    return out
