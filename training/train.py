"""Fine-tune a token classifier (BIO over 11 address fields) on the dataset from build_dataset.py.

    pip install -r training/requirements.txt
    python -m training.train --train data/dataset/train.jsonl [--train data/dataset/synthetic.jsonl] \
        --dev data/dataset/dev.jsonl --labels data/dataset/labels.json \
        --model xlm-roberta-base --out runs/xlmr-base --epochs 5 --lr 3e-5 --bs 32 [--real-weight 3]

GPU: any 16 GB card is enough for xlm-roberta-base (bs 32, max_len 128). Alternatives:
  --model microsoft/mdeberta-v3-base   (often +1–2 F1, slower tokenizer)
  --model cointegrated/rubert-tiny2    (29M params, Russian-only: fast baseline)
Outputs: HF checkpoint in --out (best dev span-F1), metrics.json, and a per-field report.
Then: python -m training.export_onnx --checkpoint runs/xlmr-base --labels data/dataset/labels.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_rows(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rows += [json.loads(l) for l in f if l.strip()]
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", action="append", required=True)
    ap.add_argument("--dev", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--model", default="xlm-roberta-base")
    ap.add_argument("--out", default="runs/xlmr-base")
    ap.add_argument("--epochs", type=float, default=5)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--real-weight", type=int, default=3, help="oversample real (non-synthetic) rows this many times")
    ap.add_argument("--label-smoothing", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)

    import numpy as np
    import torch
    from datasets import Dataset
    from seqeval.metrics import classification_report, f1_score
    from transformers import (AutoModelForTokenClassification, AutoTokenizer, DataCollatorForTokenClassification,
                              Trainer, TrainingArguments, set_seed)

    set_seed(a.seed)
    labels: list[str] = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    label2id = {l: i for i, l in enumerate(labels)}

    train_rows = load_rows(a.train)
    real = [r for r in train_rows if r.get("city") != "synthetic"]
    synth = [r for r in train_rows if r.get("city") == "synthetic"]
    train_rows = real * a.real_weight + synth
    dev_rows = load_rows([a.dev])
    print(f"train: {len(real)} real ×{a.real_weight} + {len(synth)} synthetic = {len(train_rows)}  | dev: {len(dev_rows)}")

    tok = AutoTokenizer.from_pretrained(a.model)

    def encode(batch):
        enc = tok([[t["text"] for t in toks] for toks in batch["tokens"]], is_split_into_words=True,
                  truncation=True, max_length=a.max_len)
        out_labels = []
        for i, labs in enumerate(batch["labels"]):
            word_ids = enc.word_ids(batch_index=i)
            prev = None
            ids = []
            for w in word_ids:
                if w is None:
                    ids.append(-100)
                elif w != prev:
                    ids.append(label2id[labs[w]])          # first sub-word carries the label
                else:
                    ids.append(-100)                        # other sub-words ignored
                prev = w
            out_labels.append(ids)
        enc["labels"] = out_labels
        return enc

    ds_train = Dataset.from_list([{"tokens": r["tokens"], "labels": r["labels"]} for r in train_rows]).map(
        encode, batched=True, remove_columns=["tokens", "labels"])
    ds_dev = Dataset.from_list([{"tokens": r["tokens"], "labels": r["labels"]} for r in dev_rows]).map(
        encode, batched=True, remove_columns=["tokens", "labels"])

    model = AutoModelForTokenClassification.from_pretrained(
        a.model, num_labels=len(labels), id2label=dict(enumerate(labels)), label2id=label2id)

    def compute_metrics(p):
        preds = np.argmax(p.predictions, axis=-1)
        y_true, y_pred = [], []
        for pr, lb in zip(preds, p.label_ids):
            t, q = [], []
            for pi, li in zip(pr, lb):
                if li == -100:
                    continue
                t.append(labels[li])
                q.append(labels[pi])
            y_true.append(t)
            y_pred.append(q)
        return {"span_f1": f1_score(y_true, y_pred)}

    args = TrainingArguments(
        output_dir=a.out, learning_rate=a.lr, per_device_train_batch_size=a.bs, per_device_eval_batch_size=64,
        num_train_epochs=a.epochs, weight_decay=0.01, warmup_ratio=0.06, label_smoothing_factor=a.label_smoothing,
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True, metric_for_best_model="span_f1",
        greater_is_better=True, save_total_limit=2, logging_steps=50, report_to=[], seed=a.seed,
        fp16=torch.cuda.is_available(),
    )
    trainer = Trainer(model=model, args=args, train_dataset=ds_train, eval_dataset=ds_dev,
                      data_collator=DataCollatorForTokenClassification(tok), compute_metrics=compute_metrics)
    trainer.train()
    metrics = trainer.evaluate()
    trainer.save_model(a.out)
    tok.save_pretrained(a.out)

    # per-field report on dev
    pred = trainer.predict(ds_dev)
    preds = np.argmax(pred.predictions, axis=-1)
    y_true, y_pred = [], []
    for pr, lb in zip(preds, pred.label_ids):
        y_true.append([labels[li] for li in lb if li != -100])
        y_pred.append([labels[pi] for pi, li in zip(pr, lb) if li != -100])
    report = classification_report(y_true, y_pred, digits=4)
    print(report)
    Path(a.out, "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    Path(a.out, "report.txt").write_text(report, encoding="utf-8")
    print(json.dumps(metrics, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
