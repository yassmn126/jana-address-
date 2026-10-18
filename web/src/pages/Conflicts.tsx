import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { listConflicts, resolveConflict, scanConflicts } from "../api";
import { Drawer } from "../components/Drawer";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot } from "../components/Mascot";
import { DEFAULT_PAGE_SIZE, FilterChip, PAGE_SIZES, Pagination, SearchField, Segmented, TableCard } from "../components/table";
import { AI_OFF_TEXT } from "../components/record";
import { Chip, ConfChip, ErrorNote, IconCheck, IconChevronRight, IconCompare, IconText, type ChipStatus } from "../components/ui";
import { ADDRESSES, count, fmtInt, fmtScore, fmtWhen } from "../format";
import { PROVIDERS, saveProvider, savedProvider } from "../lib/ai";
import { useAssistant } from "../state/assistant";
import { useService } from "../state/service";
import { useSession } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import {
  FIELDS, FIELD_LABELS,
  type Conflict, type ConflictCounts, type ConflictReason, type ConflictStatus, type Decision, type Field, type RecordOut, type ScanResult, type Side,
} from "../types";

const REASON: Record<ConflictReason, { label: string; status: ChipStatus }> = {
  disagreement: { label: "расхождение", status: "flag" },
  low_confidence: { label: "низкая уверенность", status: "warn" },
  ai_issue: { label: "ИИ дописал значение", status: "warn" },
  ai_error: { label: "ИИ не ответил", status: "idle" },
};
const CHOICE_LABEL: Record<string, string> = { ours: "локальная модель", ai: "ИИ", custom: "выбор оператора", dismiss: "отклонено" };
const STATUSES: ConflictStatus[] = ["pending", "resolved", "dismissed"];

const conflictPath = (id: string) => `/conflicts/${encodeURIComponent(id)}`;
const Value = ({ v }: { v: string | null | undefined }) => (v ? <>{v}</> : <span className="nil">пусто</span>);

type Picks = Partial<Record<Field, Side>>;
type Overrides = Partial<Record<Field, string>>;

export function Conflicts() {
  const { react } = useAssistant();
  const { setConflictsPending } = useService();
  const { rows } = useSession();
  const { id } = useParams();
  const { search } = useLocation();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const status = (STATUSES.includes(sp.get("status") as ConflictStatus) ? sp.get("status") : "pending") as ConflictStatus;
  const q = sp.get("q") ?? "";
  const reasonF = sp.get("reason") ?? "";
  const fieldF = sp.get("field") ?? "";
  const sizeParam = Number(sp.get("size"));
  const size = (PAGE_SIZES as readonly number[]).includes(sizeParam) ? sizeParam : DEFAULT_PAGE_SIZE;

  const [items, setItems] = useState<Conflict[]>([]);
  const [counts, setCounts] = useState<ConflictCounts>({ pending: 0, resolved: 0, dismissed: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [picks, setPicks] = useState<Record<string, Picks>>({});
  const [overrides, setOverrides] = useState<Record<string, Overrides>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const applyCounts = useCallback((c: ConflictCounts) => { setCounts(c); setConflictsPending(c.pending); }, [setConflictsPending]);

  const load = useCallback(async (s: ConflictStatus) => {
    setLoading(true);
    try {
      const res = await listConflicts(s, 500);
      setItems(res.items);
      applyCounts(res.counts);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [applyCounts]);

  useEffect(() => { load(status); }, [status, load]);

  const setParam = (patch: Record<string, string | null>) => setSp((prev) => {
    const n = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(patch)) { if (!v || (k === "status" && v === "pending")) n.delete(k); else n.set(k, v); }
    if (!("page" in patch)) n.delete("page");
    return n;
  }, { replace: true });

  const facets = useMemo(() => {
    const reasons = new Map<ConflictReason, number>();
    const fields = new Map<Field, number>();
    for (const c of items) {
      for (const r of c.reasons) reasons.set(r, (reasons.get(r) ?? 0) + 1);
      for (const f of c.disagreements) fields.set(f, (fields.get(f) ?? 0) + 1);
    }
    return { reasons: [...reasons.entries()].sort((a, b) => b[1] - a[1]), fields: [...fields.entries()].sort((a, b) => b[1] - a[1]) };
  }, [items]);

  const list = useMemo(() => {
    const s = q.toLowerCase();
    return items.filter((c) =>
      (!s || c.raw_address.toLowerCase().includes(s) || c.record_id.toLowerCase().includes(s))
      && (!reasonF || c.reasons.includes(reasonF as ConflictReason))
      && (!fieldF || c.disagreements.includes(fieldF as Field)));
  }, [items, q, reasonF, fieldF]);
  const pages = Math.max(1, Math.ceil(list.length / size));
  const page = Math.min(Math.max(0, Number(sp.get("page")) || 0), pages - 1);
  const pageItems = list.slice(page * size, (page + 1) * size);

  const selected = id ? items.find((c) => c.conflict_id === id) ?? null : null;
  const idx = selected ? list.findIndex((c) => c.conflict_id === selected.conflict_id) : -1;
  const open = useCallback((cid: string | null) => {
    navigate({ pathname: cid ? conflictPath(cid) : "/conflicts", search }, { replace: !!id && !!cid });
  }, [navigate, search, id]);

  useEffect(() => {
    if (idx >= 0 && Math.floor(idx / size) !== page) { const want = Math.floor(idx / size); setParam({ page: want ? String(want) : null }); }
  }, [idx]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- decide, then move to the next pending conflict
  const decide = useCallback(async (c: Conflict, decision: Decision) => {
    const i = list.findIndex((x) => x.conflict_id === c.conflict_id);
    const after = list[i + 1]?.conflict_id ?? list[i - 1]?.conflict_id ?? null;
    setBusyId(c.conflict_id);
    try {
      const res = await resolveConflict(c.conflict_id, { reviewer: "operator", ...decision });
      setItems((xs) => xs.filter((x) => x.conflict_id !== c.conflict_id));
      applyCounts(res.counts);
      if (res.counts.pending === 0) react("happy", "Все конфликты решены.");
      open(after);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      react("error", "Решение не сохранено.");
    } finally {
      setBusyId(null);
    }
  }, [list, applyCounts, react, open]);

  const customDecision = (c: Conflict): Decision => {
    const typed = overrides[c.conflict_id] ?? {};
    const fields: Partial<Record<Field, string | null>> = {};
    for (const [f, v] of Object.entries(typed)) fields[f as Field] = v.trim() || null;
    return { choice: "custom", picks: picks[c.conflict_id] ?? {}, fields };
  };

  return (
    <>
      <PageHeader title="Конфликты" meta={`ждут решения ${fmtInt(counts.pending)} · решены ${fmtInt(counts.resolved)} · отклонены ${fmtInt(counts.dismissed)}`} />

      <div className={`content ${selected ? "has-drawer" : ""}`}>
        <ScanPanel onScanned={(c) => { applyCounts(c); if (status !== "pending") setParam({ status: null }); else load("pending"); }} rowsCount={rows.length} />

        {error && <ErrorNote text={error} onClose={() => setError(null)} />}

        <TableCard
          toolbar={
            <>
              <Segmented<ConflictStatus> label="Статус" value={status} onChange={(v) => { setParam({ status: v }); if (id) navigate({ pathname: "/conflicts", search: v === "pending" ? "" : `?status=${v}` }); }}
                options={[
                  { value: "pending", label: "Ждут решения", count: counts.pending, tone: "flag" },
                  { value: "resolved", label: "Решены", count: counts.resolved },
                  { value: "dismissed", label: "Отклонены", count: counts.dismissed },
                ]} />
              <div className="toolbar-right">
                <SearchField value={q} onChange={(v) => setParam({ q: v })} label="Адрес или ID" />
              </div>
              {(facets.reasons.length > 1 || facets.fields.length > 1) && (
                <div className="quick-filters">
                  {facets.reasons.length > 1 && facets.reasons.map(([r, n]) => (
                    <FilterChip key={r} on={reasonF === r} n={n} onClick={() => setParam({ reason: reasonF === r ? null : r })}>{REASON[r].label}</FilterChip>
                  ))}
                  {facets.reasons.length > 1 && facets.fields.length > 1 && <span className="qf-sep" />}
                  {facets.fields.length > 1 && facets.fields.map(([f, n]) => (
                    <FilterChip key={f} on={fieldF === f} n={n} onClick={() => setParam({ field: fieldF === f ? null : f })}>{FIELD_LABELS[f]}</FilterChip>
                  ))}
                </div>
              )}
            </>
          }
          footer={list.length > DEFAULT_PAGE_SIZE || size !== DEFAULT_PAGE_SIZE
            ? <Pagination page={page} total={list.length} size={size} onPage={(n) => setParam({ page: n ? String(n) : null })} onSize={(n) => setParam({ size: n === DEFAULT_PAGE_SIZE ? null : String(n) })} />
            : null}
        >
          <div className="table-scroll">
            <table className="dtable">
              <thead>
                <tr>
                  <th>Адрес</th>
                  <th className="col-opt">Поля</th>
                  <th>{status === "pending" ? "Причина" : "Решение"}</th>
                  <th className="num">Уверенность</th>
                  <th className="col-opt">{status === "pending" ? "Создан" : "Решён"}</th>
                  <th aria-label="Открыть" />
                </tr>
              </thead>
              <tbody>
                {pageItems.map((c) => (
                  <tr key={c.conflict_id} className={c.conflict_id === selected?.conflict_id ? "is-selected" : ""} tabIndex={0}
                    onClick={() => open(c.conflict_id)} onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) open(c.conflict_id); }}>
                    <td className="cell-main">
                      <span className="cell-title" title={c.raw_address}>{c.raw_address}</span>
                      <span className="cell-sub mono">{c.record_id}</span>
                    </td>
                    <td className="col-opt">
                      <span className="field-tags">
                        {c.disagreements.slice(0, 2).map((f) => <span key={f} className="field-tag">{FIELD_LABELS[f]}</span>)}
                        {c.disagreements.length > 2 && <span className="field-tag is-more" title={c.disagreements.slice(2).map((f) => FIELD_LABELS[f]).join(", ")}>+{c.disagreements.length - 2}</span>}
                        {!c.disagreements.length && <span className="muted">совпадают</span>}
                      </span>
                    </td>
                    <td>
                      {status === "pending"
                        ? c.reasons[0] && <Chip status={REASON[c.reasons[0]].status}>{REASON[c.reasons[0]].label}{c.reasons.length > 1 ? ` +${c.reasons.length - 1}` : ""}</Chip>
                        : <Chip status={c.status === "dismissed" ? "idle" : "ok"}>{CHOICE_LABEL[c.resolution?.choice ?? "dismiss"]}</Chip>}
                    </td>
                    <td className="num">{c.confidence != null ? <ConfChip c={c.confidence} /> : <span className="muted">нет</span>}</td>
                    <td className="nowrap muted col-opt">{fmtWhen(((status === "pending" ? c.created_at : c.resolved_at) ?? c.updated_at) * 1000)}</td>
                    <td className="cell-go"><IconChevronRight /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!list.length && (
              <div className="table-empty">
                {loading ? <p className="muted">Загрузка…</p> : (
                  <>
                    <Mascot pose={status === "pending" && !q ? "celebrating" : "magnifier"} className="table-empty-mascot" />
                    <p>{q || reasonF || fieldF ? "Нет конфликтов по фильтру" : status === "pending" ? "Нет конфликтов, ждущих решения" : status === "resolved" ? "Решённых конфликтов нет" : "Отклонённых конфликтов нет"}</p>
                  </>
                )}
              </div>
            )}
          </div>
        </TableCard>
      </div>

      {selected && (
        <ConflictDrawer c={selected} position={idx >= 0 ? `${idx + 1} из ${list.length}` : undefined}
          picks={picks[selected.conflict_id] ?? {}} overrides={overrides[selected.conflict_id] ?? {}} busy={busyId === selected.conflict_id}
          onClose={() => open(null)}
          onPrev={idx > 0 ? () => open(list[idx - 1].conflict_id) : undefined}
          onNext={idx >= 0 && idx < list.length - 1 ? () => open(list[idx + 1].conflict_id) : undefined}
          onPick={(f, side) => setPicks((p) => ({ ...p, [selected.conflict_id]: { ...p[selected.conflict_id], [f]: side } }))}
          onType={(f, v) => setOverrides((o) => ({ ...o, [selected.conflict_id]: { ...o[selected.conflict_id], [f]: v } }))}
          onDecide={(d) => decide(selected, d === "custom" ? customDecision(selected) : { choice: d })} />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ scan: local parser vs external labeler → queue */

function ScanPanel({ onScanned, rowsCount }: { onScanned: (c: ConflictCounts) => void; rowsCount: number }) {
  const { react } = useAssistant();
  const { rows } = useSession();
  const { ai } = useService();
  const [provider, setProvider] = useState(() => savedProvider().provider);
  const [model, setModel] = useState(() => savedProvider().model);
  const [pasting, setPasting] = useState(false);
  const [paste, setPaste] = useState("");
  const [scanning, setScanning] = useState<string | null>(null);
  const [last, setLast] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const choose = (pid: string) => {
    const m = PROVIDERS.find((p) => p.id === pid)?.model ?? "";
    setProvider(pid);
    setModel(m);
    saveProvider(pid, m);
  };

  const scan = async (records: { id: string; raw_address: string }[]) => {
    if (!records.length) return;
    setError(null);
    const total = { scanned: 0, needs_review: 0, queued: 0, updated: 0, kept_decided: 0 };
    let res: ScanResult | null = null;
    try {
      for (let i = 0; i < records.length; i += 50) {
        setScanning(`${Math.min(i + 50, records.length)} из ${records.length}`);
        res = await scanConflicts(records.slice(i, i + 50), provider, model);
        for (const k of Object.keys(total) as (keyof typeof total)[]) total[k] += res[k];
      }
      if (res) { setLast({ ...res, ...total }); onScanned(res.counts); }
      if (total.needs_review) react("confused", `${count(total.needs_review, ADDRESSES)} из ${total.scanned} требуют решения.`, undefined, 9000);
      else react("happy", `ИИ согласен со всеми ${total.scanned} ответами.`);
    } catch (e) {
      const down = (e as { status?: number }).status === 502 && provider !== "rules";
      setError(`${down ? `«${PROVIDERS.find((x) => x.id === provider)?.label ?? provider}» не отвечает на этом сервере. Выберите «Только правила». ` : ""}${e instanceof Error ? e.message : String(e)}`);
      react("error", "Сверка с ИИ не выполнена.");
    } finally {
      setScanning(null);
    }
  };

  const lines = paste.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const queue = rows.filter((r) => r.confidence < REVIEW_THRESHOLD && !r.reviewed);
  const toInput = (rs: RecordOut[]) => rs.map((r) => ({ id: r.id, raw_address: r.raw_address }));
  const scanAll = () => {
    const calls = Math.ceil(rows.length / 50);
    if (rows.length <= 500 || window.confirm(`Сверить ${count(rows.length, ADDRESSES)}: ${calls} запросов к ИИ. Продолжить?`)) scan(toInput(rows));
  };

  return (
    <section className="card scan">
      <div className="scan-row">
        <div className="scan-title">
          <span className="scan-icon"><IconCompare /></span>
          <div>
            <h3>Сверка с внешним ИИ</h3>
            <p className="muted">{ai === false ? AI_OFF_TEXT : "В очередь попадает запись с расхождением полей, уверенностью ниже 0,70 или значением, которого нет в строке."}</p>
          </div>
        </div>
        {ai !== false && <div className="scan-controls">
          <select className="field select-pill" value={provider} onChange={(e) => choose(e.target.value)} aria-label="Внешний ИИ">
            {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <label className="inline-field">
            <span>Модель</span>
            <input className="mono" value={model} onChange={(e) => { setModel(e.target.value); saveProvider(provider, e.target.value); }} spellCheck={false} title="Имя модели на сервере" />
          </label>
          <button type="button" className="btn btn-ghost" onClick={() => setPasting((v) => !v)} aria-expanded={pasting}><IconText />Список</button>
          {queue.length > 0 && queue.length < rowsCount && (
            <button type="button" className="btn btn-ghost" onClick={scanAll} disabled={!!scanning}>Все {fmtInt(rowsCount)}</button>
          )}
          <button type="button" className="btn btn-primary" onClick={() => scan(toInput(queue.length ? queue : rows))} disabled={!!scanning || !rowsCount}>
            {scanning ? `Сверяю ${scanning}`
              : !rowsCount ? "Нет записей в сессии"
              : queue.length ? `Сверить очередь · ${fmtInt(queue.length)}`
              : `Сверить ${count(rowsCount, ADDRESSES)}`}
          </button>
        </div>}
      </div>
      {pasting && ai !== false && (
        <form className="scan-paste" onSubmit={(e) => { e.preventDefault(); scan(lines.map((l, i) => ({ id: `conflict_${Date.now()}_${i + 1}`, raw_address: l }))); }}>
          <textarea className="field" rows={3} value={paste} onChange={(e) => setPaste(e.target.value)} aria-label="Адреса, по одному в строке" spellCheck={false} />
          <button type="submit" className="btn btn-ghost" disabled={!!scanning || !lines.length}>Сверить {count(lines.length, ADDRESSES)}</button>
        </form>
      )}
      {(last || error) && (
        <div className="scan-status">
          {error ? <span className="error">{error}</span> : last && (
            <>
              <Chip status="idle">проверено {last.scanned}</Chip>
              <Chip status={last.needs_review ? "flag" : "ok"}>к оператору {last.needs_review}</Chip>
              <Chip status="idle">новых {last.queued} · обновлено {last.updated} · уже решены {last.kept_decided}</Chip>
            </>
          )}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ compare drawer */

function ConflictDrawer({ c, position, picks, overrides, busy, onClose, onPrev, onNext, onPick, onType, onDecide }: {
  c: Conflict;
  position?: string;
  picks: Picks;
  overrides: Overrides;
  busy: boolean;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  onPick: (f: Field, side: Side) => void;
  onType: (f: Field, v: string) => void;
  onDecide: (choice: "ours" | "ai" | "custom" | "dismiss") => void;
}) {
  const pending = c.status === "pending";
  const disputed = new Set(c.disagreements);
  const shown: Field[] = [
    ...FIELDS.filter((f) => disputed.has(f)),
    ...FIELDS.filter((f) => !disputed.has(f) && (c.ours[f] || c.ai[f])),
  ];
  const hasCustom = Object.keys(picks).length > 0 || Object.values(overrides).some((v) => v !== undefined);
  const finalOf = (f: Field) => overrides[f] ?? (picks[f] === "ai" ? c.ai[f] ?? "" : picks[f] === "ours" ? c.ours[f] ?? "" : c.ours[f] ?? "");

  // 1 = local model · 2 = AI · D = dismiss
  useEffect(() => {
    if (!pending) return;
    const h = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || busy || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.key === "1") onDecide("ours");
      else if (ev.key === "2" && c.disagreements.length) onDecide("ai");
      else if (ev.code === "KeyD") onDecide("dismiss");
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [pending, busy, c, onDecide]);

  return (
    <Drawer open label={`Конфликт ${c.record_id}`} position={position} onClose={onClose} onPrev={onPrev} onNext={onNext}
      header={
        <div className="drawer-title">
          <p className="drawer-id mono">{c.record_id}</p>
          {pending
            ? <Chip status="flag">ждёт решения</Chip>
            : <Chip status={c.status === "dismissed" ? "idle" : "ok"} strong={c.status === "resolved"}>{CHOICE_LABEL[c.resolution?.choice ?? "dismiss"]}</Chip>}
        </div>
      }
      footer={pending ? (
        <>
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => onDecide("dismiss")}>Отклонить<kbd>D</kbd></button>
          <span className="drawer-foot-spacer" />
          {hasCustom && <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => onDecide("custom")}>Сохранить выбор</button>}
          <button type="button" className="btn btn-ghost" disabled={busy || !c.disagreements.length} onClick={() => onDecide("ai")}>ИИ<kbd>2</kbd></button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onDecide("ours")}>Локальная<kbd className="kbd-inv">1</kbd></button>
        </>
      ) : (
        <p className="muted">
          {fmtWhen((c.resolved_at ?? c.updated_at) * 1000)}{c.resolution?.reviewer ? ` · ${c.resolution.reviewer}` : ""}{c.resolution?.note ? ` · ${c.resolution.note}` : ""}
        </p>
      )}
    >
      <section className="insp-raw">
        <p className="eyebrow">Исходная строка</p>
        <p className="insp-raw-text">{c.raw_address}</p>
        <div className="reason-row">
          {c.reasons.map((r) => (
            <Chip key={r} status={REASON[r].status}>
              {r === "low_confidence" && c.confidence != null ? `${REASON[r].label} ${fmtScore(c.confidence)}` : REASON[r].label}
            </Chip>
          ))}
        </div>
      </section>

      <section className="cmp">
        <div className="cmp-head">
          <span>Поле</span>
          <span>Локальная модель{c.confidence != null ? <b className="mono"> {fmtScore(c.confidence)}</b> : null}</span>
          <span>ИИ <b className="mono">{c.model || c.provider}</b></span>
        </div>
        {shown.map((f) => {
          const dis = disputed.has(f);
          return (
            <div key={f} className={`cmp-row ${dis ? "is-dispute" : ""}`}>
              <span className="cmp-field">{dis && <i className="cmp-dot" aria-hidden="true" />}{FIELD_LABELS[f]}</span>
              {(["ours", "ai"] as Side[]).map((side) => {
                const v = side === "ours" ? c.ours[f] : c.ai[f];
                return dis && pending ? (
                  <button key={side} type="button" className={`pick ${picks[f] === side ? "is-picked" : ""}`} onClick={() => onPick(f, side)} aria-pressed={picks[f] === side}>
                    <Value v={v} />{picks[f] === side && <IconCheck />}
                  </button>
                ) : <span key={side} className={`cmp-val ${dis ? "" : "is-same"}`}><Value v={v} /></span>;
              })}
              {dis && pending && (
                <label className="cmp-final">
                  <span>Итог</span>
                  <input className="field" value={finalOf(f)} onChange={(e) => onType(f, e.target.value)} aria-label={`Итог · ${FIELD_LABELS[f]}`} spellCheck={false} />
                </label>
              )}
              {!pending && dis && c.resolution?.fields && (
                <span className="cmp-final is-read"><span>Итог</span><b className="cmp-final-val"><Value v={c.resolution.fields[f]} /></b></span>
              )}
            </div>
          );
        })}
        {!shown.length && <p className="muted cmp-empty">Обе стороны не нашли ни одного поля.</p>}
      </section>

      {c.issues.length > 0 && (
        <section className="cmp-issues">
          <p className="eyebrow">Замечания ИИ</p>
          <ul>{c.issues.map((i) => <li key={i}>{i}</li>)}</ul>
        </section>
      )}
    </Drawer>
  );
}
