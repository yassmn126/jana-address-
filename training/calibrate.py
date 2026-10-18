"""Calibrated confidence: P(province, city, street_name, house all correct) from parser features.

    python -m training.calibrate --gold data/dataset/dev_gold.json [--gold more_gold.json:3] \
        [--out api/artifacts/calibrator.json] [--plot]

Runs the full service (rules + model + gazetteer, current arbiter policy) on the gold records, builds the
feature vector (confidence/features.py — shared with inference), fits a standardized logistic regression
with 5-fold out-of-fold predictions, then isotonic regression on those for calibration. Writes a JSON file
(coefficients + isotonic table) so the runtime needs only numpy. Prints ECE and the reliability table;
`--plot` saves a reliability diagram PNG for the defense slide.

`path:k` repeats a gold file k times (weight real data over synthetic when both are used).
Refit on the REAL dev set as soon as it exists — synthetic gold makes the model over-confident on real data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.ab import load_gold  # noqa: E402
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


def reliability(p, y, bins=10):
    import numpy as np
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.sum():
            conf, acc = float(p[m].mean()), float(y[m].mean())
            ece += m.mean() * abs(conf - acc)
            rows.append({"lo": round(float(lo), 1), "hi": round(float(hi), 1), "n": int(m.sum()),
                         "conf": round(conf, 3), "acc": round(acc, 3)})
    return rows, float(ece)


def review_budget(p, y) -> list[dict]:
    """«review the N% least confident records» → accuracy of the rest (operational promise for the pitch)."""
    import numpy as np
    order = np.argsort(p)
    out = []
    for frac in (0.0, 0.05, 0.1, 0.2, 0.3):
        k = int(round(frac * len(p)))
        rest = order[k:]
        out.append({"review_pct": int(frac * 100), "auto_accuracy": round(float(y[rest].mean()), 4) if len(rest) else 1.0})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", action="append", required=True, help="gold file, optionally path:weight")
    ap.add_argument("--out", default="api/artifacts/calibrator.json")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--C", type=float, default=0.5)
    ap.add_argument("--eval-gold", action="append", default=[], help="held-out gold: report ECE / accuracy, not used to fit")
    a = ap.parse_args(argv)

    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold

    X, y, w = [], [], []
    for spec in a.gold:
        path, _, k = spec.partition(":")
        weight = float(k) if k else 1.0
        for g in load_gold(path):
            p = parse_one(g["id"], g["raw_address"], explain=True)
            X.append(featurize(p["flags"], p, p.get("model_probs"), len(p["tokens"]), g["raw_address"]))
            y.append(target(p, g))
            w.append(weight)
    X, y, w = np.array(X, dtype=np.float64), np.array(y), np.array(w)
    print(f"{len(y)} records, address-block accuracy = {np.average(y, weights=w):.4f}")
    mean, scale = X.mean(0), X.std(0)
    scale[scale == 0] = 1.0
    Xs = (X - mean) / scale

    oof = np.zeros(len(y))
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    for tr, te in skf.split(Xs, y):
        clf = LogisticRegression(C=a.C, max_iter=3000)
        clf.fit(Xs[tr], y[tr], sample_weight=w[tr])
        oof[te] = clf.predict_proba(Xs[te])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(oof, y, sample_weight=w)
    cal = iso.predict(oof)
    rows, ece = reliability(cal, y)
    raw_rows, raw_ece = reliability(oof, y)
    clf = LogisticRegression(C=a.C, max_iter=3000).fit(Xs, y, sample_weight=w)

    print("reliability (out-of-fold, after isotonic):")
    for r in rows:
        print(f"  [{r['lo']:.1f},{r['hi']:.1f}) n={r['n']:5d} conf={r['conf']:.3f} acc={r['acc']:.3f}")
    print(f"ECE = {ece:.4f} (before isotonic {raw_ece:.4f})")
    budget = review_budget(cal, y)
    for b in budget:
        print(f"  review {b['review_pct']:2d}% least confident → remaining accuracy {b['auto_accuracy']:.4f}")
    top = sorted(zip(FEATURE_NAMES, clf.coef_[0]), key=lambda t: -abs(t[1]))[:12]
    print("strongest features:", ", ".join(f"{n} {c:+.2f}" for n, c in top))

    out = {"version": VERSION, "features": FEATURE_NAMES, "mean": mean.round(6).tolist(), "scale": scale.round(6).tolist(),
           "coef": clf.coef_[0].round(6).tolist(), "intercept": float(clf.intercept_[0]),
           "iso_x": [float(v) for v in iso.X_thresholds_], "iso_y": [float(v) for v in iso.y_thresholds_],
           "ece": round(ece, 4), "n": int(len(y)), "reliability": rows, "review_budget": budget,
           "trained_on": a.gold}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"→ {a.out}")
    held = {}
    for path in a.eval_gold:
        ps, ys = [], []
        for g in load_gold(path):
            p = parse_one(g["id"], g["raw_address"], explain=True)
            xs = (np.array(featurize(p["flags"], p, p.get("model_probs"), len(p["tokens"]), g["raw_address"])) - mean) / scale
            z = float(xs @ clf.coef_[0] + clf.intercept_[0])
            ps.append(float(np.interp(1 / (1 + np.exp(-z)), out["iso_x"], out["iso_y"])))
            ys.append(target(p, g))
        ps, ys = np.array(ps), np.array(ys)
        _, e = reliability(ps, ys)
        held[Path(path).stem] = {"n": int(len(ys)), "accuracy": round(float(ys.mean()), 4),
                                 "mean_confidence": round(float(ps.mean()), 4), "ece": round(e, 4)}
        print(f"held-out {Path(path).stem}: n={len(ys)} acc={ys.mean():.4f} mean conf={ps.mean():.4f} ECE={e:.4f}")
    if held:
        out["held_out"] = held
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.figure(figsize=(4, 4))
        plt.plot([0, 1], [0, 1], "--", color="#999")
        plt.plot([r["conf"] for r in rows], [r["acc"] for r in rows], "o-", label=f"calibrated (ECE {ece:.3f})")
        plt.plot([r["conf"] for r in raw_rows], [r["acc"] for r in raw_rows], "x:", alpha=0.6, label=f"raw (ECE {raw_ece:.3f})")
        plt.xlabel("predicted confidence"); plt.ylabel("observed accuracy"); plt.legend(loc="upper left", fontsize=8)
        plt.title("Address-block confidence"); plt.tight_layout()
        png = Path(a.out).with_suffix(".png")
        plt.savefig(png, dpi=150)
        print(f"→ {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
