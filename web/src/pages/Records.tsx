import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Drawer } from "../components/Drawer";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot } from "../components/Mascot";
import { PAGE_SIZE, Pagination, SearchField, Segmented, SortHeader, TableCard, type SortDir } from "../components/table";
import {
  Chip, ConfChip, FileButton, Highlighted, IconChevronRight, IconDownload, IconExternal, IconMapPin, IconText, IconUndo, IconUpload, useDropTarget,
  type ChipStatus,
} from "../components/ui";
import { ADDRESSES, count, fmtInt, fmtScore, RECORDS } from "../format";
import { PATHS } from "../routes";
import { useSession } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import { FIELDS, FIELD_LABELS, PRECISION_LABELS, SPAN_CLASS, provenanceLabel, type Field, type Precision, type RecordOut } from "../types";

type View = "all" | "queue" | "edited" | "reviewed";
const VIEWS: View[] = ["all", "queue", "edited", "reviewed"];
const NO_CITY = "__none";

/** Select text in the raw address, press a key: the selection becomes that field. */
const HOTKEYS: Record<string, Field> = {
  s: "street_name", h: "house", a: "apartment", e: "entrance", f: "floor",
  d: "district", l: "living_area", m: "landmark", c: "city", p: "province",
};
const HOTKEY_HINT: [string, string][] = [["S", "улица"], ["H", "дом"], ["A", "кв"], ["E", "подъезд"], ["F", "этаж"], ["D", "район"], ["L", "массив"], ["M", "ориентир"], ["C", "город"], ["P", "область"]];

const isLow = (r: RecordOut) => r.confidence < REVIEW_THRESHOLD && !r.reviewed;

function status(r: RecordOut): { tone: ChipStatus; label: string; strong?: boolean } {
  if (r.reviewed) return { tone: "ok", label: "принято", strong: true };
  if (r.edited) return { tone: "warn", label: "исправлено" };
  if (r.confidence < REVIEW_THRESHOLD) return { tone: "flag", label: "на проверку" };
  return { tone: "ok", label: "готово" };
}

const filledCount = (r: RecordOut) => FIELDS.filter((f) => r[f]).length;

/* ------------------------------------------------------------------ URL state: ?view=&q=&city=&sort=&page= */

function useListParams() {
  const [sp, setSp] = useSearchParams();
  const view = (VIEWS.includes(sp.get("view") as View) ? sp.get("view") : "all") as View;
  const rawSort = sp.get("sort");
  const sort: SortDir = rawSort === "asc" || rawSort === "desc" ? rawSort : rawSort === "none" ? null : view === "queue" ? "asc" : null;
  const set = useCallback((patch: Record<string, string | null>) => {
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v == null || v === "") n.delete(k); else n.set(k, v);
      }
      if (!("page" in patch)) n.delete("page");
      if (n.get("view") === "all") n.delete("view");
      return n;
    }, { replace: true });
  }, [setSp]);
  return { view, q: sp.get("q") ?? "", city: sp.get("city") ?? "", sort, page: Math.max(0, Number(sp.get("page")) || 0), set };
}

/* ------------------------------------------------------------------ page */

export function Records() {
  const s = useSession();
  const { id } = useParams();
  const { search } = useLocation();
  const navigate = useNavigate();
  const p = useListParams();
  const rows = s.rows;

  const counts = useMemo(() => ({
    all: rows.length,
    queue: rows.filter(isLow).length,
    edited: rows.filter((r) => r.edited).length,
    reviewed: rows.filter((r) => r.reviewed).length,
  }), [rows]);

  const cities = useMemo(() => {
    const m = new Map<string, number>();
    let none = 0;
    for (const r of rows) { if (r.city) m.set(r.city, (m.get(r.city) ?? 0) + 1); else none++; }
    return { list: [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])), none };
  }, [rows]);

  const list = useMemo(() => {
    let v = rows;
    if (p.view === "queue") v = v.filter(isLow);
    if (p.view === "edited") v = v.filter((r) => r.edited);
    if (p.view === "reviewed") v = v.filter((r) => r.reviewed);
    if (p.city) v = v.filter((r) => (p.city === NO_CITY ? !r.city : r.city === p.city));
    if (p.q) {
      const q = p.q.toLowerCase();
      v = v.filter((r) => r.raw_address.toLowerCase().includes(q) || r.id.toLowerCase().includes(q)
        || (r.city ?? "").toLowerCase().includes(q) || (r.street_name ?? "").toLowerCase().includes(q));
    }
    if (p.sort) v = [...v].sort((a, b) => (p.sort === "asc" ? a.confidence - b.confidence : b.confidence - a.confidence));
    return v;
  }, [rows, p.view, p.city, p.q, p.sort]);

  const selected = id ? rows.find((r) => r.id === id) ?? null : null;
  const idx = selected ? list.findIndex((r) => r.id === selected.id) : -1;
  const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  const page = Math.min(p.page, pages - 1);
  const pageRows = list.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const open = useCallback((rid: string | null) => {
    navigate({ pathname: rid ? PATHS.record(rid) : PATHS.records, search }, { replace: !!id && !!rid });
  }, [navigate, search, id]);

  // keep the open record on the visible page
  useEffect(() => {
    if (idx >= 0) {
      const want = Math.floor(idx / PAGE_SIZE);
      if (want !== page) p.set({ page: want ? String(want) : null });
    }
  }, [idx]); // eslint-disable-line react-hooks/exhaustive-deps

  // scroll the open row into view when stepping with the keyboard
  const tbody = useRef<HTMLTableSectionElement>(null);
  useEffect(() => {
    if (!selected) return;
    tbody.current?.querySelector<HTMLElement>(`[data-id="${CSS.escape(selected.id)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const prev = idx > 0 ? () => open(list[idx - 1].id) : undefined;
  const next = idx >= 0 && idx < list.length - 1 ? () => open(list[idx + 1].id) : undefined;

  const accept = useCallback(async (r: RecordOut) => {
    const after = list[idx + 1]?.id ?? list[idx - 1]?.id ?? null;
    const ok = await s.accept(r);
    if (ok && after) open(after);
  }, [list, idx, s, open]);

  const resetFilters = () => p.set({ view: null, q: null, city: null, sort: null });

  return (
    <>
      <PageHeader title="Записи" meta={rows.length ? `${count(rows.length, RECORDS)} · на проверку ${fmtInt(counts.queue)} · принято ${fmtInt(counts.reviewed)}` : undefined}>
        <FileButton onFile={s.loadFile} className="btn btn-ghost" disabled={s.busy}><IconUpload />Загрузить JSON</FileButton>
        <button type="button" className="btn btn-primary" onClick={s.exportJson} disabled={!rows.length}><IconDownload />output.json</button>
      </PageHeader>

      <div className={`content ${selected ? "has-drawer" : ""}`}>
        {s.error && <div className="error" role="alert">{s.error}</div>}

        {!rows.length ? <EmptyRecords /> : (
          <TableCard
            toolbar={
              <>
                <Segmented<View> label="Фильтр по статусу" value={p.view} onChange={(v) => p.set({ view: v, sort: null })}
                  options={[
                    { value: "all", label: "Все", count: counts.all },
                    { value: "queue", label: "На проверку", count: counts.queue, tone: "flag" },
                    { value: "edited", label: "Исправлены", count: counts.edited },
                    { value: "reviewed", label: "Приняты", count: counts.reviewed },
                  ]} />
                <div className="toolbar-right">
                  <select className="field select-pill" value={p.city} onChange={(e) => p.set({ city: e.target.value })} aria-label="Город">
                    <option value="">Все города</option>
                    {cities.list.map(([c, n]) => <option key={c} value={c}>{c} · {n}</option>)}
                    {cities.none > 0 && <option value={NO_CITY}>Без города · {cities.none}</option>}
                  </select>
                  <SearchField value={p.q} onChange={(q) => p.set({ q })} label="Адрес, ID или улица" />
                </div>
              </>
            }
            footer={list.length > PAGE_SIZE ? <Pagination page={page} total={list.length} onPage={(n) => p.set({ page: n ? String(n) : null })} /> : null}
          >
            <div className="table-scroll">
              <table className="dtable records-table">
                <thead>
                  <tr>
                    <th>Адрес</th>
                    <th>Город</th>
                    <th className="col-opt">Улица</th>
                    <th className="col-opt">Дом, кв</th>
                    <th className="num col-opt">Поля</th>
                    <SortHeader label="Уверенность" className="num" dir={p.sort} onChange={(d) => p.set({ sort: d ?? "none" })} />
                    <th>Статус</th>
                    <th aria-label="Открыть" />
                  </tr>
                </thead>
                <tbody ref={tbody}>
                  {pageRows.map((r) => {
                    const st = status(r);
                    return (
                      <tr key={r.id} data-id={r.id} className={r.id === selected?.id ? "is-selected" : ""} tabIndex={0}
                        onClick={() => open(r.id)} onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) open(r.id); }}>
                        <td className="cell-main">
                          <span className="cell-title" title={r.raw_address}>{r.raw_address}</span>
                          <span className="cell-sub mono">{r.id}</span>
                        </td>
                        <td className="cell-clip">{r.city ?? <span className="muted">нет</span>}</td>
                        <td className="cell-clip col-opt">{[r.street_type, r.street_name].filter(Boolean).join(" ") || <span className="muted">нет</span>}</td>
                        <td className="cell-clip col-opt">{r.house ?? <span className="muted">нет</span>}{r.apartment ? <span className="muted">, кв {r.apartment}</span> : null}</td>
                        <td className="num mono muted col-opt">{filledCount(r)}/11</td>
                        <td className="num"><ConfChip c={r.confidence} /></td>
                        <td><Chip status={st.tone} strong={st.strong}>{st.label}</Chip></td>
                        <td className="cell-go"><IconChevronRight /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!list.length && (
                <div className="table-empty">
                  <Mascot pose={p.view === "queue" && !p.q && !p.city ? "celebrating" : "magnifier"} className="table-empty-mascot" />
                  <p>{p.view === "queue" && !p.q && !p.city ? "Очередь проверки пуста" : "Нет записей с такими фильтрами"}</p>
                  {(p.q || p.city || p.view !== "all") && <button type="button" className="btn btn-ghost btn-sm" onClick={resetFilters}>Сбросить фильтры</button>}
                </div>
              )}
            </div>
          </TableCard>
        )}
      </div>

      {selected && (
        <RecordDrawer r={selected} position={idx >= 0 ? `${idx + 1} из ${list.length}` : undefined}
          onClose={() => open(null)} onPrev={prev} onNext={next} onAccept={accept} />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ empty session */

function EmptyRecords() {
  const s = useSession();
  const [text, setText] = useState("");
  const drop = useDropTarget(s.loadFile);
  const n = text.split(/\r?\n/).filter((l) => l.trim()).length;
  return (
    <section className={`card empty-records ${drop.over ? "is-drag" : ""}`} {...drop.handlers}>
      <div className="empty-records-intro">
        <Mascot pose="hello" className="empty-mascot" />
        <h2>Загрузите адреса</h2>
        <p className="muted">JSON-массив {"{ id, raw_address }"} или {"{ records: [...] }"}</p>
        <FileButton onFile={s.loadFile} disabled={s.busy}><IconUpload />Выбрать файл</FileButton>
      </div>
      <form className="empty-records-paste" onSubmit={(e) => { e.preventDefault(); s.loadText(text); setText(""); }}>
        <label className="eyebrow" htmlFor="paste-addresses">Список адресов, по одному в строке</label>
        <textarea id="paste-addresses" className="field" rows={8} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        <div className="empty-records-actions">
          <span className="muted mono">{count(n, ADDRESSES)}</span>
          <button type="submit" className="btn btn-primary" disabled={s.busy || !n}><IconText />Разобрать</button>
        </div>
      </form>
    </section>
  );
}

/* ------------------------------------------------------------------ inspector */

function RecordDrawer({ r, position, onClose, onPrev, onNext, onAccept }: {
  r: RecordOut;
  position?: string;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  onAccept: (r: RecordOut) => void;
}) {
  const s = useSession();
  const rawRef = useRef<HTMLParagraphElement>(null);
  const [flash, setFlash] = useState<Field | null>(null);
  const st = status(r);
  const changed = r.original ? FIELDS.filter((f) => r[f] !== r.original![f]) : [];

  // relabel: selected text inside the raw address + field key; Enter accepts; ⌘/Ctrl+Enter accepts from inputs
  useEffect(() => {
    const h = (ev: KeyboardEvent) => {
      const el = ev.target as HTMLElement;
      const typing = el?.tagName === "INPUT" || el?.tagName === "TEXTAREA" || el?.tagName === "SELECT";
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); onAccept(r); return; }
      if (typing || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const inDrawer = el === document.body || !!el?.closest?.(".drawer");
      if (ev.key === "Enter" && inDrawer && !(el?.tagName === "BUTTON" || el?.tagName === "A" || el?.tagName === "SUMMARY")) { ev.preventDefault(); onAccept(r); return; }
      const field = HOTKEYS[ev.key.toLowerCase()];
      const sel = window.getSelection();
      const text = sel?.toString().trim();
      if (field && text && rawRef.current && sel?.anchorNode && rawRef.current.contains(sel.anchorNode)) {
        ev.preventDefault();
        s.update(r.id, field, text);
        sel.removeAllRanges();
        setFlash(field);
        window.setTimeout(() => setFlash(null), 900);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [r, s, onAccept]);

  const geo = r.geo;
  const precision = geo?.precision as Precision | undefined;
  const pct = Math.round(r.confidence * 100);

  return (
    <Drawer open label={`Запись ${r.id}`} position={position} onClose={onClose} onPrev={onPrev} onNext={onNext}
      header={
        <div className="drawer-title">
          <p className="drawer-id mono">{r.id}</p>
          <Chip status={st.tone} strong={st.strong}>{st.label}</Chip>
        </div>
      }
      footer={
        <>
          {changed.length > 0 && (
            <button type="button" className="btn btn-quiet" onClick={() => s.revert(r.id)}><IconUndo />Отменить правки ({changed.length})</button>
          )}
          <span className="drawer-foot-spacer" />
          <button type="button" className={`btn ${r.reviewed ? "btn-ghost" : "btn-primary"}`} onClick={() => onAccept(r)}>
            {r.reviewed ? "Принято" : changed.length ? "Сохранить и принять" : "Принять"}<kbd className="kbd-inv">↵</kbd>
          </button>
        </>
      }
    >
      <section className="insp-raw">
        <p className="eyebrow">Исходная строка</p>
        <p ref={rawRef} className="insp-raw-text"><Highlighted r={r} /></p>
        <div className="insp-keys" aria-label="Горячие клавиши разметки">
          <span>Выделите фрагмент и нажмите</span>
          {HOTKEY_HINT.map(([k, label]) => <span key={k}><kbd>{k}</kbd>{label}</span>)}
        </div>
      </section>

      <section className="insp-metrics">
        <div className="insp-metric">
          <p className="eyebrow">Уверенность</p>
          <p className={`insp-big ${r.confidence < REVIEW_THRESHOLD ? "is-flag" : "is-ok"}`}>{fmtScore(r.confidence)}</p>
          <span className="conf-track" aria-hidden="true">
            <span className={`conf-fill ${r.confidence < REVIEW_THRESHOLD ? "is-flag" : ""}`} style={{ width: `${pct}%` }} />
            <span className="conf-mark" style={{ left: `${REVIEW_THRESHOLD * 100}%` }} />
          </span>
          <p className="insp-note">{r.confidence < REVIEW_THRESHOLD ? "ниже порога 0,70" : "выше порога 0,70"}</p>
        </div>
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
      </section>

      <section className="insp-fields">
        <div className="insp-section-head">
          <p className="eyebrow">Поля · {filledCount(r)} из 11</p>
          {changed.length > 0 && <span className="chip chip-warn"><i className="chip-dot" />изменено {changed.length}</span>}
        </div>
        <div className="ff-grid">
          {FIELDS.map((f) => {
            const fc = r.field_confidence?.[f];
            const was = r.original && r.original[f] !== r[f] ? r.original[f] : undefined;
            const src = provenanceLabel(r.provenance?.[f]);
            return (
              <label key={f} className={`ff ${was !== undefined ? "is-changed" : ""} ${flash === f ? "is-flash" : ""}`}>
                <span className="ff-label">
                  <i className={`ff-dot ${SPAN_CLASS[f.toUpperCase()] ?? ""}`} aria-hidden="true" />
                  {FIELD_LABELS[f]}
                  {fc != null && r[f] && <span className={`ff-conf ${fc < REVIEW_THRESHOLD ? "is-flag" : ""}`}>{fmtScore(fc)}</span>}
                </span>
                <input className="field" value={r[f] ?? ""} onChange={(e) => s.update(r.id, f, e.target.value)} spellCheck={false} />
                <span className="ff-foot">
                  {was !== undefined ? <>было: <s>{was ?? "пусто"}</s></> : src ?? ""}
                </span>
              </label>
            );
          })}
        </div>
      </section>

      <details className="insp-details">
        <summary>Разбор модели</summary>
        <p className="eyebrow">Токены</p>
        <div className="tokens">
          {r.tokens?.length
            ? r.tokens.map((t, i) => (
                <span key={i} className={`tok ${t.label ? SPAN_CLASS[t.label] ?? "sp-o" : ""}`} title={`${t.kind}${t.role ? " · " + t.role : ""}${t.label ? " → " + t.label : ""}`}>{t.text}</span>
              ))
            : <span className="muted">нет</span>}
        </div>
        {r.spans?.length ? (
          <>
            <p className="eyebrow">Правила</p>
            <ul className="prov">{r.spans.map((sp) => <li key={sp.field + sp.start}><b>{sp.field}</b> «{sp.text}» <span className="muted mono">{sp.rule} · {sp.score}</span></li>)}</ul>
          </>
        ) : null}
        {r.flags?.length ? (
          <>
            <p className="eyebrow">Флаги</p>
            <div className="tokens">{r.flags.map((f) => <span key={f} className="tok">{f}</span>)}</div>
          </>
        ) : null}
      </details>
    </Drawer>
  );
}
