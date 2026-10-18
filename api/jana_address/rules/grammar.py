"""Rule grammar v1 — deterministic span extraction over tokens.

Design:
  * Every pass only assigns LABELS to tokens; values are cut from the raw string later
    by offsets (see assembly). The grammar never rewrites text.
  * Keyword lexicon (lexicon.yaml) → token roles. Province table (province.yaml) and the
    seed gazetteer (places_seed.yaml) give recognizers for provinces / cities / districts.
  * Passes, in order:
      0  classify tokens (roles, postcode, noise)
      1  province (n-gram match)
      2  number ↔ keyword binding for house / apartment / entrance / floor
         (glued first, then single-candidate, then dominant style)
      3  street with a type keyword (Case A: type before name, B: name before type, C: type + number)
      4  district (word + «район» / «район» + word)
      5  city (keyword + name, Kazakh suffix, seed match, bilingual duplicates)
      6  living area (ЖК ...) and the «микрорайон + separate street» rule
      7  street without a type keyword (first word chunk followed by a number)
      8  house without a keyword (first free number after the street)
      9  landmark (keyword + following words)
  * Flags record every heuristic decision; the confidence model consumes them.

This module is intentionally verbose and linear so juniors can step through it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..preprocess import extract, clean, match_view, norm_token
from ..tokenizer import Token, tokenize

HERE = Path(__file__).parent

DETAIL_FIELDS = ("APARTMENT", "ENTRANCE", "FLOOR")
NUMBER_FIELDS = ("HOUSE",) + DETAIL_FIELDS
ADJ_SUFFIXES = ("ский", "ская", "ской", "ское", "кий", "кая", "ной", "ный", "ная", "ий", "ый", "ая")
NAME_PARTICLES = {"би", "батыр", "батыра", "хан", "хана", "ата", "ана", "акын", "акына"}   # «Казыбек би», «Райымбек батыр»
OFFICE_WORDS = {"оф", "офис", "офиса", "office", "ofis", "каб", "кабинет", "кабинете", "kabinet", "бутик", "павильон", "бокс", "секция"}
NUMBERED_TYPES = {"линия", "аллея", "проезд", "квартал"}     # «5 линия», «линия 4», «7 аллея»: the number IS the name
NUMBERED_TOKENS = {"көше", "koshe"}                           # «12 көше» (bare Kazakh «street»), not «Абай көшесі»
CORRECTION_MARKERS = {"нет", "исправляю", "исправление", "ой", "точнее", "вернее", "жоқ", "қателестім", "correction", "sorry"}
# noise words that never start or end a name (a name may still start with «на», «у», «и.» — an initial, a preposition in an OSM name)
TRIM_NOISE = {"частный", "частном", "частного", "номер", "ном", "нөмір", "исправляю", "точнее", "вернее", "жоқ",
              "қателестім", "correction", "тел", "телефон", "получатель", "получателя", "заказ", "курьер", "курьеру", "позвонить",
              "звонить", "заранее", "доставка", "самовывоз", "уточнить", "оставить", "адрес", "индекс"}
PAREN_MARKERS = {"бывш", "бывшая", "бывший", "бывшее", "бывшие", "старое", "старая", "ранее", "прежнее", "ex", "formerly", "ныне", "now"}
TIME_WORDS = {"час", "часа", "часов", "минут", "минуту", "минуты", "день", "дня", "сутки", "неделю", "полчаса"}
CODE_WORDS = {"код", "кодом", "домофон", "домофона", "intercom", "пин"}
CITY_WORDS_URBAN = {"город", "г", "гор", "қаласы", "каласы", "қ", "қала", "кала"}
_HOUSE_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?$")


@dataclass
class Span:
    field: str
    start: int
    end: int
    rule: str
    score: float = 0.9


@dataclass
class RuleParse:
    raw: str
    tokens: list[Token]
    spans: dict[str, Span] = field(default_factory=dict)      # field -> chosen span
    flags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)                   # province_canonical, street_type_lemma, ...


class Lexicon:
    """Loads lexicon.yaml / province.yaml / places_seed.yaml into lookup tables."""

    def __init__(self, rules_dir: Path = HERE):
        lex = yaml.safe_load((rules_dir / "lexicon.yaml").read_text(encoding="utf-8"))
        prov = yaml.safe_load((rules_dir / "province.yaml").read_text(encoding="utf-8"))
        seed = yaml.safe_load((rules_dir / "places_seed.yaml").read_text(encoding="utf-8"))

        self.roles: dict[str, set[str]] = {}
        simple_roles = ["city_kw", "city_suffix_kk", "province_kw", "district_kw", "living_kw", "house_kw",
                        "building_kw", "apartment_kw", "entrance_kw", "floor_kw", "ambiguous_p",
                        "landmark_kw", "landmark_marker_only", "noise"]
        for role in simple_roles:
            for w in lex.get(role, []) or []:
                self.roles.setdefault(str(w).lower(), set()).add(role)
        self.street_type_of: dict[str, str] = {}
        for lemma, variants in (lex.get("street_types") or {}).items():
            for v in variants or []:
                self.street_type_of[str(v).lower()] = lemma
                self.roles.setdefault(str(v).lower(), set()).add("street_type")
        self.microdistrict_like = set(lex.get("microdistrict_like", []))
        self.bigrams = lex.get("street_type_bigrams") or {}
        self.ordinal_suffix = {str(w).lower() for w in lex.get("ordinal_suffix", []) or []}
        self.conventions = yaml.safe_load((rules_dir / "conventions.yaml").read_text(encoding="utf-8")) or {}

        # provinces: variant (matching view) -> canonical
        self.province_by_variant: dict[str, dict] = {}
        self.provinces: list[dict] = prov["provinces"]
        for p in self.provinces:
            if p.get("city"):
                continue  # cities of republican significance are derived from the city, not matched as province
            for v in p["variants"]:
                self.province_by_variant[match_view(v)] = p
            # colloquial «Костанай обл», «Актобе область», «Ақтөбе обл.»: regional centre or Kazakh stem + region word
            stems = {p.get("center") or ""}
            if p.get("kk", "").endswith(" облысы"):
                stems.add(p["kk"][: -len(" облысы")])
            for stem in filter(None, stems):
                for w in ("обл", "область", "облысы"):
                    self.province_by_variant.setdefault(match_view(f"{stem} {w}"), p)
            for v in p["variants"]:                  # Latin: «Almaty obl», «Aktobe oblysy» from «almaty region»
                if v.endswith(" region"):
                    for w in ("obl", "oblast", "oblysy", "obl."):
                        self.province_by_variant.setdefault(match_view(f"{v[:-len(' region')]} {w}"), p)
        self.province_of_city_name: dict[str, str] = {}   # Астана/Нур-Султан/… -> canonical
        for p in self.provinces:
            if p.get("city"):
                for v in p["variants"]:
                    self.province_of_city_name[match_view(v)] = p["canonical"]
        self.max_prov_ngram = max(len(v.split()) for v in self.province_by_variant)

        # cities: matching view -> record
        self.city_by_name: dict[str, dict] = {}
        for c in seed["cities"]:
            keys = {c["name"], c.get("kk", "")} | set(c.get("aliases", []) or [])
            for k in keys:
                if k:
                    self.city_by_name.setdefault(match_view(k), c)
        # districts
        self.city_districts: dict[str, list[str]] = seed.get("city_districts", {})
        self.district_names: dict[str, str] = {}             # matching view -> (RU name)
        self.city_of_district: dict[str, str] = {}
        for city, names in self.city_districts.items():
            for n in names:
                self.district_names[match_view(n)] = n
                self.city_of_district.setdefault(match_view(n), city)
        self.city_districts_kk: dict[str, dict] = seed.get("city_districts_kk") or {}
        for city, mapping in (seed.get("city_districts_kk") or {}).items():
            for kk, ru in mapping.items():
                self.district_names.setdefault(match_view(kk), ru)
                self.city_of_district.setdefault(match_view(kk), city)
        self.microdistricts: dict[str, dict[str, dict]] = seed.get("microdistricts", {})

    # -- role helpers --------------------------------------------------------
    def roles_of(self, tok: Token) -> set[str]:
        if tok.kind != "WORD":
            return set()
        n = norm_token(tok.text)
        r = set(self.roles.get(n, set()))
        if not r:
            r = set(self.roles.get(match_view(n), set()))
        return r

    def street_type(self, tok: Token) -> str | None:
        n = norm_token(tok.text)
        return self.street_type_of.get(n) or self.street_type_of.get(match_view(n))


_LEX: Lexicon | None = None


def get_lexicon() -> Lexicon:
    global _LEX
    if _LEX is None:
        _LEX = Lexicon()
    return _LEX


# ----------------------------------------------------------------------------
class RuleParser:
    def __init__(self, lexicon: Lexicon | None = None):
        self.lex = lexicon or get_lexicon()

    # ---------------------------------------------------------------- public
    def parse(self, raw: str) -> RuleParse:
        text = clean(raw)
        toks = tokenize(text)
        rp = RuleParse(raw=raw, tokens=toks)
        if not toks:
            rp.flags.append("empty")
            return rp
        self._split_glued_dom(rp)
        self._split_glued_keyword(rp)        # «мкрСтроитель», «улАбая», «ТолебиДом» → keyword + name
        self._phones(rp)
        self._classify(rp)
        self._separators(rp)                 # «Актау/14 мкр/59/12»: slashes as separators; «а/я 15» is a PO box
        self._parentheticals(rp)             # «Ерубаева (бывш. Ленина)», «Almaty (Alma-Ata)»: the alias is not a value
        self._province(rp)
        self._bind_numbers(rp, phase=1)      # glued + unambiguous keyword↔number pairs
        self._street_with_type(rp)           # may bind the house after the street name
        self._bind_numbers(rp, phase=2)      # remaining pairs, now that the house is known
        self._house_fallback(rp)             # first free number after the street (before «корпус» is read)
        self._building_part(rp)              # «корпус 2» → house «12/2»
        self._district(rp)
        self._city(rp)
        self._bare_district(rp)              # «Астана, Сарыарка, ул. …»: a chunk that is a district of the city
        self._living_area(rp)
        self._street_fallback(rp)
        self._house_fallback(rp)
        self._house_letter(rp)               # «Абая 10 Б» → house «10Б»
        self._building_part(rp)              # the house may have been found only now
        self._dash_apartment(rp)             # «Абая 10-5» → apartment 5
        self._corrections(rp)                # «Абая 10, нет, 12», «кв 11 — исправляю: кв 12»: the last word wins
        self._landmark(rp)
        self._street_leftover(rp)            # «Айнабулак-3, дом 150»: the only free chunk is the street
        self._leftovers(rp)
        return rp

    # --------------------------------------------------------------- gazetteer-aware helpers (lazy import: osm.py imports us)
    def _known_settlement(self, text: str) -> bool:
        from ..gazetteer.osm import get_gazetteer
        return get_gazetteer().settlement(text) is not None

    def _fuzzy_city(self, text: str):
        from ..gazetteer.osm import get_gazetteer
        return get_gazetteer().fuzzy_city(text)

    def _living_anywhere(self, text: str) -> bool:
        from ..gazetteer.osm import get_gazetteer
        return get_gazetteer().living_base_anywhere(text)

    def _known_microdistrict(self, rp: RuleParse, text: str) -> bool:
        from ..gazetteer.osm import get_gazetteer
        city = rp.meta.get("city_known") or rp.meta.get("city_text")
        return bool(city) and get_gazetteer().is_microdistrict(city, text)

    def _dash_apartment(self, rp: RuleParse):
        """«Абая 10-5», «Ермекова 58/3-12», «Кутузова 204 - 12»: house-dash-number with no keyword → apartment."""
        toks = rp.tokens
        sp = rp.spans.get("HOUSE")
        if sp is None:
            return
        k = next((i for i, t in enumerate(toks) if t.end == sp.end), None)
        if k is None or k + 2 >= len(toks):
            return
        dash, num = toks[k + 1], toks[k + 2]
        house_text = extract(rp.raw, sp.start, sp.end) or ""
        if dash.kind == "PUNCT" and dash.text == "-" and self._free(num) and num.kind == "ALNUM" \
                and (k + 3 >= len(toks) or toks[k + 3].is_sep or toks[k + 3].role in ("apartment_kw", "entrance_kw", "floor_kw", "landmark_kw")):
            dash.label = num.label = "HOUSE"                  # «7-7а»: a pair of buildings, one house value («4в-94» stays house + apartment)
            sp.end = num.end
            sp.rule = "house_range"
            rp.flags.append("house_range")
            return
        if "APARTMENT" in rp.spans:
            return
        if "slash_separated" in rp.flags and dash.kind == "PUNCT" and dash.text == "/" and num.kind == "NUM" and self._free(num) \
                and (k + 3 >= len(toks) or toks[k + 3].is_sep):
            self._set(rp, "APARTMENT", toks, [k + 2], "slash_after_house", 0.6)   # «Актау/14 мкр/59/12»
            dash.label = "O"
            rp.flags.append("slash_apartment")
            return
        if dash.kind == "PUNCT" and dash.text == "-" and num.kind == "NUM" and self._free(num) \
                and (k + 3 >= len(toks) or toks[k + 3].is_sep or toks[k + 3].role in ("entrance_kw", "floor_kw", "landmark_kw")):
            self._set(rp, "APARTMENT", toks, [k + 2], "dash_after_house", 0.65)
            dash.label = "O"
            rp.flags.append("dash_apartment")

    def _street_leftover(self, rp: RuleParse):
        """House found via its keyword, no street, exactly one free word chunk left → that chunk is the street."""
        if "STREET_NAME" in rp.spans or "HOUSE" not in rp.spans:
            return
        toks = rp.tokens
        chunks, cur = [], []
        for i, t in enumerate(toks):
            if t.label is None and t.kind == "WORD" and t.role is None and match_view(t.text) not in self.lex.city_by_name:
                cur.append(i)
            elif cur:
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        if len(chunks) == 1:
            self._set(rp, "STREET_NAME", toks, chunks[0], "leftover_chunk", 0.55)
            rp.flags.append("street_without_type")
            rp.flags.append("street_from_leftover")

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _free(t: Token) -> bool:
        return t.label is None

    SKIP_PUNCT = (".", ":", "-", "№", "(", ")", '"', "«", "»", "'")

    def _skippable(self, t: Token) -> bool:
        """Punctuation inside phrases and Kazakh/Russian ordinal suffixes («11-ші», «2-й») are transparent."""
        return (t.kind == "PUNCT" and t.text in self.SKIP_PUNCT) or t.role == "ordinal"

    def _next_content(self, toks: list[Token], i: int) -> int | None:
        j = i + 1
        while j < len(toks) and self._skippable(toks[j]):
            j += 1
        return j if j < len(toks) else None

    def _prev_content(self, toks: list[Token], i: int) -> int | None:
        j = i - 1
        while j >= 0 and self._skippable(toks[j]):
            j -= 1
        return j if j >= 0 else None

    @staticmethod
    def _set(rp: RuleParse, fieldname: str, toks: list[Token], idxs: list[int], rule: str, score: float = 0.9):
        """Label tokens idxs with fieldname and record the span (min start .. max end).
        A name never starts or ends with a noise word («Нурсат частный дом» → «Нурсат»), unless it is all noise («ул. Казахстан»)."""
        if not idxs:
            return
        if fieldname in ("CITY", "DISTRICT", "LIVING_AREA", "STREET_NAME", "LANDMARK") and len(idxs) > 1:
            idxs = sorted(idxs)
            while len(idxs) > 1 and norm_token(toks[idxs[0]].text) in TRIM_NOISE and toks[idxs[0]].role != "ordinal":
                toks[idxs.pop(0)].label = "O"
            while len(idxs) > 1 and norm_token(toks[idxs[-1]].text) in TRIM_NOISE and toks[idxs[-1]].role != "ordinal":
                toks[idxs.pop()].label = "O"
        for k in idxs:
            toks[k].label = fieldname
        start = min(toks[k].start for k in idxs)
        end = max(toks[k].end for k in idxs)
        if fieldname not in rp.spans:
            rp.spans[fieldname] = Span(fieldname, start, end, rule, score)

    def _is_kw(self, t: Token) -> bool:
        return bool(t.role) and t.role not in ("noise",)

    def _phones(self, rp: RuleParse):
        """«87011234567», «+7 701 555 44 33», «8 (777) 123-45-67» → O (never a house / apartment)."""
        toks = rp.tokens
        n = len(toks)
        i = 0
        while i < n:
            t = toks[i]
            if t.kind == "NUM" and len(t.text) >= 9:
                t.label, t.role = "O", "phone"
                rp.flags.append("phone")
                i += 1
                continue
            j = i + 1 if t.text == "+" else i
            if j < n and toks[j].kind == "NUM" and toks[j].text in ("7", "8"):
                k, digits, idxs = j + 1, 1, [i, j] if j != i else [j]
                while k < n and (toks[k].kind == "NUM" or toks[k].text in ("(", ")", "-")) and digits < 11:
                    if toks[k].kind == "NUM":
                        digits += len(toks[k].text)
                    idxs.append(k)
                    k += 1
                if digits == 11:
                    for x in idxs:
                        toks[x].label, toks[x].role = "O", "phone"
                    rp.flags.append("phone")
                    i = k
                    continue
            i += 1

    def _split_glued_dom(self, rp: RuleParse):
        """«5мкр 12д 34кв»: when other numbers in the string are glued to keywords, «12д» is «12» + «д(ом)»,
        not a house with the letter Д. Splits the ALNUM token in place (offsets unchanged)."""
        toks = rp.tokens
        glued = sum(1 for a, b in zip(toks, toks[1:]) if a.kind == "NUM" and b.kind == "WORD" and not b.space_before
                    and (self.lex.roles_of(b) & {"house_kw", "apartment_kw", "entrance_kw", "floor_kw", "street_type"}))
        if glued == 0:
            return
        out: list[Token] = []
        for t in toks:
            if t.kind == "ALNUM" and t.text[-1] in "дД" and len(t.text) >= 2:
                out.append(Token(t.text[:-1], t.start, t.end - 1, "NUM", t.space_before))
                out.append(Token(t.text[-1], t.end - 1, t.end, "WORD", False))
                rp.flags.append("glued_dom_split")
            else:
                out.append(t)
        rp.tokens[:] = out

    _GLUED_KW = re.compile(r"^([a-zа-яё]{2,5})\.?([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][^\W\d_]{2,}.*)$")
    _GLUED_DISTRICT = re.compile(r"^(.{4,}?(?:ский|ская|кий|кая))(р-н|р/н|район|рн)$", re.I)   # «Талгарскийр-н»
    _GLUED_SUFFIX = re.compile(r"^([A-ZА-ЯЁ][a-zа-яё]{2,})(Дом|Кв|Квартира|Подъезд|Этаж|Мкр|Ул|Улица|Дача)$")   # «ТолебиДом»
    _GLUED_UPPER = re.compile(r"^(ЖК|ТРЦ|ТЦ|БЦ|СТ)([A-ZА-ЯЁ][a-zа-яё][^\W\d_]*)$")                    # «ЖКХайвил», not «СТЕПНОГОРСК»

    def _split_glued_keyword(self, rp: RuleParse):
        """A lower-case street/settlement keyword glued to a capitalized name: «мкрСтроитель», «улАбая», «пр.Абая» stays."""
        out: list[Token] = []
        for t in rp.tokens:
            m = self._GLUED_KW.match(t.text) if t.kind == "WORD" else None
            d = self._GLUED_DISTRICT.match(t.text) if t.kind == "WORD" else None
            if t.kind == "WORD" and not m and not d:
                d = self._GLUED_SUFFIX.match(t.text) or self._GLUED_UPPER.match(t.text)
            if m and (self.lex.street_type_of.get(m.group(1)) or {"living_kw", "city_kw"} & self.lex.roles.get(m.group(1), set())):
                cut = t.start + len(m.group(1))
                out.append(Token(m.group(1), t.start, cut, "WORD", t.space_before))
                out.append(Token(t.text[len(m.group(1)):], cut, t.end, "WORD", False))
                rp.flags.append("glued_keyword_split")
            elif d:
                cut = t.start + len(d.group(1))
                out.append(Token(d.group(1), t.start, cut, "WORD", t.space_before))
                out.append(Token(d.group(2), cut, t.end, "WORD", False))
                rp.flags.append("glued_keyword_split")
            else:
                out.append(t)
        rp.tokens[:] = out

    @staticmethod
    def _latin_name_word(t: Token) -> bool:
        """«Avenue», «House», «Residence» inside a Latin brand name: Capitalized Latin, 4+ letters, a keyword by role."""
        return (t.kind == "WORD" and len(t.text) >= 4 and t.text[0].isupper() and t.text[1:].islower()
                and t.text.isascii() and t.role in ("street_type", "house_kw", "living_kw"))

    def _separators(self, rp: RuleParse):
        """«Актау/14 мкр/59/12» (3+ slashes, no commas): slashes separate fields. «а/я 15» is a PO box → O."""
        toks = rp.tokens
        n = len(toks)
        for i, t in enumerate(toks):
            if norm_token(t.text) in ("а/я", "a/ya"):
                t.label, t.role = "O", "pobox"
                q = self._next_content(toks, i)
                if q is not None and toks[q].is_number_like:
                    toks[q].label = "O"
                    toks[q].role = "pobox"
                rp.flags.append("po_box")
        dashes = [i for i, t in enumerate(toks) if t.kind == "PUNCT" and t.text == "—"]
        if len(dashes) >= 3:                                   # «Павлодар — Кутузова 204 — 3 подъезд — …»; a road name has 1–2
            for i in dashes:
                toks[i].meta["sep"] = True
            rp.flags.append("dash_separated")
        slashes = [i for i, t in enumerate(toks) if t.is_slash]
        if len(slashes) >= 3 and not any(t.is_sep for t in toks):
            spaced = [i for i in slashes if toks[i].space_before or (i + 1 < n and toks[i + 1].space_before)]
            has_apt_kw = any(t.role == "apartment_kw" for t in toks)
            # «г.астана/ пр. … 53/2 / кв 117»: the spaced slashes separate, the tight «53/2» is a fraction;
            # «Актау/14 мкр/59/12»: nothing is spaced → every slash separates — except a tight «N/N» when «кв» is written
            # elsewhere («ул.сарайшык 34/1/кв 88»)
            for i in (spaced or slashes):
                tight_fraction = (0 < i < n - 1 and toks[i - 1].is_number_like and toks[i + 1].is_number_like
                                  and not toks[i].space_before and not toks[i + 1].space_before)
                if has_apt_kw and tight_fraction:
                    continue
                toks[i].meta["sep"] = True
            rp.flags.append("slash_separated")

    def _parentheticals(self, rp: RuleParse):
        """A bracket group holding a former/alternative name («(бывш. Ленина)», «(Astana)», «(Alma-Ata)») is not a value."""
        toks = rp.tokens
        n = len(toks)
        i = 0
        while i < n:
            if toks[i].kind == "PUNCT" and toks[i].text == "(":
                j = i + 1
                while j < n and not (toks[j].kind == "PUNCT" and toks[j].text == ")"):
                    j += 1
                if j < n:
                    words = [t for t in toks[i + 1:j] if t.kind != "PUNCT"]
                    text = " ".join(t.text for t in words)
                    prev = self._prev_content(toks, i)
                    same_city = (prev is not None and match_view(text) in self.lex.city_by_name
                                 and self.lex.city_by_name.get(match_view(toks[prev].text)) is self.lex.city_by_name[match_view(text)])
                    alias = bool(words) and (norm_token(words[0].text) in PAREN_MARKERS or same_city)
                    # «Нурпеисова (Акбулак)» keeps its bracket: OSM street names carry old names that way
                    if alias:
                        for k in range(i, j + 1):
                            toks[k].label = "O"
                            if toks[k].kind != "PUNCT":
                                toks[k].role = "paren"
                        rp.flags.append("alias_in_brackets")
                    i = j
            i += 1

    def _corrections(self, rp: RuleParse):
        """«ул. Абая 10, нет, 12»: a bare number right after a correction word replaces the most recent house/detail."""
        toks = rp.tokens
        for m, t in enumerate(toks):
            if norm_token(t.text) not in CORRECTION_MARKERS or t.kind != "WORD":
                continue
            q = m + 1
            while q < len(toks) and (toks[q].is_sep or self._skippable(toks[q]) or norm_token(toks[q].text) in CORRECTION_MARKERS):
                q += 1
            if q >= len(toks) or not toks[q].is_number_like or not self._free(toks[q]):
                continue
            recent = max(((f, sp) for f, sp in rp.spans.items() if f in ("HOUSE", "APARTMENT", "ENTRANCE", "FLOOR")
                          and sp.end <= t.start), key=lambda x: x[1].end, default=None)
            if recent is None:
                continue
            f, sp = recent
            for x in toks:
                if x.start >= sp.start and x.end <= sp.end:
                    x.label = None
            del rp.spans[f]
            self._set(rp, f, toks, self._house_idxs(toks, q) if f == "HOUSE" else [q], "corrected", 0.7)
            t.label = "O"
            rp.flags.append("correction_applied")

    # ------------------------------------------------------------- pass 0
    def _classify(self, rp: RuleParse):
        toks = rp.tokens
        for i, t in enumerate(toks):
            if t.role == "phone":
                continue
            if t.kind == "NUM" and len(t.text) == 6 and i + 1 < len(toks) and not toks[i + 1].is_number_like:
                t.label, t.role = "O", "postcode"
                rp.flags.append("postcode")
                continue
            # ordinal suffix right after a number («11-ші», «2-й», «3-го»): transparent, never a value
            if t.kind == "WORD" and norm_token(t.text) in self.lex.ordinal_suffix and i > 0:
                prev = toks[i - 1]
                prev2 = toks[i - 2] if i > 1 else None
                if prev.kind == "NUM" or (prev.kind == "PUNCT" and prev.text == "-" and prev2 is not None and prev2.kind == "NUM"):
                    t.role, t.label = "ordinal", "O"
                    continue
            roles = self.lex.roles_of(t)
            if not self.lex.conventions.get("office_as_apartment", True) and norm_token(t.text) in OFFICE_WORDS:
                roles.discard("apartment_kw")
            if "street_type" in roles and len(norm_token(t.text)) == 1 and not (t.end < len(rp.raw) and rp.raw[t.end] == "."):
                roles.discard("street_type")         # «у получателя» is not «улица получателя»; «у.» / «ш.» are
            if norm_token(t.text) in ("city", "town", "village"):   # «Astana city», «Kaskelen town» — «Esentai City» is a name
                prv = toks[i - 1] if i > 0 and toks[i - 1].kind == "WORD" else None
                nxt = toks[i + 1] if i + 1 < len(toks) and toks[i + 1].kind == "WORD" else None
                known = lambda x: x is not None and (match_view(x.text) in self.lex.city_by_name or self._known_settlement(x.text))
                roles = {"city_suffix_kk"} if known(prv) else ({"city_kw"} if known(nxt) else set())
            if t.text in ("СТ", "СНТ", "СДТ") and not (t.end < len(rp.raw) and rp.raw[t.end] == ".") \
                    and re.search(r"[а-яё]", rp.raw):
                roles = {"living_kw"}                # «СТ Мичуринец» = садовое товарищество; «ст. Шу» / «ст Шу» = станция
            if norm_token(t.text) in CODE_WORDS:
                t.role, t.label = "code_kw", "O"
                # «код подъезда 1234», «домофон 25», «код 12#»: the number (and a following «подъезда») are not values
                j = i + 1
                while j < len(toks) and j <= i + 2 and (toks[j].kind == "PUNCT" and toks[j].text in self.SKIP_PUNCT
                                                       or norm_token(toks[j].text) in ("подъезда", "подъезд", "двери", "домофона", "от")):
                    toks[j].label = "O"
                    j += 1
                if j < len(toks) and toks[j].is_number_like:
                    toks[j].label = "O"
                    rp.flags.append("door_code")
                continue
            if "street_type" in roles:
                t.role = "street_type"
                t.meta["lemma"] = self.lex.street_type(t)
            elif "ambiguous_p" in roles:
                t.role = "ambiguous_p"
            elif roles & {"apartment_kw", "entrance_kw", "floor_kw", "house_kw"}:
                # a word can be both (e.g. «п») — resolved later; pick the most specific role
                for r in ("apartment_kw", "entrance_kw", "floor_kw", "house_kw"):
                    if r in roles:
                        t.role = r
                        break
            elif "district_kw" in roles:
                t.role = "district_kw"
            elif "province_kw" in roles:
                t.role = "province_kw"
            elif "city_suffix_kk" in roles:
                t.role = "city_suffix_kk"
            elif "city_kw" in roles:
                t.role = "city_kw"
            elif "living_kw" in roles:
                t.role = "living_kw"
            elif "building_kw" in roles:
                t.role = "building_kw"
            elif "landmark_kw" in roles or "landmark_marker_only" in roles:
                t.role = "landmark_kw"
                if "landmark_marker_only" in roles:
                    t.meta["marker_only"] = True
            elif "noise" in roles:
                # noise words stay unlabeled: they may still be part of a name («ул. Казахстан»);
                # if nothing claims them they are dropped silently in _leftovers.
                t.role = "noise"
        # bigrams: «жилой массив», «жилой комплекс»
        for i in range(len(toks) - 1):
            a, b = toks[i], toks[i + 1]
            if a.kind == "WORD" and b.kind == "WORD":
                second = self.lex.bigrams.get(norm_token(a.text))
                if second and norm_token(b.text) in second:
                    lemma = second[norm_token(b.text)]
                    if lemma == "ЖК":
                        a.role = b.role = "living_kw"
                    else:
                        a.role = b.role = "street_type"
                        a.meta["lemma"] = b.meta["lemma"] = lemma
                        a.meta["bigram_head"] = True
        # «ЖК» written in capitals is a WORD «ЖК» → living_kw via lexicon already.

    # ------------------------------------------------------------- pass 1
    def _province(self, rp: RuleParse):
        toks = rp.tokens
        n = len(toks)
        i = 0
        while i < n:
            if toks[i].kind != "WORD" or not self._free(toks[i]):
                i += 1
                continue
            prev = self._prev_content(toks, i)
            if prev is not None and toks[prev].role == "street_type":
                i += 1                       # «ул. Карагандинская 119» is a street named after the region
                continue
            best = None
            # try longest n-gram of consecutive WORD tokens (dots allowed between)
            words: list[int] = []
            j = i
            while j < n and len(words) < self.lex.max_prov_ngram:
                if toks[j].kind == "WORD":
                    words.append(j)
                    key = match_view(" ".join(toks[k].text for k in words))
                    if key in self.lex.province_by_variant:
                        best = (list(words), self.lex.province_by_variant[key])
                elif toks[j].kind == "PUNCT" and toks[j].text == ".":
                    pass
                else:
                    break
                j += 1
            if best:
                idxs, p = best
                after = self._next_content(toks, idxs[-1])
                if after is not None and toks[after].role == "street_type":
                    i = idxs[-1] + 1             # «Акмолинская ул. 5» — a street named after the region
                    continue
                if len(idxs) == 1 and after is not None and toks[after].role != "province_kw" \
                        and (toks[after].is_number_like or toks[after].role == "house_kw"):
                    i = idxs[-1] + 1             # «алматинская д.8а», «Актюбинская 12» — a street, not the region
                    continue
                # swallow a following «область/обл» keyword
                nxt = self._next_content(toks, idxs[-1])
                if nxt is not None and toks[nxt].role == "province_kw":
                    idxs.append(nxt)
                self._set(rp, "PROVINCE", toks, idxs, "province_table", 0.95)
                rp.meta["province_canonical"] = p["canonical"]
                i = idxs[-1] + 1
                continue
            i += 1

    # ------------------------------------------------------------- pass 2
    def _bind_numbers(self, rp: RuleParse, phase: int = 2):
        """Bind house/apartment/entrance/floor keywords to a neighbouring number.

        phase 1: only glued («4подьезд») and single-candidate pairs — safe decisions.
        phase 2: after the street pass bound the house, resolve what is left; truly
                 ambiguous pairs follow the dominant style of the string (flagged).
        """
        toks = rp.tokens
        kw_idx = [i for i, t in enumerate(toks)
                  if t.role in ("house_kw", "apartment_kw", "entrance_kw", "floor_kw", "ambiguous_p") and self._free(t)]
        if not kw_idx:
            return
        has_kk = any(t.role == "city_suffix_kk" or norm_token(t.text) in ("пәтер", "көшесі", "қаласы", "үй") for t in toks)

        def field_of(t: Token) -> str:
            if t.role == "ambiguous_p":
                # п. + name = settlement (handled by city pass); п + number = пәтер (kk) or подъезд
                return "APARTMENT" if has_kk else "ENTRANCE"
            return {"house_kw": "HOUSE", "apartment_kw": "APARTMENT", "entrance_kw": "ENTRANCE", "floor_kw": "FLOOR"}[t.role]

        def cand_prev(i: int) -> int | None:
            p = self._prev_content(toks, i)
            if p is not None and toks[p].is_number_like and self._free(toks[p]):
                # «55/6» fraction belongs to the house: walk back to the leading number («3/9 этаж» → floor 3 of 9)
                if p - 2 >= 0 and toks[p - 1].is_slash and toks[p - 2].is_number_like and self._free(toks[p - 2]) \
                        and toks[p - 1].start == toks[p - 2].end and toks[p].start == toks[p - 1].end:
                    return p - 2
                return p
            return None

        def cand_next(i: int) -> int | None:
            q = self._next_content(toks, i)
            if q is not None and toks[q].is_number_like and self._free(toks[q]):
                # number glued to a FOLLOWING keyword («9этаж») is not ours
                if q + 1 < len(toks) and toks[q + 1].role in ("house_kw", "apartment_kw", "entrance_kw", "floor_kw") \
                        and not toks[q + 1].space_before and not toks[q + 1].meta.get("bound"):
                    return None
                return q
            return None

        pending = set(kw_idx)
        decided: list[tuple[int, int, str]] = []   # (kw, num, 'prev'|'next')

        def bind(kw: int, num: int, direction: str):
            fieldname = field_of(toks[kw])
            idxs = [num]
            tight = num + 2 < len(toks) and toks[num + 1].start == toks[num].end and toks[num + 2].start == toks[num + 1].end
            if num + 2 < len(toks) and toks[num + 1].is_slash and toks[num + 2].is_number_like and self._free(toks[num + 2]) \
                    and (fieldname == "HOUSE" or tight):          # «кв 3 / 2 этаж»: a spaced slash separates fields
                if fieldname == "HOUSE":
                    idxs = [num, num + 1, num + 2]             # «55/6» stays a fraction
                else:
                    toks[num + 1].label = toks[num + 2].label = "O"   # «3/9 этаж» → 3 (of 9 floors)
                    rp.flags.append("floor_of_total")
            corrected = False
            if fieldname in rp.spans:
                old = rp.spans[fieldname]
                new_start, new_end = min(toks[kw].start, toks[num].start), max(toks[kw].end, toks[num].end)
                lo, hi = (old.end, new_start) if old.end <= new_start else (new_end, old.start)
                marker = any(x.kind == "WORD" and norm_token(x.text) in CORRECTION_MARKERS for x in toks if lo <= x.start < hi)
                if marker and old.start > new_start:                 # the bound pair IS the correction: keep it, drop this one
                    old.rule = "corrected"
                    rp.flags.append("correction_applied")
                    toks[kw].label = "O"
                    pending.discard(kw)
                    return
                if marker:
                    for x in toks:                                   # «кв 11 — исправляю: кв 12»: the correction wins
                        if x.start >= old.start and x.end <= old.end:
                            x.label = None
                    del rp.spans[fieldname]
                    rp.flags.append("correction_applied")
                    corrected = True
                else:
                    rp.flags.append(f"duplicate_{fieldname.lower()}")
                    toks[kw].label = "O"
                    pending.discard(kw)          # without this the fixpoint loop below never terminates
                    return
            self._set(rp, fieldname, toks, idxs, f"kw_{direction}", 0.95 if direction == "glued" else 0.9)
            if corrected:
                rp.spans[fieldname].rule = "corrected"
            toks[kw].label = "O"
            toks[kw].meta["bound"] = fieldname
            if toks[kw].role == "ambiguous_p":
                rp.flags.append("ambiguous_p_as_" + fieldname.lower())
            decided.append((kw, num, direction))
            pending.discard(kw)

        # pass 2a: glued «4подьезд», «11дом», «кв.70»/«кв70»
        for kw in sorted(pending):
            t = toks[kw]
            p = cand_prev(kw)
            q = cand_next(kw)
            glued_prev = p is not None and not t.space_before and toks[kw - 1] is toks[p]
            glued_next = q is not None and toks[q].start == t.end
            if glued_prev and glued_next and p >= 1 and toks[p - 1].kind == "WORD" and not toks[p].space_before \
                    and toks[p - 1].role is None:
                glued_prev = False                                 # «Жандосова58кв12»: 58 belongs to the street
            if glued_prev and glued_next and p >= 1 and not toks[p].space_before \
                    and toks[p - 1].role in ("house_kw", "apartment_kw", "entrance_kw", "floor_kw"):
                glued_prev = False                                 # «д11кв47»: keyword-first style, 11 is the house
            if glued_prev:
                bind(kw, p, "glued")
                continue
            if glued_next:                                         # «кв70»
                bind(kw, q, "glued")
        # pass 2b: single candidate, iterate to a fixpoint
        changed = True
        while changed:
            changed = False
            for kw in sorted(pending):
                p, q = cand_prev(kw), cand_next(kw)
                if (p is None) != (q is None):
                    bind(kw, p if p is not None else q, "prev" if p is not None else "next")
                    changed = True
        if phase == 1:
            return
        # pass 2c: ambiguous → dominant style of the string, tie → next (keyword-first is the written norm)
        if pending:
            before = sum(1 for _, _, d in decided if d in ("prev", "glued"))
            after = sum(1 for _, _, d in decided if d == "next")
            prefer_prev = before > after
            for kw in sorted(pending):
                p, q = cand_prev(kw), cand_next(kw)
                if p is None and q is None:
                    if toks[kw].role != "ambiguous_p":       # «п. Ботакара» is a settlement: leave it to the city pass
                        toks[kw].label = "O"
                        rp.flags.append("keyword_without_number")
                    continue
                if prefer_prev:
                    bind(kw, p, "prev")
                else:
                    bind(kw, q, "next")
                rp.flags.append("ambiguous_number_binding")
    def _building_part(self, rp: RuleParse):
        """«корпус 2», «блок А» after the house → remembered for canon (Q7: house «12/2»)."""
        toks = rp.tokens
        if "HOUSE" not in rp.spans:
            return
        for i, t in enumerate(toks):
            if t.role == "building_kw" and self._free(t) and t.start >= rp.spans["HOUSE"].end:
                q = self._next_content(toks, i)
                if q is not None and self._free(toks[q]) and (toks[q].is_number_like or (toks[q].kind == "WORD" and len(toks[q].text) == 1)):
                    t.label = toks[q].label = "O"
                    rp.meta["house_building"] = toks[q].text
                    rp.meta["house_building_span"] = (toks[q].start, toks[q].end)
                    rp.flags.append("house_has_building_part")
                    return
                # Kazakh order «42 үй 2 корпус»: the number right before the keyword, right after the house
                b = self._prev_content(toks, i)
                comma = b is not None and any(x.kind == "PUNCT" and x.text in (",", ";") for x in toks
                                              if rp.spans["HOUSE"].end <= x.start < toks[b].start)
                if b is not None and toks[b].is_number_like and toks[b].start >= rp.spans["HOUSE"].end \
                        and (self._free(toks[b]) or (not comma and toks[b].label in ("ENTRANCE", "APARTMENT", "FLOOR"))):
                    # «42 үй 2 корпус» (no comma: even a number the binder took) · «25, 3 блок» (comma: a free number only)
                    for f in ("ENTRANCE", "APARTMENT", "FLOOR"):
                        sp = rp.spans.get(f)
                        if sp is not None and sp.start == toks[b].start:
                            del rp.spans[f]
                    t.label = toks[b].label = "O"
                    rp.meta["house_building"] = toks[b].text
                    rp.meta["house_building_span"] = (toks[b].start, toks[b].end)
                    rp.flags.append("house_has_building_part")
                    return

    # ------------------------------------------------------------- pass 3
    def _street_with_type(self, rp: RuleParse):
        toks = rp.tokens
        n = len(toks)
        found: list[dict] = []
        for i, t in enumerate(toks):
            if t.role != "street_type" or not self._free(t) or t.meta.get("bigram_tail"):
                continue
            lemma = t.meta.get("lemma")
            micro = lemma in self.lex.microdistrict_like
            kw_idxs = [i]
            if t.meta.get("bigram_head") and i + 1 < n and toks[i + 1].role == "street_type":
                kw_idxs.append(i + 1)
                toks[i + 1].meta["bigram_tail"] = True
            last_kw = kw_idxs[-1]

            name: list[int] = []
            house: list[int] = []
            case = None
            j = self._next_content(toks, last_kw)
            # Case A: type BEFORE name  («ул. Курильская 9», «Мкр Аксай 3а, 59», «ул. Микрорайон 17»)
            name_is_type = (j is not None and toks[j].role == "street_type" and lemma == "улица" and not micro
                            and (nn := self._next_content(toks, j)) is not None and toks[nn].is_number_like)
            if j is not None and toks[j].kind == "WORD" and self._free(toks[j]) and (not self._is_kw(toks[j]) or name_is_type):
                case = "A"
                if name_is_type:
                    toks[j].role = None
                k = j
                while k < n and toks[k].kind == "WORD" and self._free(toks[k]) and (not self._is_kw(toks[k]) or (
                        name and self._latin_name_word(toks[k]) and not (k + 1 < n and toks[k + 1].is_number_like))):
                    if name and k + 1 < n and toks[k + 1].role == "district_kw" and norm_token(toks[k].text).endswith(ADJ_SUFFIXES):
                        break                # «мкр Жулдыз-1 Турксибский район»: the adjective belongs to the district
                    name.append(k)
                    k += 1
                    # allow a dot inside names like «им. Абая» — skip single dots
                    while k < n and toks[k].kind == "PUNCT" and toks[k].text == ".":
                        k += 1
                # trailing number
                k = self._next_content(toks, name[-1]) if name else None
                if k is not None and toks[k].is_number_like and self._free(toks[k]):
                    house_later = "HOUSE" in rp.spans and rp.spans["HOUSE"].start > toks[k].end
                    if (micro and self._number_belongs_to_name(rp, k)) or (house_later and toks[k].kind == "NUM"):
                        name.append(k)
                        k2 = self._next_content(toks, k)
                        if k2 is not None and toks[k2].is_number_like and self._free(toks[k2]) and toks[k2 - 1].is_sep:
                            house = self._house_idxs(toks, k2)
                    else:
                        house = self._house_idxs(toks, k)
            elif j is not None and toks[j].kind == "NUM" and self._free(toks[j]) and not micro and (
                    (p0 := self._prev_content(toks, kw_idxs[0])) is None or toks[p0].is_sep
                    or not (toks[p0].kind == "WORD" and self._free(toks[p0]) and not self._is_kw(toks[p0]))) and (
                    (nxt := self._next_content(toks, j)) is not None and (
                        (toks[nxt].kind == "WORD" and self._free(toks[nxt]) and not self._is_kw(toks[nxt]))
                        or (toks[nxt].is_sep and (n2 := self._next_content(toks, nxt)) is not None
                            and (toks[n2].role == "house_kw" or (lemma != "улица" and toks[n2].is_number_like))))):
                # «пер. 2-ой, 13б» / «проезд 6-ой, 8» are numbered lanes; «улица 59, 164пәт» is house 59
                case = "A"
                name = [j]
                if toks[nxt].kind == "WORD":
                    k = nxt
                    while k < n and toks[k].kind == "WORD" and self._free(toks[k]) and not self._is_kw(toks[k]):
                        name.append(k)
                        k += 1
                    k = self._next_content(toks, name[-1])
                    if k is not None and toks[k].is_number_like and self._free(toks[k]):
                        house = self._house_idxs(toks, k)
            else:
                p = self._prev_content(toks, kw_idxs[0])
                # Case B: name BEFORE type («Рыбинский переулок», «Нурсити 2 микрорайон», «66квартал»)
                numbered = micro or lemma in NUMBERED_TYPES or norm_token(t.text) in NUMBERED_TOKENS
                pp = self._prev_content(toks, p) if p is not None else None
                after_living = pp is not None and toks[pp].role == "living_kw" and lemma in NUMBERED_TYPES
                if p is not None and self._free(toks[p]) and (not self._is_kw(toks[p]) or self._latin_name_word(toks[p])) \
                        and not toks[p].is_sep and not after_living \
                        and (toks[p].kind == "WORD" or (numbered and toks[p].is_number_like)):
                    case = "B"
                    gz_name = False
                    k = p
                    if numbered and toks[k].is_number_like:
                        name.append(k)
                        k = self._prev_content(toks, k) if micro else None     # «Геолог 3линия»: no walk-back for линия/аллея
                    while k is not None and k >= 0 and toks[k].kind == "WORD" and self._free(toks[k]) \
                            and (not self._is_kw(toks[k]) or (name and self._latin_name_word(toks[k]))):
                        if name and (k == 0 or toks[k - 1].is_sep) and (match_view(toks[k].text) in self.lex.city_by_name
                                                                     or self._fuzzy_city(toks[k].text)):   # «Карагнда»
                            if not (len(name) == 1 and self._living_anywhere(f"{toks[k].text} {toks[name[0]].text}")):
                                break        # «Алматы Жетысу 2 мкр»: the city is not part of the name («Аксай 4 мкр» is)
                        name.append(k)
                        k = self._prev_content(toks, k)
                        if k is not None and toks[k].is_sep:
                            break
                    name.sort()
                    words = [x for x in name if toks[x].kind == "WORD"]
                    if micro and words and len(words) < len(name) and toks[name[-1]].is_number_like:
                        stem = " ".join(toks[x].text for x in words)
                        whole = " ".join(toks[x].text for x in name)
                        if self._living_anywhere(whole):
                            gz_name = True   # «Аксай 4 мкр»: a known microdistrict, not the town Аксай + «4»
                        elif self._living_anywhere(stem) and match_view(stem) not in self.lex.city_by_name \
                                and not self._known_settlement(stem):      # «Пришахтинск 22 мкр» yes; «Солнечный 15мкр» is a town
                            # «Караганда Пришахтинск 22 мкр»: a known living area + a numbered микрорайон inside it
                            self._set(rp, "LIVING_AREA", toks, words, "micro_living_prefix", 0.8)
                            name = [x for x in name if x not in words]
                    q = self._next_content(toks, last_kw)
                    if q is not None and toks[q].is_number_like and self._free(toks[q]):
                        house = self._house_idxs(toks, q)
                # Case C: type + number («мкр 11, дом 5», «квартал 408», «линия 4»)
                elif j is not None and toks[j].is_number_like and self._free(toks[j]) and (micro or lemma in NUMBERED_TYPES):
                    case = "C"
                    name.append(j)
                    k2 = self._next_content(toks, j)
                    if k2 is not None and toks[k2].is_number_like and self._free(toks[k2]) and toks[k2 - 1].is_sep:
                        house = self._house_idxs(toks, k2)
            if not name:
                continue
            if not micro and toks[name[-1]].kind == "NUM":          # «пер. 2-ой», «проезд 6 -ой»: the ordinal stays in the name
                q = name[-1] + 1
                if q + 1 < n and toks[q].kind == "PUNCT" and toks[q].text == "-" and toks[q + 1].role == "ordinal":
                    name += [q, q + 1]
                elif q < n and toks[q].role == "ordinal" and not toks[q].space_before:
                    name.append(q)
            if case == "B" and gz_name:
                case = "B_gazetteer"
            found.append({"kw": kw_idxs, "name": name, "house": house, "lemma": lemma, "micro": micro, "case": case})

        if not found:
            return
        # «микрорайон» + a separate street-like chunk → the microdistrict is the living area
        street_like = [f for f in found if not f["micro"]]
        micro_like = [f for f in found if f["micro"]]
        if street_like and micro_like:
            primary = street_like[0]
            for f in micro_like:
                self._set(rp, "LIVING_AREA", toks, f["name"], "micro_as_living", 0.8)
                for k in f["kw"]:
                    toks[k].label = "O"
            rp.flags.append("microdistrict_as_living_area")
        else:
            primary = found[0]
            if len(found) > 1:
                rp.flags.append("multiple_street_types")
        for k in primary["kw"]:
            toks[k].label = "STREET_TYPE"
        rp.spans["STREET_TYPE"] = Span("STREET_TYPE", toks[primary["kw"][0]].start, toks[primary["kw"][-1]].end, "type_kw", 0.95)
        rp.meta["street_type_lemma"] = primary["lemma"]
        self._set(rp, "STREET_NAME", toks, primary["name"], f"type_case_{primary['case']}",
                  0.99 if primary["case"] == "B_gazetteer" else 0.9)
        if primary["house"] and "HOUSE" not in rp.spans:
            self._set(rp, "HOUSE", toks, primary["house"], "after_street", 0.85)
        # remaining found chunks (e.g. two streets) stay unlabeled → leftovers lower confidence

    def _number_belongs_to_name(self, rp: RuleParse, k: int) -> bool:
        """For microdistrict-like types: is the number at k part of the NAME («Аксай 3а, 59»)?"""
        toks = rp.tokens
        if "HOUSE" in rp.spans:                       # house already bound via «дом»
            return True
        k2 = self._next_content(toks, k)
        if k2 is not None and toks[k2].is_sep:
            k3 = self._next_content(toks, k2)
            if k3 is not None and toks[k3].is_number_like and self._free(toks[k3]):
                return True                           # second number follows after a comma
        if k2 is not None and toks[k2].is_number_like and self._free(toks[k2]):
            return True                               # «Аксай 1а 12»: two numbers in a row, the first is the name's
        if k2 is not None and toks[k2].role == "house_kw":
            k3 = self._next_content(toks, k2)
            if k3 is not None and toks[k3].is_number_like and self._free(toks[k3]):
                return True                           # «Нурсат 2 дом 14»
        # «Аксай 3а, 59» — a letter suffix on the number rarely denotes a house in this position,
        # unless nothing else in the string can be the house («Алмагуль 9а, кв 47» → house 9а)
        return toks[k].kind == "ALNUM" and k2 is not None and toks[k2].is_sep and self._free_number_after(toks, k2)

    def _free_number_after(self, toks: list[Token], i: int) -> bool:
        """Is there a number after i that is not bound (or about to be bound) to a detail keyword?"""
        detail = {"apartment_kw", "entrance_kw", "floor_kw", "ambiguous_p"}
        for j in range(i + 1, len(toks)):
            t = toks[j]
            if not t.is_number_like or not self._free(t):
                continue
            prev = toks[j - 1] if j > 0 else None
            nxt = toks[j + 1] if j + 1 < len(toks) else None
            if (prev is not None and prev.role in detail) or (nxt is not None and nxt.role in detail):
                continue
            return True
        return False

    def _house_idxs(self, toks: list[Token], k: int) -> list[int]:
        idxs = [k]
        if k + 2 < len(toks) and toks[k + 1].is_slash and toks[k + 2].is_number_like and self._free(toks[k + 2]):
            idxs = [k, k + 1, k + 2]
        return idxs

    # ------------------------------------------------------------- pass 4
    def _district(self, rp: RuleParse):
        toks = rp.tokens
        for i, t in enumerate(toks):
            if t.role != "district_kw" or not self._free(t):
                continue
            p = self._prev_content(toks, i)
            q = self._next_content(toks, i)
            prev_ok = p is not None and toks[p].kind == "WORD" and self._free(toks[p]) and not self._is_kw(toks[p])
            next_ok = q is not None and toks[q].kind == "WORD" and self._free(toks[q]) and not self._is_kw(toks[q])
            prev_known = prev_ok and match_view(toks[p].text) in self.lex.district_names
            next_known = next_ok and match_view(toks[q].text) in self.lex.district_names
            prev_adj = prev_ok and norm_token(toks[p].text).endswith(ADJ_SUFFIXES)
            idxs: list[int] = []
            if prev_known or (prev_adj and not next_known):
                idxs = [p]
                # two-word districts before the keyword: «имени Казыбек би район» is rare; keep one word
            elif next_known or (next_ok and not prev_ok):
                idxs = [q]
                q2 = self._next_content(toks, q)       # «р-н Казыбек би», «район Байконыр»: a two-word name
                if q2 is not None and q2 == q + 1 and toks[q2].kind == "WORD" and self._free(toks[q2]) \
                        and not self._is_kw(toks[q2]) and (norm_token(toks[q2].text) in NAME_PARTICLES
                        or match_view(f"{toks[q].text} {toks[q2].text}") in self.lex.district_names):
                    idxs.append(q2)
            elif prev_ok:
                idxs = [p]
            if idxs:
                self._set(rp, "DISTRICT", toks, idxs, "district_kw", 0.9)
                t.label = "O"
                key = match_view(toks[idxs[0]].text)
                if key in self.lex.city_of_district:
                    rp.meta["city_of_district"] = self.lex.city_of_district[key]
                    rp.meta["district_known"] = self.lex.district_names[key]
            else:
                t.label = "O"
                rp.flags.append("district_kw_without_name")

    def _bare_district(self, rp: RuleParse):
        """A whole comma-separated chunk equal to a district of the parsed city («Астана, Сарыарка, ул. Сейфуллина 31»)."""
        if "DISTRICT" in rp.spans or "CITY" not in rp.spans:
            return
        city = rp.meta.get("city_known")
        names = {match_view(n) for n in self.lex.city_districts.get(city, []) or []}
        names |= {match_view(k) for k in (self.lex.city_districts_kk.get(city) or {})}
        if not names:
            return
        toks = rp.tokens
        n = len(toks)
        i = 0
        while i < n:
            if toks[i].kind == "WORD" and (i == 0 or (self._prev_content(toks, i) is None or toks[self._prev_content(toks, i)].is_sep)):
                k = i
                while k < n and toks[k].kind == "WORD" and self._free(toks[k]) and not self._is_kw(toks[k]):
                    k += 1
                nxt = self._next_content(toks, k - 1) if k > i else None
                text = " ".join(t.text for t in toks[i:k])
                if k > i and (nxt is None or toks[nxt].is_sep) and match_view(text) in names \
                        and match_view(text) != match_view(rp.meta.get("city_text", "")):
                    self._set(rp, "DISTRICT", toks, list(range(i, k)), "district_bare", 0.8)
                    return
            i += 1

    # ------------------------------------------------------------- pass 5
    def _city(self, rp: RuleParse):
        toks = rp.tokens
        n = len(toks)
        # 5a keyword + name: «город Алматы», «г. Талдыкорган», «пос. Боралдай», «п. Боралдай»
        for i, t in enumerate(toks):
            if t.role not in ("city_kw", "ambiguous_p") or not self._free(t):
                continue
            j = self._next_content(toks, i)
            if j is None or toks[j].kind != "WORD" or not self._free(toks[j]) or self._is_kw(toks[j]):
                continue
            if len(norm_token(t.text)) == 1 and not (t.end < len(rp.raw) and rp.raw[t.end] == ".") \
                    and not toks[j].text[0].isupper() and match_view(toks[j].text) not in self.lex.city_by_name \
                    and not self._known_settlement(toks[j].text):
                continue                     # «рядом с мечетью»: «с» is a preposition here, not «село»
            idxs = []
            k = j
            while k < n and toks[k].kind == "WORD" and self._free(toks[k]) and not self._is_kw(toks[k]):
                idxs.append(k)
                k += 1
            t.label = "O"
            if "CITY" in rp.spans:
                for k in idxs:
                    toks[k].label = "O"
                rp.flags.append("city_duplicate")
                continue
            self._set(rp, "CITY", toks, idxs, "city_kw", 0.9)
            self._note_city(rp, " ".join(toks[k].text for k in idxs))
            rp.meta["city_kind"] = "city" if norm_token(t.text) in CITY_WORDS_URBAN else "village"
        # 5b Kazakh suffix: «Ақтөбе қаласы», «Боралдай ауылы»
        for i, t in enumerate(toks):
            if t.role != "city_suffix_kk" or not self._free(t):
                continue
            p = self._prev_content(toks, i)
            if p is None or toks[p].kind != "WORD" or self._is_kw(toks[p]):
                t.label = "O"
                continue
            idxs = [p]
            k = self._prev_content(toks, p)
            while k is not None and toks[k].kind == "WORD" and self._free(toks[k]) and not self._is_kw(toks[k]) \
                    and match_view(" ".join([toks[k].text] + [toks[x].text for x in idxs])) in self.lex.city_by_name:
                idxs.insert(0, k)
                k = self._prev_content(toks, k)
            t.label = "O"
            if not self._free(toks[p]):
                continue
            if "CITY" in rp.spans:
                for k in idxs:
                    toks[k].label = "O"
                rp.flags.append("city_kk_duplicate")
                continue
            self._set(rp, "CITY", toks, idxs, "city_kk_suffix", 0.8)
            rp.flags.append("city_kk_only")
            rp.meta["city_kind"] = "city" if norm_token(t.text) in CITY_WORDS_URBAN else "village"
            self._note_city(rp, " ".join(toks[k].text for k in idxs), kk=True)
        # 5c bare seed match (prefer the first free WORD chunk that is a known city)
        if "CITY" not in rp.spans:
            for i, t in enumerate(toks):
                if t.kind != "WORD" or not self._free(t) or self._is_kw(t):
                    continue
                # single or hyphenated token first, then two-word («Отеген батыр»)
                keys = [(match_view(t.text), [i])]
                if i + 1 < n and toks[i + 1].kind == "WORD" and self._free(toks[i + 1]):
                    keys.insert(0, (match_view(t.text + " " + toks[i + 1].text), [i, i + 1]))
                for key, idxs in keys:
                    if key in self.lex.city_by_name:
                        # a bare word right before a number with no street yet is more likely a street
                        q = self._next_content(toks, idxs[-1])
                        if q is not None and toks[q].is_number_like and self._free(toks[q]) and "STREET_NAME" not in rp.spans:
                            continue
                        self._set(rp, "CITY", toks, idxs, "city_seed", 0.8)
                        self._note_city(rp, key)
                        break
                if "CITY" in rp.spans:
                    break
        # 5c'' a free word chunk standing alone between separators that OSM knows as a settlement
        #      («Карасайский район, Шамалган, ул. Ленина 2») — the 7.8k OSM villages, not only the seed's towns
        if "CITY" not in rp.spans:
            i = 0
            while i < n and "CITY" not in rp.spans:
                if toks[i].kind == "WORD" and self._free(toks[i]) and not self._is_kw(toks[i]) and toks[i].text[:1].isupper() \
                        and toks[i].role != "noise" and (i == 0 or toks[i - 1].is_sep):    # villages named «Казахстан» exist
                    j = i
                    while j + 1 < n and toks[j + 1].kind == "WORD" and self._free(toks[j + 1]) and not self._is_kw(toks[j + 1]):
                        j += 1
                    rest = [t for k, t in enumerate(toks) if not (i <= k <= j) and t.kind != "PUNCT"
                            and t.role not in ("phone", "noise", "code_kw") and t.label != "O"]
                    if (j + 1 >= n or toks[j + 1].is_sep) and j - i < 3 and rest:   # a lone «Абая» is a street
                        text = " ".join(toks[k].text for k in range(i, j + 1))
                        if self._known_settlement(text):
                            self._set(rp, "CITY", toks, list(range(i, j + 1)), "city_osm_chunk", 0.7)
                            self._note_city(rp, text)
                            rp.flags.append("city_from_osm_chunk")
                            break
                    i = j + 1
                    continue
                i += 1
        # 5c' first token, followed by a separator, one edit away from a city/town → city as written («Алмты,»)
        if "CITY" not in rp.spans:
            first = next((i for i, t in enumerate(toks) if t.kind != "PUNCT"), None)
            if first is not None and toks[first].kind == "WORD" and self._free(toks[first]) and not self._is_kw(toks[first]) \
                    and first + 1 < n and toks[first + 1].is_sep and toks[first].text[0].isupper():
                fz = self._fuzzy_city(toks[first].text)
                if fz:
                    self._set(rp, "CITY", toks, [first], "city_fuzzy", 0.6)
                    self._note_city(rp, fz["name"])
                    rp.meta["city_text"] = toks[first].text        # output stays as written
                    rp.flags.append("city_fuzzy_match")
        # 5d duplicates of the found city elsewhere in the string → O
        if "CITY" in rp.spans:
            city_key = match_view(rp.meta.get("city_text", ""))
            for t in toks:
                if t.kind == "WORD" and self._free(t) and match_view(t.text) == city_key:
                    t.label = "O"
                    rp.flags.append("city_duplicate")
        # 5e province from city (cities of republican significance / seed)
        if "CITY" in rp.spans and "province_canonical" not in rp.meta:
            key = match_view(rp.meta.get("city_text", ""))
            if key in self.lex.province_of_city_name:
                rp.meta["province_from_city"] = self.lex.province_of_city_name[key]
            elif key in self.lex.city_by_name:
                rp.meta["province_from_city"] = self.lex.city_by_name[key]["province"]

    def _note_city(self, rp: RuleParse, text: str, kk: bool = False):
        rp.meta["city_text"] = text
        rec = self.lex.city_by_name.get(match_view(text))
        if rec:
            rp.meta["city_known"] = rec["name"]
            if rec.get("district") and "DISTRICT" not in rp.spans:
                rp.meta["district_from_city"] = rec["district"]
            # Kazakh spelling («Ақтөбе», «Қарағанды қаласы») → Russian name (Q2). Latin / old names stay as written.
            written_kk = match_view(text) == match_view(rec.get("kk", "")) and rec.get("kk") != rec["name"]
            cyrillic = bool(re.search(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]", text))     # «Almaty qalasy» stays «Almaty»
            if ((kk and cyrillic) or written_kk) and rec["name"] != text:
                rp.meta["city_ru_from_kk"] = rec["name"]

    # ------------------------------------------------------------- pass 6
    def _living_area(self, rp: RuleParse):
        toks = rp.tokens
        n = len(toks)
        for i, t in enumerate(toks):
            if t.role != "living_kw" or not self._free(t):
                continue
            kw_start = t.start
            # multi-word keywords («жилой комплекс», «тұрғын үй кешені»): skip all keyword words
            j = self._next_content(toks, i)
            while j is not None and toks[j].role == "living_kw":
                toks[j].label = "O"
                j = self._next_content(toks, j)
            idxs: list[int] = []
            if j is not None and toks[j].kind == "WORD" and self._free(toks[j]) and not self._is_kw(toks[j]):
                # Case A: «ЖК Томирис»
                k = j
                while k < n and self._free(toks[k]) and not self._is_kw(toks[k]) and toks[k].kind == "WORD":
                    idxs.append(k)
                    k += 1
            else:
                # Case B (Kazakh order): «Томирис тұрғын үй кешені»
                p = self._prev_content(toks, i)
                while p is not None and toks[p].kind == "WORD" and self._free(toks[p]) and not self._is_kw(toks[p]):
                    idxs.insert(0, p)
                    p = self._prev_content(toks, p)
                    if p is not None and toks[p].is_sep:
                        break
            if not idxs:
                t.label = "O"
                continue
            t.label = "O"
            k2 = self._next_content(toks, i)
            while k2 is not None and toks[k2].role == "living_kw" and self._free(toks[k2]):
                toks[k2].label = "O"
                k2 = self._next_content(toks, k2)
            if "LIVING_AREA" in rp.spans:
                for k in idxs:
                    toks[k].label = "O"
                continue
            self._set(rp, "LIVING_AREA", toks, idxs, "living_kw", 0.85)
            rp.meta["living_area_kw_start"] = kw_start      # for conventions.living_area_keep_type_word

    # ------------------------------------------------------------- pass 7
    def _street_fallback(self, rp: RuleParse):
        """No street type keyword: first free WORD chunk directly followed by a number."""
        if "STREET_NAME" in rp.spans:
            return
        toks = rp.tokens
        n = len(toks)
        i = 0
        while i < n:
            t = toks[i]
            if t.kind == "WORD" and self._free(t) and not self._is_kw(t):
                idxs = [i]
                k = i + 1
                while k < n and toks[k].kind == "WORD" and self._free(toks[k]) and not self._is_kw(toks[k]) \
                        and not (k + 1 < n and toks[k + 1].role == "district_kw" and norm_token(toks[k].text).endswith(ADJ_SUFFIXES)):
                    idxs.append(k)
                    k += 1
                q = self._next_content(toks, idxs[-1])
                if q is not None and toks[q].is_number_like and self._free(toks[q]) and not toks[q - 1].is_sep:
                    q2 = self._next_content(toks, q)
                    second = None
                    if q2 is not None and toks[q2].is_sep:
                        q3 = self._next_content(toks, q2)
                        if q3 is not None and toks[q3].is_number_like and self._free(toks[q3]):
                            second = q3
                    elif q2 is not None and toks[q2].role == "house_kw" or (q2 is not None and "HOUSE" in rp.spans
                                                                             and rp.spans["HOUSE"].start >= toks[q2].start):
                        second = -1
                    if second is not None and self._known_microdistrict(rp, " ".join(toks[k].text for k in idxs)):
                        # «Орбита 3, 27а», «Аксай 3 д 20»: a known микрорайон + number → the number is part of the name
                        self._set(rp, "STREET_NAME", toks, idxs + [q], "fallback_micro_number", 0.65)
                        rp.flags.append("street_without_type")
                        rp.flags.append("microdistrict_number_in_name")
                        if second >= 0 and "HOUSE" not in rp.spans:
                            self._set(rp, "HOUSE", toks, self._house_idxs(toks, second), "after_street", 0.7)
                        return
                    self._set(rp, "STREET_NAME", toks, idxs, "fallback_word_number", 0.7)
                    rp.flags.append("street_without_type")
                    if "HOUSE" not in rp.spans:
                        self._set(rp, "HOUSE", toks, self._house_idxs(toks, q), "after_street", 0.75)
                    return
                q2 = self._next_content(toks, q) if q is not None and toks[q].is_sep else None
                if q2 is not None and toks[q2].is_number_like and self._free(toks[q2]) \
                        and "HOUSE" not in rp.spans and not any(t.label is None and t.kind == "WORD" and t.role is None
                                                                 for t in toks[q2 + 1:]):
                    q = q2
                    # «Алматы, Абая, 10, 5»: name, separator, number — and no later word that could be the street
                    self._set(rp, "STREET_NAME", toks, idxs, "fallback_word_sep_number", 0.6)
                    rp.flags.append("street_without_type")
                    self._set(rp, "HOUSE", toks, self._house_idxs(toks, q), "after_street_sep", 0.6)
                    rp.flags.append("house_without_keyword")
                    return
                i = k
                continue
            i += 1

    def _house_letter(self, rp: RuleParse):
        """«Абая 10 Б, кв 5» → the lone letter after the house belongs to it (canon drops the space → «10Б»)."""
        toks = rp.tokens
        sp = rp.spans.get("HOUSE")
        if sp is None:
            return
        k = next((i for i, t in enumerate(toks) if t.end == sp.end), None)
        if k is None or toks[k].kind != "NUM" or k + 1 >= len(toks):
            return
        n = toks[k + 1]
        if n.kind == "PUNCT" and n.text == "-" and k + 2 < len(toks) and toks[k + 2].kind == "WORD" \
                and len(toks[k + 2].text) == 1 and toks[k + 2].label is None and toks[k + 2].text.isalpha() \
                and (k + 3 >= len(toks) or not toks[k + 3].is_number_like):
            n.label = toks[k + 2].label = "HOUSE"            # «д. 21-а» → house «21а» (canon drops the dash)
            sp.end = toks[k + 2].end
            rp.flags.append("house_letter_dashed")
            return
        if n.kind == "WORD" and len(n.text) == 1 and n.label is None and n.text.isalpha() \
                and norm_token(n.text) not in ("к", "п", "д", "э") \
                and (k + 2 >= len(toks) or toks[k + 2].is_sep or toks[k + 2].kind == "PUNCT" or toks[k + 2].role in
                     ("apartment_kw", "entrance_kw", "floor_kw", "building_kw")):
            n.label = "HOUSE"
            n.role = None
            sp.end = n.end
            rp.flags.append("house_letter_spaced")

    # ------------------------------------------------------------- pass 8
    def _house_fallback(self, rp: RuleParse):
        if "HOUSE" in rp.spans or "STREET_NAME" not in rp.spans:
            return
        toks = rp.tokens
        s_end = rp.spans["STREET_NAME"].end
        cands = [i for i, t in enumerate(toks) if t.is_number_like and self._free(t) and t.start >= s_end]
        if cands:
            self._set(rp, "HOUSE", toks, self._house_idxs(toks, cands[0]), "house_fallback", 0.7)
            rp.flags.append("house_without_keyword")
            if len(cands) > 1:
                rp.flags.append("house_ambiguous")

    # ------------------------------------------------------------- pass 9
    def _landmark(self, rp: RuleParse):
        toks = rp.tokens
        n = len(toks)
        for i, t in enumerate(toks):
            if t.role != "landmark_kw" or not self._free(t):
                continue
            idxs = [] if t.meta.get("marker_only") else [i]
            k = i + 1
            if t.meta.get("marker_only"):
                while k < n and norm_token(toks[k].text) in ("с", "со", "от"):    # «рядом с мечетью», «напротив от …»
                    toks[k].label = "O"
                    k += 1
                if k < n and norm_token(toks[k].text) in TIME_WORDS:
                    continue                                                       # «звонить за час»
            while k < n and self._free(toks[k]) and not toks[k].is_sep:
                tk = toks[k]
                if tk.kind == "WORD" and not self._is_kw(tk):
                    idxs.append(k)
                elif tk.kind == "PUNCT" and tk.text in ("№", "-", "."):
                    pass
                elif tk.is_number_like and k > 0 and toks[k - 1].kind == "PUNCT" and toks[k - 1].text == "№":
                    idxs.append(k)
                elif tk.kind == "WORD" and tk.role == "landmark_kw":
                    idxs.append(k)
                else:
                    break
                k += 1
            if t.meta.get("marker_only"):
                t.label = "O"
            if not idxs:
                continue
            if "LANDMARK" in rp.spans:
                for k in idxs:
                    toks[k].label = "O"
                continue
            self._set(rp, "LANDMARK", toks, idxs, "landmark_kw", 0.8)

    # ------------------------------------------------------------- leftovers
    def _leftovers(self, rp: RuleParse):
        """Flag unclaimed tokens and record free WORD chunks (gazetteer may turn one into living_area)."""
        toks = rp.tokens
        chunks: list[dict] = []
        cur: list[int] = []
        for i, t in enumerate(toks):
            free_word = t.label is None and t.kind == "WORD" and t.role is None
            if free_word:
                cur.append(i)
                rp.flags.append("leftover_word")
            else:
                if cur:
                    chunks.append({"start": toks[cur[0]].start, "end": toks[cur[-1]].end,
                                   "text": rp.raw[toks[cur[0]].start:toks[cur[-1]].end], "idxs": list(cur)})
                    cur = []
                if t.label is None and t.kind == "WORD" and t.role == "noise":
                    t.label = "O"
                elif t.label is None and t.is_number_like:
                    rp.flags.append("leftover_number")
        if cur:
            chunks.append({"start": toks[cur[0]].start, "end": toks[cur[-1]].end,
                           "text": rp.raw[toks[cur[0]].start:toks[cur[-1]].end], "idxs": list(cur)})
        rp.meta["leftover_chunks"] = chunks
        if "STREET_NAME" not in rp.spans:
            rp.flags.append("no_street")
        if "HOUSE" not in rp.spans:
            rp.flags.append("no_house")
