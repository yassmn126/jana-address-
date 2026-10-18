# Independent benchmark — addresses we did not write

Every other gold set in this repo (dev, held-out test, stress, rural, conventions) was written by the same person who
wrote the rule grammar and the synthetic generator. Scores on them are optimistic by construction. This benchmark
fixes that: **Claude Opus 5.5 wrote 528 realistic Kazakhstan delivery addresses in 22 themes and labeled them by
[CONVENTIONS.md](CONVENTIONS.md)**, and we measured our parser against them before looking at a single record.

```
make benchmark                                    # ours vs data/claude_generated.json (526 records)
python -m eval.llm_generate --out data/x.json     # a fresh set (ANTHROPIC_API_KEY or `ant auth login`)
```

## How it was built (`eval/llm_generate.py`)

* 22 themes × 24 strings: each region's typical patterns (Алматы микрорайоны, Астана ЖК, Актау «12-5-7», Караганда
  жилые массивы, the Russian-speaking north, the Kazakh-speaking south), villages, fully Kazakh strings, Latin/English,
  glued numbers and typos, courier notes and phones, very short strings, the full formal format, корпус/блок,
  CAPS/lowercase/NBSP, bilingual duplicates, small towns, and the microdistrict-number vs house ambiguity.
* The system prompt is the whole of CONVENTIONS.md plus the 42 convention examples; output is forced into a JSON
  schema (structured outputs), effort `high`, prompt caching on the shared system prompt.
* Every label goes through the same copy-not-rewrite validators as our training labels
  (`training/validate_labels.py`). The validators were wrong twice — they rejected labels the conventions explicitly
  allow (a Kazakh city spelling mapped to its Russian name; «12 корпус 2» → `12/2`) — so 69 strings were first dropped,
  then relabeled; the validators are fixed and covered by tests.
* 30 of the relabeled strings came back with «корпус» dropped (`12` instead of `12/2`). The cause was **our own
  labeling prompt** (`training/prompts/label_system.md`), which contradicted CONVENTIONS Q7. Fixed in the prompt and
  in the 30 golds (tag `house_q7_fixed`). On day 0 this bug would have put every корпус address of `train.json` into
  the review queue with a wrong suggestion.

## Results (jury metric, hybrid mode)

| | before (main `f825f06`) | after (this branch, model v6) |
|---|---|---|
| **independent benchmark, 526** | **97.08** | **99.47** |
| held-out test_handmade, 89 | 100.00 | 100.00 |
| stress / dev / rural / conventions | 100.00 | 100.00 |
| synthetic dev, 5000 | 99.39 | 99.41 |
| Latin synthetic rows (new generator, 119) | 93.95 | 99.47 |

Per theme, worst first (before → after):

| theme | n | before | after |
|---|---|---|---|
| Латиница и английские слова | 22 | 82.96 | 100.00 |
| Астана | 20 | 94.46 | 98.75 |
| Караганда и Темиртау | 21 | 95.28 | 97.25 |
| Костанай, Петропавловск, Кокшетау, Рудный | 21 | 95.54 | 100.00 |
| relabeled: Kazakh city / корпус join | 67 | 95.62 | 98.12 |
| CAPS / lowercase / NBSP / odd punctuation | 20 | 97.22 | 100.00 |
| Алматы | 45 | 97.91 | 100.00 |
| сёла и посёлки | 23 | 98.37 | 100.00 |
| Тараз, Кызылорда, Туркестан | 21 | 98.65 | 100.00 |
| glued numbers and typos | 23 | 98.69 | 100.00 |
| 9 more themes | 196 | 99.2–100 | 100.00 |
| 9 themes already at 100 | — | 100.00 | 100.00 |

## What it found, and what changed

| found | fix |
|---|---|
| Latin and transliterated Kazakh: «Almaty qalasy», «Aksai-5 shagyn audany 12 uy 34 pater», «Turan avenue», «Abay dangyly», «Lenina st», «8 etazh», «podezd 4», «Bostandyk district», «Almaty obl», «ZhK Highvill», «near school #5» | lexicon (Latin keywords, «кошесі» without Kazakh letters, «кеңсе» = office); Latin «X obl/oblast»; «city/town/village» are keywords only next to a known settlement («Esentai City» stays a name); Kazakh Latin letters (ū ğ ı ş ä ö ü ñ) in the tokenizer; Latin city names stay as written (Q16) |
| the model overrides the grammar on keywords it never saw («Abay dangyly» as a street name) | arbiter: a model span that starts/ends with a lexicon keyword **outside the tagger's vocabulary** loses to the rule span (in-vocabulary words stay with the model: «Expo Avenue», «Smart House», «Набережная» are OSM names) |
| …and the model had never seen Latin addresses written that way | synthetic generator: Latin mode now writes postfix types, «qalasy», «district/rayon», «obl», «ZhK», «uy», «etazh», «5th floor», «korpus», «near …» (8 % of rows); **model v6** = warm start on 120k such rows |
| «Аксай 4 мкр» (microdistrict Аксай-4, not the town Аксай + «4»); «Караганда Пришахтинск 22 мкр» (living area + numbered микрорайон) | gazetteer: the whole name known → it is the микрорайон and wins over the model; a known living area + a number not known together → split |
| «Қостанай, Абай даңғылы 42 үй 2 корпус» | корпус after its number (Kazakh order) → `42/2`, never an entrance |
| «р-н Казыбек би», «Астана, Сарыарка, ул. …» | two-word district names; a whole chunk equal to a district of the parsed city |
| «мкрСтроитель», «д11кв47» | keyword glued to a capitalized name is split; keyword-first glued numbers bind to the keyword before them |
| «кв 3 / 2 этаж» | a spaced « / » separates fields; only «3/9 этаж» (tight) is a floor of total |
| «СТ Строитель» (садовое товарищество) | living area without «СТ», like «ЖК» (Q15); «ст. Шу» stays a station |
| a lone «Абая» | never vetoed or turned into a village when it is the only thing in the string |

Regression tests: [`api/tests/test_independent_benchmark.py`](../api/tests/test_independent_benchmark.py)
(43 cases, rules-only and hybrid).

## Remaining disagreements (3 of 526)

| string | ours | Claude | verdict |
|---|---|---|---|
| Караганда - Майкудук - Восток-5 мкр - 14 дом - кв 39 | street «Караганда - Майкудук - Восток-5» | city / living area / street split on « - » | ours wrong; dash-as-separator is rare and collides with real dashed names («Калбатау - Алматы»), left as is |
| … ЖК Изумрудный квартал | «Изумрудный» | «Изумрудный квартал» | Claude right; «квартал» inside a ЖК name is a type word for us |
| Қызылорда қ., Тасбөгет кенті, Абай көшесі 27 | — | living area «Тасбөгет» | Claude's reading is reasonable (a кент inside the city); no convention yet |

## Caveat — and the honest number

After the fixes above, this set is development data too: its clean, first-contact number is **97.08**. So a second set
was generated the same way (22 themes × 12, `data/claude_heldout.json`, 263 records), nobody read it, and it was
measured **once**, after all the fixes and the v6 model, with nothing changed afterwards:

| untouched held-out, 263 | rules | model | **hybrid** |
|---|---|---|---|
| main `f825f06` | 94.43 | 95.86 | **96.30** |
| this branch (v6) | 97.19 | 98.83 | **99.33** |

**+3.0 points on addresses no one tuned on** — the improvement generalizes. What is still wrong there (7 field errors in
6 records): misspelled keywords the lexicon does not have yet («микрараён», «көшсі»), a house glued to a street name
followed by a fraction («Бухар-Жырау55/3кв.12»), a Latin «r-n», a Latin «korpus» after a postfix street type, and
«Карагнда» (typo) blocking the «Пришахтинск 21 мкр» split. These stay unfixed here on purpose; the next round of fixes
turns this set into development data, and a third set would be needed for the next honest number.
