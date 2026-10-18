"""Human-in-the-loop conflict queue: local parser vs an external AI labeler.

A record lands in the queue when the two disagree on a compared field, when our own confidence is low, or when the
AI's values break the copy-not-rewrite rules (training/validate_labels). An operator then keeps ours, takes the AI's,
picks per field or types a value; the decision is written to corrections.jsonl — training data only, never consulted
at inference (the brief bans answering from stored exact matches).

Storage is a small SQLite file in JANA_DATA_DIR (stdlib only), so listing and resolving also work in the offline jury
image; only *scanning* needs the repository's eval/ + training/ tooling and a provider (see main.py /conflicts/scan).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
STATUSES = ("pending", "resolved", "dismissed")
CHOICES = ("ours", "ai", "custom", "dismiss")
DEFAULT_MIN_CONFIDENCE = 0.7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conflicts (
  conflict_id   TEXT PRIMARY KEY,
  record_id     TEXT NOT NULL,
  raw_address   TEXT NOT NULL,
  ours          TEXT NOT NULL,          -- JSON: our string-derived fields
  ai            TEXT NOT NULL,          -- JSON: the AI labeler's fields (null-filled on an AI error)
  disagreements TEXT NOT NULL,          -- JSON list of field names
  issues        TEXT NOT NULL,          -- JSON list: validator / provider issues of the AI answer
  reasons       TEXT NOT NULL,          -- JSON list: disagreement | low_confidence | ai_issue | ai_error
  confidence    REAL,                   -- our confidence for the record
  provider      TEXT,
  model         TEXT,
  status        TEXT NOT NULL DEFAULT 'pending',
  resolution    TEXT,                   -- JSON: {choice, picks, fields(final), reviewer, note}
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  resolved_at   REAL
);
CREATE INDEX IF NOT EXISTS conflicts_status ON conflicts(status, created_at);
"""


def conflict_id(record_id: str, raw_address: str) -> str:
    """Stable id: the same record re-scanned updates its queue entry instead of duplicating it."""
    return hashlib.sha1(f"{record_id}\x1f{raw_address}".encode("utf-8")).hexdigest()[:16]


def classify(row: dict, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> Optional[dict]:
    """One eval.llm_verify row → a queue item, or None when the record needs no human.

    The row carries: id, raw_address, ours, llm, disagreements, issues and (from llm_verify.run) confidence."""
    issues = list(row.get("issues") or [])
    ai_error = any(str(i).startswith("llm_error") for i in issues)
    disagreements = [] if ai_error else list(row.get("disagreements") or [])   # nothing to compare against
    confidence = row.get("confidence")
    reasons = []
    if disagreements:
        reasons.append("disagreement")
    if confidence is not None and confidence < min_confidence:
        reasons.append("low_confidence")
    if issues and not ai_error:
        reasons.append("ai_issue")
    if not reasons:
        return None
    if ai_error:
        reasons.append("ai_error")
    return {
        "conflict_id": conflict_id(str(row["id"]), row["raw_address"]),
        "record_id": str(row["id"]),
        "raw_address": row["raw_address"],
        "ours": {f: (row.get("ours") or {}).get(f) for f in FIELDS},
        "ai": {f: (row.get("llm") or {}).get(f) for f in FIELDS},
        "disagreements": disagreements,
        "issues": issues,
        "reasons": reasons,
        "confidence": confidence,
    }


def final_fields(item: dict, choice: str, picks: dict[str, str] | None = None,
                 overrides: dict[str, Optional[str]] | None = None) -> dict[str, Optional[str]]:
    """The operator's decision → the 11 jury fields.

    ours   — our values as they are (they are exact copies from the string);
    ai     — ours, with every disagreeing field taken from the AI (agreeing fields keep our exact copy);
    custom — ours, then per-field `picks` (ours | ai), then typed `overrides`.
    `picks` and `overrides` are applied on top of any choice."""
    ours, ai = item["ours"], item["ai"]
    out = dict(ours)
    if choice == "ai":
        for f in item["disagreements"]:
            out[f] = ai.get(f)
    for f, side in (picks or {}).items():
        out[f] = ai.get(f) if side == "ai" else ours.get(f)
    for f, v in (overrides or {}).items():
        v = None if v is None else " ".join(str(v).split())
        out[f] = v or None
    return {f: out.get(f) for f in FIELDS}


class ConflictStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per operation: commit on success, always close (no lingering file handles)."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("ours", "ai", "disagreements", "issues", "reasons", "resolution"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def upsert(self, items: Iterable[dict], provider: str, model: str, requeue: bool = False) -> dict:
        """Insert new items; refresh pending ones; leave decided ones alone unless `requeue`."""
        stats = {"queued": 0, "updated": 0, "kept_decided": 0}
        now = time.time()
        with self._db() as db:
            for it in items:
                cur = db.execute("SELECT status FROM conflicts WHERE conflict_id = ?", (it["conflict_id"],)).fetchone()
                payload = (json.dumps(it["ours"], ensure_ascii=False), json.dumps(it["ai"], ensure_ascii=False),
                           json.dumps(it["disagreements"]), json.dumps(it["issues"], ensure_ascii=False),
                           json.dumps(it["reasons"]), it["confidence"], provider, model)
                if cur is None:
                    db.execute("INSERT INTO conflicts (conflict_id, record_id, raw_address, ours, ai, disagreements, issues, "
                               "reasons, confidence, provider, model, status, created_at, updated_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                               (it["conflict_id"], it["record_id"], it["raw_address"], *payload, now, now))
                    stats["queued"] += 1
                elif cur["status"] == "pending" or requeue:
                    db.execute("UPDATE conflicts SET ours=?, ai=?, disagreements=?, issues=?, reasons=?, confidence=?, "
                               "provider=?, model=?, status='pending', resolution=NULL, resolved_at=NULL, updated_at=? "
                               "WHERE conflict_id = ?", (*payload, now, it["conflict_id"]))
                    stats["updated"] += 1
                else:
                    stats["kept_decided"] += 1
        return stats

    def counts(self) -> dict:
        with self._db() as db:
            got = dict(db.execute("SELECT status, COUNT(*) FROM conflicts GROUP BY status").fetchall())
        return {s: got.get(s, 0) for s in STATUSES}

    def list(self, status: str = "pending", limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        where, args = ("", ()) if status == "all" else ("WHERE status = ?", (status,))
        order = "created_at ASC" if status == "pending" else "COALESCE(resolved_at, updated_at) DESC"
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM conflicts {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM conflicts {where} ORDER BY {order} LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [self._row(r) for r in rows], total

    def get(self, cid: str) -> Optional[dict]:
        with self._db() as db:
            r = db.execute("SELECT * FROM conflicts WHERE conflict_id = ?", (cid,)).fetchone()
        return self._row(r) if r else None

    def decide(self, cid: str, choice: str, picks: dict | None = None, overrides: dict | None = None,
               reviewer: str | None = None, note: str | None = None) -> Optional[dict]:
        """Record the operator's decision. Returns the updated item (with resolution.fields) or None if unknown."""
        item = self.get(cid)
        if item is None:
            return None
        status = "dismissed" if choice == "dismiss" else "resolved"
        resolution = {"choice": choice, "picks": picks or {}, "reviewer": reviewer, "note": note,
                      "fields": None if choice == "dismiss" else final_fields(item, choice, picks, overrides)}
        now = time.time()
        with self._db() as db:
            db.execute("UPDATE conflicts SET status=?, resolution=?, resolved_at=?, updated_at=? WHERE conflict_id=?",
                       (status, json.dumps(resolution, ensure_ascii=False), now, now, cid))
        return self.get(cid)
