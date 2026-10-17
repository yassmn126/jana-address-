from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from jana_address.main import app  # noqa: E402

JURY_KEYS = {"id", "raw_address", "province", "city", "district", "living_area", "street_type", "street_name",
             "house", "apartment", "entrance", "floor", "landmark", "confidence"}


def test_batch_array_body_exact_schema():
    with TestClient(app) as c:
        payload = [{"id": "rec_1", "raw_address": "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70"},
                   {"id": "rec_2", "raw_address": ""}]
        r = c.post("/api/v1/parse/batch", json=payload)
        assert r.status_code == 200, r.text
        out = r.json()
        assert len(out) == 2
        assert set(out[0]) == JURY_KEYS, set(out[0]) ^ JURY_KEYS
        assert out[0]["street_name"] == "Аксай 3а" and out[0]["house"] == "59" and out[0]["apartment"] == "70"
        assert out[1]["id"] == "rec_2" and out[1]["confidence"] <= 0.1


def test_batch_wrapper_and_explain():
    with TestClient(app) as c:
        r = c.post("/api/v1/parse/batch?explain=1", json={"records": [{"id": "a", "raw_address": "Ул .Курильская 9 кв 1"}]})
        assert r.status_code == 200, r.text
        out = r.json()[0]
        assert "spans" in out and "tokens" in out and "provenance" in out
        assert any(s["field"] == "STREET_NAME" and s["text"] == "Курильская" for s in out["spans"])


def test_batch_file_upload_on_both_routes():
    with TestClient(app) as c:
        data = json.dumps([{"id": "f1", "raw_address": "Кулманова 133"}], ensure_ascii=False).encode("utf-8")
        for route in ("/api/v1/parse/batch/file", "/api/v1/parse/batch"):
            r = c.post(route, files={"file": ("in.json", io.BytesIO(data), "application/json")})
            assert r.status_code == 200, r.text
            assert r.json()[0]["house"] == "133"
            assert set(r.json()[0]) == JURY_KEYS
        # odd field name + download flag
        r = c.post("/api/v1/parse/batch?download=1", files={"input": ("x.json", io.BytesIO(data), "application/json")})
        assert r.status_code == 200 and "attachment" in r.headers.get("content-disposition", "")


def test_frontend_alias_route():
    """app.js (static frontend) posts to /api/parse."""
    with TestClient(app) as c:
        r = c.post("/api/parse", json=[{"id": "a", "raw_address": "жуалы 19"}])
        assert r.status_code == 200, r.text
        assert r.json()[0]["street_name"] == "жуалы" and set(r.json()[0]) == JURY_KEYS


def test_batch_json_lines_and_bom():
    with TestClient(app) as c:
        body = "﻿" + "\n".join(json.dumps({"id": f"l{i}", "raw_address": s}, ensure_ascii=False)
                                     for i, s in enumerate(["жуалы 19", "Кулманова 133"]))
        r = c.post("/api/v1/parse/batch", content=body.encode("utf-8"), headers={"Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        assert [x["house"] for x in r.json()] == ["19", "133"]


def test_health_and_version():
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"
        assert "version" in c.get("/version").json()


def test_corrections_are_stored(tmp_path, monkeypatch):
    import jana_address.main as m
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/v1/corrections", json={"id": "x", "raw_address": "жуалы 19", "fields": {"street_name": "жуалы", "house": "19"}})
        assert r.status_code == 200 and r.json()["stored"]
        assert (tmp_path / "corrections.jsonl").read_text(encoding="utf-8").count("\n") == 1
