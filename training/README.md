# training/ — the Training-Data Factory and the model

`train.json` has no labels. This folder turns it into a verified dataset, a tagger (ONNX) and a
calibrated confidence model. Full step list with checks: `docs/HANDOFF.md` → Track B.

```
train.json ──► label_llm.py ──► labels_llm.jsonl ──► review in the UI ──► corrections.jsonl
                                       │                                          │
                                       └──────────► build_dataset.py ◄────────────┘
                                                        │  train.jsonl · dev.jsonl · dev_gold.json · labels.json
                                       synth.py ◄───────┤
                                          └──► train.py ┴──► export_onnx.py ──► api/artifacts/
                                                             calibrate.py  ──► api/artifacts/calibrator.pkl
```

| script | in → out |
|---|---|
| `label_llm.py` | records → `labels_*.jsonl` (LLM / local vLLM / rules), validated, with disagreements vs rules |
| `validate_labels.py` | pure validators + `resolve_spans` + `to_bio` (shared by everything) |
| `build_dataset.py` | labels + corrections → token/BIO dataset, stratified dev split (frozen ids), jury-format gold |
| `synth.py` | templates mined from verified rows + noise operators (slide-9 difficulties) → synthetic rows |
| `train.py` | HF Trainer, token classification, first-sub-word labels, seqeval span-F1, best checkpoint |
| `export_onnx.py` | checkpoint → `model.onnx` (int8) + `tokenizer.json` + `labels.json`, ONNX≡PyTorch check |
| `calibrate.py` | parser features → P(address block correct): LightGBM + isotonic, ECE, reliability plot |

## 1. Pre-label
```bash
pip install -r api/requirements-dev.txt
export ANTHROPIC_API_KEY=...                      # or a local vLLM: --provider openai-compatible
python -m training.label_llm --input data/train.json --output data/labels_llm.jsonl --limit 100   # try 100 first
python -m training.label_llm --input data/train.json --output data/labels_llm.jsonl --resume      # then all
```
Default model `claude-sonnet-5-5` ($2 / $10 per MTok — 5 000 records ≈ 2–3 M tokens ≈ a few dollars;
`--model claude-haiku-5-5` is ~20× cheaper, check quality on the first 100). The script asks for
structured JSON (`output_config.format = json_schema`) so parsing never fails. For a very large file use
the Message Batches API (50 % off, asynchronous). **The API is a labeling tool only — the jury run is
offline, so no API call can ever be part of inference.**

## 2. Verify (humans, in our UI)
Load `labels_llm.jsonl` in the review UI (Track A1), sort by issues / disagreements, fix with hotkeys,
accept. Order: validator errors → disagreements with rules → warnings → 10 % random QA sample.
Target: ≥ 1 500 verified. Freeze **dev = 700** stratified by city; never train on it.

## 3. Build, synthesize, train, export, calibrate
```bash
python -m training.build_dataset --labels data/labels_llm.jsonl --corrections data/corrections.jsonl --only-verified --out data/dataset
python -m training.synth --dataset data/dataset/train.jsonl --n 30000 --out data/dataset/synthetic.jsonl
pip install -r training/requirements.txt
python -m training.train --train data/dataset/train.jsonl --train data/dataset/synthetic.jsonl \
    --dev data/dataset/dev.jsonl --labels data/dataset/labels.json --model xlm-roberta-base --out runs/xlmr --epochs 5
python -m training.export_onnx --checkpoint runs/xlmr --labels data/dataset/labels.json --check data/dataset/dev.jsonl
python -m training.calibrate --gold data/dataset/dev_gold.json --plot
make test && make parse && python -m eval.metric --pred out.json --gold data/dataset/dev_gold.json --errors errors.md
```
The service detects `api/artifacts/model.onnx` automatically (`GET /version` → `"model": "onnx"`);
`JANA_MODEL=off` disables it for A/B runs. Arbiter thresholds: `api/jana_address/assembly/arbiter.py: POLICY`.
