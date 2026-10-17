"""Name keys shared by the gazetteer ETL (data_pipeline/) and the runtime lookups.

`norm()` is a MATCHING key only — values in the output are always cut from the raw string.
It extends preprocess.match_view with:
  * Kazakh letters folded to their Russian neighbours (Сығанақ = Сыганак, Әуезов = Ауезов),
  * э→е, hyphens → space, a space at letter/digit boundaries (Самал-2 = Самал 2 = Самал2),
  * «имени / им.» dropped (улица имени Абая = Абая).

`split_street / split_district / split_living` cut a type word off an OSM name, using the same
lexicon as the rule grammar, so «улица Абая», «Абай даңғылы», «Рыбинский переулок» all split.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..preprocess import match_view

KK_FOLD = str.maketrans({"ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у", "ү": "у", "һ": "х",
                         "і": "и", "э": "е", "ъ": "", "ь": ""})
_LD = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
_ORD = re.compile(r"(\d+)\s*-?\s*(?:й|ый|ий|ой|я|ая|ья|го|ого|ши|шы|нши|ншы|инши|ынши)\b")
_DROP = {"имени", "им", "атындагы", "атындағы"}

DISTRICT_WORDS = {"район", "р-н", "рн", "ауданы", "аудан", "district", "административныи", "административный",
                  "городскои", "городской"}
LIVING_KINDS = {
    "микрорайон": {"мкр", "мкрн", "микрорайон", "м-н", "мн", "микроаудан", "микроауданы", "ыкшам", "шагын",
                   "ыкшамаудан", "ыкшамауданы", "шагынаудан", "шагынауданы", "mkr", "microdistrict"},
    "ЖК": {"жк", "ж/к", "тук", "тургын", "кешени", "residence", "резиденс", "комплекс"},
    "жилой массив": {"ж/м", "жм", "жилмассив", "массив"},
    "квартал": {"квартал", "кв-л", "орам", "орамы"},
}
_LIVING_WORD = {w: k for k, ws in LIVING_KINDS.items() for w in ws}
_LIVING_FILLER = {"жилой", "ауданы", "аудан", "уй"}


def norm(text: str | None) -> str:
    if not text:
        return ""
    t = match_view(text).translate(KK_FOLD)
    t = _ORD.sub(r"\1", t)
    t = re.sub(r"[\-–—_]", " ", t)
    t = _LD.sub(" ", t)
    t = re.sub(r"[^\w\s/№]", " ", t)
    words = [w for w in t.split() if w not in _DROP]
    return " ".join(words)


@lru_cache(maxsize=1)
def _street_types() -> dict[str, str]:
    """variant (norm) → lemma; single letters and initials-like variants are excluded (OSM «Ш. Уалиханова»)."""
    from ..rules.grammar import get_lexicon

    lex = get_lexicon()
    out = {}
    for v, lemma in lex.street_type_of.items():
        k = norm(v)
        if len(k.replace(" ", "")) >= 2 and k not in {"ш", "у", "к", "кош", "жолы"}:
            out[k] = lemma
    for first, seconds in (lex.bigrams or {}).items():
        for second, lemma in seconds.items():
            if lemma != "ЖК":
                out[norm(f"{first} {second}")] = lemma
    return out


def split_street(name: str | None) -> tuple[str | None, str | None]:
    """«улица Абая» → ('улица', 'Абая'); «Абай даңғылы» → ('проспект', 'Абай'); «Абая» → (None, 'Абая')."""
    if not name:
        return None, None
    words = name.replace(" ", " ").split()
    if not words:
        return None, None
    types = _street_types()
    keys = [norm(w) for w in words]
    # two-word types first («ықшам ауданы», «жилой массив»)
    if len(words) >= 3 and f"{keys[0]} {keys[1]}" in types:
        return types[f"{keys[0]} {keys[1]}"], " ".join(words[2:])
    if len(words) >= 3 and f"{keys[-2]} {keys[-1]}" in types:
        return types[f"{keys[-2]} {keys[-1]}"], " ".join(words[:-2])
    if len(words) >= 2 and keys[0] in types:
        return types[keys[0]], " ".join(words[1:]).strip(" .")
    if len(words) >= 2 and keys[-1] in types:
        return types[keys[-1]], " ".join(words[:-1]).strip(" .")
    return None, name.strip()


def split_district(name: str | None) -> tuple[str | None, str | None]:
    """«Алмалинский район» → ('Алмалинский', 'район'); «район Есиль» → ('Есиль', 'район'); «Есіл ауданы» → ('Есіл', 'ауданы')."""
    if not name:
        return None, None
    words = name.replace(" ", " ").split()
    kept, kw = [], None
    for w in words:
        k = norm(w)
        if k in DISTRICT_WORDS or k in {"р н", "р/н"}:
            kw = kw or w
            continue
        if w.lower().strip(".") in {"имени", "им", "атындағы", "атындагы"}:
            continue
        kept.append(w)
    base = " ".join(kept).strip(" .,")
    return (base or None), kw


def split_living(name: str | None) -> tuple[str | None, str | None]:
    """«мкр Самал-2» → ('микрорайон', 'Самал-2'); «ЖК Хайвил» → ('ЖК', 'Хайвил'); «Томирис тұрғын үй кешені» → ('ЖК', 'Томирис')."""
    if not name:
        return None, None
    words = name.replace(" ", " ").replace("«", " ").replace("»", " ").replace('"', " ").split()
    kind, kept = None, []
    for w in words:
        k = norm(w)
        if k in _LIVING_WORD:
            kind = kind or _LIVING_WORD[k]
            continue
        if k in _LIVING_FILLER:
            continue
        kept.append(w)
    # «2-й микрорайон» → «2»
    base = " ".join(kept).strip(" .,")
    base = re.sub(r"^(\d+)\s*-?\s*(й|ый|ий|ой|ші|шы|нші|ншы|інші|ыншы)$", r"\1", base)
    return kind, (base or None)


def base_of_numbered(name: str | None) -> str:
    """«Аксай 3а» / «Самал-2» / «Аксай-3А» → «Аксай» / «Самал»; names without a number are returned as is."""
    if not name:
        return ""
    m = re.match(r"^(.*?[^\W\d_])[\s\-]*\d+[^\W\d_]?$", name.strip())
    return m.group(1) if m else name.strip()
