"""Grammar-based synthetic addresses from the OSM gazetteer — cold-start training data (no train.json needed).

    python -m training.synth_grammar --n 120000 --out data/dataset/synth_grammar.jsonl \
        [--gazetteer api/artifacts/gazetteer.sqlite] [--seed 1] [--gold-out data/dataset/synth_gold.json]

Why: `train.json` is unlabeled and arrives at the hackathon start. libpostal's parser is a CRF trained on
addresses GENERATED from OpenStreetMap; we do the same for Kazakhstan, with the conventions of
docs/CONVENTIONS.md applied by construction. When real labels exist, `synth.py` (templates mined from
verified records) and the real rows are mixed in; this generator keeps covering the long tail
(every city, every street, every keyword spelling).

Every record is built from pieces `(text, field)`. Field pieces are values exactly as the jury wants them
cut from the string; keyword / separator / noise pieces are `O`. The gold record (jury format) is
computed alongside, so the same rows also evaluate the full pipeline (`--gold-out`).

Slide-9 difficulties → generator features
  glued numbers «66квартал 12дом 4подьезд»   kw_glue in every number↔keyword binding
  fractions / letters «55/6 12а 33/1»         real OSM house numbers + generated N/M, Nа
  Latin «flat 191, kv 12, dom»                latin keyword sets + transliterated names (p_latin)
  микрорайон number vs house                  «Нурсити 2 микрорайон 11дом», «Мкр Аксай 3а, 59», «11 мкр, 112 дом»
  landmarks «магазин береке», «Qazpost …»     OSM POIs with kind words, marker words as O
  KK/RU splices «город Актобе/Ақтөбе қаласы»  kk duplicates, Kazakh keyword sets and word order
  duplicated city / district instead of city  city_dup, district_only
  typos & spaces «Ул .Курильская», «подьезд»  keyword typos, dot-space, double spaces, value typos
  invisible characters                        ZWSP / NBSP / BOM injected in separators
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.gazetteer.names import base_of_numbered, norm, split_living  # noqa: E402
from jana_address.preprocess import clean  # noqa: E402
from jana_address.rules.grammar import get_lexicon  # noqa: E402
from jana_address.tokenizer import tokenize  # noqa: E402
from training.validate_labels import to_bio  # noqa: E402

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
REPUBLICAN = {"Астана", "Алматы", "Шымкент"}

# ------------------------------------------------------------------ keyword inventories (weights by frequency)
KW = {
    "city": [("г.", 30), ("г. ", 0), ("город", 15), ("г", 8), ("гор.", 2)],
    "village": [("с.", 20), ("село", 8), ("пос.", 15), ("п.", 5), ("поселок", 6), ("аул", 5), ("пгт", 2), ("с/о", 1)],
    "district_after": [("район", 30), ("р-н", 25), ("р-н.", 4), ("р/н", 2), ("района", 1)],
    "district_before": [("район", 10), ("р-н", 6)],
    "province_after": [("область", 30), ("обл.", 20), ("обл", 15)],
    "house": [("д.", 30), ("дом", 25), ("д", 10), ("уй", 1), ("dom", 2), ("д .", 1)],
    "apartment": [("кв.", 30), ("кв", 35), ("квартира", 10), ("кв-ра", 1), ("kv", 4), ("kv.", 2), ("flat", 2),
                  ("apt", 1), ("оф.", 3), ("офис", 3), ("каб.", 1)],
    "entrance": [("подъезд", 30), ("подьезд", 12), ("под.", 6), ("п-д", 4), ("подезд", 2), ("п.", 2), ("entrance", 1)],
    "floor": [("этаж", 35), ("эт.", 12), ("эт", 4), ("floor", 1)],
    "living_kw": [("ЖК", 30), ("жк", 8), ("Ж/К", 2), ("жилой комплекс", 4), ("мкр", 20), ("мкр.", 10),
                  ("микрорайон", 6), ("ж/м", 4), ("жилой массив", 2)],
    "landmark_marker": [("ориентир", 10), ("напротив", 6), ("рядом с", 5), ("возле", 4), ("около", 2), ("за", 1)],
}
KW_KK = {
    "city_suffix": [("қаласы", 30), ("қ.", 6), ("каласы", 3)],
    "village_suffix": [("ауылы", 20), ("кенті", 3)],
    "district_after": [("ауданы", 30), ("ауд.", 2)],
    "province_after": [("облысы", 30), ("обл.", 3)],
    "street_after": {"улица": [("көшесі", 30), ("к-сі", 3), ("көш.", 2)], "проспект": [("даңғылы", 30), ("даңғ.", 3)],
                     "переулок": [("тұйығы", 5), ("өткелі", 2)], "бульвар": [("гүлзары", 5)]},
    "micro_after": [("ықшам ауданы", 20), ("ықшам аудан", 10), ("шағын ауданы", 8), ("шағын аудан", 4), ("мкр", 6)],
    "house": [("үй", 30), ("уй", 5)],
    "apartment": [("пәтер", 30), ("пәт.", 3), ("кв", 8)],
    "entrance": [("кіреберіс", 30), ("подъезд", 10)],
    "floor": [("қабат", 30), ("этаж", 6)],
}
STREET_KW_BEFORE = {
    "улица": [("ул.", 50), ("ул", 25), ("улица", 18), ("ул .", 2), ("Ул.", 5), ("УЛ.", 1), ("ul.", 2), ("ul", 1), ("у.", 1)],
    "проспект": [("пр.", 25), ("пр-т", 20), ("проспект", 20), ("просп.", 6), ("пр", 6), ("пр-кт", 3), ("prospekt", 1), ("ave", 1)],
    "микрорайон": [("мкр", 40), ("мкр.", 25), ("микрорайон", 10), ("м-н", 3), ("мкрн", 3), ("мкр-н", 2), ("mkr", 2), ("м/р", 1)],
    "переулок": [("пер.", 30), ("переулок", 10), ("пер", 6)],
    "квартал": [("квартал", 10), ("кв-л", 4)],
    "бульвар": [("б-р", 10), ("бульвар", 10), ("бул.", 3)],
    "шоссе": [("шоссе", 10), ("ш.", 3)],
    "проезд": [("проезд", 10), ("пр-д", 4)],
    "тупик": [("тупик", 10), ("туп.", 3)],
    "площадь": [("пл.", 6), ("площадь", 6)],
    "аллея": [("аллея", 5)],
    "тракт": [("тракт", 5)],
    "набережная": [("наб.", 3), ("набережная", 3)],
    "жилой массив": [("ж/м", 6), ("жилой массив", 3)],
    "дорога": [("трасса", 2)],
}
STREET_KW_AFTER = {
    "улица": [("улица", 10), ("ул.", 4), ("ул", 3)],
    "проспект": [("проспект", 8), ("пр.", 3)],
    "микрорайон": [("мкр", 25), ("микрорайон", 15), ("мкр.", 6), ("мкрн", 2)],
    "переулок": [("переулок", 8), ("пер.", 3)],
    "квартал": [("квартал", 10), ("кв-л", 2)],
    "бульвар": [("бульвар", 3)],
}
LANDMARK_KIND = [("магазин", 30), ("маг.", 3), ("ТЦ", 12), ("ТРЦ", 5), ("супермаркет", 3), ("рынок", 3), ("школа", 6),
                 ("аптека", 3), ("остановка", 2), ("БЦ", 3), ("кафе", 2), ("банк", 1), ("мечеть", 1), ("ТД", 2)]
NOISE_TAIL = ["тел 8701{d7}", "тел. +7 707 {d3} {d2} {d2}", "+77{d9}", "позвонить заранее", "оставить у двери",
              "домофон {d2}", "код {d4}", "до 18:00", "доставка", "звонить перед доставкой", "вход со двора",
              "самовывоз", "оплата при получении"]
PREFIX = [("Казахстан", 10), ("Республика Казахстан", 5), ("РК", 8), ("Kazakhstan", 2), ("KZ", 1)]
KK_FOLD_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}
KW_TYPOS = {"подъезд": ["подьезд", "подезд", "подъез"], "квартира": ["кваритра", "квартра"], "этаж": ["этж", "етаж"],
            "микрорайон": ["микрорайн", "микраройон", "мирорайон"], "улица": ["улца", "улиа"], "проспект": ["проспек", "пропект"]}
INVIS = ["​", " ", "﻿", "‎", " "]


def wpick(rnd: random.Random, items):
    tot = sum(w for _, w in items)
    r = rnd.random() * tot
    for v, w in items:
        r -= w
        if r <= 0:
            return v
    return items[-1][0]


def translit(s: str) -> str:
    out = []
    for ch in s:
        lo = ch.lower()
        t = KK_FOLD_TRANSLIT.get(lo)
        if t is None:
            out.append(ch)
        else:
            out.append(t.capitalize() if ch != lo and t else t)
    return "".join(out)


# ------------------------------------------------------------------ pools from the gazetteer (fallback: seed YAML)
class Pools:
    def __init__(self, path: str | None, rnd: random.Random):
        self.rnd = rnd
        self.lex = get_lexicon()
        self.prov_by_canon = {p["canonical"]: p for p in self.lex.provinces}
        self.cities: list[dict] = []
        self.streets: dict[int, list[dict]] = defaultdict(list)
        self.living: dict[int, list[dict]] = defaultdict(list)
        self.districts: dict[int, list[dict]] = defaultdict(list)
        self.houses: list[str] = []
        self.pois: dict[int, list[str]] = defaultdict(list)
        self.global_pois: list[str] = []
        if path and Path(path).exists():
            self._load_sqlite(path)
        self._load_seed()
        self.city_weights = [c["weight"] for c in self.cities]
        self.all_streets = [s for v in self.streets.values() for s in v]

    def _load_sqlite(self, path: str) -> None:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute("SELECT id, name, name_kk, name_en, place, province, district, population, n_streets FROM settlements "
                           "WHERE place IN ('city','town','village') AND name IS NOT NULL").fetchall()
        for sid, name, kk, en, place, prov, dist, pop, nst in rows:
            if not prov or not re.match(r"^[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]", name or ""):
                continue
            w = (pop or 0) / 1000 + nst * 0.2 + {"city": 20, "town": 5, "village": 0.3}[place]
            if name in REPUBLICAN:
                w *= 3
            self.cities.append({"id": sid, "name": name, "kk": kk, "en": en, "place": place, "province": prov,
                                "rural_district": dist, "weight": w})
        for sid, name, base, typ in con.execute("SELECT settlement_id, name, base, type FROM streets"):
            self.streets[sid].append({"name": name, "base": base, "type": typ})
        for sid, base, kind, did in con.execute("SELECT settlement_id, base, kind, district_id FROM living_areas"):
            self.living[sid].append({"base": base, "kind": kind})
        for did, sid, name, kk in con.execute("SELECT id, settlement_id, name, name_kk FROM city_districts"):
            self.districts[sid].append({"name": name, "kk": kk})
        self.houses = [h for (h,) in con.execute("SELECT house FROM house_samples") if h]
        for sid, name, kind in con.execute("SELECT settlement_id, name, kind FROM pois"):
            if name and len(name) <= 40:
                self.pois[sid].append(name)
                self.global_pois.append(name)
        con.close()

    def _load_seed(self) -> None:
        """Seed YAML: guarantees every city of the convention examples exists, even without OSM."""
        have = {c["name"] for c in self.cities}
        for rec in {id(v): v for v in self.lex.city_by_name.values()}.values():
            if rec["name"] in have:
                continue
            sid = -len(self.cities) - 1
            self.cities.append({"id": sid, "name": rec["name"], "kk": rec.get("kk"), "en": None, "place": "town",
                                "province": rec["province"], "rural_district": rec.get("district"), "weight": 3.0})
            for d in self.lex.city_districts.get(rec["name"], []):
                self.districts[sid].append({"name": d, "kk": None})
            for m in (self.lex.microdistricts.get(rec["name"]) or {}):
                self.living[sid].append({"base": m, "kind": "микрорайон"})
        if not self.houses:
            self.houses = [str(i) for i in range(1, 300)]
        if not self.global_pois:
            self.global_pois = ["Береке", "Магнум", "Small", "Анвар", "Мега", "Достык Плаза", "Арзан", "Керемет"]

    def city(self) -> dict:
        return self.rnd.choices(self.cities, weights=self.city_weights, k=1)[0]

    def street(self, city: dict) -> dict:
        pool = self.streets.get(city["id"])
        if pool and self.rnd.random() < 0.93:
            return self.rnd.choice(pool)
        return self.rnd.choice(self.all_streets) if self.all_streets else {"name": "улица Абая", "base": "Абая", "type": "улица"}


# ------------------------------------------------------------------ record builder
class Rec:
    """Pieces + gold. `add(text, field)` appends; `sep()` appends a separator piece."""

    def __init__(self, rnd: random.Random, style: dict):
        self.rnd, self.style = rnd, style
        self.pieces: list[list] = []          # [text, field]
        self.gold = {f: None for f in FIELDS}
        self.city_mapped = False              # Kazakh spelling written, Russian name in gold (Q2)
        self.house_join = ""                  # «12 корпус 2» → gold «12/2» (Q7)

    def add(self, text: str, field: str | None = None):
        if text:
            self.pieces.append([text, field])

    def sp(self):
        self.add(" ")

    def sep(self):
        self.add(self.style["sep"] if self.rnd.random() > 0.15 else self.rnd.choice([", ", " ", ",", ". ", "; ", " , "]))


def kw_num(r: Rec, kw: str, num: str, field: str, kw_first_p: float, glue_p: float):
    """«кв 12» / «12 кв» / «кв12» / «12кв» / «кв.12»."""
    rnd = r.rnd
    glue = rnd.random() < glue_p
    if rnd.random() < kw_first_p:
        r.add(kw)
        if not glue or not re.search(r"[^\W\d_]$", kw):
            r.add(" " if not kw.endswith(".") or rnd.random() < 0.6 else "")
        r.add(num, field)
    else:
        r.add(num, field)
        r.add("" if glue else ("-" if rnd.random() < 0.03 else " "))
        r.add(kw)


def gen_house(p: Pools, rnd: random.Random) -> str:
    x = rnd.random()
    if x < 0.45 and p.houses:
        h = rnd.choice(p.houses).strip()
        if re.fullmatch(r"\d{1,4}[^\W\d_]?(/\d{1,4}[^\W\d_]?)?", h):
            return h
    n = str(int(rnd.paretovariate(1.1) * 3) % 400 + 1)
    y = rnd.random()
    if y < 0.12:
        return f"{n}/{rnd.randint(1, 12)}"
    if y < 0.24:
        return n + rnd.choice("абвгдАБВГ")
    if y < 0.26:
        return f"{n}/{rnd.randint(1, 9)}{rnd.choice('аб')}"
    return n


def typo(word: str, rnd: random.Random) -> str:
    if len(word) < 5 or not word.isalpha():
        return word
    i = rnd.randint(1, len(word) - 2)
    op = rnd.random()
    if op < 0.4:
        return word[:i] + word[i + 1:]
    if op < 0.7:
        return word[:i] + word[i] + word[i:]
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def build_record(p: Pools, rnd: random.Random) -> Rec:
    kk = rnd.random() < 0.15                     # Kazakh keywords and word order
    latin = (not kk) and rnd.random() < 0.05
    style = {"sep": wpick(rnd, [(", ", 70), (" ", 18), (",", 10), (". ", 2)])}
    r = Rec(rnd, style)
    g = r.gold
    city = p.city()
    is_rep = city["name"] in REPUBLICAN
    is_village = city["place"] == "village"
    blocks: list[tuple[str, callable]] = []

    # ---- province
    def province_block():
        prov = p.prov_by_canon.get(city["province"])
        if prov is None or prov.get("city"):
            return False
        x = rnd.random()
        if kk and prov.get("kk"):
            name = prov["kk"].replace(" облысы", "")
            r.add(name, "province")
            r.sp()
            r.add(wpick(rnd, KW_KK["province_after"]), "province")
        else:
            adj = prov["canonical"].replace(" область", "") if prov["canonical"].endswith(" область") else None
            abbr = [v for v in prov["variants"] if v.isalpha() and len(v) == 3 and v.upper() == v.upper()]
            if adj is None:          # «область Абай»
                name = prov["canonical"]
                r.add(name if rnd.random() < 0.6 else name.replace("область ", "обл. "), "province")
            elif abbr and x < 0.25:
                r.add(abbr[0].upper(), "province")
            else:
                r.add(adj if rnd.random() > 0.05 else adj.lower(), "province")
                r.sp()
                r.add(wpick(rnd, KW["province_after"]), "province")
        g["province"] = prov["canonical"]
        return True

    # ---- city
    city_written = {"text": None}

    def city_block():
        name = city["name"]
        if latin:
            name = city.get("en") or translit(name)
        written = name
        if kk and city.get("kk") and rnd.random() < 0.7:
            written = city["kk"]
        if is_village and not latin:
            if kk and rnd.random() < 0.6:
                r.add(written, "city"); r.sp(); r.add(wpick(rnd, KW_KK["village_suffix"]))
            else:
                kw = wpick(rnd, KW["village"])
                r.add(kw); r.add("" if kw.endswith(".") and rnd.random() < 0.3 else " "); r.add(written, "city")
        else:
            x = rnd.random()
            if kk and x < 0.6:
                r.add(written, "city"); r.sp(); r.add(wpick(rnd, KW_KK["city_suffix"]))
            elif x < 0.45 and not latin:
                kw = wpick(rnd, KW["city"])
                r.add(kw); r.add("" if kw.endswith(".") and rnd.random() < 0.35 else " "); r.add(written, "city")
            elif x < 0.55 and not latin and city.get("kk") and not kk:
                # «город Актобе/Ақтөбе қаласы» splice
                kw = wpick(rnd, [("город", 5), ("г.", 3), ("", 2)])
                if kw:
                    r.add(kw); r.sp()
                r.add(written, "city"); r.add(rnd.choice(["/", " / ", "/"])); r.add(city["kk"]); r.sp(); r.add("қаласы")
            elif x < 0.60 and not latin:
                r.add(written, "city"); r.add(rnd.choice([" ", ", "])); r.add(written if rnd.random() < 0.8 else written.upper())
            else:
                if rnd.random() < 0.06:
                    written = written.lower() if rnd.random() < 0.6 else written.upper()
                r.add(written, "city")
        city_written["text"] = written
        # gold: Kazakh spelling → Russian name (Q2); Latin / Russian as written
        if written == city.get("kk") and city.get("kk") != city["name"]:
            g["city"] = city["name"]
            r.city_mapped = True
        else:
            g["city"] = written
        if is_rep:
            g["province"] = city["name"]
        return True

    # ---- district
    dists = p.districts.get(city["id"]) or []
    rural = city.get("rural_district")

    def district_block():
        if dists and not is_village:
            d = rnd.choice(dists)
            name = d["kk"] if kk and d.get("kk") else d["name"]
        elif rural:
            name = rural
        else:
            return False
        if kk:
            r.add(name, "district"); r.sp(); r.add(wpick(rnd, KW_KK["district_after"]))
        elif rnd.random() < 0.75 or not name.endswith(("ский", "ской", "кий")):
            r.add(name, "district"); r.sp(); r.add(wpick(rnd, KW["district_after"]))
        else:
            r.add(wpick(rnd, KW["district_before"])); r.sp(); r.add(name, "district")
        if name.endswith(("ский", "кий")) is False and rnd.random() < 0.0:
            pass
        g["district"] = name
        return True

    # ---- street / microdistrict-as-street
    lives = p.living.get(city["id"]) or []
    st = p.street(city)
    micro_mode = rnd.random() < (0.30 if city["place"] in ("city", "town") else 0.08)
    living_written = {"on": False}

    def living_block():
        cands = [l for l in lives if l["base"] and len(l["base"]) < 30]
        if not cands:
            return False
        l = rnd.choice(cands)
        base = l["base"]
        if rnd.random() < 0.3 and not re.search(r"\d", base):
            base = f"{base}{rnd.choice(['-', ' ', '-'])}{rnd.randint(1, 12)}"
        if kk and l["kind"] == "ЖК" and rnd.random() < 0.6:
            r.add(base, "living_area"); r.sp(); r.add("тұрғын үй кешені")
        elif kk and l["kind"] in ("микрорайон", "") and rnd.random() < 0.5:
            r.add(base, "living_area"); r.sp(); r.add(wpick(rnd, KW_KK["micro_after"]))
        else:
            kw = {"ЖК": wpick(rnd, [("ЖК", 30), ("жк", 6), ("Ж/К", 2), ("жилой комплекс", 3)]),
                  "жилой массив": wpick(rnd, [("ж/м", 5), ("жилой массив", 2), ("мкр", 2)])}.get(
                      l["kind"], wpick(rnd, [("мкр", 20), ("мкр.", 10), ("микрорайон", 5), ("", 4)]))
            if kw:
                r.add(kw); r.add(" " if not kw.endswith(".") or rnd.random() < 0.7 else "")
            q = rnd.random() < 0.15 and l["kind"] == "ЖК"
            if q:
                r.add("«" if rnd.random() < 0.5 else '"')
            r.add(base, "living_area")
            if q:
                r.add("»" if rnd.random() < 0.5 else '"')
        g["living_area"] = base
        living_written["on"] = True
        return True

    def street_block():
        """Returns True if a street was written; may also write the house (micro patterns need it)."""
        if micro_mode:
            return micro_street()
        typ, base = st.get("type"), st.get("base") or st.get("name")
        if latin:
            base = translit(base)
        if rnd.random() < 0.03:
            base = typo(base, rnd)
        if rnd.random() < 0.08:
            base = base.lower()
        x = rnd.random()
        lemma = typ
        if typ is None and x < 0.6:
            lemma = "улица" if rnd.random() < 0.85 else None    # OSM has no type → most often «ул.»
        if lemma and kk and lemma in KW_KK["street_after"] and rnd.random() < 0.85:
            r.add(base, "street_name"); r.sp(); r.add(wpick(rnd, KW_KK["street_after"][lemma]), "street_type")
        elif lemma and x < 0.92 and lemma in STREET_KW_BEFORE and rnd.random() < 0.88:
            kw = wpick(rnd, STREET_KW_BEFORE[lemma])
            if latin:
                kw = {"улица": rnd.choice(["ul.", "ul", "str.", "street"]), "проспект": rnd.choice(["pr.", "prospekt", "ave", "avenue"]),
                      "микрорайон": "mkr"}.get(lemma, kw)
            if rnd.random() < 0.04 and lemma in KW_TYPOS:
                kw = rnd.choice(KW_TYPOS[lemma])
            if kw == "ул .":
                r.add("ул", "street_type"); r.add(" ."); r.add(base, "street_name")
            else:
                r.add(kw, "street_type")
                r.add(" " if not kw.endswith(".") or rnd.random() < 0.65 else "")
                r.add(base, "street_name")
        elif lemma and lemma in STREET_KW_AFTER and rnd.random() < 0.5:
            r.add(base, "street_name"); r.sp(); r.add(wpick(rnd, STREET_KW_AFTER[lemma]), "street_type")
        else:
            lemma = None
            r.add(base, "street_name")
        g["street_type"] = lemma
        g["street_name"] = base
        return True

    def micro_street():
        named = [l for l in lives if l["kind"] in ("микрорайон", "") and l["base"] and len(l["base"]) < 25
                 and not re.search(r"\d", base_of_numbered(l["base"]) or "x")]
        use_num = (not named) or rnd.random() < 0.5
        lemma = "микрорайон" if rnd.random() < 0.88 else "квартал"
        if use_num:
            name = str(rnd.randint(1, 35) if lemma == "микрорайон" else rnd.randint(1, 450))
        else:
            name = base_of_numbered(rnd.choice(named)["base"])
            if rnd.random() < 0.45:
                name = f"{name}{rnd.choice(['-', ' ', '-', ''])}{rnd.randint(1, 9)}{rnd.choice(['', '', '', 'а', 'б', 'А'])}"
                if re.search(r"[^\W\d_]\d", name):          # «Аксай3» would glue into one token pair; keep separable
                    name = name.replace(name.rstrip("0123456789абА"), name.rstrip("0123456789абА") + " ", 1)
        if latin:
            name = translit(name)
        x = rnd.random()
        if kk and lemma == "микрорайон" and x < 0.7:
            if use_num and rnd.random() < 0.5:
                r.add(name, "street_name"); r.add(rnd.choice(["-ші", "-шы", ""])); r.sp()
            else:
                r.add(name, "street_name"); r.sp()
            kw = wpick(rnd, KW_KK["micro_after"])
            r.add(kw, "street_type")
        elif x < 0.55:
            kw = wpick(rnd, STREET_KW_BEFORE[lemma])
            glue = use_num and rnd.random() < 0.15
            r.add(kw, "street_type")
            r.add("" if glue or (kw.endswith(".") and rnd.random() < 0.4) else " ")
            r.add(name, "street_name")
        else:
            r.add(name, "street_name")
            r.add("" if use_num and rnd.random() < 0.3 else " ")
            r.add(wpick(rnd, STREET_KW_AFTER[lemma]), "street_type")
        g["street_type"], g["street_name"] = lemma, name
        # «Аксай 3а» needs the house to be clearly separated (comma or keyword): convention rule 2
        return True

    # ---- house and details
    def house_block(after_street: bool):
        h = gen_house(p, rnd)
        hk = KW_KK["house"] if kk else KW["house"]
        x = rnd.random()
        name_has_num = bool(g["street_name"] and re.search(r"\d", g["street_name"]))
        if micro_mode or name_has_num:
            # separation is mandatory: «, 59» or a keyword
            if x < 0.55:
                r.sep(); kw_num(r, wpick(rnd, hk), h, "house", 0.45 if not kk else 0.1, 0.25)
            else:
                r.add(rnd.choice([", ", ","])); r.add(h, "house")
        elif x < 0.62 and after_street:
            r.add(" " if rnd.random() < 0.9 else ""); r.add(h, "house")     # «Абая 12» / «Назарбаева8»
        elif x < 0.75:
            r.add(rnd.choice([" ", ", "])); r.add(h, "house")
        else:
            r.sep(); kw_num(r, wpick(rnd, hk), h, "house", 0.75 if not kk else 0.15, 0.2)
        g["house"] = h
        if rnd.random() < 0.025:
            k = str(rnd.randint(1, 5)) if rnd.random() < 0.7 else rnd.choice("АБВ")
            r.add(rnd.choice([" корпус ", " корп. ", ", блок ", " блок "]) + k)
            r.house_join = f"/{k}"
        return True

    details = []
    if rnd.random() < 0.72:
        details.append("apartment")
    if rnd.random() < 0.22:
        details.append("entrance")
    if rnd.random() < 0.18:
        details.append("floor")
    rnd.shuffle(details)
    if "apartment" in details and rnd.random() < 0.6:
        details.remove("apartment"); details.append("apartment")

    num_first_style = rnd.random() < (0.55 if kk else 0.25)       # «5 подъезд 239 кв» strings
    dash_apartment = (not micro_mode) and "apartment" in details and rnd.random() < 0.05

    def details_block():
        for f in details:
            if f == "apartment" and dash_apartment:
                continue
            hi = {"apartment": 450, "entrance": 12, "floor": 25}[f]
            n = str(min(hi, int(rnd.paretovariate(0.9)) + rnd.randint(0, 3)) if f != "apartment" else rnd.randint(1, hi))
            if f == "apartment" and rnd.random() < 0.02:
                n += rnd.choice("аб")
            kwset = (KW_KK if kk else KW)[f]
            kw = wpick(rnd, kwset)
            if latin and f == "apartment":
                kw = rnd.choice(["kv", "kv.", "flat", "apt"])
            if rnd.random() < 0.03 and f == "entrance":
                kw = rnd.choice(KW_TYPOS["подъезд"])
            if kk and rnd.random() < 0.3 and f in ("floor", "entrance"):
                r.sep(); r.add(n, f); r.add(rnd.choice(["-ші ", "-шы ", "-інші "])); r.add(kw)
            else:
                r.sep(); kw_num(r, kw, n, f, 0.25 if num_first_style else 0.8, 0.22)
            g[f] = n

    def landmark_block():
        x = rnd.random()
        pois = p.pois.get(city["id"]) or p.global_pois
        name = rnd.choice(pois) if pois else "Береке"
        if x < 0.08:
            text = f"школа №{rnd.randint(1, 180)}"
        elif x < 0.14:
            text = f"Qazpost {rnd.choice(pois)}" if pois else "Qazpost"
        elif re.match(r"^(магазин|ТЦ|ТРЦ|ТД|БЦ|супермаркет|рынок|аптека|школа|кафе)\b", name, re.I):
            text = name
        else:
            text = f"{wpick(rnd, LANDMARK_KIND)} {name}"
        if rnd.random() < 0.3:
            text = text.lower()
        r.sep()
        if rnd.random() < 0.3:
            r.add(wpick(rnd, KW["landmark_marker"])); r.sp()
        r.add(text, "landmark")
        g["landmark"] = text

    # ---------------------------------------------------------------- assemble
    if rnd.random() < 0.06:
        r.add(f"{rnd.randint(10, 19)}{rnd.randint(0, 9)}{rnd.randint(0, 9)}00"); r.sep()
    if rnd.random() < 0.06:
        r.add(wpick(rnd, PREFIX)); r.sep()

    want_city = rnd.random() < 0.85
    district_only = (not want_city) and dists and rnd.random() < 0.35
    want_prov = (not is_rep) and rnd.random() < (0.28 if want_city else 0.1)
    want_dist = (rnd.random() < (0.22 if not is_village else 0.35)) or district_only
    want_living = (not micro_mode) and rnd.random() < 0.12
    want_landmark = rnd.random() < 0.07
    city_at_end = want_city and rnd.random() < 0.08
    living_after = rnd.random() < 0.3                # «пр. Аль-Фараби 77/8, ЖК Esentai City, кв 45»

    head = []
    if want_prov:
        head.append(province_block)
    if want_dist and is_village:
        head.append(district_block)
    if want_city and not city_at_end:
        head.append(city_block)
    if want_dist and not is_village:
        head.append(district_block)
    if want_dist and want_prov and rnd.random() < 0.1:
        head.reverse()
    wrote_any = False
    for b in head:
        if wrote_any:
            r.sep()
        if b():
            wrote_any = True
        elif r.pieces and r.pieces[-1][1] is None:
            r.pieces.pop()
    if want_living and not living_after:
        if wrote_any:
            r.sep()
        if living_block():
            wrote_any = True
    if want_landmark and rnd.random() < 0.25:
        landmark_block(); want_landmark = False; wrote_any = True
    if wrote_any:
        r.sep()
    street_block()
    house_block(after_street=True)
    if want_living and living_after:
        r.sep()
        if not living_block() and r.pieces and r.pieces[-1][1] is None:
            r.pieces.pop()
    if dash_apartment:
        n = str(rnd.randint(1, 300))
        r.add(rnd.choice(["-", "-", " - "])); r.add(n, "apartment"); g["apartment"] = n
    details_block()
    if want_landmark:
        landmark_block()
    if city_at_end:
        r.sep(); city_block()
    if rnd.random() < 0.04:
        tail = rnd.choice(NOISE_TAIL).format(d2=rnd.randint(10, 99), d3=rnd.randint(100, 999), d4=rnd.randint(1000, 9999),
                                              d7=rnd.randint(1000000, 9999999), d9=rnd.randint(100000000, 999999999))
        r.sep(); r.add(tail)
    if not want_city and not district_only and g["province"] is None:
        pass
    if not living_written["on"]:
        g["living_area"] = None
    return r


# ------------------------------------------------------------------ noise over the rendered pieces
def noise(r: Rec, rnd: random.Random) -> None:
    ps = r.pieces
    x = rnd.random()
    if x < 0.10:
        for pc in ps:
            pc[0] = pc[0].lower()
    elif x < 0.13:
        for pc in ps:
            pc[0] = pc[0].upper()
    if rnd.random() < 0.06:                  # invisible characters inside separators
        seps = [pc for pc in ps if pc[1] is None and " " in pc[0]]
        if seps:
            pc = rnd.choice(seps)
            pc[0] = pc[0].replace(" ", rnd.choice(INVIS) + (" " if rnd.random() < 0.5 else ""), 1)
    if rnd.random() < 0.04:                  # NBSP everywhere
        for pc in ps:
            if pc[1] is None:
                pc[0] = pc[0].replace(" ", " ")
    if rnd.random() < 0.05:                  # doubled spaces
        for pc in ps:
            if pc[1] is None and pc[0] == " ":
                pc[0] = "  "
                break
    if rnd.random() < 0.03:                  # leading/trailing junk
        ps.insert(0, [rnd.choice([" ", "﻿", "-", "  "]), None])
    # gold follows case changes of values (jury lowercases anyway, but keep exactness)
    for f in FIELDS:
        pass


def render(r: Rec) -> tuple[str, dict[str, tuple[int, int]]]:
    raw, spans, pos = "", {}, 0
    for text, f in r.pieces:
        if f:
            # pieces of one field separated only by whitespace form ONE span (province name + «облысы»),
            # matching the rule grammar's spans
            if f in spans and raw[spans[f][1]:pos].strip() == "":
                spans[f] = (spans[f][0], pos + len(text))
            elif f not in spans:
                spans[f] = (pos, pos + len(text))
        raw += text
        pos += len(text)
    return raw, spans


def finalize_gold(r: Rec, raw: str, spans: dict) -> dict:
    """Values that are cut from the string follow the (possibly case-changed) text; normalized ones don't."""
    g = dict(r.gold)
    for f in ("city", "district", "living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark"):
        if f in spans and g.get(f) is not None:
            if f == "city" and r.city_mapped:
                continue
            g[f] = raw[spans[f][0]:spans[f][1]]
    if g.get("house") and r.house_join:
        g["house"] += r.house_join
    return g


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=120000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gazetteer", default=str(ROOT / "api/artifacts/gazetteer.sqlite"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--gold-out", help="also write the jury-format gold for these rows (for end-to-end eval)")
    ap.add_argument("--id-prefix", default="sg")
    a = ap.parse_args(argv)
    rnd = random.Random(a.seed)
    pools = Pools(a.gazetteer, rnd)
    print(f"pools: {len(pools.cities)} settlements, {len(pools.all_streets)} streets, "
          f"{sum(len(v) for v in pools.living.values())} living areas, {len(pools.houses)} house numbers", file=sys.stderr)
    n_ok = n_skip = 0
    golds = []
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        while n_ok < a.n:
            rec = build_record(pools, rnd)
            noise(rec, rnd)
            raw, spans = render(rec)
            toks = tokenize(clean(raw))
            starts, ends = {t.start for t in toks}, {t.end for t in toks}
            if not toks or any(s not in starts or e not in ends for s, e in spans.values()):
                n_skip += 1
                continue
            labels = to_bio(raw, spans, [(t.text, t.start, t.end) for t in toks])
            gold = finalize_gold(rec, raw, spans)
            rid = f"{a.id_prefix}_{n_ok}"
            f.write(json.dumps({"id": rid, "raw": raw,
                                "tokens": [{"text": t.text, "start": t.start, "end": t.end} for t in toks],
                                "labels": labels, "city": "synthetic", "fields": gold}, ensure_ascii=False) + "\n")
            if a.gold_out:
                golds.append({"id": rid, "raw_address": raw, **gold})
            n_ok += 1
    if a.gold_out:
        Path(a.gold_out).write_text(json.dumps(golds, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{n_ok} rows → {a.out} (skipped {n_skip} misaligned)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
