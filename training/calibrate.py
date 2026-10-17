"""Calibrated confidence: P(province, city, street_name, house all correct) from parser features.

    python -m training.calibrate --gold data/dataset/dev_gold.json --out api/artifacts/calibrator.pkl [--plot]

Runs the full service (rules + model if artifacts exist) on the gold records, builds the feature vector
(confidence/features.py), fits LightGBM (fallback: logistic regression) with 5-fold CV predictions,
then isotonic regression on top for calibration. Prints ECE and writes a reliability table (and a PNG
with --plot) for the defense slide. The service picks the pickle up automatically (confidence/heuristic.py).
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.metric import ADDRESS, ENRICHABLE, norm  # noqa: E402
from jana_address.confidence.features import FEATURE_NAMES, VERSION, featurize  # noqa: E402
from jana_address.service import parse_one  # noqa: E402


def target(pred: dict, gold: dict) -> int:
    for f in ADDRESS:
        pv, gv = norm(pred.get(f)), norm(gold.get(f))
        if gv is None and (pv is None or f in ENRICHABLE):
            continue
        if pv != gv:
            return 0
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", default="api/artifacts/calibrator.pkl")
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args(argv)

    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.model_selection import cross_val_predict

    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    X, y = [], []
    for g in gold:
        p = parse_one(g["id"], g["raw_address"], explain=True)
        X.append(featurize(p["flags"], p, p.get("model_probs"), len(p["tokens"]), g["raw_address"]))
        y.append(target(p, g))
    X, y = np.array(X), np.array(y)
    print(f"{len(y)} records, base accuracy of address block = {y.mean():.3f}")

    try:
        import lightgbm as lgb
        clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10, verbose=-1)
    except ImportError:
        from sklearn.linear_model import LogisticRegression
        clf = LogisticRegression(max_iter=2000, C=1.0)
    raw_scores = cross_val_predict(clf, X, y, cv=5, method="predict_proba")[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip").fit(raw_scores, y)
    clf.fit(X, y)
    cal = iso.predict(raw_scores)

    bins = np.linspace(0, 1, 11)
    ece, rows = 0.0, []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (cal >= lo) & (cal < hi if hi < 1 else cal <= hi)
        if m.sum():
            conf, acc = cal[m].mean(), y[m].mean()
            ece += m.mean() * abs(conf - acc)
            rows.append((lo, hi, int(m.sum()), round(float(conf), 3), round(float(acc), 3)))
    print("reliability (bin, n, mean conf, accuracy):")
    for r in rows:
        print(f"  [{r[0]:.1f},{r[1]:.1f}) n={r[2]:4d} conf={r[3]:.3f} acc={r[4]:.3f}")
    print(f"ECE = {ece:.4f}")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "wb") as f:
        pickle.dump({"version": VERSION, "features": FEATURE_NAMES, "clf": clf, "iso": iso, "ece": ece, "n": int(len(y))}, f)
    print(f"→ {a.out}")
    if a.plot:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(4, 4))
        plt.plot([0, 1], [0, 1], "--", color="#999")
        plt.plot([r[3] for r in rows], [r[4] for r in rows], "o-")
        plt.xlabel("predicted confidence"); plt.ylabel("observed accuracy"); plt.title(f"Reliability (ECE={ece:.3f})")
        plt.tight_layout(); plt.savefig(Path(a.out).with_suffix(".png"), dpi=150)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
