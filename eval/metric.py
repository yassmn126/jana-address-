"""Replica of the jury metric (brief, slides 10–11).

Per field, exact match after: lowercase → strip punctuation → collapse whitespace.
Weights: address 60% (province, city, street_name, house) · details 25% (apartment,
entrance, floor) · reference fields 15% (district, living_area; only where present in
the string). Records where a field is absent in both prediction and gold are excluded
from that field's count. street_type and landmark are NOT scored (checked at the defense).

Usage:
    python -m eval.metric --pred out.json --gold gold.json [--lenient] [--errors errors.md]
    python -m eval.metric --pred out.json --gold gold.json --json      # machine-readable

`--lenient`: a predicted value where gold is null never counts as an error (the brief
only promises this for gazetteer-filled fields; strict is our pessimistic default).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict

PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
WS = re.compile(r"\s+")

ADDRESS = ["province", "city", "street_name", "house"]
DETAILS = ["apartment", "entrance", "floor"]
REFS = ["district", "living_area"]
UNSCORED = ["street_type", "landmark"]
ENRICHABLE = {"province", "city", "district", "living_area"}
WEIGHTS = {"address": 0.60, "details": 0.25, "refs": 0.15}


def norm(v) -> str | None:
    if v is None:
        return None
    v = PUNCT.sub("", str(v).lower())
    v = WS.sub(" ", v).strip()
    return v if v else None


def field_accuracy(preds: list[dict], golds: list[dict], field: str, strict: bool = True) -> tuple[float, int, int]:
    """Return (accuracy, hits, total) for one field."""
    hit = tot = 0
    for p, g in zip(preds, golds):
        pv, gv = norm(p.get(field)), norm(g.get(field))
        if gv is None and (pv is None or field in ENRICHABLE or not strict):
            continue
        tot += 1
        hit += int(pv == gv)
    return (hit / tot if tot else 1.0), hit, tot


def score(preds: list[dict], golds: list[dict], strict: bool = True) -> dict:
    """Weighted jury score + per-group, per-field and per-record diagnostics."""
    assert len(preds) == len(golds), "pred/gold length mismatch"
    per_field = {}
    for f in ADDRESS + DETAILS + REFS + UNSCORED:
        acc, hit, tot = field_accuracy(preds, golds, f, strict)
        per_field[f] = {"acc": round(acc, 4), "hit": hit, "total": tot}
    group = {
        "address": sum(per_field[f]["acc"] for f in ADDRESS) / len(ADDRESS),
        "details": sum(per_field[f]["acc"] for f in DETAILS) / len(DETAILS),
        "refs": sum(per_field[f]["acc"] for f in REFS) / len(REFS),
    }
    total = sum(WEIGHTS[k] * group[k] for k in WEIGHTS)
    # alternative reading of "точность адреса": all four address fields right in the same record
    rec_ok = rec_tot = 0
    for p, g in zip(preds, golds):
        considered = [f for f in ADDRESS if not (norm(g.get(f)) is None and (norm(p.get(f)) is None or f in ENRICHABLE or not strict))]
        if not considered:
            continue
        rec_tot += 1
        rec_ok += int(all(norm(p.get(f)) == norm(g.get(f)) for f in considered))
    return {
        "score": round(total, 4),
        "score_pct": round(100 * total, 2),
        "groups": {k: round(v, 4) for k, v in group.items()},
        "address_all4_per_record": round(rec_ok / rec_tot, 4) if rec_tot else 1.0,
        "per_field": per_field,
        "n": len(preds),
        "strict": strict,
    }


def errors(preds: list[dict], golds: list[dict], strict: bool = True, limit: int = 30) -> str:
    """Markdown error report grouped by field: raw string, predicted, gold."""
    by_field: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    confusion = Counter()
    for p, g in zip(preds, golds):
        for f in ADDRESS + DETAILS + REFS:
            pv, gv = norm(p.get(f)), norm(g.get(f))
            if gv is None and (pv is None or f in ENRICHABLE or not strict):
                continue
            if pv != gv:
                by_field[f].append((g.get("raw_address", p.get("raw_address", "")), str(p.get(f)), str(g.get(f))))
                kind = "null→value" if pv is None else "value→null" if gv is None else "wrong"
                confusion[(f, kind)] += 1
    lines = ["# Error report", ""]
    lines.append("| field | kind | count |")
    lines.append("|---|---|---|")
    for (f, kind), c in sorted(confusion.items(), key=lambda x: -x[1]):
        lines.append(f"| {f} | {kind} | {c} |")
    for f, items in by_field.items():
        lines += ["", f"## {f} — {len(items)} errors", "", "| raw | pred | gold |", "|---|---|---|"]
        for raw, pv, gv in items[:limit]:
            raw_esc = raw.replace("|", "\\|")      # (kept outside the f-string: Python 3.11 compatibility)
            lines.append(f"| {raw_esc} | {pv} | {gv} |")
    return "\n".join(lines)


def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "records" in data:
        data = data["records"]
    return data


def align(preds: list[dict], golds: list[dict]) -> tuple[list[dict], list[dict]]:
    """Align by id (order-independent); missing predictions become empty records."""
    by_id = {str(p.get("id")): p for p in preds}
    out_p, out_g = [], []
    for g in golds:
        out_g.append(g)
        out_p.append(by_id.get(str(g.get("id")), {"id": g.get("id"), "raw_address": g.get("raw_address")}))
    return out_p, out_g


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--lenient", action="store_true")
    ap.add_argument("--errors", help="write a markdown error report to this path")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    a = ap.parse_args(argv)
    preds, golds = align(_load(a.pred), _load(a.gold))
    res = score(preds, golds, strict=not a.lenient)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"JURY SCORE  {res['score_pct']:.2f}%   (strict={res['strict']}, n={res['n']})")
        print(f"  address 60%: {res['groups']['address']:.4f}   details 25%: {res['groups']['details']:.4f}   refs 15%: {res['groups']['refs']:.4f}")
        print(f"  all-4-address-fields-per-record: {res['address_all4_per_record']:.4f}")
        print("  per field:")
        for f, v in res["per_field"].items():
            tag = "" if f not in UNSCORED else "  (not scored by jury)"
            print(f"    {f:12s} {v['acc']:.4f}  ({v['hit']}/{v['total']}){tag}")
    if a.errors:
        with open(a.errors, "w", encoding="utf-8") as f:
            f.write(errors(preds, golds, strict=not a.lenient))
        print(f"error report → {a.errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
