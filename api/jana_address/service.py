"""parse_one / parse_batch — the single entry point used by the API, the CLI and tests.

    rules(raw) ─┐
                ├─ arbiter (assembly/arbiter.py) → assemble/canon → gazetteer enrichment → confidence
    model(raw) ─┘

Mode (env JANA_MODE or the `mode` argument; eval/ab.py compares them on a gold file):
  hybrid  rules ⊕ ONNX tagger through the arbiter policy (default when a model artifact exists)
  rules   rule grammar only
  model   tagger spans only (rules still supply keyword-derived meta such as the canonical province)
"""
from __future__ import annotations

import json
import sys
import time
from typing import Iterable

import os

from .assembly.arbiter import MODEL_ONLY, arbitrate
from .assembly.canon import FIELDS, assemble
from .confidence.heuristic import calibrated_confidence, confidence
from .gazetteer.enrich import enrich
from .ml.onnx_tagger import get_tagger
from .rules.grammar import RuleParser

_PARSER: RuleParser | None = None


def get_parser() -> RuleParser:
    global _PARSER
    if _PARSER is None:
        _PARSER = RuleParser()
    return _PARSER


MODE = os.environ.get("JANA_MODE", "hybrid")


def components() -> dict:
    """What is actually running (for /version and the pitch)."""
    from .gazetteer.osm import get_gazetteer
    t = get_tagger()
    gz = get_gazetteer()
    return {"rules": "v1", "model": t.kind if t.available() else "none", "mode": MODE if t.available() else "rules",
            "gazetteer": gz.kind, "gazetteer_stats": gz.stats(),
            "model_metrics": (t.charcrf.meta.get("metrics") if getattr(t, "charcrf", None) else None)}


def _empty(id_: str, raw: str) -> dict:
    out = {"id": id_, "raw_address": raw}
    out.update({f: None for f in FIELDS})
    out["confidence"] = 0.05
    return out


def parse_one(id_: str, raw: str, explain: bool = False, mode: str | None = None) -> dict:
    """Never raises: a failure returns nulls with confidence 0.05 (brief: no record may be skipped)."""
    raw = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    mode = mode or MODE
    try:
        parser = get_parser()
        rp = parser.parse(raw)
        tagger = get_tagger()
        if tagger.available() and mode != "rules":
            model_spans, tok_labels, tok_probs = tagger.tag(rp.tokens)
            arbitrate(rp, model_spans, parser, policy=MODEL_ONLY if mode == "model" else None)
            if explain:
                rp.meta["model_token_labels"] = tok_labels
                rp.meta["model_token_probs"] = [round(p, 3) for p in tok_probs]
        fields, prov = assemble(rp)
        enrich(fields, prov, rp)
        conf = calibrated_confidence(rp.flags, fields, rp.meta.get("model_probs"), len(rp.tokens), raw)
        if conf is None:
            conf = confidence(rp.flags, fields)
        if all(fields.get(k) is None for k in ("street_name", "house", "apartment", "city")):
            conf = min(conf, 0.08)     # conventions: an unparseable string gets nulls and confidence ≤ 0.1
        out = {"id": id_, "raw_address": raw, **fields, "confidence": conf}
        if explain:
            out["spans"] = [
                {"field": s.field, "start": s.start, "end": s.end, "text": raw[s.start:s.end], "rule": s.rule, "score": s.score}
                for s in sorted(rp.spans.values(), key=lambda s: s.start)
            ]
            out["tokens"] = [
                {"text": t.text, "start": t.start, "end": t.end, "kind": t.kind, "label": t.label, "role": t.role}
                for t in rp.tokens
            ]
            out["flags"] = rp.flags
            out["provenance"] = prov
            out["field_confidence"] = {s.field.lower(): round(s.score, 3) for s in rp.spans.values()}
            out["model_probs"] = rp.meta.get("model_probs", {})
            if "model_token_labels" in rp.meta:
                for tk, lab, pr in zip(out["tokens"], rp.meta["model_token_labels"], rp.meta["model_token_probs"]):
                    tk["model_label"], tk["model_prob"] = lab, pr
        return out
    except Exception as e:  # noqa: BLE001 — the batch must survive any single bad record
        out = _empty(id_, raw)
        if explain:
            out.update({"spans": [], "tokens": [], "flags": [f"error:{type(e).__name__}"], "provenance": {}, "field_confidence": {}})
        return out


def parse_batch(records: Iterable[dict], explain: bool = False, mode: str | None = None) -> list[dict]:
    return [parse_one(str(r.get("id", i)), r.get("raw_address", ""), explain, mode) for i, r in enumerate(records)]


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m jana_address.service input.json output.json [--explain]"""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("usage: python -m jana_address.service input.json output.json [--explain]")
        return 2
    explain = "--explain" in argv
    with open(argv[0], encoding="utf-8") as f:
        data = json.load(f)
    records = data["records"] if isinstance(data, dict) and "records" in data else data
    t0 = time.perf_counter()
    out = parse_batch(records, explain)
    dt = time.perf_counter() - t0
    with open(argv[1], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"{len(out)} records in {dt:.2f}s ({1000*dt/max(1,len(out)):.1f} ms/record)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
