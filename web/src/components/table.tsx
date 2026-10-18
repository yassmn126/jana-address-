import { useEffect, useRef, type ReactNode } from "react";
import { fmtInt } from "../format";
import { IconArrowDown, IconArrowUp, IconChevronLeft, IconChevronRight, IconSearch, IconSort } from "./ui";

/** Segmented tabs with counts («Все 416 · На проверку 3 · …»). */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: string; count?: number; tone?: "flag" }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="seg" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value}
          className={`seg-btn ${value === o.value ? "is-active" : ""}`} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count != null && <span className={`seg-count ${o.tone === "flag" && o.count ? "is-flag" : ""}`}>{fmtInt(o.count)}</span>}
        </button>
      ))}
    </div>
  );
}

/** Search box that commits on every keystroke; `/` focuses it from anywhere on the page. */
export function SearchField({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const h = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (ev.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") { ev.preventDefault(); ref.current?.focus(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <label className="search">
      <IconSearch />
      <input ref={ref} type="search" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} placeholder={label} />
      {!value && <kbd className="search-kbd">/</kbd>}
    </label>
  );
}

/** Column header that sorts: none → ascending → descending → none. */
export type SortDir = "asc" | "desc" | null;
export function SortHeader({ label, dir, onChange, className = "" }: { label: string; dir: SortDir; onChange: (d: SortDir) => void; className?: string }) {
  const next: SortDir = dir === null ? "asc" : dir === "asc" ? "desc" : null;
  return (
    <th className={className} aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}>
      <button type="button" className={`th-sort ${dir ? "is-on" : ""}`} onClick={() => onChange(next)}>
        {label}
        {dir === "asc" ? <IconArrowUp /> : dir === "desc" ? <IconArrowDown /> : <IconSort />}
      </button>
    </th>
  );
}

export const PAGE_SIZES = [25, 50, 100, 200] as const;
export const DEFAULT_PAGE_SIZE = 50;

/** Page numbers with gaps: 1 … 4 5 [6] 7 8 … 120 */
function pageList(page: number, pages: number): (number | "gap")[] {
  const out: (number | "gap")[] = [];
  const add = (n: number) => { if (n >= 0 && n < pages && !out.includes(n)) out.push(n); };
  add(0);
  if (page > 3) out.push("gap");
  for (let n = page - 2; n <= page + 2; n++) add(n);
  if (page < pages - 4) out.push("gap");
  add(pages - 1);
  return out;
}

/** «51–100 из 12 340», page size, numbered pages, prev / next. */
export function Pagination({ page, total, size, onPage, onSize }: {
  page: number;
  total: number;
  size: number;
  onPage: (p: number) => void;
  onSize: (s: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total ? page * size + 1 : 0;
  const to = Math.min(total, (page + 1) * size);
  return (
    <div className="pager">
      <span className="pager-range">{fmtInt(from)}–{fmtInt(to)} из {fmtInt(total)}</span>
      <label className="pager-size">
        <span>по</span>
        <select value={size} onChange={(e) => onSize(Number(e.target.value))} aria-label="Строк на странице">
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      {pages > 1 && (
        <nav className="pager-btns" aria-label="Страницы">
          <button type="button" className="icon-btn is-round" onClick={() => onPage(page - 1)} disabled={page <= 0} aria-label="Предыдущая страница"><IconChevronLeft /></button>
          {pageList(page, pages).map((n, i) => n === "gap"
            ? <span key={`g${i}`} className="pager-gap">…</span>
            : <button key={n} type="button" className={`pager-num ${n === page ? "is-active" : ""}`} onClick={() => onPage(n)} aria-current={n === page ? "page" : undefined}>{fmtInt(n + 1)}</button>)}
          <button type="button" className="icon-btn is-round" onClick={() => onPage(page + 1)} disabled={page >= pages - 1} aria-label="Следующая страница"><IconChevronRight /></button>
        </nav>
      )}
    </div>
  );
}

/** Toggle chip with a count, for filter panels. */
export function FilterChip({ on, onClick, children, n }: { on: boolean; onClick: () => void; children: ReactNode; n?: number }) {
  return (
    <button type="button" className={`fchip ${on ? "is-on" : ""}`} onClick={onClick} aria-pressed={on}>
      {children}{n != null && <span className="fchip-n">{fmtInt(n)}</span>}
    </button>
  );
}

/** Card with a toolbar row on top of a data table. */
export function TableCard({ toolbar, children, footer }: { toolbar: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <section className="card table-card">
      <div className="table-toolbar">{toolbar}</div>
      {children}
      {footer && <div className="table-foot">{footer}</div>}
    </section>
  );
}
