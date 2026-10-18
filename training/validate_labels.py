"""Validators for labels (LLM, human or rules). Pure functions, no I/O.

A label set is a dict with the 11 field keys. `validate_record` returns a list of
issues; `resolve_spans` maps each value back to character offsets in the raw string
(needed to train the token tagger). Severity: 'error' blocks auto-acceptance,
'warning' only lowers priority in the review queue.

Checks (from docs/CONVENTIONS.md):
  * every string-derived value must occur in the raw string (case-insensitive,
    whitespace/punctuation tolerant) — the model must COPY, not rewrite;
  * province must be one of the canonical names;
  * house / apartment / entrance / floor match their patterns;
  * city has no «город/г./қаласы», district has no «район», street_name has no type word;
  * living_area and street_type=микрорайон are not used for the SAME chunk;
  * spans of different fields do not overlap.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.preprocess import match_view  # noqa: E402

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
STRING_FIELDS = ["city", "district", "living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark"]

_PROV = yaml.safe_load((ROOT / "api/jana_address/rules/province.yaml").read_text(encoding="utf-8"))["provinces"]
CANONICAL_PROVINCES = {p["canonical"] for p in _PROV}
_LEX = yaml.safe_load((ROOT / "api/jana_address/rules/lexicon.yaml").read_text(encoding="utf-8"))
STREET_TYPE_LEMMAS = set(_LEX["street_types"].keys())
TYPE_WORDS = {v for vs in _LEX["street_types"].values() for v in vs} | STREET_TYPE_LEMMAS
CITY_WORDS = set(_LEX["city_kw"]) | set(_LEX["city_suffix_kk"])
DISTRICT_WORDS = set(_LEX["district_kw"])

HOUSE_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?(/\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?)?$")
NUM_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?$")
_WS_PUNCT = re.compile(r"[\s\.,;:()\"'«»]+")


def _loose_regex(value: str) -> re.Pattern:
    """Regex that finds `value` in raw tolerating case, spaces and dots between characters."""
    parts = [re.escape(ch) for ch in value if not ch.isspace()]
    return re.compile(r"[\s\.]*".join(parts), re.IGNORECASE)


def find_all(raw: str, value: str) -> list[tuple[int, int]]:
    if not value:
        return []
    return [(m.start(), m.end()) for m in _loose_regex(value).finditer(raw)]


def _city_is_kk_mapping(raw: str, value: str) -> bool:
    """«Қарағанды қаласы» labeled as «Караганда»: accepted when the seed gazetteer links them."""
    from jana_address.rules.grammar import get_lexicon  # lazy: heavy-ish import

    lex = get_lexicon()
    target = match_view(value)
    words = re.findall(r"[^\s,;/.]+", raw)
    for n in (1, 2):                      # every 1- and 2-word window («Ақтөбе қаласы» must also try «Ақтөбе»)
        for i in range(len(words) - n + 1):
            rec = lex.city_by_name.get(match_view(" ".join(words[i:i + n])))
            if rec and match_view(rec["name"]) == target:
                return True
    return False


_BUILDING = r"(?:корпус[а-я]*|корп\.?|к\.|блок[а-я]*|block|korpus|korp\.?|строение|стр\.?)"


def _house_is_join(raw: str, value: str) -> bool:
    """«12 корпус 2» / «12 блок А» / «42 үй 2 корпус» labeled «12/2» / «12/А» (CONVENTIONS Q7, house_building_join)."""
    m = re.fullmatch(r"(\d+[^\W\d_]?)/(\w+)", value)
    if not m:
        return False
    a, b = (re.escape(x) for x in m.groups())
    return bool(re.search(rf"(?<!\d){a}\s*(?:үй|дом)?[\s,.]*{_BUILDING}\s*{b}(?!\w)", raw, re.I)
                or re.search(rf"(?<!\d){a}\s*(?:үй)?[\s,.]*{b}\s*{_BUILDING}(?!\w)", raw, re.I))


def validate_record(raw: str, f: dict) -> list[dict]:
    issues: list[dict] = []

    def err(code, msg, field=None):
        issues.append({"severity": "error", "code": code, "field": field, "msg": msg})

    def warn(code, msg, field=None):
        issues.append({"severity": "warning", "code": code, "field": field, "msg": msg})

    for k in FIELDS:
        if k not in f:
            err("missing_key", f"missing key {k}", k)
    for k in STRING_FIELDS:
        v = f.get(k)
        if v is None:
            continue
        if not isinstance(v, str) or not v.strip():
            err("empty_value", "empty / non-string value", k)
            continue
        if not find_all(raw, v):
            if k == "city" and _city_is_kk_mapping(raw, v):
                warn("city_mapped_from_kk", f"city={v!r} is the Russian name of a Kazakh mention", k)
            elif k == "house" and _house_is_join(raw, v):
                warn("house_building_join", f"house={v!r} joins a корпус/блок", k)
            else:
                err("not_in_string", f"{k}={v!r} not found in raw", k)
    p = f.get("province")
    if p is not None and p not in CANONICAL_PROVINCES:
        err("province_not_canonical", f"province={p!r} not in canonical list", "province")
    st = f.get("street_type")
    if st is not None and st not in STREET_TYPE_LEMMAS:
        err("street_type_not_lemma", f"street_type={st!r} not a lemma", "street_type")
    h = f.get("house")
    if h is not None and not HOUSE_RE.match(str(h)):
        warn("house_nonstandard", f"house={h!r} does not match N[a](/N[a])", "house")
    for k in ("apartment", "entrance", "floor"):
        v = f.get(k)
        if v is not None and not NUM_RE.match(str(v)):
            err("not_a_number", f"{k}={v!r} must be a number (+letter)", k)
    c = f.get("city")
    if c is not None:
        words = {match_view(w) for w in _WS_PUNCT.split(c) if w}
        if words & {match_view(w) for w in CITY_WORDS}:
            err("city_has_type_word", f"city={c!r} contains a type word", "city")
    d = f.get("district")
    if d is not None:
        words = {match_view(w) for w in _WS_PUNCT.split(d) if w}
        if words & {match_view(w) for w in DISTRICT_WORDS}:
            err("district_has_type_word", f"district={d!r} contains «район»", "district")
    s = f.get("street_name")
    if s is not None:
        first_raw = _WS_PUNCT.split(s.strip())[0] if s.strip() else ""
        first = match_view(first_raw)
        initial = len(first_raw) == 1 and first_raw.isupper()          # «Ш. Уалиханова» is an initial, not «ш.» = шоссе
        if first in {match_view(w) for w in TYPE_WORDS} and not initial and len(_WS_PUNCT.split(s.strip())) > 1:
            err("street_name_has_type_word", f"street_name={s!r} starts with a type word", "street_name")
        if match_view(s) in {match_view(w) for w in TYPE_WORDS}:
            err("street_name_is_type_word", f"street_name={s!r} is only a type word", "street_name")
    la = f.get("living_area")
    if la is not None and st in ("микрорайон", "квартал", "жилой массив") and s is not None \
            and match_view(la) == match_view(s):
        err("living_area_equals_street", "living_area duplicates the microdistrict street_name", "living_area")
    if la is not None and st in ("микрорайон", "квартал", "жилой массив"):
        warn("living_area_with_micro_type", "living_area together with a microdistrict-type street — check the rule", "living_area")
    if s is None and h is not None:
        warn("house_without_street", "house present but no street_name", "house")
    return issues


def _token_bounds(raw: str) -> tuple[set[int], set[int]]:
    from jana_address.preprocess import clean
    from jana_address.tokenizer import tokenize
    toks = tokenize(clean(raw))
    return {t.start for t in toks}, {t.end for t in toks}


def resolve_spans(raw: str, f: dict) -> tuple[dict[str, tuple[int, int]], list[dict]]:
    """Map string-derived values to (start, end) offsets; prefer non-overlapping, left-to-right.

    Candidates that sit on token boundaries win over ones that cut a token («12» inside «112» or
    «2а» inside «12а» would give the tagger no label at all). Returns (spans, issues). Ambiguity
    (several possible positions) is a warning."""
    spans: dict[str, tuple[int, int]] = {}
    issues: list[dict] = []
    starts, ends = _token_bounds(raw)
    # longer values first so «Аксай 3а» claims its text before «3а»-like numbers do
    order = sorted((k for k in STRING_FIELDS if f.get(k)), key=lambda k: -len(str(f[k])))
    taken: list[tuple[int, int]] = []
    for k in order:
        cands = find_all(raw, str(f[k]))
        free = [c for c in cands if all(c[1] <= a or c[0] >= b for a, b in taken)]
        if not cands:
            continue  # already reported by validate_record
        if not free:
            issues.append({"severity": "error", "code": "span_overlap", "field": k, "msg": f"{k} overlaps another field"})
            continue
        aligned = [c for c in free if c[0] in starts and c[1] in ends]
        pool = aligned or free
        if len(pool) > 1:
            issues.append({"severity": "warning", "code": "ambiguous_span", "field": k,
                           "msg": f"{k}={f[k]!r} occurs {len(pool)}× — took the first free occurrence"})
        if not aligned:
            issues.append({"severity": "warning", "code": "span_not_token_aligned", "field": k,
                           "msg": f"{k}={f[k]!r} does not start/end on a token boundary"})
        spans[k] = pool[0]
        taken.append(pool[0])
    return spans, issues


def to_bio(raw: str, spans: dict[str, tuple[int, int]], tokens: list[tuple[str, int, int]]) -> list[str]:
    """Token-level BIO labels from character spans (tokens = (text, start, end))."""
    labels = []
    for _, s, e in tokens:
        lab = "O"
        for field, (a, b) in spans.items():
            if s >= a and e <= b:
                lab = ("B-" if s == a else "I-") + field.upper()
                break
        labels.append(lab)
    return labels
