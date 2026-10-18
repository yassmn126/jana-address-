"""System prompt, output schema and user message of the runtime AI path.

The system prompt is static (instructions + 42 convention examples + 16 runtime examples), so it is sent with an
explicit cache breakpoint: every request after the first one within 5 minutes reads it from the prompt cache
(0.05–0.1x of the input price) instead of paying for it again. Only the per-batch user message is new each time.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]

_NULLABLE = {"anyOf": [{"type": "string"}, {"type": "null"}]}
RECORD_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "country": _NULLABLE, **{f: _NULLABLE for f in FIELDS}, "notes": _NULLABLE},
    "required": ["id", "country", *FIELDS, "notes"],
    "additionalProperties": False,
}
# structured outputs (output_config.format): the answer is constrained to this schema — always parseable JSON with
# exactly these keys. 13 nullable unions (limit 16), 0 optional parameters (limit 24).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": RECORD_SCHEMA}},
    "required": ["records"],
    "additionalProperties": False,
}


def _example(raw: str, expected: dict) -> str:
    shown = {k: v for k, v in expected.items() if v is not None}
    return (f"<example>\n<raw_address>{raw}</raw_address>\n"
            f"<fields>{json.dumps(shown, ensure_ascii=False)}</fields>\n</example>")


@lru_cache(maxsize=1)
def system_prompt() -> str:
    base = (PROMPTS / "runtime_system.md").read_text(encoding="utf-8").strip()
    conv = json.loads((PROMPTS / "conventions_examples.json").read_text(encoding="utf-8"))["examples"]
    extra = json.loads((PROMPTS / "runtime_examples.json").read_text(encoding="utf-8"))["examples"]
    blocks = [_example(e["raw_address"], {"country": "KZ", **e["expected"]}) for e in conv]
    blocks += [_example(e["raw_address"], e["expected"]) for e in extra]
    return (base + "\n\n<examples>\nEvery example lists the non-null fields of the answer; all other fields are null.\n"
            + "\n".join(blocks) + "\n</examples>\n")


@lru_cache(maxsize=1)
def prompt_version() -> str:
    """Changes whenever the prompt, the examples or the schema change (shown in /api/v1/ai/status and eval reports)."""
    blob = system_prompt() + json.dumps(OUTPUT_SCHEMA, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def user_message(items: list[dict]) -> str:
    """items: [{"id", "raw_address", "draft"?}] — the draft holds only the non-null string-derived local fields."""
    payload = []
    for it in items:
        row = {"id": it["id"], "raw_address": it["raw_address"]}
        if it.get("draft"):
            row["draft"] = it["draft"]
        payload.append(row)
    return ("Parse these addresses. Return {\"records\": [...]}: one object per item, in the same order, with the same id.\n"
            "<records>\n" + json.dumps(payload, ensure_ascii=False) + "\n</records>")


def approx_tokens(text: str) -> int:
    """Rough count for budgeting only (Cyrillic ≈ 2.6 chars/token, Latin ≈ 3.6 on the Claude 4.7+ tokenizer).
    The real number comes back in `usage` and is what /api/v1/ai/status reports."""
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    return int(cyr / 2.6 + (len(text) - cyr) / 3.6) + 1
