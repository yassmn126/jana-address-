"""FastAPI application.

Jury endpoint:  POST /api/v1/parse/batch   body = [{"id","raw_address"}, ...]  (or {"records":[...]})
                → [RecordOut, ...] in the exact jury schema.
Also:           POST /api/v1/parse/batch/file (multipart upload), POST /api/v1/parse (single),
                ?explain=1 on both parse endpoints (adds spans/tokens/flags/provenance),
                POST /api/v1/corrections, GET /health, GET /version.
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
from .schemas import Correction, ExplainOut, RecordIn, RecordOut
from .service import components, get_parser, parse_batch, parse_one

DATA_DIR = Path(os.environ.get("JANA_DATA_DIR", "/data"))

app = FastAPI(title="Jana Address", version=__version__,
              description="Free-form Kazakhstan address → structured fields. Offline, rules + ML + gazetteer.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_READY = {"ok": False, "started": time.time()}


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


@app.post("/api/parse", response_model=None, include_in_schema=False)   # alias used by the static frontend (app.js)
@app.post("/api/v1/parse/batch", response_model=None,
          responses={200: {"description": "Array of records in the jury schema (ExplainOut with ?explain=1)"}})
async def parse_batch_endpoint(request: Request,
                               explain: bool = Query(False, description="add spans/tokens/flags/provenance"),
                               download: bool = Query(False, description="return as an attachment output.json")):
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
    return _respond(parse_batch(_to_records(payload), explain), explain, download)


@app.post("/api/v1/parse/batch/file", response_model=None)
async def parse_batch_file(file: UploadFile = File(...), explain: bool = Query(False), download: bool = Query(False)):
    """Alias of /api/v1/parse/batch for clients that insist on a named multipart field."""
    try:
        payload = _parse_payload_bytes(await file.read())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid JSON: {e}") from e
    return _respond(parse_batch(_to_records(payload), explain), explain, download)


@app.post("/api/v1/parse", response_model=None)
def parse_single(record: RecordIn, explain: bool = Query(False)):
    return JSONResponse(_serialize([parse_one(record.id, record.raw_address, explain)], explain)[0])


@app.post("/api/v1/corrections")
def add_correction(c: Correction) -> dict:
    """Append a manual correction (training data only; never used for inference)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "corrections.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**c.model_dump(), "ts": time.time()}, ensure_ascii=False) + "\n")
    return {"stored": True, "path": str(path)}


@app.post("/api/v1/verify", response_model=None)
def verify_endpoint(records: list[RecordIn], provider: str = Query("anthropic", description="anthropic | openai-compatible | rules"),
                    model: str = Query("claude-opus-5-5")):
    """AI cross-check (dev / ops tool, never part of parsing): our parse vs an LLM labeler, field-by-field diff.

    Works where the repository's eval/ and training/ are available (`make api-dev`) and a provider is configured
    (ANTHROPIC_API_KEY / `ant auth login` for `anthropic`, OPENAI_BASE_URL for a local vLLM). The offline jury image
    returns 503. At most 50 records per call. Disagreements are for a human to decide (eval/llm_verify.py → review.html)."""
    import sys
    root = Path(__file__).resolve().parents[2]
    if not (root / "eval" / "llm_verify.py").exists():
        raise HTTPException(503, "verification tooling is not part of this image (run from the repository: make api-dev)")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if len(records) > 50:
        raise HTTPException(413, "at most 50 records per verify call (use eval/llm_verify.py for files)")
    try:
        from eval.llm_verify import run
    except ImportError as e:
        raise HTTPException(503, f"verification dependencies missing: {e}") from e
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise HTTPException(503, "pip install anthropic (api/requirements-dev.txt)") from e
    recs = [{"id": r.id, "raw_address": r.raw_address} for r in records]
    try:
        result = run(recs, {}, provider, model, batch_size=20)
    except Exception as e:  # noqa: BLE001 — surface provider / credential errors as 502, never crash the API
        raise HTTPException(502, f"{provider} failed: {type(e).__name__}: {e}") from e
    return {"summary": result["summary"],
            "records": [{k: r[k] for k in ("id", "raw_address", "ours", "llm", "disagreements", "issues")} for r in result["rows"]]}


@app.exception_handler(Exception)
async def _unhandled(_, exc: Exception):
    return JSONResponse(status_code=500, content={"error": type(exc).__name__, "detail": str(exc)})
