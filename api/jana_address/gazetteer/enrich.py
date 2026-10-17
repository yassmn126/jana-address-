"""Gazetteer enrichment — fills NULL fields only, never overwrites what the string says.

Backed by `gazetteer.osm.Gazetteer`: api/artifacts/gazetteer.sqlite (OSM Kazakhstan, built by
data_pipeline/build_gazetteer.py) with the YAML seed (rules/places_seed.yaml) as fallback.

Order (ARCHITECTURE.md §3.6):
  0  Kazakh-spelled city → Russian name («Қарағанды», «Орал» → «Караганда», «Уральск»)  [convention Q2]
  1  city ← city district («Турксибский район, …» → Алматы)
  2  province ← city (KATO/OSM hierarchy; republican cities are their own province)
  3  living_area ← a leftover word chunk that IS a known микрорайон/ЖК of that city (text is in the string)
  4  district ← микрорайон / ЖК polygon → street + house point (streets crossing districts) →
               street majority → oblast district (аудан) of a town / village
  5  street_type ← the street's type in OSM («Кулманова 133» → улица) — not scored, shown at the defense

Provenance values: 'string', 'string+table', 'string+gazetteer:osm', 'gazetteer:osm', 'gazetteer:osm:house',
'gazetteer:osm:street', 'gazetteer:osm:street_majority', 'gazetteer:seed'. The jury sees them with ?explain=1.
"""
from __future__ import annotations

import re

from ..preprocess import extract
from ..rules.grammar import RuleParse, Span, get_lexicon
from .osm import Gazetteer, get_gazetteer

_KK_LETTERS = re.compile(r"[әғқңөұүһіӘҒҚҢӨҰҮҺІ]")


def enrich(fields: dict, prov: dict, rp: RuleParse, gz: Gazetteer | None = None) -> None:
    """Mutates fields/prov in place. Only fills None values (and maps a Kazakh city spelling, step 0)."""
    gz = gz or get_gazetteer()
    conv = get_lexicon().conventions

    # 0. «Ақтөбе» / «Орал» written → Russian name. The seed already did this inside the parser for big cities.
    if fields["city"] and prov.get("city") == "string" and conv.get("city_kk_to_ru", True):
        ru, src = gz.city_ru(fields["city"])
        if ru and (_KK_LETTERS.search(fields["city"]) or ru.lower() != fields["city"].lower()):
            fields["city"], prov["city"] = ru, f"string+{src}"

    # 1. city from a city district
    if fields["city"] is None and fields["district"]:
        c, src = gz.city_of_district(fields["district"])
        if c is None and rp.meta.get("city_of_district"):
            c, src = rp.meta["city_of_district"], "gazetteer:seed"
        if c:
            fields["city"], prov["city"] = c, src

    # 2. province from city
    if fields["province"] is None and fields["city"]:
        p, src = gz.province_of_city(fields["city"])
        if p:
            fields["province"], prov["province"] = p, src

    # 3. a free word chunk that is a known микрорайон / ЖК of this city → living_area (it is IN the string)
    if fields["living_area"] is None and fields["city"]:
        for ch in rp.meta.get("leftover_chunks", []):
            if len(ch["text"]) >= 4 and gz.is_microdistrict(fields["city"], ch["text"]):
                fields["living_area"] = extract(rp.raw, ch["start"], ch["end"])
                prov["living_area"] = "string+gazetteer"
                rp.spans["LIVING_AREA"] = Span("LIVING_AREA", ch["start"], ch["end"], "gazetteer_chunk", 0.75)
                for k in ch["idxs"]:
                    rp.tokens[k].label = "LIVING_AREA"
                    if "leftover_word" in rp.flags:
                        rp.flags.remove("leftover_word")
                rp.flags.append("living_area_from_gazetteer")
                break

    # 4. district
    if fields["district"] is None:
        d, src = None, None
        if fields["street_type"] in ("микрорайон", "квартал", "жилой массив") and fields["street_name"]:
            d, src = gz.district_of_microdistrict(fields["city"], fields["street_name"])
        if d is None and fields["living_area"]:
            d, src = gz.district_of_microdistrict(fields["city"], fields["living_area"])
        if d is None and fields["street_name"]:
            d, src = gz.district_of_street(fields["city"], fields["street_name"], fields["house"], fields["street_type"])
        if d is None:
            d, src = gz.rural_district_of(fields["city"], fields["province"])
        if d is None and rp.meta.get("district_from_city"):
            d, src = rp.meta["district_from_city"], "gazetteer:seed"
        if d:
            fields["district"], prov["district"] = d, src
            if src and src.startswith("gazetteer:osm"):
                rp.flags.append("district_from_osm")

    # 5. street type (not scored; judged at the defense)
    if fields["street_type"] is None and fields["street_name"] and fields["city"]:
        t, src = gz.street_type_of(fields["city"], fields["street_name"])
        if t:
            fields["street_type"], prov["street_type"] = t, src
    if fields["street_name"] and fields["city"] and gz.con is not None:
        rp.flags.append("street_in_gazetteer" if gz.street_exists(fields["city"], fields["street_name"]) else "street_not_in_gazetteer")
