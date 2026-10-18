"""Runtime AI path — everything except the network: prompt, request knobs, pricing, validation, routing, merging,
circuit breaker, API wiring. A fake transport stands in for the model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.countries import detect_country, is_country_word  # noqa: E402
from jana_address.llm import client as ai_client  # noqa: E402
from jana_address.llm.client import AIClient, AIConfig, _Overflow, mask_pii, request_params  # noqa: E402
from jana_address.llm.policy import apply_ai, merge, route, validate  # noqa: E402
from jana_address.llm.pricing import cost_usd, price_row  # noqa: E402
from jana_address.llm.prompt import OUTPUT_SCHEMA, RECORD_SCHEMA, approx_tokens, prompt_version, system_prompt  # noqa: E402
from jana_address.service import parse_batch, parse_one  # noqa: E402

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
JURY_KEYS = {"id", "raw_address", *FIELDS, "confidence"}


def ai_rec(id_, **fields):
    return {"id": id_, "country": fields.pop("country", "KZ"), **{f: fields.get(f) for f in FIELDS}, "notes": None}


def echo_transport(answers: dict | None = None, calls: list | None = None):
    """Fake model: returns `answers[raw]` when given, else echoes the draft (= agrees with the local parser)."""
    def send(items, cfg):
        if calls is not None:
            calls.append(len(items))
        recs = []
        for it in items:
            ans = (answers or {}).get(it["raw_address"])
            recs.append(ai_rec(it["id"], **(ans if ans is not None else (it.get("draft") or {}))))
        return recs, {"input_tokens": 100 * len(items), "output_tokens": 130 * len(items),
                      "cache_write_tokens": 0, "cache_read_tokens": 12000}
    return send


# ----------------------------------------------------------------------------- prompt
def test_prompt_contains_rules_and_all_examples():
    sp = system_prompt()
    conv = json.loads((ROOT / "data" / "conventions_examples.json").read_text(encoding="utf-8"))["examples"]
    assert sp.count("<example>") == len(conv) + 16
    for needle in ("Copy, never rewrite", "<canonical_provinces>", "<street_types>", "<international>",
                   "область Ұлытау", "12/2", "исправляю"):
        assert needle in sp
    assert 4_000 < approx_tokens(sp) < 20_000           # cacheable (≥ 512) and well under the 100k Haiku tier
    assert len(prompt_version()) == 12


def test_packaged_examples_match_repo_examples():
    packaged = json.loads((ROOT / "api/jana_address/llm/prompts/conventions_examples.json").read_text(encoding="utf-8"))
    repo = json.loads((ROOT / "data/conventions_examples.json").read_text(encoding="utf-8"))
    assert [(e["raw_address"], e["expected"]) for e in packaged["examples"]] == \
           [(e["raw_address"], e["expected"]) for e in repo["examples"]], "re-copy data/conventions_examples.json"


def test_schema_within_structured_output_limits():
    props = RECORD_SCHEMA["properties"]
    unions = sum(1 for v in props.values() if "anyOf" in v)
    assert unions <= 16 and set(RECORD_SCHEMA["required"]) == set(props)        # no optional parameters
    assert OUTPUT_SCHEMA["additionalProperties"] is False and RECORD_SCHEMA["additionalProperties"] is False


# ----------------------------------------------------------------------------- request knobs and price
@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"])
def test_no_temperature_on_models_that_reject_it(model):
    p = request_params(model, "low")
    assert "extra_body" not in p and "temperature" not in p
    assert p["output_config"]["effort"] == "low" and p["output_config"]["format"]["type"] == "json_schema"


def test_temperature_zero_where_still_accepted():
    p = request_params("claude-haiku-4-5", "low")
    assert p["extra_body"] == {"temperature": 0.0} and "effort" not in p["output_config"]


def test_pricing_reproduces_the_console():
    # 2026-10-18 in the Console, Opus 5.5: cache write 777 722, cache read 221 334, input 372, output 351 113 → $10.96
    usd = cost_usd("claude-opus-5-5", input_tokens=372, output_tokens=351_113,
                   cache_write_tokens=777_722, cache_read_tokens=221_334)
    assert abs(usd - 10.96) < 0.01
    assert price_row("claude-haiku-5-5-20261007") == price_row("claude-haiku-5-5")
    assert cost_usd("claude-haiku-5-5", input_tokens=200_000) == pytest.approx(0.1)        # >100k prompt tier
    assert cost_usd("claude-sonnet-5-5", output_tokens=1_000_000, batch=True) == pytest.approx(5.0)
    assert cost_usd("qwen2.5-32b", output_tokens=10**6) is None


def test_pii_is_masked():
    s = mask_pii("Алматы, Абая 10, тел. 8 701 123 45 67, +7 (777) 123-45-67, a.b@mail.kz")
    assert "701" not in s and "777" not in s and "@" not in s and "Абая 10" in s


# ----------------------------------------------------------------------------- validation
def test_validate_enforces_copy_not_rewrite():
    raw = "Қарағанды қ., ул. казибек би 12 корпус 2, кв 5"
    clean, bad = validate(raw, ai_rec("x", province="Карагандинская обл.", city="Караганда", street_type="ул.",
                                      street_name="Казыбек би", house="12/2", apartment="кв 5"))
    assert clean["city"] == "Караганда"                 # Kazakh spelling → Russian name is allowed
    assert clean["house"] == "12/2"                      # корпус join is allowed
    assert set(bad) == {"province", "street_type", "street_name", "apartment"}
    assert clean["street_name"] is None and clean["apartment"] is None


def test_validate_foreign_province_is_a_span():
    clean, bad = validate("Россия, Московская обл., г. Химки, ул. Ленина 5",
                          ai_rec("x", country="RU", province="Московская обл.", city="Химки", street_type="улица",
                                 street_name="Ленина", house="5"))
    assert not bad and clean["province"] == "Московская обл."


# ----------------------------------------------------------------------------- routing and merging
def test_route_modes():
    rows = [{"confidence": 0.99, "country": "KZ"}, {"confidence": 0.5, "country": "KZ"}, {"confidence": 0.99, "country": "RU"}]
    cfg = AIConfig(min_conf=0.8)
    assert route(rows, "always", cfg) == [0, 1, 2]
    assert route(rows, "fallback", cfg) == [1, 2]
    assert route(rows, "foreign", cfg) == [2]


def test_merge_policy():
    cfg = AIConfig(keep_local_conf=0.9)
    raw = "Алматы, ул. Абая 10, кв 5"
    row = parse_one("a", raw, explain=True)
    same = {f: row[f] for f in FIELDS if not str(row["provenance"].get(f) or "").startswith("gazetteer")}
    agreed = merge(dict(row), ai_rec("a", **same), cfg)
    assert agreed["ai"]["agree"] and agreed["confidence"] >= 0.95
    disagree = merge(dict(row), ai_rec("a", **{**same, "apartment": "10"}), cfg)  # high local confidence → keep local
    assert disagree["apartment"] == "5" and disagree["ai"]["base"] == "local" and disagree["confidence"] <= 0.6
    low = {**row, "confidence": 0.4}
    took = merge(dict(low), ai_rec("a", **{**same, "apartment": "10"}), cfg)
    assert took["apartment"] == "10" and took["ai"]["base"] == "ai"
    bad = merge(dict(low), ai_rec("a", **{**same, "street_name": "Абай"}), cfg)  # rewritten value → local kept
    assert bad["street_name"] == "Абая" and bad["ai"]["invalid"] == ["street_name"]


# ----------------------------------------------------------------------------- country detection
@pytest.mark.parametrize("raw,code", [
    ("Россия, г. Москва, ул. Тверская, д. 7, кв. 15", "RU"),
    ("Кыргызстан, г. Бишкек, мкр Джал-29, д. 12, кв 4", "KG"),
    ("Uzbekistan, Tashkent, Amir Temur street 108, apt 21", "UZ"),
    ("10115 Berlin, Invalidenstraße 117, Wohnung 3", "DE"),
    ("Республика Казахстан, Алматы, Абая 10", "KZ"),
    ("Алматы, ТЦ Москва, 2 этаж", None),
    ("кафе Грузия, Алматы, Абая 5", None),
    ("Алматы, ул. Ташкентская 10", None),
])
def test_detect_country(raw, code):
    assert detect_country(raw) == code


def test_foreign_address_is_flagged_and_not_confident():
    r = parse_one("k", "Кыргызстан, г. Бишкек, мкр Джал-29, д. 12, кв 4", explain=True)
    assert r["country"] == "KG" and "foreign:KG" in r["flags"] and r["confidence"] <= 0.3
    assert r["city"] is None or not is_country_word(r["city"])


# ----------------------------------------------------------------------------- client behaviour
def test_batches_split_and_parallel():
    calls = []
    c = AIClient(AIConfig(batch_size=7, concurrency=3), transport=echo_transport(calls=calls))
    got, errs = c.parse([{"id": f"r{i}", "raw_address": f"Абая {i}"} for i in range(20)])
    assert sorted(calls) == [6, 7, 7] and len(got) == 20 and not errs
    assert c.stats.as_dict()["records"] == 20 and c.stats.as_dict()["cost_usd"] > 0


def test_overflow_is_split_in_half():
    def send(items, cfg):
        if len(items) > 2:
            raise _Overflow({"input_tokens": 0, "output_tokens": 10})
        return [ai_rec(it["id"]) for it in items], {"input_tokens": 1, "output_tokens": 1}
    got, errs = AIClient(AIConfig(batch_size=8, concurrency=1), transport=send).parse(
        [{"id": f"r{i}", "raw_address": "x"} for i in range(8)])
    assert len(got) == 8 and not errs


def test_circuit_breaker_keeps_local_answers():
    def broken(items, cfg):
        raise ConnectionError("network down")
    c = AIClient(AIConfig(batch_size=1, concurrency=1, max_failures=3, cooldown_s=60), transport=broken)
    rows = [parse_one(str(i), "Алматы, ул. Абая 10, кв 5", explain=True) for i in range(5)]
    out = apply_ai([dict(r) for r in rows], "always", c)
    assert all(r["ai"]["used"] is False for r in out) and [r["apartment"] for r in out] == ["5"] * 5
    assert c.stats.failures == 3 and not c.available()[0]            # 3 failures opened the circuit


def test_default_mode_is_off_and_never_calls(monkeypatch):
    monkeypatch.delenv("JANA_AI_MODE", raising=False)
    calls = []
    ai_client.set_client(AIClient(AIConfig(), transport=echo_transport(calls=calls)))
    try:
        rows = parse_batch([{"id": "1", "raw_address": "Алматы, ул. Абая 10, кв 5"}])
        assert not calls and "ai" not in rows[0]
    finally:
        ai_client.set_client(None)


def test_api_with_ai_keeps_the_jury_schema():
    from jana_address.main import app
    calls = []
    raw = "Кыргызстан, г. Бишкек, мкр Джал-29, д. 12, кв 4"
    answers = {raw: {"country": "KG", "city": "Бишкек", "street_type": "микрорайон", "street_name": "Джал-29",
                     "house": "12", "apartment": "4"}}
    ai_client.set_client(AIClient(AIConfig(), transport=echo_transport(answers, calls)))
    try:
        with TestClient(app) as tc:
            body = [{"id": "kz", "raw_address": "Алматы, ул. Абая 10, кв 5"}, {"id": "kg", "raw_address": raw}]
            r = tc.post("/api/v1/parse/batch?ai=fallback", json=body)
            assert r.status_code == 200, r.text
            out = r.json()
            assert all(set(o) == JURY_KEYS for o in out)
            assert out[1]["city"] == "Бишкек" and out[1]["street_name"] == "Джал-29" and calls == [1]
            r = tc.post("/api/v1/parse/batch?ai=always&explain=1", json=body)
            ex = r.json()
            assert ex[0]["ai"]["used"] and ex[1]["country"] == "KG"
            st = tc.get("/api/v1/ai/status").json()
            assert st["stats"]["records"] == 3 and st["prompt_version"] == prompt_version()
            assert tc.post("/api/v1/parse/batch?ai=bogus", json=body).status_code == 422
    finally:
        ai_client.set_client(None)
