# Robustness report — mode hybrid, 416 hand-labeled records

Original strings: jury score **99.92**

| transformation | jury score | Δ | consistency | changed |
|---|---|---|---|---|
| lower | 99.92 | +0.00 | 100.0 % | 0 |
| upper | 99.92 | +0.00 | 100.0 % | 0 |
| nbsp | 99.92 | +0.00 | 100.0 % | 0 |
| zwsp_after_comma | 99.92 | +0.00 | 100.0 % | 0 |
| double_space | 99.92 | +0.00 | 100.0 % | 0 |
| tab_newline | 99.92 | +0.00 | 100.0 % | 0 |
| no_space_after_comma | 99.89 | -0.03 | 99.8 % | 1 |
| commas_to_spaces | 98.36 | -1.56 | 98.1 % | 8 |
| bom_prefix | 99.92 | +0.00 | 100.0 % | 0 |
| country_prefix | 99.92 | +0.00 | 99.8 % | 1 |
| postcode_prefix | 99.92 | +0.00 | 100.0 % | 0 |
| phone_suffix | 99.86 | -0.06 | 99.5 % | 2 |
| note_suffix | 99.86 | -0.06 | 99.5 % | 2 |
| trailing_dot | 99.89 | -0.03 | 99.8 % | 1 |
| kv_latin | 99.92 | +0.00 | 100.0 % | 0 |
| kv_glued | 99.92 | +0.00 | 100.0 % | 0 |
| semicolons | 99.92 | +0.00 | 100.0 % | 0 |

## Per category (original strings)

| category | n | jury score | all-4 per record |
|---|---|---|---|
| glued | 12 | 100.00 | 100.0 % |
| kazakh | 39 | 99.62 | 97.4 % |
| latin | 17 | 100.00 | 100.0 % |
| microdistrict | 81 | 100.00 | 100.0 % |
| fraction_house | 44 | 100.00 | 100.0 % |
| letter_house | 26 | 100.00 | 100.0 % |
| dash_apartment | 14 | 100.00 | 100.0 % |
| landmark | 20 | 100.00 | 100.0 % |
| living_area | 28 | 100.00 | 100.0 % |
| district_in_string | 80 | 99.80 | 98.8 % |
| province_in_string | 55 | 100.00 | 100.0 % |
| no_city | 27 | 100.00 | 100.0 % |
| no_street_type | 85 | 100.00 | 100.0 % |
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

**country_prefix**
- `'Казахстан, дом 10 кв 5'` — city: None → 'Казахстан'; district: None → 'Енбекшиказахский'

**phone_suffix**
- `'Алматы ул. Жибек Жолы 50, 2 этаж, бутик 14, тел 87011234567'` — apartment: None → '14'
- `'Абая, тел 87011234567'` — province: None → 'Алматинская область'; city: None → 'Абая'; street_name: 'Абая' → None; district: None → 'Карасайский'

**note_suffix**
- `'Алматы ул. Жибек Жолы 50, 2 этаж, бутик 14, позвонить заранее'` — apartment: None → '14'
- `'Абая, позвонить заранее'` — province: None → 'Алматинская область'; city: None → 'Абая'; street_name: 'Абая' → None; district: None → 'Карасайский'

**trailing_dot**
- `'Абая.'` — street_name: 'Абая' → None
