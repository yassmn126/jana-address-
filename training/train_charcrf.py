"""Train the char-CNN + BiLSTM + CRF tagger and export it to ONNX (api/artifacts/model.onnx + model_meta.json).

    python -m training.train_charcrf --train data/dataset/synth_grammar.jsonl [--train data/dataset/train.jsonl ...] \
        --dev data/dataset/synth_dev.jsonl --test data/dev_handmade.json --test data/conventions_examples.json \
        --out runs/charcrf --export api/artifacts [--epochs 8] [--real-weight 5] [--init runs/charcrf/model.pt]

Rows: build_dataset.py / synth*.py JSONL ({"raw","tokens","labels"}). `--test` also accepts jury-format gold
JSON (list or {"examples": [...]}) — converted to BIO with the same span resolver as build_dataset.py.
Rows whose `city` is not "synthetic" are real and are oversampled `--real-weight` times.
`--init` warm-starts from a previous run (e.g. synthetic-only pretraining → fine-tune on real labels);
vocabularies are kept from the init run so the embedding rows line up.

CPU is enough (M-series laptop: ~1 min / epoch on 150k rows); `--device cuda` on the GPU box.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.ml.charcrf import (FEATURES, GAZ_SLICE, MAX_CHARS, SHAPES, Featurizer, bio_mask,  # noqa: E402
                                     build_gazetteer_sets)
from jana_address.preprocess import clean, norm_token  # noqa: E402
from jana_address.tokenizer import Token, tokenize  # noqa: E402

LABEL_FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
                "house", "apartment", "entrance", "floor", "landmark"]
LABELS = ["O"] + [p + f.upper() for f in LABEL_FIELDS for p in ("B-", "I-")]


# --------------------------------------------------------------------------- data
def load_rows(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix == ".jsonl":
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("examples") or data.get("records") or []
    return [gold_to_row(g) for g in data]


def gold_to_row(g: dict) -> dict:
    """Jury-format gold (or a conventions example) → BIO row, using build_dataset's span resolver."""
    from training.build_dataset import _spans_for_record
    from training.validate_labels import to_bio

    fields = g.get("expected") or {f: g.get(f) for f in LABEL_FIELDS}
    raw = g["raw_address"]
    spans, _ = _spans_for_record(raw, fields)
    toks = tokenize(clean(raw))
    labels = to_bio(raw, spans, [(t.text, t.start, t.end) for t in toks])
    return {"id": g.get("id"), "raw": raw, "tokens": [{"text": t.text, "start": t.start, "end": t.end} for t in toks],
            "labels": labels, "city": "gold", "fields": fields}


def row_tokens(row: dict) -> list[Token]:
    """Re-tokenize from the raw string (same tokenizer as inference) and check it matches the stored tokens."""
    toks = tokenize(clean(row["raw"]))
    if len(toks) != len(row["tokens"]) or any(t.start != s["start"] or t.end != s["end"] for t, s in zip(toks, row["tokens"])):
        raise ValueError(f"token mismatch: {row['raw']!r}")
    return toks


def build_vocab(rows: list[dict], min_word: int = 2) -> tuple[dict, dict]:
    cc, wc = Counter(), Counter()
    for r in rows:
        for t in r["tokens"]:
            cc.update(t["text"].lower()[:MAX_CHARS])
            wc[norm_token(t["text"])] += 1
    chars = {"<pad>": 0, "<unk>": 1}
    for ch, n in cc.most_common():
        if n >= 2:
            chars[ch] = len(chars)
    words = {"<pad>": 0, "<unk>": 1}
    for w, n in wc.most_common(60000):
        if n >= min_word:
            words[w] = len(words)
    return chars, words


def featurize_rows(rows: list[dict], fz: Featurizer, label2id: dict) -> list[dict]:
    out = []
    bad = 0
    for r in rows:
        try:
            toks = row_tokens(r)
        except ValueError:
            bad += 1
            continue
        if not toks:
            continue
        x = fz.encode(toks)
        x["tags"] = np.array([label2id.get(l, 0) for l in r["labels"]], dtype=np.int64)
        x["real"] = r.get("city") not in ("synthetic",)
        x["row"] = r
        out.append(x)
    if bad:
        print(f"  skipped {bad} rows with token mismatch", file=sys.stderr)
    return out


# --------------------------------------------------------------------------- model
def make_model(n_chars, n_words, n_labels, cfg):
    import torch
    from torch import nn

    class CharCrfTagger(nn.Module):
        def __init__(self):
            super().__init__()
            self.char_emb = nn.Embedding(n_chars, cfg["char_dim"], padding_idx=0)
            self.char_cnn = nn.Conv1d(cfg["char_dim"], cfg["char_out"], kernel_size=3, padding=1)
            self.word_emb = nn.Embedding(n_words, cfg["word_dim"], padding_idx=0)
            self.shape_emb = nn.Embedding(len(SHAPES), cfg["shape_dim"], padding_idx=0)
            self.feat_proj = nn.Linear(len(FEATURES), cfg["feat_dim"])
            d = cfg["char_out"] + cfg["word_dim"] + cfg["shape_dim"] + cfg["feat_dim"]
            self.lstm = nn.LSTM(d, cfg["hidden"], num_layers=cfg["layers"], bidirectional=True, batch_first=True,
                                dropout=cfg["dropout"] if cfg["layers"] > 1 else 0.0)
            self.drop = nn.Dropout(cfg["dropout"])
            self.out = nn.Linear(2 * cfg["hidden"], n_labels)
            self.trans = nn.Parameter(torch.zeros(n_labels, n_labels))
            self.start = nn.Parameter(torch.zeros(n_labels))
            self.end = nn.Parameter(torch.zeros(n_labels))

        def emissions(self, chars, words, shapes, feats, lengths=None):
            B, T, C = chars.shape
            ce = self.char_emb(chars.reshape(B * T, C)).transpose(1, 2)
            ch = torch.relu(self.char_cnn(ce)).max(dim=2).values.reshape(B, T, -1)
            x = torch.cat([ch, self.word_emb(words), self.shape_emb(shapes), torch.relu(self.feat_proj(feats))], dim=-1)
            x = self.drop(x)
            if lengths is not None:
                packed = nn.utils.rnn.pack_padded_sequence(x, lengths.cpu(), batch_first=True, enforce_sorted=False)
                h, _ = self.lstm(packed)
                h, _ = nn.utils.rnn.pad_packed_sequence(h, batch_first=True, total_length=T)
            else:
                h, _ = self.lstm(x)
            return self.out(self.drop(h))

        def forward(self, chars, words, shapes, feats):          # export path: one unpadded sequence
            return self.emissions(chars, words, shapes, feats)

    return CharCrfTagger()


def crf_nll(em, tags, mask, trans, start, end, allowed, allowed_start):
    import torch
    NEG = -1e4
    tr = torch.where(allowed, trans, torch.full_like(trans, NEG))
    st = torch.where(allowed_start, start, torch.full_like(start, NEG))
    B, T, L = em.shape
    maskf = mask.float()
    score = st[tags[:, 0]] + em[:, 0].gather(1, tags[:, :1]).squeeze(1)
    for t in range(1, T):
        s_t = tr[tags[:, t - 1], tags[:, t]] + em[:, t].gather(1, tags[:, t:t + 1]).squeeze(1)
        score = score + s_t * maskf[:, t]
    last_idx = mask.sum(1) - 1
    last = tags.gather(1, last_idx.unsqueeze(1)).squeeze(1)
    score = score + end[last]
    alpha = st.unsqueeze(0) + em[:, 0]
    for t in range(1, T):
        nxt = torch.logsumexp(alpha.unsqueeze(2) + tr.unsqueeze(0), dim=1) + em[:, t]
        alpha = torch.where(mask[:, t:t + 1], nxt, alpha)
    logz = torch.logsumexp(alpha + end.unsqueeze(0), dim=1)
    return (logz - score).mean()


def collate(batch: list[dict], train: bool, rnd: random.Random, word_drop: float, gaz_drop: float):
    import torch
    B = len(batch)
    T = max(len(x["words"]) for x in batch)
    chars = np.zeros((B, T, MAX_CHARS), dtype=np.int64)
    words = np.zeros((B, T), dtype=np.int64)
    shapes = np.zeros((B, T), dtype=np.int64)
    feats = np.zeros((B, T, len(FEATURES)), dtype=np.float32)
    tags = np.zeros((B, T), dtype=np.int64)
    mask = np.zeros((B, T), dtype=bool)
    lengths = np.zeros(B, dtype=np.int64)
    for i, x in enumerate(batch):
        n = len(x["words"])
        chars[i, :n] = x["chars"]
        w = x["words"].copy()
        f = x["feats"].copy()
        if train:
            w[np.array([rnd.random() < word_drop for _ in range(n)])] = 1
            if rnd.random() < gaz_drop:
                f[:, GAZ_SLICE] = 0.0
            else:
                drop = np.array([rnd.random() < gaz_drop / 3 for _ in range(n)])
                f[drop, GAZ_SLICE] = 0.0
        words[i, :n] = w
        shapes[i, :n] = x["shapes"]
        feats[i, :n] = f
        tags[i, :n] = x["tags"]
        mask[i, :n] = True
        lengths[i] = n
    return tuple(torch.from_numpy(a) for a in (chars, words, shapes, feats, tags, mask, lengths))


# --------------------------------------------------------------------------- evaluation
def spans_of(labels: list[str]) -> set[tuple[str, int, int]]:
    out, cur = set(), None
    for i, l in enumerate(labels + ["O"]):
        if cur and not (l.startswith("I-") and l[2:] == cur[0]):
            out.add((cur[0], cur[1], i))
            cur = None
        if l.startswith("B-") or (l.startswith("I-") and cur is None):
            cur = (l[2:], i)
    return out


def evaluate(model, data: list[dict], labels: list[str], device, bs: int = 256) -> dict:
    import torch
    from jana_address.ml.charcrf import viterbi
    model.eval()
    allowed, allowed_start = bio_mask(labels)
    trans = model.trans.detach().cpu().numpy().astype(np.float64)
    start = model.start.detach().cpu().numpy().astype(np.float64)
    end = model.end.detach().cpu().numpy().astype(np.float64)
    tp, fp, fn = Counter(), Counter(), Counter()
    seq_ok = 0
    with torch.no_grad():
        for i in range(0, len(data), bs):
            batch = data[i:i + bs]
            chars, words, shapes, feats, tags, mask, lengths = collate(batch, False, None, 0, 0)
            em = model.emissions(chars.to(device), words.to(device), shapes.to(device), feats.to(device), lengths).cpu().numpy()
            for b, x in enumerate(batch):
                n = len(x["words"])
                pred = [labels[j] for j in viterbi(em[b, :n].astype(np.float64), trans, start, end, allowed, allowed_start)]
                gold = [labels[j] for j in x["tags"]]
                ps, gs = spans_of(pred), spans_of(gold)
                seq_ok += int(ps == gs)
                for s in ps & gs:
                    tp[s[0]] += 1
                for s in ps - gs:
                    fp[s[0]] += 1
                for s in gs - ps:
                    fn[s[0]] += 1
    T, P, N = sum(tp.values()), sum(fp.values()), sum(fn.values())
    f1 = 2 * T / max(1, 2 * T + P + N)
    per = {f: round(2 * tp[f] / max(1, 2 * tp[f] + fp[f] + fn[f]), 4) for f in sorted(set(tp) | set(fn) | set(fp))}
    model.train()
    return {"span_f1": round(f1, 4), "seq_acc": round(seq_ok / max(1, len(data)), 4), "per_field": per, "n": len(data)}


# --------------------------------------------------------------------------- export
def export(model, meta: dict, out_dir: Path) -> Path:
    import torch
    out_dir.mkdir(parents=True, exist_ok=True)
    model.eval().cpu()
    T = 7
    dummy = (torch.zeros(1, T, MAX_CHARS, dtype=torch.int64) + 2, torch.ones(1, T, dtype=torch.int64),
             torch.ones(1, T, dtype=torch.int64), torch.zeros(1, T, len(FEATURES), dtype=torch.float32))
    path = out_dir / "model.onnx"
    names = ["chars", "words", "shapes", "feats"]
    dyn = {"chars": {1: "T"}, "words": {1: "T"}, "shapes": {1: "T"}, "feats": {1: "T"}, "emissions": {1: "T"}}
    try:
        torch.onnx.export(model, dummy, str(path), input_names=names, output_names=["emissions"], dynamic_axes=dyn,
                          opset_version=17, dynamo=False)
    except TypeError:
        torch.onnx.export(model, dummy, str(path), input_names=names, output_names=["emissions"], dynamic_axes=dyn,
                          opset_version=17)
    meta = dict(meta)
    meta["crf"] = {"transitions": model.trans.detach().numpy().round(5).tolist(),
                   "start": model.start.detach().numpy().round(5).tolist(),
                   "end": model.end.detach().numpy().round(5).tolist()}
    (out_dir / "model_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (out_dir / "labels.json").write_text(json.dumps(meta["labels"], ensure_ascii=False, indent=1), encoding="utf-8")
    # an HF tokenizer.json from an older XLM-R export would make the runtime pick the wrong model kind
    stale = out_dir / "tokenizer.json"
    if stale.exists():
        stale.rename(out_dir / "tokenizer.json.xlmr-disabled")
    return path


def check_export(model, data: list[dict], out_dir: Path, n: int = 300) -> float:
    """ONNX emissions == PyTorch emissions on n rows (max abs diff)."""
    import onnxruntime as ort
    import torch
    sess = ort.InferenceSession(str(out_dir / "model.onnx"), providers=["CPUExecutionProvider"])
    worst = 0.0
    model.eval()
    with torch.no_grad():
        for x in data[:n]:
            feeds = {"chars": x["chars"][None], "words": x["words"][None], "shapes": x["shapes"][None], "feats": x["feats"][None]}
            on = sess.run(None, feeds)[0]
            pt = model(*(torch.from_numpy(v) for v in feeds.values())).numpy()
            worst = max(worst, float(np.abs(on - pt).max()))
    return worst


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", action="append", required=True)
    ap.add_argument("--dev", required=True, help="model selection set (held-out synthetic or real dev)")
    ap.add_argument("--test", action="append", default=[], help="reported every epoch, never used for selection")
    ap.add_argument("--out", default="runs/charcrf")
    ap.add_argument("--export", help="write model.onnx + model_meta.json here (e.g. api/artifacts)")
    ap.add_argument("--gazetteer", default=str(ROOT / "api/artifacts/gazetteer.sqlite"))
    ap.add_argument("--init", help="warm start from a previous run's model.pt")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--real-weight", type=int, default=5)
    ap.add_argument("--max-train", type=int, default=0, help="cap the number of synthetic rows (0 = all)")
    ap.add_argument("--word-drop", type=float, default=0.12)
    ap.add_argument("--gaz-drop", type=float, default=0.3)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args(argv)

    import torch
    torch.manual_seed(a.seed)
    torch.set_num_threads(a.threads)
    rnd = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    train_rows = [r for p in a.train for r in load_rows(p)]
    synth = [r for r in train_rows if r.get("city") == "synthetic"]
    real = [r for r in train_rows if r.get("city") != "synthetic"]
    if a.max_train and len(synth) > a.max_train:
        rnd.shuffle(synth)
        synth = synth[: a.max_train]
    dev_rows = load_rows(a.dev)
    tests = {Path(p).stem: load_rows(p) for p in a.test}
    print(f"train: {len(synth)} synthetic + {len(real)} real (×{a.real_weight}) | dev {len(dev_rows)} | "
          + " ".join(f"{k} {len(v)}" for k, v in tests.items()), flush=True)

    cfg = {"char_dim": 32, "char_out": 96, "word_dim": 64, "shape_dim": 16, "feat_dim": 48,
           "hidden": a.hidden, "layers": a.layers, "dropout": 0.25}
    if a.init:
        ck = torch.load(a.init, map_location="cpu", weights_only=False)
        chars, words, gsets, cfg = ck["meta"]["chars"], ck["meta"]["words"], ck["meta"]["gazetteer_sets"], ck["meta"]["config"]
    else:
        chars, words = build_vocab(synth + real)
        gsets = build_gazetteer_sets(a.gazetteer)
    meta = {"kind": "charcrf", "version": 1, "labels": LABELS, "chars": chars, "words": words, "gazetteer_sets": gsets,
            "config": cfg, "features": FEATURES, "shapes": SHAPES, "max_chars": MAX_CHARS}
    fz = Featurizer(meta)
    label2id = {l: i for i, l in enumerate(LABELS)}
    tr = featurize_rows(synth, fz, label2id) + featurize_rows(real, fz, label2id) * a.real_weight
    dv = featurize_rows(dev_rows, fz, label2id)
    te = {k: featurize_rows(v, fz, label2id) for k, v in tests.items()}
    print(f"featurized in {time.time() - t0:.0f}s; vocab chars={len(chars)} words={len(words)}", flush=True)

    device = torch.device(a.device)
    model = make_model(len(chars), len(words), len(LABELS), cfg).to(device)
    if a.init:
        model.load_state_dict(ck["state"])
    allowed_np, allowed_start_np = bio_mask(LABELS)
    allowed = torch.from_numpy(allowed_np).to(device)
    allowed_start = torch.from_numpy(allowed_start_np).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps_per_epoch = math.ceil(len(tr) / a.bs)
    total = steps_per_epoch * a.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=total, pct_start=0.08)
    print(f"params: {sum(p.numel() for p in model.parameters()) / 1e6:.2f}M; {steps_per_epoch} steps/epoch", flush=True)

    best, history = -1.0, []
    for ep in range(1, a.epochs + 1):
        model.train()
        # length-bucketed shuffling: similar lengths per batch → less padding
        idx = sorted(range(len(tr)), key=lambda i: len(tr[i]["words"]) + rnd.random() * 6)
        batches = [idx[i:i + a.bs] for i in range(0, len(idx), a.bs)]
        rnd.shuffle(batches)
        tl, te0 = 0.0, time.time()
        for bi, b in enumerate(batches):
            chars_b, words_b, shapes_b, feats_b, tags_b, mask_b, len_b = collate([tr[i] for i in b], True, rnd, a.word_drop, a.gaz_drop)
            em = model.emissions(chars_b.to(device), words_b.to(device), shapes_b.to(device), feats_b.to(device), len_b)
            loss = crf_nll(em, tags_b.to(device), mask_b.to(device), model.trans, model.start, model.end, allowed, allowed_start)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            sched.step()
            tl += float(loss)
        dres = evaluate(model, dv, LABELS, device)
        tres = {k: evaluate(model, v, LABELS, device) for k, v in te.items()}
        rec = {"epoch": ep, "loss": round(tl / len(batches), 4), "dev": dres, "tests": tres, "sec": round(time.time() - te0)}
        history.append(rec)
        print(f"ep {ep} loss {rec['loss']:.4f} dev F1 {dres['span_f1']:.4f} seq {dres['seq_acc']:.4f} | "
              + " | ".join(f"{k} F1 {v['span_f1']:.4f} seq {v['seq_acc']:.4f}" for k, v in tres.items())
              + f" | {rec['sec']}s", flush=True)
        if dres["span_f1"] > best:
            best = dres["span_f1"]
            torch.save({"state": model.state_dict(), "meta": meta, "epoch": ep}, out / "model.pt")
    (out / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")

    ck = torch.load(out / "model.pt", map_location="cpu", weights_only=False)
    model.load_state_dict(ck["state"])
    final = {"best_epoch": ck["epoch"], "dev": evaluate(model, dv, LABELS, torch.device("cpu")),
             "tests": {k: evaluate(model, v, LABELS, torch.device("cpu")) for k, v in te.items()},
             "train_rows": {"synthetic": len(synth), "real": len(real)}}
    meta["metrics"] = final
    print(json.dumps(final, ensure_ascii=False, indent=1))
    if a.export:
        path = export(model.cpu(), meta, Path(a.export))
        diff = check_export(model, dv, Path(a.export))
        print(f"→ {path} ({path.stat().st_size / 1e6:.1f} MB) + model_meta.json; ONNX vs PyTorch max |Δemission| = {diff:.2e} "
              f"({'OK' if diff < 1e-3 else 'INVESTIGATE'})")
    print(f"total {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
