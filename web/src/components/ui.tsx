import { useCallback, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { fmtScore } from "../format";
import { REVIEW_THRESHOLD } from "../stats";
import { SPAN_CLASS, type RecordOut } from "../types";

/** Status pill: colored dot + mono label. ok = moss, warn = amber, flag = coral, idle = neutral. */
export type ChipStatus = "ok" | "warn" | "flag" | "idle";

export function Chip({ status, children, title, strong }: { status: ChipStatus; children: ReactNode; title?: string; strong?: boolean }) {
  return (
    <span className={`chip chip-${status} ${strong ? "is-strong" : ""}`} title={title}>
      <i className="chip-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

/** Outlined mono pill for units and limits. */
export const Tag = ({ children }: { children: ReactNode }) => <span className="tag">{children}</span>;

/** Rounded track + fill; a non-zero value is never thinner than the radius. */
export function Bar({ value, tone = "accent" }: { value: number; tone?: "accent" | "flag" | "muted" }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <span className="bar" aria-hidden="true">
      <span className={`bar-fill bar-${tone} ${pct === 0 ? "zero" : ""}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

/** Stat card: mono label, large figure, one caption line. Becomes a link or button when it leads somewhere. */
export function StatCard({ label, value, caption, tone, to, onClick }: {
  label: string;
  value: ReactNode;
  caption?: ReactNode;
  tone?: "flag" | "ok";
  to?: string;
  onClick?: () => void;
}) {
  const body = (
    <>
      <p className="stat-label">{label}</p>
      <p className={`stat-value ${tone ? `is-${tone}` : ""}`}>{value}</p>
      {caption && <p className="stat-caption">{caption}</p>}
      {(to || onClick) && <span className="stat-go" aria-hidden="true"><IconArrowRight /></span>}
    </>
  );
  if (to) return <Link to={to} className="card stat is-link">{body}</Link>;
  if (onClick) return <button type="button" className="card stat is-link" onClick={onClick}>{body}</button>;
  return <article className="card stat">{body}</article>;
}

/** Confidence tiers mapped onto pill statuses. */
export const confStatus = (c: number): ChipStatus => (c >= 0.9 ? "ok" : c >= REVIEW_THRESHOLD ? "warn" : "flag");

export function ConfChip({ c }: { c: number }) {
  return <Chip status={confStatus(c)} title={c < REVIEW_THRESHOLD ? "ниже 0,70: нужна проверка" : undefined}>{fmtScore(c)}</Chip>;
}

/** Hidden file input behind any trigger; resets so the same file can be picked twice. */
export function FileButton({ onFile, className = "btn btn-primary", children, disabled }: {
  onFile: (f: File) => void;
  className?: string;
  children: ReactNode;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className={className} onClick={() => input.current?.click()} disabled={disabled}>{children}</button>
      <input
        ref={input}
        type="file"
        accept=".json,.jsonl,application/json"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onFile(f);
        }}
      />
    </>
  );
}

/** Drag-and-drop of one file onto any element. */
export function useDropTarget(onFile: (f: File) => void) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const has = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  const handlers = {
    onDragEnter: useCallback((e: DragEvent) => { if (!has(e)) return; e.preventDefault(); depth.current++; setOver(true); }, []),
    onDragOver: useCallback((e: DragEvent) => { if (has(e)) e.preventDefault(); }, []),
    onDragLeave: useCallback(() => { depth.current = Math.max(0, depth.current - 1); if (!depth.current) setOver(false); }, []),
    onDrop: useCallback((e: DragEvent) => {
      if (!has(e)) return;
      e.preventDefault();
      depth.current = 0;
      setOver(false);
      const f = e.dataTransfer.files[0];
      if (f) onFile(f);
    }, [onFile]),
  };
  return { over, handlers };
}

/** Raw string with field spans highlighted (uses explain spans; falls back to plain text). */
export function Highlighted({ r }: { r: RecordOut }) {
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

/** Explain block shared by the records panel and the single-line parse result. */
export function ExplainDetails({ r, fieldLabels }: { r: RecordOut; fieldLabels: Record<string, string> }) {
  const filled = Object.keys(fieldLabels).filter((f) => r[f as keyof RecordOut]);
  return (
    <>
      <h4>Токены</h4>
      <div className="tokens">
        {r.tokens?.length
          ? r.tokens.map((t, i) => (
              <span key={i} className={`tok ${t.label ? SPAN_CLASS[t.label] ?? "sp-o" : ""}`} title={`${t.kind}${t.role ? " · " + t.role : ""}${t.label ? " → " + t.label : ""}`}>{t.text}</span>
            ))
          : <span className="muted">нет</span>}
      </div>
      <h4>Флаги</h4>
      <div className="tokens">
        {r.flags?.length ? r.flags.map((f) => <Chip key={f} status="warn">{f}</Chip>) : <span className="muted">нет</span>}
      </div>
      <h4>Источник значений</h4>
      <ul className="prov">
        {filled.length
          ? filled.map((f) => (
              <li key={f}><b>{fieldLabels[f]}</b> {String(r[f as keyof RecordOut])} <span className="muted mono">{r.provenance?.[f] ?? "?"}</span></li>
            ))
          : <li className="muted">нет</li>}
      </ul>
      {r.spans?.length ? (
        <>
          <h4>Правила</h4>
          <ul className="prov">{r.spans.map((s) => <li key={s.field + s.start}><b>{s.field}</b> «{s.text}» <span className="muted mono">{s.rule} · {s.score}</span></li>)}</ul>
        </>
      ) : null}
    </>
  );
}

/* Inline icons (stroke = currentColor). */
const I = ({ d, size = 18 }: { d: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
export const IconGrid = () => <I d={<><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /></>} />;
export const IconTable = () => <I d={<><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M3 9.5h18" /><path d="M9 9.5V20" /></>} />;
export const IconLayers = () => <I d={<><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 17 9 5 9-5" /></>} />;
export const IconZap = () => <I d={<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />} />;
export const IconCompare = () => <I d={<><circle cx="6" cy="6" r="3" /><circle cx="18" cy="18" r="3" /><path d="M13 6h3a2 2 0 0 1 2 2v7" /><path d="M11 18H8a2 2 0 0 1-2-2V9" /></>} />;
export const IconCheck = () => <I size={14} d={<path d="M20 6 9 17l-5-5" />} />;
export const IconSearch = () => <I d={<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>} />;
export const IconArrowRight = () => <I size={16} d={<><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>} />;
export const IconChevronRight = () => <I size={16} d={<path d="m9 6 6 6-6 6" />} />;
export const IconChevronLeft = () => <I size={16} d={<path d="m15 6-6 6 6 6" />} />;
export const IconChevronUp = () => <I size={16} d={<path d="m6 15 6-6 6 6" />} />;
export const IconChevronDown = () => <I size={16} d={<path d="m6 9 6 6 6-6" />} />;
export const IconArrowUp = () => <I size={13} d={<><path d="M12 19V5" /><path d="m6 11 6-6 6 6" /></>} />;
export const IconArrowDown = () => <I size={13} d={<><path d="M12 5v14" /><path d="m6 13 6 6 6-6" /></>} />;
export const IconSort = () => <I size={13} d={<><path d="m7 9 5-5 5 5" /><path d="m7 15 5 5 5-5" /></>} />;
export const IconExternal = () => <I size={14} d={<><path d="M14 4h6v6" /><path d="M20 4 10 14" /><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" /></>} />;
export const IconUndo = () => <I size={16} d={<><path d="M9 14 4 9l5-5" /><path d="M4 9h11a5 5 0 0 1 0 10h-3" /></>} />;
export const IconMapPin = () => <I size={16} d={<><path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21Z" /><circle cx="12" cy="9.5" r="2.5" /></>} />;
export const IconSparkle = () => <I size={16} d={<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />} />;
export const IconClose = () => <I size={16} d={<><path d="M18 6 6 18" /><path d="m6 6 12 12" /></>} />;
export const IconText = () => <I d={<><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h10" /></>} />;
export const IconUpload = ({ size = 18 }: { size?: number }) => <I size={size} d={<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" /></>} />;
export const IconDownload = () => <I d={<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" x2="12" y1="15" y2="3" /></>} />;
export const IconPin = ({ size = 20 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.2a7.6 7.6 0 0 0-7.6 7.6c0 5.7 7.6 12 7.6 12s7.6-6.3 7.6-12A7.6 7.6 0 0 0 12 2.2Zm0 10.5a2.9 2.9 0 1 1 0-5.8 2.9 2.9 0 0 1 0 5.8Z" /></svg>
);
