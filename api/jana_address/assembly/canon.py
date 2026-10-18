"""Spans → field values, plus the few normalizations the brief asks for.

Rules (brief, slide 8):
  province      canonical name from the table («Актюбинская область», «Алматы»)
  city          name only, no «город/пос.», Russian half
  district      without «район»
  living_area   микрорайон / ЖК name when a separate street exists
  street_type   lemma (улица, проспект, микрорайон, переулок, квартал, ...)
  street_name   EXACTLY as written (no case / typo fixes)
  house         as written incl. fraction and letter («1/2», «12а»)
  apartment / entrance / floor   number only
  landmark      as written
"""
from __future__ import annotations

import re

from ..preprocess import extract
from ..rules.grammar import RuleParse, get_lexicon

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]

_NUM_ONLY = re.compile(r"[^0-9A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі/]")


def _val(rp: RuleParse, field: str) -> str | None:
    sp = rp.spans.get(field)
    if not sp:
        return None
    return extract(rp.raw, sp.start, sp.end)


def _number(v: str | None) -> str | None:
    if v is None:
        return None
    v = _NUM_ONLY.sub("", v).strip("/")
    v = re.sub(r"(?<=\d)[йяЙЯ]$", "", v)            # glued ordinal: «2й подъезд», «3я» → 2, 3 (й/я are never apartment letters)
    return v or None


_CITY_PREFIX = re.compile(r"^(?:г|гор|город|пос|п|пгт|с|село|аул|ауыл|ст|станция)\.?\s+", re.IGNORECASE)
_CITY_SUFFIX = re.compile(r"\s+(?:қаласы|каласы|ауылы|кенті|кенти|қ|г)\.?$", re.IGNORECASE)


def _strip_settlement_words(city: str) -> str:
    """The city value is the name only (brief): «Тұрар Рысқұлов ауылы» → «Тұрар Рысқұлов», «г. Алматы» → «Алматы»."""
    out = _CITY_SUFFIX.sub("", _CITY_PREFIX.sub("", city)).strip()
    return out or city


def assemble(rp: RuleParse) -> tuple[dict, dict]:
    """Return (fields, provenance). Provenance: 'string' | 'string+table' | None (not found)."""
    fields: dict[str, str | None] = {f: None for f in FIELDS}
    prov: dict[str, str | None] = {f: None for f in FIELDS}

    # province: only the canonical table may rewrite text
    if "PROVINCE" in rp.spans and rp.meta.get("province_canonical"):
        fields["province"] = rp.meta["province_canonical"]
        prov["province"] = "string+table"
    elif rp.meta.get("province_from_city") and rp.meta["province_from_city"] in ("Астана", "Алматы", "Шымкент"):
        # city of republican significance written in the string → province == city
        fields["province"] = rp.meta["province_from_city"]
        prov["province"] = "string+table"

    conv = get_lexicon().conventions

    city = _val(rp, "CITY")
    if city:
        city = _strip_settlement_words(city)
        if rp.meta.get("city_ru_from_kk") and conv.get("city_kk_to_ru", True):
            # Kazakh-only mention («Қарағанды қаласы») → Russian name from the gazetteer (Q2).
            fields["city"] = rp.meta["city_ru_from_kk"]
            prov["city"] = "string+table"
        else:
            fields["city"] = city
            prov["city"] = "string"

    for f, key in (("district", "DISTRICT"), ("living_area", "LIVING_AREA"), ("street_name", "STREET_NAME"),
                   ("landmark", "LANDMARK")):
        v = _val(rp, key)
        if v:
            fields[f] = v
            prov[f] = "string"

    # «Әуезов ауданы» → «Ауэзовский» only when the switch is on (default: as written)
    if fields["district"] and conv.get("district_kk_to_ru") and rp.meta.get("district_known") \
            and rp.meta["district_known"] != fields["district"]:
        fields["district"] = rp.meta["district_known"]
        prov["district"] = "string+table"

    # «ЖК Томирис» → keep the type word when the switch is on (Q6; brief example says «Томирис»)
    if fields["living_area"] and conv.get("living_area_keep_type_word") and "living_area_kw_start" in rp.meta:
        sp = rp.spans["LIVING_AREA"]
        fields["living_area"] = extract(rp.raw, rp.meta["living_area_kw_start"], sp.end)

    if "STREET_TYPE" in rp.spans and rp.meta.get("street_type_lemma"):
        fields["street_type"] = rp.meta["street_type_lemma"]
        prov["street_type"] = "string+table"

    house = _val(rp, "HOUSE")
    if house:
        fields["house"] = house.replace(" ", "")
        prov["house"] = "string"
        join = conv.get("house_building_join")
        if join is not None and rp.meta.get("house_building"):
            # Q7 (team decision): «12 корпус 2» → «12/2»
            fields["house"] = f"{fields['house']}{join}{rp.meta['house_building']}"
            prov["house"] = "string+table"

    for f, key in (("apartment", "APARTMENT"), ("entrance", "ENTRANCE"), ("floor", "FLOOR")):
        v = _number(_val(rp, key))
        if v:
            fields[f] = v
            prov[f] = "string"
    return fields, prov
