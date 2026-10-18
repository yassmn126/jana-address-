/**
 * Why a record got its confidence — exact SHAP values of the API's calibrated model.
 *
 * The API scores confidence with a logistic regression over standardized features, then an isotonic table
 * (api/jana_address/confidence: features.py · heuristic.py, weights in api/artifacts/calibrator.json).
 * Every input of that model is in the explain response (flags, fields, model_probs, tokens, raw string), so the
 * browser rebuilds the same feature vector. For a linear model with independent features the SHAP value of
 * feature i is coef_i · (x_i − mean_i) / scale_i: contributions add up exactly to the logit, and the logit
 * through the isotonic table gives the score the API returned. `exact` says whether it reproduced it.
 */
import type { RecordOut } from "../types";

interface Calibrator {
  version: number;
  features: string[];
  mean: number[];
  scale: number[];
  coef: number[];
  intercept: number;
  iso_x: number[];
  iso_y: number[];
}

const FLAG_FEATURES = [
  "no_street", "no_house", "street_without_type", "house_without_keyword", "house_ambiguous",
  "ambiguous_number_binding", "ambiguous_p_as_apartment", "ambiguous_p_as_entrance", "leftover_word",
  "leftover_number", "multiple_street_types", "microdistrict_as_living_area", "city_kk_only",
  "keyword_without_number", "duplicate_house", "duplicate_apartment", "house_has_building_part",
  "district_kw_without_name", "city_duplicate", "postcode", "living_area_from_gazetteer",
  "street_in_gazetteer", "street_not_in_gazetteer", "district_from_osm", "arb_province_not_canonical",
];
const ARB_FIELDS = ["province", "city", "street_name", "house", "apartment", "entrance", "floor", "district", "living_area"];
const ARB_OUTCOMES = ["agree", "rule_only", "model_only", "model_wins", "rule_wins", "model_rejected", "overlap_dropped"];
const ADDRESS_PROBS = ["PROVINCE", "CITY", "STREET_NAME", "HOUSE"];

/** features.py · featurize, line for line. */
export function featurize(r: RecordOut): number[] {
  const flags = r.flags ?? [];
  const fl = new Set(flags);
  const mp = r.model_probs ?? {};
  const x: number[] = [];
  for (const f of FLAG_FEATURES) {
    x.push(f === "leftover_word" || f === "leftover_number" ? flags.filter((g) => g === f).length : fl.has(f) ? 1 : 0);
  }
  for (const f of ARB_FIELDS) for (const o of ARB_OUTCOMES) x.push(fl.has(`arb_${f}_${o}`) ? 1 : 0);
  for (const f of ARB_FIELDS) x.push(Number(mp[f.toUpperCase()] ?? 0));
  for (const f of ["province", "city", "street_name", "house", "apartment"] as const) x.push(r[f] ? 1 : 0);
  const raw = r.raw_address;
  x.push((r.tokens ?? []).length);
  x.push(/[a-z]/i.test(raw) ? 1 : 0);
  x.push(raw.includes("/") ? 1 : 0);
  const addr = ADDRESS_PROBS.filter((k) => k in mp).map((k) => Number(mp[k]));
  x.push(addr.length ? Math.min(...addr) : 0);
  x.push(addr.length ? addr.reduce((a, b) => a + b, 0) / addr.length : 0);
  x.push(flags.filter((f) => f.endsWith("_model_wins") || f.endsWith("_rule_wins") || f.endsWith("_model_rejected")).length);
  x.push(Object.keys(mp).length ? 1 : 0);
  return x;
}

/** numpy.interp: linear, clamped to the end values. */
function interp(p: number, xs: number[], ys: number[]) {
  if (p <= xs[0]) return ys[0];
  if (p >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 1; i < xs.length; i++) {
    if (p <= xs[i]) {
      const [x0, x1, y0, y1] = [xs[i - 1], xs[i], ys[i - 1], ys[i]];
      return x1 === x0 ? y0 : y0 + ((y1 - y0) * (p - x0)) / (x1 - x0);
    }
  }
  return ys[ys.length - 1];
}

const sigmoid = (z: number) => 1 / (1 + Math.exp(-Math.max(-30, Math.min(30, z))));
const calibrate = (cal: Calibrator, z: number) => interp(sigmoid(z), cal.iso_x, cal.iso_y);

export interface Contribution {
  feature: string;
  value: number;
  shap: number;          // contribution to the logit
  label: string;
}

export interface Explanation {
  base: number;                  // calibrated score of the average record (all features at their mean)
  score: number;                 // the model's score for this record, before the «nothing parsed» cap
  reproduced: number;            // after the API's rounding and cap: compare with r.confidence
  exact: boolean;
  cappedEmpty: boolean;          // nothing parsed → API caps confidence at 0.08
  contributions: Contribution[]; // sorted by |shap|, largest first
}

let calPromise: Promise<Calibrator> | null = null;
export const loadCalibrator = () =>
  (calPromise ??= import("../model/calibrator.json").then((m) => m.default as unknown as Calibrator));

export function explain(r: RecordOut, cal: Calibrator): Explanation | null {
  if (!r.flags || !r.tokens || cal.features.length !== cal.coef.length) return null;
  const x = featurize(r);
  if (x.length !== cal.features.length) return null;
  const contributions = cal.features.map((feature, i) => {
    const shap = (cal.coef[i] * (x[i] - cal.mean[i])) / cal.scale[i];
    return { feature, value: x[i], shap, label: describe(feature, x[i]) };
  }).sort((a, b) => Math.abs(b.shap) - Math.abs(a.shap));
  const z = cal.intercept + contributions.reduce((s, c) => s + c.shap, 0);
  const score = calibrate(cal, z);
  const cappedEmpty = (["street_name", "house", "apartment", "city"] as const).every((k) => r[k] == null);
  let reproduced = Math.round(Math.max(0.01, Math.min(0.99, score)) * 100) / 100;
  if (cappedEmpty) reproduced = Math.min(reproduced, 0.08);
  return {
    base: calibrate(cal, cal.intercept),
    score,
    reproduced,
    exact: Math.abs(reproduced - r.confidence) < 0.005,
    cappedEmpty,
    contributions,
  };
}

/* ------------------------------------------------------------------ human-readable feature names */

const FIELD_RU: Record<string, string> = {
  province: "область", city: "город", street_name: "улица", house: "дом", apartment: "квартира",
  entrance: "подъезд", floor: "этаж", district: "район", living_area: "жилой массив",
  street_type: "тип улицы", landmark: "ориентир",
};
const FLAG_RU: Record<string, string> = {
  no_street: "улица не найдена",
  no_house: "дом не найден",
  street_without_type: "улица без типа",
  house_without_keyword: "номер дома без слова «дом»",
  house_ambiguous: "номер дома неоднозначен",
  ambiguous_number_binding: "число не привязано к полю",
  ambiguous_p_as_apartment: "«п» прочитано как квартира",
  ambiguous_p_as_entrance: "«п» прочитано как подъезд",
  leftover_word: "лишние слова в строке",
  leftover_number: "лишние числа в строке",
  multiple_street_types: "несколько типов улицы",
  microdistrict_as_living_area: "микрорайон записан как жилой массив",
  city_kk_only: "город только на казахском",
  keyword_without_number: "ключевое слово без номера",
  duplicate_house: "дом указан дважды",
  duplicate_apartment: "квартира указана дважды",
  house_has_building_part: "номер дома с корпусом",
  district_kw_without_name: "«район» без названия",
  city_duplicate: "город указан дважды",
  postcode: "в строке есть индекс",
  living_area_from_gazetteer: "жилой массив подтверждён OSM",
  street_in_gazetteer: "улица есть в справочнике OSM",
  street_not_in_gazetteer: "улицы нет в справочнике OSM",
  district_from_osm: "район подставлен из OSM",
  arb_province_not_canonical: "область не в каноничной форме",
};
const OUTCOME_RU: Record<string, string> = {
  agree: "правила и модель согласны",
  rule_only: "нашли только правила",
  model_only: "нашла только модель",
  model_wins: "модель переспорила правила",
  rule_wins: "правила переспорили модель",
  model_rejected: "ответ модели отклонён",
  overlap_dropped: "пересечение спанов отброшено",
};
const OUTCOME_NOT_RU: Record<string, string> = {
  agree: "правила и модель не совпали",
  rule_only: "нашли не только правила",
  model_only: "нашла не только модель",
  model_wins: "модель не спорила с правилами",
  rule_wins: "правила не спорили с моделью",
  model_rejected: "ответ модели не отклонялся",
  overlap_dropped: "пересечений спанов не было",
};
const HAS_RU: Record<string, [string, string]> = {
  province: ["Область найдена", "Область не найдена"],
  city: ["Город найден", "Город не найден"],
  street_name: ["Улица найдена", "Улица не найдена"],
  house: ["Дом найден", "Дом не найден"],
  apartment: ["Квартира найдена", "Квартира не найдена"],
};
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const num = (v: number) => v.toFixed(2).replace(".", ",");

export function describe(feature: string, x: number): string {
  const [kind, a, b] = feature.split(":");
  if (kind === "flag") {
    const t = FLAG_RU[a] ?? a;
    if (a === "leftover_word" || a === "leftover_number") return x ? `${cap(t)}: ${x}` : `Нет: ${t}`;
    return x ? cap(t) : `Нет признака: ${t}`;
  }
  if (kind === "arb") return `${cap(FIELD_RU[a] ?? a)}: ${(x ? OUTCOME_RU : OUTCOME_NOT_RU)[b] ?? b}`;
  if (kind === "prob") return x ? `Модель уверена в поле «${FIELD_RU[a] ?? a}» на ${num(x)}` : `Модель не нашла поле «${FIELD_RU[a] ?? a}»`;
  if (kind === "has") return HAS_RU[a]?.[x ? 0 : 1] ?? `${a}: ${x}`;
  if (feature === "n_tokens") return `Токенов в строке: ${x}`;
  if (feature === "has_latin") return x ? "В строке есть латиница" : "В строке нет латиницы";
  if (feature === "has_slash") return x ? "В строке есть «/»" : "В строке нет «/»";
  if (feature === "model:min_prob_address") return `Минимальная уверенность модели по адресу: ${num(x)}`;
  if (feature === "model:mean_prob_address") return `Средняя уверенность модели по адресу: ${num(x)}`;
  if (feature === "model:n_disagree") return `Споров правил и модели: ${x}`;
  if (feature === "model:used") return x ? "Работали правила и модель" : "Работали только правила";
  return feature;
}

/** Parser flags that are not model features, for filters and chips. */
const MORE_FLAGS_RU: Record<string, string> = {
  alias_in_brackets: "название в скобках",
  city_from_osm_chunk: "город найден по фрагменту OSM",
  city_fuzzy_match: "город найден с опечаткой",
  city_kk_duplicate: "город продублирован на казахском",
  correction_applied: "применена правка оператора",
  dash_apartment: "квартира через дефис",
  dash_separated: "части адреса через дефис",
  door_code: "в строке код домофона",
  empty: "пустая строка",
  floor_of_total: "этаж в виде «5/9»",
  glued_dom_split: "«дом» отделён от номера",
  glued_keyword_split: "ключевое слово отделено от номера",
  house_letter_dashed: "литера дома через дефис",
  house_letter_spaced: "литера дома через пробел",
  house_range: "диапазон номеров дома",
  layout_fixed: "исправлена раскладка клавиатуры",
  layout_fixed_whole: "вся строка в другой раскладке",
  microdistrict_number_in_name: "номер микрорайона в названии",
  phone: "в строке телефон",
  po_box: "абонентский ящик",
  slash_apartment: "квартира через «/»",
  slash_separated: "части адреса через «/»",
  street_from_leftover: "улица из оставшихся слов",
};

/** Short operator-facing names for parser flags (filters, chips). */
export const flagLabel = (flag: string) => {
  if (FLAG_RU[flag]) return cap(FLAG_RU[flag]);
  if (MORE_FLAGS_RU[flag]) return cap(MORE_FLAGS_RU[flag]);
  const m = /^arb_(.+)_(agree|rule_only|model_only|model_wins|rule_wins|model_rejected|overlap_dropped)$/.exec(flag);
  if (m) return `${cap(FIELD_RU[m[1]] ?? m[1])}: ${OUTCOME_RU[m[2]]}`;
  return flag;
};
