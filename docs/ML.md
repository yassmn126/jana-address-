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

Jury-metric replica (`eval/metric.py`, strict), `make ab`. Model = **v4b** char-CRF: 220k synthetic rows × 6 epochs
(41 min on an M4 CPU), then warm-started on 60k rows from the newer generator × 2 epochs (3 min). Rules = the grammar after
the stress-set fixes (§2a).

| gold set | n | rules | model only | **hybrid** | all-4-address-fields per record (hybrid) |
|---|---|---|---|---|---|
| `test_handmade` — **held out, never tuned on** | 89 | 99.81 | 99.81 | **99.81** | 98.9 % |
| `stress_handmade` — adversarial (§2a) | 106 | 97.66 | 99.75 | **100.00** | 100 % |
| `dev_handmade` | 139 | 100.00 | 100.00 | **100.00** | 100 % |
| `conventions_examples` | 42 | 100.00 | 100.00 | **100.00** | 100 % |
| synthetic dev (other seed, in-distribution) | 5 000 | 91.99 | 99.45 | **99.42** | 97.5 % |

The one remaining held-out error is ambiguous: «Байконыр» → «Байконур» (Kazakh name without Kazakh letters, convention Q2;
the label keeps it as written). Model choice between v4 and v4b was made on the stress set (99.60 → 99.90 hybrid before
tuning); v4b is also better on the held-out set, which is reported, not used.

* **Speed:** 0.6 ms/record end to end (rules + tagger + arbiter + gazetteer + confidence). 1 000 records take 0.61 s; the limit is 300 s.
* **Confidence:** ECE 0.0012 out-of-fold, 0.004 on the held-out set. Review budget: auto-accepting everything gives 97.6 %
  address-block accuracy on the calibration mix, sending the 10 % least confident to a human gives 98.8 % on the rest
  (`api/artifacts/calibrator.json`, plot in `calibrator.png`).
* **Arbiter:** τ_solo 0.7, τ_field 0.5, τ_weak 0.3, τ_override 0.9 — the model wins conflicts against positional grammar guesses,
  numbers bound to an explicit keyword («кв 70», «4подьезд») still need the model at ≥ 0.9 to override the rule.
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

## 3. Commands

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r training/requirements.txt -r api/requirements-dev.txt osmium shapely
make osm gazetteer          # OSM → api/artifacts/gazetteer.sqlite
make synth-grammar          # 200k train + 5k dev synthetic rows
make train-charcrf          # → api/artifacts/model.onnx + model_meta.json (CPU)
make tune                   # → api/artifacts/arbiter_policy.json
make ab                     # rules vs model vs hybrid
make ml-cold-start          # all of the above + calibration + tests
```
`JANA_MODE=rules|model|hybrid` switches the runtime (default hybrid). `JANA_MODEL=off` disables the tagger,
`JANA_GAZETTEER=seed` disables OSM, `JANA_CALIBRATOR=off` falls back to the heuristic confidence. `GET /version` shows what is loaded.

## 4. When `train.json` arrives (day 0)

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
