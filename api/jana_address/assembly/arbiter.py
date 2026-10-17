"""Arbiter — merges rule spans and model spans into the final spans (ARCHITECTURE.md §3.4).

Policy (all thresholds in POLICY; tune on the dev set with `make eval`):
  1. rule and model agree (same offsets)                → accept, score = max
  2. only one side has the field                        → accept it (model needs prob ≥ tau_solo)
  3. conflict                                           → model wins if prob ≥ tau_field (tau_weak against a
                                                          positional rule guess), else rule;
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
# positional guesses of the grammar (no keyword evidence): the model may override them more easily
WEAK_RULES = {"fallback_word_number", "house_fallback", "city_seed", "after_street", "micro_as_living", "gazetteer_chunk"}
HOUSE_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?(/\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?)?$")
NUM_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?$")

DEFAULT_POLICY = {
    "tau_solo": 0.55,          # model-only span accepted above this
    "tau_field": 0.70,         # model beats a conflicting rule span above this
    "tau_override": 0.95,      # model beats a keyword-bound rule span above this
    "tau_weak": 0.55,          # model beats a WEAK (positional) rule span above this
    "field_tau": {"STREET_NAME": 0.65, "HOUSE": 0.75, "CITY": 0.70, "LANDMARK": 0.60},
}
# model-only mode (eval/ab.py): rule spans are ignored, every valid model span is taken
MODEL_ONLY = {"tau_solo": 0.0, "tau_field": 0.0, "tau_override": 0.0, "tau_weak": 0.0, "field_tau": {}, "ignore_rules": True}


def _load_policy() -> dict:
    """api/artifacts/arbiter_policy.json (written by training/tune_arbiter.py) overrides the defaults."""
    import json
    import os
    from pathlib import Path
    path = Path(os.environ.get("JANA_ARTIFACTS_DIR", Path(__file__).resolve().parents[2] / "artifacts")) / "arbiter_policy.json"
    pol = dict(DEFAULT_POLICY)
    if path.exists():
        try:
            pol.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a broken policy file must not take the service down
            pass
    return pol


POLICY = _load_policy()


def _ok(rp: RuleParse, field: str, start: int, end: int) -> bool:
    text = extract(rp.raw, start, end)
    if not text:
        return False
    if field == "HOUSE":
        return bool(HOUSE_RE.match(text.replace(" ", "")))
    if field in ("APARTMENT", "ENTRANCE", "FLOOR"):
        return bool(NUM_RE.match(re.sub(r"[^0-9A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]", "", text)))
    return True


PROVINCE_KW = ("область", "обл", "облысы", "облысында", "region", "obl", "oblast")


def province_lookup(parser: RuleParser, text: str) -> dict | None:
    """Canonical province for a model span: as is, without a trailing «обл/облысы», or with «облысы» added."""
    key = match_view(text)
    table = parser.lex.province_by_variant
    if key in table:
        return table[key]
    words = key.split()
    if words and words[-1] in PROVINCE_KW and " ".join(words[:-1]) in table:
        return table[" ".join(words[:-1])]
    for suffix in (" облысы", " область"):
        if key + suffix in table:
            return table[key + suffix]
    return None


def _free_of(final: dict, span: Span, field: str) -> bool:
    return all(s.end <= span.start or s.start >= span.end for f, s in final.items() if f != field)


def arbitrate(rp: RuleParse, model_spans: list[ModelSpan], parser: RuleParser, policy: dict | None = None) -> None:
    """Mutates rp.spans / rp.tokens / rp.meta / rp.flags in place."""
    policy = policy or POLICY
    ignore_rules = policy.get("ignore_rules", False)
    best_model: dict[str, ModelSpan] = {}
    for ms in model_spans:
        if ms.field in FIELDS and (ms.field not in best_model or ms.prob > best_model[ms.field].prob):
            best_model[ms.field] = ms
    rp.meta["model_probs"] = {f: round(m.prob, 3) for f, m in best_model.items()}

    final: dict[str, Span] = {}
    for f in FIELDS:
        r, m = (None if ignore_rules else rp.spans.get(f)), best_model.get(f)
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
            weak = r.rule in WEAK_RULES
            threshold = policy["tau_override"] if keyword_bound else (min(tau, policy.get("tau_weak", tau)) if weak else tau)
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
        p = province_lookup(parser, extract(rp.raw, final["PROVINCE"].start, final["PROVINCE"].end) or "")
        if p:
            rp.meta["province_canonical"] = p["canonical"]
        else:
            rp.flags.append("arb_province_not_canonical")
            r = rp.spans.get("PROVINCE")
            if r is not None and not ignore_rules and _free_of(final, r, "PROVINCE"):
                final["PROVINCE"] = r                  # the rule span WAS canonical: keep it
            else:
                final.pop("PROVINCE")
    if "STREET_TYPE" in final and final["STREET_TYPE"].rule == "model":
        text = extract(rp.raw, final["STREET_TYPE"].start, final["STREET_TYPE"].end) or ""
        words = text.split()
        lemma = None
        if len(words) >= 2:
            lemma = (parser.lex.bigrams.get(match_view(words[-2])) or {}).get(match_view(words[-1]))
        lemma = lemma or parser.lex.street_type_of.get(match_view(words[-1] if words else ""))
        if lemma and lemma != "ЖК":
            rp.meta["street_type_lemma"] = lemma
        else:
            r = rp.spans.get("STREET_TYPE")
            if r is not None and not ignore_rules and _free_of(final, r, "STREET_TYPE"):
                final["STREET_TYPE"] = r
            else:
                final.pop("STREET_TYPE")
    # city meta (Kazakh→Russian name, province of a republican city, oblast district) must follow the FINAL city span
    rule_city = rp.spans.get("CITY")
    if "CITY" not in final or (rule_city is None or (final["CITY"].start, final["CITY"].end) != (rule_city.start, rule_city.end)):
        for k in ("city_text", "city_known", "city_ru_from_kk", "district_from_city", "province_from_city"):
            rp.meta.pop(k, None)
        if "CITY" in final:
            parser._note_city(rp, extract(rp.raw, final["CITY"].start, final["CITY"].end) or "")
            key = match_view(rp.meta.get("city_text", ""))
            if key in parser.lex.province_of_city_name:
                rp.meta["province_from_city"] = parser.lex.province_of_city_name[key]
            elif key in parser.lex.city_by_name:
                rp.meta["province_from_city"] = parser.lex.city_by_name[key]["province"]

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
