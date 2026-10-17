import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { health, parseBatch, sendCorrection } from "./api";
import { FIELDS, FIELD_LABELS, JURY_KEYS, SPAN_CLASS, type Field, type RecordOut } from "./types";

/** Hotkeys for click-to-relabel: select text inside the raw string, press a key. */
const HOTKEYS: Record<string, Field> = {
  s: "street_name", h: "house", a: "apartment", e: "entrance", f: "floor",
  d: "district", l: "living_area", m: "landmark", c: "city", p: "province",
};

type SortKey = "conf-asc" | "conf-desc" | "input";

export default function App() {
  const [rows, setRows] = useState<RecordOut[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiOk, setApiOk] = useState<boolean | null>(null);
  const [sort, setSort] = useState<SortKey>("conf-asc");
  const [maxConf, setMaxConf] = useState(1);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [paste, setPaste] = useState("");
  const selectionRef = useRef<{ id: string; text: string } | null>(null);

  useEffect(() => {
    health().then((h) => setApiOk(h.status === "ok")).catch(() => setApiOk(false));
  }, []);

  // ---------------------------------------------------------------- loading
  const load = useCallback(async (records: { id: string; raw_address: string }[]) => {
    setBusy(true);
    setError(null);
    try {
      const out: RecordOut[] = [];
      for (let i = 0; i < records.length; i += 200) {
        out.push(...(await parseBatch(records.slice(i, i + 200), true)));
        setRows([...out]);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const onFile = async (file: File) => {
    const text = await file.text();
    let data: unknown = JSON.parse(text.replace(/^﻿/, ""));
    if (data && typeof data === "object" && "records" in (data as Record<string, unknown>)) data = (data as { records: unknown }).records;
    if (!Array.isArray(data)) return setError("JSON must be an array of {id, raw_address}");
    load(data.map((r: Record<string, unknown>, i) => ({ id: String(r.id ?? i), raw_address: String(r.raw_address ?? "") })));
  };

  const onPaste = () => {
    const lines = paste.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length) load(lines.map((l, i) => ({ id: `paste_${i + 1}`, raw_address: l })));
  };

  // ---------------------------------------------------------------- editing
  const update = (id: string, field: Field, value: string | null) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, [field]: value === "" ? null : value, edited: true } : r)));

  const accept = async (r: RecordOut) => {
    const fields = Object.fromEntries(FIELDS.map((f) => [f, r[f]])) as Record<string, string | null>;
    try {
      if (r.edited) await sendCorrection(r, fields);
      setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, reviewed: true } : x)));
    } catch (e) {
      setError(String(e));
    }
  };

  // click-to-relabel: remember the selection inside a raw cell; a hotkey assigns it
  const onRawMouseUp = (id: string) => {
    const sel = window.getSelection()?.toString().trim();
    selectionRef.current = sel ? { id, text: sel } : null;
    setSelected(id);
  };
  useEffect(() => {
    const h = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      const field = HOTKEYS[ev.key.toLowerCase()];
      const s = selectionRef.current;
      if (field && s) {
        update(s.id, field, s.text);
        ev.preventDefault();
      } else if (ev.key === "Enter" && selected) {
        const r = rows.find((x) => x.id === selected);
        if (r) accept(r);
      } else if ((ev.key === "j" || ev.key === "k") && visible.length) {
        const idx = visible.findIndex((x) => x.id === selected);
        const next = visible[Math.min(visible.length - 1, Math.max(0, idx + (ev.key === "j" ? 1 : -1)))];
        if (next) setSelected(next.id);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  // ---------------------------------------------------------------- derived
  const visible = useMemo(() => {
    let v = rows.filter((r) => r.confidence <= maxConf);
    if (query) {
      const q = query.toLowerCase();
      v = v.filter((r) => r.raw_address.toLowerCase().includes(q) || r.id.toLowerCase().includes(q));
    }
    if (sort === "conf-asc") v = [...v].sort((a, b) => a.confidence - b.confidence);
    if (sort === "conf-desc") v = [...v].sort((a, b) => b.confidence - a.confidence);
    return v;
  }, [rows, maxConf, query, sort]);

  const stats = useMemo(() => {
    const n = rows.length;
    const avg = n ? rows.reduce((s, r) => s + r.confidence, 0) / n : 0;
    const low = rows.filter((r) => r.confidence < 0.7).length;
    const fill = (f: Field) => (n ? Math.round((100 * rows.filter((r) => r[f]).length) / n) : 0);
    return { n, avg, low, fill, reviewed: rows.filter((r) => r.reviewed).length };
  }, [rows]);

  const exportJson = () => {
    const out = rows.map((r) => Object.fromEntries(JURY_KEYS.map((k) => [k, r[k as keyof RecordOut] ?? null])));
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "output.json";
    a.click();
  };

  const sel = rows.find((r) => r.id === selected);

  // ---------------------------------------------------------------- render
  return (
    <div className="app">
      <header>
        <h1>Jana Address <span className="muted">review</span></h1>
        <span className={`dot ${apiOk ? "ok" : apiOk === false ? "bad" : ""}`} title="API health" />
        <div className="stats">
          <span><b>{stats.n}</b> записей</span>
          <span>ср. confidence <b>{stats.avg.toFixed(2)}</b></span>
          <span className={stats.low ? "warn" : ""}><b>{stats.low}</b> ниже 0.70</span>
          <span><b>{stats.reviewed}</b> проверено</span>
          <span className="muted">заполнено: улица {stats.fill("street_name")}% · дом {stats.fill("house")}% · район {stats.fill("district")}%</span>
        </div>
        <button onClick={exportJson} disabled={!rows.length}>Экспорт JSON (X)</button>
      </header>

      <section className="loader">
        <label className="drop" onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}>
          <input type="file" accept=".json,application/json" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
          <span>Перетащите sample_input.json или нажмите, чтобы выбрать</span>
        </label>
        <div className="paste">
          <textarea placeholder={"или вставьте адреса, по одному в строке\nТуран 55/6 магазин береке 4подьезд 9этаж"} value={paste} onChange={(e) => setPaste(e.target.value)} rows={3} />
          <button onClick={onPaste} disabled={busy || !paste.trim()}>Разобрать</button>
        </div>
      </section>

      {error && <div className="error">{error}</div>}

      <section className="controls">
        <label>Сортировка
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="conf-asc">confidence ↑ (очередь на проверку)</option>
            <option value="conf-desc">confidence ↓</option>
            <option value="input">как во входе</option>
          </select>
        </label>
        <label>Показать confidence ≤ <b>{maxConf.toFixed(2)}</b>
          <input type="range" min={0} max={1} step={0.01} value={maxConf} onChange={(e) => setMaxConf(Number(e.target.value))} />
        </label>
        <input className="search" placeholder="поиск по строке / id" value={query} onChange={(e) => setQuery(e.target.value)} />
        <span className="muted hint">Выделите текст в адресе и нажмите: S улица · H дом · A кв · E подъезд · F этаж · D район · L массив · M ориентир · C город · Enter принять · J/K след/пред</span>
      </section>

      <div className="layout">
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
                <tr key={r.id} className={`${r.id === selected ? "sel" : ""} ${r.reviewed ? "reviewed" : ""}`} onClick={() => setSelected(r.id)}>
                  <td className="muted">{i + 1}</td>
                  <td className="raw" onMouseUp={() => onRawMouseUp(r.id)}><Highlighted r={r} /></td>
                  {FIELDS.map((f) => (
                    <td key={f} className={r.provenance?.[f]?.startsWith("gazetteer") ? "gaz" : ""} title={r.provenance?.[f] ?? ""}>
                      <input value={r[f] ?? ""} onChange={(e) => update(r.id, f, e.target.value)} placeholder="—" />
                    </td>
                  ))}
                  <td><ConfBadge c={r.confidence} /></td>
                  <td><button className="small" onClick={(e) => { e.stopPropagation(); accept(r); }}>{r.reviewed ? "✓" : "принять"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <p className="muted center">Загрузите файл — таблица появится здесь.</p>}
        </div>

        <aside className="explain">
          <h3>Explain</h3>
          {!sel && <p className="muted">Выберите запись.</p>}
          {sel && (
            <>
              <p className="rawline"><Highlighted r={sel} /></p>
              <h4>Токены</h4>
              <div className="tokens">
                {sel.tokens?.map((t, i) => (
                  <span key={i} className={`tok ${t.label ? SPAN_CLASS[t.label] ?? "sp-o" : ""}`} title={`${t.kind}${t.role ? " · " + t.role : ""}${t.label ? " → " + t.label : ""}`}>{t.text}</span>
                ))}
              </div>
              <h4>Флаги</h4>
              <p>{sel.flags?.length ? sel.flags.join(", ") : <span className="muted">нет</span>}</p>
              <h4>Источник значений</h4>
              <ul className="prov">
                {FIELDS.filter((f) => sel[f]).map((f) => (
                  <li key={f}><b>{FIELD_LABELS[f]}</b>: {sel[f]} <span className="muted">({sel.provenance?.[f] ?? "?"})</span></li>
                ))}
              </ul>
              <h4>Правила</h4>
              <ul className="prov">{sel.spans?.map((s) => <li key={s.field}><b>{s.field}</b> «{s.text}» <span className="muted">{s.rule} · {s.score}</span></li>)}</ul>
            </>
          )}
        </aside>
      </div>
      {busy && <div className="busy">Разбор…</div>}
    </div>
  );
}

/** Raw string with field spans highlighted (uses explain spans; falls back to plain text). */
function Highlighted({ r }: { r: RecordOut }) {
  const spans = [...(r.spans ?? [])].sort((a, b) => a.start - b.start);
  if (!spans.length) return <>{r.raw_address}</>;
  const parts: JSX.Element[] = [];
  let pos = 0;
  spans.forEach((s, i) => {
    if (s.start > pos) parts.push(<span key={`t${i}`}>{r.raw_address.slice(pos, s.start)}</span>);
    parts.push(<mark key={`s${i}`} className={SPAN_CLASS[s.field] ?? ""} title={`${s.field} · ${s.rule}`}>{r.raw_address.slice(s.start, s.end)}</mark>);
    pos = Math.max(pos, s.end);
  });
  if (pos < r.raw_address.length) parts.push(<span key="tail">{r.raw_address.slice(pos)}</span>);
  return <>{parts}</>;
}

function ConfBadge({ c }: { c: number }) {
  const cls = c >= 0.9 ? "hi" : c >= 0.7 ? "mid" : "lo";
  return <span className={`badge ${cls}`}>{c.toFixed(2)}</span>;
}
