"""Offline country detection — is this address in Kazakhstan at all?

The jury data is Kazakhstani, but real Jana Post traffic is not only that. The local parser was built for Kazakhstan, so
on «Кыргызстан, г. Бишкек, …» it took the country for the city and still reported high confidence. This module spots a
foreign address from explicit evidence only, so a Kazakhstani address is never touched by accident:

  * a country name/code as a separate phrase («Россия», «РФ», «Uzbekistan», «Deutschland»), not inside a landmark or a
    street name («магазин Корея», «ул. Грузинская», «ресторан Турция» do not count);
  * or a well-known foreign city written as the settlement («г. Москва», «Бишкек, …» at the start of a part), when the
    string names no Kazakhstani region/city and the gazetteer does not know that name in Kazakhstan.

Returns an ISO 3166-1 alpha-2 code: "KZ" for an explicit Kazakhstan mention, a foreign code, or None (= assume KZ).
"""
from __future__ import annotations

import re

COUNTRIES: dict[str, tuple[str, ...]] = {
    "KZ": ("казахстан", "республика казахстан", "рк", "kazakhstan", "qazaqstan", "kz", "қазақстан", "қазақстан республикасы"),
    "RU": ("россия", "рф", "российская федерация", "russia", "russian federation", "ресей"),
    "KG": ("кыргызстан", "киргизия", "кыргызская республика", "kyrgyzstan", "kyrgyz republic", "қырғызстан"),
    "UZ": ("узбекистан", "uzbekistan", "o'zbekiston", "ozbekiston", "ўзбекистон", "өзбекстан"),
    "TJ": ("таджикистан", "tajikistan", "тәжікстан"),
    "TM": ("туркменистан", "turkmenistan", "түрікменстан"),
    "BY": ("беларусь", "белоруссия", "республика беларусь", "belarus"),
    "UA": ("украина", "ukraine"),
    "CN": ("китай", "кнр", "china", "prc", "қытай"),
    "TR": ("турция", "turkey", "türkiye", "turkiye", "түркия"),
    "DE": ("германия", "germany", "deutschland"),
    "US": ("сша", "usa", "united states", "united states of america"),
    "GB": ("великобритания", "united kingdom", "england"),
    "AE": ("оаэ", "uae", "united arab emirates"),
    "GE": ("грузия", "georgia", "sakartvelo"),
    "AM": ("армения", "armenia"),
    "AZ": ("азербайджан", "azerbaijan"),
    "MN": ("монголия", "mongolia"),
    "KR": ("южная корея", "республика корея", "south korea", "republic of korea"),
    "PL": ("польша", "poland", "polska"),
    "CZ": ("чехия", "czech republic", "czechia"),
    "LT": ("литва", "lithuania"),
    "LV": ("латвия", "latvia"),
    "EE": ("эстония", "estonia"),
    "IT": ("италия", "italy", "italia"),
    "FR": ("франция", "france"),
    "ES": ("испания", "spain", "españa"),
    "NL": ("нидерланды", "netherlands"),
    "IL": ("израиль", "israel"),
    "CA": ("канада", "canada"),
    "IN": ("индия", "india"),
    "JP": ("япония", "japan"),
}
# Settlements that are unambiguously foreign (checked against the Kazakhstani gazetteer at run time as well).
FOREIGN_CITIES: dict[str, tuple[str, ...]] = {
    "RU": ("москва", "санкт-петербург", "новосибирск", "омск", "екатеринбург", "челябинск", "оренбург", "барнаул",
           "астрахань", "самара", "казань", "тюмень", "курган", "moscow", "saint petersburg", "novosibirsk"),
    "KG": ("бишкек", "каракол", "джалал-абад", "bishkek"),
    "UZ": ("ташкент", "самарканд", "бухара", "наманган", "андижан", "tashkent", "samarkand", "toshkent"),
    "TJ": ("душанбе", "худжанд", "dushanbe"),
    "BY": ("минск", "minsk"),
    "CN": ("урумчи", "пекин", "шанхай", "гуанчжоу", "кульджа", "urumqi", "beijing", "shanghai", "guangzhou"),
    "TR": ("стамбул", "анкара", "анталья", "istanbul", "ankara", "antalya"),
    "AE": ("дубай", "абу-даби", "dubai", "abu dhabi"),
    "GE": ("тбилиси", "батуми", "tbilisi", "batumi"),
    "AM": ("ереван", "yerevan"),
    "AZ": ("баку", "baku"),
    "DE": ("берлин", "мюнхен", "гамбург", "франкфурт", "berlin", "münchen", "munich", "hamburg", "frankfurt"),
    "GB": ("лондон", "london"),
    "US": ("нью-йорк", "new york", "los angeles", "chicago"),
    "KR": ("сеул", "seoul"),
}
# a phrase right after one of these words is a name of a shop / street / complex, not a country or a city
_NAME_CONTEXT = {"магазин", "маг", "тц", "трц", "тд", "бц", "кафе", "ресторан", "рест", "бар", "отель", "гостиница",
                 "салон", "ул", "улица", "пр", "проспект", "пер", "переулок", "мкр", "микрорайон", "жк", "ст", "снт",
                 "остановка", "ост", "рынок", "базар", "аптека", "школа", "клиника", "центр", "супермаркет", "street",
                 "st", "shop", "cafe", "restaurant", "көшесі", "даңғылы", "дүкені", "бульвар", "б-р", "сквер", "парк",
                 "кинотеатр", "кино", "театр", "банк", "клуб", "фитнес", "сауна", "завод", "комбинат", "компания",
                 "тоо", "ип", "ао", "ооо", "hotel", "mall", "plaza", "ск", "дк", "дом", "фирма", "склад", "офис"}
_CITY_KW = {"г", "гор", "город", "city", "қаласы", "қ", "shahri", "шаары", "stadt"}
_TOKEN = re.compile(r"[0-9A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІіÄäÖöÜüŞşĞğİıÇçÉéŪūŋ'’-]+")


def _tokens(raw: str) -> list[str]:
    return [t.lower().replace("ё", "е").replace("’", "'") for t in _TOKEN.findall(raw or "")]


def _index(table: dict[str, tuple[str, ...]]) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """first word → [(code, phrase words)] so a string is scanned once, not once per phrase."""
    idx: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for code, phrases in table.items():
        for ph in phrases:
            words = tuple(ph.split())
            idx.setdefault(words[0], []).append((code, words))
    return idx


_COUNTRY_IDX = _index(COUNTRIES)
_CITY_IDX = _index(FOREIGN_CITIES)


def _hits(tokens: list[str], idx) -> list[tuple[int, str, str]]:
    """[(position, code, phrase)] for every phrase of `idx` found as whole words."""
    out = []
    for i, tok in enumerate(tokens):
        for code, words in idx.get(tok, ()):
            if tuple(tokens[i:i + len(words)]) == words:
                out.append((i, code, " ".join(words)))
    return out


def _clean_hit(tokens: list[str], i: int) -> bool:
    return not (i > 0 and tokens[i - 1].rstrip(".") in _NAME_CONTEXT)


def detect_country(raw: str, kz_signal: bool = False, known_in_kz=None) -> str | None:
    """kz_signal: the local parse found a Kazakhstani province (from the string or the gazetteer).
    known_in_kz(name) -> bool: gazetteer check for the foreign-city rule (None = skip the check)."""
    toks = _tokens(raw)
    if not toks:
        return None
    hits = [(i, code) for i, code, _ in _hits(toks, _COUNTRY_IDX) if _clean_hit(toks, i)]
    if hits:                                             # an explicit Kazakhstan mention wins over anything else
        return "KZ" if any(code == "KZ" for _, code in hits) else min(hits)[1]
    if kz_signal:
        return None
    for i, code, name in _hits(toks, _CITY_IDX):
        if not _clean_hit(toks, i):
            continue
        before = toks[i - 1].rstrip(".") if i > 0 else ""
        if (i == 0 or before in _CITY_KW or before.isdigit()) and not (known_in_kz and known_in_kz(name)):
            return code
    return None


def is_country_word(value: str | None) -> bool:
    """«Кыргызстан» / «Uzbekistan» parsed as a city is a country, not a settlement."""
    if not value:
        return False
    v = " ".join(_tokens(value))
    return any(v == ph for phrases in COUNTRIES.values() for ph in phrases)


def local_country(raw: str, fields: dict, prov: dict) -> str | None:
    """Country for parse_one (offline): explicit country words, or a foreign city written as the settlement when the
    parse found nothing Kazakhstani in the string."""
    from .gazetteer.osm import get_gazetteer

    gz = get_gazetteer()

    def known(name: str) -> bool:
        try:
            return gz.settlement(name) is not None
        except Exception:  # noqa: BLE001
            return False

    kz_signal = fields.get("province") is not None and str(prov.get("province") or "").startswith("string")
    code = detect_country(raw, kz_signal=kz_signal, known_in_kz=known)
    if code is None and fields.get("city") and is_country_word(fields["city"]):
        code = detect_country(fields["city"])
    return code
