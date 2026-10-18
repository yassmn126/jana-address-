"""Jury-format gold files → the labels JSONL that build_dataset.py reads.

    python -m training.gold_to_labels data/claude_generated.json data/claude_heldout.json --out runs/claude_labels.jsonl

Use: the Claude-written sets (docs/BENCHMARK.md) as fine-tuning rows once they have served as benchmarks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gold", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    n = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for path in a.gold:
            for g in json.loads(Path(path).read_text(encoding="utf-8")):
                f.write(json.dumps({"id": f"{Path(path).stem}:{g['id']}", "raw_address": g["raw_address"],
                                    "fields": {k: g.get(k) for k in FIELDS}, "issues": [], "labeler": "claude-gold"},
                                   ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} labels → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
