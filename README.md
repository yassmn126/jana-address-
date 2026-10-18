# Jana Address — the free-form Kazakhstan address parser

**Terricon Valley × Jana Post Hackathon 2026 · Challenge 01.** One line of address, typed any way a customer types it,
→ 12 structured fields in the exact jury schema + a calibrated confidence. **Fully offline** (one Docker image, no
network, no external APIs), **1 000 addresses in under a second**, **100.00** on every hand-labeled set and
**98.98** on an adversarial set nobody tuned on.

```
«Нурсити 2 микрорайон 11дом 5 подьезд 239 кв»   «Fkvfns? ek/ F,fz 10? rd 5»   «Almaty qalasy, Abai daŋğyly 10, 25 päter»
          ↓                                              ↓                                   ↓
 микрорайон «Нурсити 2» · дом 11 ·            Алматы · ул. Абая · 10 · кв 5          Almaty · проспект Abai · 10 · кв 25
 подъезд 5 · кв 239                           (keyboard left in the Latin layout)   (Kazakh Latin alphabet, 2021)
```

> **Для жюри, коротко.** Правила ⊕ нейросеть (char-CNN+BiLSTM+CRF, 2,6 млн параметров, ONNX 10,5 МБ) ⊕ справочник
> OpenStreetMap (977 тыс. домов) внутри одного Docker-образа. Старт 1,1 с, 1 000 адресов за 0,7–0,9 с, 84 МБ памяти,
> без сети. Значения копируются из строки как написаны; нормализуются только область (таблица) и тип улицы (лемма).
> 6 независимых наборов (1 719 адресов, написаны не нами) с честным протоколом «мерим один раз, потом правим»;
> нетронутый «злой» набор: 83,51 → 98,98. Ни одной таблицы «строка → ответ». Полное описание —
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); что получает каждый критерий жюри — [docs/CHECKLIST.md](docs/CHECKLIST.md).

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Quick start](#2-quick-start)
3. [API](#3-api)
4. [How it works](#4-how-it-works)
5. [Results](#5-results)
6. [Data, training, hardware](#6-data-training-hardware)
7. [Where Claude is used (and where it is not)](#7-where-claude-is-used-and-where-it-is-not)
8. [Labeling conventions](#8-labeling-conventions)
9. [Jury rules — compliance](#9-jury-rules--compliance)
10. [Repository layout](#10-repository-layout)
11. [Make targets](#11-make-targets)
12. [Team, docs, licenses](#12-team-docs-licenses)

---

## 1. What it does

Input: `[{"id": "...", "raw_address": "..."}, ...]`. Output: one object per input record, same order, `raw_address`
echoed unchanged, `null` where a field is not in the string, `confidence` ∈ [0, 1]. Nothing is ever skipped: a broken
record yields all-null fields with confidence ≤ 0.08 and the batch goes on.

```json
{"id": "rec_00417", "raw_address": "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
 "province": "Алматы", "city": "Алматы", "district": "Ауэзовский", "living_area": null,
 "street_type": "микрорайон", "street_name": "Аксай 3а", "house": "59", "apartment": "70",
 "entrance": null, "floor": null, "landmark": null, "confidence": 0.99}
```

| field | rule | example |
|---|---|---|
| `province` | the only normalized name: canonical from a 20-row table («ВКО» → «Восточно-Казахстанская область»; a republican city names itself) | `Алматы`, `Актюбинская область` |
| `city` | as written («Алмата» stays «Алмата»); a Kazakh spelling of a known city → its Russian name («Қарағанды қ.» → «Караганда») | `Алматы`, `Astana` |
| `district` | city district or oblast district as written, without «район»; filled from the gazetteer when absent (marked as such) | `Ауэзовский`, `Есиль` |
| `living_area` | микрорайон / ЖК / жилой массив / садовое товарищество when a separate street exists; type word stripped | `Хайвил`, `Пришахтинск` |
| `street_type` | lemma from ~600 spellings RU / KK / Latin | `улица`, `проспект`, `микрорайон` |
| `street_name` | copied exactly, numbered микрорайоны keep their number | `Аксай 3а`, `Mangilik El`, `2-ой` |
| `house` | as written; «12 корпус 2» → `12/2`, «12 блок А» → `12/А`, «21-а» → `21а` | `59`, `55/6`, `12а` |
| `apartment` `entrance` `floor` | numbers only; «оф./каб./бутик N» count as apartment | `70`, `5`, `9` |
| `landmark` | free text after «напротив / рядом / ориентир …» or a known venue word | `школа №65`, `ТЦ Mega` |

`?explain=1` adds `spans`, `tokens`, `flags`, `provenance` (string / string+table / gazetteer), `model_probs`,
`geo` (lat, lon, precision, `in_database`) — what the UI and the defense show. The default answer has **exactly** the
jury keys (checked by the offline test).

---

## 2. Quick start

### Run (jury)

```bash
# A — a machine with network (build once, ~2 min; nothing is fetched at run time):
docker compose up --build        # API http://localhost:8000 · UI http://localhost:3000 · Swagger http://localhost:8000/docs

# B — a machine without network (we hand over jana-address-images.tar, built for linux/amd64 + linux/arm64):
docker load -i jana-address-images.tar && docker compose up
```

Container healthy in **1.1 s** (limit 180 s). The API image is `python:3.11-slim` + rules + `model.onnx` 10.5 MB +
`gazetteer.sqlite` 36 MB; no torch, no downloads, no network calls.

### Parse the test file

```bash
curl -s -X POST http://localhost:8000/api/v1/parse/batch -F file=@test_input.json -o output.json          # multipart
curl -s -X POST http://localhost:8000/api/v1/parse/batch -H 'Content-Type: application/json' \
     --data-binary @test_input.json -o output.json                                                        # JSON body
```

The batch endpoint accepts a multipart file (any field name) or a JSON body (`[{id, raw_address}]`, `{"records": [...]}`,
JSON Lines, or a plain list of strings). `?download=1` returns an attachment `output.json`.
CLI with the same code path: `python -m jana_address.service in.json out.json`.

### Prove it works offline

```bash
make offline-check   # builds the API image, runs it with --network none: start time, exact schema, jury metric on the
                     # hand-labeled sets, parity with the local code, 1000-record timing, memory
```

Last run: healthy 1.1 s · exact schema OK · 100.00 / 100.00 · 0 records differ from local · **1000 records in 0.87 s** · 84 MiB.

### Review UI (http://localhost:3000)

| path | page |
|---|---|
| `/dashboard` | next step (upload · review queue · done · API down), session numbers, review queue, confidence histogram, field fill, cities; run archive and geocoding precision from `/api/v1/runs` |
| `/records`, `/records/:id` | every parsed record: status tabs, city, filters (confidence band, empty fields, OSM geocode level, parser flags) with counts, search, sort, 25–200 rows per page, bulk accept and export. The right-hand drawer edits all 11 fields with per-field confidence and source, shows the geocode, **why this score** and a one-record AI cross-check |
| `/conflicts`, `/conflicts/:id` | our parser vs an AI second opinion: table by reason and field, compare drawer, pick per field |
| `/parse` | one string: fields with sources, geocode, why this score, copy JSON, add to records |

**Why this score** shows exact SHAP values of the calibrated confidence model: it is a standardized logistic regression +
isotonic table (`api/artifacts/calibrator.json`), every feature is in the explain response, so the browser rebuilds the
feature vector and splits the logit per feature (`web/src/lib/explain.ts`; reproduces the API's score on all demo records).

Keys: **J/K** or **↑/↓** step · **Enter** accept · **Esc** close · **/** search · **[** collapse the sidebar; in the record
drawer select text in the raw string and press **S** street · **H** house · **A** apartment · **E** entrance · **F** floor ·
**D** district · **L** living area · **M** landmark · **C** city · **P** region. In conflicts: **1** ours · **2** AI · **D** dismiss.
Filters, sort and page are in the URL, so every view is a link; the working session is kept in the browser's IndexedDB.
Corrections go to `POST /api/v1/corrections` for retraining only — they are never consulted at inference.

### Develop

```bash
pip install -r api/requirements-dev.txt && make test     # 355 tests, ~3 min on a laptop
make api-dev                                             # uvicorn --reload on :8000
make web-dev                                             # vite dev server on :5173, proxies /api → :8000
```

---

## 3. API

| method & path | what |
|---|---|
| `POST /api/v1/parse/batch` | **the jury endpoint** — file or JSON → array in the jury schema; `?explain=1`, `?download=1` |
| `POST /api/v1/parse/batch/file` | same, named multipart field `file` |
| `POST /api/v1/parse` | one record |
| `POST /api/v1/geocode` | parse + point: `{lat, lon, precision, in_database}`; precision = house → house_near → street → microdistrict → district → locality |
| `GET /api/v1/suggest?q=&city=` | autocomplete from the gazetteer (settlements, streets, микрорайоны / ЖК) — the «fix the input at the source» feature |
| `GET /api/v1/runs` · `/runs/stats` · `/runs/{id}` · `/runs/{id}/download` · `/runs/{id}/points` | **run archive**: every batch ever parsed, kept in SQLite in the `/data` volume — inputs, outputs, confidence, timing, and per record whether the address exists in our database; points for a map |
| `POST /api/v1/corrections` | store a human correction (training data only) |
| `POST /api/v1/conflicts/scan` · `GET /api/v1/conflicts` · `…/{id}/resolve` · `…/{id}/approve` | human-in-the-loop queue: our parser vs an AI labeler, disagreements for review |
| `POST /api/v1/verify` | AI cross-check of a batch: our answer vs Claude's (needs credentials; 503 in the jury image) |
| `POST /api/v1/parse/batch?ai=foreign\|fallback\|always` · `GET /api/v1/ai/status` | **AI mode** (off by default): Claude as a second annotator for foreign / uncertain / all records; status shows model, prompt version, tokens, USD |
| `GET /health` · `GET /version` | liveness; what is loaded (rules, model, mode, gazetteer stats, model metrics) |

Swagger: http://localhost:8000/docs.

---

## 4. How it works

```
raw ─► clean ─► layout fix ─► tokenize ─► RULE GRAMMAR ─┐
       (invisible   («Fkvfns»→Алматы   (words, numbers,  │ spans + token roles
        chars,  ) when the keyboard  «12а», «55/6»)  ▼
                      was in the wrong                ARBITER ◄── TAGGER  char-CNN + BiLSTM + CRF (ONNX)
                      layout)                           │  field by field: agree / rule / model by tuned thresholds;
                                                        │  absolute rules; veto on swallowed keywords
                                                        ▼
                                              CANON  conventions.yaml — 16 decisions as switches
                                                        │  copy as written; normalize only province + street type
                                                        ▼
                                              ENRICH  OSM gazetteer — fills nulls only, with provenance
                                                        ▼
                                              CONFIDENCE  logistic regression + isotonic (ECE 0.006)
                                                        ▼
                                   jury answer ─► RUN ARCHIVE (SQLite, /data) + geocode (point, precision, in_database)
```

* **Rule grammar** ([`api/jana_address/rules/grammar.py`](api/jana_address/rules/grammar.py), ~1 400 lines;
  [`lexicon.yaml`](api/jana_address/rules/lexicon.yaml) with ≈600 keyword spellings in Russian, Kazakh (incl. case
  suffixes «көшесінде», «даңғылындағы») and Latin / transliteration («qalasy», «koshesi», «dangyly», «päter»), plus
  typos): 14 passes — phones and door codes, «/» and «—» separator styles, bracket aliases «(бывш. Ленина)», province,
  number↔keyword binding («кв 70», «70 кв», «4подьезд», «д11кв47»), street with its type before or after the name,
  микрорайон-number vs house («Мкр Аксай 3а, 59» ↔ «мкр Жетысу 20»), district, city (keyword, Kazakh suffix, gazetteer,
  fuzzy «Карагнда»), ЖК and garden societies, landmarks, corrections inside the string («нет, 12», «исправляю: кв 12»),
  leftovers. Every decision is a span with a rule name and a score.
* **Tagger** ([`ml/charcrf.py`](api/jana_address/ml/charcrf.py), [`training/train_charcrf.py`](training/train_charcrf.py)):
  char-CNN (32→96) + word embedding (28 678 words) + token shape + 44 binary features (lexicon roles, gazetteer sets,
  context) → 2 × BiLSTM(128) → CRF over 23 BIO labels. **2.62 M parameters, ONNX 10.5 MB, 0.6–0.9 ms per address on
  one CPU core.** Trained from scratch — no pretrained download, nothing fetched at run time.
* **Arbiter** ([`assembly/arbiter.py`](api/jana_address/assembly/arbiter.py)): per field — agreement, rule only, model
  only (≥ τ_solo), conflict (model wins above τ_field; against a keyword-bound rule only above τ_override). Absolute
  rules the model cannot override: a gazetteer-confirmed micro name («Аксай 4 мкр»), a correction, a «7-7а» house pair,
  «ЖК X» against a model span elsewhere. A model span that starts or ends on a keyword the tagger never saw loses to the
  grammar; a name span never swallows the known city at its edge («ЖК Хайвил Астана» → ЖК «Хайвил», city «Астана»).
  Thresholds grid-searched on the jury metric ([`training/tune_arbiter.py`](training/tune_arbiter.py)).
* **Gazetteer** ([`gazetteer/osm.py`](api/jana_address/gazetteer/osm.py),
  [`data_pipeline/build_gazetteer.py`](data_pipeline/build_gazetteer.py)): OpenStreetMap Kazakhstan → 36 MB SQLite:
  5 416 settlements ru/kk/en, 20 provinces, city districts, 5 350 микрорайоны / ЖК, 50 504 streets, **977 k house points**.
  Used three ways: features for the tagger; filling empty reference fields (never rewriting what is written; every value
  carries its provenance); geocoding to the building (96 % of real addresses typed messily) and autocomplete.
* **Confidence** ([`confidence/`](api/jana_address/confidence)): logistic regression on 60 parse features + isotonic
  calibration, stored as JSON, numpy-only at run time. ECE 0.006 on an untouched set: «0.9» means right ~90 % of the time,
  so the number can drive a review queue (auto-accept everything 97.5 % → review the 10 % least confident 99.0 %).
* **Run archive** ([`archive.py`](api/jana_address/archive.py)): the jury machine has no network, so there is no
  Supabase / Postgres to send results to — a SQLite file in the `/data` volume keeps every batch (inputs, outputs,
  confidence, timing) and, per record, the geocoding precision → **whether the address exists in our database**.

Full write-up: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). ML details and every experiment: [docs/ML.md](docs/ML.md),
[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).

---

## 5. Results

Jury metric replica ([`eval/metric.py`](eval/metric.py), strict mode — more pessimistic than the brief promises):
exact match per field after lower-casing and punctuation removal; address fields 60 %, details 25 %, reference fields 15 %.

### Sets written by someone else (Claude Opus 5.5 writes and labels, we only measure)

Every «untouched» number was measured **once**, before any fix on that set; then the set was fixed against and a new one
generated. Rounds and what each found: [docs/BENCHMARK.md](docs/BENCHMARK.md).

| set | records | original main | **now (v8)** |
|---|---|---|---|
| regional #1 (22 themes) — fixed against | 526 | 97.08 | 99.94 |
| regional #2 — untouched at round 2 | 263 | 96.30 | 100.00 |
| regional #3 — untouched at round 3 | 263 | 93.91 | 99.78 |
| adversarial #4 (16 themes) — untouched at round 4 | 217 | 85.69 | 99.55 |
| **adversarial #5 — untouched, measured once per model, never fixed against** | 190 | **83.51** | **98.98** |
| **regional #6 — untouched, measured once per model, never fixed against** | 260 | 98.49 | **98.94** |

Adversarial themes: several typos per word, keyboard-layout mixing («Turan аvenue»), the 2021 Kazakh Latin alphabet,
everything glued («ТолебиДом202кв17»), reversed order, corrections inside the string, old names in brackets, 15-field
strings, buildings instead of streets, dachas and garden societies, «/», «;», «—» separators, PO boxes, dictation style.

### Our own sets

| set | records | rules | model | **hybrid** |
|---|---|---|---|---|
| held-out test (never tuned on) | 89 | 100.00 | 100.00 | **100.00** |
| adversarial stress set | 106 | 97.66 | 99.85 | **100.00** |
| dev + 42 convention examples | 181 | 100.00 | 100.00 | **100.00** |
| villages, district centres, Kazakh village strings | 40 | 98.86 | 100.00 | **100.00** |
| synthetic dev (other seed, 5 000) | 5 000 | 92.20 | 99.46 | **99.25** |

The hybrid beats both rules alone and the model alone on every set: rules are exact where a keyword exists, the model
carries the strange strings, the gazetteer resolves what neither can.

### Robustness ([docs/ROBUSTNESS.md](docs/ROBUSTNESS.md), `make robustness`)

21 metamorphic transformations of the 416 hand-labeled strings; 16 change **no** answer at all (case, NBSP / zero-width /
BOM, double spaces, tabs, «Казахстан» or a postcode in front, a phone or a courier note at the end, «kv», «кв5», «;»).
The rest: commas removed 99.44 · Latin lookalikes in every letter 99.77 · a quarter of the words typed in the wrong
keyboard layout **98.42 (was ~70)** · the whole string in the wrong layout 99.85. Fuzzing: 2 500 random strings, no crash,
schema always exact.

### Speed and footprint

1 000 records: **0.7–0.9 s** end to end (limit 300 s) · container healthy in 1.1 s (limit 180 s) · 84 MiB RAM ·
API image = slim Python + 10.5 MB model + 36 MB gazetteer · one CPU core is enough.

---

## 6. Data, training, hardware

| what | how much | from where |
|---|---|---|
| Synthetic training addresses | 220 k (v3) + 90 k (v4b) + 120 k Latin-aware (v6) + 60 k per fine-tune | [`training/synth_grammar.py`](training/synth_grammar.py) from OSM: glued numbers, Kazakh word order, Latin, typos, landmarks, invisible characters — labels by construction |
| Teacher rows | **5 861** | Claude Opus 5.5 writes and labels by our conventions ([`eval/llm_generate.py`](eval/llm_generate.py)); copy-not-rewrite validators drop label errors |
| Independent benchmarks | 6 sets, **1 719** | same generator, regional + adversarial themes |
| Hand-labeled | 416 | held-out, stress, rural, conventions |
| Robustness / fuzz | 21 × 416 / 2 500 | `eval/robustness.py`, `api/tests/test_fuzz.py` |

Model versions, chosen by A/B on every set (`make ab`, each run with artifact hashes in `eval/history.jsonl`):
v3 synthetic → v4b (stress-set grammar + harder synthetic) → v6 (Latin written the way customers type — the independent
benchmark found it) → v7 (789 Claude rows: overfits, rejected) → **v8 (5 861 Claude rows ×3 + the benchmark sets + 60 k
synthetic)**. v8's gain shows exactly where it should: on untouched sets (+2.5 adversarial, +0.45 regional).

**Hardware: a laptop CPU (Apple M4). No GPU was used** — v3 from scratch 41 min, every warm start 3–9 min.

### Day 0 — when `train.json` arrives

```bash
make day0          # pseudo-label (rules ⊕ model agree) → agreement = gold, disagreement = review page →
                   # dataset → warm-start fine-tune → arbiter re-tune → calibration → A/B logged; previous artifacts backed up
make day0-claude   # + Claude as an independent second labeler — only with the organizers' OK to send train.json out
```

The hidden set is the same data as `train.json`: this is the main lever, and it is one command.

---

## 7. Where Claude is used (and where it is not)

Claude Opus 5.5 (Anthropic API, structured outputs, prompt caching) is used **only while preparing**, never at run time:

1. **Independent benchmarks** — a writer of addresses who is not the author of the grammar. It found: Latin and
   transliteration, Kazakh case suffixes, garden societies, corrections inside the string, and a bug in our own labeling
   prompt (корпус).
2. **Teacher** — the 5 861 labeled rows behind v8.
3. **Second opinion** — `make verify`: our answer vs Claude's, disagreements into a review page and the UI's conflict queue.
4. **Day 0** — `make day0-claude`: an independent second labeler for `train.json`, with the organizers' permission;
   without it, day 0 is fully offline.

5. **AI mode at run time, on request** ([docs/AI_MODE.md](docs/AI_MODE.md)) — `?ai=foreign | fallback | always` on the
   parse endpoints (or env `JANA_AI_MODE`): foreign addresses (detected offline by
   [`countries.py`](api/jana_address/countries.py): «Кыргызстан, г. Бишкек…» is flagged `foreign:KG` instead of being
   parsed as a Kazakh city), the low-confidence tail, or every record goes to Claude as a second annotator; every AI value
   must pass the copy-not-rewrite validators; `GET /api/v1/ai/status` shows the model, prompt version, tokens and USD
   spent. Default model `claude-fable-5-1` (the strongest), `claude-opus-5-5` / `claude-haiku-5-5` one env var away;
   `make ai-cost` prints the cost per 1 000 addresses for each.

The jury image contains no credentials, no Claude SDK and makes no network calls; `/api/v1/verify` answers 503 there and
`?ai=` is ignored. All Claude-written data is synthetic; Jana Post data never leaves the machine.

**Turning the AI on (demo / production, never the jury run):**

| where | how |
|---|---|
| Render (the online demo) | Dashboard → service `jana-address-api` → *Environment* → add `ANTHROPIC_API_KEY` and, for a user-scoped key (`sk-ant-usr-…`), `ANTHROPIC_WORKSPACE_ID` → Save → redeploy. The image already has the SDK (`api/Dockerfile.render`). |
| your laptop, from the repo | `cp .env.example .env`, put the key there, `make api-dev` — or sign in once with `ant auth login` and set `JANA_AI_ASSUME_CREDENTIALS=1` |
| your laptop, in Docker | `docker compose -f docker-compose.yml -f docker-compose.ai.yml up --build` (reads `.env`) |
| **no access to any dashboard** (bring your own key) | in the UI, «Сверка с внешним ИИ» → **Ключ Anthropic**: paste your key. It stays in that browser's storage and travels to the API in a request header (`X-Anthropic-Api-Key`, plus `X-Anthropic-Workspace-Id` for `sk-ant-usr-…` keys), is used for that request and is neither stored nor logged. The online demo needs no server-side key at all. |

The key goes into the Console once and into those places only — never into a chat, a commit, a URL or an image. In the UI
the provider menu («Сверка с внешним ИИ») lists Claude Fable 5.1, Opus 5.5, Haiku 5.5, a local LLM and «Только правила»;
`GET /api/v1/ai/status` reports `credentials: server | request | none`.

---

## 8. Labeling conventions

Decisions are written down in [docs/CONVENTIONS.md](docs/CONVENTIONS.md) (16 of them) and the debatable ones are
switches in [`api/jana_address/rules/conventions.yaml`](api/jana_address/rules/conventions.yaml), so an answer from the
organizers is a one-line change: «область Абай» vs «Абайская область» (Q4) · ЖК with or without the type word (Q6) ·
«12 корпус 2» → `12/2` (Q7) · «оф./каб./бутик» = apartment (Q13) · Kazakh city spelling → Russian name (Q2) · a city name
inside a ЖК name is the city (Q6a) · «СТ Строитель» → living area «Строитель» (Q15) · Latin names stay as written (Q16).

---

## 9. Jury rules — compliance

| rule | where we are | proof |
|---|---|---|
| Any stack, **Docker mandatory** | `docker compose up`; API + UI images, amd64 + arm64 | `docker-compose.yml`, `make images save-images` |
| **Offline at the final run**: all dictionaries and models inside the image | rules + ONNX + gazetteer.sqlite are baked in; nothing fetched at run time | `make offline-check` runs with `--network none` |
| **Container start ≤ 3 min** | 1.1 s | offline check |
| **1 000 records ≤ 5 min** | 0.7–0.9 s | offline check, `make speed` |
| Dictionaries, gazetteers, mapping tables welcome | lexicon, province table, OSM gazetteer, conventions table | `api/jana_address/rules/`, `api/artifacts/` |
| **Forbidden: answering by record id or by exact match of the whole input string** | no table «string → answer» exists anywhere; answers are built from tokens; 16 of 21 string corruptions leave the answer unchanged; corrections are training data only | `make robustness`, `api/tests/test_robustness.py` |
| Issued data must not leave the hackathon | `train.json`, labels, corrections git-ignored; Claude labeling only with permission; artifacts in the repo built from OSM + synthetic data only | [docs/ML.md §5](docs/ML.md) |

---

## 10. Repository layout

```
api/jana_address/        the service
  preprocess.py            offset-preserving cleaning, homoglyph / Kazakh-Latin folds, keyboard-layout maps
  tokenizer.py             words, numbers, «12а», «55/6», hyphenated names
  rules/                   grammar.py · lexicon.yaml · province.yaml · places_seed.yaml · conventions.yaml
  ml/                      charcrf.py (featurizer + ONNX runtime) · onnx_tagger.py
  assembly/                arbiter.py (rules ⊕ model) · canon.py (conventions → values)
  gazetteer/               osm.py (lookups, geocode, suggest) · enrich.py (fill nulls with provenance) · names.py
  confidence/              features.py · heuristic.py (calibrated model, numpy-only)
  archive.py               run archive (SQLite in /data) · conflicts.py  human-in-the-loop queue
  service.py main.py schemas.py
api/artifacts/           gazetteer.sqlite 36 MB · model.onnx 10.5 MB · model_meta.json · arbiter_policy.json · calibrator.json
api/tests/               323 tests: golden conventions, API, ML contract, stress rules, independent + adversarial
                         benchmarks, keyboard layout, robustness, fuzz, geocoding, archive, AI cross-check
data_pipeline/           build_gazetteer.py  OSM PBF → gazetteer.sqlite (~90 s)
training/                synth_grammar.py · train_charcrf.py · tune_arbiter.py · calibrate.py · day0.py · pseudo_label.py
                         label_llm.py · validate_labels.py · build_dataset.py · gold_to_labels.py
eval/                    metric.py (jury metric replica + error report) · ab.py · robustness.py · llm_generate.py
                         llm_verify.py · geocode_check.py · speed.py · history.py
web/                     React 18 + Vite + TypeScript review UI (Dashboard · Batch · Quick test · Conflicts), nginx
data/                    sample_input/output · hand-labeled sets · claude_* benchmark sets · teacher/ (git-ignored:
                         train.json, labels, corrections, data/osm/, data/dataset/)
docs/                    ARCHITECTURE · BENCHMARK · CHECKLIST · CONVENTIONS · ML · EXPERIMENTS · ROBUSTNESS · GEOCODING · HANDOFF
scripts/                 offline_check.sh · verify_with_claude.sh
```

---

## 11. Make targets

```bash
make up / down / logs           # docker compose
make test                       # 355 tests
make offline-check              # the jury simulation with --network none
make speed                      # 1000 records through the running API
make images save-images         # amd64 + arm64 images → jana-address-images.tar for an offline machine

make ab                         # rules vs model vs hybrid on every gold set, logged to eval/history.jsonl
make benchmark                  # ours vs the Claude-written benchmark
make hard3k                     # the nationwide 3 000-address exam (frozen) → runs/hard3k_s2026/report.md
make ai-cost / ai-eval          # what the AI path costs; local vs local+AI on a 600-record sample
make robustness                 # 21 metamorphic transformations → docs/ROBUSTNESS.md
make geocode-check              # 500 real OSM addresses typed messily → distance to the true point
make verify                     # AI cross-check (needs credentials) → review page
make history                    # eval/history.jsonl → docs/EXPERIMENTS.md

make osm gazetteer              # OSM extract → gazetteer.sqlite
make synth-grammar train-charcrf tune calibrate   # retrain the tagger from scratch on a laptop CPU
make ml-cold-start              # all of the above, no train.json needed
make day0 / day0-claude         # train.json → labels → review queue → fine-tune → tune → calibrate → A/B
```

---

## 12. Team, docs, licenses

* **Track A (frontend, UI, deploy):** Yasmin — [web/](web), Render + Vercel demo (`render.yaml`, `vercel.json`).
* **Track B (data, ML, API):** Kassymzhomart — everything under `api/`, `training/`, `eval/`, `data_pipeline/`.
  Who does what and the hand-off: [docs/HANDOFF.md](docs/HANDOFF.md).

| doc | what |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | the full picture for the jury: pipeline, model, data, stack, why no external DB, the map |
| [docs/CHECKLIST.md](docs/CHECKLIST.md) | each jury criterion: what is covered, what else could be done, ranked by expected gain |
| [docs/BENCHMARK.md](docs/BENCHMARK.md) | the independent-benchmark protocol, every round's numbers and what each round found |
| [docs/ML.md](docs/ML.md) · [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | ML design, results, retraining, every logged experiment |
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | the 16 labeling decisions and their switches |
| [docs/ROBUSTNESS.md](docs/ROBUSTNESS.md) · [docs/GEOCODING.md](docs/GEOCODING.md) | robustness report; geocoding check |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the original design document the system was built from |

Gazetteer data © OpenStreetMap contributors, available under the Open Database License (ODbL). Secrets, personal data
and `train.json` are never committed (see `.gitignore`).
