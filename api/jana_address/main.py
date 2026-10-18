"""FastAPI application.

Jury endpoint:  POST /api/v1/parse/batch   body = [{"id","raw_address"}, ...]  (or {"records":[...]})
                → [RecordOut, ...] in the exact jury schema.
Also:           POST /api/v1/parse/batch/file (multipart upload), POST /api/v1/parse (single),
                ?explain=1 on both parse endpoints (adds spans/tokens/flags/provenance),
                POST /api/v1/corrections, GET /health, GET /version.
Archive:        every batch is kept in SQLite under JANA_DATA_DIR: GET /api/v1/runs, /runs/stats, /runs/{id},
                /runs/{id}/download, /runs/{id}/points (see archive.py; JANA_ARCHIVE=off disables).
Human-in-the-loop: POST /api/v1/conflicts/scan (local parser vs AI labeler → queue), GET /api/v1/conflicts,
                GET /api/v1/conflicts/{id}, POST /api/v1/conflicts/{id}/resolve, POST /api/v1/conflicts/{id}/approve.
Claude key:     the server's ANTHROPIC_API_KEY, or the caller's own key in the X-Anthropic-Api-Key header
                (+ X-Anthropic-Workspace-Id for sk-ant-usr-… keys): used for that request only, never stored or logged.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Union

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from . import __version__
from .archive import get_archive
from .conflicts import DEFAULT_MIN_CONFIDENCE, ConflictStore, classify
from .schemas import ConflictResolution, Correction, ExplainOut, RecordIn, RecordOut
from .service import components, get_parser, parse_batch, parse_one

DATA_DIR = Path(os.environ.get("JANA_DATA_DIR", "/data"))

app = FastAPI(title="Jana Address", version=__version__,
              description="Free-form Kazakhstan address → structured fields. Offline, rules + ML + gazetteer.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_READY = {"ok": False, "started": time.time()}


def _request_credentials(request: Request) -> tuple[str | None, str | None]:
    """Bring your own key: (api_key, workspace_id) from this request's headers, or (None, None).
    Headers, never query strings (URLs end up in access logs); the values are passed to the SDK and dropped."""
    key = (request.headers.get("x-anthropic-api-key") or "").strip()
    ws = (request.headers.get("x-anthropic-workspace-id") or "").strip()
    if key and not key.isascii():
        raise HTTPException(400, "the Anthropic key must be plain ASCII (stray quotes, spaces or a Cyrillic letter?)")
    return key or None, ws or None


@app.on_event("startup")
def _warmup() -> None:
    get_parser()                      # loads lexicon / seed gazetteer (later: ONNX model + sqlite)
    parse_one("warmup", "город Алматы, мкр Аксай 3а, 59, кв 70")
    _READY["ok"] = True


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if _READY["ok"] else "starting", "uptime_s": round(time.time() - _READY["started"], 1)}


@app.get("/version")
def version() -> dict:
    return {"version": __version__, "components": components()}


def _to_records(payload: Any) -> list[dict]:
    if isinstance(payload, dict) and "records" in payload:
        payload = payload["records"]
    elif isinstance(payload, dict) and "raw_address" in payload:
        payload = [payload]                       # a single record is a batch of one
    if not isinstance(payload, list):
        raise HTTPException(400, "Body must be a JSON array of {id, raw_address} or {\"records\": [...]}")
    recs = []
    for i, r in enumerate(payload):
        if isinstance(r, str):
            r = {"id": str(i), "raw_address": r}  # a plain list of address strings
        if not isinstance(r, dict):
            raise HTTPException(400, f"record #{i} is not an object")
        recs.append({"id": str(r.get("id", i)), "raw_address": r.get("raw_address", "") or ""})
    return recs


def _serialize(rows: list[dict], explain: bool) -> list[dict]:
    """Validate against the right schema so the default response has EXACTLY the jury keys."""
    model = ExplainOut if explain else RecordOut
    return [model(**r).model_dump() for r in rows]


def _parse_payload_bytes(data: bytes) -> Any:
    """JSON array / {"records": [...]} / JSON Lines — whatever the jury's file turns out to be."""
    text = data.decode("utf-8-sig")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows


def _respond(rows: list[dict], explain: bool, download: bool) -> JSONResponse:
    headers = {"Content-Disposition": 'attachment; filename="output.json"'} if download else None
    return JSONResponse(_serialize(rows, explain), headers=headers)


def _parse_and_archive(records: list[dict], explain: bool, source: str, ai: str | None = None,
                       creds: tuple[str | None, str | None] = (None, None)) -> list[dict]:
    """Parse (optionally with the AI path, see llm/), then keep the batch in the run archive (best effort)."""
    ai_client = None
    if creds[0]:                                   # the caller's own Claude key, for this batch only
        from .llm.client import client_for
        ai_client = client_for(*creds)
    t0 = time.perf_counter()
    rows = parse_batch(records, explain, ai=ai, ai_client=ai_client)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    arch = get_archive()
    if arch is not None:
        try:
            from .gazetteer.osm import get_gazetteer
            gz = get_gazetteer()
            geo = [r.get("geo") if explain else (gz.geocode(r) if gz.geo_ready() else None) for r in rows]
            arch.log(source, rows, elapsed_ms, geo, __version__)
        except Exception:  # noqa: BLE001 — never fail a parse because the archive hiccupped
            pass
    return rows


@app.post("/api/parse", response_model=None, include_in_schema=False)   # alias used by the static frontend (app.js)
@app.post("/api/v1/parse/batch", response_model=None,
          responses={200: {"description": "Array of records in the jury schema (ExplainOut with ?explain=1)"}})
async def parse_batch_endpoint(request: Request,
                               explain: bool = Query(False, description="add spans/tokens/flags/provenance"),
                               download: bool = Query(False, description="return as an attachment output.json"),
                               ai: str | None = Query(None, pattern="^(off|foreign|fallback|always)$",
                                                 description="AI path: off | foreign | fallback | always (default env JANA_AI_MODE, else off)")):
    """Batch endpoint for the jury. Accepts BOTH:
      * JSON body: `[{"id","raw_address"}, ...]` or `{"records": [...]}` (also JSON Lines)
      * multipart/form-data with the file in field `file` (any field name is accepted)
    """
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = next((v for v in form.values() if isinstance(v, StarletteUploadFile)), None)
        if upload is None:
            raise HTTPException(400, "multipart request without a file part")
        data = await upload.read()
    else:
        data = await request.body()
    try:
        payload = _parse_payload_bytes(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid JSON: {e}") from e
    return _respond(_parse_and_archive(_to_records(payload), explain, "batch", ai, _request_credentials(request)),
                    explain, download)


@app.post("/api/v1/parse/batch/file", response_model=None)
async def parse_batch_file(request: Request, file: UploadFile = File(...), explain: bool = Query(False),
                           download: bool = Query(False),
                           ai: str | None = Query(None, pattern="^(off|foreign|fallback|always)$")):
    """Alias of /api/v1/parse/batch for clients that insist on a named multipart field."""
    try:
        payload = _parse_payload_bytes(await file.read())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid JSON: {e}") from e
    return _respond(_parse_and_archive(_to_records(payload), explain, f"file:{file.filename or ''}", ai,
                                       _request_credentials(request)), explain, download)


@app.post("/api/v1/parse", response_model=None)
def parse_single(request: Request, record: RecordIn, explain: bool = Query(False),
                 ai: str | None = Query(None, pattern="^(off|foreign|fallback|always)$")):
    rows = _parse_and_archive([{"id": record.id, "raw_address": record.raw_address}], explain, "single", ai,
                              _request_credentials(request))
    return JSONResponse(_serialize(rows, explain)[0])


@app.get("/api/v1/ai/status")
def ai_status(request: Request) -> dict:
    """Runtime AI path: availability, model, effort, prompt version, tokens and USD spent since start.
    `credentials`: "server" (env / auth profile), "request" (a key in this request's header) or "none"."""
    from .llm.client import client_for
    return client_for(*_request_credentials(request)).status()


# ------------------------------------------------------------------ run archive
@app.get("/api/v1/runs")
def runs_list(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    """Recent batches: when, how many records, timing, share of addresses found in our database."""
    arch = get_archive()
    return arch.runs(limit) if arch else []


@app.get("/api/v1/runs/stats")
def runs_stats() -> dict:
    arch = get_archive()
    return arch.stats() if arch else {"runs": 0, "records": 0, "disabled": True}


@app.get("/api/v1/runs/{run_id}")
def runs_get(run_id: int) -> dict:
    arch = get_archive()
    run = arch.run(run_id) if arch else None
    if run is None:
        raise HTTPException(404, "no such run")
    return run


@app.get("/api/v1/runs/{run_id}/download")
def runs_download(run_id: int):
    arch = get_archive()
    if arch is None or arch.run(run_id) is None:
        raise HTTPException(404, "no such run")
    return JSONResponse(arch.records(run_id), headers={"Content-Disposition": f'attachment; filename="run_{run_id}.json"'})


@app.get("/api/v1/runs/{run_id}/points")
def runs_points(run_id: int) -> list[dict]:
    """Map points of a run: {id, lat, lon, precision, in_database}."""
    arch = get_archive()
    if arch is None or arch.run(run_id) is None:
        raise HTTPException(404, "no such run")
    return arch.points(run_id)


@app.post("/api/v1/corrections")
def add_correction(c: Correction) -> dict:
    """Append a manual correction (training data only; never used for inference)."""
    return {"stored": True, "path": str(_store_correction(c.model_dump()))}


@app.get("/api/v1/suggest")
def suggest(q: str = Query(..., min_length=2, description="what the user has typed so far"),
            city: str | None = Query(None, description="restrict to streets / микрорайоны of this settlement"),
            limit: int = Query(10, ge=1, le=50)) -> list[dict]:
    """Autocomplete from the OSM gazetteer: settlements (no city) or streets + микрорайоны/ЖК of a city."""
    from .gazetteer.osm import get_gazetteer
    return get_gazetteer().suggest(q, city, limit)


@app.post("/api/v1/geocode", response_model=None)
def geocode(record: RecordIn) -> dict:
    """Parse one address and return its point: {lat, lon, precision} (house → street → microdistrict → district → locality)."""
    out = parse_one(record.id, record.raw_address, explain=True)
    return {"id": record.id, "raw_address": record.raw_address,
            "fields": {k: out[k] for k in RecordOut.model_fields if k not in ("id", "raw_address")}, "geo": out.get("geo")}


def _crosscheck(records: list[RecordIn], provider: str, model: str,
                creds: tuple[str | None, str | None] = (None, None)) -> dict:
    """Run eval.llm_verify (our parse vs an LLM labeler) on ≤ 50 records; shared by /verify and /conflicts/scan.

    Needs the repository's eval/ and training/ (`make api-dev`) and a configured provider (ANTHROPIC_API_KEY /
    `ant auth login` for `anthropic`, OPENAI_BASE_URL for a local vLLM). The offline jury image answers 503."""
    import sys
    root = Path(__file__).resolve().parents[2]
    if not (root / "eval" / "llm_verify.py").exists():
        raise HTTPException(503, "verification tooling is not part of this image (run from the repository: make api-dev)")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if len(records) > 50:
        raise HTTPException(413, "at most 50 records per call (use eval/llm_verify.py --queue for files)")
    try:
        from eval.llm_verify import PROVIDERS, run
    except ImportError as e:
        raise HTTPException(503, f"verification dependencies missing: {e}") from e
    if provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider {provider!r}; one of {sorted(PROVIDERS)}")
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise HTTPException(503, "pip install anthropic (api/requirements-dev.txt)") from e
        if not (creds[0] or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("JANA_AI_ASSUME_CREDENTIALS") == "1"):
            raise HTTPException(401, "нет ключа Claude: вставьте свой ключ в поле «Ключ Anthropic» (он уходит только с "
                                     "этим запросом и не сохраняется) или задайте ANTHROPIC_API_KEY на сервере")
    recs = [{"id": r.id, "raw_address": r.raw_address} for r in records]
    try:
        return run(recs, {}, provider, model, batch_size=20, credentials=creds if provider == "anthropic" else None)
    except Exception as e:  # noqa: BLE001 — credential errors as 401, provider errors as 502, never crash the API
        name, msg = type(e).__name__, str(getattr(e, "message", e))
        if name in ("AuthenticationError", "PermissionDeniedError"):
            raise HTTPException(401, f"Claude отклонил ключ ({name}): проверьте его в Anthropic Console") from e
        if name == "BadRequestError" and "workspace" in msg:
            raise HTTPException(401, "ключ привязан к пользователю, а не к workspace: укажите Workspace ID (wrkspc_…)") from e
        raise HTTPException(502, f"{provider} failed: {name}: {msg[:300]}") from e


@app.post("/api/v1/verify", response_model=None)
def verify_endpoint(request: Request, records: list[RecordIn],
                    provider: str = Query("anthropic", description="anthropic | openai-compatible | rules"),
                    model: str = Query("claude-fable-5-1", description="claude-fable-5-1 (strongest) | claude-opus-5-5 | claude-haiku-5-5")):
    """AI cross-check (dev / ops tool, never part of parsing): our parse vs an LLM labeler, field-by-field diff.
    At most 50 records per call. To keep the disagreements for a human, use /api/v1/conflicts/scan instead."""
    result = _crosscheck(records, provider, model, _request_credentials(request))
    return {"summary": result["summary"],
            "records": [{k: r[k] for k in ("id", "raw_address", "ours", "llm", "disagreements", "issues")} for r in result["rows"]]}


# ----------------------------------------------------------------------------- human-in-the-loop conflict queue
def _conflicts() -> ConflictStore:
    return ConflictStore(DATA_DIR / "conflicts.sqlite3")


def _store_correction(record: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "corrections.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**record, "ts": time.time()}, ensure_ascii=False) + "\n")
    return path


@app.post("/api/v1/conflicts/scan", response_model=None)
def conflicts_scan(request: Request, records: list[RecordIn],
                   provider: str = Query("anthropic", description="anthropic | openai-compatible | rules"),
                   model: str = Query("claude-fable-5-1", description="claude-fable-5-1 (strongest) | claude-opus-5-5 | claude-haiku-5-5"),
                   min_confidence: float = Query(DEFAULT_MIN_CONFIDENCE, ge=0.0, le=1.0,
                                                 description="our confidence below this also goes to the queue"),
                   requeue: bool = Query(False, description="re-open records that were already decided")):
    """Compare the local parser with the AI labeler on ≤ 50 records and queue what needs a human:
    a field disagreement, our confidence < min_confidence, or AI values that break the copy-not-rewrite rules."""
    result = _crosscheck(records, provider, model, _request_credentials(request))
    items = [it for it in (classify(r, min_confidence) for r in result["rows"]) if it]
    stats = _conflicts().upsert(items, provider, model, requeue=requeue)
    return {"scanned": len(result["rows"]), "needs_review": len(items), **stats,
            "conflict_ids": [it["conflict_id"] for it in items], "counts": _conflicts().counts(),
            "summary": result["summary"]}


@app.get("/api/v1/conflicts")
def conflicts_list(status: str = Query("pending", pattern="^(pending|resolved|dismissed|all)$"),
                   limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    """The queue, oldest pending first (decided ones: most recent first)."""
    store = _conflicts()
    items, total = store.list(status, limit, offset)
    return {"items": items, "total": total, "counts": store.counts()}


@app.get("/api/v1/conflicts/{cid}")
def conflicts_get(cid: str) -> dict:
    item = _conflicts().get(cid)
    if item is None:
        raise HTTPException(404, f"no conflict {cid}")
    return item


@app.post("/api/v1/conflicts/{cid}/resolve")
def conflicts_resolve(cid: str, decision: ConflictResolution) -> dict:
    """Decide a conflict: keep ours / take the AI's / per-field picks + typed values / dismiss.
    Anything but `dismiss` is written to corrections.jsonl (training data only, never used at inference)."""
    store = _conflicts()
    current = store.get(cid)
    if current is None:
        raise HTTPException(404, f"no conflict {cid}")
    if current["status"] != "pending":
        raise HTTPException(409, f"conflict {cid} is already {current['status']}")
    item = store.decide(cid, decision.choice, decision.picks, decision.fields, decision.reviewer, decision.note)
    stored = None
    if decision.choice != "dismiss":
        res = item["resolution"]
        changed = sorted(f for f in res["fields"] if res["fields"][f] != item["ours"].get(f))
        stored = str(_store_correction({
            "id": item["record_id"], "raw_address": item["raw_address"], "fields": res["fields"],
            "reviewer": decision.reviewer or "conflict-queue",
            "note": f"conflict:{cid} choice={decision.choice} vs {item['provider']}:{item['model']}"
                    + (f" changed={','.join(changed)}" if changed else " kept=ours")
                    + (f" · {decision.note}" if decision.note else "")}))
    return {"item": item, "correction_path": stored, "counts": store.counts()}


@app.post("/api/v1/conflicts/{cid}/approve")
def conflicts_approve(cid: str, side: str = Query("ours", pattern="^(ours|ai)$"), reviewer: str | None = Query(None)) -> dict:
    """One-click shortcut: approve our output (side=ours) or the AI's (side=ai) as the final answer."""
    return conflicts_resolve(cid, ConflictResolution(choice=side, reviewer=reviewer))


@app.exception_handler(Exception)
async def _unhandled(_, exc: Exception):
    return JSONResponse(status_code=500, content={"error": type(exc).__name__, "detail": str(exc)})
