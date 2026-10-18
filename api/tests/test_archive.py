"""Run archive: every batch is kept in SQLite under JANA_DATA_DIR, with the in-database flag per record."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JANA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JANA_ARCHIVE", "on")
    from jana_address import archive, main
    monkeypatch.setattr(archive, "_ARCHIVE", None)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def test_batches_are_archived_with_in_database_flag(client, tmp_path):
    body = [{"id": "1", "raw_address": "Алматы, ул. Абая 10, кв 5"},
            {"id": "2", "raw_address": "Актау, 14 мкр, 59 дом, кв 12"},
            {"id": "3", "raw_address": "???"}]
    out = client.post("/api/v1/parse/batch", json=body).json()
    assert [r["id"] for r in out] == ["1", "2", "3"] and set(out[0]) == set(out[2])      # the jury schema is untouched

    runs = client.get("/api/v1/runs").json()
    assert runs and runs[0]["records"] == 3 and runs[0]["source"] == "batch"
    run = client.get(f"/api/v1/runs/{runs[0]['id']}").json()
    assert len(run["items"]) == 3
    assert run["items"][0]["geo"]["precision"] in ("house", "house_near", "street")
    assert run["items"][2]["geo"]["precision"] == "none" and run["items"][2]["geo"]["in_database"] is False

    stats = client.get("/api/v1/runs/stats").json()
    assert stats["runs"] >= 1 and stats["records"] >= 3 and "none" in stats["by_precision"]
    dl = client.get(f"/api/v1/runs/{runs[0]['id']}/download")
    assert dl.status_code == 200 and dl.json()[1]["house"] == "59"
    pts = client.get(f"/api/v1/runs/{runs[0]['id']}/points").json()
    assert all("lat" in p and "precision" in p for p in pts) and len(pts) >= 1
    assert (tmp_path / "archive.sqlite").exists()
    assert client.get("/api/v1/runs/999999").status_code == 404


def test_archive_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("JANA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JANA_ARCHIVE", "off")
    from jana_address import archive
    monkeypatch.setattr(archive, "_ARCHIVE", None)
    assert archive.get_archive() is None
    assert not (tmp_path / "archive.sqlite").exists()
