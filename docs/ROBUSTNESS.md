# Robustness report — mode hybrid, 376 hand-labeled records

Original strings: jury score **99.96**

| transformation | jury score | Δ | consistency | changed |
|---|---|---|---|---|
| lower | 99.96 | +0.00 | 100.0 % | 0 |
| upper | 99.96 | +0.00 | 100.0 % | 0 |
| nbsp | 99.96 | +0.00 | 100.0 % | 0 |
| zwsp_after_comma | 99.96 | +0.00 | 100.0 % | 0 |
| double_space | 99.96 | +0.00 | 100.0 % | 0 |
| tab_newline | 99.96 | +0.00 | 100.0 % | 0 |
| no_space_after_comma | 99.92 | -0.04 | 99.7 % | 1 |
| commas_to_spaces | 98.31 | -1.65 | 98.4 % | 6 |
| bom_prefix | 99.96 | +0.00 | 100.0 % | 0 |
| country_prefix | 99.96 | +0.00 | 100.0 % | 0 |
| postcode_prefix | 99.96 | +0.00 | 100.0 % | 0 |
| phone_suffix | 99.88 | -0.08 | 99.5 % | 2 |
| note_suffix | 99.88 | -0.08 | 99.5 % | 2 |
| trailing_dot | 99.92 | -0.04 | 99.7 % | 1 |
| kv_latin | 99.96 | +0.00 | 100.0 % | 0 |
| kv_glued | 99.96 | +0.00 | 100.0 % | 0 |
| semicolons | 99.96 | +0.00 | 100.0 % | 0 |

## Per category (original strings)

| category | n | jury score | all-4 per record |
|---|---|---|---|
| glued | 12 | 100.00 | 100.0 % |
| kazakh | 33 | 100.00 | 100.0 % |
| latin | 16 | 100.00 | 100.0 % |
| microdistrict | 77 | 100.00 | 100.0 % |
| fraction_house | 43 | 100.00 | 100.0 % |
| letter_house | 26 | 100.00 | 100.0 % |
| dash_apartment | 14 | 100.00 | 100.0 % |
| landmark | 19 | 100.00 | 100.0 % |
| living_area | 26 | 100.00 | 100.0 % |
| district_in_string | 52 | 100.00 | 100.0 % |
| province_in_string | 37 | 100.00 | 100.0 % |
| no_city | 27 | 100.00 | 100.0 % |
| no_street_type | 81 | 100.00 | 100.0 % |
| 3plus_details | 46 | 100.00 | 100.0 % |
| noise_words | 12 | 100.00 | 100.0 % |

## Inconsistent examples (original → transformed)

**no_space_after_comma**
- `'Алматы ул. Жибек Жолы 50,2 этаж,бутик 14'` — apartment: None → '14'

**commas_to_spaces**
- `'Нур-Султан район Алматы пр. Тауелсиздик 34'` — province: 'Астана' → 'Алматы'; city: 'Нур-Султан' → 'Алматы'; district: 'Алматы' → 'Нур-Султан'
- `'Караганда Майкудук 12 мкр 18 дом кв 54'` — street_name: '12' → 'Майкудук 12'; living_area: 'Майкудук' → None
- `'Алматы Абая 10 5'` — street_name: 'Абая' → 'Абая 10'; house: '10' → '5'; apartment: '5' → None; district: 'Бостандыкский' → None
- `'ЖК Хайвил Астана Кабанбай батыра 46/3 кв 112'` — province: None → 'Астана'; city: None → 'Астана'; district: None → 'Есиль'; living_area: 'Хайвил Астана' → 'Хайвил'

**phone_suffix**
- `'Алматы ул. Жибек Жолы 50, 2 этаж, бутик 14, тел 87011234567'` — apartment: None → '14'
- `'Абая, тел 87011234567'` — street_name: 'Абая' → None

**note_suffix**
- `'Алматы ул. Жибек Жолы 50, 2 этаж, бутик 14, позвонить заранее'` — apartment: None → '14'
- `'Абая, позвонить заранее'` — street_name: 'Абая' → None

**trailing_dot**
- `'Абая.'` — street_name: 'Абая' → None
