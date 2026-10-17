"""A/B: rules vs model vs hybrid on one or more gold files, with the jury metric replica.

    python -m eval.ab --gold data/dev_handmade.json --gold data/conventions_examples.json \
        [--gold data/dataset/dev_gold.json] [--errors-dir runs/ab] [--json runs/ab/ab.json]

Gold: jury-format list (id, raw_address, 11 fields) or the conventions file ({"examples": [{raw_address, expected}]}).
Prints JURY SCORE per mode and per file, plus the per-field accuracy table and the disagreement count
(records where hybrid ≠ rules) — the number to look at when tuning the arbiter.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.metric import ADDRESS, DETAILS, REFS, errors, norm, score  # noqa: E402
from jana_address.service import get_tagger, parse_one  # noqa: E402

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
MODES = ["rules", "model", "hybrid"]


def load_gold(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("examples") or data.get("records") or []
    out = []
    for i, g in enumerate(data):
        if "expected" in g:
            out.append({"id": g.get("id", str(i)), "raw_address": g["raw_address"], **g["expected"]})
        else:
            out.append({"id": g.get("id", str(i)), "raw_address": g["raw_address"], **{f: g.get(f) for f in FIELDS}})
    return out


def run(gold: list[dict], mode: str) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    preds = [parse_one(g["id"], g["raw_address"], mode=mode) for g in gold]
    return preds, (time.perf_counter() - t0) / max(1, len(gold)) * 1000


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", action="append", required=True)
    ap.add_argument("--errors-dir")
    ap.add_argument("--json")
    ap.add_argument("--modes", default=",".join(MODES))
    a = ap.parse_args(argv)
    modes = a.modes.split(",")
    if not get_tagger().available():
        print("no model artifact in api/artifacts — only 'rules' is meaningful", file=sys.stderr)
        modes = ["rules"]
    report = {}
    for path in a.gold:
        gold = load_gold(path)
        name = Path(path).stem
        report[name] = {}
        preds_by_mode = {}
        for m in modes:
            preds, ms = run(gold, m)
            preds_by_mode[m] = preds
            res = score(preds, gold)
            res["ms_per_record"] = round(ms, 2)
            report[name][m] = res
            if a.errors_dir:
                Path(a.errors_dir).mkdir(parents=True, exist_ok=True)
                Path(a.errors_dir, f"{name}.{m}.md").write_text(errors(preds, gold), encoding="utf-8")
        if "hybrid" in preds_by_mode and "rules" in preds_by_mode:
            diff = sum(1 for p, q in zip(preds_by_mode["hybrid"], preds_by_mode["rules"])
                       if any(norm(p.get(f)) != norm(q.get(f)) for f in ADDRESS + DETAILS + REFS))
            report[name]["hybrid_vs_rules_changed_records"] = diff
        print(f"\n== {name} (n={len(gold)})")
        print(f"  {'mode':8s} {'SCORE':>7s} {'addr':>6s} {'det':>6s} {'refs':>6s} {'all4':>6s} {'ms/rec':>7s}   " +
              " ".join(f"{f[:7]:>7s}" for f in ADDRESS + DETAILS + REFS))
        for m in modes:
            r = report[name][m]
            print(f"  {m:8s} {r['score_pct']:7.2f} {r['groups']['address']:6.3f} {r['groups']['details']:6.3f} "
                  f"{r['groups']['refs']:6.3f} {r['address_all4_per_record']:6.3f} {r['ms_per_record']:7.2f}   " +
                  " ".join(f"{r['per_field'][f]['acc']:7.3f}" for f in ADDRESS + DETAILS + REFS))
        if "hybrid_vs_rules_changed_records" in report[name]:
            print(f"  hybrid changed {report[name]['hybrid_vs_rules_changed_records']} records vs rules")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
