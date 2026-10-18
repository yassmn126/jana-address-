"""Day 0 — train.json arrives: two independent labelers, agreement = gold, disagreement = human review, then retrain.

    python -m training.day0 --input data/train.json [--claude] [--limit 0] [--skip-train]

Steps (each writes its file, so a step can be re-run alone):
  1. ours     training/pseudo_label.py   → OUT/labels_pseudo.jsonl   (rules ⊕ model, offline)
  2. claude   training/label_llm.py      → OUT/labels_llm.jsonl      (--claude; Claude Opus 5.5, or a local vLLM via
                                                                      --provider openai-compatible; --resume-safe)
  3. merge    agreement on every scored field (jury normalization) → verified; else → review queue
              → OUT/labels_day0.jsonl (build_dataset input) + OUT/review.html (decide → corrections.jsonl)
  4. dataset  build_dataset --only-verified (+ data/corrections.jsonl exported from the review page) → OUT/dataset/
  5. train    warm-start the tagger on real rows (×5) + 60k synthetic → api/artifacts (unless --skip-train)
  6. tune / calibrate / A/B on the real dev split, logged to eval/history.jsonl
Without --claude, step 3 uses our high-confidence silver labels only (pseudo_label's agreement of rules and model).
Data policy: sending train.json to an external API needs the organizers' OK (docs/CONVENTIONS.md Q9) — otherwise run
step 2 against the university GPU (`--provider openai-compatible`, OPENAI_BASE_URL) or skip --claude.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

PY = sys.executable
SCORED = ["province", "city", "district", "living_area", "street_name", "house", "apartment", "entrance", "floor"]


def sh(*args: str) -> None:
    print("$", " ".join(args), flush=True)
    r = subprocess.run([PY, "-W", "ignore", "-m", *args], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"step failed: {' '.join(args)}")


def read_jsonl(p: Path) -> dict[str, dict]:
    return {json.loads(l)["id"]: json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def merge(pseudo: Path, llm: Path | None, out: Path, review_dir: Path) -> dict:
    from eval.llm_verify import write_html
    from eval.metric import norm

    ours = read_jsonl(pseudo)
    theirs = read_jsonl(llm) if llm else {}
    agreed = review = 0
    rows_for_review = []
    with out.open("w", encoding="utf-8") as f:
        for rid, o in ours.items():
            mine = o["fields"]
            other = theirs.get(rid, {}).get("fields") if theirs else None
            if other is not None:
                dis = [k for k in SCORED + ["street_type"] if norm(mine.get(k)) != norm(other.get(k))]
                ok = not dis and not any(i.get("severity") == "error" for i in theirs[rid].get("issues", []))
            else:
                dis = o.get("disagreements", [])
                ok = not o.get("needs_review", True)
            issues = [] if ok else [{"severity": "error", "code": "needs_human", "field": d} for d in (dis or ["review"])]
            f.write(json.dumps({"id": rid, "raw_address": o["raw_address"], "fields": mine, "issues": issues,
                                "disagreements": dis, "needs_review": not ok,
                                "llm_fields": other, "labeler": "day0:agree" if ok else "day0:review"}, ensure_ascii=False) + "\n")
            if ok:
                agreed += 1
            else:
                review += 1
                if other is not None:
                    rows_for_review.append({"id": rid, "raw_address": o["raw_address"], "ours": mine, "llm": other,
                                            "gold": None, "disagreements": dis, "issues": []})
    review_dir.mkdir(parents=True, exist_ok=True)
    if rows_for_review:
        write_html(review_dir / "review.html", rows_for_review)
    return {"agreed": agreed, "review": review, "review_page": str(review_dir / "review.html") if rows_for_review else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/train.json")
    ap.add_argument("--claude", action="store_true", help="second labeler = Claude (or --provider openai-compatible)")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-train", action="store_true")
    ap.add_argument("--out-dir", default="runs/day0")
    a = ap.parse_args(argv)
    t0 = time.time()
    out_dir = ROOT / a.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    lim = ["--limit", str(a.limit)] if a.limit else []
    od = a.out_dir
    REAL = f"{od}/dataset"            # its own split: data/dataset holds the synthetic corpora

    sh("training.pseudo_label", "--input", a.input, "--output", f"{od}/labels_pseudo.jsonl", *lim)
    llm = None
    if a.claude:
        sh("training.label_llm", "--input", a.input, "--output", f"{od}/labels_llm.jsonl", "--provider", a.provider,
           "--resume", *lim)
        llm = out_dir / "labels_llm.jsonl"
    stats = merge(out_dir / "labels_pseudo.jsonl", llm, out_dir / "labels_day0.jsonl", out_dir)
    print(f"merge: {stats}", flush=True)
    corr = ["--corrections", "data/corrections.jsonl"] if (ROOT / "data/corrections.jsonl").exists() else []
    dev_size = str(max(50, min(700, stats["agreed"] // 5)))
    sh("training.build_dataset", "--labels", f"{od}/labels_day0.jsonl", *corr, "--only-verified", "--out", REAL,
       "--dev-size", dev_size)
    if not a.skip_train:
        backup = out_dir / "artifacts_before"          # roll back = copy these back to api/artifacts
        backup.mkdir(exist_ok=True)
        for name in ("model.onnx", "model_meta.json", "arbiter_policy.json", "calibrator.json"):
            if (ROOT / "api/artifacts" / name).exists():
                shutil.copy2(ROOT / "api/artifacts" / name, backup / name)
        sh("training.train_charcrf", "--init", "runs/charcrf/model.pt", "--train", f"{REAL}/train.jsonl",
           "--train", "data/dataset/synth_grammar.jsonl", "--max-train", "60000", "--real-weight", "5",
           "--dev", f"{REAL}/dev.jsonl", "--test", "data/test_handmade.json", "--out", f"{od}/charcrf",
           "--export", "api/artifacts", "--epochs", "4", "--lr", "1e-3")
        sh("training.tune_arbiter", "--gold", f"{REAL}/dev_gold.json", "--gold", "data/stress_handmade.json:0.5")
        sh("training.calibrate", "--gold", f"{REAL}/dev_gold.json", "--eval-gold", "data/test_handmade.json")
    sh("eval.ab", "--gold", f"{REAL}/dev_gold.json", "--gold", "data/test_handmade.json", "--gold", "data/stress_handmade.json",
       "--errors-dir", str(out_dir / "ab"), "--log", f"day0 on {a.input} ({stats['agreed']} agreed, {stats['review']} to review)")
    (out_dir / "summary.json").write_text(json.dumps({**stats, "input": a.input, "claude": a.claude,
                                                      "minutes": round((time.time() - t0) / 60, 1)}, indent=1), encoding="utf-8")
    print(f"day 0 done in {(time.time() - t0) / 60:.1f} min — review: {stats['review_page'] or 'nothing to review'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
