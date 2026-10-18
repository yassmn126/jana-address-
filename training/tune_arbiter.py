"""Grid-search the arbiter thresholds (rules ⊕ model) against the jury metric → api/artifacts/arbiter_policy.json.

    python -m training.tune_arbiter --gold data/dev_handmade.json --gold data/conventions_examples.json \
        --gold data/dataset/synth_dev_gold.json:0.3 [--max-per-file 1500] [--out api/artifacts/arbiter_policy.json]

Rule parses and model spans are computed ONCE per record; each policy only re-runs arbitrate → assemble →
enrich → score, so a 100+ point grid takes a few minutes on a laptop. `path:w` weights a gold file in the
objective (keep synthetic low — it flatters the model). Tune on DEV only, never on the final held-out set.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.ab import load_gold  # noqa: E402
from eval.metric import score  # noqa: E402
from jana_address.assembly.arbiter import DEFAULT_POLICY, arbitrate  # noqa: E402
from jana_address.assembly.canon import assemble  # noqa: E402
from jana_address.gazetteer.enrich import enrich  # noqa: E402
from jana_address.service import get_parser, get_tagger  # noqa: E402

GRID = {                         # 960 policies; deliberately coarse — the dev sets are small
    "tau_solo": [0.3, 0.5, 0.7, 0.85],
    "tau_field": [0.5, 0.65, 0.8, 0.9, 0.95],
    "tau_override": [0.9, 0.95, 0.98, 1.01],
    "tau_weak": [0.3, 0.45, 0.6, 0.75],
    "tau_veto": [0.9, 0.97, 1.01],
}


def prepare(gold: list[dict]):
    parser, tagger = get_parser(), get_tagger()
    cache = []
    for g in gold:
        rp = parser.parse(g["raw_address"])
        spans, labels, probs = tagger.tag(rp.tokens)
        cache.append((rp, spans, labels, probs))
    return cache


def evaluate(cache, gold, policy, parser) -> float:
    preds = []
    for (rp0, spans, labels, probs), g in zip(cache, gold):
        rp = copy.deepcopy(rp0)
        arbitrate(rp, spans, parser, policy=policy, tok_labels=labels, tok_probs=probs)
        fields, prov = assemble(rp)
        enrich(fields, prov, rp)
        preds.append({"id": g["id"], **fields})
    return score(preds, gold)["score"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", action="append", required=True)
    ap.add_argument("--max-per-file", type=int, default=1500)
    ap.add_argument("--out", default=str(ROOT / "api/artifacts/arbiter_policy.json"))
    a = ap.parse_args(argv)
    if not get_tagger().available():
        print("no model artifact — nothing to tune", file=sys.stderr)
        return 2
    parser = get_parser()
    sets = []
    for spec in a.gold:
        path, _, w = spec.partition(":")
        gold = load_gold(path)[: a.max_per_file]
        sets.append((Path(path).stem, float(w) if w else 1.0, gold, prepare(gold)))
    print("prepared:", ", ".join(f"{n} {len(g)}×{w}" for n, w, g, _ in sets), flush=True)

    def objective(policy):
        per = {n: evaluate(c, g, policy, parser) for n, w, g, c in sets}
        return sum(w * per[n] for n, w, _, _ in sets) / sum(w for _, w, _, _ in sets), per

    base_obj, base_per = objective({**DEFAULT_POLICY})
    print(f"default policy: {base_obj:.4f} {json.dumps({k: round(v, 4) for k, v in base_per.items()})}", flush=True)
    best = (base_obj, {**DEFAULT_POLICY}, base_per)
    t0 = time.time()
    keys = list(GRID)
    for i, combo in enumerate(itertools.product(*(GRID[k] for k in keys))):
        pol = {**DEFAULT_POLICY, **dict(zip(keys, combo)), "field_tau": {}}
        obj, per = objective(pol)
        if obj > best[0] + 1e-9:
            best = (obj, pol, per)
            print(f"  [{i}] {obj:.4f} {dict(zip(keys, combo))} {json.dumps({k: round(v, 4) for k, v in per.items()})}", flush=True)
    obj, pol, per = best
    out = {k: v for k, v in pol.items() if k != "ignore_rules"}
    out["_tuned"] = {"objective": round(obj, 4), "per_set": {k: round(v, 4) for k, v in per.items()},
                     "default_objective": round(base_obj, 4), "gold": a.gold, "grid": GRID,
                     "built": time.strftime("%Y-%m-%d %H:%M")}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"best {obj:.4f} (default {base_obj:.4f}) → {a.out} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
