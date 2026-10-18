"""Fuzz: the brief says no record may be skipped or fail. Thousands of hostile strings must each return the exact
jury schema, values cut from the string, confidence in [0, 1], in bounded time."""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.service import parse_batch, parse_one  # noqa: E402

KEYS = {"id", "raw_address", "province", "city", "district", "living_area", "street_type", "street_name", "house",
        "apartment", "entrance", "floor", "landmark", "confidence"}
ALPHABETS = [
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
    "әғқңөұүһіӘҒҚҢӨҰҮҺІ",
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "0123456789", " ,.;:-/№()«»\"'#+*&%$@!?_=<>[]{}|\\",
    "​‌‍﻿  \t\n\r\x00\x07\x1b",
    "😀🏠📦🇰🇿✓→—…", "中文字符日本語한국어", "αβγδε", "עברית", "عربى",
]
KEYWORDS = ["ул.", "кв", "дом", "д.", "мкр", "подъезд", "этаж", "г.", "район", "обл", "ЖК", "пр-т", "көшесі", "пәтер", "корпус",
            "код", "тел", "/", "-", "№", "Алматы", "Астана", "Абая", "55/6", "12а", "3/9", "87011234567", "Казахстан"]


def hostile(rnd: random.Random) -> str:
    kind = rnd.random()
    if kind < 0.35:                                   # keyword soup: plausible tokens in random order
        return " ".join(rnd.choice(KEYWORDS + [str(rnd.randint(0, 99999))]) for _ in range(rnd.randint(1, 25)))
    if kind < 0.7:                                    # random characters from mixed scripts
        pool = "".join(rnd.sample(ALPHABETS, rnd.randint(1, 4)))
        return "".join(rnd.choice(pool) for _ in range(rnd.randint(0, 120)))
    if kind < 0.8:
        return rnd.choice(["", " ", "\n", ",,,,", "////", "кв кв кв кв", "1/2/3/4/5/6/7", "-" * 50, "<script>alert(1)</script>",
                           "'; DROP TABLE addresses; --", "None", "null", "NaN", "\\u0000", "%s%s%s", "{{7*7}}"])
    if kind < 0.9:                                    # very long input
        return ("ул. Абая 10, кв 5, " * rnd.randint(50, 400))[: rnd.randint(1000, 8000)]
    return "".join(chr(rnd.randint(1, 0x2FFF)) for _ in range(rnd.randint(1, 60)))


def test_fuzz_never_fails_and_keeps_the_schema():
    rnd = random.Random(20261018)
    t0 = time.perf_counter()
    for i in range(2500):
        raw = hostile(rnd)
        out = parse_one(f"f{i}", raw)
        assert set(out) == KEYS, (raw, set(out) ^ KEYS)
        assert out["raw_address"] == raw and out["id"] == f"f{i}"
        assert 0.0 <= out["confidence"] <= 1.0
        for f in ("city", "street_name", "house", "apartment", "entrance", "floor", "landmark", "district", "living_area"):
            v = out[f]
            assert v is None or (isinstance(v, str) and v.strip() == v and v), (raw, f, v)
        assert not out["confidence"] > 0.1 or any(out[f] for f in ("street_name", "house", "apartment", "city")), (raw, out)
    assert time.perf_counter() - t0 < 120              # ~2500 hostile records well inside the brief's budget


def test_batch_with_non_string_and_missing_fields():
    rows = parse_batch([{"id": 1, "raw_address": None}, {"raw_address": 12345}, {"id": "x"}, {"id": "y", "raw_address": ["a"]}])
    assert len(rows) == 4 and all(set(r) == KEYS for r in rows)
    assert rows[1]["raw_address"] == "12345"
