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

import dataclasses
import re

from ..ml.onnx_tagger import ModelSpan
from ..preprocess import extract, match_view, norm_token
from ..rules.grammar import TRIM_NOISE, RuleParse, RuleParser, Span

FIELDS = ["PROVINCE", "CITY", "DISTRICT", "LIVING_AREA", "STREET_TYPE", "STREET_NAME",
          "HOUSE", "APARTMENT", "ENTRANCE", "FLOOR", "LANDMARK"]
KEYWORD_RULES = {"kw_glued", "kw_prev", "kw_next"}
# the model cannot override these: «Аксай 4 мкр» (the gazetteer knows the whole name), a correction («нет, 12»), a house range
ABSOLUTE_RULES = {"type_case_B_gazetteer", "corrected", "house_range"}
DETAIL_KW_ROLES = {"house_kw", "apartment_kw", "entrance_kw", "floor_kw", "building_kw"}
# positional guesses of the grammar (no keyword evidence): the model may override them more easily
WEAK_RULES = {"fallback_word_number", "house_fallback", "city_seed", "after_street", "micro_as_living", "gazetteer_chunk",
              "fallback_word_sep_number", "after_street_sep", "leftover_chunk", "fallback_micro_number", "dash_after_house",
              "city_fuzzy", "city_osm_chunk"}
# spans whose boundaries come from an explicit keyword («район Есиль», «г. Алматы», «ЖК Томирис», «Актюбинская обл»):
# they win overlaps against model spans
ANCHORED_RULES = KEYWORD_RULES | {"district_kw", "city_kw", "city_kk_suffix", "living_kw", "province_table", "type_kw",
                                  "district_bare", "micro_living_prefix", "corrected", "house_range",
                                  "type_case_A", "type_case_B", "type_case_C"}   # «мкр Жулдыз-1» is the street, not a ж/м
COUNTRY = {"казахстан", "kazakhstan", "рк", "kz", "республика казахстан", "қазақстан", "казакстан"}
HOUSE_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?(/\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?)?$")
NUM_RE = re.compile(r"^\d+[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]?$")

DEFAULT_POLICY = {
    "tau_solo": 0.55,          # model-only span accepted above this
    "tau_field": 0.70,         # model beats a conflicting rule span above this
    "tau_override": 0.95,      # model beats a keyword-bound rule span above this
    "tau_weak": 0.55,          # model beats a WEAK (positional) rule span above this
    "tau_veto": 1.01,          # a WEAK rule span with no model span is dropped when the model says O this surely (>1 = off)
    "field_tau": {"STREET_NAME": 0.65, "HOUSE": 0.75, "CITY": 0.70, "LANDMARK": 0.60},
}
# model-only mode (eval/ab.py): rule spans are ignored, every valid model span is taken
MODEL_ONLY = {"tau_solo": 0.0, "tau_field": 0.0, "tau_override": 0.0, "tau_weak": 0.0, "tau_veto": 1.01, "field_tau": {},
              "ignore_rules": True}


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
    if not text or not re.search(r"[^\W_]", text):        # «???», «-», «,» are never a value
        return False
    inside = [t for t in rp.tokens if t.start >= start and t.end <= end]
    if any(t.role in ("phone", "paren", "postcode", "pobox") or t.meta.get("sep") for t in inside):
        return False                                       # phones, «(бывш. Ленина)», postcodes, «а/я 15», separators: never a value
    if field in NAME_FIELDS and any(t.kind == "PUNCT" and t.text == "—" and t.meta.get("sep") for t in inside):
        return False                                       # «Павлодар — Кутузова — …»: repeated em dashes separate fields
    if field in ("STREET_NAME", "CITY", "DISTRICT", "LIVING_AREA") and match_view(text) in COUNTRY \
            and not rp.raw[:start].strip(" ,.;\ufeff"):
        return False                                       # «Казахстан, дом 10» — the country, not «ул. Казахстан»
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


KW_ROLES = {"street_type", "house_kw", "apartment_kw", "entrance_kw", "floor_kw", "living_kw", "city_kw", "city_suffix_kk",
            "district_kw", "province_kw", "building_kw"}
NAME_FIELDS = {"CITY", "DISTRICT", "LIVING_AREA", "STREET_NAME", "LANDMARK"}


def _swallows_keyword(rp: RuleParse, field: str, start: int, end: int, rule_span: Span | None,
                      known_word=None) -> bool:
    """A model name span that starts or ends with a lexicon keyword the grammar read as a keyword AND the tagger never
    saw in training («Abay dangyly», «Almaty qalasy», «obl, Talgar rayon»): the grammar knows that word, the model does
    not. Words in the tagger's vocabulary are left to the model («Expo Avenue», «Smart House», «Набережная» are names in
    OSM). A trailing living-area / settlement word stays («Highvill Residence», «Жана Кала»)."""
    if field not in NAME_FIELDS:
        return False
    toks = [t for t in rp.tokens if t.start >= start and t.end <= end and t.kind != "PUNCT"]
    if not toks:
        return False
    for t in (toks[0], toks[-1]):
        if t.kind != "WORD" or t.role not in KW_ROLES \
                or (rule_span is not None and t.start >= rule_span.start and t.end <= rule_span.end):
            continue
        if len(t.text) == 1:
            continue                                  # an initial: «К. Либкнехта», «Ш. Уалиханова»
        if t is toks[-1] and t is not toks[0] and len(t.text) >= 4 and t.text.isascii() and t.text[0].isupper() \
                and t.text[1:].islower():
            continue                                  # a Latin brand tail: «Smart House», «Expo Avenue», «Dostyk Residence»
        if t.label == "STREET_TYPE" or t.role in DETAIL_KW_ROLES:
            return True                               # «5 линия» (the grammar read «линия» as the type), «… частный дом»
        if known_word is not None and known_word(t.text):
            continue                                  # a word the tagger was trained on («Набережная»)
        if t is toks[-1] and t is not toks[0] and (t.role in ("living_kw", "city_kw")
                                                   or (rule_span is None and t.role == "street_type")):
            continue                                  # «Highvill Residence», «Жана Кала»; model-only «3 линия»
        return True
    return False


def _model_says_o(rp: RuleParse, span: Span, labels: list[str], probs: list[float] | None, tau: float) -> bool:
    """Every token of the rule span is O for the tagger with probability ≥ tau."""
    idx = [i for i, t in enumerate(rp.tokens) if t.start >= span.start and t.end <= span.end]
    if not idx or tau > 1.0:
        return False
    return all(labels[i] == "O" and (probs is None or probs[i] >= tau) for i in idx)


def _trim_noise(rp: RuleParse, field: str, start: int, end: int) -> tuple[int, int]:
    """A model name span never starts or ends with a noise word («Нурсат частный» → «Нурсат»), unless it is all noise."""
    if field not in NAME_FIELDS:
        return start, end
    toks = [t for t in rp.tokens if t.start >= start and t.end <= end and t.kind != "PUNCT"]
    if len(toks) < 2:
        return start, end
    while len(toks) > 1 and norm_token(toks[0].text) in TRIM_NOISE:
        toks.pop(0)
    while len(toks) > 1 and norm_token(toks[-1].text) in TRIM_NOISE:
        toks.pop()
    return toks[0].start, toks[-1].end


def _free_of(final: dict, span: Span, field: str) -> bool:
    return all(s.end <= span.start or s.start >= span.end for f, s in final.items() if f != field)


def arbitrate(rp: RuleParse, model_spans: list[ModelSpan], parser: RuleParser, policy: dict | None = None,
              tok_labels: list[str] | None = None, tok_probs: list[float] | None = None, known_word=None) -> None:
    """Mutates rp.spans / rp.tokens / rp.meta / rp.flags in place.
    tok_labels / tok_probs (the tagger's per-token output) enable the model veto on weak rule spans;
    known_word(text) → True when the tagger's vocabulary has the word (see _swallows_keyword)."""
    policy = policy or POLICY
    ignore_rules = policy.get("ignore_rules", False)
    best_model: dict[str, ModelSpan] = {}
    rule_city = None if ignore_rules else rp.spans.get("CITY")
    known_city = rule_city is not None and bool(rp.meta.get("city_known")) and rule_city.rule != "city_osm_chunk"
    for ms in model_spans:
        if ms.field in FIELDS and (ms.field not in best_model or ms.prob > best_model[ms.field].prob):
            if known_city and ms.field in NAME_FIELDS and ms.field != "CITY" \
                    and (ms.start, ms.end) != (rule_city.start, rule_city.end):
                # «ЖК Хайвил Астана»: a name span never swallows the known city at its edge — the city stays a city
                if ms.start == rule_city.start and ms.end > rule_city.end:
                    rest = [t for t in rp.tokens if t.start >= rule_city.end and t.end <= ms.end and t.kind != "PUNCT"]
                    if rest:
                        ms = dataclasses.replace(ms, start=rest[0].start)
                elif ms.end == rule_city.end and ms.start < rule_city.start:
                    rest = [t for t in rp.tokens if t.start >= ms.start and t.end <= rule_city.start and t.kind != "PUNCT"]
                    if rest:
                        ms = dataclasses.replace(ms, end=rest[-1].end)
            s0, e0 = _trim_noise(rp, ms.field, ms.start, ms.end)
            if (s0, e0) != (ms.start, ms.end):
                ms = dataclasses.replace(ms, start=s0, end=e0)
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
            if r.rule in WEAK_RULES and tok_labels and len(rp.spans) > 1 \
                    and _model_says_o(rp, r, tok_labels, tok_probs, policy.get("tau_veto", 1.01)):
                # (never veto the only thing parsed: a lone «Абая» is a street, not an empty address)
                rp.flags.append(f"arb_{f.lower()}_model_veto")
                continue
            final[f] = r
            rp.flags.append(f"arb_{f.lower()}_rule_only")
        elif m and not r:
            bspan = rp.meta.get("house_building_span")
            if not ignore_rules and bspan and m.start <= bspan[0] and bspan[1] <= m.end:
                rp.flags.append(f"arb_{f.lower()}_model_rejected")      # «42 үй 2 корпус»: «2» is the корпус
            elif m.prob >= policy["tau_solo"] and _ok(rp, f, m.start, m.end) \
                    and (ignore_rules or not _swallows_keyword(rp, f, m.start, m.end, None, known_word)):
                final[f] = Span(f, m.start, m.end, "model", m.prob)
                rp.flags.append(f"arb_{f.lower()}_model_only")
            else:
                rp.flags.append(f"arb_{f.lower()}_model_rejected")
        elif r and m:
            if r.rule in ABSOLUTE_RULES:
                final[f] = r
                rp.flags.append(f"arb_{f.lower()}_rule_wins")
                continue
            if f == "PROVINCE" and r.rule == "province_table" and r.start <= m.start and m.end <= r.end:
                final[f] = r                          # «Almaty obl»: the table match is longer than the model's «Almaty»
                rp.flags.append(f"arb_{f.lower()}_rule_wins")
                continue
            if r.rule == "living_kw" and (m.end <= r.start or m.start >= r.end):
                final[f] = r                          # «ЖК Хайвил» (explicit keyword) vs the model's «левый берег» elsewhere
                # (only ЖК: city/district keyword spans are error-prone — «А.Б.» as «аул Б» — and the model corrects them)
                rp.flags.append(f"arb_{f.lower()}_rule_wins")
                continue
            keyword_bound = r.rule in KEYWORD_RULES
            weak = r.rule in WEAK_RULES
            threshold = policy["tau_override"] if keyword_bound else (min(tau, policy.get("tau_weak", tau)) if weak else tau)
            if m.prob >= threshold and _ok(rp, f, m.start, m.end) and not _swallows_keyword(rp, f, m.start, m.end, r, known_word):
                final[f] = Span(f, m.start, m.end, "model", m.prob)
                rp.flags.append(f"arb_{f.lower()}_model_wins")
            else:
                final[f] = r
                rp.flags.append(f"arb_{f.lower()}_rule_wins")
        # neither: nothing

    # overlap resolution between different fields: keyword-anchored rule spans first, then the higher score;
    # a model span that loses falls back to the rule span of its field when that one still fits
    # keyword-anchored rule spans win only a same-token conflict with a model span of another field («район алматы»:
    # district vs city); when the model's span is longer («18-я (микрорайон Болашак)») the higher score decides as before
    model_offsets = {(x.start, x.end) for x in final.values() if x.rule == "model"}

    def anchored(sp: Span) -> bool:
        rule = sp.rule[6:] if sp.rule.startswith("agree:") else sp.rule
        if rule == "province_table" and any(sp.start <= a and b <= sp.end and (a, b) != (sp.start, sp.end)
                                            for a, b in model_offsets):
            return True                                # «Almaty obl» (table) beats the model's city «Almaty» inside it
        return rule in ANCHORED_RULES and (sp.start, sp.end) in model_offsets

    accepted = sorted(final.values(), key=lambda s: (s.rule not in ABSOLUTE_RULES, not anchored(s), -s.score))
    kept: list[Span] = []
    for s in accepted:
        if any(not (s.end <= k.start or s.start >= k.end) for k in kept):
            rp.flags.append(f"arb_{s.field.lower()}_overlap_dropped")
            r = None if ignore_rules else rp.spans.get(s.field)
            if s.rule == "model" and r is not None and all(r.end <= k.start or r.start >= k.end for k in kept):
                kept.append(r)
                rp.flags.append(f"arb_{s.field.lower()}_rule_restored")
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
