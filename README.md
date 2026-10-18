# Jana Address — free-form Kazakhstan address parser

Terricon Valley × Jana Post Hackathon 2026, Challenge 01. One line in → 12 fields out, offline.

```
«город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70»
→ province Алматы · city Алматы · district Ауэзовский (gazetteer) · street_type микрорайон
  · street_name «Аксай 3а» · house 59 · apartment 70 · confidence 0.91
```

**How it works (short):** offset-preserving preprocessing → tokenizer → rule grammar ⊕
char-CNN+BiLSTM+CRF tagger (ONNX, trained on 200k addresses generated from OpenStreetMap) → arbiter →
canonicalization (only `province`, `street_type` and number fields are normalized; names are copied
*exactly as written*) → OSM gazetteer enrichment (fills nulls only, with provenance; house-level
districts) → calibrated confidence. Full design: [ARCHITECTURE.md](ARCHITECTURE.md); ML details and
results: [docs/ML.md](docs/ML.md).
Labeling rules: [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Run (jury)
Option A — machine with network (build once, ~2 min):
```bash
docker compose up --build        # API http://localhost:8000  ·  UI http://localhost:3000  ·  Swagger http://localhost:8000/docs
```
Option B — offline machine (we hand over `jana-address-images.tar`):
```bash
docker load -i jana-address-images.tar && docker compose up
```
Startup ≈ 10 s. 1000 records ≈ 1 s on 2 vCPU with rules; the ONNX tagger adds ~20 ms/record
(limit in the brief: 5 min). Images are built for linux/amd64 and linux/arm64.

### Batch endpoint — upload the test file
```bash
curl -s -X POST http://localhost:8000/api/v1/parse/batch -F file=@test_input.json -o output.json
# or as a JSON body:
curl -s -X POST http://localhost:8000/api/v1/parse/batch -H 'Content-Type: application/json' --data-binary @test_input.json -o output.json
```
`POST /api/v1/parse/batch` accepts **a multipart file (any field name)** or **a JSON body**
(array of `{"id","raw_address"}`, `{"records":[...]}`, or JSON Lines). Add `?download=1` to get the
answer as an attachment `output.json`. The response is an array in the exact `sample_output.json`
schema, one object per input record in input order, `raw_address` echoed unchanged, `null` where a
field is unknown, `confidence` in 0..1. No record is ever skipped or fails.
`POST /api/v1/parse/batch/file` (field `file`) and `POST /api/v1/parse` (single record) also exist.

`?explain=1` adds `spans`, `tokens`, `flags`, `provenance`, `model_probs` (what came from the string
vs the gazetteer vs the model) — used by the UI and at the defense; the default response has no extra keys.

CLI equivalent (same code path): `python -m jana_address.service in.json out.json`.
The UI (http://localhost:3000) does the same via drag & drop and lets a person fix low-confidence rows.

### Review UI (http://localhost:3000)
Upload a file or paste addresses → table with colored spans in the raw string, every field editable,
sort by confidence (review queue), confidence slider, search, export JSON in the jury schema.
Click-to-relabel: select text in the address and press **S** street · **H** house · **A** apartment ·
**E** entrance · **F** floor · **D** district · **L** living area · **M** landmark · **C** city;
**Enter** accepts, **J/K** moves. Corrections are stored (`POST /api/v1/corrections`) for retraining only —
they are never consulted at inference (the brief forbids answer lookup by exact input).

## Develop
```bash
make test        # 178 tests: golden conventions, API, ML contract, stress rules, robustness, fuzz, geocoding, AI cross-check
make api-dev     # uvicorn --reload on :8000      (pip install -r api/requirements-dev.txt)
make web-dev     # vite dev server on :5173, proxies /api
make eval        # jury-metric replica on a gold file (eval/metric.py)
make label-dry   # labeling pipeline with the rule parser (no API key)
make ml-cold-start   # OSM gazetteer → synthetic data → tagger → arbiter tuning → calibration (docs/ML.md)
make ab              # rules vs model vs hybrid on the hand-labeled gold sets (jury metric)
make pseudo-label finetune-real   # when train.json is in data/
make speed       # 1000 records through the running API
make images save-images offline-test
```
Who does what: [docs/HANDOFF.md](docs/HANDOFF.md). Conventions and decisions: [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Repository
```
api/jana_address/   preprocess.py tokenizer.py rules/{lexicon,province,places_seed,conventions}.yaml rules/grammar.py
                    ml/{onnx_tagger,charcrf}.py assembly/{arbiter,canon}.py gazetteer/{enrich,osm,names}.py
                    confidence/{heuristic,features}.py service.py main.py schemas.py
api/artifacts/      gazetteer.sqlite · model.onnx · model_meta.json · arbiter_policy.json · calibrator.json (api/artifacts/README.md)
data_pipeline/      build_gazetteer.py (OSM PBF → gazetteer.sqlite)
api/tests/          test_golden.py · test_api.py · test_ml_contract.py · test_training_pipeline.py
eval/               metric.py (exact replica of the jury metric + error report) · ab.py (rules/model/hybrid) · speed.py
training/           synth_grammar.py · train_charcrf.py · tune_arbiter.py · pseudo_label.py · calibrate.py
                    label_llm.py · validate_labels.py · build_dataset.py · synth.py · train.py/export_onnx.py (XLM-R, GPU)
web/                React + Vite review UI, nginx proxy
data/               sample_input.json · sample_output.json · conventions_examples.json · dev_handmade.json · test_handmade.json
                    (train.json, labels, corrections, data/osm/, data/dataset/ are git-ignored)
docs/               CONVENTIONS.md · HANDOFF.md · ML.md · ARCHITECTURE.md
```

## Status
| part | state |
|---|---|
| Rule grammar v1 (RU/KZ/Latin keywords, glued tokens, fractions, bilingual splices, microdistrict rule, Kazakh word order and ordinals) | ✅ 42/42 convention examples |
| REST API (file upload or JSON), exact jury schema, explain mode, corrections, Docker compose | ✅ |
| Review UI (spans, edit, hotkeys, queue, export) | ✅ v1 — backlog in HANDOFF A1–A3 |
| Jury-metric replica + error report + speed check | ✅ |
| Labeling pipeline (LLM / local vLLM / rules) with validators | ✅ (needs train.json) |
| Dataset builder, synthetic generator, training/export/calibration scripts | ✅ scripts + round-trip tests (no model trained yet) |
| ML tagger: char-CNN+BiLSTM+CRF trained on OSM-generated addresses, ONNX 10.5 MB, 0.6 ms/record; jury metric on hand-labeled sets: held-out 99.8, adversarial stress set 100.0 | ✅ committed in `api/artifacts/` — results in [docs/ML.md](docs/ML.md) |
| Gazetteer: OSM Kazakhstan → sqlite (settlements ru/kk, provinces, city districts, 5.3k микрорайоны/ЖК, 50k streets, house-level districts), seed fallback | ✅ `make gazetteer` |
| Calibrated confidence (logistic + isotonic, JSON, numpy-only at runtime) | ✅ on synthetic + handmade gold — refit on the real dev set |
| Geocoding (house → street → микрорайон → district → city point, `?explain=1` / `POST /api/v1/geocode`) + suggest (`GET /api/v1/suggest`) | ✅ 97.4 % of real OSM addresses typed messily resolve to the exact building |
| Robustness (17 metamorphic transformations), adversarial stress set, experiment history, AI cross-check vs an LLM | ✅ `make robustness`, `make history`, `make verify` — docs/ML.md §2a–2c |
| Self-training on `train.json` (rules ⊕ model agreement → silver labels + review queue) | ✅ `make pseudo-label` → `make finetune-real` |

Secrets, personal data and `train.json` are never committed (see `.gitignore`).

Gazetteer data © OpenStreetMap contributors, available under the Open Database License (ODbL).
