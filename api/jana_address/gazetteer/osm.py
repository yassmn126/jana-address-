"""Runtime gazetteer over api/artifacts/gazetteer.sqlite (built by data_pipeline/build_gazetteer.py from OSM),
with the YAML seed as a fallback for what OSM does not map (e.g. Karaganda's city districts).

Every answer carries its source so the API can show provenance: 'gazetteer:osm' or 'gazetteer:seed'.
Lookups are keyed by place/street NAMES (never by the raw input string) — the brief bans answer lookup
by exact input, a gazetteer keyed by street/city is explicitly allowed («это и есть решение задачи»).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

from ..rules.grammar import Lexicon, get_lexicon
from .names import base_of_numbered, norm

ARTIFACTS = Path(os.environ.get("JANA_ARTIFACTS_DIR", Path(__file__).resolve().parents[2] / "artifacts"))
PLACE_RANK = {"city": 4, "town": 3, "village": 2, "hamlet": 1}
OSM = "gazetteer:osm"
SEED = "gazetteer:seed"


class Gazetteer:
    """OSM + seed. Methods return (value, source) or (None, None)."""

    def __init__(self, path: Path | None = None, lex: Lexicon | None = None):
        self.lex = lex or get_lexicon()
        self.path = Path(path) if path else ARTIFACTS / "gazetteer.sqlite"
        self.con: sqlite3.Connection | None = None
        self.lock = threading.Lock()
        if self.path.exists() and os.environ.get("JANA_GAZETTEER", "auto") != "seed":
            self.con = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self._seed_cities = {norm(k): v for k, v in self.lex.city_by_name.items()}

    @property
    def kind(self) -> str:
        return "osm+seed" if self.con is not None else "seed"

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        if self.con is None:
            return []
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    def stats(self) -> dict:
        if self.con is None:
            return {"kind": "seed"}
        import json
        row = self._q("SELECT value FROM meta WHERE key='stats'")
        built = self._q("SELECT value FROM meta WHERE key='built'")
        return {"kind": self.kind, "built": built[0][0] if built else None, **(json.loads(row[0][0]) if row else {})}

    # ------------------------------------------------------------------ settlements
    @lru_cache(maxsize=20000)
    def settlement(self, name: str | None, province: str | None = None) -> dict | None:
        """Best settlement for a written name: same province first, then city > town > village, then size."""
        if not name:
            return None
        key = norm(name)
        rows = self._q(
            "SELECT s.id, s.name, s.name_kk, s.place, s.province, s.district, s.n_streets, s.population "
            "FROM settlement_names n JOIN settlements s ON s.id = n.settlement_id WHERE n.norm = ?", (key,))
        if not rows:
            return None
        cands = {r[0]: r for r in rows}.values()

        def score(r):
            return (1 if province and r[4] == province else 0, PLACE_RANK.get(r[3], 0), r[6] or 0, r[7] or 0)

        r = max(cands, key=score)
        return {"id": r[0], "name": r[1], "kk": r[2], "place": r[3], "province": r[4], "district": r[5]}

    def _candidates(self, name: str) -> list[tuple]:
        """(id, name, kk, place, province, district, n_streets, population) for every settlement with this name."""
        rows = self._q(
            "SELECT DISTINCT s.id, s.name, s.name_kk, s.place, s.province, s.district, s.n_streets, s.population "
            "FROM settlement_names n JOIN settlements s ON s.id = n.settlement_id WHERE n.norm = ?", (norm(name),))
        return rows

    def province_of_city(self, city: str | None, province_hint: str | None = None, kind: str | None = None,
                         district_hint: str | None = None):
        """Province of a written settlement name, only when it is unambiguous.

        «Абай» is a town in Karaganda region AND dozens of villages: with «ауылы / село» in the string
        (kind='village') only villages count; a district written next to it («Сарыағаш ауданы») narrows it
        down; if several provinces remain the answer is None — a wrong fill is worse than none."""
        if not city:
            return None, None
        seed = self._seed_cities.get(norm(city))
        cands = self._candidates(city)
        if kind == "village":
            cands = [r for r in cands if r[3] in ("village", "hamlet", "town")] or cands
        elif kind == "city":
            cands = [r for r in cands if r[3] in ("city", "town")] or cands
        if district_hint:
            dk = norm(district_hint)[:6]
            narrowed = [r for r in cands if r[5] and norm(r[5]).startswith(dk)]
            cands = narrowed or cands
        if province_hint:
            cands = [r for r in cands if r[4] == province_hint] or cands
        if not cands:
            if seed and kind != "village":
                return seed["province"], SEED
            return None, None
        top = max(PLACE_RANK.get(r[3], 0) for r in cands)
        provs = {r[4] for r in cands if PLACE_RANK.get(r[3], 0) == top and r[4]}
        if top >= PLACE_RANK["town"] and seed and seed["province"] in provs:
            return seed["province"], SEED
        if len(provs) == 1:
            return provs.pop(), OSM
        return None, None

    @lru_cache(maxsize=1)
    def _city_names(self) -> list[tuple[str, str, str]]:
        """(norm, name, province) of cities and towns — the pool for typo-tolerant matching."""
        out = {norm(r["name"]): (norm(r["name"]), r["name"], r["province"]) for r in self._seed_cities.values()}
        for name, prov in self._q("SELECT name, province FROM settlements WHERE place IN ('city','town') AND province IS NOT NULL"):
            if name and norm(name) not in out:
                out[norm(name)] = (norm(name), name, prov)
        return list(out.values())

    def fuzzy_city(self, written: str | None) -> dict | None:
        """«Алмты» → Алматы, «Павладар» → Павлодар: one edit (two for long names), unique best match only."""
        if not written:
            return None
        key = norm(written)
        if len(key) < 5:
            return None
        limit = 1 if len(key) < 9 else 2
        names = self._city_names()
        try:
            from rapidfuzz.distance import OSA
            from rapidfuzz.process import extract
            hits = extract(key, [k for k, _, _ in names], scorer=OSA.distance, score_cutoff=limit, limit=3)
            scored = [(d, i) for _, d, i in hits]
        except ImportError:                                     # pure-Python fallback (same semantics, slower)
            scored = []
            for i, (k, _, _) in enumerate(names):
                if abs(len(k) - len(key)) <= limit:
                    d = _osa(key, k, limit)
                    if d <= limit:
                        scored.append((d, i))
        if not scored:
            return None
        scored.sort()
        best_d, i = scored[0]
        if best_d == 0 or (len(scored) > 1 and scored[1][0] == best_d):
            return None                                         # exact match (not a typo) or a tie
        if key in (names[i][0] + "а", names[i][0] + "ва"):
            return None                                         # «Сатпаева» is the street (genitive), not the town Сатпаев
        return {"name": names[i][1], "province": names[i][2], "distance": best_d}

    def city_ru(self, written: str | None):
        """Kazakh spelling of a settlement → its Russian name («Қарағанды» → «Караганда», «Орал» → «Уральск»)."""
        if not written:
            return None, None
        key = norm(written)
        seed = self._seed_cities.get(key)
        if seed and seed.get("kk") and norm(seed["kk"]) == key and norm(seed["name"]) != key:
            return seed["name"], SEED
        s = self.settlement(written)
        if s and s["kk"] and norm(s["kk"]) == key and s["name"] and norm(s["name"]) != key \
                and s["place"] in ("city", "town"):
            return s["name"], OSM
        return None, None

    def rural_district_of(self, city: str | None, province_hint: str | None = None):
        """Oblast district (аудан) of a town/village («Боралдай» → «Илийский»). Cities have city districts instead."""
        if not city:
            return None, None
        seed = self._seed_cities.get(norm(city))
        if seed and seed.get("district"):
            return seed["district"], SEED
        s = self.settlement(city, province_hint)
        if s and s["district"] and s["place"] != "city":
            return s["district"], OSM
        return None, None

    # ------------------------------------------------------------------ city districts
    def city_of_district(self, district: str | None):
        if not district:
            return None, None
        key = norm(district)
        rows = self._q("SELECT DISTINCT s.name FROM district_names n JOIN city_districts d ON d.id = n.district_id "
                       "JOIN settlements s ON s.id = d.settlement_id WHERE n.norm = ?", (key,))
        seed = self.lex.city_of_district.get(key) or self.lex.city_of_district.get(district.lower())
        if seed is None:
            from ..preprocess import match_view
            seed = self.lex.city_of_district.get(match_view(district))
        if len(rows) == 1 and (seed is None or seed == rows[0][0]):
            return rows[0][0], OSM
        if seed:
            return seed, SEED
        return None, None

    def _district_name(self, did: int | None) -> str | None:
        if did is None:
            return None
        r = self._q("SELECT name FROM city_districts WHERE id = ?", (did,))
        return r[0][0] if r else None

    # ------------------------------------------------------------------ living areas / microdistricts
    def _living(self, city: str | None, name: str | None):
        if not city or not name:
            return None
        s = self.settlement(city)
        if s is None:
            return None
        key, stem = norm(name), norm(base_of_numbered(name))
        rows = self._q("SELECT name, district_id, kind FROM living_areas WHERE settlement_id = ? AND base_norm = ?", (s["id"], key))
        if not rows and stem and stem != key:
            rows = self._q("SELECT name, district_id, kind FROM living_areas WHERE settlement_id = ? AND (base_norm = ? OR stem_norm = ?)",
                           (s["id"], stem, stem))
        if not rows:
            rows = self._q("SELECT l.name, l.district_id, l.kind FROM living_names n JOIN living_areas l ON l.id = n.living_id "
                           "WHERE l.settlement_id = ? AND n.norm IN (?, ?)", (s["id"], key, stem))
        if not rows:
            # microdistricts mapped as streets («Мамыр 4-й микрорайон»)
            rows = self._q("SELECT name, district_id, type FROM streets WHERE settlement_id = ? AND base_norm IN (?, ?) "
                           "AND type IN ('микрорайон', 'квартал', 'жилой массив')", (s["id"], key, stem))
        return rows[0] if rows else None

    def is_microdistrict(self, city: str | None, name: str | None) -> bool:
        if self._seed_micro(city, name) is not None:
            return True
        return self._living(city, name) is not None

    def _seed_micro(self, city, name):
        if not city or not name:
            return None
        rec = self.lex.city_by_name.get(norm(city)) or self._seed_cities.get(norm(city))
        city_ru = rec["name"] if rec else city
        table = self.lex.microdistricts.get(city_ru) or {}
        stem = norm(base_of_numbered(name))
        for k, v in table.items():
            if norm(k) == stem:
                return v
        return None

    def district_of_microdistrict(self, city: str | None, name: str | None):
        row = self._living(city, name)
        if row is not None and row[1] is not None:
            return self._district_name(row[1]), OSM
        seed = self._seed_micro(city, name)
        if seed and seed.get("district"):
            return seed["district"], SEED
        return None, None

    # ------------------------------------------------------------------ streets
    def _streets(self, city: str | None, street: str | None) -> list[tuple]:
        if not city or not street:
            return []
        s = self.settlement(city)
        if s is None:
            return []
        key = norm(street)
        rows = self._q("SELECT id, type, district_id, n_districts FROM streets WHERE settlement_id = ? AND base_norm = ?", (s["id"], key))
        if not rows:
            rows = self._q("SELECT st.id, st.type, st.district_id, st.n_districts FROM street_names n JOIN streets st ON st.id = n.street_id "
                           "WHERE st.settlement_id = ? AND n.norm = ?", (s["id"], key))
        return rows

    def district_of_street(self, city: str | None, street: str | None, house: str | None = None, street_type: str | None = None):
        """House-level first (streets crossing districts), then the street's majority district."""
        rows = self._streets(city, street)
        if street_type and len(rows) > 1:
            typed = [r for r in rows if r[1] == street_type]
            rows = typed or rows
        if not rows:
            return None, None
        sid, _t, did, nd = rows[0]
        if nd and nd > 1 and house:
            import re
            hn = re.sub(r"[\s\-]", "", house.lower().replace("ё", "е"))
            h = self._q("SELECT district_id FROM houses WHERE street_id = ? AND house_norm = ?", (sid, hn))
            if h:
                return self._district_name(h[0][0]), OSM + ":house"
        if did is not None:
            return self._district_name(did), OSM + (":street_majority" if nd and nd > 1 else ":street")
        return None, None

    def street_type_of(self, city: str | None, street: str | None):
        rows = self._streets(city, street)
        types = {r[1] for r in rows if r[1]}
        if len(types) == 1:
            return types.pop(), OSM
        return None, None

    def street_exists(self, city: str | None, street: str | None) -> bool:
        return bool(self._streets(city, street))


def _osa(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance (Damerau–Levenshtein with adjacent transpositions), early exit > limit."""
    if a == b:
        return 0
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


_GZ: Gazetteer | None = None


def get_gazetteer() -> Gazetteer:
    global _GZ
    if _GZ is None:
        _GZ = Gazetteer()
    return _GZ
