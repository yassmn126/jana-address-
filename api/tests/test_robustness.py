"""Metamorphic tests (eval/robustness.py, pinned): transformations that change the string but not the answer must
not change the scored fields. Runs on the hand-labeled sets with whatever model is in api/artifacts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.ab import load_gold  # noqa: E402
from eval.robustness import TRANSFORMS, scored_view  # noqa: E402
from jana_address.service import parse_one  # noqa: E402

# transformations that must be 100 % invariant (commas_to_spaces / suffixes can make a string genuinely ambiguous)
STRICT = ["lower", "upper", "nbsp", "zwsp_after_comma", "double_space", "tab_newline", "bom_prefix", "country_prefix",
          "postcode_prefix", "kv_latin", "kv_glued", "semicolons"]
GOLD = load_gold(str(ROOT / "data" / "test_handmade.json")) + load_gold(str(ROOT / "data" / "stress_handmade.json"))


@pytest.mark.parametrize("name", STRICT)
def test_invariant_under(name):
    fn = TRANSFORMS[name]
    changed = []
    for g in GOLD:
        a = scored_view(parse_one(g["id"], g["raw_address"]))
        b = scored_view(parse_one(g["id"], fn(g["raw_address"])))
        if a != b:
            changed.append(fn(g["raw_address"]))
    assert not changed, f"{name}: {len(changed)} records changed, e.g. {changed[:3]}"
