import { useState } from "react";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot } from "../components/Mascot";
import { Chip, ConfChip, ExplainDetails, Highlighted, IconZap } from "../components/ui";
import { useSession } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import { FIELDS, FIELD_LABELS, type RecordOut } from "../types";

const EXAMPLES = [
  "Туран 55/6 магазин береке 4подьезд 9этаж",
  "Нурсити 2 микрорайон 11дом 5 подьезд 239 кв",
  "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
  "66квартал 12дом 69квартира",
];

export function QuickTest() {
  const { parseOne, addRow } = useSession();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ rec: RecordOut; added: boolean } | null>(null);

  const run = async (raw = value) => {
    const line = raw.trim();
    if (!line) return;
    setValue(line);
    setBusy(true);
    try {
      const rec = await parseOne(line);
      setResult(rec ? { rec, added: false } : null);
    } finally {
      setBusy(false);
    }
  };

  const low = result ? result.rec.confidence < REVIEW_THRESHOLD : false;

  return (
    <>
      <PageHeader title="Разбор строки" />
      <div className="content">
        <article className="card quick">
          <form className="quick-form" onSubmit={(e) => { e.preventDefault(); run(); }}>
            <input className="field" value={value} onChange={(e) => setValue(e.target.value)} spellCheck={false} aria-label="Адрес одной строкой" autoFocus />
            <button type="submit" className="btn btn-primary" disabled={busy || !value.trim()}><IconZap />{busy ? "Разбираю…" : "Разобрать"}</button>
          </form>
          <div className="examples">
            {EXAMPLES.map((ex) => <button key={ex} type="button" className="example" title={ex} onClick={() => run(ex)}>{ex}</button>)}
          </div>

          {result && (
            <div className="result">
              <div className="result-head">
                <Mascot mood={low ? "confused" : "happy"} className="result-mascot" />
                <ConfChip c={result.rec.confidence} />
                <Chip status={low ? "flag" : "ok"}>{low ? "на проверку" : "готово к доставке"}</Chip>
                <button type="button" className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} disabled={result.added}
                  onClick={() => { addRow(result.rec); setResult({ ...result, added: true }); }}>
                  {result.added ? "✓ в записях" : "Добавить в записи"}
                </button>
              </div>
              <p className="rawline"><Highlighted r={result.rec} /></p>
              <div className="kv">
                {FIELDS.map((f) => (
                  <div key={f} className="kv-row">
                    <span>{FIELD_LABELS[f]}</span>
                    <span>{result.rec[f] ?? <span className="nil">null</span>}</span>
                  </div>
                ))}
              </div>
              <ExplainDetails r={result.rec} fieldLabels={FIELD_LABELS} />
            </div>
          )}
        </article>
      </div>
    </>
  );
}
