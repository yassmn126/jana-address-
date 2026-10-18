"""Which records go to the AI, how its answer is checked, and how it is merged with the local answer.

Modes (query `?ai=` or env JANA_AI_MODE):
  off       never call the AI (default; the jury run is offline by the rules of the brief)
  foreign   only addresses detected as outside Kazakhstan
  fallback  foreign + records whose calibrated local confidence < JANA_AI_MIN_CONF (0.80) — the production setting:
            the local parser stays the answer for the ~95 % it is sure about, the AI looks at the uncertain tail
  always    every record (the local answer is sent as a draft; the AI acts as a second annotator)

Merge rules (per record):
  * AI values are validated first: copy-not-rewrite span check, canonical province, closed street-type list, number
    patterns. An invalid AI value is dropped and the local value is kept for that field.
  * foreign address                      → AI values (the local parser and gazetteer are Kazakhstan-only)
  * local confidence ≥ JANA_AI_KEEP_LOCAL_CONF (0.90) → local values; a disagreement only lowers the confidence
    (measured on 416 gold rows: when Opus 5.5 and our parser disagreed, our parser was right 25 times out of 32)
  * otherwise                            → AI values; gazetteer enrichment (province/district filled from the city)
    is kept where the AI left the field empty
  * confidence: agreement → max(local, 0.95); disagreement → 0.60 (kept local) / 0.70 (took AI) — both land in the
    review queue at the default review threshold.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..preprocess import match_view
from .client import MODES, AIClient, AIConfig, get_client
from .prompt import FIELDS

ENRICHABLE = ("province", "city", "district", "living_area")
SPAN_FIELDS = ("city", "district", "living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark")
_RULES = Path(__file__).resolve().parents[1] / "rules"
_NUM = re.compile(r"^\d+[^\W\d_]?$")
_HOUSE = re.compile(r"^[\dA-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі/\-]+$")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_BUILDING = r"(?:корпус[а-я]*|корп\.?|к\.|блок[а-я]*|block|korpus|korp\.?|строение|стр\.?)"


@lru_cache(maxsize=1)
def _canon() -> tuple[set[str], set[str]]:
    prov = yaml.safe_load((_RULES / "province.yaml").read_text(encoding="utf-8"))["provinces"]
    lex = yaml.safe_load((_RULES / "lexicon.yaml").read_text(encoding="utf-8"))
    return {p["canonical"] for p in prov}, set(lex["street_types"].keys())


def jnorm(v) -> str | None:
    """The jury's comparison: lowercase, punctuation removed, whitespace collapsed."""
    if v is None:
        return None
    t = re.sub(r"\s+", " ", _PUNCT.sub("", str(v).lower())).strip()
    return t or None


def _in_raw(raw: str, value: str) -> bool:
    parts = [re.escape(ch) for ch in value if not ch.isspace()]
    if not parts:
        return False
    return re.search(r"[\s\.]*".join(parts), raw, re.IGNORECASE) is not None or \
        (jnorm(value) or "\x00") in (jnorm(raw) or "")


def _city_kk_to_ru(raw: str, value: str) -> bool:
    from ..rules.grammar import get_lexicon
    lex = get_lexicon()
    target = match_view(value)
    words = re.findall(r"[^\s,;/.]+", raw)
    for n in (1, 2):
        for i in range(len(words) - n + 1):
            rec = lex.city_by_name.get(match_view(" ".join(words[i:i + n])))
            if rec and match_view(rec["name"]) == target:
                return True
    return False


def _house_join(raw: str, value: str) -> bool:
    m = re.fullmatch(r"(\d+[^\W\d_]?)/(\w+)", value)
    if not m:
        return False
    a, b = (re.escape(x) for x in m.groups())
    return bool(re.search(rf"(?<!\d){a}\s*(?:үй|дом)?[\s,.]*{_BUILDING}\s*{b}(?!\w)", raw, re.I)
                or re.search(rf"(?<!\d){a}\s*(?:үй)?[\s,.]*{b}\s*{_BUILDING}(?!\w)", raw, re.I))


def validate(raw: str, rec: dict, local: dict | None = None) -> tuple[dict, list[str]]:
    """→ (fields with invalid values set to None, names of the invalid fields). A value equal to the local parser's
    value is accepted as is (both systems agree, e.g. a whole string typed in the wrong keyboard layout)."""
    provinces, lemmas = _canon()
    foreign = (rec.get("country") or "KZ").upper() != "KZ"
    out, bad = {}, []
    for f in FIELDS:
        v = rec.get(f)
        if isinstance(v, str):
            v = v.strip() or None
        elif v is not None:
            v = None
        if v is None:
            out[f] = None
            continue
        if local is not None and jnorm(local.get(f)) == jnorm(v):
            out[f] = local.get(f) if f in ("province", "street_type") else v
            continue
        ok = True
        if f == "province":
            ok = (v in provinces) if not foreign else _in_raw(raw, v)
        elif f == "street_type":
            ok = v in lemmas
        else:
            ok = _in_raw(raw, v) or (f == "city" and not foreign and _city_kk_to_ru(raw, v)) \
                or (f == "house" and _house_join(raw, v))
            if ok and f in ("apartment", "entrance", "floor"):
                ok = bool(_NUM.match(v))
            if ok and f == "house":
                ok = bool(_HOUSE.match(v))
        out[f] = v if ok else None
        if not ok:
            bad.append(f)
    return out, bad


def route(rows: list[dict], mode: str, cfg: AIConfig) -> list[int]:
    if mode == "always":
        return list(range(len(rows)))
    picked = []
    for i, r in enumerate(rows):
        foreign = (r.get("country") or "KZ") != "KZ"
        if foreign or (mode == "fallback" and (r.get("confidence") or 0) < cfg.min_conf):
            picked.append(i)
    return picked


def _draft(row: dict) -> dict:
    prov = row.get("provenance") or {}
    return {f: row[f] for f in FIELDS if row.get(f) is not None and not str(prov.get(f) or "").startswith("gazetteer")}


def merge(row: dict, ai: dict, cfg: AIConfig) -> dict:
    raw = row.get("raw_address", "")
    prov = row.get("provenance") or {}
    local = {f: row.get(f) for f in FIELDS}
    clean, bad = validate(raw, ai, local)
    country = (ai.get("country") or row.get("country") or "KZ").upper()
    foreign = country != "KZ"
    local_conf = float(row.get("confidence") or 0.0)
    from_string = {f: local[f] if not str(prov.get(f) or "").startswith("gazetteer") else None for f in FIELDS}
    agree = all(jnorm(from_string[f]) == jnorm(clean[f]) for f in FIELDS if f != "street_type") and not bad
    if foreign:
        base, final = "ai", dict(clean)
        for f in bad:                                    # an invalid AI value: keep the local one only if it is ours
            final[f] = from_string[f]
    elif local_conf >= cfg.keep_local_conf:
        base, final = "local", dict(local)
    else:
        base, final = "ai", dict(clean)
        for f in FIELDS:
            if final[f] is None and (f in bad or (f in ENRICHABLE and str(prov.get(f) or "").startswith("gazetteer"))):
                final[f] = local[f]
    if agree:
        conf = max(local_conf, 0.95)
    elif foreign:
        conf = 0.60 if not bad else 0.40
    else:
        conf = 0.60 if base == "local" else (0.70 if not bad else 0.55)
    changed = [f for f in FIELDS if jnorm(final[f]) != jnorm(local[f])]
    out = {**row, **final, "confidence": round(conf, 4), "country": country}
    out["ai"] = {"used": True, "base": base, "agree": agree, "changed": changed, "invalid": bad,
                 "notes": ai.get("notes")}
    return out


def apply_ai(rows: list[dict], mode: str, client: AIClient | None = None) -> list[dict]:
    """rows: local parse_one(explain=True) results (they carry provenance/flags/country)."""
    if mode not in MODES:
        raise ValueError(f"ai mode must be one of {MODES}")
    if mode == "off":
        return rows
    client = client or get_client()
    ok, why = client.available()
    idx = route(rows, mode, client.cfg)
    if not idx:
        return rows
    if not ok:
        for i in idx:
            rows[i] = {**rows[i], "ai": {"used": False, "error": why}}
        return rows
    items = [{"id": f"r{i}", "raw_address": rows[i].get("raw_address", ""),
              **({"draft": _draft(rows[i])} if client.cfg.send_draft else {})} for i in idx]
    got, errors = client.parse(items)
    for i, it in zip(idx, items):
        ai = got.get(it["id"])
        if ai is None:
            rows[i] = {**rows[i], "ai": {"used": False, "error": errors.get(it["id"], "no answer")}}
            continue
        rows[i] = merge(rows[i], ai, client.cfg)
    return rows

