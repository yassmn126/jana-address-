# ML solution — Jana Address

The runtime is **rules ⊕ learned tagger ⊕ gazetteer**. They are arbitrated rather than picked one over the other, and everything runs offline on CPU.
This page covers what was built, why, the measured results, and how to retrain once `train.json` arrives.

```
raw ─► preprocess (offset-preserving) ─► tokenizer ─┬─► rule grammar ─────────────┐
                                                    └─► char-CNN+BiLSTM+CRF (ONNX) ┴─► arbiter ─► canon ─► gazetteer (OSM) ─► calibrated confidence
```

## 1. The bet: no labels yet → learn from OpenStreetMap

`train.json` is unlabeled and only arrives at the start of the hackathon. So we follow the
**libpostal recipe**: libpostal's parser is a CRF trained on addresses *generated from OpenStreetMap*.
We do the same for Kazakhstan, and our generator applies the labeling conventions of
[CONVENTIONS.md](CONVENTIONS.md) by construction.

| piece | file | what it does |
|---|---|---|
| Gazetteer ETL | `data_pipeline/build_gazetteer.py` | OSM PBF (225 MB) → `api/artifacts/gazetteer.sqlite` (16 MB) in ~90 s: 7.8k settlements (ru/kk/en names), 20 provinces, city districts (Алматы 8, Астана 6, Шымкент 5, Тараз 2; Караганда from the seed), 5.3k микрорайоны/ЖК, 50k streets split into type + name, house-level district for the 812 streets that cross districts, 58k POIs |
| Synthetic generator | `training/synth_grammar.py` | 220k labeled addresses from real OSM names, covering every slide-9 difficulty: glued numbers, fractions/letters (from real OSM house numbers), Latin, микрорайон-number vs house, landmarks, KK/RU splices and Kazakh word order, duplicated city, district instead of city, typos, invisible characters, postcodes, phones |
| Tagger | `training/train_charcrf.py` → `api/jana_address/ml/charcrf.py` | char-CNN + word embedding + token shape + 44 lexicon/gazetteer/context features → 2×BiLSTM(128) → CRF over 23 BIO labels. 2.6 M params, **ONNX 10.5 MB**, trains on a laptop CPU (~5 min/epoch on 220k rows) |
| Decoding | `ml/charcrf.py` | Viterbi with hard BIO constraints; forward–backward marginals give per-token probabilities to the arbiter and the confidence model |
| Arbiter | `assembly/arbiter.py` + `training/tune_arbiter.py` | per-field policy: agreement → accept; conflict → the model needs prob ≥ τ (lower against the grammar's positional guesses, higher against keyword-bound numbers); thresholds grid-searched on the jury metric → `api/artifacts/arbiter_policy.json` |
| Confidence | `training/calibrate.py` → `confidence/heuristic.py` | logistic regression on 109 parser/arbiter/model/gazetteer features + isotonic calibration, exported as JSON (numpy-only at runtime); ECE, reliability plot, review-budget table |
| Self-training | `training/pseudo_label.py` | rules and model label `train.json` offline. Where they agree with high confidence the result becomes a silver label; disagreements go to the review UI (same JSONL shape as `label_llm.py`) |
| A/B | `eval/ab.py` | jury-metric replica for rules vs model vs hybrid on any gold file |

The gazetteer never overwrites the string. It only fills nulls, and every value carries its provenance:
`string`, `string+table`, `string+gazetteer:osm`, `gazetteer:osm:house`, `gazetteer:osm:street_majority`,
`gazetteer:seed`, `gazetteer:fuzzy`. Ambiguous names get no guess: «Абай ауылы» is a town in Karaganda
region and dozens of villages, so province stays null unless the string or the district narrows it down.

## 2. Results

Jury-metric replica (`eval/metric.py`, strict), `make ab`. Model = **v8** char-CRF: 220k synthetic rows × 6 epochs
(41 min on an M4 CPU), warm-started on 60k rows from the newer generator (v4b), then on 120k Latin-aware rows (v6), then
on **5 861 Claude-written teacher rows ×3 + the four Claude benchmark sets + 60k synthetic** (v8, 4 epochs, 7 min; see
BENCHMARK.md). v8's gain shows only where it should: on addresses nobody tuned on. Rules = the grammar after the
stress-set (§2a) and independent-benchmark ([BENCHMARK.md](BENCHMARK.md)) fixes.

| gold set | n | rules | model only | **hybrid** | all-4-address-fields per record (hybrid) |
|---|---|---|---|---|---|
| **independent benchmark — written & labeled by Claude Opus 5.5** ([BENCHMARK.md](BENCHMARK.md)) | 526 | 97.41 | 98.85 | **99.47** (97.08 on first contact) | 99.8 % |
| **untouched held-out — second Claude set, measured once** ([BENCHMARK.md](BENCHMARK.md)) | 263 | 97.19 | 98.83 | **99.33** (main: 96.30) | 98.5 % |
| **adversarial HARD set #4** — 16 themes of what breaks parsers, fixed against ([BENCHMARK.md](BENCHMARK.md)) | 217 | 89.30 | 90.10 | **98.92** (first contact 91.66, main 85.69) | 97.7 % |
| **adversarial HARD set #5 — untouched, measured once per model** | 190 | 94.54 | 98.86 | **98.98** (v6: 96.47; main 83.51) | 94.6 % |
| **regional set #6 — untouched, measured once per model** | 260 | 96.46 | 98.88 | **98.94** (v6: 98.49) | 98.5 % |
| `test_handmade` — held out (see note) | 89 | 100.00 | 100.00 | **100.00** | 100 % |
| `stress_handmade` — adversarial (§2a) | 106 | 97.66 | 99.85 | **100.00** | 100 % |
| `dev_handmade` | 139 | 100.00 | 100.00 | **100.00** | 100 % |
| `conventions_examples` | 42 | 100.00 | 100.00 | **100.00** | 100 % |
| `rural_handmade` — villages, district centres, Kazakh village strings, post pickups | 40 | 98.86 | 100.00 | **100.00** | 100 % |
| synthetic dev (other seed, in-distribution) | 5 000 | 92.20 | 99.46 | **99.41** | 97.7 % |

Numbers as of the last `make ab` (the full history is in [EXPERIMENTS.md](EXPERIMENTS.md)). **Note on the held-out set:** its last
error («Байконыр» → «Байконур») was fixed after the Claude cross-check (§2b) ran over all demo sets, including this one: Claude and the
gold label agreed on «as written», so the mapping was narrowed to literal Kazakh forms. Treat 100.00 there as dev-like; the clean
estimate before that fix was 99.81. Model choice between v4 and v4b was made on the stress set (99.60 → 99.90 hybrid before
tuning); v4b is also better on the held-out set, which is reported, not used.

* **Speed:** 0.6 ms/record end to end (rules + tagger + arbiter + gazetteer + confidence). 1 000 records take 0.61 s; the limit is 300 s.
* **Confidence:** ECE 0.0017 out-of-fold, 0.005 on the held-out set. Review budget: auto-accepting everything gives 97.6 %
  address-block accuracy on the calibration mix, sending the 10 % least confident to a human gives 98.8 % on the rest
  (`api/artifacts/calibrator.json`, plot in `calibrator.png`).
* **Arbiter:** τ_solo 0.7, τ_field 0.5, τ_weak 0.3, τ_override 0.9, τ_veto 0.9. The model wins conflicts against positional
  grammar guesses and can veto a positional guess it is sure is noise. Numbers bound to an explicit keyword («кв 70»,
  «4подьезд») still need the model at ≥ 0.9 to override the rule. Keyword-anchored spans («район Алматы») win same-token
  conflicts with the model.
* **Artifacts:** `model.onnx` 10.5 MB + `model_meta.json` 1.0 MB, ONNX ≡ PyTorch (max |Δ emission| 9e-6).

`dev_handmade` (139) and `test_handmade` (89) are realistic addresses written and labeled by hand
before the generator existed. `test_handmade` was never used for tuning anything. `conventions_examples` (42)
are the golden convention cases. Synthetic dev is 5k generated rows from a different seed. It is in-distribution,
so it flatters the model; read it as a sanity check, not as an estimate.
**Caveat:** both handmade sets come from the same team that wrote the generator and the rules. The first real
number will come from the 700-record dev split of `train.json` (`make finetune-real` prints it).

## 2a. Stress test — how the model was improved after it hit 99.6

A model that scores 99.6 on its own test sets is only as good as those sets. `data/stress_handmade.json` (106 rows) was
written in styles the generator never produced: recipient names and companies («ИП Сериков, Астана, …»), house‑apartment
dashes («52а‑18», «12‑5‑7»), details before the street («3 подъезд 5 этаж 25 кв Абая 10 Алматы»), door codes («код подъезда 1234»),
glued ordinals («2й подъезд»), floors as «3/9 этаж», «10 Б», «блок А», numbered streets («ул. 8 Марта», «ул. 187»), microdistricts
with no type word («Орбита 3, 27а»), typo'd cities («Алмты»), junk strings.

| | rules | hybrid v3 |
|---|---|---|
| stress set, before | 89.69 | 92.65 |

Every failure class became (a) a generator feature, so the tagger sees it, and (b) where the pattern is unambiguous, a grammar
rule (`api/tests/test_stress_rules.py` pins them). Tokenizer: «52а‑18» no longer glues into one word. Grammar: building parts are
joined after the fallback house; one‑letter type/settlement abbreviations need a dot or a Capitalized name («у получателя»,
«с мечетью»); «код/домофон N» is never a value; «3/9 этаж» → 3; «Жандосова58кв12» binds the house to the street; a microdistrict
name keeps its number when a second number or «дом N» follows; a typo'd first token one edit from a city is the city, as written.

| stress set (106) | rules | hybrid |
|---|---|---|
| before (v3 model, old grammar) | 89.69 | 92.65 |
| after (v4b model, new grammar) | 97.66 | **100.00** |

The held-out set moved too, without being looked at row by row: rules 96.50 → 99.81, hybrid 99.64 → 99.81.

## 2b. Robustness and AI cross-check

**Metamorphic tests** (`make robustness`, report in [ROBUSTNESS.md](ROBUSTNESS.md)): 17 transformations that change the string
but not the answer — case, NBSP/zero-width/BOM, doubled spaces, tabs/newlines, commas removed, country/postcode prefix,
phone/note suffix, Latin «kv», glued «кв70», semicolons. Consistency is the share of records whose scored fields stay
identical. Twelve of them are pinned at 100 % in `api/tests/test_robustness.py`. Also per-category scores (glued, Kazakh,
Latin, микрорайон, fraction, dash, landmark, village, no city, …).

**AI cross-check** (`make verify`, `eval/llm_verify.py`, `POST /api/v1/verify`): our parse vs an LLM labeler (Claude Opus 5.5
with the conventions prompt and structured JSON output, or a local vLLM on the university GPU). Every field where they differ goes
to a human: `review.html` (pick ours / LLM / type a value → export `corrections.jsonl` → `build_dataset --corrections`) and
`review_queue.jsonl` for the review UI. LLM values that are not in the string are flagged — the brief's copy-not-rewrite rule
applies to the LLM too. With gold, both sides are scored and each disagreement gets a verdict. Needs `ANTHROPIC_API_KEY` (or
`ant auth login`), or `VERIFY_ARGS="--provider openai-compatible"` with `OPENAI_BASE_URL`. Never part of parsing; the offline
jury image answers `/api/v1/verify` with 503. Do not send `train.json` to an external API without the organizers' OK.

**First real Claude run (2026-10-18, Opus 5.5 via `ant auth login`, 416 addresses, 7.5 min):** Claude agreed with our parser on
96 % of records; against gold our parser scored 99.79 and Claude 99.37. On the disputed fields we were right 13 times
(корпус → «12/2» convention, «ЮКО» → Туркестанская, «10Б», …) and Claude 6 times — all 6 fixed: «Костанай обл» as a province,
«Байконыр» kept as written, «рядом с мечетью» landmark, no landmark from «звонить за час», province provenance for a typo'd
republican city, and a gold-label inconsistency (open question Q14 in CONVENTIONS.md). Run it with `bash scripts/verify_with_claude.sh`
(key from a hidden prompt / the clipboard, never echoed) or with no key at all after `ant auth login`.

### Wrong keyboard layout and Latin lookalikes

Two corruptions real users produce without noticing: the keyboard left in the Latin layout («Алматы, ул. Абая 10» typed as
«Fkvfns? ek/ F,fz 10»), and single Latin lookalikes inside Cyrillic words («Aлмaты», «yл.», «кв» as «kb»). Lookalikes were
already folded for dictionary matching (`match_view`); the wrong layout needed a pass of its own (`grammar._layout_fix`):

* every whitespace chunk with Latin letters and no Russian ones is read as ЙЦУКЕН keys; it is a *hit* when that reading
  is a lexicon keyword, a city, a settlement, a street or a living area of the gazetteer (`ek` → ул, `rd` → кв,
  `Fkvfns` → Алматы, `Nehfy` → Туран), or when it cannot be a Latin word (a bracket / quote key next to a letter —
  «[» is х, «;» is ж — no Latin vowel, or 20 % vowels or less in 5+ letters);
* a hit is remapped in place (same length, so every offset into the raw string holds); real Latin keywords («mkr», «kv»,
  «street»), Kazakh Latin words («Aqtau», «shaǵyn», «üi») and lone letters («E 26» is an Astana street) never are;
* when the whole string has no Russian letters and most chunks are hits, the whole string is read as ЙЦУКЕН (`?` is the
  comma key, `/` the period key) and the **values are the intended text** («Экибастуз», not «"rb,fcnep»); plausible Latin
  words inside it («Esentai City») are kept. With only some chunks fixed, values stay as written and only recognition
  (and the gazetteer lookups, through the tokens' reading) uses the fix.

`make robustness` has four such transformations; `api/tests/test_robustness.py` keeps the floors:

| transformation | jury score | records unchanged |
|---|---|---|
| homoglyph_30 (30 % of lookalike letters Latin) | 100.00 | 99.8 % |
| homoglyph_all (every lookalike letter Latin) | 99.47 | 97.6 % |
| wrong_layout_words (a quarter of the words) | 98.42 | 93.8 % |
| wrong_layout_all (the whole string) | 99.58 | 98.3 % |

Before the pass, a quarter of wrong-layout words cost 30 points (68–73 on the hand-labeled sets).

## 2c. Geocoding and suggest — what Yandex / 2GIS address stacks do, offline

The brief's pain points include «не считается автоматически зона доставки»: an address string has no coordinates. The gazetteer
now stores every OSM house point (977k), street points and district centroids, so the service can do what a geocoder does:

* `?explain=1` → `geo: {lat, lon, precision}`, also `POST /api/v1/geocode`. Precision, as in the Yandex Geocoder: `house` (the
  OSM building) → `house_near` (same house without its корпус/fraction) → `street` → `microdistrict` → `district` → `locality`.
  The jury schema is unchanged; `geo` exists only in explain mode.
* `GET /api/v1/suggest?q=&city=` → prefix autocomplete over streets (ranked by number of OSM houses), микрорайоны/ЖК and
  settlements. That's the «prevent bad input at the source» feature of their forms; the review UI can use it for corrections.
* `make geocode-check` ([GEOCODING.md](GEOCODING.md)): 500 real OSM addresses from ~100 cities, typed in 7 messy styles
  (capitals, no type word, city last, Kazakh order, «д.»), parse → geocode → distance to the true point:
  **96–97 % resolve to the exact building** (seed-dependent), ~98 % land within 2 km. This measures the parse → match chain; OSM's own
  accuracy is a separate question.

Fixes this check found: renamed towns (OSM «Арал», «Саркан», «Казалы» vs the seed's «Аральск», «Сарканд», «Казалинск»);
streets with initials matched without them («Калдаякова» = «Ш. Калдаякова»); streets named after a region («ул. Карагандинская»,
«Акмолинская ул.») are no longer parsed as the province; the house is searched in every OSM segment of a street before its
midpoint is used.

## 3. Commands

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r training/requirements.txt -r api/requirements-dev.txt osmium shapely
make osm gazetteer          # OSM → api/artifacts/gazetteer.sqlite
make synth-grammar          # 200k train + 5k dev synthetic rows
make train-charcrf          # → api/artifacts/model.onnx + model_meta.json (CPU)
make tune                   # → api/artifacts/arbiter_policy.json
make ab NOTE="what changed" # rules vs model vs hybrid, appended to eval/history.jsonl → docs/EXPERIMENTS.md
make ml-cold-start          # all of the above + calibration + tests
make robustness             # metamorphic tests → docs/ROBUSTNESS.md
make geocode-check          # real OSM addresses → parse → geocode → docs/GEOCODING.md
make verify                 # AI cross-check (needs a Claude key or a local vLLM) → runs/verify/review.html
make history                # experiment table → docs/EXPERIMENTS.md
```
`JANA_MODE=rules|model|hybrid` switches the runtime (default hybrid). `JANA_MODEL=off` disables the tagger,
`JANA_GAZETTEER=seed` disables OSM, `JANA_CALIBRATOR=off` falls back to the heuristic confidence. `GET /version` shows what is loaded.

## 4. When `train.json` arrives (day 0)

**One command:** `make day0` (`python -m training.day0 --input data/train.json`). It writes everything to `runs/day0/`:
`labels_pseudo.jsonl` → `labels_day0.jsonl` (agreement = verified, rest = review) → `dataset/` (its own frozen dev split,
separate from the synthetic `data/dataset/`) → warm-start fine-tune → arbiter re-tune → calibration on the real dev → A/B
logged to `eval/history.jsonl`. The previous artifacts are copied to `runs/day0/artifacts_before/` first, so rolling back
means copying 4 files. `make day0-claude` adds Claude as an independent second labeler: agreement of two different labelers
becomes gold, and every disagreement lands in `runs/day0/review.html` (pick ours / Claude / type, export
`corrections.jsonl` to `data/`, re-run). Only with the organizers' OK to send `train.json` out; otherwise point
`--provider openai-compatible` at a local vLLM on the university GPU. `--limit 200 --skip-train` is a 1-minute dry run.

The same steps by hand:

1. `make pseudo-label`. Offline: silver labels where rules ⊕ model agree, plus a review queue of the disagreements.
   The LLM labeler (`make label`, or local vLLM on the university GPU) is a second opinion for the queue.
2. Humans review the queue in the UI (HANDOFF A1). Accepted rows go to `data/corrections.jsonl`.
3. `make finetune-real`. Builds the dataset (700-record dev frozen), warm-starts the synthetic-pretrained tagger
   on real rows (×5) + 60k synthetic, re-exports, re-calibrates on the real dev, and prints the A/B.
4. `make tune` with `GOLD_SETS="--gold data/dataset/dev_gold.json"`. Re-tunes the arbiter on real dev.
5. Error loop: `runs/ab/*.md` → fix lexicon / generator operators → retrain. Three loops max.

GPU (optional, `gpu/train_remote.sh`): `STAGES="pseudo charcrf-data charcrf calibrate ab"` runs the same thing with
CUDA. The XLM-R path (`training/train.py` + `export_onnx.py`) is still there. The runtime auto-detects which
artifact is present: `model_meta.json` means char-CRF, `tokenizer.json` means XLM-R.

## 5. Data policy

Committed artifacts (`gazetteer.sqlite`, `model.onnx`, `model_meta.json`, `arbiter_policy.json`, `calibrator.json`)
are built **only from OpenStreetMap (ODbL, © OpenStreetMap contributors) and synthetic data**. No Jana Post data is in them.
After fine-tuning on `train.json`, the word vocabulary and the weights are derived from hackathon data. The brief
says issued data must not leave the hackathon, so keep those artifacts out of public repositories: ship them inside the
Docker image / `jana-address-images.tar`, or push only if the repository is private.
