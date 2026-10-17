"""Arbiter — merges rule spans and model spans into the final spans (ARCHITECTURE.md §3.4).

Policy (all thresholds in POLICY; tune on the dev set with `make eval`):
  1. rule and model agree (same offsets)                → accept, score = max
  2. only one side has the field                        → accept it (model needs prob ≥ tau_solo)
  3. conflict                                           → model wins if prob ≥ tau_field, else rule;
                                                          EXCEPT apartment/entrance/floor/house bound by an
                                                          explicit keyword (rule ids kw_glued/kw_prev/kw_next):
                                                          the rule wins unless the model is very sure (≥ tau_override)
  4. hard constraints on every accepted span: non-empty text; house pattern; detail fields numeric;
     no overlap between accepted spans of different fields (higher score survives)
Every decision is recorded in rp.flags as `arb_<field>_<decision>` for the confidence model.
"""
from __future__ import annotations

import re

from ..ml.onnx_tagger import ModelSpan
from ..preprocess import extract, match_view
from ..rules.grammar import RuleParse, RuleParser, Span

FIELDS = ["PROVINCE", "CITY", "DISTRICT", "LIVING_AREA", "STREET_TYPE", "STREET_NAME",
          "HOUSE", "APARTMENT", "ENTRANCE", "FLOOR", "LANDMARK"]
KEYWORD_RULES = {"kw_glued", "kw_prev", "kw_next"}
HOUSE_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?(/\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?)?$")
NUM_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?$")

POLICY = {
    "tau_solo": 0.55,          # model-only span accepted above this
    "tau_field": 0.70,         # model beats a conflicting rule span above this
    "tau_override": 0.95,      # model beats a keyword-bound rule span above this
    "field_tau": {"STREET_NAME": 0.65, "HOUSE": 0.75, "CITY": 0.70, "LANDMARK": 0.60},
}


def _ok(rp: RuleParse, field: str, start: int, end: int) -> bool:
    text = extract(rp.raw, start, end)
    if not text:
        return False
    if field == "HOUSE":
        return bool(HOUSE_RE.match(text.replace(" ", "")))
    if field in ("APARTMENT", "ENTRANCE", "FLOOR"):
        return bool(NUM_RE.match(re.sub(r"[^0-9A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]", "", text)))
    return True


def arbitrate(rp: RuleParse, model_spans: list[ModelSpan], parser: RuleParser, policy: dict = POLICY) -> None:
    """Mutates rp.spans / rp.tokens / rp.meta / rp.flags in place."""
    best_model: dict[str, ModelSpan] = {}
    for ms in model_spans:
        if ms.field in FIELDS and (ms.field not in best_model or ms.prob > best_model[ms.field].prob):
            best_model[ms.field] = ms
    rp.meta["model_probs"] = {f: round(m.prob, 3) for f, m in best_model.items()}

    final: dict[str, Span] = {}
    for f in FIELDS:
        r, m = rp.spans.get(f), best_model.get(f)
        tau = policy["field_tau"].get(f, policy["tau_field"])
        if r and m and r.start == m.start and r.end == m.end:
            final[f] = Span(f, r.start, r.end, f"agree:{r.rule}", max(r.score, m.prob))
            rp.flags.append(f"arb_{f.lower()}_agree")
        elif r and not m:
            final[f] = r
            rp.flags.append(f"arb_{f.lower()}_rule_only")
        elif m and not r:
            if m.prob >= policy["tau_solo"] and _ok(rp, f, m.start, m.end):
                final[f] = Span(f, m.start, m.end, "model", m.prob)
                rp.flags.append(f"arb_{f.lower()}_model_only")
            else:
                rp.flags.append(f"arb_{f.lower()}_model_rejected")
        elif r and m:
            keyword_bound = r.rule in KEYWORD_RULES
            threshold = policy["tau_override"] if keyword_bound else tau
            if m.prob >= threshold and _ok(rp, f, m.start, m.end):
                final[f] = Span(f, m.start, m.end, "model", m.prob)
                rp.flags.append(f"arb_{f.lower()}_model_wins")
            else:
                final[f] = r
                rp.flags.append(f"arb_{f.lower()}_rule_wins")
        # neither: nothing

    # overlap resolution between different fields: keep the higher score
    accepted = sorted(final.values(), key=lambda s: -s.score)
    kept: list[Span] = []
    for s in accepted:
        if any(not (s.end <= k.start or s.start >= k.end) for k in kept):
            rp.flags.append(f"arb_{s.field.lower()}_overlap_dropped")
            continue
        kept.append(s)
    final = {s.field: s for s in kept}

    # province / street_type need their table lookups when the model introduced or changed the span
    if "PROVINCE" in final and final["PROVINCE"].rule == "model":
        key = match_view(extract(rp.raw, final["PROVINCE"].start, final["PROVINCE"].end) or "")
        p = parser.lex.province_by_variant.get(key)
        if p:
            rp.meta["province_canonical"] = p["canonical"]
        else:
            final.pop("PROVINCE")
            rp.flags.append("arb_province_not_canonical")
    if "STREET_TYPE" in final and final["STREET_TYPE"].rule == "model":
        text = extract(rp.raw, final["STREET_TYPE"].start, final["STREET_TYPE"].end) or ""
        lemma = parser.lex.street_type_of.get(match_view(text.split()[-1] if text else ""))
        if lemma:
            rp.meta["street_type_lemma"] = lemma
        else:
            final.pop("STREET_TYPE")
    if "CITY" in final and final["CITY"].rule == "model":
        parser._note_city(rp, extract(rp.raw, final["CITY"].start, final["CITY"].end) or "")

    # rebuild token labels from the final spans (explain mode shows the final decision)
    rp.spans = final
    for t in rp.tokens:
        if t.label not in ("O", None):
            t.label = None
    for s in final.values():
        for t in rp.tokens:
            if t.start >= s.start and t.end <= s.end:
                t.label = s.field
    rp.meta["model_used"] = True
