import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "../components/layout/PageHeader";
import { ConfChip, ExplainDetails, FileButton, Highlighted, IconDownload, IconUpload, useDropTarget } from "../components/ui";
import { count, RECORDS } from "../format";
import { PATHS } from "../routes";
import { useSession, type SortKey } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import { FIELDS, FIELD_LABELS, type Field } from "../types";

/** Hotkeys for click-to-relabel: select text inside the raw string, press a key. */
const HOTKEYS: Record<string, Field> = {
  s: "street_name", h: "house", a: "apartment", e: "entrance", f: "floor",
  d: "district", l: "living_area", m: "landmark", c: "city", p: "province",
};

export function Records() {
  const s = useSession();
  const { id } = useParams();
  const navigate = useNavigate();
  const [paste, setPaste] = useState("");
  const drop = useDropTarget(s.loadFile);
  const selectionRef = useRef<{ id: string; text: string } | null>(null);
  const selected = id ?? null;
  const select = (rid: string) => navigate(PATHS.record(rid), { replace: true });

  const visible = useMemo(() => {
    let v = s.rows.filter((r) => r.confidence <= s.maxConf);
    if (s.query) {
      const q = s.query.toLowerCase();
      v = v.filter((r) => r.raw_address.toLowerCase().includes(q) || r.id.toLowerCase().includes(q));
    }
    if (s.sort === "conf-asc") v = [...v].sort((a, b) => a.confidence - b.confidence);
    if (s.sort === "conf-desc") v = [...v].sort((a, b) => b.confidence - a.confidence);
    return v;
  }, [s.rows, s.maxConf, s.query, s.sort]);

  const sel = s.rows.find((r) => r.id === selected);

  const onRawMouseUp = (rid: string) => {
    const text = window.getSelection()?.toString().trim();
    selectionRef.current = text ? { id: rid, text } : null;
    if (rid !== selected) select(rid);
  };

  useEffect(() => {
    const h = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const field = HOTKEYS[ev.key.toLowerCase()];
      const cur = selectionRef.current;
      if (field && cur) {
        s.update(cur.id, field, cur.text);
        ev.preventDefault();
      } else if (ev.key === "Enter" && sel) {
        s.accept(sel);
      } else if ((ev.key === "j" || ev.key === "k") && visible.length) {
        const idx = visible.findIndex((x) => x.id === selected);
        const next = visible[Math.min(visible.length - 1, Math.max(0, idx + (ev.key === "j" ? 1 : -1)))];
        if (next) select(next.id);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  return (
    <>
      <PageHeader title="Записи" meta={s.rows.length ? `${visible.length} из ${count(s.rows.length, RECORDS)} · на проверку ${s.stats.queue}` : undefined}>
        <FileButton onFile={s.loadFile} className="btn btn-ghost" disabled={s.busy}><IconUpload />Загрузить JSON</FileButton>
        <button type="button" className="btn btn-primary" onClick={s.exportJson} disabled={!s.rows.length}><IconDownload />output.json</button>
      </PageHeader>

      <div className="content">
        {!s.rows.length && (
          <section className="card loader">
            <div className={`drop ${drop.over ? "is-drag" : ""}`} {...drop.handlers}>
              <span className="drop-icon"><IconUpload /></span>
              <span>Перетащите <span className="mono">sample_input.json</span> сюда</span>
              <FileButton onFile={s.loadFile} className="btn btn-ghost btn-sm">Выбрать файл</FileButton>
            </div>
            <form className="paste" onSubmit={(e) => { e.preventDefault(); s.loadText(paste); setPaste(""); }}>
              <textarea className="field" aria-label="Адреса, по одному в строке" value={paste} onChange={(e) => setPaste(e.target.value)} rows={3} />
              <div><button type="submit" className="btn btn-primary" disabled={s.busy || !paste.trim()}>Разобрать список</button></div>
            </form>
          </section>
        )}

        {s.error && <div className="error" role="alert">{s.error}</div>}

        {s.rows.length > 0 && (
          <>
            <section className="card controls">
              <label>Сортировка
                <select className="field" style={{ width: "auto" }} value={s.sort} onChange={(e) => s.setSort(e.target.value as SortKey)}>
                  <option value="conf-asc">уверенность ↑</option>
                  <option value="conf-desc">уверенность ↓</option>
                  <option value="input">как во входе</option>
                </select>
              </label>
              <label>Уверенность ≤ <b>{s.maxConf.toFixed(2).replace(".", ",")}</b>
                <input type="range" min={0} max={1} step={0.01} value={s.maxConf} onChange={(e) => s.setMaxConf(Number(e.target.value))} />
              </label>
              <input className="field search-field" type="search" aria-label="Поиск по адресу или ID" value={s.query} onChange={(e) => s.setQuery(e.target.value)} />
              <div className="hotkeys" aria-label="Горячие клавиши">
                <span><kbd>S</kbd> улица</span><span><kbd>H</kbd> дом</span><span><kbd>A</kbd> кв</span><span><kbd>E</kbd> подъезд</span>
                <span><kbd>F</kbd> этаж</span><span><kbd>D</kbd> район</span><span><kbd>L</kbd> массив</span><span><kbd>M</kbd> ориентир</span>
                <span><kbd>C</kbd> город</span><span><kbd>Enter</kbd> принять</span><span><kbd>J</kbd>/<kbd>K</kbd> следующая</span>
              </div>
            </section>

            <div className="split">
              <article className="card table-card">
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
                      {visible.map((r, i) => (
                        <tr key={r.id} className={`${r.id === selected ? "sel" : ""} ${r.confidence < REVIEW_THRESHOLD && !r.reviewed ? "low" : ""} ${r.reviewed ? "reviewed" : ""}`} onClick={() => r.id !== selected && select(r.id)}>
                          <td className="idx">{i + 1}</td>
                          <td className="raw" onMouseUp={() => onRawMouseUp(r.id)}><Highlighted r={r} /></td>
                          {FIELDS.map((f) => (
                            <td key={f} className={r.provenance?.[f]?.startsWith("gazetteer") ? "gaz" : ""} title={r.provenance?.[f] ?? ""}>
                              <input value={r[f] ?? ""} onChange={(e) => s.update(r.id, f, e.target.value)} aria-label={`${FIELD_LABELS[f]} · ${r.id}`} />
                            </td>
                          ))}
                          <td><ConfChip c={r.confidence} /></td>
                          <td>
                            <button type="button" className={`btn btn-sm ${r.reviewed ? "btn-ghost" : "btn-primary"}`} onClick={(e) => { e.stopPropagation(); s.accept(r); }}>
                              {r.reviewed ? "✓ принято" : "принять"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>

              <aside className="card explain">
                <p className="eyebrow">Разбор записи</p>
                {!sel && <p className="muted">Выберите строку в таблице.</p>}
                {sel && (
                  <>
                    <p className="rawline"><Highlighted r={sel} /></p>
                    <div style={{ marginTop: 10 }}><ConfChip c={sel.confidence} /></div>
                    <ExplainDetails r={sel} fieldLabels={FIELD_LABELS} />
                  </>
                )}
              </aside>
            </div>
          </>
        )}
      </div>
    </>
  );
}
