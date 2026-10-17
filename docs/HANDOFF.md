# Handoff — who builds what

Two tracks, one repo. Everything below has an acceptance check; "done" means the check passes.
Branch naming: `feat/<track>-<thing>`; PRs into `main`; `make test` must stay green.

## Track A — Yasmin: frontend + backend routes

Start: `make api-dev` (API :8000, Swagger at /docs) and `make web-dev` (UI :5173). Code map:
`api/jana_address/main.py` (routes), `schemas.py` (Pydantic), `service.py` (parse_one/parse_batch),
`web/src/App.tsx` (the whole UI today), `web/src/api.ts`, `web/src/types.ts`.

### A1 — Review-queue import (priority 1)
The labeling pipeline writes `data/labels_llm.jsonl` (one JSON per line: `id, raw_address, fields,
rules_fields, disagreements, issues, needs_review`). The UI must load this file (drag & drop) and show:
- the LLM fields (editable), the rule parser's fields where they disagree (highlighted cell), the
  `issues` list, and a `needs_review` filter;
- sorting: validator errors → disagreements → warnings → rest;
- "accept" writes the final fields to `POST /api/v1/corrections` (already exists);
- export of all accepted rows as `corrections.jsonl` (same shape the backend writes).
Check: load a 50-line file produced by `make label-dry`, accept 5 rows, export → 5 lines.

### A2 — Backend routes
| route | purpose | notes |
|---|---|---|
| `GET /api/v1/corrections` | list + `?since=` + `?format=jsonl` export | reads `JANA_DATA_DIR/corrections.jsonl` |
| `GET /api/v1/stats` | counts for the stats bar of the last batch (fill rate per field, confidence histogram, flags histogram) | compute in `service.parse_batch` and cache the last result |
| `POST /api/v1/parse/batch?explain=1` streaming (NDJSON) | progress for big files in the UI | optional; current chunking by 200 in the UI is acceptable |
| `GET /api/v1/metrics/dev` | last `eval/metric.py` result (JSON written by `make eval` to `data/dataset/metrics_dev.json`) | shows our own score in the UI/pitch |
| `GET /api/v1/suggest?q=` | **stretch**: autocomplete over the gazetteer (cities, districts, microdistricts, streets) — the "prevent bad input at the source" product story | after the sqlite gazetteer lands |
Check: Swagger shows the routes; `tests/test_api.py` gets one test per route.

### A3 — UI polish (usability = 15 % of the score)
- keyboard flow end-to-end without a mouse (J/K, Enter, hotkeys S/H/A/E/F/D/L/M/C already exist);
- review-budget slider: "review the N % least confident" with expected accuracy from
  `GET /api/v1/metrics/dev` once calibration exists;
- flags filter (multi-select of `flags` values), provenance legend (string / table / gazetteer colors);
- explain panel v2: show `model_probs` next to rule scores when the model is on;
- RU/EN strings via a tiny i18n map; README screenshots.
Check: a new teammate can review 20 records in < 3 minutes without instructions.

### A4 — Delivery
- `make images && make save-images` on a clean machine; `make offline-test` passes;
- `docker buildx` for amd64 + arm64 (jury laptop may be Apple Silicon);
- README "Run (jury)" section verified on a machine that never built the images.

## Track B — Kassymzhomart: data + ML (GPU)

**Status (2026-10-18, branch `feat/ml-tagger`):** cold-start ML is done without `train.json`. That covers the OSM gazetteer
(`make gazetteer`), the grammar synthetic generator, the char-CNN+BiLSTM+CRF tagger (ONNX in `api/artifacts/`), arbiter
tuning, the JSON calibrator, the A/B evaluator and offline self-training (`make pseudo-label` → `make finetune-real`).
Details and numbers: [ML.md](ML.md). B1–B4 below still apply once real data is in; the char-CRF path replaces the
XLM-R steps unless the GPU run beats it on the real dev set.

Code map: `training/` (all scripts), `api/jana_address/ml/onnx_tagger.py` (inference contract),
`api/jana_address/assembly/arbiter.py` (rules ⊕ model policy), `api/jana_address/confidence/`.

```
train.json ─► label_llm.py ─► labels_llm.jsonl ─► (UI review, Track A1) ─► corrections.jsonl
                                     │                                            │
                                     └──────────► build_dataset.py ◄──────────────┘
                                                        │  train.jsonl / dev.jsonl / dev_gold.json / labels.json
                                     synth.py ◄─────────┤
                                        │               │
                                        └──► train.py ──┴──► export_onnx.py ──► api/artifacts/{model.onnx,tokenizer.json,labels.json}
                                                                                      │
                                                                  calibrate.py ──► api/artifacts/calibrator.pkl
```

### B0 — GPU server (univer-gpu6, 2 × A10 23 GB, shared)
Everything runs from the Mac terminal over the Kerio VPN (cloud agents cannot reach 192.168.124.26).
`gpu/README.md` has the order: `gpu/bootstrap.sh` (env report + venv + CUDA torch + repo + tests) →
`gpu/sync.sh` (push data) → `gpu/label_local.sh` (vLLM labeling, data stays on campus) →
`gpu/train_remote.sh` (tmux, one GPU chosen by free memory, logs in `runs/`). Paste the bootstrap
summary into the chat before the first training run; confirm the university's allocation policy first.

### B1 — Labels (day 0–1)
1. `python -m training.label_llm --input data/train.json --output data/labels_llm.jsonl --limit 100` → read the output, fix the prompt/conventions if needed, then run all (`--resume`).
   Local GPU alternative: `vllm serve Qwen/Qwen2.5-32B-Instruct` → `OPENAI_BASE_URL=... --provider openai-compatible`.
2. Human review in the UI (A1). Target ≥ 1 500 verified; dev = 700 frozen (`data/dataset/dev_ids.txt`).
3. `python -m training.build_dataset --labels data/labels_llm.jsonl --corrections data/corrections.jsonl --only-verified --out data/dataset`
   Check: `stats.json` fill rates look like the data; `python -m eval.metric --pred <rules output> --gold data/dataset/dev_gold.json` gives the rules baseline (expect 70–85 % on real data).

### B2 — Model (day 1–2)
```
pip install -r training/requirements.txt
python -m training.synth --dataset data/dataset/train.jsonl --n 30000 --out data/dataset/synthetic.jsonl
python -m training.train --train data/dataset/train.jsonl --train data/dataset/synthetic.jsonl \
    --dev data/dataset/dev.jsonl --labels data/dataset/labels.json --model xlm-roberta-base --out runs/xlmr --epochs 5
python -m training.export_onnx --checkpoint runs/xlmr --labels data/dataset/labels.json --check data/dataset/dev.jsonl
make test && make parse && python -m eval.metric --pred out.json --gold data/dataset/dev_gold.json --errors errors.md
```
Check: dev span-F1 > rules baseline; `export_onnx --check` agreement > 99.5 %; `make eval` higher than rules-only.
Then tune `assembly/arbiter.py: POLICY` thresholds on dev (never on the 300 held-out test rows).
GPU sizing: xlm-roberta-base, bs 32, max_len 128 → < 8 GB VRAM, ~15–25 min for 40k rows × 5 epochs on a T4/V100.
If the GPU falls through: `--model cointegrated/rubert-tiny2` trains on CPU in ~30 min (weaker on Kazakh).

### B3 — Confidence + gazetteer (day 2)
- `python -m training.calibrate --gold data/dataset/dev_gold.json --plot` → `api/artifacts/calibrator.pkl` + reliability PNG for the pitch.
- Gazetteer ETL (`data_pipeline/`, to be written): OSM PBF + KATO → `gazetteer.sqlite` implementing the
  interface in `gazetteer/enrich.py` (`province_of_city`, `city_of_district`, `district_of_microdistrict`,
  `district_of_street(city, street, house)`, `street_type_of`, `is_microdistrict`). Replace `SeedGazetteer`.
  Check: district fill rate on dev rises; wrong districts on a 100-record audit < 3 %.

### B4 — Error loops (day 2–3)
`python -m eval.metric ... --errors errors.md` → fix lexicon / conventions / synth operators → retrain. Three loops max, then freeze.

## Shared rules
- `data/train.json`, labels, corrections, artifacts are git-ignored; share them via the team drive.
- Any convention change goes through `docs/CONVENTIONS.md` + `data/conventions_examples.json` + `make test`.
- The corrections store is for retraining only; nothing in the request path may look up answers by `raw_address` (disqualification clause).
