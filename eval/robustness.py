"""Robustness (metamorphic) tests + per-category accuracy on the hand-labeled gold sets.

    python -m eval.robustness [--gold data/test_handmade.json ...] [--mode hybrid] [--md runs/robustness.md]

Metamorphic: each transformation changes the STRING but not the answer (the jury compares lower-cased, punctuation-free
values, so case and spacing changes leave the gold unchanged). For every transformation we report
  score        jury metric of the transformed strings against the unchanged gold
  consistency  % of records whose scored fields are identical to the prediction on the original string
A drop in consistency is brittleness the hidden set will find: real users type with NBSP, without commas, in capitals,
with their phone number appended.

Per category: records are tagged by what makes them hard (glued numbers, Kazakh, Latin, микрорайон, fraction, dash,
landmark, village, no city, no street type, 3+ details) and scored separately — weak spots show up as low rows.
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

from eval.ab import load_gold  # noqa: E402
from eval.metric import ADDRESS, DETAILS, REFS, norm, score  # noqa: E402
from jana_address.service import parse_one  # noqa: E402

SCORED = ADDRESS + DETAILS + REFS
DEFAULT_GOLD = ["data/test_handmade.json", "data/stress_handmade.json", "data/dev_handmade.json", "data/conventions_examples.json",
                "data/rural_handmade.json"]


def _kv_latin(s: str) -> str:
    return re.sub(r"\bкв\.?\s*(?=\d)", lambda m: "kv " if m.group(0).endswith(" ") or not m.group(0).endswith(".") else "kv. ", s)


TRANSFORMS = {
    "lower": str.lower,
    "upper": str.upper,
    "nbsp": lambda s: s.replace(" ", " "),
    "zwsp_after_comma": lambda s: s.replace(",", ",​"),
    "double_space": lambda s: s.replace(" ", "  "),
    "tab_newline": lambda s: s.replace(", ", ",\n", 1).replace(" ", "\t", 1),
    "no_space_after_comma": lambda s: re.sub(r",\s+", ",", s),
    "commas_to_spaces": lambda s: re.sub(r"\s*,\s*", " ", s),
    "bom_prefix": lambda s: "﻿" + s,
    "country_prefix": lambda s: "Казахстан, " + s,
    "postcode_prefix": lambda s: "050000, " + s,
    "phone_suffix": lambda s: s + ", тел 87011234567",
    "note_suffix": lambda s: s + ", позвонить заранее",
    "trailing_dot": lambda s: s.rstrip(".") + ".",
    "kv_latin": _kv_latin,
    "kv_glued": lambda s: re.sub(r"\bкв\.?\s+(?=\d)", "кв", s),
    "semicolons": lambda s: s.replace(",", ";"),
}

CATEGORIES = {
    "glued": lambda r, g: bool(re.search(r"\d[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]{2,}|[А-Яа-яЁё]{2,}\d", r)),
    "kazakh": lambda r, g: bool(re.search(r"[ӘәҒғҚқҢңӨөҰұҮүҺһІі]", r)),
    "latin": lambda r, g: bool(re.search(r"[A-Za-z]{2,}", r)),
    "microdistrict": lambda r, g: g.get("street_type") in ("микрорайон", "квартал"),
    "fraction_house": lambda r, g: "/" in (g.get("house") or ""),
    "letter_house": lambda r, g: bool(re.search(r"\d[^\d/]$", g.get("house") or "")),
    "dash_apartment": lambda r, g: bool(g.get("house") and g.get("apartment") and re.search(
        re.escape(g["house"]) + r"\s*-\s*" + re.escape(g["apartment"]), r)),
    "landmark": lambda r, g: bool(g.get("landmark")),
    "living_area": lambda r, g: bool(g.get("living_area")),
    "district_in_string": lambda r, g: bool(g.get("district")),
    "province_in_string": lambda r, g: bool(g.get("province")) and g.get("province") not in ("Алматы", "Астана", "Шымкент"),
    "no_city": lambda r, g: not g.get("city"),
    "no_street_type": lambda r, g: bool(g.get("street_name")) and not g.get("street_type"),
    "3plus_details": lambda r, g: sum(bool(g.get(f)) for f in DETAILS) >= 3 - 1 and bool(g.get("entrance") or g.get("floor")),
    "noise_words": lambda r, g: bool(re.search(r"тел|получатель|ИП |ТОО|код|домофон|звонить|\+7|87\d{9}", r)),
}


def scored_view(p: dict) -> tuple:
    return tuple(norm(p.get(f)) for f in SCORED)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", action="append")
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "model", "rules"])
    ap.add_argument("--md", help="write the report as markdown")
    ap.add_argument("--examples", type=int, default=4, help="inconsistent examples shown per transformation")
    a = ap.parse_args(argv)
    gold = [g for p in (a.gold or DEFAULT_GOLD) for g in load_gold(str(ROOT / p) if not Path(p).is_absolute() else p)]
    base = [parse_one(g["id"], g["raw_address"], mode=a.mode) for g in gold]
    base_score = score(base, gold)["score_pct"]
    lines = [f"# Robustness report — mode {a.mode}, {len(gold)} hand-labeled records", "",
             f"Original strings: jury score **{base_score:.2f}**", "",
             "| transformation | jury score | Δ | consistency | changed |", "|---|---|---|---|---|"]
    bad_examples: dict[str, list] = {}
    summary = {}
    for name, fn in TRANSFORMS.items():
        preds, changed = [], []
        for g, b in zip(gold, base):
            raw2 = fn(g["raw_address"])
            p = parse_one(g["id"], raw2, mode=a.mode)
            preds.append(p)
            if scored_view(p) != scored_view(b):
                diff = {f: (b.get(f), p.get(f)) for f in SCORED if norm(b.get(f)) != norm(p.get(f))}
                changed.append((raw2, diff))
        s = score(preds, gold)["score_pct"]
        cons = 100 * (1 - len(changed) / len(gold))
        summary[name] = {"score": s, "consistency": round(cons, 2), "changed": len(changed)}
        lines.append(f"| {name} | {s:.2f} | {s - base_score:+.2f} | {cons:.1f} % | {len(changed)} |")
        bad_examples[name] = changed[: a.examples]
    lines += ["", "## Per category (original strings)", "", "| category | n | jury score | all-4 per record |", "|---|---|---|---|"]
    by_cat = defaultdict(list)
    for g, p in zip(gold, base):
        for c, test in CATEGORIES.items():
            if test(g["raw_address"], g):
                by_cat[c].append((p, g))
    cats = {}
    for c in CATEGORIES:
        items = by_cat.get(c, [])
        if not items:
            continue
        r = score([p for p, _ in items], [g for _, g in items])
        cats[c] = {"n": len(items), "score": r["score_pct"], "all4": r["address_all4_per_record"]}
        lines.append(f"| {c} | {len(items)} | {r['score_pct']:.2f} | {100 * r['address_all4_per_record']:.1f} % |")
    lines += ["", "## Inconsistent examples (original → transformed)", ""]
    for name, items in bad_examples.items():
        if not items:
            continue
        lines.append(f"**{name}**")
        for raw2, diff in items:
            d = "; ".join(f"{f}: {o!r} → {n!r}" for f, (o, n) in diff.items())
            lines.append(f"- `{raw2!r}` — {d}")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    if a.md:
        Path(a.md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.md).write_text(text, encoding="utf-8")
        Path(a.md).with_suffix(".json").write_text(json.dumps({"base": base_score, "transforms": summary, "categories": cats},
                                                              ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
