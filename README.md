# Jana Address — free-form Kazakhstan address parser

Terricon Valley × Jana Post Hackathon 2026, Challenge 01. One line in → 12 fields out, offline.

```
«город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70»
→ province Алматы · city Алматы · district Ауэзовский (gazetteer) · street_type микрорайон
  · street_name «Аксай 3а» · house 59 · apartment 70 · confidence 0.91
```

**How it works (short):** offset-preserving preprocessing → tokenizer → rule grammar
(+ ML token tagger, in progress) → arbiter → canonicalization (only `province`, `street_type`
and number fields are normalized; names are copied *exactly as written*) → gazetteer enrichment
(fills nulls only, with provenance) → calibrated confidence. Full design: [ARCHITECTURE.md](ARCHITECTURE.md).
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
make test        # 100 tests: golden conventions, API contract, ML contract, data-factory round trip
make api-dev     # uvicorn --reload on :8000      (pip install -r api/requirements-dev.txt)
make web-dev     # vite dev server on :5173, proxies /api
make eval        # jury-metric replica on a gold file (eval/metric.py)
make label-dry   # labeling pipeline with the rule parser (no API key)
make dataset synth train export calibrate eval-dev   # the ML track (training/README.md)
make speed       # 1000 records through the running API
make images save-images offline-test
```
Who does what: [docs/HANDOFF.md](docs/HANDOFF.md). Conventions and decisions: [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Repository
```
api/jana_address/   preprocess.py tokenizer.py rules/{lexicon,province,places_seed,conventions}.yaml rules/grammar.py
                    ml/onnx_tagger.py assembly/{arbiter,canon}.py gazetteer/enrich.py confidence/{heuristic,features}.py
                    service.py main.py schemas.py
api/artifacts/      model.onnx · tokenizer.json · labels.json · calibrator.pkl (built by training/, git-ignored)
api/tests/          test_golden.py · test_api.py · test_ml_contract.py · test_training_pipeline.py
eval/               metric.py (exact replica of the jury metric + error report) · speed.py
training/           label_llm.py · validate_labels.py · build_dataset.py · synth.py · train.py · export_onnx.py · calibrate.py
web/                React + Vite review UI, nginx proxy
data/               sample_input.json · sample_output.json · conventions_examples.json   (train.json is git-ignored)
docs/               CONVENTIONS.md · HANDOFF.md · ARCHITECTURE.md
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
| ONNX tagger runtime + arbiter (rules ⊕ model) | ✅ wired; activates when `api/artifacts/model.onnx` exists |
| Gazetteer | 🟡 YAML seed (cities, districts, Almaty/Karaganda microdistricts) — OSM + KATO ETL → sqlite is next |
| Calibrated confidence | 🟡 heuristic v0 live; `training/calibrate.py` ready |

Secrets, personal data and `train.json` are never committed (see `.gitignore`).
