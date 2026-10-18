"""AI cross-check: the Claude call path against a stubbed SDK (no network), the comparison, and the endpoint."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from training import label_llm  # noqa: E402


class _Resp:
    def __init__(self, records, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.stop_details = types.SimpleNamespace(category="general_harms") if stop_reason == "refusal" else None
        self.usage = types.SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=80)
        self.content = [types.SimpleNamespace(type="text", text=json.dumps({"records": records}))]


def _fake_anthropic(monkeypatch, behaviour):
    """behaviour(batch_ids, kwargs) -> _Resp. Records every request's kwargs."""
    calls = []

    class _Messages:
        def create(self, **kw):
            ids = [r["id"] for r in json.loads(kw["messages"][0]["content"].split("\n", 1)[1])]
            calls.append((ids, kw))
            return behaviour(ids, kw)

    class _Client:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=_Messages())

    mod = types.SimpleNamespace(Anthropic=_Client)
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return calls


def _rec(i, street="Абая", house="10"):
    return {"id": i, **{f: None for f in label_llm.FIELDS}, "street_name": street, "house": house, "notes": None}


def test_request_shape_structured_output_caching_fallback(monkeypatch):
    calls = _fake_anthropic(monkeypatch, lambda ids, kw: _Resp([_rec(i) for i in ids]))
    usage = {"input": 0, "output": 0}
    out = label_llm.label_with_anthropic([{"id": "a", "raw_address": "Абая 10"}], "SYS", "claude-opus-5-5", usage)
    assert out[0]["street_name"] == "Абая"
    kw = calls[0][1]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] in ("low", "medium", "high", "xhigh", "max")
    assert kw["cache_control"] == {"type": "ephemeral"} and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert usage == {"input": 100, "output": 50, "cache_read": 80}


def test_refusal_raises_and_max_tokens_splits(monkeypatch):
    _fake_anthropic(monkeypatch, lambda ids, kw: _Resp([], stop_reason="refusal"))
    with pytest.raises(label_llm.LLMRefusal):
        label_llm.label_with_anthropic([{"id": "a", "raw_address": "x"}], "SYS", "m", {"input": 0, "output": 0})

    def split_until_small(ids, kw):
        return _Resp([], "max_tokens") if len(ids) > 2 else _Resp([_rec(i) for i in ids])
    calls = _fake_anthropic(monkeypatch, split_until_small)
    batch = [{"id": str(i), "raw_address": "Абая 10"} for i in range(8)]
    out = label_llm.label_with_anthropic(batch, "SYS", "m", {"input": 0, "output": 0})
    assert [r["id"] for r in out] == [str(i) for i in range(8)] and len(calls) == 7


def test_crosscheck_flags_disagreements_and_llm_rewrites(monkeypatch):
    from eval import llm_verify

    def fake(batch, system, model, usage):        # an LLM that «fixes» case and misses the apartment
        return [{**_rec(r["id"], street="Толе Би", house="15"), "apartment": None, "city": "Алматы"} for r in batch]
    monkeypatch.setitem(llm_verify.PROVIDERS, "fake", fake)
    res = llm_verify.run([{"id": "1", "raw_address": "Алматы, ул Толе би 15, кв 3"}], {}, "fake", "m", 20)
    row = res["rows"][0]
    assert "apartment" in row["disagreements"] and row["ours"]["apartment"] == "3"
    assert "street_name" not in row["disagreements"]       # jury normalization: «Толе Би» == «Толе би»
    assert res["summary"]["records_with_disagreement"] == 1


def test_verify_endpoint_with_rules_provider():
    from fastapi.testclient import TestClient
    from jana_address.main import app
    with TestClient(app) as c:
        r = c.post("/api/v1/verify?provider=rules", json=[{"id": "1", "raw_address": "Алматы,Абая,10,5"},
                                                          {"id": "2", "raw_address": "Кулманова 133"}])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["n"] == 2 and {x["id"] for x in body["records"]} == {"1", "2"}
        assert c.post("/api/v1/verify?provider=rules", json=[{"id": str(i), "raw_address": "x"} for i in range(51)]).status_code == 413
