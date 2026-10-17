"""labels (LLM / rules / human) → token-level BIO dataset for the tagger + jury-format gold files.

    python -m training.build_dataset --labels data/labels_llm.jsonl [--labels more.jsonl] \
        [--corrections data/corrections.jsonl] [--only-verified] --out data/dataset --dev-size 700

Inputs
  --labels       JSONL from training/label_llm.py  ({"id","raw_address","fields",...})
  --corrections  JSONL from the UI (POST /api/v1/corrections: {"id","raw_address","fields"}) — overrides labels
  --only-verified keep records that were accepted in the UI (present in corrections) or had no issues
Outputs (in --out)
  train.jsonl / dev.jsonl   {"id","raw","tokens":[{"text","start","end"}],"labels":["B-STREET_NAME",...],"city"}
  train_gold.json / dev_gold.json   jury-format gold for `python -m eval.metric`
  labels.json               label list (index = class id) — copied to api/artifacts by export_onnx.py
  stats.json                counts per field / city

Tokens come from jana_address.tokenizer on the SAME cleaned string the service uses, so the model's
view at training time equals its view at inference time. Split is stratified by city (rule parser's
city) so dev has the same city mix as train; dev is frozen by writing `dev_ids.txt` — rerunning keeps
the same dev ids when the file exists.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.preprocess import clean  # noqa: E402
from jana_address.service import parse_one  # noqa: E402
from jana_address.tokenizer import tokenize  # noqa: E402
from training.validate_labels import FIELDS, STRING_FIELDS, resolve_spans, to_bio, validate_record  # noqa: E402

LABEL_FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
                "house", "apartment", "entrance", "floor", "landmark"]
LABELS = ["O"] + [p + f.upper() for f in LABEL_FIELDS for p in ("B-", "I-")]


def _read_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _spans_for_record(raw: str, fields: dict) -> tuple[dict[str, tuple[int, int]], list[dict]]:
    """Char spans for every labeled field.

    Values copied verbatim are located directly. Values that are NOT substrings — canonical province,
    street_type lemma, a Kazakh city mapped to Russian («Қарағанды» → «Караганда»), a joined house
    («12 корпус 2» → «12/2») — get the WRITTEN span from the rule parser when it produced the same value;
    otherwise the field has no span (and the record is dropped from the tagger set by the caller)."""
    spans, issues = resolve_spans(raw, fields)
    rp = parse_one("x", raw, explain=True)
    for f in LABEL_FIELDS:
        if fields.get(f) and f not in spans and rp.get(f) == fields[f]:
            sp = next((s for s in rp["spans"] if s["field"] == f.upper()), None)
            if sp:
                spans[f] = (sp["start"], sp["end"])
    return spans, issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", action="append", required=True)
    ap.add_argument("--corrections", action="append", default=[])
    ap.add_argument("--only-verified", action="store_true")
    ap.add_argument("--out", default="data/dataset")
    ap.add_argument("--dev-size", type=int, default=700)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args(argv)

    records: dict[str, dict] = {}
    for path in a.labels:
        for r in _read_jsonl(path):
            records[r["id"]] = {"id": r["id"], "raw": r["raw_address"], "fields": r["fields"],
                                "verified": False, "issues": r.get("issues", [])}
    corrected: set[str] = set()
    for path in a.corrections:
        for c in _read_jsonl(path):
            rec = records.setdefault(c["id"], {"id": c["id"], "raw": c["raw_address"], "fields": {}, "verified": True, "issues": []})
            rec["fields"] = {f: c["fields"].get(f) for f in LABEL_FIELDS}
            rec["verified"] = True
            corrected.add(c["id"])
    if a.only_verified:
        records = {k: v for k, v in records.items()
                   if v["verified"] or not any(i.get("severity") == "error" for i in v["issues"])}

    rows, dropped = [], Counter()
    for r in records.values():
        raw = r["raw"]
        fields = {f: (r["fields"].get(f) or None) for f in LABEL_FIELDS}
        errs = [i for i in validate_record(raw, fields) if i["severity"] == "error" and i["code"] != "not_in_string"]
        spans, span_issues = _spans_for_record(raw, fields)
        missing = [f for f in STRING_FIELDS if fields.get(f) and f not in spans]
        if errs or missing:
            dropped["invalid"] += 1
            continue
        toks = tokenize(clean(raw))
        labels = to_bio(raw, spans, [(t.text, t.start, t.end) for t in toks])
        city = parse_one("x", raw)["city"] or "∅"
        rows.append({"id": r["id"], "raw": raw, "tokens": [{"text": t.text, "start": t.start, "end": t.end} for t in toks],
                     "labels": labels, "city": city, "verified": r["verified"], "fields": fields})

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dev_ids_path = out / "dev_ids.txt"
    if dev_ids_path.exists():
        dev_ids = set(dev_ids_path.read_text(encoding="utf-8").split())
    else:
        rnd = random.Random(a.seed)
        by_city: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_city[row["city"]].append(row)
        dev_ids = set()
        total = len(rows)
        for city, items in by_city.items():
            rnd.shuffle(items)
            k = round(a.dev_size * len(items) / max(1, total))
            dev_ids.update(x["id"] for x in items[:k])
        # prefer verified records in dev
        dev_ids_path.write_text("\n".join(sorted(dev_ids)), encoding="utf-8")
    train = [r for r in rows if r["id"] not in dev_ids]
    dev = [r for r in rows if r["id"] in dev_ids]

    for name, part in (("train", train), ("dev", dev)):
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps({k: r[k] for k in ("id", "raw", "tokens", "labels", "city")}, ensure_ascii=False) + "\n")
        gold = [{"id": r["id"], "raw_address": r["raw"], **r["fields"]} for r in part]
        (out / f"{name}_gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "labels.json").write_text(json.dumps(LABELS, ensure_ascii=False, indent=1), encoding="utf-8")

    stats = {"train": len(train), "dev": len(dev), "dropped": dict(dropped), "verified": sum(r["verified"] for r in rows),
             "fill_rate": {f: round(sum(1 for r in rows if r["fields"].get(f)) / max(1, len(rows)), 3) for f in LABEL_FIELDS},
             "cities": Counter(r["city"] for r in rows).most_common(20)}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
