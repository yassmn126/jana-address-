"""Human-in-the-loop conflict queue: classification, the decision math, and the scan → list → resolve API flow.
No network: the "AI" is a stub provider that copies our answer and disagrees on purpose."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.conflicts import ConflictStore, classify, conflict_id, final_fields  # noqa: E402

F = ["province", "city", "district", "living_area", "street_type", "street_name",
     "house", "apartment", "entrance", "floor", "landmark"]


def _fields(**kw):
    return {f: kw.get(f) for f in F}


# ----------------------------------------------------------------------------- classify / final_fields
def test_classify_reasons():
    ours = _fields(city="Алматы", street_name="Абая", house="10")
    row = {"id": "1", "raw_address": "Алматы, Абая 10", "ours": ours, "confidence": 0.95}
    assert classify({**row, "llm": ours, "disagreements": [], "issues": []}) is None          # agree, confident

    dis = classify({**row, "llm": {**ours, "house": "10а"}, "disagreements": ["house"], "issues": []})
    assert dis["reasons"] == ["disagreement"] and dis["disagreements"] == ["house"] and dis["ai"]["house"] == "10а"
    assert dis["conflict_id"] == conflict_id("1", "Алматы, Абая 10")

    low = classify({**row, "confidence": 0.4, "llm": ours, "disagreements": [], "issues": []})
    assert low["reasons"] == ["low_confidence"]
    assert classify({**row, "confidence": 0.4, "llm": ours, "disagreements": [], "issues": []}, min_confidence=0.3) is None

    rewrite = classify({**row, "llm": ours, "disagreements": [], "issues": ["llm_not_in_string:street_name"]})
    assert rewrite["reasons"] == ["ai_issue"]

    # the AI failed: nothing to compare against — queued only when we are unsure ourselves
    err = {**row, "llm": _fields(), "disagreements": list(F), "issues": ["llm_error:refusal"]}
    assert classify(err) is None
    err_low = classify({**err, "confidence": 0.3})
    assert err_low["reasons"] == ["low_confidence", "ai_error"] and err_low["disagreements"] == []


def test_final_fields_choices_picks_overrides():
    item = {"ours": _fields(street_name="Абая", house="10", apartment="5"),
            "ai": _fields(street_name="абая", house="10а", apartment="6", entrance="2"),
            "disagreements": ["house", "apartment"]}
    assert final_fields(item, "ours") == item["ours"]
    ai = final_fields(item, "ai")
    assert ai["house"] == "10а" and ai["apartment"] == "6"
    assert ai["street_name"] == "Абая" and ai["entrance"] is None     # agreeing / non-disputed fields keep our exact copy
    mixed = final_fields(item, "custom", picks={"house": "ai"}, overrides={"floor": "  3 ", "apartment": ""})
    assert mixed["house"] == "10а" and mixed["apartment"] is None and mixed["floor"] == "3"


def test_store_upsert_keeps_decisions(tmp_path):
    store = ConflictStore(tmp_path / "q.sqlite3")
    it = classify({"id": "1", "raw_address": "x", "ours": _fields(house="1"), "llm": _fields(house="2"),
                   "disagreements": ["house"], "issues": [], "confidence": 0.9})
    assert store.upsert([it], "stub", "m") == {"queued": 1, "updated": 0, "kept_decided": 0}
    assert store.upsert([it], "stub", "m") == {"queued": 0, "updated": 1, "kept_decided": 0}   # re-scan refreshes
    store.decide(it["conflict_id"], "ai")
    assert store.upsert([it], "stub", "m")["kept_decided"] == 1                                 # a decision is kept
    assert store.counts() == {"pending": 0, "resolved": 1, "dismissed": 0}
    assert store.upsert([it], "stub", "m", requeue=True)["updated"] == 1
    assert store.counts()["pending"] == 1


# ----------------------------------------------------------------------------- API flow
CLEAR = "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70"        # the sample record: confident, AI agrees
DISPUTED = "город Алматы, ул Толе би 15, кв 3"                          # the stub AI reads another flat number
JUNK = "позвонить перед доставкой"                                      # not an address: our confidence is low


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from eval import llm_verify
    import jana_address.main as m

    def stub_ai(batch, system, model, usage):        # copies our string fields; disagrees on the apartment of DISPUTED
        out = []
        for r in batch:
            ours, _ = llm_verify.our_fields(r["id"], r["raw_address"])
            if r["raw_address"] == DISPUTED:
                ours = {**ours, "apartment": "31"}
            out.append({"id": r["id"], **ours})
        return out

    monkeypatch.setitem(llm_verify.PROVIDERS, "stub", stub_ai)
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    with TestClient(m.app) as c:
        yield c, tmp_path


def _scan(c, **params):
    q = "&".join(f"{k}={v}" for k, v in {"provider": "stub", "model": "test", **params}.items())
    return c.post(f"/api/v1/conflicts/scan?{q}", json=[{"id": "a", "raw_address": CLEAR},
                                                        {"id": "b", "raw_address": DISPUTED},
                                                        {"id": "c", "raw_address": JUNK}])


def test_scan_list_resolve_flow(client):
    c, data = client
    r = _scan(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 3 and body["needs_review"] == 2 and body["queued"] == 2
    assert body["counts"] == {"pending": 2, "resolved": 0, "dismissed": 0}

    listing = c.get("/api/v1/conflicts").json()
    by_rec = {it["record_id"]: it for it in listing["items"]}
    assert set(by_rec) == {"b", "c"} and listing["total"] == 2
    b, cc = by_rec["b"], by_rec["c"]
    # «31» is not in «…кв 3»: besides the disagreement, the copy-not-rewrite validator flags the AI's invented value
    assert b["reasons"] == ["disagreement", "ai_issue"] and b["disagreements"] == ["apartment"]
    assert any("apartment" in i for i in b["issues"])
    assert b["ours"]["apartment"] == "3" and b["ai"]["apartment"] == "31"
    assert "low_confidence" in cc["reasons"] and cc["confidence"] < 0.7
    assert c.get(f"/api/v1/conflicts/{b['conflict_id']}").json()["raw_address"] == DISPUTED

    # take the AI's answer → resolved, written to corrections.jsonl with the AI's flat
    res = c.post(f"/api/v1/conflicts/{b['conflict_id']}/resolve", json={"choice": "ai", "reviewer": "op1"})
    assert res.status_code == 200, res.text
    assert res.json()["item"]["status"] == "resolved" and res.json()["item"]["resolution"]["fields"]["apartment"] == "31"
    lines = (data / "corrections.jsonl").read_text(encoding="utf-8").splitlines()
    corr = json.loads(lines[0])
    assert len(lines) == 1 and corr["id"] == "b" and corr["fields"]["apartment"] == "31" and corr["reviewer"] == "op1"
    assert "changed=apartment" in corr["note"]
    assert c.post(f"/api/v1/conflicts/{b['conflict_id']}/resolve", json={"choice": "ours"}).status_code == 409

    # one-click approve of our own output
    ok = c.post(f"/api/v1/conflicts/{cc['conflict_id']}/approve?side=ours").json()
    assert ok["item"]["resolution"]["choice"] == "ours" and ok["counts"] == {"pending": 0, "resolved": 2, "dismissed": 0}
    assert len((data / "corrections.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    # decisions survive a re-scan; requeue re-opens them; dismiss writes no correction
    assert _scan(c).json()["kept_decided"] == 2
    assert _scan(c, requeue="true").json()["counts"]["pending"] == 2
    d = c.post(f"/api/v1/conflicts/{b['conflict_id']}/resolve", json={"choice": "dismiss"}).json()
    assert d["item"]["status"] == "dismissed" and d["correction_path"] is None
    assert len((data / "corrections.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert c.get("/api/v1/conflicts?status=dismissed").json()["total"] == 1


def test_conflict_api_errors(client):
    c, _ = client
    assert c.get("/api/v1/conflicts/nope").status_code == 404
    assert c.post("/api/v1/conflicts/nope/resolve", json={"choice": "ours"}).status_code == 404
    _scan(c)
    cid = c.get("/api/v1/conflicts").json()["items"][0]["conflict_id"]
    assert c.post(f"/api/v1/conflicts/{cid}/resolve", json={"choice": "custom", "picks": {"zip": "ai"}}).status_code == 422
    assert c.post(f"/api/v1/conflicts/{cid}/resolve", json={"choice": "maybe"}).status_code == 422
    assert c.get("/api/v1/conflicts?status=weird").status_code == 422
    assert c.post("/api/v1/conflicts/scan?provider=nobody", json=[{"id": "1", "raw_address": "x"}]).status_code == 400
    assert c.post("/api/v1/conflicts/scan?provider=stub",
                  json=[{"id": str(i), "raw_address": "x"} for i in range(51)]).status_code == 413
