import { useCallback, useEffect, useState } from "react";
import { listConflicts, resolveConflict, scanConflicts } from "../api";
import { MascotAssistant, type MascotState, type Mood } from "../components/Mascot";
import { Chip, IconCheck, IconCompare, type ChipStatus } from "../components/ui";
import {
  FIELDS, FIELD_LABELS,
  type Conflict, type ConflictCounts, type ConflictReason, type ConflictStatus, type Decision, type Field, type RecordOut, type ScanResult, type Side,
} from "../types";

/** The "external AI" options of eval/llm_verify.py (training/label_llm.py PROVIDERS). */
const PROVIDERS = [
  { id: "openai-compatible", label: "Локальный LLM · openai-compatible", model: "", hint: "vLLM в кампусе: OPENAI_BASE_URL, данные не уходят наружу" },
  { id: "rules", label: "Только правила · rules", model: "rules", hint: "без сети и ИИ — проверка самой цепочки" },
];
const PROVIDER_KEY = "jana-address/conflicts/provider";

const REASON: Record<ConflictReason, { label: string; status: ChipStatus }> = {
  disagreement: { label: "расхождение", status: "flag" },
  low_confidence: { label: "низкая уверенность", status: "warn" },
  ai_issue: { label: "ИИ переписал значение", status: "warn" },
  ai_error: { label: "ИИ не ответил", status: "idle" },
};
const CHOICE_LABEL: Record<string, string> = { ours: "локальная модель", ai: "AI API", custom: "выбор оператора", dismiss: "отклонено" };
const TABS: { status: ConflictStatus; label: string }[] = [
  { status: "pending", label: "Ожидают" },
  { status: "resolved", label: "Решены" },
  { status: "dismissed", label: "Отклонены" },
];

const when = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "");
const Value = ({ v }: { v: string | null | undefined }) => (v ? <>{v}</> : <span className="nil">null</span>);

type Picks = Partial<Record<Field, Side>>;
type Overrides = Partial<Record<Field, string>>;

export function Conflicts({ reaction, react, rows, onCounts }: {
  reaction: MascotState | null;
  react: (mood: Mood, text: string, action?: MascotState["action"], ms?: number) => void;
  rows: RecordOut[];
  onCounts: (c: ConflictCounts) => void;
}) {
  const [status, setStatus] = useState<ConflictStatus>("pending");
  const [items, setItems] = useState<Conflict[]>([]);
  const [counts, setCounts] = useState<ConflictCounts>({ pending: 0, resolved: 0, dismissed: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState(() => {
    try { return localStorage.getItem(PROVIDER_KEY) || "anthropic"; } catch { return "anthropic"; }
  });
  const [model, setModel] = useState(() => PROVIDERS.find((p) => p.id === provider)?.model ?? "");
  const [paste, setPaste] = useState("");
  const [scanning, setScanning] = useState<string | null>(null);
  const [lastScan, setLastScan] = useState<ScanResult | null>(null);
  const [picks, setPicks] = useState<Record<string, Picks>>({});
  const [overrides, setOverrides] = useState<Record<string, Overrides>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  const applyCounts = useCallback((c: ConflictCounts) => { setCounts(c); onCounts(c); }, [onCounts]);

  const load = useCallback(async (s: ConflictStatus) => {
    setLoading(true);
    try {
      const res = await listConflicts(s);
      setItems(res.items);
      applyCounts(res.counts);
      setError(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [applyCounts]);

  useEffect(() => { load(status); }, [status, load]);

  const chooseProvider = (id: string) => {
    setProvider(id);
    setModel(PROVIDERS.find((p) => p.id === id)?.model ?? "");
    try { localStorage.setItem(PROVIDER_KEY, id); } catch { /* storage blocked */ }
  };

  // ---------------------------------------------------------------- scan: local parser vs AI → queue (≤ 50 per call)
  const scan = async (records: { id: string; raw_address: string }[]) => {
    if (!records.length) return;
    setError(null);
    const total = { scanned: 0, needs_review: 0, queued: 0, updated: 0, kept_decided: 0 };
    let last: ScanResult | null = null;
    react("waiting", `Сравниваю ${records.length} адресов с ИИ…`, undefined, 600000);
    try {
      for (let i = 0; i < records.length; i += 50) {
        setScanning(`${Math.min(i + 50, records.length)} / ${records.length}`);
        last = await scanConflicts(records.slice(i, i + 50), provider, model);
        for (const k of Object.keys(total) as (keyof typeof total)[]) total[k] += last[k];
      }
      if (last) {
        setLastScan({ ...last, ...total });
        applyCounts(last.counts);
      }
      if (total.needs_review) react("confused", `Нашёл ${total.needs_review} из ${total.scanned}, где нужен человек. Решите их ниже.`, undefined, 9000);
      else react("happy", `ИИ согласен со всеми ${total.scanned} ответами — конфликтов нет.`);
      if (status !== "pending") setStatus("pending"); else load("pending");
    } catch (e) {
      const msg = String(e instanceof Error ? e.message : e);
      setError(msg);
      react("confused", "Не получилось сравнить с ИИ — подробности в красной плашке.");
    } finally {
      setScanning(null);
    }
  };

  const scanRows = () => scan(rows.map((r) => ({ id: r.id, raw_address: r.raw_address })));
  const scanPaste = () => {
    const lines = paste.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    scan(lines.map((l, i) => ({ id: `conflict_${Date.now()}_${i + 1}`, raw_address: l })));
  };

  // ---------------------------------------------------------------- decide
  const decide = useCallback(async (c: Conflict, decision: Decision) => {
    setBusyId(c.conflict_id);
    try {
      const res = await resolveConflict(c.conflict_id, { reviewer: "operator", ...decision });
      setItems((xs) => xs.filter((x) => x.conflict_id !== c.conflict_id));
      applyCounts(res.counts);
      const left = res.counts.pending;
      const what = decision.choice === "dismiss" ? "Отклонено" : `Сохранено (${CHOICE_LABEL[decision.choice]})`;
      if (left === 0) react("happy", `${what}. Очередь конфликтов пуста!`);
      else react("happy", `${what}. Осталось решить: ${left}.`, undefined, 3500);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      react("confused", "Не получилось сохранить решение.");
    } finally {
      setBusyId(null);
    }
  }, [applyCounts, react]);

  const customDecision = (c: Conflict): Decision => {
    const typed = overrides[c.conflict_id] ?? {};
    const fields: Partial<Record<Field, string | null>> = {};
    for (const [f, v] of Object.entries(typed)) fields[f as Field] = v.trim() || null;
    return { choice: "custom", picks: picks[c.conflict_id] ?? {}, fields };
  };

  // 1 = keep local · 2 = take AI · D = dismiss — on the card under the pointer / with focus
  useEffect(() => {
    if (status !== "pending") return;
    const h = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || busyId) return;
      const c = items.find((x) => x.conflict_id === activeId);
      if (!c) return;
      if (ev.key === "1") decide(c, { choice: "ours" });
      else if (ev.key === "2") decide(c, { choice: "ai" });
      else if (ev.code === "KeyD") decide(c, { choice: "dismiss" });
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [status, items, activeId, busyId, decide]);

  const mascot: MascotState = reaction ?? (
    error ? { mood: "confused", text: "Что-то пошло не так — подробности в красной плашке." }
    : counts.pending ? { mood: "idle", text: `Конфликтов ждут решения: ${counts.pending}. Сравните и выберите вариант в один клик.` }
    : { mood: "idle", text: "Конфликтов нет. Проверьте записи через ИИ — расхождения появятся здесь." });
  const prov = PROVIDERS.find((p) => p.id === provider);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Human-in-the-loop · конфликты</p>
          <h1 className="title"><span className="dim">Локальная модель против ИИ.</span><br />Решает человек — в один клик.</h1>
        </div>
        <MascotAssistant state={mascot} />
      </div>

      {/* Scan: compare the local parser with the AI labeler and queue what needs a human */}
      <section className="card scan">
        <div className="scan-intro">
          <p className="eyebrow muted">Проверка через ИИ</p>
          <p>В очередь попадает запись, если поля расходятся, уверенность модели ниже 0,70 или ИИ переписал значение, которого нет в строке.</p>
        </div>
        <div className="scan-controls">
          <label className="scan-field">
            <span className="metric-label">Внешний ИИ</span>
            <select className="field" value={provider} onChange={(e) => chooseProvider(e.target.value)}>
              {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
            <span className="muted scan-hint">{prov?.hint}</span>
          </label>
          <label className="scan-field">
            <span className="metric-label">Модель</span>
            <input className="field mono" value={model} onChange={(e) => setModel(e.target.value)} placeholder="имя модели на сервере" spellCheck={false} />
          </label>
          <div className="scan-actions">
            <button type="button" className="btn btn-primary" onClick={scanRows} disabled={!!scanning || !rows.length}>
              <IconCompare />{rows.length ? `Проверить загруженные (${rows.length})` : "Нет загруженных записей"}
            </button>
            <textarea className="field" rows={2} value={paste} onChange={(e) => setPaste(e.target.value)} placeholder="или вставьте адреса, по одному в строке" />
            <button type="button" className="btn btn-ghost btn-sm" onClick={scanPaste} disabled={!!scanning || !paste.trim()}>Проверить вставленные</button>
          </div>
        </div>
        {(scanning || lastScan) && (
          <div className="scan-status">
            {scanning
              ? <Chip status="idle">сравниваю… {scanning}</Chip>
              : lastScan && <>
                  <Chip status="idle">проверено {lastScan.scanned}</Chip>
                  <Chip status={lastScan.needs_review ? "flag" : "ok"}>к человеку {lastScan.needs_review}</Chip>
                  <Chip status="idle">новых {lastScan.queued} · обновлено {lastScan.updated} · уже решены {lastScan.kept_decided}</Chip>
                </>}
          </div>
        )}
      </section>

      {error && <div className="error" role="alert">{error}</div>}

      {/* Queue */}
      <div className="queue-head">
        <div className="seg" role="tablist" aria-label="Статус">
          {TABS.map((t) => (
            <button key={t.status} type="button" role="tab" aria-selected={status === t.status}
              className={`seg-btn ${status === t.status ? "is-active" : ""}`} onClick={() => setStatus(t.status)}>
              {t.label}<span className={`seg-count ${t.status === "pending" && counts.pending ? "flag" : ""}`}>{counts[t.status]}</span>
            </button>
          ))}
        </div>
        {status === "pending" && items.length > 0 && (
          <div className="hotkeys"><span><kbd>1</kbd> локальная</span><span><kbd>2</kbd> ИИ</span><span><kbd>D</kbd> отклонить</span><span>· на карточке под курсором</span></div>
        )}
      </div>

      {loading && !items.length ? <div className="card empty">Загружаю очередь…</div>
        : !items.length ? (
          <div className="card empty">
            {status === "pending" ? "Очередь пуста. Проверьте записи через ИИ — расхождения появятся здесь." : "Пока ничего."}
          </div>
        ) : (
          <div className="conflicts">
            {items.map((c) => (
              <ConflictCard key={c.conflict_id} c={c}
                picks={picks[c.conflict_id] ?? {}} overrides={overrides[c.conflict_id] ?? {}}
                busy={busyId === c.conflict_id} active={activeId === c.conflict_id}
                onActivate={() => setActiveId(c.conflict_id)}
                onPick={(f, side) => setPicks((p) => ({ ...p, [c.conflict_id]: { ...p[c.conflict_id], [f]: side } }))}
                onType={(f, v) => setOverrides((o) => ({ ...o, [c.conflict_id]: { ...o[c.conflict_id], [f]: v } }))}
                onDecide={(d) => decide(c, d === "custom" ? customDecision(c) : { choice: d })}
              />
            ))}
          </div>
        )}
    </div>
  );
}

function ConflictCard({ c, picks, overrides, busy, active, onActivate, onPick, onType, onDecide }: {
  c: Conflict;
  picks: Picks;
  overrides: Overrides;
  busy: boolean;
  active: boolean;
  onActivate: () => void;
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
  const finalOf = (f: Field) => overrides[f] ?? (picks[f] === "ai" ? c.ai[f] ?? "" : picks[f] === "ours" ? c.ours[f] ?? "" : "");

  return (
    <article className={`card conflict ${active ? "is-active" : ""}`} tabIndex={0} onMouseEnter={onActivate} onFocus={onActivate} aria-busy={busy}>
      <header className="conflict-head">
        <div className="conflict-raw">
          <p className="eyebrow muted">{c.record_id}</p>
          <p className="raw">{c.raw_address}</p>
        </div>
        <div className="conflict-tags">
          {c.reasons.map((r) => (
            <Chip key={r} status={REASON[r].status}>
              {r === "disagreement" ? `${REASON[r].label}: ${c.disagreements.map((f) => FIELD_LABELS[f]).join(", ")}`
                : r === "low_confidence" ? `${REASON[r].label} ${c.confidence?.toFixed(2)}` : REASON[r].label}
            </Chip>
          ))}
          <Chip status="idle">{c.provider} · {c.model}</Chip>
        </div>
      </header>

      <div className="compare-wrap">
        <table className="compare">
          <thead>
            <tr>
              <th>Поле</th>
              <th>Локальная модель{c.confidence != null && <span className="th-meta"> · {c.confidence.toFixed(2)}</span>}</th>
              <th>AI API</th>
              {pending ? <th>Итог (своё значение)</th> : <th>Решение</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((f) => {
              const dis = disputed.has(f);
              return (
                <tr key={f} className={dis ? "is-dispute" : "is-same"}>
                  <td className="f">{FIELD_LABELS[f]}</td>
                  {(["ours", "ai"] as Side[]).map((side) => (
                    <td key={side}>
                      {dis && pending ? (
                        <button type="button" className={`pick ${picks[f] === side ? "is-picked" : ""}`} onClick={() => onPick(f, side)}
                          aria-pressed={picks[f] === side} title={side === "ours" ? "взять значение локальной модели" : "взять значение ИИ"}>
                          <Value v={side === "ours" ? c.ours[f] : c.ai[f]} />
                          {picks[f] === side && <IconCheck />}
                        </button>
                      ) : <span className={dis ? "" : "same"}><Value v={side === "ours" ? c.ours[f] : c.ai[f]} /></span>}
                    </td>
                  ))}
                  <td>
                    {pending
                      ? dis ? <input className="field final" value={finalOf(f)} onChange={(e) => onType(f, e.target.value)} placeholder="выберите слева или впишите" aria-label={`Итог · ${FIELD_LABELS[f]}`} />
                            : <span className="same muted">совпадает</span>
                      : <span className={c.resolution?.fields && c.resolution.fields[f] !== c.ours[f] ? "changed" : ""}><Value v={c.resolution?.fields?.[f]} /></span>}
                  </td>
                </tr>
              );
            })}
            {!shown.length && <tr><td colSpan={4} className="muted">Обе стороны не нашли ни одного поля.</td></tr>}
          </tbody>
        </table>
      </div>

      {c.issues.length > 0 && <p className="conflict-issues mono">замечания: {c.issues.join(" · ")}</p>}

      <footer className="conflict-actions">
        {pending ? (
          <>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onDecide("ours")}>Принять локальную <kbd>1</kbd></button>
            <button type="button" className="btn btn-ghost" disabled={busy || !c.disagreements.length} onClick={() => onDecide("ai")}>Принять ИИ <kbd>2</kbd></button>
            {hasCustom && <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => onDecide("custom")}>Сохранить выбор</button>}
            <button type="button" className="link-btn muted-link" disabled={busy} onClick={() => onDecide("dismiss")}>Отклонить <kbd>D</kbd></button>
          </>
        ) : (
          <>
            <Chip status={c.status === "dismissed" ? "idle" : "ok"}>{CHOICE_LABEL[c.resolution?.choice ?? "dismiss"]}</Chip>
            <span className="muted mono" style={{ fontSize: 12 }}>{when(c.resolved_at)}{c.resolution?.reviewer ? ` · ${c.resolution.reviewer}` : ""}</span>
          </>
        )}
      </footer>
    </article>
  );
}
