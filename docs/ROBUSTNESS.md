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
| commas_to_spaces | 98.98 | -1.02 | 98.3 % | 7 |
| bom_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| country_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| postcode_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| phone_suffix | 100.00 | +0.00 | 100.0 % | 0 |
| note_suffix | 99.96 | -0.04 | 99.8 % | 1 |
| trailing_dot | 99.96 | -0.04 | 99.8 % | 1 |
| kv_latin | 100.00 | +0.00 | 100.0 % | 0 |
| kv_glued | 100.00 | +0.00 | 100.0 % | 0 |
| semicolons | 100.00 | +0.00 | 100.0 % | 0 |
| homoglyph_30 | 100.00 | +0.00 | 99.8 % | 1 |
| homoglyph_all | 99.47 | -0.53 | 97.6 % | 10 |
| wrong_layout_words | 98.42 | -1.58 | 93.8 % | 26 |
| wrong_layout_all | 99.58 | -0.42 | 98.3 % | 7 |

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
- `'Нур-Султан район Алматы пр. Тауелсиздик 34'` — province: 'Астана' → None; city: 'Нур-Султан' → 'Астана'; district: 'Алматы' → 'Нур-Султан'
- `'Алматы Абая 10 5'` — street_name: 'Абая' → 'Абая 10'; house: '10' → '5'; apartment: '5' → None; district: 'Бостандыкский' → None
- `'ЖК Хайвил Астана Кабанбай батыра 46/3 кв 112'` — province: None → 'Астана'; city: None → 'Астана'; district: None → 'Есиль'; living_area: 'Хайвил Астана' → 'Хайвил'
- `'г Алматы город Алматы ул Абая 10'` — district: 'Бостандыкский' → 'Алматы'

**note_suffix**
- `'Абая, позвонить заранее'` — street_name: 'Абая' → None; living_area: None → 'Абая'

**trailing_dot**
- `'Абая.'` — province: None → 'Алматинская область'; city: None → 'Абая'; street_name: 'Абая' → None; district: None → 'Карасайский'

**homoglyph_30**
- `'Шымkенt, до bостpебоbания'` — city: 'Шымкент' → 'Шымkенt'; living_area: None → 'до bостpебоbания'

**homoglyph_all**
- `'Aлmatы, Haypызбaй ayдahы, Шұғылa ықшam ayдahы, 340'` — city: 'Алматы' → 'Aлmatы'; street_name: 'Шұғыла' → 'Шұғылa'; district: 'Наурызбай' → 'Haypызбaй'; living_area: None → 'Шұғылa'
- `'Aлmatы, Aбaя 10, kb 25, koд пoдъeздa 1234'` — city: 'Алматы' → 'Aлmatы'; street_name: 'Абая' → 'Aбaя'; entrance: None → '1234'
- `'Шыmkeht қ., Hұpcәt ықшamayдahы, 125 үй, 7 пәtep'` — city: 'Шымкент' → 'Шыmkeht'; street_name: 'Нұрсәт' → 'Hұpcәt'; district: 'Каратауский' → None
- `'Aлmatы Aлmatы Aбaя 10 Aбaя 10 kb 5'` — city: 'Алматы' → 'Aлmatы'; street_name: 'Абая' → 'Aбaя'; living_area: 'Абая 10' → None

**wrong_layout_words**
- `'Алматинская обл, Енбекшиказахский h-y? c/ Шелек, ул. Жибек жолы 112'` — city: 'Шелек' → None; living_area: None → 'c/ Шелек'
- `"fcnfyf кабанбай батыра 46/3 кв 112 'nf; 9"` — city: 'астана' → 'fcnfyf'; floor: '9' → None
- `'Frnj,t? мкр. Батыс-2, д. 13<? кв. 77'` — city: 'Актобе' → 'Frnj,t'; house: '13Б' → '13'
- `'Шымкент, Каратауский р-н, vrh/ Кайтпас-2, ул. Fr;jk 15'` — street_name: 'Акжол' → 'Fr'

**wrong_layout_all**
- `';/v Ifyshfr? ek/ :fyrj;f ,fnshf 15'` — living_area: 'Шанырак' → 'ж.м Шанырак'
- `'Fқnө,t қ/? 12 i/f? 15 үq? 48 gәnth'` — street_name: '12' → '12 ш.а'
- `'Fkvfns; ek/ F,fz 10; rd 5'` — city: 'Алматы' → 'Алматыж'; house: '10' → '10ж'; district: 'Бостандыкский' → None
- `'Fkvfns? F,fz 10f/1? rd 5'` — house: '10а/1' → '10а'; living_area: None → '1'
