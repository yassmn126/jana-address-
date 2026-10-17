"""Feature vector for the calibrated confidence model. Shared by training/calibrate.py and inference,
so the two can never drift apart. Keep FEATURE_NAMES append-only (the pickle stores the version)."""
from __future__ import annotations

FLAG_FEATURES = [
    "no_street", "no_house", "street_without_type", "house_without_keyword", "house_ambiguous",
    "ambiguous_number_binding", "ambiguous_p_as_apartment", "ambiguous_p_as_entrance", "leftover_word",
    "leftover_number", "multiple_street_types", "microdistrict_as_living_area", "city_kk_only",
    "keyword_without_number", "duplicate_house", "duplicate_apartment", "house_has_building_part",
    "district_kw_without_name", "city_duplicate", "postcode", "living_area_from_gazetteer",
]
ARB_FIELDS = ["province", "city", "street_name", "house", "apartment", "entrance", "floor", "district", "living_area"]
ARB_OUTCOMES = ["agree", "rule_only", "model_only", "model_wins", "rule_wins", "model_rejected", "overlap_dropped"]
FEATURE_NAMES = (
    [f"flag:{f}" for f in FLAG_FEATURES]
    + [f"arb:{f}:{o}" for f in ARB_FIELDS for o in ARB_OUTCOMES]
    + [f"prob:{f}" for f in ARB_FIELDS]
    + ["has:province", "has:city", "has:street_name", "has:house", "has:apartment", "n_tokens", "has_latin", "has_slash"]
)
VERSION = 1


def featurize(flags: list[str], fields: dict, model_probs: dict | None, n_tokens: int, raw: str) -> list[float]:
    fl = set(flags)
    counts = {f: flags.count(f) for f in ("leftover_word", "leftover_number")}
    x: list[float] = []
    for f in FLAG_FEATURES:
        x.append(float(counts.get(f, 1 if f in fl else 0)))
    for f in ARB_FIELDS:
        for o in ARB_OUTCOMES:
            x.append(1.0 if f"arb_{f}_{o}" in fl else 0.0)
    mp = model_probs or {}
    for f in ARB_FIELDS:
        x.append(float(mp.get(f.upper(), 0.0)))
    for f in ("province", "city", "street_name", "house", "apartment"):
        x.append(1.0 if fields.get(f) else 0.0)
    x.append(float(n_tokens))
    x.append(1.0 if any("a" <= ch.lower() <= "z" for ch in raw) else 0.0)
    x.append(1.0 if "/" in raw else 0.0)
    assert len(x) == len(FEATURE_NAMES)
    return x
