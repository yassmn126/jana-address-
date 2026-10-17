"""Char-CNN + BiLSTM + CRF token tagger — runtime half (numpy + onnxruntime only, no torch).

Why this architecture (and not only XLM-R): it trains from scratch in minutes on a laptop CPU, needs no
pretrained download, exports to a ~10 MB ONNX file and tags a record in well under a millisecond. It is the
libpostal recipe (a CRF trained on addresses generated from OpenStreetMap) with a neural encoder.
`training/train.py` still fine-tunes XLM-R on a GPU; the runtime picks whichever artifact is present.

Inputs per token (identical at training and inference — training imports THIS module):
  chars   lower-cased characters, first MAX_CHARS                        → char-CNN
  word    lower-cased token id (vocabulary from training, UNK otherwise)  → embedding
  shape   coarse token shape (number, number+letter, Capitalized word, …, each punctuation mark)
  feats   multi-hot: lexicon roles (street type, house/apartment/entrance/floor keyword, city keyword, …),
          gazetteer membership (city, village, city district, микрорайон/ЖК, province word),
          numeric facts (length, 6-digit postcode), context (first/last, after a comma, space before)
Decoding: Viterbi over emissions + learned transitions with hard BIO constraints; per-token marginal
probabilities (forward–backward) feed the arbiter and the confidence model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from ..preprocess import match_view, norm_token
from ..tokenizer import Token

MAX_CHARS = 16
PUNCT_SHAPES = [",", ".", "/", "-", "№", "(", ")", '"', "«", "»", ";", ":", "'", "#", "+"]
SHAPES = ["PAD", "NUM", "ALNUM", "CAP", "LOWER", "UPPER", "MIXED", "LATIN_CAP", "LATIN_LOWER", "LATIN_UPPER",
          "HYPHEN_WORD", "SHORT"] + [f"P{p}" for p in PUNCT_SHAPES] + ["P_OTHER"]
ROLE_FEATURES = ["street_type", "street_type_micro", "city_kw", "city_suffix_kk", "province_kw", "district_kw",
                 "living_kw", "house_kw", "building_kw", "apartment_kw", "entrance_kw", "floor_kw", "ambiguous_p",
                 "landmark_kw", "landmark_marker_only", "noise", "ordinal"]
GAZ_FEATURES = ["gz_city", "gz_village", "gz_district", "gz_living", "gz_province", "gz_city_kk_suffix_next"]
NUM_FEATURES = ["num_len1", "num_len2", "num_len3", "num_len4", "num_len5", "num_len6p", "has_digit", "has_letter",
                "has_kk_letter", "has_latin"]
CTX_FEATURES = ["first", "last", "after_sep", "before_sep", "space_before", "glued_prev", "glued_next",
                "prev_is_num", "next_is_num", "prev_is_slash", "next_is_slash"]
FEATURES = ROLE_FEATURES + GAZ_FEATURES + NUM_FEATURES + CTX_FEATURES
_LAT = re.compile(r"[A-Za-z]")
_KK = re.compile(r"[ӘәҒғҚқҢңӨөҰұҮүҺһІі]")
GAZ_SLICE = slice(len(ROLE_FEATURES), len(ROLE_FEATURES) + len(GAZ_FEATURES))


def gaz_key(text: str) -> str:
    """Gazetteer key of a token for the membership features (Kazakh letters folded, ordinal suffix kept)."""
    from ..gazetteer.names import norm
    return norm(text)


def shape_of(t: Token) -> str:
    s = t.text
    if t.kind == "NUM":
        return "NUM"
    if t.kind == "ALNUM":
        return "ALNUM"
    if t.kind == "PUNCT":
        return f"P{s}" if s in PUNCT_SHAPES else "P_OTHER"
    if "-" in s:
        return "HYPHEN_WORD"
    if len(s) <= 2:
        return "SHORT"
    latin = bool(_LAT.match(s))
    if s.isupper():
        return "LATIN_UPPER" if latin else "UPPER"
    if s[0].isupper() and (len(s) == 1 or s[1:].islower()):
        return "LATIN_CAP" if latin else "CAP"
    if s.islower():
        return "LATIN_LOWER" if latin else "LOWER"
    return "MIXED"


class Featurizer:
    """Turns OUR tokens into model inputs. `meta` is written by training/train_charcrf.py into model_meta.json."""

    def __init__(self, meta: dict, lexicon=None):
        from ..rules.grammar import get_lexicon

        self.lex = lexicon or get_lexicon()
        self.char2id: dict[str, int] = meta["chars"]
        self.word2id: dict[str, int] = meta["words"]
        self.shape2id = {s: i for i, s in enumerate(SHAPES)}
        self.sets = {k: set(v) for k, v in meta["gazetteer_sets"].items()}
        self.micro_lemmas = set(self.lex.microdistrict_like)

    # ---- per-token pieces
    def char_ids(self, text: str) -> list[int]:
        low = text.lower()[:MAX_CHARS]
        return [self.char2id.get(ch, 1) for ch in low] + [0] * (MAX_CHARS - len(low))

    def word_id(self, text: str) -> int:
        return self.word2id.get(norm_token(text), 1)

    def roles(self, toks: list[Token], i: int) -> set[str]:
        t = toks[i]
        out: set[str] = set()
        if t.kind != "WORD":
            return out
        r = self.lex.roles_of(t)
        out |= {x for x in r if x in ROLE_FEATURES}
        lemma = self.lex.street_type(t)
        if lemma:
            out.add("street_type")
            if lemma in self.micro_lemmas:
                out.add("street_type_micro")
        nt = norm_token(t.text)
        if i + 1 < len(toks) and toks[i + 1].kind == "WORD":
            second = (self.lex.bigrams or {}).get(nt)
            if second and norm_token(toks[i + 1].text) in second:
                out.add("street_type" if second[norm_token(toks[i + 1].text)] != "ЖК" else "living_kw")
        if i > 0 and toks[i - 1].kind == "WORD":
            second = (self.lex.bigrams or {}).get(norm_token(toks[i - 1].text))
            if second and nt in second:
                out.add("street_type" if second[nt] != "ЖК" else "living_kw")
        if nt in self.lex.ordinal_suffix and i > 0 and (toks[i - 1].kind == "NUM" or (
                toks[i - 1].text == "-" and i > 1 and toks[i - 2].kind == "NUM")):
            out.add("ordinal")
        return out

    def gaz(self, toks: list[Token], i: int) -> set[str]:
        t = toks[i]
        out: set[str] = set()
        if t.kind not in ("WORD", "ALNUM"):
            return out
        k = gaz_key(t.text)
        # unigram and the bigram that starts or ends here («Отеген батыр», «Казыбек би»)
        keys = {k}
        if i + 1 < len(toks) and toks[i + 1].kind == "WORD":
            keys.add(k + " " + gaz_key(toks[i + 1].text))
        if i > 0 and toks[i - 1].kind == "WORD":
            keys.add(gaz_key(toks[i - 1].text) + " " + k)
        for name, s in (("gz_city", self.sets["city"]), ("gz_village", self.sets["village"]),
                        ("gz_district", self.sets["district"]), ("gz_living", self.sets["living"]),
                        ("gz_province", self.sets["province"])):
            if keys & s:
                out.add(name)
        if i + 1 < len(toks) and norm_token(toks[i + 1].text) in ("қаласы", "каласы", "ауылы", "кенті"):
            out.add("gz_city_kk_suffix_next")
        return out

    def feats(self, toks: list[Token], i: int) -> np.ndarray:
        t = toks[i]
        on = self.roles(toks, i) | self.gaz(toks, i)
        if t.kind in ("NUM", "ALNUM"):
            digits = len(re.sub(r"\D", "", t.text))
            on.add(f"num_len{digits}" if digits < 6 else "num_len6p")
        if any(ch.isdigit() for ch in t.text):
            on.add("has_digit")
        if any(ch.isalpha() for ch in t.text):
            on.add("has_letter")
        if _KK.search(t.text):
            on.add("has_kk_letter")
        if _LAT.search(t.text):
            on.add("has_latin")
        n = len(toks)
        if i == 0:
            on.add("first")
        if i == n - 1:
            on.add("last")
        if i > 0 and toks[i - 1].is_sep:
            on.add("after_sep")
        if i + 1 < n and toks[i + 1].is_sep:
            on.add("before_sep")
        if t.space_before:
            on.add("space_before")
        if i > 0 and not t.space_before and toks[i - 1].kind != "PUNCT" and t.kind != "PUNCT":
            on.add("glued_prev")
        if i + 1 < n and not toks[i + 1].space_before and toks[i + 1].kind != "PUNCT" and t.kind != "PUNCT":
            on.add("glued_next")
        if i > 0 and toks[i - 1].is_number_like:
            on.add("prev_is_num")
        if i + 1 < n and toks[i + 1].is_number_like:
            on.add("next_is_num")
        if i > 0 and toks[i - 1].is_slash:
            on.add("prev_is_slash")
        if i + 1 < n and toks[i + 1].is_slash:
            on.add("next_is_slash")
        v = np.zeros(len(FEATURES), dtype=np.float32)
        for j, f in enumerate(FEATURES):
            if f in on:
                v[j] = 1.0
        return v

    def encode(self, toks: list[Token]) -> dict[str, np.ndarray]:
        T = len(toks)
        chars = np.array([self.char_ids(t.text) for t in toks], dtype=np.int64).reshape(T, MAX_CHARS)
        words = np.array([self.word_id(t.text) for t in toks], dtype=np.int64)
        shapes = np.array([self.shape2id[shape_of(t)] for t in toks], dtype=np.int64)
        feats = np.stack([self.feats(toks, i) for i in range(T)]) if T else np.zeros((0, len(FEATURES)), np.float32)
        return {"chars": chars, "words": words, "shapes": shapes, "feats": feats}


# --------------------------------------------------------------------------- CRF decoding (numpy)
def bio_mask(labels: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """(allowed transitions L×L, allowed start L) for well-formed BIO."""
    L = len(labels)
    trans = np.ones((L, L), dtype=bool)
    start = np.ones(L, dtype=bool)
    for j, lj in enumerate(labels):
        if lj.startswith("I-"):
            start[j] = False
            for i, li in enumerate(labels):
                if li[2:] != lj[2:] or li == "O":
                    trans[i, j] = False
    return trans, start


def viterbi(em: np.ndarray, trans: np.ndarray, start: np.ndarray, end: np.ndarray,
            allowed: np.ndarray, allowed_start: np.ndarray) -> list[int]:
    T, L = em.shape
    NEG = -1e4
    tr = np.where(allowed, trans, NEG)
    score = em[0] + np.where(allowed_start, start, NEG)
    back = np.zeros((T, L), dtype=np.int64)
    for t in range(1, T):
        cand = score[:, None] + tr                      # L(prev) × L(cur)
        back[t] = cand.argmax(0)
        score = cand.max(0) + em[t]
    score = score + end
    best = [int(score.argmax())]
    for t in range(T - 1, 0, -1):
        best.append(int(back[t, best[-1]]))
    return best[::-1]


def marginals(em: np.ndarray, trans: np.ndarray, start: np.ndarray, end: np.ndarray,
              allowed: np.ndarray, allowed_start: np.ndarray) -> np.ndarray:
    """Per-token posterior P(label_t = j | x) by forward–backward in log space."""
    T, L = em.shape
    NEG = -1e4
    tr = np.where(allowed, trans, NEG)

    def lse(a, axis):
        m = a.max(axis=axis, keepdims=True)
        return (m + np.log(np.exp(a - m).sum(axis=axis, keepdims=True))).squeeze(axis)

    alpha = np.zeros((T, L))
    alpha[0] = em[0] + np.where(allowed_start, start, NEG)
    for t in range(1, T):
        alpha[t] = lse(alpha[t - 1][:, None] + tr, 0) + em[t]
    beta = np.zeros((T, L))
    beta[T - 1] = end
    for t in range(T - 2, -1, -1):
        beta[t] = lse(tr + (em[t + 1] + beta[t + 1])[None, :], 1)
    logz = lse(alpha[T - 1] + end, 0)
    return np.exp(alpha + beta - logz)


class CharCrfRuntime:
    """Loads model.onnx + model_meta.json (kind=charcrf); `tag(tokens)` → (labels, probs)."""

    def __init__(self, model_path: Path, meta_path: Path, threads: int = 2):
        import onnxruntime as ort

        self.meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        self.labels: list[str] = self.meta["labels"]
        self.featurizer = Featurizer(self.meta)
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        crf = self.meta["crf"]
        self.trans = np.array(crf["transitions"], dtype=np.float64)
        self.start = np.array(crf["start"], dtype=np.float64)
        self.end = np.array(crf["end"], dtype=np.float64)
        self.allowed, self.allowed_start = bio_mask(self.labels)

    def emissions(self, toks: list[Token]) -> np.ndarray:
        x = self.featurizer.encode(toks)
        feeds = {k: v[None, ...] for k, v in x.items()}
        return self.session.run(None, feeds)[0][0].astype(np.float64)

    def tag(self, toks: list[Token]) -> tuple[list[str], list[float]]:
        if not toks:
            return [], []
        em = self.emissions(toks)
        path = viterbi(em, self.trans, self.start, self.end, self.allowed, self.allowed_start)
        marg = marginals(em, self.trans, self.start, self.end, self.allowed, self.allowed_start)
        return [self.labels[j] for j in path], [float(marg[t, j]) for t, j in enumerate(path)]


def build_gazetteer_sets(sqlite_path: str | None, lexicon=None) -> dict[str, list[str]]:
    """Name sets for the membership features (saved INTO model_meta.json, so training == inference)."""
    import sqlite3
    from ..gazetteer.names import base_of_numbered, norm
    from ..rules.grammar import get_lexicon

    lex = lexicon or get_lexicon()
    sets: dict[str, set[str]] = {"city": set(), "village": set(), "district": set(), "living": set(), "province": set()}
    for k, rec in lex.city_by_name.items():
        sets["city"].add(norm(k))
    for n in lex.district_names:
        sets["district"].add(norm(n))
    for city, table in (lex.microdistricts or {}).items():
        for m in table:
            sets["living"].add(norm(m))
    for p in lex.provinces:
        for v in [p["canonical"], p.get("kk") or ""] + list(p["variants"]):
            for w in v.split():
                w = norm(w)
                if w and w not in ("область", "обл", "облысы", "region", "oblast"):
                    sets["province"].add(w)
    if sqlite_path and Path(sqlite_path).exists():
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        for key, place in con.execute("SELECT n.norm, s.place FROM settlement_names n JOIN settlements s ON s.id = n.settlement_id"):
            if key and len(key) >= 3:
                sets["city" if place in ("city", "town") else "village"].add(key)
        for (key,) in con.execute("SELECT norm FROM district_names"):
            if key:
                sets["district"].add(key)
        for (base, stem) in con.execute("SELECT base_norm, stem_norm FROM living_areas"):
            for k in (base, stem):
                if k and len(k) >= 3 and not k.isdigit():
                    sets["living"].add(k)
        con.close()
    sets["village"] -= sets["city"]
    return {k: sorted(v) for k, v in sets.items()}


__all__ = ["Featurizer", "CharCrfRuntime", "FEATURES", "SHAPES", "MAX_CHARS", "GAZ_SLICE", "viterbi", "marginals",
           "bio_mask", "build_gazetteer_sets", "match_view"]
