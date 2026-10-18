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
| commas_to_spaces | 99.08 | -0.92 | 98.3 % | 7 |
| bom_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| country_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| postcode_prefix | 100.00 | +0.00 | 100.0 % | 0 |
| phone_suffix | 100.00 | +0.00 | 100.0 % | 0 |
| note_suffix | 99.96 | -0.04 | 99.8 % | 1 |
| trailing_dot | 99.96 | -0.04 | 99.8 % | 1 |
| kv_latin | 100.00 | +0.00 | 100.0 % | 0 |
| kv_glued | 100.00 | +0.00 | 100.0 % | 0 |
| semicolons | 100.00 | +0.00 | 100.0 % | 0 |

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
| 3plus_details | 46 | 100.00 | 100.0 % |
| noise_words | 12 | 100.00 | 100.0 % |

## Inconsistent examples (original → transformed)

**commas_to_spaces**
- `'Нур-Султан район Алматы пр. Тауелсиздик 34'` — city: 'Нур-Султан' → 'Астана'; district: 'Алматы' → 'Нур-Султан'
- `'Алматы Абая 10 5'` — street_name: 'Абая' → 'Абая 10'; house: '10' → '5'; apartment: '5' → None; district: 'Бостандыкский' → None
- `'ЖК Хайвил Астана Кабанбай батыра 46/3 кв 112'` — province: None → 'Астана'; city: None → 'Астана'; district: None → 'Есиль'; living_area: 'Хайвил Астана' → 'Хайвил'
- `'г Алматы город Алматы ул Абая 10'` — district: 'Бостандыкский' → 'Алматы'

**note_suffix**
- `'Абая, позвонить заранее'` — street_name: 'Абая' → None; living_area: None → 'Абая'

**trailing_dot**
- `'Абая.'` — province: None → 'Алматинская область'; city: None → 'Абая'; street_name: 'Абая' → None; district: None → 'Карасайский'
