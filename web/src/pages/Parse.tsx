import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot } from "../components/Mascot";
import { AiCheck, ConfidenceCard, ConfidenceExplain, GeoCard } from "../components/record";
import { Chip, ConfChip, Highlighted, IconCheck, IconZap } from "../components/ui";
import { fmtScore } from "../format";
import { PATHS } from "../routes";
import { useSession } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import { FIELDS, FIELD_LABELS, JURY_KEYS, SPAN_CLASS, provenanceLabel, type RecordOut } from "../types";

const EXAMPLES = [
  "Туран 55/6 магазин береке 4подьезд 9этаж",
  "Нурсити 2 микрорайон 11дом 5 подьезд 239 кв",
  "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
  "66квартал 12дом 69квартира",
  "Астана, улица Кенесары 40, кв 12",
];
const HISTORY_KEY = "jana-address/parse-history";
const HISTORY_MAX = 8;

type Entry = { raw: string; confidence: number };

function readHistory(): Entry[] {
  try {
    const v = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
    return Array.isArray(v) ? v.slice(0, HISTORY_MAX) : [];
  } catch { return []; }
}

export function Parse() {
  const { parseOne, addRow, rows } = useSession();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<RecordOut | null>(null);
  const [history, setHistory] = useState<Entry[]>(readHistory);
  const [copied, setCopied] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const added = rec ? rows.some((r) => r.id === rec.id) : false;

  useEffect(() => {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch { /* storage blocked */ }
  }, [history]);

  const run = async (raw = value) => {
    const line = raw.trim();
    if (!line || busy) return;
    setValue(line);
    setBusy(true);
    setCopied(false);
    try {
      const r = await parseOne(line);
      if (r) {
        setRec(r);
        setHistory((h) => [{ raw: line, confidence: r.confidence }, ...h.filter((e) => e.raw !== line)].slice(0, HISTORY_MAX));
      }
    } finally {
      setBusy(false);
      input.current?.select();
    }
  };

  const copy = async () => {
    if (!rec) return;
    const out = Object.fromEntries(JURY_KEYS.map((k) => [k, rec[k as keyof RecordOut] ?? null]));
    try {
      await navigator.clipboard.writeText(JSON.stringify(out, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard blocked */ }
  };

  const low = rec ? rec.confidence < REVIEW_THRESHOLD : false;

  return (
    <>
      <PageHeader title="Разбор строки" />
      <div className="content">
        <section className="card parse-hero">
          <form className="parse-form" onSubmit={(e) => { e.preventDefault(); run(); }}>
            <input ref={input} className="field" value={value} onChange={(e) => setValue(e.target.value)} spellCheck={false}
              aria-label="Адрес одной строкой" autoFocus />
            <button type="submit" className="btn btn-primary" disabled={busy || !value.trim()}><IconZap />{busy ? "Разбираю…" : "Разобрать"}</button>
          </form>
          <div className="parse-suggest">
            {history.length > 0 && (
              <div className="parse-row">
                <span className="eyebrow">Недавние</span>
                {history.map((h) => (
                  <button key={h.raw} type="button" className="example" title={h.raw} onClick={() => run(h.raw)}>
                    <i className={`dot ${h.confidence < REVIEW_THRESHOLD ? "is-flag" : "is-ok"}`} aria-hidden="true" />{h.raw}
                  </button>
                ))}
              </div>
            )}
            <div className="parse-row">
              <span className="eyebrow">Примеры</span>
              {EXAMPLES.map((ex) => <button key={ex} type="button" className="example" title={ex} onClick={() => run(ex)}>{ex}</button>)}
            </div>
          </div>
        </section>

        {!rec ? (
          <section className="card parse-empty">
            <Mascot pose="laptop" className="parse-empty-mascot" />
            <h2>Введите адрес и нажмите Enter</h2>
          </section>
        ) : (
          <div className="parse-grid" key={rec.id}>
            <article className="card parse-result">
              <div className="parse-result-head">
                <Mascot mood={low ? "confused" : "happy"} className="result-mascot" />
                <div className="parse-result-title">
                  <div className="parse-chips">
                    <ConfChip c={rec.confidence} />
                    <Chip status={low ? "flag" : "ok"} strong={!low}>{low ? "на проверку" : "готово к доставке"}</Chip>
                  </div>
                  <p className="parse-raw"><Highlighted r={rec} /></p>
                </div>
              </div>

              <div className="kv-grid">
                {FIELDS.map((f) => {
                  const v = rec[f];
                  const fc = rec.field_confidence?.[f];
                  return (
                    <div key={f} className={`kv-cell ${v ? "" : "is-empty"}`}>
                      <span className="ff-label">
                        <i className={`ff-dot ${SPAN_CLASS[f.toUpperCase()] ?? ""}`} aria-hidden="true" />
                        {FIELD_LABELS[f]}
                        {fc != null && v && <span className={`ff-conf ${fc < REVIEW_THRESHOLD ? "is-flag" : ""}`}>{fmtScore(fc)}</span>}
                      </span>
                      <span className="kv-value">{v ?? <span className="nil">null</span>}</span>
                      {v && <span className="ff-foot">{provenanceLabel(rec.provenance?.[f]) ?? ""}</span>}
                    </div>
                  );
                })}
              </div>

              <div className="parse-actions">
                <button type="button" className="btn btn-ghost" onClick={copy}>{copied ? <><IconCheck />Скопировано</> : "Копировать JSON"}</button>
                {added
                  ? <Link className="btn btn-ghost" to={PATHS.record(rec.id)}><IconCheck />В записях</Link>
                  : <button type="button" className="btn btn-primary" onClick={() => addRow(rec)}>Добавить в записи</button>}
              </div>
            </article>

            <aside className="parse-side">
              <div className="insp-metrics">
                <ConfidenceCard r={rec} />
                <GeoCard r={rec} />
              </div>
              <section className="card parse-why">
                <p className="eyebrow">Почему {fmtScore(rec.confidence)} · SHAP</p>
                <ConfidenceExplain r={rec} />
              </section>
              <AiCheck r={rec} />
            </aside>
          </div>
        )}
      </div>
    </>
  );
}
