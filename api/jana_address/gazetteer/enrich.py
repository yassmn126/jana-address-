"""Gazetteer enrichment — fills NULL fields only, never overwrites what the string says.

v0 backs onto the YAML seed (places_seed.yaml). The real implementation keeps the same
interface but queries gazetteer.sqlite built by data_pipeline/ (OSM + KATO):

    class Gazetteer(Protocol):
        def province_of_city(city) -> str | None
        def city_of_district(district) -> str | None
        def district_of_microdistrict(city, name) -> str | None
        def district_of_street(city, street, house) -> str | None      # house-level, then street-level
        def street_type_of(city, street) -> str | None
        def city_ru(kk_name) -> str | None

Provenance strings: 'gazetteer:seed' here, 'gazetteer:osm' / 'gazetteer:kato' later.
"""
from __future__ import annotations

import re

from ..preprocess import extract, match_view
from ..rules.grammar import Lexicon, RuleParse, Span, get_lexicon

_BASE_RE = re.compile(r"^(.*?)[\s\-]*\d+[а-яa-zё]?$", re.IGNORECASE)   # «Аксай 3а» → «Аксай», «Самал-2» → «Самал»


class SeedGazetteer:
    def __init__(self, lex: Lexicon | None = None):
        self.lex = lex or get_lexicon()

    def province_of_city(self, city: str | None) -> str | None:
        if not city:
            return None
        rec = self.lex.city_by_name.get(match_view(city))
        return rec["province"] if rec else None

    def city_of_district(self, district: str | None) -> str | None:
        if not district:
            return None
        return self.lex.city_of_district.get(match_view(district))

    def district_of_microdistrict(self, city: str | None, name: str | None) -> str | None:
        if not city or not name:
            return None
        rec = self.lex.city_by_name.get(match_view(city))
        city_ru = rec["name"] if rec else city
        table = self.lex.microdistricts.get(city_ru) or {}
        if not table:
            return None
        base = name
        m = _BASE_RE.match(name)
        if m and m.group(1):
            base = m.group(1)
        key = match_view(base)
        for k, v in table.items():
            if match_view(k) == key:
                return v["district"]
        return None

    def is_microdistrict(self, city: str | None, name: str | None) -> bool:
        """Known микрорайон / жилой массив / ЖК name for this city (seed; real impl: OSM place=*)."""
        if not city or not name:
            return False
        rec = self.lex.city_by_name.get(match_view(city))
        city_ru = rec["name"] if rec else city
        table = self.lex.microdistricts.get(city_ru) or {}
        base = name
        m = _BASE_RE.match(name)
        if m and m.group(1):
            base = m.group(1)
        key = match_view(base)
        return any(match_view(k) == key for k in table)

    def district_of_street(self, city, street, house) -> str | None:  # real impl: OSM house points
        return None

    def street_type_of(self, city, street) -> str | None:              # real impl: OSM highway name:ru
        return None


_GZ: SeedGazetteer | None = None


def get_gazetteer() -> SeedGazetteer:
    global _GZ
    if _GZ is None:
        _GZ = SeedGazetteer()
    return _GZ


def enrich(fields: dict, prov: dict, rp: RuleParse, gz: SeedGazetteer | None = None) -> None:
    """Mutates fields/prov in place. Only fills None values."""
    gz = gz or get_gazetteer()
    tag = "gazetteer:seed"

    if fields["city"] is None:
        c = gz.city_of_district(fields["district"]) or rp.meta.get("city_of_district")
        if c:
            fields["city"], prov["city"] = c, tag
    if fields["province"] is None:
        p = gz.province_of_city(fields["city"])
        if p:
            fields["province"], prov["province"] = p, tag
    # a free word chunk that is a known микрорайон/жилой массив of the city → living_area
    # («Караганда, Пришахтинск, ул. Ленина 5»). The text IS in the string, so this counts
    # for the metric: provenance 'string+gazetteer'.
    if fields["living_area"] is None:
        for ch in rp.meta.get("leftover_chunks", []):
            if gz.is_microdistrict(fields["city"], ch["text"]):
                fields["living_area"] = extract(rp.raw, ch["start"], ch["end"])
                prov["living_area"] = "string+gazetteer"
                rp.spans["LIVING_AREA"] = Span("LIVING_AREA", ch["start"], ch["end"], "gazetteer_chunk", 0.75)
                for k in ch["idxs"]:
                    rp.tokens[k].label = "LIVING_AREA"
                    if "leftover_word" in rp.flags:
                        rp.flags.remove("leftover_word")
                rp.flags.append("living_area_from_gazetteer")
                break
    if fields["district"] is None:
        d = None
        if fields["street_type"] in ("микрорайон", "квартал", "жилой массив"):
            d = gz.district_of_microdistrict(fields["city"], fields["street_name"])
        if d is None and fields["living_area"]:
            d = gz.district_of_microdistrict(fields["city"], fields["living_area"])
        if d is None:
            d = gz.district_of_street(fields["city"], fields["street_name"], fields["house"])
        if d is None and rp.meta.get("district_from_city"):
            d = rp.meta["district_from_city"]
        if d:
            fields["district"], prov["district"] = d, tag
    if fields["street_type"] is None and fields["street_name"]:
        t = gz.street_type_of(fields["city"], fields["street_name"])
        if t:
            fields["street_type"], prov["street_type"] = t, tag
