"""Synthetic address generator — mirrors the brief's slide-9 difficulties, one operator each.

    python -m training.synth --dataset data/dataset/train.jsonl --n 30000 --out data/dataset/synthetic.jsonl

How it works
  1. Template mining: every verified record becomes a template by replacing labeled spans with slots
     («{CITY}, мкр {STREET_NAME}, {HOUSE}, кв {APARTMENT}»). The words between slots (keywords,
     punctuation, noise) are kept verbatim — that is where the real-world style lives.
  2. Slot pools: values harvested from the verified records per field + the seed gazetteer
     (cities, districts, microdistricts) + generated numbers (fractions «33/1», letters «12а»).
  3. Noise operators (each applied with a probability, offsets are tracked so labels stay exact):
       glue          «дом 11» → «11дом», «кв 70» → «кв70», «4 подъезд» → «4подъезд»
       abbrev        ул. ↔ ул ↔ улица ↔ ulitsa ↔ көшесі, мкр ↔ микрорайон ↔ ықшам аудан …
       kk_dup        «город X» → «город X/X қаласы»
       city_dup      «Алматы» → «Алматы Алматы»
       latin_kw      «кв» → «kv», «flat»; «дом» → «dom»
       typo          «подъезд» → «подьезд», «Ул .Курильская», doubled spaces, dropped dots
       invisible     insert ​ /   / ﻿ at word boundaries
       case          lower / UPPER / Title random
       postcode      prepend a 6-digit code
       reorder       swap the order of detail chunks («кв 5, подъезд 2» ↔ «подъезд 2, кв 5»)
  Output format == build_dataset.py rows (tokens + BIO labels), so train.py can concatenate them.
  Validate ONLY on real data: synthetic rows never go into dev.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.preprocess import clean  # noqa: E402
from jana_address.rules.grammar import get_lexicon  # noqa: E402
from jana_address.tokenizer import tokenize  # noqa: E402
from training.validate_labels import to_bio  # noqa: E402

FIELDS = ["PROVINCE", "CITY", "DISTRICT", "LIVING_AREA", "STREET_TYPE", "STREET_NAME",
          "HOUSE", "APARTMENT", "ENTRANCE", "FLOOR", "LANDMARK"]

ABBREV = {
    "улица": ["ул.", "ул", "улица", "ул .", "ulitsa", "көшесі", "у."],
    "проспект": ["пр.", "пр-т", "проспект", "просп.", "даңғылы", "пр"],
    "микрорайон": ["мкр", "мкр.", "микрорайон", "м-н", "мкрн", "ықшам ауданы", "mkr"],
    "переулок": ["пер.", "переулок", "пер"],
    "квартал": ["квартал", "кв-л"],
    "дом": ["дом", "д.", "д", "үй", "dom"],
    "кв": ["кв", "кв.", "квартира", "пәтер", "kv", "flat"],
    "подъезд": ["подъезд", "подьезд", "под.", "п-д", "кіреберіс"],
    "этаж": ["этаж", "эт.", "қабат", "floor"],
    "город": ["город", "г.", "г", "қаласы"],
    "район": ["район", "р-н", "ауданы"],
}
KW_CLASS = {v: k for k, vs in ABBREV.items() for v in vs}
INVISIBLE = ["​", " ", "﻿", "‎"]
TYPOS = {"подъезд": "подьезд", "улица": "улца", "квартира": "кваритра", "микрорайон": "микрорайн", "Ул.": "Ул .", "ул.": "ул ."}


class Piece:
    __slots__ = ("text", "field")

    def __init__(self, text: str, field: str | None):
        self.text, self.field = text, field


def mine_templates(rows: list[dict]) -> tuple[list[list[Piece]], dict[str, list[str]]]:
    """Rows from build_dataset (tokens+labels) → piece lists + per-field value pools."""
    templates: list[list[Piece]] = []
    pools: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        raw, toks, labs = r["raw"], r["tokens"], r["labels"]
        pieces: list[Piece] = []
        pos = 0
        i = 0
        while i < len(toks):
            lab = labs[i]
            if lab == "O":
                i += 1
                continue
            field = lab[2:]
            j = i
            while j + 1 < len(toks) and labs[j + 1] == "I-" + field:
                j += 1
            s, e = toks[i]["start"], toks[j]["end"]
            if s > pos:
                pieces.append(Piece(raw[pos:s], None))
            pieces.append(Piece(raw[s:e], field))
            pools[field].append(raw[s:e])
            pos = e
            i = j + 1
        if pos < len(raw):
            pieces.append(Piece(raw[pos:], None))
        if any(p.field for p in pieces):
            templates.append(pieces)
    return templates, pools


def seed_pools(pools: dict[str, list[str]]) -> None:
    lex = get_lexicon()
    for rec in lex.city_by_name.values():
        pools["CITY"].append(rec["name"])
    for names in lex.city_districts.values():
        pools["DISTRICT"].extend(names)
    for city, table in lex.microdistricts.items():
        for name in table:
            pools["STREET_NAME"].append(f"{name}-{random.randint(1, 5)}")
            pools["LIVING_AREA"].append(name)


def gen_house(rnd: random.Random) -> str:
    n = rnd.randint(1, 250)
    r = rnd.random()
    if r < 0.15:
        return f"{n}/{rnd.randint(1, 9)}"
    if r < 0.25:
        return f"{n}{rnd.choice('абвг')}"
    return str(n)


def fill(template: list[Piece], pools: dict[str, list[str]], rnd: random.Random) -> list[Piece]:
    out = []
    for p in template:
        if p.field is None:
            out.append(Piece(p.text, None))
        elif p.field == "HOUSE":
            out.append(Piece(gen_house(rnd), "HOUSE"))
        elif p.field in ("APARTMENT", "ENTRANCE", "FLOOR"):
            hi = {"APARTMENT": 400, "ENTRANCE": 12, "FLOOR": 25}[p.field]
            out.append(Piece(str(rnd.randint(1, hi)), p.field))
        elif pools.get(p.field) and rnd.random() < 0.8:
            out.append(Piece(rnd.choice(pools[p.field]), p.field))
        else:
            out.append(Piece(p.text, p.field))
    return out


# --------------------------------------------------------------------------- noise operators
def op_abbrev(pieces: list[Piece], rnd: random.Random) -> None:
    for p in pieces:
        if p.field is None:
            def repl(m):
                w = m.group(0)
                cls = KW_CLASS.get(w.lower())
                return rnd.choice(ABBREV[cls]) if cls and rnd.random() < 0.5 else w
            p.text = re.sub(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі\.\-]+", repl, p.text)


_KW_BEFORE = re.compile(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]\.?\s+$")   # «дом » / «кв. » then a number
_KW_AFTER = re.compile(r"^\s+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]")       # number then « подъезд»
NUMERIC = ("HOUSE", "APARTMENT", "ENTRANCE", "FLOOR")


def op_glue(pieces: list[Piece], rnd: random.Random) -> None:
    """Glue a number to its KEYWORD only («дом 11»→«дом11», «4 подъезд»→«4подъезд»); never to another
    value — «Нурсити 2» + «11» must not become «Нурсити 211»."""
    for i in range(len(pieces) - 1):
        a, b = pieces[i], pieces[i + 1]
        if a.field is None and b.field in NUMERIC and _KW_BEFORE.search(a.text) and rnd.random() < 0.5:
            a.text = a.text.rstrip()
        if a.field in NUMERIC and b.field is None and _KW_AFTER.match(b.text) and rnd.random() < 0.3:
            b.text = b.text.lstrip()


def op_typo(pieces: list[Piece], rnd: random.Random) -> None:
    for p in pieces:
        if p.field is None:
            for k, v in TYPOS.items():
                if k in p.text and rnd.random() < 0.5:
                    p.text = p.text.replace(k, v)
            if rnd.random() < 0.2:
                p.text = p.text.replace(", ", ",  ")


def op_kk_dup(pieces: list[Piece], rnd: random.Random) -> None:
    for i, p in enumerate(pieces):
        if p.field == "CITY" and rnd.random() < 0.5:
            pieces.insert(i + 1, Piece(f"/{p.text} қаласы", None))
            return


def op_city_dup(pieces: list[Piece], rnd: random.Random) -> None:
    for i, p in enumerate(pieces):
        if p.field == "CITY" and rnd.random() < 0.3:
            pieces.insert(i + 1, Piece(f" {p.text}", None))
            return


def op_invisible(pieces: list[Piece], rnd: random.Random) -> None:
    for p in pieces:
        if p.field is None and " " in p.text and rnd.random() < 0.3:
            p.text = p.text.replace(" ", " " + rnd.choice(INVISIBLE), 1)


def op_case(pieces: list[Piece], rnd: random.Random) -> None:
    r = rnd.random()
    if r < 0.15:
        for p in pieces:
            p.text = p.text.lower()
    elif r < 0.22:
        for p in pieces:
            p.text = p.text.upper()


def op_postcode(pieces: list[Piece], rnd: random.Random) -> None:
    if rnd.random() < 0.1:
        pieces.insert(0, Piece(f"{rnd.randint(10, 99)}0000, ", None))


OPS = [op_abbrev, op_glue, op_typo, op_kk_dup, op_city_dup, op_invisible, op_case, op_postcode]


def render(pieces: list[Piece]) -> tuple[str, dict[str, tuple[int, int]]]:
    raw, spans, pos = "", {}, 0
    for p in pieces:
        raw += p.text
        if p.field and p.field not in spans:
            spans[p.field.lower()] = (pos, pos + len(p.text))
        pos += len(p.text)
    return raw, spans


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="train.jsonl from build_dataset.py (NEVER dev)")
    ap.add_argument("--n", type=int, default=30000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    rnd = random.Random(a.seed)
    rows = [json.loads(l) for l in open(a.dataset, encoding="utf-8") if l.strip()]
    templates, pools = mine_templates(rows)
    seed_pools(pools)
    if not templates:
        print("no templates — need labeled rows", file=sys.stderr)
        return 1
    n_written = 0
    with open(a.out, "w", encoding="utf-8") as f:
        while n_written < a.n:
            pieces = fill(rnd.choice(templates), pools, rnd)
            for op in OPS:
                if rnd.random() < 0.6:
                    op(pieces, rnd)
            raw, spans = render(pieces)
            toks = tokenize(clean(raw))
            if not toks:
                continue
            # every span must sit on token boundaries, otherwise the BIO labels would be partial
            starts, ends = {t.start for t in toks}, {t.end for t in toks}
            if any(s not in starts or e not in ends for s, e in spans.values()):
                continue
            labels = to_bio(raw, spans, [(t.text, t.start, t.end) for t in toks])
            if all(l == "O" for l in labels):
                continue
            f.write(json.dumps({"id": f"syn_{n_written}", "raw": raw,
                                "tokens": [{"text": t.text, "start": t.start, "end": t.end} for t in toks],
                                "labels": labels, "city": "synthetic"}, ensure_ascii=False) + "\n")
            n_written += 1
    print(f"{n_written} synthetic rows from {len(templates)} templates → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
