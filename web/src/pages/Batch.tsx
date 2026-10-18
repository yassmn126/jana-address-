import { useState } from "react";
import { MascotAssistant, type MascotState } from "../components/Mascot";
import { ConfChip, ExplainDetails, Highlighted, IconDownload, IconUpload } from "../components/ui";
import { FIELDS, FIELD_LABELS, type Field, type RecordOut } from "../types";

export type SortKey = "conf-asc" | "conf-desc" | "input";

export interface BatchProps {
  rows: RecordOut[];
  visible: RecordOut[];
  busy: boolean;
  error: string | null;
  mascot: MascotState;
  sort: SortKey;
  setSort: (s: SortKey) => void;
  maxConf: number;
  setMaxConf: (v: number) => void;
  query: string;
  setQuery: (q: string) => void;
  paste: string;
  setPaste: (p: string) => void;
  selected: string | null;
  setSelected: (id: string) => void;
  onFile: (f: File) => void;
  onPaste: () => void;
  onRawMouseUp: (id: string) => void;
  update: (id: string, field: Field, value: string | null) => void;
  accept: (r: RecordOut) => void;
  exportJson: () => void;
}

export function Batch(p: BatchProps) {
  const [drag, setDrag] = useState(false);
  const sel = p.rows.find((r) => r.id === p.selected);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Пакетная обработка</p>
          <h1 className="title"><span className="dim">Файл целиком.</span><br />Ответ на каждую запись.</h1>
        </div>
        <MascotAssistant state={p.mascot} />
      </div>

      {/* Loader: file or pasted lines */}
      <section className="card loader">
        <label className={`drop ${drag ? "is-drag" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) p.onFile(f); }}>
          <input type="file" accept=".json,application/json" onChange={(e) => e.target.files?.[0] && p.onFile(e.target.files[0])} />
          <span className="drop-icon"><IconUpload /></span>
          <span>Перетащите <span className="mono" style={{ color: "#d3ee7c" }}>sample_input.json</span> или нажмите, чтобы выбрать</span>
          <span className="muted" style={{ fontSize: 13 }}>JSON-массив <span className="mono">{"{ id, raw_address }"}</span> или <span className="mono">{"{ records: [...] }"}</span></span>
        </label>
        <div className="paste">
          <textarea className="field" placeholder={"или вставьте адреса, по одному в строке\nТуран 55/6 магазин береке 4подьезд 9этаж"} value={p.paste} onChange={(e) => p.setPaste(e.target.value)} rows={3} />
          <div><button type="button" className="btn btn-primary" onClick={p.onPaste} disabled={p.busy || !p.paste.trim()}>Разобрать</button></div>
        </div>
      </section>

      {p.error && <div className="error" role="alert">{p.error}</div>}

      {/* Controls */}
      <section className="card controls">
        <label>Сортировка
          <select className="field" style={{ width: "auto" }} value={p.sort} onChange={(e) => p.setSort(e.target.value as SortKey)}>
            <option value="conf-asc">confidence ↑ (очередь на проверку)</option>
            <option value="conf-desc">confidence ↓</option>
            <option value="input">как во входе</option>
          </select>
        </label>
        <label>Показать confidence ≤ <b>{p.maxConf.toFixed(2)}</b>
          <input type="range" min={0} max={1} step={0.01} value={p.maxConf} onChange={(e) => p.setMaxConf(Number(e.target.value))} />
        </label>
        <input className="field search" placeholder="поиск по строке / id" value={p.query} onChange={(e) => p.setQuery(e.target.value)} />
        <div className="hotkeys" aria-label="Горячие клавиши">
          <span>Выделите текст в адресе и нажмите:</span>
          <span><kbd>S</kbd> улица</span><span><kbd>H</kbd> дом</span><span><kbd>A</kbd> кв</span><span><kbd>E</kbd> подъезд</span>
          <span><kbd>F</kbd> этаж</span><span><kbd>D</kbd> район</span><span><kbd>L</kbd> массив</span><span><kbd>M</kbd> ориентир</span>
          <span><kbd>C</kbd> город</span><span><kbd>Enter</kbd> принять</span><span><kbd>J</kbd>/<kbd>K</kbd> след/пред</span>
        </div>
      </section>

      <div className="split">
        {/* Table card · slide 03 */}
        <article className="card table-card">
          <div className="table-head">
            <div>
              <p className="eyebrow">Результаты</p>
              <p className="muted" style={{ margin: "8px 0 0", fontSize: 14 }}>{p.visible.length} из {p.rows.length} записей</p>
            </div>
            <button type="button" className="btn btn-primary" onClick={p.exportJson} disabled={!p.rows.length}><IconDownload />Экспорт JSON</button>
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>#</th><th className="raw">raw_address</th>
                  {FIELDS.map((f) => <th key={f}>{FIELD_LABELS[f]}</th>)}
                  <th>conf</th><th></th>
                </tr>
              </thead>
              <tbody>
                {p.visible.map((r, i) => (
                  <tr key={r.id} className={`${r.id === p.selected ? "sel" : ""} ${r.confidence < 0.7 && !r.reviewed ? "low" : ""} ${r.reviewed ? "reviewed" : ""}`} onClick={() => p.setSelected(r.id)}>
                    <td className="idx">{i + 1}</td>
                    <td className="raw" onMouseUp={() => p.onRawMouseUp(r.id)}><Highlighted r={r} /></td>
                    {FIELDS.map((f) => (
                      <td key={f} className={r.provenance?.[f]?.startsWith("gazetteer") ? "gaz" : ""} title={r.provenance?.[f] ?? ""}>
                        <input value={r[f] ?? ""} onChange={(e) => p.update(r.id, f, e.target.value)} placeholder="—" aria-label={`${FIELD_LABELS[f]} · ${r.id}`} />
                      </td>
                    ))}
                    <td><ConfChip c={r.confidence} /></td>
                    <td>
                      <button type="button" className={`btn btn-sm ${r.reviewed ? "btn-ghost" : "btn-primary"}`} onClick={(e) => { e.stopPropagation(); p.accept(r); }}>
                        {r.reviewed ? "✓ принято" : "принять"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!p.rows.length && <p className="empty">Загрузите файл — таблица появится здесь.</p>}
          </div>
        </article>

        {/* Explain */}
        <aside className="card explain">
          <p className="eyebrow">Explain</p>
          {!sel && <p className="muted" style={{ marginTop: 12 }}>Выберите запись.</p>}
          {sel && (
            <>
              <p className="rawline"><Highlighted r={sel} /></p>
              <div style={{ marginTop: 10 }}><ConfChip c={sel.confidence} /></div>
              <ExplainDetails r={sel} fieldLabels={FIELD_LABELS} />
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
