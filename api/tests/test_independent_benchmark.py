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
])
@pytest.mark.parametrize("parse", [rules, hybrid], ids=["rules", "hybrid"])
def test_independent_benchmark_cases(parse, raw, want):
    assert sub(parse(raw), **want) == want


def test_validator_accepts_convention_exceptions():
    from training.validate_labels import _city_is_kk_mapping, _house_is_join, validate_record

    assert _city_is_kk_mapping("Ақтөбе қаласы, Әбілқайыр хан даңғылы 52", "Актобе")
    assert _house_is_join("Қостанай, Абай даңғылы 42 үй 2 корпус", "42/2")
    assert _house_is_join("Almaty, Timiryazev st 42 korpus 15, kv 101", "42/15")
    assert not _house_is_join("Абая 12 кв 2", "12/2")
    fields = {f: None for f in F + ["landmark"]}
    fields.update(street_type="улица", street_name="Ш. Уалиханова", house="98/1")
    assert not [i for i in validate_record("Семей, ул. Ш. Уалиханова 98/1", fields) if i["severity"] == "error"]
