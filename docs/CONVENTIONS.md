# Labeling conventions — Jana Address

Source of truth for humans, the LLM labeler and the parser. Derived from the brief (slides 7–8, 10)
and `sample_output.json`. Machine-readable examples: `data/conventions_examples.json`
(42 cases incl. Kazakh-first strings; golden tests in `api/tests/test_golden.py` enforce them).

**The one rule: copy, never rewrite.** Every value except `province` and `street_type` is cut from
the raw string as written — same letters, same case, same typos. The jury compares character by
character after lowercasing and removing punctuation/extra spaces. «казибек би» stays «казибек би».

## Field rules

| field | rule | yes | no |
|---|---|---|---|
| `province` | Canonical name from `api/jana_address/rules/province.yaml` («в прямом порядке»). Republican cities: province = city name. Null when neither a region nor Астана/Алматы/Шымкент is in the string (the gazetteer may fill it later — that is enrichment, not a label). | «Актюбинская обл» → `Актюбинская область`; «ВКО» → `Восточно-Казахстанская область`; «город Алматы» → `Алматы` | `Актюбинская обл.`, `обл. Актюбинская` |
| `city` | Name only, without «город/г./пос./с./аул/ст.» and without the Kazakh half of a bilingual pair. Kazakh spelling → Russian name when the gazetteer knows it (Q2, switch `city_kk_to_ru`), else as written. Latin stays Latin (Q12). | «город Актобе/Ақтөбе қаласы» → `Актобе`; «пос. Боралдай» → `Боралдай`; «Қарағанды қаласы» → `Караганда` | `г. Актобе`, `Ақтөбе` |
| `district` | Without «район/р-н/ауданы». City districts (Ауэзовский, Есиль) and oblast districts (Илийский) both go here. Kazakh form stays as written («Әуезов», Q11). Null when not in the string — never inferred by the labeler. | «Турксибский район» → `Турксибский`; «район Есиль» → `Есиль`; «Есіл ауданы» → `Есіл` | `Турксибский район` |
| `living_area` | Микрорайон / ЖК / жилой массив name WHEN A SEPARATE STREET IS ALSO GIVEN; name only, without «мкр/ЖК» (Q6, switch `living_area_keep_type_word`). Kazakh order «Томирис тұрғын үй кешені» works too. Otherwise null. | «мкр Самал-2, ул. Жолдасбекова 9» → `Самал-2`; «ЖК Томирис, ул. Байтурсынова 10» → `Томирис`; «Караганда, Пришахтинск, ул. Ленина 5» → `Пришахтинск` | «Мкр Аксай 3а, 59» → living_area must be null (the microdistrict *is* the street) |
| `street_type` | Lemma: улица, проспект, микрорайон, переулок, квартал, бульвар, шоссе, проезд, тупик, площадь, аллея, тракт, набережная, жилой массив, дорога. Null when no type word is written. Not scored by the jury; judged at the defense. | «Мкр» → `микрорайон`; «пр-т» → `проспект`; «көшесі» → `улица`; «Кулманова 133» → null | `мкр`, `ул.` |
| `street_name` | Exactly as written, without the type word. When the микрорайон/квартал is the locator (no separate street): `street_type = микрорайон/квартал`, `street_name` = its name **including its number** (Q5). Kazakh ordinals are not part of the value («11-ші ықшам аудан» → `11`). | «Мкр Аксай 3а, 59» → `Аксай 3а`; «Нурсити 2 микрорайон 11дом» → `Нурсити 2`; «11 микрорайон, 112 дом» → `11`; «66квартал 12дом» → `66`; «ул Толе би 15» → `Толе би` | `Аксай`, `Толе Би`, `ул. Курильская`, `11-ші` |
| `house` | As written, including fraction and letter, without «дом/д.». «12 корпус 2» → `12/2`, «12 блок А» → `12/А` (team decision Q7; switch `house_building_join`). | `59`, `55/6`, `12а`, `49/2`, `150/230`, `12/2` | `55 / 6`, `12 а`, `12 корпус 2` |
| `apartment` | Number only (+ letter if written), without «кв/квартира/пәтер/kv/flat». Office («оф.», «каб.») counts as apartment (Q13, switch `office_as_apartment`). | «кв 70» → `70`; «239 кв» → `239`; «оф. 12» → `12` | `кв 70` |
| `entrance` | Number only, without «подъезд/подьезд/кіреберіс/п-д». | «4подьезд» → `4` | — |
| `floor` | Number only, without «этаж/қабат/эт.». | «9этаж» → `9` | — |
| `landmark` | The landmark object as written, without a marker word («ориентир», «напротив», «рядом», «возле»). | `магазин береке`, `ТЦ Артур`, `школа №65`, `Qazpost Солнечный` | `ориентир школа №65` |
| `confidence` | P(province, city, street_name, house all correct). Not scored; shown at the defense. | — | — |

## Disambiguation rules
1. **Number ↔ keyword binding** works in both directions: «кв 70» and «70 кв», «подъезд 4» and «4подьезд». A number glued to a keyword («11дом», «9этаж») belongs to that keyword. In a string that consistently writes number-first («11дом 5 подьезд 239 кв»), ambiguous pairs follow that style.
2. **Microdistrict number vs house**: named microdistrict + TWO numbers («Мкр Аксай 3а, 59») → first number is part of the name, second is the house. Named microdistrict + ONE number («мкр Жетысу 20, кв 9») → the number is the house. A bare number next to «мкр/микрорайон/квартал» («14 мкр», «408 квартал», «мкр 11») → the number is the name.
3. **Duplicated city** («Алматы Алматы …», «город Алматы/Алматы қаласы») → one `city`. **District instead of city** («Турксибский район, …») → `city` null in the label (the gazetteer fills it at inference).
4. Invisible characters (`​`, `﻿`, NBSP), double spaces and stray dots («Ул .Курильская») are never part of a value.
5. 6-digit postal codes, «Республика Казахстан», «РК», phone numbers, «тел.», «доставка» are ignored.
6. Unparseable string → all fields null, confidence ≤ 0.1. Never skip a record.

## Decisions log (2026-10-17) — what is settled, by whom, and the switch that implements it
`T` = team decision (our reading of the brief), `O` = confirmed by the organizers, `?` = still open.
Switches live in `api/jana_address/rules/conventions.yaml`; examples in `data/conventions_examples.json`.

| # | question | decision | who | switch / where |
|---|---|---|---|---|
| Q1 | How the jury calls the API | **file upload**; `POST /api/v1/parse/batch` accepts multipart (any field name) *and* JSON (array / `{records}` / JSON Lines); `?download=1` returns `output.json` | T (expected) | `api/jana_address/main.py` |
| Q2 | Jury machine / run mode | Ship **both**: `docker compose up --build` (needs network once) **and** `jana-address-images.tar` via `docker save` for an offline machine; build amd64 + arm64 | T | `Makefile: images save-images offline-test` |
| Q3 | «Точность адреса» = per-field or per-record | report **both** | T | `eval/metric.py` (`address` and `address_all4_per_record`) |
| Q4 | Canonical `province` for области Абай / Жетісу / Ұлытау; old «Нур-Султан» | official forms «область Абай» …; «Нур-Султан» → province `Астана`, city as written | **?** | `province.yaml` (`canonical`) |
| Q5 | Numbered микрорайон («11 микрорайон») | `street_type=микрорайон`, `street_name="11"`. Note: a микрорайон is **not** a `district` — `district` is the administrative район (Ауэзовский, Есиль); the микрорайон is the street-level locator in planned cities (Актау, Актобе, Тараз…), exactly like the sample's «Аксай 3а» | T (brief sample) | grammar Case B/C |
| Q6 | `living_area` with the word «ЖК»? | **without** — the brief's own example is «Томирис», not «ЖК Томирис». Team proposed "with"; we keep the brief's form until the organizers say otherwise | T → **?** | `living_area_keep_type_word` |
| Q7 | House with корпус/блок | **`12/2`** («12 корпус 2»), «12 блок А» → `12/А` | T | `house_building_join: "/"` |
| Q8 | Extra keys in the response | tolerated per team; we still return the **exact schema by default** (zero risk) and extras only with `?explain=1` | T | `main.py` `_serialize` |
| Q9 | LLM API on `train.json` | allowed for *preparing our base* (the "не выносятся" clause is about use after the hackathon). **Runtime is offline — no API can be used during the jury run, batched or not.** Batching matters only for labeling cost/speed: use the Message Batches API (50 % off) or the local vLLM | T | `training/label_llm.py` |
| Q10 | Kazakh vs Russian | both first-class: Kazakh keywords, «ықшам аудан», ordinals «11-ші», name-before-type order, KK→RU city mapping | T | lexicon, grammar, examples ex37–ex41 |
| Q11 | Kazakh district name («Әуезов ауданы») | as written («Әуезов») | T | `district_kk_to_ru: false` |
| Q12 | Latin city («Astana») | as written | T | — |
| Q13 | «оф.»/«каб.» → apartment | yes | T | `office_as_apartment` |

| Q14 | Kazakh-spelled village the gazetteer has no Russian name for («Қазығұрт ауылы») | as written («Қазығұрт»); Claude Opus 5.5 prefers the letter-folded «Казыгурт». «Байконыр» (no Kazakh letters) stays as written, «Орал» → «Уральск» (literal Kazakh form of a known city) | **?** | `gazetteer/osm.py: city_ru` |

| Q15 | Garden society «СТ Строитель», «СНТ Мичуринец» (садовое товарищество) | living_area **without** «СТ» («Строитель»), like «ЖК» (Q6); «ст. Шу» / lower-case «ст» stays a station (city keyword) | T | `rules/grammar.py: _classify`, `assembly/canon.py: _LIVING_PREFIX` |
| Q16 | Latin «Almaty qalasy», «Turan avenue», «8 etazh» | keywords are dropped like their Cyrillic forms; the NAME stays as written in Latin («Almaty», «Turan») — only a Cyrillic Kazakh spelling maps to Russian (Q2) | T | `rules/lexicon.yaml` |

Still worth one message to the organizers: **Q4** (province names) and **Q6** (ЖК) — both are one-line switches once answered.

## Review checklist (for the human verification sprint)
- [ ] every value is visible in the raw string (except province / street_type)
- [ ] no type words inside `city`, `district`, `street_name`, `living_area`
- [ ] microdistrict rule applied (living_area vs street_type=микрорайон)
- [ ] fraction and letter kept in `house`
- [ ] numbers only in apartment / entrance / floor
- [ ] district / province NOT filled from your own knowledge
