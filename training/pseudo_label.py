"""Offline self-training labels: rules ⊕ model agreement on unlabeled train.json → silver labels + review queue.

    python -m training.pseudo_label --input data/train.json --output data/labels_pseudo.jsonl [--tau 0.9]

No data leaves the machine (answers the brief's «данные не выносятся» question by construction) and it
needs no API key. Per record:
  * rules-only parse and model-only parse (string-derived fields only — gazetteer fills are never labels);
  * agree on every scored field AND the model's least confident labeled token ≥ tau AND no validator
    error  → silver label (`needs_review: false`, source "pseudo:agree");
  * otherwise → review queue (`needs_review: true`) with `disagreements` = fields where rules ≠ model;
    the hybrid answer is the suggested label, the UI (HANDOFF A1) shows both.
Output lines have the label_llm.py shape, so build_dataset.py / the review UI consume them unchanged.

Typical use on day 0: pseudo-label everything → humans review only the disagreements (≈ 10–25 % of rows)
→ build_dataset --only-verified → fine-tune the synthetic-pretrained tagger with --init.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.metric import norm  # noqa: E402
from jana_address.service import get_tagger, parse_one  # noqa: E402
from training.label_llm import from_string  # noqa: E402
from training.validate_labels import FIELDS, resolve_spans, validate_record  # noqa: E402

SCORED = ["province", "city", "district", "living_area", "street_name", "house", "apartment", "entrance", "floor"]


def string_fields(p: dict) -> dict:
    return {f: (p[f] if from_string(p["provenance"].get(f)) else None) for f in FIELDS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--tau", type=float, default=0.9)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    if not get_tagger().available():
        print("no model in api/artifacts — train one first (make ml-cold-start)", file=sys.stderr)
        return 2
    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    records = data["records"] if isinstance(data, dict) and "records" in data else data
    if a.limit:
        records = records[: a.limit]
    stats = Counter()
    with open(a.output, "w", encoding="utf-8") as fo:
        for r in records:
            rid, raw = str(r["id"]), r.get("raw_address", "")
            pr = parse_one(rid, raw, explain=True, mode="rules")
            pm = parse_one(rid, raw, explain=True, mode="model")
            ph = parse_one(rid, raw, explain=True, mode="hybrid")
            rf, mf, hf = string_fields(pr), string_fields(pm), string_fields(ph)
            disagreements = [f for f in SCORED if norm(rf[f]) != norm(mf[f])]
            probs = [t.get("model_prob") or 0.0 for t in ph["tokens"] if (t.get("model_label") or "O") != "O"]
            min_prob = min(probs) if probs else 0.0
            issues = validate_record(raw, hf)
            _, span_issues = resolve_spans(raw, hf)
            issues += span_issues
            n_err = sum(1 for i in issues if i["severity"] == "error")
            silver = not disagreements and min_prob >= a.tau and n_err == 0 and any(hf.values())
            stats["silver" if silver else "review"] += 1
            stats["disagree"] += bool(disagreements)
            for f in disagreements:
                stats[f"disagree:{f}"] += 1
            fo.write(json.dumps({
                "id": rid, "raw_address": raw, "fields": hf, "rules_fields": rf, "model_fields": mf,
                "disagreements": disagreements, "issues": issues, "needs_review": not silver,
                "labeler": "pseudo:agree" if silver else "pseudo:hybrid", "model_min_prob": round(min_prob, 3),
                "confidence": ph["confidence"],
            }, ensure_ascii=False) + "\n")
    n = max(1, len(records))
    print(f"{len(records)} records: silver {stats['silver']} ({100 * stats['silver'] / n:.1f}%), "
          f"review {stats['review']} ({100 * stats['review'] / n:.1f}%) → {a.output}")
    print("disagreements by field:", {k[9:]: v for k, v in stats.most_common() if k.startswith("disagree:")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
