"""Measure the runtime AI path on a gold file: score (jury metric) local vs local+AI, tokens, USD, latency.

    # what would it cost? (no API call, no key needed)
    python -m eval.ai_eval --gold data/hard3k/hard3k_s2026_gold.json --mode always --dry-run

    # real run (needs ANTHROPIC_API_KEY); --limit keeps the bill small
    python -m eval.ai_eval --gold data/hard3k/hard3k_s2026_gold.json --mode fallback --model claude-haiku-5-5 --limit 600
    python -m eval.ai_eval --gold data/hard3k/hard3k_s2026_gold.json --mode always --model claude-sonnet-5-5 --limit 200

    # Message Batches API: 50 % cheaper, asynchronous (minutes, up to 24 h) — for nightly re-checks, not for the UI
    python -m eval.ai_eval --gold ... --mode always --batch-api --limit 1000

Writes runs/ai_eval/<model>_<mode>/{summary.json, report.md, pred_local.json, pred_ai.json, errors_ai.md}.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.metric import ADDRESS, DETAILS, REFS, align, errors, score  # noqa: E402
from jana_address.llm.client import AIClient, AIConfig, max_tokens_for, request_params  # noqa: E402
from jana_address.llm.policy import _draft, merge, route  # noqa: E402
from jana_address.llm.pricing import cost_usd  # noqa: E402
from jana_address.llm.prompt import approx_tokens, prompt_version, system_prompt, user_message  # noqa: E402
from jana_address.service import parse_one  # noqa: E402

JURY = ["id", "raw_address", *ADDRESS, *DETAILS, *REFS, "street_type", "landmark", "confidence"]
OUT_TOKENS_PER_RECORD = 140          # measured: Opus 5.5 at medium effort wrote 143.5 output tokens per record (416 rows)


def _strip(row: dict) -> dict:
    return {k: row.get(k) for k in JURY}


def estimate(items: list[dict], cfg: AIConfig, out_per_record: int = OUT_TOKENS_PER_RECORD) -> dict:
    """Token and USD estimate for sending `items` with the current prompt (no API call)."""
    sys_tok = approx_tokens(system_prompt())
    chunks = [items[i:i + cfg.batch_size] for i in range(0, len(items), cfg.batch_size)]
    user_tok = sum(approx_tokens(user_message(c)) for c in chunks)
    out_tok = out_per_record * len(items)
    res = {"records": len(items), "requests": len(chunks), "system_prompt_tokens_est": sys_tok,
           "user_tokens_est": user_tok, "output_tokens_est": out_tok, "usd": {}}
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
        # first request writes the cache, the rest read it (sequential within 5 min); batch API halves everything
        sync = cost_usd(model, input_tokens=user_tok, output_tokens=out_tok, cache_write_tokens=sys_tok,
                        cache_read_tokens=sys_tok * max(0, len(chunks) - 1), aggregate=True)
        res["usd"][model] = {"sync": round(sync, 4), "batch_api": round(sync / 2, 4),
                             "per_1k_records_sync": round(1000 * sync / max(1, len(items)), 4)}
    return res


def batch_api_parse(items: list[dict], cfg: AIConfig, poll_s: float = 20.0) -> tuple[dict, dict, dict]:
    """Message Batches API: one batch, one request per chunk of cfg.batch_size records. 50 % off; cache hits are
    best-effort (requests run in any order)."""
    import anthropic
    client = anthropic.Anthropic()
    chunks = [items[i:i + cfg.batch_size] for i in range(0, len(items), cfg.batch_size)]
    reqs = []
    for k, chunk in enumerate(chunks):
        params = {"model": cfg.model, "max_tokens": max_tokens_for(len(chunk)),
                  "system": [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
                  "messages": [{"role": "user", "content": user_message(chunk)}]}
        extra = request_params(cfg.model, cfg.effort)
        params["output_config"] = extra["output_config"]
        if "extra_body" in extra:
            params.update(extra["extra_body"])
        reqs.append({"custom_id": f"chunk-{k}", "params": params})
    batch = client.messages.batches.create(requests=reqs)
    print(f"batch {batch.id}: {len(reqs)} requests submitted")
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        print(f"  {b.request_counts}")
        time.sleep(poll_s)
    results, errs = {}, {}
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}
    by_chunk = {f"chunk-{k}": c for k, c in enumerate(chunks)}
    for r in client.messages.batches.results(batch.id):
        chunk = by_chunk[r.custom_id]
        if r.result.type != "succeeded":
            errs.update({it["id"]: f"batch result {r.result.type}" for it in chunk})
            continue
        msg = r.result.message
        u = msg.usage
        usage["input_tokens"] += u.input_tokens
        usage["output_tokens"] += u.output_tokens
        usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
        try:
            text = next(bl.text for bl in msg.content if bl.type == "text")
            for rec in json.loads(text)["records"]:
                results[str(rec.get("id"))] = rec
        except Exception as e:  # noqa: BLE001
            errs.update({it["id"]: f"unparseable: {e}" for it in chunk})
    return results, errs, usage


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--mode", default="fallback", choices=["foreign", "fallback", "always"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--limit", type=int, default=0, help="random sample of N records (seed 7); 0 = all")
    ap.add_argument("--out", default="runs/ai_eval")
    ap.add_argument("--dry-run", action="store_true", help="estimate tokens and USD only")
    ap.add_argument("--batch-api", action="store_true")
    a = ap.parse_args(argv)

    gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    gold = gold["records"] if isinstance(gold, dict) else gold
    if a.limit and a.limit < len(gold):
        gold = random.Random(7).sample(gold, a.limit)
    cfg = AIConfig()
    if a.model:
        cfg.model = a.model
    if a.effort:
        cfg.effort = a.effort

    t0 = time.perf_counter()
    local = [parse_one(str(g["id"]), g["raw_address"], explain=True) for g in gold]
    t_local = time.perf_counter() - t0
    idx = route(local, a.mode, cfg)
    items = [{"id": f"r{i}", "raw_address": local[i]["raw_address"], **({"draft": _draft(local[i])} if cfg.send_draft else {})}
             for i in idx]
    est = estimate(items, cfg)
    print(f"{len(gold)} records, {len(idx)} routed to the AI ({a.mode}); prompt {prompt_version()}, "
          f"~{est['system_prompt_tokens_est']} system tokens")
    if a.dry_run:
        print(json.dumps(est, ensure_ascii=False, indent=1))
        return 0

    t1 = time.perf_counter()
    if a.batch_api:
        got, errs, usage = batch_api_parse(items, cfg)
        usd = cost_usd(cfg.model, usage["input_tokens"], usage["output_tokens"], usage["cache_write_tokens"],
                       usage["cache_read_tokens"], batch=True, aggregate=True)
        stats = {**usage, "cost_usd": round(usd or 0, 6), "requests": -(-len(items) // cfg.batch_size), "failures": len(errs)}
    else:
        client = AIClient(cfg)
        ok, why = client.available()
        if not ok:
            print(f"AI unavailable: {why}")
            return 2
        got, errs = client.parse(items)
        stats = client.stats.as_dict()
    t_ai = time.perf_counter() - t1
    merged = [dict(r) for r in local]
    for i, it in zip(idx, items):
        if it["id"] in got:
            merged[i] = merge(merged[i], got[it["id"]], cfg)
    p_local, g = align([_strip(r) | {"id": gg["id"]} for r, gg in zip(local, gold)], gold)
    p_ai, _ = align([_strip(r) | {"id": gg["id"]} for r, gg in zip(merged, gold)], gold)
    s_local, s_ai = score(p_local, g), score(p_ai, g)
    routed_gold = [gold[i] for i in idx]
    s_routed_local = score([_strip(local[i]) for i in idx], routed_gold) if idx else None
    s_routed_ai = score([_strip(merged[i]) for i in idx], routed_gold) if idx else None

    out = Path(a.out) / f"{cfg.model}_{a.mode}{'_batch' if a.batch_api else ''}"
    out.mkdir(parents=True, exist_ok=True)
    summary = {"gold": a.gold, "n": len(gold), "mode": a.mode, "model": cfg.model, "effort": cfg.effort,
               "prompt_version": prompt_version(), "routed": len(idx), "ai_errors": len(errs),
               "score_local": s_local["score_pct"], "score_local_plus_ai": s_ai["score_pct"],
               "routed_score_local": s_routed_local and s_routed_local["score_pct"],
               "routed_score_ai": s_routed_ai and s_routed_ai["score_pct"],
               "per_field_local": {f: v["acc"] for f, v in s_local["per_field"].items()},
               "per_field_ai": {f: v["acc"] for f, v in s_ai["per_field"].items()},
               "seconds_local": round(t_local, 2), "seconds_ai": round(t_ai, 2), "usage": stats,
               "usd_per_1k_routed": round(1000 * stats.get("cost_usd", 0) / max(1, len(idx)), 4),
               "estimate_before_run": est}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "pred_local.json").write_text(json.dumps(p_local, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "pred_ai.json").write_text(json.dumps(p_ai, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "errors_ai.md").write_text(errors(p_ai, g), encoding="utf-8")
    lines = [f"# AI path — {cfg.model}, mode {a.mode}{' (Batch API)' if a.batch_api else ''}", "",
             f"Gold: `{a.gold}` — {len(gold)} records, {len(idx)} sent to the AI, {len(errs)} AI errors. Prompt `{prompt_version()}`.", "",
             "| | jury score |", "|---|---|",
             f"| local only | {s_local['score_pct']} |", f"| local + AI | {s_ai['score_pct']} |"]
    if idx:
        lines += [f"| routed records, local | {s_routed_local['score_pct']} |", f"| routed records, AI | {s_routed_ai['score_pct']} |"]
    lines += ["", f"Tokens: {stats}", "",
              f"USD per 1 000 routed records: {summary['usd_per_1k_routed']}; local {t_local:.1f} s, AI {t_ai:.1f} s."]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
