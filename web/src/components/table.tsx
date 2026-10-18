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

export const PAGE_SIZE = 50;

/** «1–50 из 416» and prev / next. */
export function Pagination({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total ? page * PAGE_SIZE + 1 : 0;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);
  return (
    <div className="pager">
      <span className="pager-range">{fmtInt(from)}–{fmtInt(to)} из {fmtInt(total)}</span>
      <div className="pager-btns">
        <button type="button" className="icon-btn is-round" onClick={() => onPage(page - 1)} disabled={page <= 0} aria-label="Предыдущая страница"><IconChevronLeft /></button>
        <button type="button" className="icon-btn is-round" onClick={() => onPage(page + 1)} disabled={page >= pages - 1} aria-label="Следующая страница"><IconChevronRight /></button>
      </div>
    </div>
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
