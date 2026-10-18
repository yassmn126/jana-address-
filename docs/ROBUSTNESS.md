# Robustness report — mode hybrid, 416 hand-labeled records

Original strings: jury score **100.00**

| transformation | jury score | Δ | consistency | changed |
|---|---|---|---|---|
| lower | 100.00 | +0.00 | 100.0 % | 0 |
| upper | 100.00 | +0.00 | 100.0 % | 0 |
| nbsp | 100.00 | +0.00 | 100.0 % | 0 |
| zwsp_after_comma | 100.00 | +0.00 | 100.0 % | 0 |
| double_space | 100.00 | +0.00 | 100.0 % | 0 |
| tab_newline | 100.00 | +0.00 | 100.0 % | 0 |
| no_space_after_comma | 100.00 | +0.00 | 100.0 % | 0 |
| commas_to_spaces | 99.44 | -0.56 | 99.0 % | 4 |
| bom_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| country_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| postcode_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| phone_suffix | 100.00 | +0.00 | 100.0 % | 0 |
| note_suffix | 100.00 | +0.00 | 100.0 % | 0 |
| trailing_dot | 99.96 | -0.04 | 99.8 % | 1 |
| kv_latin | 100.00 | +0.00 | 100.0 % | 0 |
| kv_glued | 100.00 | +0.00 | 100.0 % | 0 |
| semicolons | 100.00 | +0.00 | 100.0 % | 0 |
| homoglyph_30 | 100.00 | +0.00 | 100.0 % | 0 |
| homoglyph_all | 99.77 | -0.23 | 97.6 % | 10 |
| wrong_layout_words | 98.42 | -1.58 | 95.4 % | 19 |
| wrong_layout_all | 99.85 | -0.15 | 98.8 % | 5 |

## Per category (original strings)

| category | n | jury score | all-4 per record |
|---|---|---|---|
| glued | 12 | 100.00 | 100.0 % |
| kazakh | 39 | 100.00 | 100.0 % |
| latin | 17 | 100.00 | 100.0 % |
| microdistrict | 81 | 100.00 | 100.0 % |
| fraction_house | 44 | 100.00 | 100.0 % |
| letter_house | 26 | 100.00 | 100.0 % |
| dash_apartment | 14 | 100.00 | 100.0 % |
| landmark | 21 | 100.00 | 100.0 % |
| living_area | 28 | 100.00 | 100.0 % |
| district_in_string | 80 | 100.00 | 100.0 % |
| province_in_string | 55 | 100.00 | 100.0 % |
| no_city | 27 | 100.00 | 100.0 % |
| no_street_type | 85 | 100.00 | 100.0 % |
| 3plus_details | 47 | 100.00 | 100.0 % |
| noise_words | 12 | 100.00 | 100.0 % |

## Inconsistent examples (original → transformed)

**commas_to_spaces**
- `'Алматы Абая 10 5'` — street_name: 'Абая' → 'Абая 10'; house: '10' → '5'; apartment: '5' → None; district: 'Бостандыкский' → None
- `'Алматы ТРЦ Мега Розыбакиева 247а'` — street_name: 'Розыбакиева' → None; house: '247а' → None; district: 'Бостандыкский' → None
- `'Нур-Султан ЖК Северное сияние Достык 5 кв 300'` — street_name: 'Достык' → 'сияние Достык'; district: 'Есиль' → None; living_area: 'Северное сияние' → 'Северное'
- `'ЗКО Бурлинский р-н г. Аксай 5 мкр д 2'` — city: 'Аксай' → None; street_name: '5' → 'Аксай 5'

**trailing_dot**
- `'Абая.'` — province: None → 'Алматинская область'; city: None → 'Абая'; street_name: 'Абая' → None; district: None → 'Карасайский'

**homoglyph_all**
- `'Aлmatы, Haypызбaй ayдahы, Шұғылa ықшam ayдahы, 340'` — city: 'Алматы' → 'Aлmatы'; street_name: 'Шұғыла' → 'Шұғылa'; district: 'Наурызбай' → 'Haypызбaй'; living_area: None → 'Шұғылa'
- `'Maңғыctay oблыcы, Aқtay қaлacы, 14 шaғыh ayдah, 25 үй'` — district: None → 'шaғыh'
- `'Aлmatы, Aбaя 10, kb 25, koд пoдъeздa 1234'` — city: 'Алматы' → 'Aлmatы'; street_name: 'Абая' → 'Aбaя'; entrance: None → '1234'
- `'Шыmkeht қ., Hұpcәt ықшamayдahы, 125 үй, 7 пәtep'` — city: 'Шымкент' → 'Шыmkeht'; street_name: 'Нұрсәт' → 'Hұpcәt'; district: 'Каратауский' → None

**wrong_layout_words**
- `'Актобе, мкр. Батыс-2, д. 13<? кв. 77'` — house: '13Б' → '13'
- `'Экибастуз, ул. "ythutnbrjd 54/1 кв.33'` — street_name: 'Энергетиков' → 'ythutnbrjd'
- `'Тараз ул Толе ,b 87 магазин Vfuyev'` — street_name: 'Толе би' → 'Толе'; district: 'Әулиеата' → None
- `"Астана Кабанбай батыра 46/3 rd 112 'nf; 9 подъезд 2"` — floor: '9' → None

**wrong_layout_all**
- `'Fқnө,t қ/? 12 i/f? 15 үq? 48 gәnth'` — street_name: '12' → '12 ш.а'
- `'Fkvfns; ek/ F,fz 10; rd 5'` — city: 'Алматы' → 'Алматыж'; house: '10' → '10ж'; district: 'Бостандыкский' → None
- `'Fkvfns? F,fz 10f/1? rd 5'` — house: '10а/1' → '10а'; living_area: None → '1'
- `'Rjritnfe? Qazpost? ek/ F,fz 89'` — living_area: None → 'Йфязщые'
