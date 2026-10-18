"""LLM pre-labeling of train.json → labels_llm.jsonl (then humans verify in the UI).

Usage:
    export ANTHROPIC_API_KEY=...
    python -m training.label_llm --input data/train.json --output data/labels_llm.jsonl \
        --model claude-opus-5-5 --batch-size 20 [--limit 200] [--resume]

    # no API key? dry run with the rule parser as the "labeler" (tests the pipeline end-to-end):
    python -m training.label_llm --input data/train.json --output data/labels_rules.jsonl --provider rules

Each output line:
    {"id", "raw_address", "fields": {...11 fields...}, "spans": {"street_name": [s, e], ...},
     "rules_fields": {...}, "disagreements": ["house", ...], "issues": [...], "needs_review": bool,
     "labeler": "claude-opus-5-5", "notes": "..."}

Review priority (what the UI sorts by): issues with severity=error → disagreements with the rule
parser → warnings → the rest (sample 10% for QA).

Data policy: the brief allows LLMs for preparing our base, but says issued data must not leave the
hackathon. Ask the organizers before sending train strings to an external API; the fully compliant
alternative is a local model (Qwen2.5-32B-Instruct via vLLM on the university GPU) behind the same
OpenAI-compatible interface — see `--provider openai-compatible`.

Model IDs / pricing: https://platform.claude.com/docs/en/models/overview  (Opus 5.5: $4/$20 per MTok, the default;
pass --model to choose another). Effort: env JANA_LLM_EFFORT (default medium). Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.service import parse_one  # noqa: E402
from training.validate_labels import FIELDS, resolve_spans, validate_record  # noqa: E402

PROMPT_PATH = ROOT / "training" / "prompts" / "label_system.md"
EXAMPLES_PATH = ROOT / "data" / "conventions_examples.json"

FIELD_SCHEMA = {"anyOf": [{"type": "string"}, {"type": "null"}]}
RECORD_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, **{f: FIELD_SCHEMA for f in FIELDS}, "notes": FIELD_SCHEMA},
    "required": ["id", *FIELDS, "notes"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": RECORD_SCHEMA}},
    "required": ["records"],
    "additionalProperties": False,
}


def build_system_prompt(n_examples: int = 36) -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8")
    ex = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))["examples"][:n_examples]
    shots = [{"input": e["raw_address"], "output": {"id": e["id"], **e["expected"], "notes": e.get("open_question")}} for e in ex]
    return base + "\n\n## Labeled examples (follow these conventions exactly)\n" + json.dumps(shots, ensure_ascii=False, indent=1)


def user_message(batch: list[dict]) -> str:
    return "Label these records. Return {\"records\": [...]} in the same order.\n" + json.dumps(
        [{"id": r["id"], "raw_address": r["raw_address"]} for r in batch], ensure_ascii=False, indent=1)


# ----------------------------------------------------------------------------- providers
DEFAULT_MODEL = "claude-opus-5-5"
EFFORT = os.environ.get("JANA_LLM_EFFORT", "medium")      # Opus 5.5 defaults to medium; set explicitly, «high» for hard sets


class LLMRefusal(RuntimeError):
    """The model (and its server-side fallback) declined the batch; the caller marks those records for review."""


def label_with_anthropic(batch: list[dict], system: str, model: str, usage: dict) -> list[dict]:
    """Claude API, structured JSON output. The SDK retries 429 / 5xx / connection errors itself (max_retries);
    a 400 is a bug in the request and is raised immediately. Large stable system prompt → prompt caching."""
    import anthropic  # lazy import: not needed for --provider rules

    client = anthropic.Anthropic(max_retries=6)
    resp = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_message(batch)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}, "effort": EFFORT},
        cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    usage["input"] += resp.usage.input_tokens
    usage["output"] += resp.usage.output_tokens
    usage["cache_read"] = usage.get("cache_read", 0) + (resp.usage.cache_read_input_tokens or 0)
    if resp.stop_reason == "refusal":
        category = resp.stop_details.category if resp.stop_details else None
        raise LLMRefusal(f"refusal ({category}) for batch starting {batch[0]['id']}")
    if resp.stop_reason == "max_tokens":
        if len(batch) == 1:
            raise RuntimeError(f"max_tokens on a single record {batch[0]['id']}")
        half = len(batch) // 2                       # output did not fit: split and retry
        return label_with_anthropic(batch[:half], system, model, usage) + label_with_anthropic(batch[half:], system, model, usage)
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)["records"]


def label_with_openai_compatible(batch: list[dict], system: str, model: str, usage: dict) -> list[dict]:
    """Local vLLM / any OpenAI-compatible server. Set OPENAI_BASE_URL (e.g. http://gpu-box:8000/v1) and OPENAI_API_KEY."""
    from openai import OpenAI  # pip install openai

    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL"), api_key=os.environ.get("OPENAI_API_KEY", "none"))
    resp = client.chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_message(batch)}],
        response_format={"type": "json_object"},
    )
    if resp.usage:
        usage["input"] += resp.usage.prompt_tokens
        usage["output"] += resp.usage.completion_tokens
    return json.loads(resp.choices[0].message.content)["records"]


def from_string(provenance: str | None) -> bool:
    """'string', 'string+table', 'string+gazetteer[:osm|:seed]' are labels; pure 'gazetteer:*' fills are not."""
    return bool(provenance) and provenance.startswith("string")


def rules_string_fields(id_: str, raw: str, mode: str = "rules") -> dict:
    """Parser fields that come from the STRING (gazetteer-filled fields are not labels)."""
    p = parse_one(id_, raw, explain=True, mode=mode)
    return {f: (p[f] if from_string(p["provenance"].get(f)) else None) for f in FIELDS}


def label_with_rules(batch: list[dict], system: str, model: str, usage: dict) -> list[dict]:
    return [{"id": r["id"], **rules_string_fields(r["id"], r["raw_address"]), "notes": None} for r in batch]


PROVIDERS = {"anthropic": label_with_anthropic, "openai-compatible": label_with_openai_compatible, "rules": label_with_rules}


# ----------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--provider", choices=PROVIDERS, default="anthropic")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="skip ids already present in --output")
    a = ap.parse_args(argv)

    with open(a.input, encoding="utf-8") as f:
        data = json.load(f)
    records = data["records"] if isinstance(data, dict) and "records" in data else data
    records = [{"id": str(r.get("id", i)), "raw_address": r.get("raw_address", "") or ""} for i, r in enumerate(records)]
    if a.limit:
        records = records[: a.limit]
    done: set[str] = set()
    out_path = Path(a.output)
    if a.resume and out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            done = {json.loads(line)["id"] for line in f if line.strip()}
        records = [r for r in records if r["id"] not in done]
    print(f"{len(records)} records to label with {a.provider}:{a.model} (skipping {len(done)})")

    system = build_system_prompt()
    labeler = PROVIDERS[a.provider]
    usage = {"input": 0, "output": 0}
    stats = {"errors": 0, "warnings": 0, "disagree": 0, "review": 0}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fo:
        for b in range(0, len(records), a.batch_size):
            batch = records[b: b + a.batch_size]
            by_id = {r["id"]: r for r in batch}
            try:
                labeled = labeler(batch, system, a.model, usage)
            except Exception as e:  # noqa: BLE001
                print(f"batch {b//a.batch_size} failed: {e}", file=sys.stderr)
                labeled = [{"id": r["id"], **{f: None for f in FIELDS}, "notes": f"labeler failed: {e}"} for r in batch]
            got = {str(l.get("id")): l for l in labeled}
            for r in batch:
                lab = got.get(r["id"]) or {"id": r["id"], **{f: None for f in FIELDS}, "notes": "missing in LLM output"}
                fields = {f: (lab.get(f) if lab.get(f) not in ("", "null") else None) for f in FIELDS}
                issues = validate_record(r["raw_address"], fields)
                spans, span_issues = resolve_spans(r["raw_address"], fields)
                issues += span_issues
                rules_fields = rules_string_fields(r["id"], r["raw_address"])
                disagreements = [f for f in FIELDS if (fields[f] or "").strip().lower() != (rules_fields[f] or "").strip().lower()]
                n_err = sum(1 for i in issues if i["severity"] == "error")
                n_warn = len(issues) - n_err
                needs_review = n_err > 0 or bool(disagreements) or n_warn > 0
                stats["errors"] += n_err
                stats["warnings"] += n_warn
                stats["disagree"] += bool(disagreements)
                stats["review"] += needs_review
                fo.write(json.dumps({
                    "id": r["id"], "raw_address": r["raw_address"], "fields": fields,
                    "spans": {k: list(v) for k, v in spans.items()},
                    "rules_fields": rules_fields, "disagreements": disagreements,
                    "issues": issues, "needs_review": needs_review,
                    "labeler": f"{a.provider}:{a.model}", "notes": lab.get("notes"),
                }, ensure_ascii=False) + "\n")
            fo.flush()
            print(f"  batch {b//a.batch_size + 1}/{(len(records)-1)//a.batch_size + 1} done")
    print(f"tokens: in={usage['input']:,} out={usage['output']:,}")
    print(f"issues: errors={stats['errors']} warnings={stats['warnings']} disagree_with_rules={stats['disagree']} "
          f"needs_review={stats['review']}/{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
