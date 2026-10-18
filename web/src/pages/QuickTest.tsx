import { useState } from "react";
import { Mascot, MascotAssistant, type MascotState } from "../components/Mascot";
import { Chip, ConfChip, ExplainDetails, Highlighted, IconZap } from "../components/ui";
import { FIELDS, FIELD_LABELS, type RecordOut } from "../types";

const EXAMPLES = [
  "Туран 55/6 магазин береке 4подьезд 9этаж",
  "Нурсити 2 микрорайон 11дом 5 подьезд 239 кв",
  "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
  "66квартал 12дом 69квартира",
];

export function QuickTest({ mascot, parseOne, onAdd }: {
  mascot: MascotState;
  parseOne: (raw: string) => Promise<RecordOut | null>;
  onAdd: (r: RecordOut) => void;
}) {
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

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Быстрый тест</p>
          <h1 className="title"><span className="dim">Одна строка.</span><br />Сразу все поля и объяснение.</h1>
        </div>
        <MascotAssistant state={mascot} />
      </div>

      <article className="card quick">
        <p className="eyebrow muted">Исходный адрес</p>
        <form className="quick-form" onSubmit={(e) => { e.preventDefault(); run(); }}>
          <input className="field" value={value} onChange={(e) => setValue(e.target.value)} placeholder="город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70" spellCheck={false} aria-label="Исходный адрес" />
          <button type="submit" className="btn btn-primary" disabled={busy || !value.trim()}><IconZap />{busy ? "Разбираю…" : "Разобрать"}</button>
        </form>
        <div className="examples">
          {EXAMPLES.map((ex) => <button key={ex} type="button" className="example" title={ex} onClick={() => run(ex)}>{ex}</button>)}
        </div>

        {result && (
          <div className="result">
            <div className="result-head">
              <Mascot mood={result.rec.confidence < 0.7 ? "confused" : "happy"} />
              <ConfChip c={result.rec.confidence} />
              <Chip status={result.rec.confidence < 0.7 ? "flag" : "ok"}>{result.rec.confidence < 0.7 ? "на проверку" : "готово к доставке"}</Chip>
              <button type="button" className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} disabled={result.added}
                onClick={() => { onAdd(result.rec); setResult({ ...result, added: true }); }}>
                {result.added ? "✓ в таблице" : "Добавить в таблицу"}
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
  );
}
