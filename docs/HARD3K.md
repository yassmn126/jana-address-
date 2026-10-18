# hard3k — a nationwide 3,000-address exam, measured once

**Result: 99.04** jury score (address 99.03 · details 99.42 · refs 98.42) for `main` @ 999d004 (hybrid). It was
measured once, on a frozen file nobody tuned against. Rules alone score 96.18 on the same file. 1,000 addresses take
3.1–3.4 s on one CPU core.

## What the set is

`eval/hard3k_generate.py` is an independent generator. It was written without looking at the parser, the lexicon or
`training/synth_grammar.py`. Its only inputs are `docs/CONVENTIONS.md` and the gold of the Claude-written sets.
- **Gold by construction.** Each record is assembled piece by piece. Every value piece is recorded as gold;
  noise is not. Only the values the spec normalizes are not verbatim: province, street_type, a Kazakh-spelled city →
  Russian (Q2), and корпус → «12/2» (Q7).
- **Nationwide.** The 17 regions and 3 republican cities are drawn with equal probability, then a regional centre or one
  of 42 smaller towns. Every unit has 121–172 records; Astana and Almaty get 1/20 each, like everyone else.
  Rural records come from 23 districts across all regions.
- **18 themes** (1,320 realistic, 1,680 hard):
  - Jana Post's bilingual DB prefix, free text and small towns;
  - glued tokens; numbered and named microdistricts incl. the Aktau «12-5-7» triplets; ЖК / ж.м. + street; garden
    societies;
  - Kazakh with ordinals and case endings; Latin (ASCII and the 2021 Kazakh alphabet);
  - корпус/блок/офис; landmarks and courier notes; typos, case, invisible characters;
  - reversed order, a district instead of the city, old city names; self-corrections and old names in brackets;
    villages;
  - wrong-layout keywords; whole wrong-layout strings; Latin lookalikes; house ranges; PO boxes; «№»; quotes;
    line breaks; garbage.
- **Frozen**: seed 2026, sha256 in `data/hard3k/hard3k_s2026_manifest.json`
  (gold `32dbbbb4…`, input `4ce84e67…`).
- **Protocol**: the generator was debugged on seed 7 to remove *gold* problems only, never to suit the parser:
  - «Актау» as a massiv inside Temirtau collides with the city;
  - «ул. Бульвар Независимости» hides a type word in the name;
  - «Нур Актобе» contains a city (Q6a);
  - a living area followed by a street without a comma;
  - «Семей … Юнусабадский район» in the international set.

  Then seed 2026 was generated and scored once.

## Results

| slice | n | score |
|---|---|---|
| all | 3,000 | **99.04** |
| realistic | 1,320 | 99.28 |
| hard | 1,680 | 98.99 |
| weakest themes | | corrections/brackets 96.28 · wrong-layout keywords 96.80 · typos/case/invisible 97.67 · extreme mix 98.02 |
| perfect themes | | Kazakh, Latin, glued tokens, корпус/офис, dacha, garbage: 100.00 |
| regions | 20 | 98.17 (Astana) … 99.70 (West Kazakhstan); no region below 98 |

Per field: province 99.92 · city 99.24 · street_name 98.36 · house 98.62 · apartment 99.61 · entrance 99.76 ·
floor 98.89 · district 97.56 · living_area 99.28 (street_type 92.17 and landmark 93.85 are not scored).

## The 134 records it gets wrong — and what to fix

| cause | records | example (pred → gold) |
|---|---|---|
| letter house glued to «дом» | 23 | «5/5бдом» → house `5/5` instead of `5/5б` |
| the gazetteer rewrites or mangles the city (copy rule) | 21 | «Арыс» → `Арысь`; «город Шу/Шу қаласы» → `Шу/Шу` |
| type word glued to the name | 18 | «м-нБереке», «МкрКенгир», «мкр-нАксай-1» → kept in street_name |
| misspelled type word | 15 | «улитца Жамбыла» → street_name `улитца Жамбыла` |
| house range cut | 14 | «38а-40» → `38а` |
| repeated city glued to the street | 8 | «КОКШЕТАУ Абылай хана» → street_name with the city |
| self-correction ignored | 7 | «5 пәтер, жоқ қателестім 18 пәтер» → floor 5 |
| Kazakh half of «Сарыарка район/Сарыарқа ауданы» | 7 | district `Сарыарқа` instead of `Сарыарка` |
| wrong-layout «'nf;» (= этаж) | 6 | «'nf; 11» → apartment 11 |
| old name in brackets | 6 | «Алашахана (Комсомольская) 30» → street_name with the bracket |
| number in a note / landmark | 3 | «подъезд №8 2 звонка» → entrance 2 |
| genuinely ambiguous (gold defensible, not certain) | 5 | «Астана, Сарыарка, д. 24»: avenue or district? «Костанай, Кустанайский, …»: massiv or district? |
| корпус after a glued house | 1 | «Алтынсарина75 корпус 6» → `75` |

The first five causes are 91 of the 134 records. Each is a local fix in the grammar, the arbiter or the canon step:
- a letter before «дом»;
- never replace a city spelling that is in the string;
- split a glued «мкр/м-н» prefix;
- fuzzy-match type words;
- keep «N-M» ranges.

## Confidence is over-confident here

100 of the 134 wrong records report confidence **0.99**. At the default review threshold (0.80) the review queue / AI
fallback receives 1.8 % of records and reaches 17 % of the errors. At 0.97 it receives 6.8 % and reaches 25 %. The
calibrator was fitted on the older dev sets. Refit it with hard3k-style data, or use `?ai=always` where the API is
allowed (docs/AI_MODE.md).

## International (not jury data)

`data/hard3k/intl_s2026_gold.json`: 150 addresses from Russia, Kyrgyzstan, Uzbekistan, China, Türkiye, Germany, UAE,
Georgia, Armenia, Azerbaijan, Belarus and the USA, labeled by our convention (docs/AI_MODE.md).
- The new offline country detection flags **150 of 150** as foreign.
- It flags **0 of 11,492** Kazakhstani gold records.
- Flagged records get confidence ≤ 0.30 instead of 0.99 with a country name parsed as the city.

Field accuracy on them needs the AI path: `python -m eval.ai_eval --gold data/hard3k/intl_s2026_gold.json --mode foreign`.

## Reproduce

```bash
python -m eval.hard3k_generate --n 3000 --seed 2026 --intl 150 --out data/hard3k      # byte-identical, check sha256
python -m eval.hard3k_report --gold data/hard3k/hard3k_s2026_gold.json --out runs/hard3k_s2026
python -m eval.metric --pred runs/hard3k_s2026/pred.json --gold data/hard3k/hard3k_s2026_gold.json --errors errors.md
```

New seeds (`--seed 7`, `--seed 8` …) give fresh exams of the same difficulty. Use them for development, and keep
seed 2026 for the before/after number.
