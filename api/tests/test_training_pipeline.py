"""End-to-end smoke test of the data factory without any API/GPU:
rules-labeler → build_dataset → synth, checking BIO/offset consistency."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from training.validate_labels import to_bio  # noqa: E402


def _run(*args):
    r = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _spans_from_row(row):
    spans, cur = {}, None
    for t, l in zip(row["tokens"], row["labels"]):
        if l == "O":
            cur = None
            continue
        f = l[2:]
        if l.startswith("B-") or cur != f:
            spans[f] = [t["start"], t["end"]]
            cur = f
        else:
            spans[f][1] = t["end"]
    return spans


def test_label_build_synth_roundtrip(tmp_path):
    ex = json.loads((ROOT / "data" / "conventions_examples.json").read_text(encoding="utf-8"))["examples"]
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps([{"id": e["id"], "raw_address": e["raw_address"]} for e in ex], ensure_ascii=False), encoding="utf-8")
    labels = tmp_path / "labels.jsonl"
    _run("training.label_llm", "--input", str(inp), "--output", str(labels), "--provider", "rules")
    out = tmp_path / "dataset"
    _run("training.build_dataset", "--labels", str(labels), "--out", str(out), "--dev-size", "10")
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["train"] + stats["dev"] >= len(ex) - 2, stats
    rows = [json.loads(l) for l in (out / "train.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for row in rows:
        # every labeled span is a verbatim substring and B/I are well-formed
        for f, (s, e) in _spans_from_row(row).items():
            assert row["raw"][s:e].strip() == row["raw"][s:e]
        assert len(row["tokens"]) == len(row["labels"])
    gold = json.loads((out / "dev_gold.json").read_text(encoding="utf-8"))
    assert gold and set(gold[0]) >= {"id", "raw_address", "street_name", "house"}
    # synthetic generation on top of the mined templates
    syn = tmp_path / "syn.jsonl"
    _run("training.synth", "--dataset", str(out / "train.jsonl"), "--n", "200", "--out", str(syn))
    srows = [json.loads(l) for l in syn.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(srows) == 200
    for row in srows[:50]:
        spans = _spans_from_row(row)
        assert spans, row["raw"]
        # labels recomputed from spans must equal the stored labels (offset integrity after noise ops)
        rec = to_bio(row["raw"], {k.lower(): tuple(v) for k, v in spans.items()}, [(t["text"], t["start"], t["end"]) for t in row["tokens"]])
        assert rec == row["labels"]
