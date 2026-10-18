import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Drawer } from "../components/Drawer";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot } from "../components/Mascot";
import { AiCheck, ConfidenceCard, ConfidenceExplain, GeoCard } from "../components/record";
import { DEFAULT_PAGE_SIZE, FilterChip, PAGE_SIZES, Pagination, SearchField, Segmented, SortHeader, TableCard, type SortDir } from "../components/table";
import {
  Chip, ConfChip, ErrorNote, FileButton, Highlighted, IconChevronRight, IconClose, IconDownload, IconFilter, IconText, IconUndo, IconUpload, useDropTarget,
  type ChipStatus,
} from "../components/ui";
import { ADDRESSES, count, fmtInt, fmtScore, RECORDS } from "../format";
import { flagLabel } from "../lib/explain";
import { PATHS } from "../routes";
import { useSession } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import { FIELDS, FIELD_LABELS, PRECISIONS, PRECISION_LABELS, SPAN_CLASS, provenanceLabel, type Field, type RecordOut } from "../types";

type View = "all" | "queue" | "edited" | "reviewed";
const VIEWS: View[] = ["all", "queue", "edited", "reviewed"];
type SortKey = "conf" | "fields" | "city";
const NO_CITY = "__none";

const BANDS = [
  { id: "lt50", label: "< 0,50", test: (c: number) => c < 0.5 },
  { id: "50-70", label: "0,50–0,70", test: (c: number) => c >= 0.5 && c < 0.7 },
  { id: "70-90", label: "0,70–0,90", test: (c: number) => c >= 0.7 && c < 0.9 },
  { id: "gte90", label: "≥ 0,90", test: (c: number) => c >= 0.9 },
] as const;

/** Select text in the raw address, press a key: the selection becomes that field. */
const HOTKEYS: Record<string, Field> = {
  s: "street_name", h: "house", a: "apartment", e: "entrance", f: "floor",
  d: "district", l: "living_area", m: "landmark", c: "city", p: "province",
};
const HOTKEY_HINT: [string, string][] = [["S", "улица"], ["H", "дом"], ["A", "кв"], ["E", "подъезд"], ["F", "этаж"], ["D", "район"], ["L", "массив"], ["M", "ориентир"], ["C", "город"], ["P", "область"]];

const isLow = (r: RecordOut) => r.confidence < REVIEW_THRESHOLD && !r.reviewed;
const filledCount = (r: RecordOut) => FIELDS.filter((f) => r[f]).length;
const geoKey = (r: RecordOut) => (r.geo === undefined ? null : r.geo ? r.geo.precision : "none");
const fromOsm = (r: RecordOut) => Object.values(r.provenance ?? {}).some((p) => p?.startsWith("gazetteer"));
/** Flags that describe a problem or a source; «rules and model agree» flags are everywhere and filter nothing. */
const isTellingFlag = (f: string) => !/^arb_.+_agree$/.test(f) && !f.startsWith("error:");

function status(r: RecordOut): { tone: ChipStatus; label: string; strong?: boolean } {
  if (r.reviewed) return { tone: "ok", label: "принято", strong: true };
  if (r.edited) return { tone: "warn", label: "исправлено" };
  if (r.confidence < REVIEW_THRESHOLD) return { tone: "flag", label: "на проверку" };
  return { tone: "ok", label: "готово" };
}

/** Lower-cased search text per record, cached by object identity (an edit creates a new object). */
const HAY = new WeakMap<RecordOut, string>();
const hay = (r: RecordOut) => {
  let h = HAY.get(r);
  if (h === undefined) {
    h = [r.raw_address, r.id, r.city, r.street_name, r.house, r.district, r.living_area].filter(Boolean).join(" ").toLowerCase();
    HAY.set(r, h);
  }
  return h;
};

/* ------------------------------------------------------------------ URL state */

const splitList = (v: string | null) => (v ? v.split(",").filter(Boolean) : []);

function useListParams() {
  const [sp, setSp] = useSearchParams();
  const view = (VIEWS.includes(sp.get("view") as View) ? sp.get("view") : "all") as View;
  const rawSort = sp.get("sort");
  const sortKey: SortKey | null = rawSort === "conf" || rawSort === "fields" || rawSort === "city" ? rawSort : rawSort === "none" ? null : view === "queue" ? "conf" : null;
  const dir: "asc" | "desc" = sp.get("dir") === "desc" ? "desc" : "asc";
  const sizeParam = Number(sp.get("size"));
  const size = (PAGE_SIZES as readonly number[]).includes(sizeParam) ? sizeParam : DEFAULT_PAGE_SIZE;
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
  return {
    view, sortKey, dir, size, set,
    q: sp.get("q") ?? "",
    city: sp.get("city") ?? "",
    band: sp.get("conf") ?? "",
    missing: splitList(sp.get("missing")) as Field[],
    geo: splitList(sp.get("geo")),
    flags: splitList(sp.get("flag")),
    osm: sp.get("osm") === "1",
    page: Math.max(0, Number(sp.get("page")) || 0),
  };
}
type Params = ReturnType<typeof useListParams>;

const toggleIn = (arr: string[], v: string) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]).join(",") || null;

/* ------------------------------------------------------------------ page */

export function Records() {
  const s = useSession();
  const { id } = useParams();
  const { search } = useLocation();
  const navigate = useNavigate();
  const p = useListParams();
  const rows = s.rows;
  const [showFilters, setShowFilters] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const lastPick = useRef<number | null>(null);

  // the search runs on a deferred value, so typing stays instant on 100k rows
  const q = useDeferredValue(p.q);
  const missingKey = p.missing.join(","), geoKeyStr = p.geo.join(","), flagKey = p.flags.join(",");

  const counts = useMemo(() => ({
    all: rows.length,
    queue: rows.filter(isLow).length,
    edited: rows.filter((r) => r.edited).length,
    reviewed: rows.filter((r) => r.reviewed).length,
  }), [rows]);

  const facets = useMemo(() => {
    const city = new Map<string, number>();
    const flag = new Map<string, number>();
    const geo = new Map<string, number>();
    const missing = Object.fromEntries(FIELDS.map((f) => [f, 0])) as Record<Field, number>;
    const band = Object.fromEntries(BANDS.map((b) => [b.id, 0])) as Record<string, number>;
    let noCity = 0, osm = 0;
    for (const r of rows) {
      if (r.city) city.set(r.city, (city.get(r.city) ?? 0) + 1); else noCity++;
      for (const f of FIELDS) if (!r[f]) missing[f]++;
      for (const b of BANDS) if (b.test(r.confidence)) band[b.id]++;
      const g = geoKey(r);
      if (g) geo.set(g, (geo.get(g) ?? 0) + 1);
      for (const f of new Set(r.flags ?? [])) if (isTellingFlag(f)) flag.set(f, (flag.get(f) ?? 0) + 1);
      if (fromOsm(r)) osm++;
    }
    return {
      cities: [...city.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ru")),
      noCity, missing, band, osm, geo,
      flags: [...flag.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14),
    };
  }, [rows]);

  const filtered = useMemo(() => {
    const band = BANDS.find((b) => b.id === p.band);
    const ql = q.trim().toLowerCase();
    let v = rows.filter((r) => {
      if (p.view === "queue" && !isLow(r)) return false;
      if (p.view === "edited" && !r.edited) return false;
      if (p.view === "reviewed" && !r.reviewed) return false;
      if (p.city && (p.city === NO_CITY ? r.city : r.city !== p.city)) return false;
      if (band && !band.test(r.confidence)) return false;
      if (p.missing.length && !p.missing.some((f) => !r[f])) return false;
      if (p.geo.length && !p.geo.includes(geoKey(r) ?? "")) return false;
      if (p.flags.length && !p.flags.some((f) => r.flags?.includes(f))) return false;
      if (p.osm && !fromOsm(r)) return false;
      if (ql && !hay(r).includes(ql)) return false;
      return true;
    });
    if (p.sortKey) {
      const k = p.dir === "asc" ? 1 : -1;
      const by: Record<SortKey, (a: RecordOut, b: RecordOut) => number> = {
        conf: (a, b) => a.confidence - b.confidence,
        fields: (a, b) => filledCount(a) - filledCount(b),
        city: (a, b) => (a.city ?? "￿").localeCompare(b.city ?? "￿", "ru"),
      };
      const cmp = by[p.sortKey];
      v = [...v].sort((a, b) => k * cmp(a, b));
    }
    return v;
  }, [rows, p.view, p.city, p.band, missingKey, geoKeyStr, flagKey, p.osm, p.sortKey, p.dir, q]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = id ? rows.find((r) => r.id === id) ?? null : null;
  const idx = selected ? filtered.findIndex((r) => r.id === selected.id) : -1;
  const pages = Math.max(1, Math.ceil(filtered.length / p.size));
  const page = Math.min(p.page, pages - 1);
  const pageRows = filtered.slice(page * p.size, (page + 1) * p.size);

  const open = useCallback((rid: string | null) => {
    navigate({ pathname: rid ? PATHS.record(rid) : PATHS.records, search }, { replace: !!id && !!rid });
  }, [navigate, search, id]);

  // keep the open record on the visible page
  useEffect(() => {
    if (idx >= 0) {
      const want = Math.floor(idx / p.size);
      if (want !== page) p.set({ page: want ? String(want) : null });
    }
  }, [idx]); // eslint-disable-line react-hooks/exhaustive-deps

  const tbody = useRef<HTMLTableSectionElement>(null);
  useEffect(() => {
    if (!selected) return;
    tbody.current?.querySelector<HTMLElement>(`[data-id="${CSS.escape(selected.id)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // the selection only keeps ids that still exist
  useEffect(() => {
    setPicked((prevSet) => {
      if (!prevSet.size) return prevSet;
      const ids = new Set(rows.map((r) => r.id));
      const nextSet = new Set([...prevSet].filter((x) => ids.has(x)));
      return nextSet.size === prevSet.size ? prevSet : nextSet;
    });
  }, [rows]);

  const prev = idx > 0 ? () => open(filtered[idx - 1].id) : undefined;
  const next = idx >= 0 && idx < filtered.length - 1 ? () => open(filtered[idx + 1].id) : undefined;

  const accept = useCallback(async (r: RecordOut) => {
    const after = filtered[idx + 1]?.id ?? filtered[idx - 1]?.id ?? null;
    const ok = await s.accept(r);
    if (ok && after) open(after);
  }, [filtered, idx, s, open]);

  // selection: click toggles, shift+click selects the range on this page
  const togglePick = (r: RecordOut, i: number, ev: MouseEvent) => {
    ev.stopPropagation();
    const from = lastPick.current;
    setPicked((prevSet) => {
      const nextSet = new Set(prevSet);
      if (ev.shiftKey && from != null) {
        const on = !prevSet.has(r.id);
        pageRows.slice(Math.min(from, i), Math.max(from, i) + 1).forEach((x) => (on ? nextSet.add(x.id) : nextSet.delete(x.id)));
      } else if (nextSet.has(r.id)) nextSet.delete(r.id); else nextSet.add(r.id);
      return nextSet;
    });
    lastPick.current = i;
  };
  const pageAllPicked = pageRows.length > 0 && pageRows.every((r) => picked.has(r.id));
  const pageSomePicked = pageRows.some((r) => picked.has(r.id));
  const togglePage = () => setPicked((prevSet) => {
    const nextSet = new Set(prevSet);
    pageRows.forEach((r) => (pageAllPicked ? nextSet.delete(r.id) : nextSet.add(r.id)));
    return nextSet;
  });

  const advanced = (p.band ? 1 : 0) + p.missing.length + p.geo.length + p.flags.length + (p.osm ? 1 : 0);
  const anyFilter = advanced > 0 || !!p.q || !!p.city || p.view !== "all";
  const resetFilters = () => p.set({ view: null, q: null, city: null, conf: null, missing: null, geo: null, flag: null, osm: null, sort: null, dir: null });
  const sortDir = (k: SortKey): SortDir => (p.sortKey === k ? p.dir : null);
  const setSort = (k: SortKey) => (d: SortDir) => p.set({ sort: d ? k : "none", dir: d === "desc" ? "desc" : null });

  return (
    <>
      <PageHeader title="Записи" meta={rows.length ? `${count(rows.length, RECORDS)} · на проверку ${fmtInt(counts.queue)} · принято ${fmtInt(counts.reviewed)}` : undefined}>
        <FileButton onFile={s.loadFile} className="btn btn-ghost" disabled={s.busy}><IconUpload />Загрузить JSON</FileButton>
        <button type="button" className="btn btn-primary" onClick={() => s.exportJson()} disabled={!rows.length}><IconDownload />output.json</button>
      </PageHeader>

      <div className={`content ${selected ? "has-drawer" : ""}`}>
        {s.error && <ErrorNote text={s.error} onClose={() => s.setError(null)} />}

        {!s.ready ? <TableSkeleton /> : !rows.length ? <EmptyRecords /> : (
          <TableCard
            toolbar={
              <>
                <Segmented<View> label="Статус" value={p.view} onChange={(v) => p.set({ view: v, sort: null, dir: null })}
                  options={[
                    { value: "all", label: "Все", count: counts.all },
                    { value: "queue", label: "На проверку", count: counts.queue, tone: "flag" },
                    { value: "edited", label: "Исправлены", count: counts.edited },
                    { value: "reviewed", label: "Приняты", count: counts.reviewed },
                  ]} />
                <div className="toolbar-right">
                  <select className="field select-pill" value={p.city} onChange={(e) => p.set({ city: e.target.value })} aria-label="Город">
                    <option value="">Все города</option>
                    {facets.cities.map(([c, n]) => <option key={c} value={c}>{c} · {n}</option>)}
                    {facets.noCity > 0 && <option value={NO_CITY}>Без города · {facets.noCity}</option>}
                  </select>
                  <button type="button" className={`btn btn-ghost ${showFilters || advanced ? "is-on" : ""}`} onClick={() => setShowFilters((v) => !v)} aria-expanded={showFilters}>
                    <IconFilter />Фильтры{advanced > 0 && <span className="btn-count">{advanced}</span>}
                  </button>
                  <SearchField value={p.q} onChange={(v) => p.set({ q: v })} label="Адрес, ID, улица, район" />
                </div>
                {showFilters && <FilterPanel p={p} facets={facets} />}
                {anyFilter && (
                  <div className="active-filters">
                    <span className="muted">{count(filtered.length, RECORDS)} по фильтру</span>
                    {p.band && <ActiveChip onRemove={() => p.set({ conf: null })}>уверенность {BANDS.find((b) => b.id === p.band)?.label}</ActiveChip>}
                    {p.missing.map((f) => <ActiveChip key={f} onRemove={() => p.set({ missing: toggleIn(p.missing, f) })}>нет: {FIELD_LABELS[f].toLowerCase()}</ActiveChip>)}
                    {p.geo.map((g) => <ActiveChip key={g} onRemove={() => p.set({ geo: toggleIn(p.geo, g) })}>OSM: {PRECISION_LABELS[g as keyof typeof PRECISION_LABELS]?.toLowerCase() ?? g}</ActiveChip>)}
                    {p.flags.map((f) => <ActiveChip key={f} onRemove={() => p.set({ flag: toggleIn(p.flags, f) })}>{flagLabel(f).toLowerCase()}</ActiveChip>)}
                    {p.osm && <ActiveChip onRemove={() => p.set({ osm: null })}>поля из OSM</ActiveChip>}
                    <button type="button" className="link-btn" onClick={resetFilters}>Сбросить всё</button>
                  </div>
                )}
              </>
            }
            footer={<Pagination page={page} total={filtered.length} size={p.size} onPage={(n) => p.set({ page: n ? String(n) : null })} onSize={(n) => p.set({ size: n === DEFAULT_PAGE_SIZE ? null : String(n) })} />}
          >
            <div className="table-scroll">
              <table className="dtable records-table">
                <thead>
                  <tr>
                    <th className="col-check">
                      <input type="checkbox" className="check" checked={pageAllPicked} aria-label="Выбрать страницу"
                        ref={(el) => { if (el) el.indeterminate = !pageAllPicked && pageSomePicked; }} onChange={togglePage} />
                    </th>
                    <th>Адрес</th>
                    <SortHeader label="Город" className="col-city" dir={sortDir("city")} onChange={setSort("city")} />
                    <th className="col-opt">Улица</th>
                    <th className="col-opt">Дом, кв</th>
                    <SortHeader label="Поля" className="col-opt" dir={sortDir("fields")} onChange={setSort("fields")} />
                    <SortHeader label="Уверенность" className="num" dir={sortDir("conf")} onChange={setSort("conf")} />
                    <th>Статус</th>
                    <th aria-label="Открыть" />
                  </tr>
                </thead>
                <tbody ref={tbody}>
                  {pageRows.map((r, i) => {
                    const st = status(r);
                    const on = picked.has(r.id);
                    return (
                      <tr key={r.id} data-id={r.id} className={`${r.id === selected?.id ? "is-selected" : ""} ${on ? "is-picked" : ""}`} tabIndex={0}
                        onClick={() => open(r.id)} onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) open(r.id); }}>
                        <td className="col-check" onClick={(e) => togglePick(r, i, e)}>
                          <input type="checkbox" className="check" checked={on} readOnly tabIndex={-1} aria-label={`Выбрать ${r.id}`} />
                        </td>
                        <td className="cell-main">
                          <span className="cell-title" title={r.raw_address}>{r.raw_address}</span>
                          <span className="cell-sub mono">{r.id}</span>
                        </td>
                        <td className="cell-clip col-city">{r.city ?? <span className="muted">нет</span>}</td>
                        <td className="cell-clip col-opt">{[r.street_type, r.street_name].filter(Boolean).join(" ") || <span className="muted">нет</span>}</td>
                        <td className="cell-clip col-opt">{r.house ?? <span className="muted">нет</span>}{r.apartment ? <span className="muted">, кв {r.apartment}</span> : null}</td>
                        <td className="col-opt"><FillMeter n={filledCount(r)} /></td>
                        <td className="num"><ConfChip c={r.confidence} /></td>
                        <td><Chip status={st.tone} strong={st.strong}>{st.label}</Chip></td>
                        <td className="cell-go"><IconChevronRight /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!filtered.length && (
                <div className="table-empty">
                  <Mascot pose={p.view === "queue" && !advanced && !p.q && !p.city ? "celebrating" : "magnifier"} className="table-empty-mascot" />
                  <p>{p.view === "queue" && !advanced && !p.q && !p.city ? "Очередь проверки пуста" : "Нет записей с такими фильтрами"}</p>
                  {anyFilter && <button type="button" className="btn btn-ghost btn-sm" onClick={resetFilters}>Сбросить фильтры</button>}
                </div>
              )}
            </div>
          </TableCard>
        )}
      </div>

      {picked.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Выбранные записи">
          <span className="bulk-count"><b>{fmtInt(picked.size)}</b> выбрано</span>
          {picked.size < filtered.length && (
            <button type="button" className="link-btn" onClick={() => setPicked(new Set(filtered.map((r) => r.id)))}>Выбрать все {fmtInt(filtered.length)}</button>
          )}
          <span className="bulk-sep" />
          <button type="button" className="btn btn-sm btn-primary" onClick={async () => { await s.acceptMany([...picked]); setPicked(new Set()); }}>Принять</button>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => s.exportJson([...picked])}><IconDownload />Экспорт</button>
          <button type="button" className="icon-btn" onClick={() => setPicked(new Set())} aria-label="Снять выбор"><IconClose /></button>
        </div>
      )}

      {selected && (
        <RecordDrawer r={selected} position={idx >= 0 ? `${fmtInt(idx + 1)} из ${fmtInt(filtered.length)}` : undefined}
          onClose={() => open(null)} onPrev={prev} onNext={next} onAccept={accept} />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ filters */

type Facets = {
  missing: Record<Field, number>;
  band: Record<string, number>;
  geo: Map<string, number>;
  flags: [string, number][];
  osm: number;
};

function FilterPanel({ p, facets }: { p: Params; facets: Facets }) {
  const geoKeys = PRECISIONS.filter((g) => facets.geo.has(g));
  return (
    <div className="filter-panel">
      <div className="fgroup">
        <p className="eyebrow">Уверенность</p>
        <div className="fchips">
          {BANDS.map((b) => (
            <FilterChip key={b.id} on={p.band === b.id} n={facets.band[b.id]} onClick={() => p.set({ conf: p.band === b.id ? null : b.id })}>{b.label}</FilterChip>
          ))}
        </div>
      </div>
      <div className="fgroup">
        <p className="eyebrow">Не заполнено</p>
        <div className="fchips">
          {FIELDS.filter((f) => facets.missing[f] > 0).map((f) => (
            <FilterChip key={f} on={p.missing.includes(f)} n={facets.missing[f]} onClick={() => p.set({ missing: toggleIn(p.missing, f) })}>{FIELD_LABELS[f]}</FilterChip>
          ))}
        </div>
      </div>
      {geoKeys.length > 0 && (
        <div className="fgroup">
          <p className="eyebrow">Найдено в OSM до уровня</p>
          <div className="fchips">
            {geoKeys.map((g) => (
              <FilterChip key={g} on={p.geo.includes(g)} n={facets.geo.get(g)} onClick={() => p.set({ geo: toggleIn(p.geo, g) })}>{PRECISION_LABELS[g]}</FilterChip>
            ))}
            {facets.osm > 0 && <FilterChip on={p.osm} n={facets.osm} onClick={() => p.set({ osm: p.osm ? null : "1" })}>Поля дополнены из OSM</FilterChip>}
          </div>
        </div>
      )}
      {facets.flags.length > 0 && (
        <div className="fgroup">
          <p className="eyebrow">Признаки разбора</p>
          <div className="fchips">
            {facets.flags.map(([f, n]) => (
              <FilterChip key={f} on={p.flags.includes(f)} n={n} onClick={() => p.set({ flag: toggleIn(p.flags, f) })}>{flagLabel(f)}</FilterChip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const ActiveChip = ({ children, onRemove }: { children: ReactNode; onRemove: () => void }) => (
  <span className="achip">{children}<button type="button" onClick={onRemove} aria-label="Убрать фильтр"><IconClose /></button></span>
);

/** 11 ticks, filled for each found field. */
function FillMeter({ n }: { n: number }) {
  return (
    <span className="fill-meter" title={`Заполнено полей: ${n} из 11`}>
      <span className="fill-ticks" aria-hidden="true">{FIELDS.map((f, i) => <i key={f} className={i < n ? "on" : ""} />)}</span>
      <span className="mono">{n}</span>
    </span>
  );
}

function TableSkeleton() {
  return (
    <section className="card table-card" aria-busy="true" aria-label="Загрузка сессии">
      <div className="table-toolbar"><span className="skel" style={{ width: 380, height: 40, borderRadius: 999 }} /></div>
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="skel-row"><span className="skel" style={{ width: `${40 + ((i * 17) % 35)}%` }} /><span className="skel" style={{ width: 70 }} /></div>
      ))}
    </section>
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
        <ConfidenceCard r={r} />
        <GeoCard r={r} />
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

      <section className="insp-why">
        <div className="insp-section-head">
          <p className="eyebrow">Почему {fmtScore(r.confidence)} · SHAP</p>
        </div>
        <ConfidenceExplain r={r} />
      </section>

      <section>
        <AiCheck r={r} />
      </section>

      <details className="insp-details">
        <summary>Токены и правила</summary>
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
            <ul className="prov">{r.spans.map((sp) => <li key={sp.field + sp.start}><b>{FIELD_LABELS[sp.field.toLowerCase() as Field] ?? sp.field}</b> «{sp.text}» <span className="muted mono">{sp.rule} · {fmtScore(sp.score)}</span></li>)}</ul>
          </>
        ) : null}
        {r.flags?.length ? (
          <>
            <p className="eyebrow">Признаки</p>
            <div className="tokens">{r.flags.map((f, i) => <span key={f + i} className="tok" title={f}>{flagLabel(f)}</span>)}</div>
          </>
        ) : null}
      </details>
    </Drawer>
  );
}
