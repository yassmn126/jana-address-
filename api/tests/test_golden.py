"""Golden tests: every example in data/conventions_examples.json must parse to its
`expected` fields (compared with the jury's normalization). Examples marked `hard`
are allowed to fail (xfail) — they are targets for the ML tagger / gazetteer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))                 # for eval.metric
sys.path.insert(0, str(ROOT / "api"))         # for jana_address

from eval.metric import ADDRESS, DETAILS, REFS, norm  # noqa: E402
from jana_address.service import parse_one   # noqa: E402

EXAMPLES = json.loads((ROOT / "data" / "conventions_examples.json").read_text(encoding="utf-8"))["examples"]
SCORED = ADDRESS + DETAILS + REFS


def _params():
    for ex in EXAMPLES:
        marks = [pytest.mark.xfail(reason="hard: needs ML tagger / gazetteer", strict=False)] if ex.get("hard") else []
        yield pytest.param(ex, id=ex["id"], marks=marks)


@pytest.mark.parametrize("ex", list(_params()))
def test_scored_fields(ex):
    out = parse_one(ex["id"], ex["raw_address"])
    exp = ex["expected"]
    enrich = ex.get("enrichment", {})
    mismatches = []
    for f in SCORED:
        got, want = norm(out.get(f)), norm(exp.get(f))
        if want is None and f in ("province", "city", "district", "living_area"):
            # gazetteer may fill it: accept null OR the enrichment value
            if got is not None and got != norm(enrich.get(f)):
                mismatches.append((f, out.get(f), f"null or {enrich.get(f)!r}"))
            continue
        if got != want:
            mismatches.append((f, out.get(f), exp.get(f)))
    assert not mismatches, f"{ex['raw_address']!r}: " + "; ".join(f"{f}: got {g!r}, want {w!r}" for f, g, w in mismatches)


@pytest.mark.parametrize("ex", list(_params()))
def test_unscored_fields_soft(ex):
    """street_type / landmark are not in the jury metric but are judged at the defense."""
    out = parse_one(ex["id"], ex["raw_address"])
    exp = ex["expected"]
    for f in ("street_type", "landmark"):
        assert norm(out.get(f)) == norm(exp.get(f)), f"{f}: got {out.get(f)!r}, want {exp.get(f)!r}"


def test_values_are_substrings_of_raw():
    """Invariant: every value with provenance 'string' is cut from the raw string."""
    for ex in EXAMPLES:
        out = parse_one(ex["id"], ex["raw_address"], explain=True)
        raw_cmp = norm(ex["raw_address"]) or ""
        for f, source in out["provenance"].items():
            v = out.get(f)
            if v is None or source != "string":
                continue
            assert norm(v) in raw_cmp, f"{f}={v!r} is not a substring of {ex['raw_address']!r}"


def test_never_raises_and_always_answers():
    for raw in ["", "   ", "​", "???", "1234567890", None, "кв", "ул.", "/", "город", 12345]:
        out = parse_one("x", raw)  # type: ignore[arg-type]
        assert out["id"] == "x"
        assert 0.0 <= out["confidence"] <= 1.0
        assert set(out) >= {"province", "city", "street_name", "house", "confidence"}


def test_sample_output_matches_brief():
    sample = json.loads((ROOT / "data" / "sample_output.json").read_text(encoding="utf-8"))[0]
    out = parse_one(sample["id"], sample["raw_address"])
    for f in SCORED + ["street_type"]:
        assert norm(out[f]) == norm(sample[f]), f"{f}: got {out[f]!r}, want {sample[f]!r}"
    assert out["raw_address"] == sample["raw_address"]
