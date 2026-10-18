"""Behaviours found by the independent benchmark (data/claude_generated.json: 526 addresses written and labeled by
Claude Opus 5.5, not by the author of the grammar). Rules-only cases must hold without a model; hybrid cases check
the arbiter keeps the rule reading when the tagger has never seen the keyword."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from jana_address.service import parse_one  # noqa: E402

F = ["province", "city", "district", "living_area", "street_type", "street_name", "house", "apartment", "entrance", "floor"]


def rules(raw: str) -> dict:
    return parse_one("x", raw, mode="rules")


def hybrid(raw: str) -> dict:
    return parse_one("x", raw)


def sub(out: dict, **want) -> dict:
    return {k: out[k] for k in want}


@pytest.mark.parametrize("raw,want", [
    # Latin / transliterated Kazakh keywords
    ("Almaty qalasy, Aksai-5 shagyn audany 12 uy 34 pater",
     dict(city="Almaty", street_type="микрорайон", street_name="Aksai-5", house="12", apartment="34")),
    ("Taraz, Abay dangyly 125, kv 8", dict(street_type="проспект", street_name="Abay", house="125", apartment="8")),
    ("Astana, Esil district, Turan avenue 18, apt 45", dict(district="Esil", street_name="Turan", house="18", apartment="45")),
    ("Atyrau, Satpayev str 19a, kv.7, 3 etazh", dict(street_name="Satpayev", house="19a", apartment="7", floor="3")),
    ("Pavlodar, Lomova street 45, kv 112, podezd 4", dict(street_name="Lomova", house="45", entrance="4")),
    ("Aktobe, 12 microdistrict, building 8, flat 56, 5th floor", dict(street_name="12", house="8", apartment="56", floor="5")),
    ("Almaty obl, Talgar rayon, s. Panfilovo, ul. Lenina 14",
     dict(province="Алматинская область", city="Panfilovo", district="Talgar", street_name="Lenina")),
    ("Astana city, Belkaragay koshesi 6, kv 203", dict(city="Astana", street_name="Belkaragay", house="6")),
    ("Qūlsary qalasy, mkr Geologorazvedchik, 7dom", dict(city="Qūlsary", street_name="Geologorazvedchik", house="7")),
    # Kazakh written without Kazakh letters, «кеңсе» = office
    ("Петропавл қаласы, Жамбыл кошесі 150, 9 патер", dict(street_name="Жамбыл", house="150", apartment="9")),
    ("Тараз қ., Байзақ батыр даңғылы 158, 3-ші қабат, 12 кеңсе", dict(apartment="12", floor="3")),
    # корпус after the house in Kazakh order; a spaced slash separates fields
    ("Қостанай, Абай даңғылы 42 үй 2 корпус, 17 пәтер", dict(house="42/2", entrance=None, apartment="17")),
    ("кызылорда / ул. желтоксан 18 / кв 3 / 2 этаж / т. 8 777 123 45 67", dict(house="18", apartment="3", floor="2")),
    # glued keyword + name; glued keyword-first numbers
    ("Уральск мкрСтроитель д11кв47", dict(street_name="Строитель", house="11", apartment="47")),
    # named microdistrict vs town; living area + numbered микрорайон (gazetteer)
    ("Аксай 4 мкр 23дом 45кв", dict(city=None, street_name="Аксай 4", house="23")),
    ("Караганда Пришахтинск 22 мкр 4дом кв 17", dict(living_area="Пришахтинск", street_name="22", house="4")),
    ("Актау 14 мкр 59 дом", dict(city="Актау", street_name="14", house="59")),
    # districts: two-word name after the keyword; a bare chunk that is a district of the city
    ("г. Караганда, р-н Казыбек би, пр. Бухар-Жырау, д. 59/1", dict(district="Казыбек би")),
    ("Астана, Сарыарка, ул. Сейфуллина 31 кв35", dict(district="Сарыарка", street_name="Сейфуллина")),
    # «city» inside a name; СТ = садовое товарищество
    ("Алматы, ЖК «Esentai City», Аль-Фараби 77/8, кв 45", dict(living_area="Esentai City")),
    ("Костанай, СТ Строитель, 3 линия, участок 45", dict(living_area="Строитель", house="45")),
    # residuals of the untouched held-out set (data/claude_heldout.json, round 2)
    ("Карагнда Пришахтинск 21 мкр 3 дом 44 кв", dict(living_area="Пришахтинск", street_name="21", house="3")),
    ("Актау 9 микрараён 12б дом кв 3", dict(street_type="микрорайон", street_name="9", house="12б", apartment="3")),
    ("Almaty obl., Enbekshikazakh r-n, s. Shelek, ul. Kunaeva 56", dict(district="Enbekshikazakh", city="Shelek")),
    ("Қостанай қаласы, Байтурсынов көшсі 71, 14 патер", dict(street_name="Байтурсынов", house="71", apartment="14")),
    ("Караганда пр.Бухар-Жырау55/3кв.12 4 этаж", dict(street_name="Бухар-Жырау", house="55/3", apartment="12", floor="4")),
    ("Almaty, Dostyk ave 134 korpus 2 kv 45", dict(street_name="Dostyk", house="134/2", apartment="45")),
    # residuals of the untouched set #3 (data/claude_heldout3.json)
    ("Пришахтинск 23 мкр 11 дом кв 5", dict(city=None, living_area="Пришахтинск", street_name="23", house="11")),
    ("Астана, левый берег, ЖК Хайвил, Ахмет Байтурсынулы 9 блок С, 6 подьезд, кв 501",
     dict(living_area="Хайвил", street_name="Ахмет Байтурсынулы", house="9/С")),
    ("Нурсултан, Туранн 37/9, 3 подьезд 11 этаж кв 87", dict(province="Астана", city="Нурсултан", house="37/9")),
    ("Акмолинсая обл, Целиноградский р-он, аул Косшы, ул Республики 9 кв 34",
     dict(province="Акмолинская область", district="Целиноградский", city="Косшы")),
    ("Астана қаласы, Байқоңыр ауданы, Ш. Құдайбердіұлы даңғылы 25, 3 блок, 87 пәтер", dict(house="25/3", apartment="87")),
    ("Ust-Kamenogorsk, Kazakstan stret 64, apt 101, 8 etazh", dict(street_name="Kazakstan", house="64", floor="8")),
    ("Алматинская обл,Талгарскийр-н с.Бесагаш ул.Жандосова 25", dict(district="Талгарский", city="Бесагаш", street_name="Жандосова")),
    ("с. Акмол, ул. Малика Габдуллина, д. 21-а", dict(street_name="Малика Габдуллина", house="21а")),
    ("Караганда,\\u00a0ул.\\u00a0Ерубаева\\u00a050а,\\u00a0кв\\u00a018", dict(street_name="Ерубаева", house="50а", apartment="18")),
])
@pytest.mark.parametrize("parse", [rules, hybrid], ids=["rules", "hybrid"])
def test_independent_benchmark_cases(parse, raw, want):
    assert sub(parse(raw), **want) == want


@pytest.mark.parametrize("raw,want", [
    # the adversarial HARD set (data/claude_hard.json): what breaks parsers, written the way it happens
    ("Алматы қаласындағы Сәтбаев көшесінде 5 үй, 12-ші пәтер, 3-ші қабат", dict(city="Алматы", street_name="Сәтбаев", house="5", apartment="12", floor="3")),
    ("Астана қ., Абай даңғылындағы 10 үй, 1-інші кіреберіс, 7-ші қабат, 28 пәтер", dict(street_name="Абай", house="10", entrance="1", floor="7")),
    ("Ақтөбе қаласы, 12-ші ықшам ауданда 26 үй, 4-ші кіреберіс, 55 пәтер", dict(street_name="12", house="26", entrance="4")),
    ("Almaty qalasy, Abai daŋğyly 10, 25 päter", dict(city="Almaty", street_name="Abai", house="10", apartment="25")),
    ("Aqtau qalasy, 12 şağyn audan, 34 üi, 56 päter, 2 kireberis", dict(street_name="12", house="34", apartment="56", entrance="2")),
    ("Astana, Turan аvenue 37, kv 15, 9 etаzh", dict(street_name="Turan", house="37", apartment="15", floor="9")),      # Cyrillic «а» inside Latin
    ("Almaty (Alma-Ata), Abay avenue 52, kv 14", dict(city="Almaty", province="Алматы", street_name="Abay")),
    ("Караганда, ул. Ерубаева (бывш. Ленина) 33, кв 14", dict(street_name="Ерубаева", house="33")),
    ("Нур-Султан (Астана), пр. Кабанбай батыра 46, кв 120", dict(city="Нур-Султан", province="Астана")),
    ("Алматы, ул. Абая 10, нет, 12, кв 5", dict(house="12", apartment="5")),
    ("Тараз, ул. Сулейменова 8 кв 11 — исправляю: кв 12, 3 подъезд", dict(house="8", apartment="12", entrance="3")),
    ("Қарағанды қ., Бұқар жырау даңғылы 49/2, 61 пәтер, жоқ қателестім 16 пәтер", dict(house="49/2", apartment="16")),
    ("Алматинская обл, Карасайский р-н, СТ Строитель, 5 линия, участок 23", dict(living_area="Строитель", street_name="5", house="23")),
    ("Усть-Каменогорск, дачный массив Ульбинский, 7 аллея, уч45", dict(living_area="Ульбинский", street_name="7", house="45")),
    ("Қарағанды облысы, Теміртау, Металлург саяжайы, 6 көше 15 үй", dict(living_area="Металлург", street_name="6", house="15")),
    ("ВКО г Семей СОТ Иртыш-1 линия 4 уч 38 вход с торца", dict(living_area="Иртыш-1", street_name="4", house="38")),
    ("Актобе, дачи СТ Геолог 3линия 14участок", dict(living_area="Геолог", street_name="3", house="14")),
    ("ТолебиДом202кв17", dict(street_name="Толеби", house="202", apartment="17")),
    ("Астана,ЖКХайвил,Акмешит17,кв205", dict(living_area="Хайвил", street_name="Акмешит", house="17")),
    ("Актау/14 мкр/59/12", dict(street_name="14", house="59", apartment="12")),
    ("Костанай/Аль-Фараби/115 корпус 2/кв 15", dict(street_name="Аль-Фараби", house="115/2", apartment="15")),
    ("г.астана/ул.сарайшык 34/1/кв 88", dict(street_name="сарайшык", house="34/1", apartment="88")),
    ("Павлодар — Кутузова 204 — 3 подъезд — 5 этаж — кв 47 — тел. 8 701 555 33 22", dict(city="Павлодар", street_name="Кутузова", house="204", entrance="3", floor="5", apartment="47")),
    ("050000 Алматы а/я 15", dict(city="Алматы", street_name=None, house=None)),
    ("Алматы, ТРЦ Mega Alma-Ata, Розыбакиева 247а, бутик 214, 2 этаж", dict(street_name="Розыбакиева", house="247а", apartment="214", floor="2")),
    ("кв 27 дом 5 мкр Жулдыз-1 Турксибский район", dict(district="Турксибский", street_name="Жулдыз-1", house="5", apartment="27")),
    ("8 701 555 23 41, 2 подъезд, кв 60, дом 17 по проспекту Назарбаева, Павлодар", dict(street_name="Назарбаева", house="17", entrance="2", apartment="60")),
    ("Атырау, мкр Жилгородок, улица Нурпеисовой дом номер 18а", dict(street_name="Нурпеисовой", house="18а")),
    ("Шымкент мкр Нурсат частный дом улица Жибек жолы 45 третий дом от угла", dict(living_area="Нурсат", street_name="Жибек жолы", house="45")),
    ("г. Алматы,Ауэзовский район, пер. 2-ой, 13б, кв.303", dict(street_name="2-ой", house="13б", apartment="303")),
    ("Алмты, Ауэзовкий р-н, мкр Аксай-4, д 25, квратира 18, подьзд 2", dict(street_name="Аксай-4", house="25", apartment="18", entrance="2")),
    ("СТЕПНОГОРСК 1 МКР 52 ДОМ 3 ПОДЪЕЗД 12 КВ", dict(city="СТЕПНОГОРСК", street_name="1", house="52", entrance="3", apartment="12")),
    # the grammar alone cannot know these; the tagger ⊕ gazetteer can
    ("Солнечный 15мкр 53/10 д. квартира 317", dict(city="Солнечный", living_area=None, street_name="15", house="53/10")),
])
def test_hybrid_only_cases(raw, want):
    assert sub(hybrid(raw), **want) == want


def test_validator_accepts_convention_exceptions():
    from training.validate_labels import _city_is_kk_mapping, _house_is_join, validate_record

    assert _city_is_kk_mapping("Ақтөбе қаласы, Әбілқайыр хан даңғылы 52", "Актобе")
    assert _house_is_join("Қостанай, Абай даңғылы 42 үй 2 корпус", "42/2")
    assert _house_is_join("Almaty, Timiryazev st 42 korpus 15, kv 101", "42/15")
    assert not _house_is_join("Абая 12 кв 2", "12/2")
    fields = {f: None for f in F + ["landmark"]}
    fields.update(street_type="улица", street_name="Ш. Уалиханова", house="98/1")
    assert not [i for i in validate_record("Семей, ул. Ш. Уалиханова 98/1", fields) if i["severity"] == "error"]
