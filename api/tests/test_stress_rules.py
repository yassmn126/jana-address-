"""Rule-grammar behaviours found by the adversarial stress set (data/stress_handmade.json). Rules only —
these must hold with or without a model, because the arbiter falls back to them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.preprocess import clean  # noqa: E402
from jana_address.service import parse_one  # noqa: E402
from jana_address.tokenizer import tokenize  # noqa: E402


def rules(raw: str) -> dict:
    return parse_one("x", raw, mode="rules")


def test_tokenizer_does_not_glue_letter_dash_number_after_a_digit():
    assert [t.text for t in tokenize(clean("Абилкайыр хана 52а-18"))] == ["Абилкайыр", "хана", "52а", "-", "18"]
    assert [t.text for t in tokenize(clean("Самал-2, Е-10, Нур-Султан"))][::2] == ["Самал-2", "Е-10", "Нур-Султан"]


@pytest.mark.parametrize("raw,house", [
    ("Алматы, Тауелсиздик 34 блок А кв 12", "34/А"),              # building part after a fallback house
    ("Алматы, Аль-Фараби 19, БЦ Нурлы Тау, блок 4В", "19/4В"),    # … and after a landmark
    ("Алматы, Абая 10 Б, кв 5", "10Б"),                            # spaced house letter
])
def test_house_joins(raw, house):
    assert rules(raw)["house"] == house


def test_one_letter_street_type_needs_a_dot():
    out = rules("уточнить у получателя")
    assert out["street_name"] is None and out["street_type"] is None and out["confidence"] <= 0.1
    assert rules("у. Абая 10")["street_type"] == "улица"


def test_door_code_is_not_an_entrance_or_apartment():
    out = rules("Алматы, Абая 10, кв 25, код подъезда 1234")
    assert out["apartment"] == "25" and out["entrance"] is None
    out = rules("Алматы, Абая 10, домофон 25, кв 7")
    assert out["apartment"] == "7" and out["entrance"] is None


def test_floor_of_total_and_glued_ordinals():
    assert rules("Астана, Туран 37/9, кв 45, 3/9 этаж")["floor"] == "3"
    out = rules("Караганда, Гоголя 12, кв 7, 2й подъезд, 4й этаж")
    assert (out["entrance"], out["floor"]) == ("2", "4")


def test_street_then_separator_then_number():
    out = rules("Алматы,Абая,10,5")
    assert out["street_name"] == "Абая" and out["house"] == "10"


def test_details_before_street():
    out = rules("3 подъезд 5 этаж 25 кв Абая 10 Алматы")
    assert (out["entrance"], out["floor"], out["apartment"], out["house"], out["street_name"]) == ("3", "5", "25", "10", "Абая")
    out = rules("кв 5, дом 10, ул. Абая, Алматы")
    assert (out["apartment"], out["house"], out["street_name"], out["city"]) == ("5", "10", "Абая", "Алматы")


@pytest.mark.parametrize("raw,street,house", [
    ("Шымкент мкр Нурсат 2 дом 14 кв 3", "Нурсат 2", "14"),       # name number + keyword-bound house
    ("Алматы мкр Аксай 1а 12 кв 5", "Аксай 1а", "12"),            # two numbers in a row
    ("Алматы, мкр Алмагуль 9а, кв 47", "Алмагуль", "9а"),         # nothing else can be the house
    ("город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70", "Аксай 3а", "59"),   # the brief's sample still holds
    ("Алматы Жандосова58кв12", "Жандосова", "58"),               # keyword glued on both sides
    ("Қарағанды қаласы, Гоголь көшесі 34, 12 пәтер", "Гоголь", "34"),   # Kazakh name-before-type is not «ул. 34»
    ("Астана, ул. Е 10, д. 17", "Е 10", "17"),                    # trailing number before a keyword-bound house
    ("Рудный, ул. 50 лет Октября 81, кв. 3", "50 лет Октября", "81"),
    ("Астана, ул. 187, д 12", "187", "12"),
    ("5мкр 12д 34кв", "5", "12"),                                  # all-glued string: «12д» is «12 дом»
])
def test_microdistrict_numbers_and_glued_keywords(raw, street, house):
    out = rules(raw)
    assert (out["street_name"], out["house"]) == (street, house)


def test_apartment_from_glued_both_sides():
    assert rules("Алматы Жандосова58кв12")["apartment"] == "12"


def test_settlement_abbreviation_without_number_stays_a_city():
    assert rules("Карагандинская обл, Бухар-Жырауский р-н, п. Ботакара, ул. Абая 22")["city"] == "Ботакара"
    assert rules("Шымкент, Тауке хана 23, рядом с мечетью")["city"] == "Шымкент"


def test_typo_city_kept_as_written_with_province_from_match():
    out = rules("Алмты, ул Абая 15")
    assert out["city"] == "Алмты" and out["province"] == "Алматы"
    out = rules("Павладар, ул. Естая 99")
    assert out["city"] == "Павладар" and out["province"] == "Павлодарская область"


@pytest.mark.parametrize("raw,city", [
    ("Карасайский район, Шамалган, ул. Ленина 2", "Шамалган"),          # a bare OSM village between separators
    ("Казахстан, Алматы, ул. Абая 10", "Алматы"),                       # villages named «Казахстан» exist: not the city
    ("Отеген батыр, ул. Титова 12", "Отеген батыр"),                     # Russian spelling stays as written
    ("Түлкібас ауданы, Тұрар Рысқұлов ауылы, Жібек жолы көшесі 12", "Тұрар Рысқұлов"),   # no «ауылы» in the value
])
def test_village_cities(raw, city):
    assert parse_one("x", raw)["city"] == city


@pytest.mark.parametrize("raw,province,street,house", [
    ("усть-каменогорск алматинская д.8а", None, "алматинская", "8а"),     # region adjective before a house = street
    ("Актюбинская 12, кв 3", None, "Актюбинская", "12"),
    ("Актюбинская обл, Хромтау, Абая 5", "Актюбинская область", "Абая", "5"),
    ("АСТАНА, УЛ. КАРАГАНДИНСКАЯ 119", "Астана", "КАРАГАНДИНСКАЯ", "119"),
])
def test_region_named_streets(raw, province, street, house):
    out = rules(raw)
    assert (out["street_name"], out["house"]) == (street, house)
    if province:
        assert out["province"] == province


@pytest.mark.parametrize("raw,province", [
    ("Костанай обл, г. Рудный, ул. Ленина 112 кв 23", "Костанайская область"),    # regional centre + «обл» (found by Claude)
    ("Актобе область, Хромтау, Абая 5", "Актюбинская область"),
    ("Ақтөбе обл., Алга, Абая 1", "Актюбинская область"),
    ("Алматы обл, Талгар, Кунаева 75", "Алматинская область"),                    # NOT the city Алматы
])
def test_colloquial_province_names(raw, province):
    out = parse_one("x", raw, explain=True)
    assert out["province"] == province and out["provenance"]["province"] == "string+table"


def test_findings_of_the_claude_cross_check():
    assert parse_one("x", "Байконыр ул Абая 5")["city"] == "Байконыр"           # look-alike, not the Kazakh form
    assert parse_one("x", "Орал қаласы, Абай 5")["city"] == "Уральск"           # literal Kazakh form of a known city
    assert parse_one("x", "Алматы, ул. Абая 10")["city"] == "Алматы"
    assert parse_one("x", "Шымкент, Тауке хана 23, рядом с мечетью")["landmark"] == "мечетью"
    assert parse_one("x", "Караганда, Бухар жырау 49, кв 3, звонить за час")["landmark"] is None
    out = parse_one("x", "Алмты, ул Абая 15", explain=True)
    assert out["province"] == "Алматы" and out["provenance"]["province"] == "string+table"
