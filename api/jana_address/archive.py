"""Run archive — every batch the service parses is kept, offline, in SQLite under JANA_DATA_DIR (the `/data` volume).

Why: the jury runs the container with no network, so there is no Supabase / Postgres to send results to. A file-based
database inside the volume gives the same thing for free: a history of every file that went through the service
(inputs, outputs, confidence, timing), a per-record flag whether the address exists in our OSM database (geocoding
precision: house → street → microdistrict → district → locality → none) and the points for a map.

    GET  /api/v1/runs                 recent runs (id, time, source, size, timing, share found in the database)
    GET  /api/v1/runs/stats           totals over the whole archive
    GET  /api/v1/runs/{id}            one run with its records
    GET  /api/v1/runs/{id}/download   the records as output.json (jury schema)
    GET  /api/v1/runs/{id}/points     {id, lat, lon, precision} per record — for the map in the UI

JANA_ARCHIVE=off disables it. Writing 1 000 records costs ~15 ms (one transaction); geocoding them for the flag is
the expensive part (~1 ms each) and runs only here, never on the response path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
IN_DATABASE = ("house", "house_near")           # the address resolves to a building in our OSM database


class Archive:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.con = sqlite3.connect(str(self.path), check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, source TEXT, n INTEGER NOT NULL,
                elapsed_ms REAL, mean_conf REAL, in_db INTEGER, by_precision TEXT, version TEXT);
            CREATE TABLE IF NOT EXISTS records(run_id INTEGER NOT NULL, id TEXT, raw_address TEXT, fields TEXT NOT NULL,
                confidence REAL, precision TEXT, lat REAL, lon REAL);
            CREATE INDEX IF NOT EXISTS ix_rec_run ON records(run_id);
        """)

    # ------------------------------------------------------------------ write
    def log(self, source: str, rows: list[dict], elapsed_ms: float, geo: list[dict | None], version: str = "") -> int:
        """Store one batch. `geo[i]` is the geocoder's answer for rows[i] (or None)."""
        by: dict[str, int] = {}
        recs = []
        for r, g in zip(rows, geo):
            prec = (g or {}).get("precision") or "none"
            by[prec] = by.get(prec, 0) + 1
            recs.append((r.get("id"), r.get("raw_address"), json.dumps({f: r.get(f) for f in FIELDS}, ensure_ascii=False),
                         r.get("confidence"), prec, (g or {}).get("lat"), (g or {}).get("lon")))
        conf = [r.get("confidence") for r in rows if isinstance(r.get("confidence"), (int, float))]
        in_db = sum(by.get(p, 0) for p in IN_DATABASE)
        with self._lock, self.con:
            cur = self.con.execute(
                "INSERT INTO runs(ts, source, n, elapsed_ms, mean_conf, in_db, by_precision, version) VALUES(?,?,?,?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"), source, len(rows), round(elapsed_ms, 1),
                 round(sum(conf) / len(conf), 4) if conf else None, in_db, json.dumps(by), version))
            run_id = int(cur.lastrowid)
            self.con.executemany("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)", [(run_id, *rec) for rec in recs])
        return run_id

    # ------------------------------------------------------------------ read
    @staticmethod
    def _run_row(row) -> dict:
        rid, ts, source, n, ms, conf, in_db, by, version = row
        return {"id": rid, "time": ts, "source": source, "records": n, "elapsed_ms": ms, "mean_confidence": conf,
                "in_database": in_db, "in_database_share": round(in_db / n, 4) if n else None,
                "by_precision": json.loads(by or "{}"), "version": version}

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._run_row(r) for r in rows]

    def run(self, run_id: int) -> dict | None:
        row = self.con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        out = self._run_row(row)
        out["items"] = [{"id": i, "raw_address": raw, **json.loads(f), "confidence": c,
                         "geo": {"precision": p, "lat": lat, "lon": lon, "in_database": p in IN_DATABASE}}
                        for i, raw, f, c, p, lat, lon in self.con.execute(
                            "SELECT id, raw_address, fields, confidence, precision, lat, lon FROM records WHERE run_id = ? ORDER BY rowid",
                            (run_id,))]
        return out

    def records(self, run_id: int) -> list[dict]:
        """The run's records in the jury schema (for download)."""
        return [{"id": i, "raw_address": raw, **json.loads(f), "confidence": c}
                for i, raw, f, c in self.con.execute(
                    "SELECT id, raw_address, fields, confidence FROM records WHERE run_id = ? ORDER BY rowid", (run_id,))]

    def points(self, run_id: int) -> list[dict]:
        return [{"id": i, "raw_address": raw, "lat": lat, "lon": lon, "precision": p, "in_database": p in IN_DATABASE}
                for i, raw, p, lat, lon in self.con.execute(
                    "SELECT id, raw_address, precision, lat, lon FROM records WHERE run_id = ? AND lat IS NOT NULL ORDER BY rowid",
                    (run_id,))]

    def stats(self) -> dict:
        runs, recs, in_db = self.con.execute("SELECT COUNT(*), COALESCE(SUM(n), 0), COALESCE(SUM(in_db), 0) FROM runs").fetchone()
        by = {p: c for p, c in self.con.execute("SELECT precision, COUNT(*) FROM records GROUP BY precision")}
        conf = self.con.execute("SELECT AVG(confidence) FROM records").fetchone()[0]
        return {"runs": runs, "records": recs, "in_database": in_db, "in_database_share": round(in_db / recs, 4) if recs else None,
                "by_precision": by, "mean_confidence": round(conf, 4) if conf is not None else None,
                "path": str(self.path), "size_bytes": self.path.stat().st_size if self.path.exists() else 0}


_ARCHIVE: Archive | None = None
_DISABLED = object()


def get_archive() -> Archive | None:
    """The process-wide archive, or None when JANA_ARCHIVE=off or the data dir is not writable."""
    global _ARCHIVE
    if _ARCHIVE is _DISABLED:
        return None
    if _ARCHIVE is None:
        if os.environ.get("JANA_ARCHIVE", "on").lower() in ("off", "0", "false"):
            _ARCHIVE = _DISABLED
            return None
        try:
            _ARCHIVE = Archive(Path(os.environ.get("JANA_DATA_DIR", "/data")) / "archive.sqlite")
        except Exception:  # noqa: BLE001 — a read-only /data must not take the service down
            _ARCHIVE = _DISABLED
            return None
    return _ARCHIVE
