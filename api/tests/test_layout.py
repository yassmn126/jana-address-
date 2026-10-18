"""Wrong keyboard layout («Алматы» typed as «Fkvfns») and Latin lookalikes («Aлмaты»): recognized, never over-applied."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.preprocess import layout_to_ru, looks_wrong_layout  # noqa: E402
from jana_address.service import parse_one  # noqa: E402


def sub(out: dict, **want) -> dict:
    return {k: out[k] for k in want}


def test_layout_map_is_same_length_and_keeps_fractions():
    assert layout_to_ru("Fkvfns? ek/ F,fz 10? rd 5", full=True) == "Алматы, ул. Абая 10, кв 5"
    assert layout_to_ru("49/2", full=True) == "49/2"
    assert layout_to_ru("F,fz") == "Абая" and layout_to_ru("Fkvfns,") == "Алматы,"      # partial: a trailing comma stays
    assert all(len(layout_to_ru(s, full=f)) == len(s) for s in ("Fkvfns? ek/ F,fz", "x,y") for f in (True, False))


@pytest.mark.parametrize("chunk,wrong", [
    ("Fkvfns", True), ("ljv", True), ("Ty,tribrfpf[crbq", True), ("{fy", True),
    ("Almaty", False), ("Aqtau", False), ("yqsham", False), ("kıreberıs", False), ("mkr", False), ("Abay", False),
])
def test_looks_wrong_layout(chunk, wrong):
    assert looks_wrong_layout(chunk) is wrong


@pytest.mark.parametrize("raw,want", [
    # the whole string in the wrong layout: the values are the intended text
    ("Fkvfns? ek/ F,fz 10? rd 5", dict(city="Алматы", street_name="Абая", house="10", apartment="5")),
    ('"rb,fcnep? ek/ "ythutnbrjd 54/1 rd/33', dict(city="Экибастуз", street_name="Энергетиков", house="54/1", apartment="33")),
    ("Isvrtyn?vrh/Cfvfk-3?ek/Fqvfesnjdf?15?rd/44", dict(city="Шымкент", living_area="Самал-3", street_name="Аймауытова", house="15", apartment="44")),
    ("5vrh 12l 34rd", dict(street_name="5", house="12", apartment="34")),
    ("Fkvfns? :R «Esentai City»? Fkm-Afhf,b 77/8? rd 45", dict(living_area="Esentai City", street_name="Аль-Фараби", house="77/8")),
    # some words in the wrong layout: recognized, values as written
    ("Алматы, ek. Абая 10, кв 5", dict(street_type="улица", street_name="Абая", house="10", apartment="5")),
    ("Мкр Алмагуль 15 rd 8 Алматы", dict(street_name="Алмагуль", house="15", apartment="8")),
    ("Шымкент, Каратауский h-y, мкр Кайтпас-2, ek/ Акжол 15", dict(district="Каратауский", living_area="Кайтпас-2", street_name="Акжол")),
    ("Fcnfyf Vәңuіkіr Tk 30 gәnth 155", dict(province="Астана", district="Есиль", house="30", apartment="155")),
    # never over-applied
    ("Almaty, Abay 10, kv 5", dict(city="Almaty", street_name="Abay", house="10", apartment="5")),
    ("Astana, Mangilik El 55, kv 12", dict(city="Astana", street_name="Mangilik El", house="55")),
    ("E 26,5, 2-ші кіреберіс, 37 пәтер, Астана", dict(street_name="E 26", house="5", apartment="37")),
    ("Aktobe, 12 microdistrict, building 8, flat 56, 5th floor", dict(street_name="12", house="8", floor="5")),
    ("Республика Казахстан. Астана қ.,mkr Balausa-4,8", dict(street_name="Balausa-4", house="8")),
    # Latin lookalikes inside Cyrillic words
    ("yл.Ceйфyллиha 10 kb 5 Aлmatы", dict(street_name="Ceйфyллиha", house="10", apartment="5", city="Aлmatы")),
])
def test_layout_cases(raw, want):
    assert sub(parse_one("x", raw), **want) == want
