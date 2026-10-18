"""Score the service on a hard3k gold file: overall, per theme, per region, per difficulty, plus the error report.

    python -m eval.hard3k_report --gold data/hard3k/hard3k_s2026_gold.json --out runs/hard3k_s2026 [--mode hybrid]

Writes <out>/{summary.json, report.md, errors.md, pred.json}. Uses the jury metric replica (eval/metric.py, strict).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.metric import ADDRESS, DETAILS, REFS, errors, norm, score  # noqa: E402
from jana_address.service import parse_batch  # noqa: E402

FIELDS = ADDRESS + DETAILS + REFS


def _wrong_fields(p: dict, g: dict) -> list[str]:
    out = []
    for f in FIELDS:
        pv, gv = norm(p.get(f)), norm(g.get(f))
        if gv is None and (pv is None or f in ("province", "city", "district", "living_area")):
            continue
        if pv != gv:
            out.append(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default=None, help="hybrid | rules | model (default: the service default)")
    a = ap.parse_args(argv)
    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    pred = parse_batch([{"id": g["id"], "raw_address": g["raw_address"]} for g in gold], mode=a.mode)
    dt = time.perf_counter() - t0
    overall = score(pred, gold)
    groups: dict[str, dict[str, list[int]]] = {"theme": defaultdict(list), "region": defaultdict(list), "difficulty": defaultdict(list)}
    for i, g in enumerate(gold):
        groups["theme"][g.get("_theme", "?")].append(i)
        groups["region"][str(g.get("_region"))].append(i)
        groups["difficulty"][g.get("_difficulty", "?")].append(i)
    breakdown = {}
    for kind, d in groups.items():
        breakdown[kind] = {}
        for k, idx in sorted(d.items()):
            s = score([pred[i] for i in idx], [gold[i] for i in idx])
            wrong = sum(1 for i in idx if _wrong_fields(pred[i], gold[i]))
            breakdown[kind][k] = {"n": len(idx), "score": s["score_pct"], "records_with_error": wrong,
                                  "address": round(100 * s["groups"]["address"], 2), "details": round(100 * s["groups"]["details"], 2),
                                  "refs": round(100 * s["groups"]["refs"], 2)}
    tag_err: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p, g in zip(pred, gold):
        bad = bool(_wrong_fields(p, g))
        for t in g.get("_tags", []):
            tag_err[t][0] += 1
            tag_err[t][1] += int(bad)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"gold": a.gold, "n": len(gold), "seconds": round(dt, 2), "ms_per_record": round(1000 * dt / max(1, len(gold)), 2),
               "score": overall["score_pct"], "groups": overall["groups"], "address_all4_per_record": overall["address_all4_per_record"],
               "per_field": overall["per_field"], "breakdown": breakdown,
               "tags": {t: {"n": n, "with_error": e, "error_rate": round(e / n, 4)} for t, (n, e) in sorted(tag_err.items())}}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "pred.json").write_text(json.dumps(pred, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "errors.md").write_text(errors(pred, gold, limit=60), encoding="utf-8")
    lines = [f"# hard3k — {Path(a.gold).name}", "",
             f"**Jury score {overall['score_pct']}** on {len(gold)} records (address {100 * overall['groups']['address']:.2f}, "
             f"details {100 * overall['groups']['details']:.2f}, refs {100 * overall['groups']['refs']:.2f}); "
             f"{dt:.2f} s ({1000 * dt / max(1, len(gold)):.2f} ms/record).", ""]
    for kind in ("difficulty", "theme", "region"):
        lines += [f"## By {kind}", "", "| " + kind + " | n | score | address | details | refs | records with an error |", "|---|---|---|---|---|---|---|"]
        rows = sorted(breakdown[kind].items(), key=lambda kv: kv[1]["score"])
        for k, v in rows:
            lines.append(f"| {k} | {v['n']} | {v['score']} | {v['address']} | {v['details']} | {v['refs']} | {v['records_with_error']} |")
        lines.append("")
    lines += ["## Per field", "", "| field | accuracy | hits / total |", "|---|---|---|"]
    for f, v in overall["per_field"].items():
        lines.append(f"| {f} | {100 * v['acc']:.2f} | {v['hit']} / {v['total']} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4]))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
