"""AI cross-check: our parser vs an LLM labeler on the same addresses; every disagreement goes to a human.

    python -m eval.llm_verify --input data/demo_input.json [--gold data/demo_gold.json] --out runs/verify \
        [--provider anthropic|openai-compatible|rules] [--model claude-opus-5-5] [--limit 300] [--batch-size 20]

What it does
  1. our answer: the full service (rules ⊕ model ⊕ gazetteer); only STRING-derived fields are compared — gazetteer
     fills are enrichment, not parsing;
  2. the LLM's answer: the labeling prompt of training/label_llm.py (conventions + 36 examples, structured JSON);
  3. field-by-field comparison with the jury's normalization; the LLM's values are run through the same validators as
     our labels (copy-not-rewrite: an LLM that «fixes» a typo or a case is caught as `llm_not_in_string`);
  4. with --gold, both sides are scored — the report says who is right on each disagreement and gives the LLM's own
     jury score as a reference;
  5. outputs, in --out:
       report.md            agreement per field, every disagreement (raw | ours | LLM | gold | hint)
       review_queue.jsonl   label_llm.py shape (fields = ours, llm_fields, disagreements, issues) → review UI (HANDOFF A1)
       review.html          standalone page: pick ours / LLM / type a value per disagreement, export corrections.jsonl
                            (the POST /api/v1/corrections shape → build_dataset --corrections)
       summary.json

Providers: `anthropic` (Claude API; credentials from ANTHROPIC_API_KEY or an `ant auth login` profile),
`openai-compatible` (a local vLLM on the university GPU: OPENAI_BASE_URL — data never leaves the campus), `rules`
(the rule grammar as a stand-in "second opinion": no network, used by the tests).
Data policy: do not send Jana Post's train.json to an external API unless the organizers allow it; use the local vLLM.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from eval.ab import load_gold  # noqa: E402
from eval.metric import ADDRESS, DETAILS, REFS, norm, score  # noqa: E402
from jana_address.service import parse_one  # noqa: E402
from training.label_llm import (DEFAULT_MODEL, PROVIDERS, LLMRefusal, build_system_prompt,  # noqa: E402
                                from_string)
from training.validate_labels import FIELDS, validate_record  # noqa: E402

COMPARED = ADDRESS + DETAILS + REFS + ["street_type", "landmark"]


def our_fields(rid: str, raw: str) -> tuple[dict, dict]:
    p = parse_one(rid, raw, explain=True)
    fields = {f: (p[f] if from_string(p["provenance"].get(f)) else None) for f in FIELDS}
    return fields, p


def llm_labels(records: list[dict], provider: str, model: str, batch_size: int, usage: dict) -> dict[str, dict]:
    labeler = PROVIDERS[provider]
    system = build_system_prompt()
    out: dict[str, dict] = {}
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            got = labeler(batch, system, model, usage)
        except LLMRefusal as e:
            print(f"  {e}", file=sys.stderr)
            for r in batch:
                out[r["id"]] = {"_error": "refusal"}
            continue
        by_id = {str(g.get("id")): g for g in got}
        for r in batch:
            g = by_id.get(r["id"])
            out[r["id"]] = {f: (g.get(f) or None) for f in FIELDS} if g else {"_error": "missing in LLM answer"}
        print(f"  {min(i + batch_size, len(records))}/{len(records)} labeled", file=sys.stderr)
    return out


def compare(ours: dict, llm: dict) -> list[str]:
    return [f for f in COMPARED if norm(ours.get(f)) != norm(llm.get(f))]


def verdict(f: str, ours: dict, llm: dict, gold: dict | None) -> str:
    if gold is None:
        return ""
    g = norm(gold.get(f))
    o, m = norm(ours.get(f)), norm(llm.get(f))
    if o == g and m != g:
        return "ours ✓"
    if m == g and o != g:
        return "LLM ✓"
    return "neither" if o != g and m != g else "both"


def write_html(path: Path, rows: list[dict]) -> None:
    """Standalone review page: one card per disagreement, choose ours / LLM / custom, export corrections.jsonl."""
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Jana Address — AI cross-check review</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#e5e5ea; --ours:#0a7d32; --llm:#0057b8; --bad:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#121214; --fg:#f2f2f7; --muted:#a1a1a6; --line:#2c2c30;
  --ours:#4cc27a; --llm:#6aa8ff; --bad:#ff6b6b; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:16px; }}
h1 {{ font-size:18px; margin:0 0 4px }} .muted {{ color:var(--muted) }}
.card {{ border:1px solid var(--line); border-radius:10px; padding:12px; margin:12px 0; max-width:980px }}
.raw {{ font-family:ui-monospace,Menlo,monospace; font-size:13px; word-break:break-word }}
table {{ border-collapse:collapse; width:100%; margin-top:8px }} td,th {{ border-top:1px solid var(--line); padding:4px 6px;
  text-align:left; vertical-align:top }} .o {{ color:var(--ours) }} .l {{ color:var(--llm) }} .bad {{ color:var(--bad) }}
input[type=text] {{ width:100%; box-sizing:border-box; background:transparent; color:var(--fg); border:1px solid var(--line);
  border-radius:6px; padding:2px 6px }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); display:flex; gap:12px;
  flex-wrap:wrap; align-items:center }}
button {{ border:1px solid var(--line); background:transparent; color:var(--fg); border-radius:8px; padding:6px 12px; cursor:pointer }}
</style></head><body>
<h1>AI cross-check — disagreements between our parser and the LLM</h1>
<div class="muted">Pick the right value per field (ours / LLM / type one). «Export» downloads corrections.jsonl for
<code>build_dataset --corrections</code>. Rule of the brief: values are copied from the string exactly as written.</div>
<div class="bar"><span id="count"></span><button id="exp">Export corrections.jsonl</button>
<label><input type="checkbox" id="only"> only undecided</label></div><div id="list"></div>
<script>
const rows = {data};
const FIELDS = {json.dumps(FIELDS)};
const state = {{}};
function key(r, f) {{ return r.id + "|" + f; }}
function render() {{
  const only = document.getElementById("only").checked;
  const list = document.getElementById("list"); list.textContent = "";
  let undecided = 0;
  for (const r of rows) {{
    const open = r.disagreements.filter(f => !state[key(r, f)]);
    undecided += open.length;
    if (only && !open.length) continue;
    const card = document.createElement("div"); card.className = "card";
    const raw = document.createElement("div"); raw.className = "raw"; raw.textContent = r.id + " · " + r.raw_address; card.appendChild(raw);
    if (r.issues && r.issues.length) {{ const w = document.createElement("div"); w.className = "bad";
      w.textContent = "LLM issues: " + r.issues.join("; "); card.appendChild(w); }}
    const t = document.createElement("table");
    t.innerHTML = "<tr><th>field</th><th class=o>ours</th><th class=l>LLM</th><th>gold</th><th>decision</th></tr>";
    for (const f of r.disagreements) {{
      const tr = document.createElement("tr");
      const cell = (v, c) => {{ const td = document.createElement("td"); if (c) td.className = c; td.textContent = v ?? "∅"; return td; }};
      tr.appendChild(cell(f)); tr.appendChild(cell(r.ours[f], "o")); tr.appendChild(cell(r.llm[f], "l"));
      tr.appendChild(cell(r.gold ? r.gold[f] : "")); const td = document.createElement("td");
      for (const [lbl, val] of [["ours", r.ours[f]], ["LLM", r.llm[f]]]) {{
        const b = document.createElement("button"); b.textContent = lbl;
        if (state[key(r, f)] && state[key(r, f)].from === lbl) b.style.fontWeight = "700";
        b.onclick = () => {{ state[key(r, f)] = {{from: lbl, value: val ?? null}}; render(); }}; td.appendChild(b);
      }}
      const inp = document.createElement("input"); inp.type = "text"; inp.placeholder = "other value (empty = null)";
      if (state[key(r, f)] && state[key(r, f)].from === "custom") inp.value = state[key(r, f)].value ?? "";
      inp.onchange = () => {{ state[key(r, f)] = {{from: "custom", value: inp.value.trim() || null}}; render(); }};
      td.appendChild(inp); tr.appendChild(td); t.appendChild(tr);
    }}
    card.appendChild(t); list.appendChild(card);
  }}
  document.getElementById("count").textContent = rows.length + " records · " + undecided + " field decisions open";
}}
document.getElementById("only").onchange = render;
document.getElementById("exp").onclick = () => {{
  const lines = [];
  for (const r of rows) {{
    const decided = r.disagreements.filter(f => state[key(r, f)]);
    if (!decided.length) continue;
    const fields = {{}}; for (const f of FIELDS) fields[f] = r.ours[f] ?? null;
    for (const f of decided) fields[f] = state[key(r, f)].value;
    lines.push(JSON.stringify({{id: r.id, raw_address: r.raw_address, fields, reviewer: "ai-crosscheck",
      note: "decided: " + decided.map(f => f + "=" + state[key(r, f)].from).join(", ")}}));
  }}
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([lines.join("\\n") + "\\n"], {{type: "application/json"}}));
  a.download = "corrections.jsonl"; a.click();
}};
render();
</script></body></html>""", encoding="utf-8")


def run(records: list[dict], gold_by_id: dict[str, dict], provider: str, model: str, batch_size: int) -> dict:
    usage = {"input": 0, "output": 0}
    t0 = time.time()
    llm = llm_labels(records, provider, model, batch_size, usage)
    rows, per_field, n_dis = [], Counter(), 0
    ours_all, llm_all, gold_all = [], [], []
    for r in records:
        ours, parsed = our_fields(r["id"], r["raw_address"])
        m = llm.get(r["id"], {"_error": "missing"})
        err = m.get("_error")
        mf = {f: m.get(f) for f in FIELDS} if not err else {f: None for f in FIELDS}
        dis = compare(ours, mf) if not err else list(COMPARED)
        issues = [] if err else [f"{i['code']}:{i.get('field')}" for i in validate_record(r["raw_address"], mf)
                                  if i["severity"] == "error"]
        if err:
            issues = [f"llm_error:{err}"]
        g = gold_by_id.get(r["id"])
        if g is not None:
            ours_all.append({"id": r["id"], **ours})
            llm_all.append({"id": r["id"], **mf})
            gold_all.append(g)
        for f in dis:
            per_field[f] += 1
        n_dis += bool(dis)
        rows.append({"id": r["id"], "raw_address": r["raw_address"], "ours": ours, "llm": mf, "gold": g,
                     "disagreements": dis, "issues": issues, "confidence": parsed.get("confidence"),
                     "verdicts": {f: verdict(f, ours, mf, g) for f in dis}})
    res = {"n": len(records), "records_with_disagreement": n_dis, "per_field": dict(per_field), "provider": provider,
           "model": model, "usage": usage, "seconds": round(time.time() - t0, 1)}
    if gold_all:
        res["ours_score"] = score(ours_all, gold_all)["score_pct"]
        res["llm_score"] = score(llm_all, gold_all)["score_pct"]
        res["verdicts"] = dict(Counter(v for r in rows for v in r["verdicts"].values()))
    return {"summary": res, "rows": rows}


def write_outputs(out: Path, result: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows, s = result["rows"], result["summary"]
    dis_rows = [r for r in rows if r["disagreements"]]
    with (out / "review_queue.jsonl").open("w", encoding="utf-8") as f:
        for r in dis_rows:
            f.write(json.dumps({"id": r["id"], "raw_address": r["raw_address"], "fields": r["ours"], "llm_fields": r["llm"],
                                "disagreements": r["disagreements"], "issues": [{"severity": "error", "code": i} for i in r["issues"]],
                                "needs_review": True, "labeler": f"crosscheck:{s['provider']}"}, ensure_ascii=False) + "\n")
    write_html(out / "review.html", dis_rows)
    lines = [f"# AI cross-check — ours vs {s['provider']}:{s['model']}", "",
             f"{s['n']} records, **{s['records_with_disagreement']}** with at least one disagreement "
             f"({100 * s['records_with_disagreement'] / max(1, s['n']):.1f} %). "
             f"Tokens in/out: {s['usage'].get('input', 0)}/{s['usage'].get('output', 0)}.", ""]
    if "ours_score" in s:
        lines += [f"Against gold: **ours {s['ours_score']:.2f}**, LLM {s['llm_score']:.2f} (jury metric). "
                  f"Disagreement verdicts: {s['verdicts']}", ""]
    lines += ["| field | disagreements |", "|---|---|"] + [f"| {f} | {c} |" for f, c in sorted(s["per_field"].items(), key=lambda x: -x[1])]
    lines += ["", "## Disagreements", "", "| id | raw | field | ours | LLM | gold | verdict |", "|---|---|---|---|---|---|---|"]
    for r in dis_rows:
        for f in r["disagreements"]:
            raw = r["raw_address"].replace("|", "\\|")
            gv = r["gold"].get(f) if r["gold"] else ""
            lines.append(f"| {r['id']} | {raw} | {f} | {r['ours'].get(f)} | {r['llm'].get(f)} | {gv} | {r['verdicts'].get(f, '')} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="jury input format, or a gold file (raw_address is taken from it)")
    ap.add_argument("--gold", help="optional gold (jury format) to score both sides")
    ap.add_argument("--out", default="runs/verify")
    ap.add_argument("--provider", choices=list(PROVIDERS), default="anthropic")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--queue", help="also push disagreements / low-confidence records into this conflict queue "
                                    "(e.g. data/conflicts.sqlite3, the file the API's /api/v1/conflicts reads)")
    ap.add_argument("--min-confidence", type=float, default=0.7, help="our confidence below this also goes to the queue")
    a = ap.parse_args(argv)
    recs = [{"id": str(g["id"]), "raw_address": g["raw_address"]} for g in load_gold(a.input)]
    if a.limit:
        recs = recs[: a.limit]
    gold_by_id = {str(g["id"]): g for g in load_gold(a.gold)} if a.gold else {}
    try:
        result = run(recs, gold_by_id, a.provider, a.model, a.batch_size)
    except TypeError as e:
        if "authentication" not in str(e):
            raise
        print("No Claude credentials: export ANTHROPIC_API_KEY=... or run `ant auth login`; "
              "or use --provider openai-compatible with a local vLLM (OPENAI_BASE_URL).", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — anthropic.* errors without importing the SDK here
        name, msg = type(e).__name__, str(getattr(e, "message", e))
        if name in ("AuthenticationError", "PermissionDeniedError"):
            print(f"Claude rejected the key ({name}): check it in the Anthropic Console.", file=sys.stderr)
            return 3
        if name == "BadRequestError" and "workspace" in msg:
            print("This key is not scoped to a workspace. Set ANTHROPIC_WORKSPACE_ID to the workspace ID "
                  "(Anthropic Console → Settings → Workspaces, it looks like wrkspc_…) and run again.", file=sys.stderr)
            return 4
        if name in ("APIConnectionError", "APITimeoutError"):
            print("Could not reach the Claude API (network, or an invalid character in the key / headers).", file=sys.stderr)
            return 6
        if name.endswith("Error") and hasattr(e, "status_code"):
            print(f"Claude API error {e.status_code}: {msg}", file=sys.stderr)
            return 5
        raise
    write_outputs(Path(a.out), result)
    s = result["summary"]
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(f"→ {a.out}/report.md, review_queue.jsonl, review.html")
    if a.queue:
        from jana_address.conflicts import ConflictStore, classify
        items = [it for it in (classify(r, a.min_confidence) for r in result["rows"]) if it]
        stats = ConflictStore(Path(a.queue)).upsert(items, a.provider, a.model)
        print(f"→ conflict queue {a.queue}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
