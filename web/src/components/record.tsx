import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { scanConflicts } from "../api";
import { fmtScore } from "../format";
import { providerLabel, savedProvider } from "../lib/ai";
import { explain, loadCalibrator, type Explanation } from "../lib/explain";
import { REVIEW_THRESHOLD } from "../stats";
import { PRECISION_LABELS, type Field, type Precision, type RecordOut } from "../types";
import { IconCompare, IconExternal, IconInfo, IconMapPin } from "./ui";

/** Big score, bar with the 0.70 threshold mark. */
export function ConfidenceCard({ r }: { r: RecordOut }) {
  const low = r.confidence < REVIEW_THRESHOLD;
  return (
    <div className="insp-metric">
      <p className="eyebrow">Уверенность</p>
      <p className={`insp-big ${low ? "is-flag" : "is-ok"}`}>{fmtScore(r.confidence)}</p>
      <span className="conf-track" aria-hidden="true">
        <span className={`conf-fill ${low ? "is-flag" : ""}`} style={{ width: `${Math.round(r.confidence * 100)}%` }} />
        <span className="conf-mark" style={{ left: `${REVIEW_THRESHOLD * 100}%` }} />
      </span>
      <p className="insp-note">{low ? "ниже порога 0,70" : "выше порога 0,70"}</p>
    </div>
  );
}

/** Geocoder answer from the explain response: precision, matched object, coordinates, map link. */
export function GeoCard({ r }: { r: RecordOut }) {
  const geo = r.geo;
  const precision = geo?.precision as Precision | undefined;
  return (
    <div className="insp-metric">
      <p className="eyebrow">Геокодирование</p>
      {geo ? (
        <>
          <p className="insp-geo"><IconMapPin />{precision && PRECISION_LABELS[precision] ? PRECISION_LABELS[precision] : geo.precision}{geo.matched ? ` · ${geo.matched}` : ""}</p>
          <p className="insp-note mono">{geo.lat.toFixed(5)}, {geo.lon.toFixed(5)}</p>
          <a className="link" href={`https://www.openstreetmap.org/?mlat=${geo.lat}&mlon=${geo.lon}#map=17/${geo.lat}/${geo.lon}`} target="_blank" rel="noreferrer">
            Открыть карту<IconExternal />
          </a>
        </>
      ) : geo === null ? (
        <>
          <p className="insp-geo is-flag"><IconMapPin />Не найден в OSM</p>
          <p className="insp-note">нет города или населённого пункта</p>
        </>
      ) : <p className="insp-note">нет данных</p>}
    </div>
  );
}

/**
 * «Почему 0,43»: exact SHAP values of the confidence model (lib/explain.ts), as a diverging bar list.
 * Bars are contributions to the logit; the base is the average record.
 */
export function ConfidenceExplain({ r }: { r: RecordOut }) {
  const [ex, setEx] = useState<Explanation | null | undefined>(undefined);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let alive = true;
    setAll(false);
    loadCalibrator().then((cal) => alive && setEx(explain(r, cal))).catch(() => alive && setEx(null));
    return () => { alive = false; };
  }, [r]);

  if (ex === undefined) return null;
  if (ex === null) return <p className="insp-note">Нет разбора модели для этой записи: загрузите её заново.</p>;

  const shown = ex.contributions.filter((c) => Math.abs(c.shap) >= 0.005);
  const list = all ? shown : shown.slice(0, 8);
  const max = Math.max(...list.map((c) => Math.abs(c.shap)), 0.01);
  const rest = shown.length - list.length;

  return (
    <div className="shap">
      <div className="shap-summary">
        <span><span className="muted">средняя запись</span> <b className="mono">{fmtScore(ex.base)}</b></span>
        <span className="shap-arrow" aria-hidden="true">→</span>
        <span><span className="muted">эта запись</span> <b className={`mono ${r.confidence < REVIEW_THRESHOLD ? "is-flag" : "is-ok"}`}>{fmtScore(r.confidence)}</b></span>
        {!ex.exact && <span className="chip chip-warn" title={`Модель этой сборки даёт ${fmtScore(ex.reproduced)}`}><i className="chip-dot" />модель API новее</span>}
      </div>
      {ex.cappedEmpty && (
        <p className="shap-cap"><IconInfo />Адрес не распознан: улица, дом, квартира и город пусты, поэтому уверенность ограничена 0,08.</p>
      )}
      <ul className="shap-list">
        {list.map((c) => (
          <li key={c.feature} className={c.shap < 0 ? "is-neg" : "is-pos"} title={`${c.feature} = ${Number.isInteger(c.value) ? c.value : c.value.toFixed(3)} · ${c.shap > 0 ? "+" : ""}${c.shap.toFixed(3)}`}>
            <span className="shap-label">{c.label}</span>
            <span className="shap-bar" aria-hidden="true">
              <span style={{ width: `${(Math.abs(c.shap) / max) * 50}%` }} />
            </span>
            <span className="shap-val mono">{c.shap > 0 ? "+" : "−"}{Math.abs(c.shap).toFixed(2).replace(".", ",")}</span>
          </li>
        ))}
      </ul>
      <div className="shap-foot">
        <span className="muted">вклад в логит · <span className="is-pos-text">повышает</span> · <span className="is-neg-text">понижает</span></span>
        {(rest > 0 || all) && (
          <button type="button" className="link-btn" onClick={() => setAll((v) => !v)}>{all ? "Главные 8" : `Ещё ${rest}`}</button>
        )}
      </div>
    </div>
  );
}

/** One-record cross-check with the external labeler; a disagreement lands in the conflict queue. */
export function AiCheck({ r }: { r: RecordOut }) {
  const [state, setState] = useState<{ status: "idle" | "busy" | "agree" | "queued" | "error"; text?: string; id?: string; fields?: Field[] }>({ status: "idle" });
  useEffect(() => { setState({ status: "idle" }); }, [r.id]);
  const { provider, model } = savedProvider();

  const run = async () => {
    setState({ status: "busy" });
    try {
      const res = await scanConflicts([{ id: r.id, raw_address: r.raw_address }], provider, model);
      if (res.needs_review && res.conflict_ids[0]) setState({ status: "queued", id: res.conflict_ids[0] });
      else setState({ status: "agree" });
    } catch (e) {
      setState({ status: "error", text: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    <div className="ai-check">
      <div className="ai-check-row">
        <span className="scan-icon"><IconCompare /></span>
        <div className="ai-check-text">
          <p className="ai-check-title">Сверка с внешним ИИ</p>
          <p className="insp-note">{providerLabel(provider)}{model ? ` · ${model}` : ""}</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={run} disabled={state.status === "busy"}>
          {state.status === "busy" ? "Сверяю…" : "Сверить"}
        </button>
      </div>
      {state.status === "agree" && <p className="ai-check-result is-ok">ИИ разметил строку так же.</p>}
      {state.status === "queued" && state.id && (
        <p className="ai-check-result is-flag">Есть расхождение, запись в очереди конфликтов. <Link className="link" to={`/conflicts/${encodeURIComponent(state.id)}`}>Открыть</Link></p>
      )}
      {state.status === "error" && <p className="ai-check-result is-flag">{state.text}</p>}
    </div>
  );
}

