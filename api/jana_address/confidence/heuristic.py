"""Confidence v0 — hand-tuned heuristic over parser flags.

Target semantics (keep them when you replace this with the calibrated model):
    confidence ≈ P(province, city, street_name, house are all correct)

v1 plan (training/calibrate.py): LightGBM or logistic regression on the same flags +
model span probabilities + gazetteer hits, fitted on the human-verified dev set,
then isotonic calibration. Report ECE and a reliability diagram at the defense.
"""
from __future__ import annotations

PENALTIES = {
    "no_street": 0.35,
    "no_house": 0.20,
    "street_without_type": 0.05,
    "house_without_keyword": 0.05,
    "house_ambiguous": 0.10,
    "ambiguous_number_binding": 0.10,
    "ambiguous_p_as_apartment": 0.05,
    "ambiguous_p_as_entrance": 0.08,
    "leftover_word": 0.07,       # per occurrence, capped below
    "leftover_number": 0.10,     # per occurrence, capped below
    "multiple_street_types": 0.10,
    "microdistrict_as_living_area": 0.03,
    "city_kk_only": 0.05,
    "keyword_without_number": 0.05,
    "duplicate_house": 0.10,
    "duplicate_apartment": 0.05,
    "house_has_building_part": 0.05,
    "district_kw_without_name": 0.05,
    "empty": 0.95,
}
CAPS = {"leftover_word": 0.25, "leftover_number": 0.30}


_CAL = None
_CAL_TRIED = False


def _calibrator():
    """Load api/artifacts/calibrator.pkl once (training/calibrate.py). None → heuristic."""
    global _CAL, _CAL_TRIED
    if not _CAL_TRIED:
        _CAL_TRIED = True
        import os
        import pickle
        from pathlib import Path
        path = Path(os.environ.get("JANA_ARTIFACTS_DIR", Path(__file__).resolve().parents[2] / "artifacts")) / "calibrator.pkl"
        if path.exists() and os.environ.get("JANA_CALIBRATOR", "auto") != "off":
            try:
                with path.open("rb") as f:
                    _CAL = pickle.load(f)
            except Exception:  # noqa: BLE001 — a broken pickle must not take the service down
                _CAL = None
    return _CAL


def calibrated_confidence(flags: list[str], fields: dict, model_probs: dict | None, n_tokens: int, raw: str) -> float | None:
    cal = _calibrator()
    if cal is None:
        return None
    from .features import VERSION, featurize
    if cal.get("version") != VERSION:
        return None
    x = [featurize(flags, fields, model_probs, n_tokens, raw)]
    p = float(cal["clf"].predict_proba(x)[0][1])
    p = float(cal["iso"].predict([p])[0])
    return round(max(0.01, min(0.99, p)), 2)


def confidence(flags: list[str], fields: dict) -> float:
    score = 0.96
    counted: dict[str, float] = {}
    for f in flags:
        pen = PENALTIES.get(f, 0.0)
        if f in CAPS:
            counted[f] = min(CAPS[f], counted.get(f, 0.0) + pen)
        else:
            counted[f] = max(counted.get(f, 0.0), pen)
    score -= sum(counted.values())
    if fields.get("city") is None and fields.get("province") is None:
        score -= 0.04
    if all(fields.get(k) is None for k in ("street_name", "house", "apartment", "city")):
        score = min(score, 0.08)
    return round(max(0.05, min(0.98, score)), 2)
