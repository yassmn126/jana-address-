import type { ReactNode } from "react";
import { SPAN_CLASS, type RecordOut } from "../types";

/** Figma "Chip" (component 8:26): status dot + mono label. ok = moss, warn = yellow, flag = coral, idle = muted. */
export type ChipStatus = "ok" | "warn" | "flag" | "idle";

export function Chip({ status, children, title }: { status: ChipStatus; children: ReactNode; title?: string }) {
  return (
    <span className={`chip chip-${status}`} title={title}>
      <i className="chip-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

/** Outlined mono pill (slide 10 · "tag · estimate"). */
export const Tag = ({ children }: { children: ReactNode }) => <span className="tag">{children}</span>;

/** Track + bar (slide 10): glass track, fully rounded fill, min 8 px when non-zero. */
export function Bar({ value, tone = "accent" }: { value: number; tone?: "accent" | "flag" }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <span className="bar" aria-hidden="true">
      <span className={`bar-fill ${tone === "flag" ? "flag" : ""} ${pct === 0 ? "zero" : ""}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

/** Metric card (slide 06): mono label, big figure, optional chip, spacer, caption. */
export function MetricCard({ label, value, tone, chip, caption, onClick, title }: {
  label: string;
  value: ReactNode;
  tone?: "accent" | "flag" | "dim";
  chip?: ReactNode;
  caption: ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  const body = (
    <>
      <p className="metric-label">{label}</p>
      <p className={`metric-value ${tone ?? ""}`}>{value}</p>
      {chip}
      <div className="metric-spacer" />
      <p className="metric-caption">{caption}</p>
    </>
  );
  return onClick
    ? <button type="button" className="card metric" onClick={onClick} title={title}>{body}</button>
    : <article className="card metric">{body}</article>;
}

/** Confidence tiers of the original review UI mapped onto the Chip statuses. */
export const confStatus = (c: number): ChipStatus => (c >= 0.9 ? "ok" : c >= 0.7 ? "warn" : "flag");

export function ConfChip({ c }: { c: number }) {
  return <Chip status={confStatus(c)} title={c < 0.7 ? "ниже 0.70 — на проверку" : undefined}>{c.toFixed(2)}</Chip>;
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

/** Explain block shared by the batch side panel and the quick test result. */
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

/* Small inline icons (stroke = currentColor). */
const I = ({ d, size = 16 }: { d: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
export const IconGrid = () => <I d={<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>} />;
export const IconLayers = () => <I d={<><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 17 9 5 9-5" /></>} />;
export const IconZap = () => <I d={<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />} />;
export const IconUpload = ({ size = 18 }: { size?: number }) => <I size={size} d={<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" /></>} />;
export const IconDownload = () => <I d={<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" x2="12" y1="15" y2="3" /></>} />;
export const IconPin = () => (
  <svg width={20} height={20} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.2a7.6 7.6 0 0 0-7.6 7.6c0 5.7 7.6 12 7.6 12s7.6-6.3 7.6-12A7.6 7.6 0 0 0 12 2.2Zm0 10.5a2.9 2.9 0 1 1 0-5.8 2.9 2.9 0 0 1 0 5.8Z" /></svg>
);
