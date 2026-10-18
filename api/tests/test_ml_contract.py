"""ML contract tests that run WITHOUT a trained model: BIO→span merge, tokenizer alignment with a toy
tokenizer + fake ONNX session, and the arbiter policy."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.assembly.arbiter import arbitrate  # noqa: E402
from jana_address.assembly.canon import assemble  # noqa: E402
from jana_address.ml.onnx_tagger import ModelSpan, OnnxTagger, spans_from_token_labels  # noqa: E402
from jana_address.preprocess import clean  # noqa: E402
from jana_address.rules.grammar import RuleParser  # noqa: E402
from jana_address.tokenizer import tokenize  # noqa: E402

LABELS = ["O", "B-STREET_NAME", "I-STREET_NAME", "B-HOUSE", "I-HOUSE", "B-APARTMENT", "I-APARTMENT"]


def test_spans_from_token_labels_merges_bio_and_tolerates_lone_I():
    toks = tokenize(clean("Толе би 15 кв 3"))
    labels = ["B-STREET_NAME", "I-STREET_NAME", "B-HOUSE", "O", "I-APARTMENT"]
    probs = [0.9, 0.8, 0.95, 0.99, 0.7]
    spans = spans_from_token_labels(toks, labels, probs)
    assert [(s.field, s.start, s.end) for s in spans] == [("STREET_NAME", 0, 7), ("HOUSE", 8, 10), ("APARTMENT", 14, 15)]
    assert spans[0].prob == 0.8 and abs(spans[0].mean_prob - 0.85) < 1e-9


class _FakeSession:
    """Returns logits that label each sub-token by a lookup on its id."""

    def __init__(self, id2label: dict[int, int], n_classes: int):
        self.id2label, self.n = id2label, n_classes

    def get_inputs(self):
        class _I:  # noqa: D401
            def __init__(self, name): self.name = name
        return [_I("input_ids"), _I("attention_mask")]

    def run(self, _, feeds):
        ids = feeds["input_ids"]
        out = np.full((ids.shape[0], ids.shape[1], self.n), -5.0, dtype=np.float32)
        for b in range(ids.shape[0]):
            for p in range(ids.shape[1]):
                out[b, p, self.id2label.get(int(ids[b, p]), 0)] = 5.0
        return [out]


def _toy_tagger(vocab_words: list[str], word_label: dict[str, str]) -> OnnxTagger:
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    vocab = {"<unk>": 0, "<pad>": 1}
    for w in vocab_words:
        vocab.setdefault(w, len(vocab))
    tok = Tokenizer(WordLevel(vocab, unk_token="<unk>"))
    tok.pre_tokenizer = Whitespace()
    t = OnnxTagger.__new__(OnnxTagger)
    t.dir = Path(".")
    t.labels = LABELS
    t.tokenizer = tok
    t.session = _FakeSession({vocab[w]: LABELS.index(l) for w, l in word_label.items()}, len(LABELS))
    t.input_names = {"input_ids", "attention_mask"}
    return t


def test_tagger_alignment_with_fake_model():
    raw = "Кулманова 133, кв 7"
    toks = tokenize(clean(raw))
    tagger = _toy_tagger([t.text for t in toks], {"Кулманова": "B-STREET_NAME", "133": "B-HOUSE", "7": "B-APARTMENT"})
    spans, labels, probs = tagger.tag(toks)
    assert labels == ["B-STREET_NAME", "B-HOUSE", "O", "O", "B-APARTMENT"]
    assert {(s.field, raw[s.start:s.end]) for s in spans} == {("STREET_NAME", "Кулманова"), ("HOUSE", "133"), ("APARTMENT", "7")}
    assert all(p > 0.99 for p in probs if p)
    batched = tagger.tag_batch([toks, toks])
    assert batched[0][1] == labels and batched[1][1] == labels


def _rp(raw: str):
    parser = RuleParser()
    return parser, parser.parse(raw)


def _ms(field, raw, text, prob):
    s = raw.index(text)
    return ModelSpan(field, s, s + len(text), prob, prob, [])


def test_arbiter_agree_and_model_only():
    raw = "Кулманова 133 зеленая крыша"          # «зеленая крыша» has no landmark keyword → rules miss it
    parser, rp = _rp(raw)
    assert "LANDMARK" not in rp.spans
    arbitrate(rp, [_ms("STREET_NAME", raw, "Кулманова", 0.9), _ms("HOUSE", raw, "133", 0.9),
                   _ms("LANDMARK", raw, "зеленая крыша", 0.8)], parser)
    fields, prov = assemble(rp)
    assert fields["street_name"] == "Кулманова" and fields["house"] == "133" and fields["landmark"] == "зеленая крыша"
    assert "arb_street_name_agree" in rp.flags and "arb_landmark_model_only" in rp.flags


def test_arbiter_keyword_bound_rule_beats_unsure_model():
    raw = "Нурсити 2 микрорайон 11дом 5 подьезд 239 кв"
    parser, rp = _rp(raw)
    # model wrongly thinks 239 is the entrance with moderate confidence → keyword-bound rule must win
    arbitrate(rp, [_ms("ENTRANCE", raw, "239", 0.8), _ms("APARTMENT", raw, "5", 0.8)], parser)
    fields, _ = assemble(rp)
    assert fields["entrance"] == "5" and fields["apartment"] == "239"
    assert "arb_entrance_rule_wins" in rp.flags


def test_arbiter_confident_model_overrides_fallback_rule():
    raw = "Костанай, Наримановская 55 кв 4"
    parser, rp = _rp(raw)
    assert rp.spans["STREET_NAME"].rule == "fallback_word_number"
    arbitrate(rp, [_ms("STREET_NAME", raw, "Костанай, Наримановская", 0.95)], parser)
    # the model span swallows the known city at its edge → trimmed to «Наримановская» (= the rule span); the city stays
    fields, _ = assemble(rp)
    assert fields["street_name"] == "Наримановская"
    assert fields["city"] == "Костанай"
    assert "arb_street_name_agree" in rp.flags


def test_arbiter_rejects_invalid_house_from_model():
    raw = "ул. Абая 12а, кв 3"
    parser, rp = _rp(raw)
    arbitrate(rp, [_ms("HOUSE", raw, "Абая 12а", 0.99)], parser)
    fields, _ = assemble(rp)
    assert fields["house"] == "12а"                     # rule kept: model span fails the house pattern
    assert "arb_house_rule_wins" in rp.flags
