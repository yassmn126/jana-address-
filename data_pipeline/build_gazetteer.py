"""OpenStreetMap Kazakhstan → api/artifacts/gazetteer.sqlite (the «собственный справочник»).

    python -m data_pipeline.build_gazetteer --pbf data/osm/kazakhstan-latest.osm.pbf \
        --out api/artifacts/gazetteer.sqlite [--report data_pipeline/report.json]

Source: Geofabrik extract of OSM (ODbL, © OpenStreetMap contributors), one pass with pyosmium
(locations + multipolygon assembly), then shapely STRtree joins. Runs once at build time (~2–5 min
on a laptop); the service only reads the result, offline.

What is extracted and how it is joined
  settlements     place=city|town|village|hamlet nodes + areas, names ru/kk/en/old/alt,
                  province = admin_level 4 polygon that contains it (mapped to our canonical names),
                  district = admin_level 6 «… район / ауданы» polygon that contains it
  city districts  admin polygons named «… район / ауданы» that lie INSIDE a city polygon
                  (Алматы: Ауэзовский…, Астана: Есиль…, Шымкент, Караганда …)
  living areas    place=suburb|neighbourhood|quarter + named landuse=residential + «мкр / ЖК / ж/м»
                  names → (city, name, base name without number, kind, district)
  streets         named highway=* ways, name split into (type lemma, base) with our lexicon
                  («улица Абая» → улица / Абая, «Абай даңғылы» → проспект / Абай); every way
                  contributes up to 5 sample points → district votes; segments merged per city
  houses          addr:street + addr:housenumber (nodes and building ways) → city district by
                  point-in-polygon → house-level district for streets that cross districts
                  (проспект Абая crosses three Almaty districts — resolved by house, not street)
  pois            named shops / malls / schools … (landmark pool for the synthetic generator)

Point → settlement: smallest place polygon that contains the point, else the nearest place node
within a radius that depends on its rank (city 12 km, town 6 km, village 2.5 km, hamlet 1.2 km).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.gazetteer.names import (  # noqa: E402
    DISTRICT_WORDS, base_of_numbered, norm, split_district, split_living, split_street,
)
from jana_address.rules.grammar import get_lexicon  # noqa: E402

PLACE_RANK = {"city": 4, "town": 3, "village": 2, "hamlet": 1}
PLACE_RADIUS_KM = {"city": 12.0, "town": 6.0, "village": 2.5, "hamlet": 1.2}
LIVING_PLACES = {"suburb", "neighbourhood", "quarter", "borough"}
HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
            "living_street", "pedestrian", "service", "road", "trunk_link", "primary_link",
            "secondary_link", "tertiary_link", "footway", "track"}
NAME_KEYS = ("name", "name:ru", "name:kk", "name:en", "old_name", "old_name:ru", "alt_name", "alt_name:ru",
             "official_name", "official_name:ru", "short_name", "name:kk-Latn")
POI_KEYS = ("shop", "amenity", "tourism", "leisure", "office", "healthcare")
POI_KIND_RU = {
    "supermarket": "супермаркет", "convenience": "магазин", "mall": "ТРЦ", "marketplace": "рынок",
    "school": "школа", "kindergarten": "детсад", "hospital": "больница", "clinic": "поликлиника",
    "pharmacy": "аптека", "bank": "банк", "cafe": "кафе", "restaurant": "ресторан", "fuel": "АЗС",
    "post_office": "почта", "university": "университет", "college": "колледж", "place_of_worship": "мечеть",
    "hotel": "гостиница", "park": "парк", "stadium": "стадион", "department_store": "ТЦ",
}


def _names(tags) -> dict[str, str]:
    out = {}
    for k in NAME_KEYS:
        v = tags.get(k)
        if v:
            out[k] = v.strip()
    return out


def _ru(names: dict) -> str | None:
    return names.get("name:ru") or names.get("name") or names.get("name:kk")


def km(lat1, lon1, lat2, lon2) -> float:
    dlat = (lat2 - lat1) * 111.2
    dlon = (lon2 - lon1) * 111.2 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


# --------------------------------------------------------------------------- OSM pass
def read_osm(pbf: str) -> dict:
    import osmium
    from shapely import wkb as swkb

    wkbfab = osmium.geom.WKBFactory()

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.admin, self.place_areas, self.res_areas = [], [], []
            self.place_nodes, self.living_nodes = [], []
            self.streets, self.houses, self.pois = [], [], []
            self.n = Counter()

        # -- nodes
        def node(self, n):
            t = n.tags
            if not len(t) or not n.location.valid():
                return
            lat, lon = n.location.lat, n.location.lon
            place = t.get("place")
            if place:
                names = _names(t)
                if names:
                    rec = {"names": names, "place": place, "lat": lat, "lon": lon,
                           "population": t.get("population")}
                    if place in PLACE_RANK:
                        self.place_nodes.append(rec)
                    elif place in LIVING_PLACES:
                        self.living_nodes.append(rec)
            if "addr:housenumber" in t and "addr:street" in t:
                self.houses.append((lat, lon, t["addr:street"], t["addr:housenumber"]))
            if "name" in t and any(k in t for k in POI_KEYS):
                self.pois.append((lat, lon, t["name"], next(t.get(k) for k in POI_KEYS if k in t)))
            self.n["node_tagged"] += 1

        # -- ways
        def way(self, w):
            t = w.tags
            hw = t.get("highway")
            if hw and hw in HIGHWAYS and ("name" in t or "name:ru" in t or "name:kk" in t):
                pts = [(nr.location.lat, nr.location.lon) for nr in w.nodes if nr.location.valid()]
                if pts:
                    k = max(1, len(pts) // 4)
                    sample = pts[::k][:5]
                    self.streets.append({"id": w.id, "names": _names(t), "highway": hw, "pts": sample})
                    self.n["street_way"] += 1
            if "addr:housenumber" in t and "addr:street" in t and w.is_closed():
                pts = [(nr.location.lat, nr.location.lon) for nr in w.nodes if nr.location.valid()]
                if pts:
                    lat = sum(p[0] for p in pts) / len(pts)
                    lon = sum(p[1] for p in pts) / len(pts)
                    self.houses.append((lat, lon, t["addr:street"], t["addr:housenumber"]))
            if "name" in t and w.is_closed() and any(k in t for k in POI_KEYS):
                pts = [(nr.location.lat, nr.location.lon) for nr in w.nodes if nr.location.valid()]
                if pts:
                    self.pois.append((sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts),
                                      t["name"], next(t.get(k) for k in POI_KEYS if k in t)))

        # -- areas (closed ways + multipolygon relations)
        def area(self, a):
            t = a.tags
            kind = None
            if t.get("boundary") == "administrative" and t.get("admin_level") in {"4", "5", "6", "7", "8", "9", "10"}:
                kind = "admin"
            elif t.get("place") in PLACE_RANK or t.get("place") in LIVING_PLACES:
                kind = "place"
            elif t.get("landuse") == "residential" and ("name" in t or "name:ru" in t):
                kind = "res"
            if not kind:
                return
            names = _names(t)
            if not names:
                return
            try:
                geom = swkb.loads(wkbfab.create_multipolygon(a), hex=True)
            except Exception:  # noqa: BLE001 — broken multipolygons are skipped
                self.n["bad_area"] += 1
                return
            rec = {"names": names, "geom": geom, "osm": ("w" if a.from_way() else "r") + str(a.orig_id())}
            if kind == "admin":
                rec["level"] = int(t["admin_level"])
                self.admin.append(rec)
            elif kind == "place":
                rec["place"] = t["place"]
                self.place_areas.append(rec)
            else:
                self.res_areas.append(rec)

    h = H()
    t0 = time.time()
    h.apply_file(pbf, locations=True, idx="flex_mem")
    print(f"OSM read in {time.time() - t0:.0f}s: admin={len(h.admin)} place_areas={len(h.place_areas)} "
          f"res_areas={len(h.res_areas)} place_nodes={len(h.place_nodes)} living_nodes={len(h.living_nodes)} "
          f"street_ways={len(h.streets)} houses={len(h.houses)} pois={len(h.pois)} {dict(h.n)}", flush=True)
    return {"admin": h.admin, "place_areas": h.place_areas, "res_areas": h.res_areas,
            "place_nodes": h.place_nodes, "living_nodes": h.living_nodes,
            "streets": h.streets, "houses": h.houses, "pois": h.pois}


# --------------------------------------------------------------------------- spatial helpers
class PolyIndex:
    """STRtree over polygons; `smallest(lat, lon)` returns the smallest polygon record containing the point."""

    def __init__(self, recs: list[dict]):
        from shapely.strtree import STRtree
        self.recs = recs
        self.tree = STRtree([r["geom"] for r in recs]) if recs else None

    def containing(self, lat: float, lon: float) -> list[dict]:
        if self.tree is None:
            return []
        from shapely.geometry import Point
        p = Point(lon, lat)
        idx = self.tree.query(p, predicate="intersects")
        return [self.recs[i] for i in idx]

    def smallest(self, lat: float, lon: float) -> dict | None:
        c = self.containing(lat, lon)
        return min(c, key=lambda r: r["geom"].area) if c else None


class NodeGrid:
    """Nearest place node within a rank-dependent radius (0.1° buckets)."""

    def __init__(self, nodes: list[dict]):
        self.grid: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for n in nodes:
            self.grid[(int(n["lat"] * 10), int(n["lon"] * 10))].append(n)

    def nearest(self, lat: float, lon: float) -> dict | None:
        best, best_score = None, 1e9
        gi, gj = int(lat * 10), int(lon * 10)
        for di in range(-2, 3):
            for dj in range(-2, 3):
                for n in self.grid.get((gi + di, gj + dj), ()):
                    d = km(lat, lon, n["lat"], n["lon"])
                    r = PLACE_RADIUS_KM[n["place"]]
                    if d <= r and d / r < best_score:
                        best, best_score = n, d / r
        return best


# --------------------------------------------------------------------------- build
def province_mapper():
    lex = get_lexicon()
    by_variant = {}
    for p in lex.provinces:
        by_variant[norm(p["canonical"])] = p["canonical"]
        if p.get("kk"):
            by_variant[norm(p["kk"])] = p["canonical"]
        for v in p["variants"]:
            by_variant[norm(v)] = p["canonical"]

    def canon(names: dict) -> str | None:
        for k in ("name:ru", "name", "name:kk", "official_name:ru", "alt_name:ru", "old_name:ru", "alt_name"):
            v = names.get(k)
            if not v:
                continue
            key = norm(v)
            if key in by_variant:
                return by_variant[key]
            # «Алматинская область» / «Алматы облысы» / «город Алматы» → drop the type word and retry
            stripped = re.sub(r"\b(область|облысы|обл|город|г|қаласы|қ)\b", " ", key).strip()
            stripped = re.sub(r"\s+", " ", stripped)
            if stripped in by_variant:
                return by_variant[stripped]
        return None

    return canon


def build(osm: dict, out: str, report_path: str | None) -> dict:
    canon_province = province_mapper()
    rep: dict = {}

    # ---- admin hierarchy
    prov_polys = []
    for a in osm["admin"]:
        if a["level"] == 4:
            p = canon_province(a["names"])
            if p:
                prov_polys.append({**a, "province": p})
            else:
                rep.setdefault("unmapped_level4", []).append(_ru(a["names"]))
    prov_idx = PolyIndex(prov_polys)
    rep["provinces_mapped"] = sorted({p["province"] for p in prov_polys})

    district_like = []           # admin polygons named «… район / ауданы»
    for a in osm["admin"]:
        ru = _ru(a["names"]) or ""
        if any(w in norm(ru).split() for w in DISTRICT_WORDS) or a["level"] in (6, 9):
            district_like.append(a)

    # ---- settlements: nodes first, then areas not represented by a node
    settlements: list[dict] = []
    for n in osm["place_nodes"]:
        settlements.append({"names": n["names"], "place": n["place"], "lat": n["lat"], "lon": n["lon"],
                            "population": _int(n.get("population")), "geom": None})
    by_name = defaultdict(list)
    for s in settlements:
        by_name[norm(_ru(s["names"]) or "")].append(s)
    for a in osm["place_areas"]:
        if a["place"] not in PLACE_RANK:
            continue
        c = a["geom"].representative_point()
        key = norm(_ru(a["names"]) or "")
        match = [s for s in by_name.get(key, []) if km(s["lat"], s["lon"], c.y, c.x) < 25]
        if match:
            match[0]["geom"] = a["geom"]
        else:
            s = {"names": a["names"], "place": a["place"], "lat": c.y, "lon": c.x, "population": None, "geom": a["geom"]}
            settlements.append(s)
            by_name[key].append(s)
    # city administrations («Актобе Г.А.», «Карагандинская городская администрация», «Орал қалалық әкімдігі»):
    # the polygon is attached to the biggest city/town node inside it — used as a LAST-resort container
    # (it also covers suburbs and villages, which must keep their own names)
    for a in osm["admin"]:
        ru = norm(_ru(a["names"]) or "") + " " + norm(a["names"].get("name:kk", ""))
        same_name = any(s["place"] in ("city", "town") and a["geom"].contains(_pt(s)) for s in by_name.get(norm(_ru(a["names"]) or ""), []))
        if not same_name and not re.search(r"\b(г а|к а|га|ка|городская администрация|калалык акимдиги|акимат|акимдиги)\b", ru):
            continue
        inside = [s for s in settlements if s["place"] in ("city", "town") and a["geom"].contains(_pt(s))]
        if inside:
            host = max(inside, key=lambda s: (PLACE_RANK[s["place"]], s["population"] or 0))
            if host.get("admin_geom") is None or host["admin_geom"].area < a["geom"].area:
                host["admin_geom"] = a["geom"]
    for s in settlements:
        if s["place"] in ("city",) and s["geom"] is None and s.get("admin_geom") is not None:
            s["geom"] = None          # place polygon stays empty; admin polygon is used only as fallback
    for i, s in enumerate(settlements):
        s["id"] = i + 1
        pp = prov_idx.smallest(s["lat"], s["lon"])
        s["province"] = pp["province"] if pp else None
        s["district"] = None
        s["admin_district_osm"] = None
    settle_poly_idx = PolyIndex([s for s in settlements if s["geom"] is not None])
    admin_city_idx = PolyIndex([{**s, "geom": s["admin_geom"], "ref": s} for s in settlements if s.get("admin_geom") is not None])
    node_grid = NodeGrid([s for s in settlements])

    def settlement_of(lat: float, lon: float) -> dict | None:
        """smallest place polygon → nearest place node in radius → city administration polygon."""
        best = None
        for s in settle_poly_idx.containing(lat, lon):
            if best is None or s["geom"].area < best["geom"].area:
                best = s
        if best is not None:
            return best
        n = node_grid.nearest(lat, lon)
        if n is not None:
            return n
        a = admin_city_idx.smallest(lat, lon)
        return a["ref"] if a is not None else None

    # ---- city districts: district-like polygons inside a city/town polygon (or a republican city)
    city_districts: list[dict] = []
    big_cities = [{**s, "geom": s["geom"] if s["geom"] is not None else s.get("admin_geom"), "ref": s}
                  for s in settlements if s["place"] in ("city", "town") and (s["geom"] is not None or s.get("admin_geom") is not None)]
    for a in district_like:
        ru = _ru(a["names"]) or ""
        base, _ = split_district(ru)
        if not base:
            continue
        rp = a["geom"].representative_point()
        host = None
        for s in big_cities:
            g = s["geom"]
            if g.area > a["geom"].area * 1.05 and g.contains(rp) and not re.search(
                    r"\b(г а|к а|га|ка|администрация|акимдиги|сельск|ауылдык|округ)\b", norm(ru)):
                if host is None or g.area < host["geom"].area:
                    host = s
        if host is None:
            continue
        host = host["ref"]
        kk_base = split_district(a["names"].get("name:kk", ""))[0] if a["names"].get("name:kk") else None
        city_districts.append({"id": len(city_districts) + 1, "settlement": host, "name": base, "kk": kk_base,
                               "names": a["names"], "geom": a["geom"], "level": a["level"]})
    # one district per (city, name): keep the most specific (highest admin_level)
    dedup = {}
    for d in sorted(city_districts, key=lambda d: -d["level"]):
        dedup.setdefault((d["settlement"]["id"], norm(d["name"])), d)
    city_districts = list(dedup.values())
    for i, d in enumerate(city_districts):
        d["id"] = i + 1
    cd_idx = PolyIndex(city_districts)
    rep["city_districts"] = Counter(_ru(d["settlement"]["names"]) for d in city_districts).most_common(30)

    def city_district_of(lat: float, lon: float, settlement: dict | None) -> dict | None:
        best = None
        for d in cd_idx.containing(lat, lon):
            if settlement is not None and d["settlement"]["id"] != settlement["id"]:
                continue
            if best is None or d["geom"].area < best["geom"].area:
                best = d
        return best

    # oblast district (аудан) for settlements outside cities
    aud_polys = [a for a in district_like if a["level"] == 6 and not any(d["names"] is a["names"] for d in city_districts)]
    aud_idx = PolyIndex(aud_polys)
    for s in settlements:
        if s["place"] == "city":
            continue
        a = aud_idx.smallest(s["lat"], s["lon"])
        if a is not None:
            base, _ = split_district(_ru(a["names"]) or "")
            if base and any(w in norm(_ru(a["names"]) or "").split() for w in DISTRICT_WORDS):
                s["district"] = base

    # ---- living areas
    living: list[dict] = []
    seen_living = set()

    def add_living(names: dict, lat: float, lon: float, source: str):
        ru = _ru(names)
        if not ru:
            return
        kind, base = split_living(ru)
        if not base:
            return
        s = settlement_of(lat, lon)
        if s is None:
            return
        key = (s["id"], norm(base))
        if key in seen_living:
            return
        seen_living.add(key)
        d = city_district_of(lat, lon, s)
        kk = split_living(names["name:kk"])[1] if names.get("name:kk") else None
        living.append({"settlement": s, "name": ru, "base": base, "kk": kk, "kind": kind or "", "district": d,
                       "lat": lat, "lon": lon, "source": source})

    for a in osm["place_areas"]:
        if a["place"] in LIVING_PLACES:
            c = a["geom"].representative_point()
            add_living(a["names"], c.y, c.x, "place_area")
    for n in osm["living_nodes"]:
        add_living(n["names"], n["lat"], n["lon"], "place_node")
    for a in osm["res_areas"]:
        ru = _ru(a["names"]) or ""
        kind, base = split_living(ru)
        if kind or re.search(r"\d", ru) is None:
            c = a["geom"].representative_point()
            add_living(a["names"], c.y, c.x, "landuse")
    rep["living_areas"] = len(living)

    # ---- streets
    groups: dict[tuple[int, str, str], dict] = {}
    unassigned = 0
    for w in osm["streets"]:
        names = w["names"]
        ru = names.get("name:ru") or names.get("name")
        if not ru:
            continue
        typ, base = split_street(ru)
        if not base or len(base) < 2:
            continue
        lat, lon = w["pts"][len(w["pts"]) // 2]
        s = settlement_of(lat, lon)
        if s is None:
            unassigned += 1
            continue
        key = (s["id"], norm(base), typ or "")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"settlement": s, "name": ru, "base": base, "type": typ, "alt": set(), "votes": Counter(),
                               "highway": Counter(), "houses": {}}
        g["highway"][w["highway"]] += 1
        for k in ("name:kk", "name", "old_name", "alt_name", "name:en", "old_name:ru", "alt_name:ru"):
            if names.get(k):
                b = split_street(names[k])[1]
                if b and norm(b) != norm(base):
                    g["alt"].add(b)
        for (plat, plon) in w["pts"]:
            d = city_district_of(plat, plon, s)
            if d is not None:
                g["votes"][d["id"]] += 1
    rep["street_ways_unassigned"] = unassigned

    # ---- houses → district, attached to the street group of the same city
    street_by_city_base: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for (sid, b, _t), g in groups.items():
        street_by_city_base[(sid, b)].append(g)
    house_hits = 0
    for lat, lon, street, hn in osm["houses"]:
        typ, base = split_street(street)
        if not base:
            continue
        s = settlement_of(lat, lon)
        if s is None:
            continue
        cands = street_by_city_base.get((s["id"], norm(base)))
        if not cands:
            continue
        g = cands[0] if len(cands) == 1 else next((c for c in cands if c["type"] == typ), cands[0])
        d = city_district_of(lat, lon, s)
        g["houses"][norm_house(hn)] = (hn, d["id"] if d else None)
        if d is not None:
            g["votes"][d["id"]] += 2
        house_hits += 1
    rep["houses_matched"] = house_hits

    # ---- POIs
    pois = []
    for lat, lon, name, kind in osm["pois"]:
        s = settlement_of(lat, lon)
        pois.append((s["id"] if s else None, name, kind))

    # ---- count streets per settlement (used for city disambiguation priors)
    n_streets = Counter(g["settlement"]["id"] for g in groups.values())

    # ---- write sqlite
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    if outp.exists():
        outp.unlink()
    con = sqlite3.connect(str(outp))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta VALUES ('source', 'OpenStreetMap Kazakhstan extract (Geofabrik), ODbL © OpenStreetMap contributors')")
    con.execute("INSERT INTO meta VALUES ('built', ?)", (time.strftime("%Y-%m-%d %H:%M"),))
    for s in settlements:
        names = s["names"]
        con.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            s["id"], _ru(names), names.get("name:kk"), names.get("name:en"), s["place"], s["province"], s["district"],
            round(s["lat"], 5), round(s["lon"], 5), s["population"], n_streets.get(s["id"], 0)))
        for k, v in names.items():
            for part in re.split(r"[;/]", v):
                if part.strip():
                    con.execute("INSERT INTO settlement_names VALUES (?,?,?)", (norm(part), s["id"], k))
    for d in city_districts:
        con.execute("INSERT INTO city_districts VALUES (?,?,?,?)", (d["id"], d["settlement"]["id"], d["name"], d["kk"]))
        variants = {d["name"]} | ({d["kk"]} if d["kk"] else set())
        for k, v in d["names"].items():
            b = split_district(v)[0]
            if b:
                variants.add(b)
        for v in variants:
            con.execute("INSERT INTO district_names VALUES (?,?)", (norm(v), d["id"]))
    for i, l in enumerate(living, 1):
        con.execute("INSERT INTO living_areas VALUES (?,?,?,?,?,?,?,?,?,?)", (
            i, l["settlement"]["id"], l["name"], l["base"], norm(l["base"]), norm(base_of_numbered(l["base"])),
            l["kind"], l["district"]["id"] if l["district"] else None, round(l["lat"], 5), round(l["lon"], 5)))
        if l["kk"] and norm(l["kk"]) != norm(l["base"]):
            con.execute("INSERT INTO living_names VALUES (?,?)", (norm(l["kk"]), i))
    sid = 0
    house_pool: list[str] = []
    for (setl, b, _t), g in groups.items():
        sid += 1
        votes = g["votes"]
        top = votes.most_common(1)[0][0] if votes else None
        con.execute("INSERT INTO streets VALUES (?,?,?,?,?,?,?,?,?,?)", (
            sid, setl, g["name"], g["base"], b, g["type"], top, len(votes), len(g["houses"]),
            g["highway"].most_common(1)[0][0]))
        for alt in g["alt"]:
            con.execute("INSERT INTO street_names VALUES (?,?)", (norm(alt), sid))
        if len(votes) > 1:              # house-level district only matters where the street crosses districts
            for hn_norm, (hn, did) in g["houses"].items():
                if did is not None:
                    con.execute("INSERT INTO houses VALUES (?,?,?)", (sid, hn_norm, did))
        for hn_norm, (hn, did) in g["houses"].items():
            house_pool.append(hn)
    import random as _r
    _r.Random(5).shuffle(house_pool)
    con.executemany("INSERT INTO house_samples VALUES (?)", [(h,) for h in house_pool[:60000]])
    con.executemany("INSERT INTO pois VALUES (?,?,?)", pois)
    con.executescript(INDEXES)
    con.commit()
    stats = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("settlements", "settlement_names", "city_districts", "living_areas", "streets", "houses", "house_samples", "pois")}
    con.execute("INSERT INTO meta VALUES ('stats', ?)", (json.dumps(stats),))
    con.commit()
    con.execute("VACUUM")
    con.close()
    rep["tables"] = stats
    rep["size_mb"] = round(outp.stat().st_size / 1e6, 1)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    if report_path:
        Path(report_path).write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return rep


def norm_house(h: str) -> str:
    return re.sub(r"[\s\-]", "", h.lower().replace("ё", "е"))


def _int(v) -> int | None:
    try:
        return int(re.sub(r"[^\d]", "", str(v))) if v else None
    except ValueError:
        return None


def _pt(s):
    from shapely.geometry import Point
    return Point(s["lon"], s["lat"])


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE settlements(id INTEGER PRIMARY KEY, name TEXT, name_kk TEXT, name_en TEXT, place TEXT,
                         province TEXT, district TEXT, lat REAL, lon REAL, population INTEGER, n_streets INTEGER);
CREATE TABLE settlement_names(norm TEXT, settlement_id INTEGER, key TEXT);
CREATE TABLE city_districts(id INTEGER PRIMARY KEY, settlement_id INTEGER, name TEXT, name_kk TEXT);
CREATE TABLE district_names(norm TEXT, district_id INTEGER);
CREATE TABLE living_areas(id INTEGER PRIMARY KEY, settlement_id INTEGER, name TEXT, base TEXT, base_norm TEXT,
                          stem_norm TEXT, kind TEXT, district_id INTEGER, lat REAL, lon REAL);
CREATE TABLE living_names(norm TEXT, living_id INTEGER);
CREATE TABLE streets(id INTEGER PRIMARY KEY, settlement_id INTEGER, name TEXT, base TEXT, base_norm TEXT, type TEXT,
                     district_id INTEGER, n_districts INTEGER, n_houses INTEGER, highway TEXT);
CREATE TABLE street_names(norm TEXT, street_id INTEGER);
CREATE TABLE houses(street_id INTEGER, house_norm TEXT, district_id INTEGER);
CREATE TABLE house_samples(house TEXT);
CREATE TABLE pois(settlement_id INTEGER, name TEXT, kind TEXT);
"""
INDEXES = """
CREATE INDEX ix_sn ON settlement_names(norm);
CREATE INDEX ix_dn ON district_names(norm);
CREATE INDEX ix_cd ON city_districts(settlement_id);
CREATE INDEX ix_la ON living_areas(settlement_id, base_norm);
CREATE INDEX ix_la2 ON living_areas(settlement_id, stem_norm);
CREATE INDEX ix_ln ON living_names(norm);
CREATE INDEX ix_st ON streets(settlement_id, base_norm);
CREATE INDEX ix_stn ON street_names(norm);
CREATE INDEX ix_h ON houses(street_id, house_norm);
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", default="data/osm/kazakhstan-latest.osm.pbf")
    ap.add_argument("--out", default="api/artifacts/gazetteer.sqlite")
    ap.add_argument("--report", default="data_pipeline/report.json")
    a = ap.parse_args(argv)
    t0 = time.time()
    osm = read_osm(a.pbf)
    build(osm, a.out, a.report)
    print(f"done in {time.time() - t0:.0f}s → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
