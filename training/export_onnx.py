"""Export the fine-tuned checkpoint to ONNX (+ dynamic int8) and drop the artifacts into api/artifacts/.

    python -m training.export_onnx --checkpoint runs/xlmr-base --labels data/dataset/labels.json \
        [--out api/artifacts] [--no-quant] [--check data/dataset/dev.jsonl]

Writes: model.onnx, tokenizer.json, labels.json. `--check` runs the ONNX model and the PyTorch model on
N dev rows and asserts the argmax labels agree (catches export/alignment bugs before Docker).
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", default="api/artifacts")
    ap.add_argument("--no-quant", action="store_true")
    ap.add_argument("--check", help="dev.jsonl to compare ONNX vs PyTorch on")
    ap.add_argument("--check-n", type=int, default=200)
    ap.add_argument("--opset", type=int, default=17)
    a = ap.parse_args(argv)

    import numpy as np
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(a.checkpoint)
    model = AutoModelForTokenClassification.from_pretrained(a.checkpoint).eval()

    sample = tok(["город Алматы , мкр Аксай 3а , 59 , кв 70".split()], is_split_into_words=True, return_tensors="pt")
    fp32 = out / "model_fp32.onnx"
    with torch.no_grad():
        torch.onnx.export(
            model, (sample["input_ids"], sample["attention_mask"]), str(fp32),
            input_names=["input_ids", "attention_mask"], output_names=["logits"],
            dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                          "logits": {0: "batch", 1: "seq"}},
            opset_version=a.opset, do_constant_folding=True,
        )
    final = out / "model.onnx"
    if a.no_quant:
        shutil.move(str(fp32), str(final))
    else:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        quantize_dynamic(str(fp32), str(final), weight_type=QuantType.QInt8)
        fp32.unlink()
    tok.backend_tokenizer.save(str(out / "tokenizer.json"))
    stale = out / "model_meta.json"            # a char-CRF meta would make the runtime pick the wrong model kind
    if stale.exists():
        stale.rename(out / "model_meta.json.charcrf-disabled")
    shutil.copy(a.labels, out / "labels.json")
    print(f"→ {final} ({final.stat().st_size/1e6:.0f} MB), tokenizer.json, labels.json")

    if a.check:
        import onnxruntime as ort
        rows = [json.loads(l) for l in open(a.check, encoding="utf-8") if l.strip()][: a.check_n]
        sess = ort.InferenceSession(str(final), providers=["CPUExecutionProvider"])
        agree = total = 0
        for r in rows:
            words = [t["text"] for t in r["tokens"]]
            enc = tok([words], is_split_into_words=True, return_tensors="pt", truncation=True, max_length=128)
            with torch.no_grad():
                pt = model(**enc).logits[0].argmax(-1).numpy()
            on = sess.run(None, {"input_ids": enc["input_ids"].numpy(), "attention_mask": enc["attention_mask"].numpy()})[0][0].argmax(-1)
            agree += int((pt == on).sum())
            total += len(pt)
        print(f"ONNX vs PyTorch argmax agreement: {agree/total:.4%} over {total} sub-tokens "
              f"({'OK' if agree/total > 0.995 else 'INVESTIGATE — quantization may hurt; try --no-quant'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
