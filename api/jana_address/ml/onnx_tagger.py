"""ONNX token tagger — the learned half of the parser.

Contract (training/export_onnx.py writes these into api/artifacts/):
    model.onnx       inputs  input_ids[int64, B×L], attention_mask[int64, B×L]
                     output  logits[float32, B×L×C]
    tokenizer.json   HuggingFace fast tokenizer (xlm-roberta-base or whatever was fine-tuned)
    labels.json      ["O", "B-PROVINCE", "I-PROVINCE", ...]   (index = class id)

The tagger consumes OUR tokens (jana_address.tokenizer) so that model spans map 1:1 onto raw
character offsets: encode(tokens, is_pretokenized=True) → first-subword label per token →
BIO merge → spans. If the artifacts are missing the tagger reports `available() == False`
and the service runs on rules only — nothing else changes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..tokenizer import Token

ARTIFACTS = Path(os.environ.get("JANA_ARTIFACTS_DIR", Path(__file__).resolve().parents[2] / "artifacts"))
MAX_LEN = 128


@dataclass
class ModelSpan:
    field: str            # PROVINCE, CITY, ... (without B-/I-)
    start: int
    end: int
    prob: float           # min token probability inside the span
    mean_prob: float
    token_idxs: list[int]


def spans_from_token_labels(tokens: list[Token], labels: list[str], probs: list[float]) -> list[ModelSpan]:
    """Merge BIO labels over our tokens into character spans. Tolerates I- without B- (starts a span)."""
    spans: list[ModelSpan] = []
    cur: ModelSpan | None = None
    for i, (t, lab, p) in enumerate(zip(tokens, labels, probs)):
        if lab == "O" or not lab:
            if cur:
                spans.append(cur)
                cur = None
            continue
        prefix, field = (lab.split("-", 1) + [""])[:2] if "-" in lab else ("B", lab)
        if cur and prefix == "I" and cur.field == field:
            cur.end = t.end
            cur.prob = min(cur.prob, p)
            cur.mean_prob = (cur.mean_prob * len(cur.token_idxs) + p) / (len(cur.token_idxs) + 1)
            cur.token_idxs.append(i)
            continue
        if cur:
            spans.append(cur)
        cur = ModelSpan(field, t.start, t.end, p, p, [i])
    if cur:
        spans.append(cur)
    return spans


class OnnxTagger:
    def __init__(self, artifacts: Path = ARTIFACTS):
        self.dir = Path(artifacts)
        self.session = None
        self.tokenizer = None
        self.labels: list[str] = []
        self._load()

    def _load(self) -> None:
        model, tok, labels = self.dir / "model.onnx", self.dir / "tokenizer.json", self.dir / "labels.json"
        if os.environ.get("JANA_MODEL", "auto").lower() == "off" or not (model.exists() and tok.exists() and labels.exists()):
            return
        import onnxruntime as ort                    # heavy imports only when the artifacts exist
        from tokenizers import Tokenizer

        self.labels = json.loads(labels.read_text(encoding="utf-8"))
        self.tokenizer = Tokenizer.from_file(str(tok))
        self.tokenizer.enable_truncation(MAX_LEN)
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("JANA_ORT_THREADS", "2"))
        self.session = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}

    def available(self) -> bool:
        return self.session is not None

    # ------------------------------------------------------------------ inference
    def tag(self, tokens: list[Token]) -> tuple[list[ModelSpan], list[str], list[float]]:
        """Return (spans, per-token labels, per-token probabilities) for one address."""
        if not tokens or not self.available():
            return [], [], []
        enc = self.tokenizer.encode([t.text for t in tokens], is_pretokenized=True)
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        feeds = {k: v for k, v in feeds.items() if k in self.input_names}
        logits = self.session.run(None, feeds)[0][0]                  # L × C
        logits = logits - logits.max(axis=-1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=-1, keepdims=True)
        # first sub-word of each of OUR tokens carries the label
        word_ids = enc.word_ids
        tok_label = ["O"] * len(tokens)
        tok_prob = [0.0] * len(tokens)
        seen: set[int] = set()
        for pos, w in enumerate(word_ids):
            if w is None or w in seen or w >= len(tokens):
                continue
            seen.add(w)
            c = int(probs[pos].argmax())
            tok_label[w] = self.labels[c]
            tok_prob[w] = float(probs[pos][c])
        return spans_from_token_labels(tokens, tok_label, tok_prob), tok_label, tok_prob

    def tag_batch(self, batch: list[list[Token]]) -> list[tuple[list[ModelSpan], list[str], list[float]]]:
        """Batched variant (padding) — use for throughput; semantics identical to tag()."""
        if not batch or not self.available():
            return [([], [], []) for _ in batch]
        encs = self.tokenizer.encode_batch([[t.text for t in toks] for toks in batch], is_pretokenized=True)
        L = max(len(e.ids) for e in encs)
        pad_id = self.tokenizer.token_to_id("<pad>") or 0
        ids = np.full((len(encs), L), pad_id, dtype=np.int64)
        mask = np.zeros((len(encs), L), dtype=np.int64)
        for i, e in enumerate(encs):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.ids)] = 1
        feeds = {k: v for k, v in {"input_ids": ids, "attention_mask": mask}.items() if k in self.input_names}
        logits = self.session.run(None, feeds)[0]
        logits = logits - logits.max(axis=-1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=-1, keepdims=True)
        out = []
        for i, (e, toks) in enumerate(zip(encs, batch)):
            tok_label = ["O"] * len(toks)
            tok_prob = [0.0] * len(toks)
            seen: set[int] = set()
            for pos, w in enumerate(e.word_ids):
                if w is None or w in seen or w >= len(toks):
                    continue
                seen.add(w)
                c = int(probs[i, pos].argmax())
                tok_label[w] = self.labels[c]
                tok_prob[w] = float(probs[i, pos][c])
            out.append((spans_from_token_labels(toks, tok_label, tok_prob), tok_label, tok_prob))
        return out


_TAGGER: OnnxTagger | None = None


def get_tagger() -> OnnxTagger:
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = OnnxTagger()
    return _TAGGER
