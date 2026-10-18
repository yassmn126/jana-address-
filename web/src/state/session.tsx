import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { parseBatch, sendCorrection } from "../api";
import { ADDRESSES, count, RECORDS } from "../format";
import { PATHS } from "../routes";
import { computeStats, REVIEW_THRESHOLD, type Stats } from "../stats";
import { FIELDS, JURY_KEYS, type Field, type RecordOut } from "../types";
import { useAssistant } from "./assistant";
import { useService } from "./service";
import { parseInputFile, uniqueIds, type Input } from "../lib/input";
import { deleteValue, loadValue, saveValue } from "./storage";


/** The operator's working set: the parsed records, their edits and the review progress. */
interface SessionCtx {
  ready: boolean;                // the saved session has been restored (or there was none)
  rows: RecordOut[];
  stats: Stats;
  busy: boolean;
  progress: { done: number; total: number } | null;
  error: string | null;
  setError: (e: string | null) => void;
  lastRun: { ms: number; count: number; at: number } | null;
  load: (input: Input[]) => Promise<void>;
  loadFile: (file: File) => Promise<void>;
  loadText: (text: string) => void;
  parseOne: (raw: string) => Promise<RecordOut | null>;
  addRow: (rec: RecordOut) => void;
  update: (id: string, field: Field, value: string | null) => void;
  revert: (id: string) => void;
  accept: (r: RecordOut) => Promise<boolean>;
  acceptMany: (ids: string[]) => Promise<number>;
  exportJson: (ids?: string[]) => void;
  clear: () => void;
  showQueue: () => void;
}

const Ctx = createContext<SessionCtx | null>(null);

/** The session lives in IndexedDB (state/storage.ts), so a reload or a new tab keeps the file. */
const SAVE_KEY = "current";
const LEGACY_KEY = "jana-address/session/v1";       // sessionStorage copy of the first redesign build
const CHUNK = 1000;
type Saved = { rows: RecordOut[]; lastRun: SessionCtx["lastRun"] };

/** One shared promise: StrictMode runs effects twice, and the legacy copy can be read only once. */
let restoring: Promise<Saved | null> | null = null;
const restore = () => (restoring ??= readSaved());

async function readSaved(): Promise<Saved | null> {
  try {
    const legacy = sessionStorage.getItem(LEGACY_KEY);
    if (legacy) {
      sessionStorage.removeItem(LEGACY_KEY);
      const s = JSON.parse(legacy);
      if (Array.isArray(s.rows)) return { rows: s.rows, lastRun: s.lastRun ?? null };
    }
  } catch { /* storage blocked or corrupt */ }
  const saved = await loadValue<Saved>(SAVE_KEY);
  return saved && Array.isArray(saved.rows) ? saved : null;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const { react } = useAssistant();
  const { refreshArchive } = useService();
  const [ready, setReady] = useState(false);
  const [rows, setRows] = useState<RecordOut[]>([]);
  const [lastRun, setLastRun] = useState<SessionCtx["lastRun"]>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<SessionCtx["progress"]>(null);
  const [error, setError] = useState<string | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  useEffect(() => {
    let alive = true;
    restore().then((saved) => {
      if (!alive) return;
      if (saved && !rowsRef.current.length) { setRows(saved.rows); setLastRun(saved.lastRun); }
      setReady(true);
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!ready) return;
    const t = window.setTimeout(() => {
      if (rows.length) saveValue(SAVE_KEY, { rows, lastRun });
      else deleteValue(SAVE_KEY);
    }, 600);
    return () => window.clearTimeout(t);
  }, [rows, lastRun, ready]);

  const stats = useMemo(() => computeStats(rows), [rows]);

  const showQueue = useCallback(() => navigate(PATHS.queue), [navigate]);

  const load = useCallback(async (input: Input[]) => {
    if (!input.length) return;
    setBusy(true);
    setError(null);
    setProgress({ done: 0, total: input.length });
    const t0 = performance.now();
    try {
      const out: RecordOut[] = [];
      let repeated = 0;
      for (let i = 0; i < input.length; i += CHUNK) {
        out.push(...(await parseBatch(input.slice(i, i + CHUNK), true)));
        const u = uniqueIds(out);
        repeated = u.repeated;
        setRows(u.rows);
        setProgress({ done: out.length, total: input.length });
      }
      setLastRun({ ms: performance.now() - t0, count: out.length, at: Date.now() });
      if (repeated) setError(`Повторяющиеся id: ${repeated}. В таблице у повторов суффикс ~2, ~3; в output.json id исходные.`);
      const low = out.filter((r) => r.confidence < REVIEW_THRESHOLD).length;
      if (low) react("confused", `${count(low, ADDRESSES)} из ${out.length} ниже порога 0,70.`, { label: "Открыть очередь", run: showQueue }, 9000);
      else react("happy", `${count(out.length, RECORDS)} готовы, проверка не нужна.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      react("error", "Разбор не выполнен: API вернул ошибку или недоступен.");
    } finally {
      setBusy(false);
      setProgress(null);
      refreshArchive();
    }
  }, [react, showQueue, refreshArchive]);

  const loadFile = useCallback(async (file: File) => {
    try {
      const input = parseInputFile(file.name, await file.text());
      if (!input.length) throw new Error("в файле нет адресов");
      await load(input);
    } catch (e) {
      setError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
      react("error", "Файл не прочитан: нужен JSON, JSON Lines, CSV с колонкой raw_address или текст по адресу в строке.");
    }
  }, [load, react]);

  const loadText = useCallback((text: string) => {
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    load(lines.map((l, i) => ({ id: `line_${i + 1}`, raw_address: l })));
  }, [load]);

  const parseOne = useCallback(async (raw: string) => {
    try {
      const [rec] = await parseBatch([{ id: `quick_${Date.now()}`, raw_address: raw }], true);
      refreshArchive();
      return rec ?? null;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      react("sleeping", "Строка не разобрана: нет связи с сервером.");
      return null;
    }
  }, [react, refreshArchive]);

  const addRow = useCallback((rec: RecordOut) => setRows((rs) => [rec, ...rs.filter((x) => x.id !== rec.id)]), []);

  const update = useCallback((id: string, field: Field, value: string | null) =>
    setRows((rs) => rs.map((r) => {
      if (r.id !== id) return r;
      const original = r.original ?? (Object.fromEntries(FIELDS.map((f) => [f, r[f]])) as Record<Field, string | null>);
      const next = { ...r, [field]: value === "" ? null : value, original, reviewed: false };
      return { ...next, edited: FIELDS.some((f) => next[f] !== original[f]) };
    })), []);

  const revert = useCallback((id: string) =>
    setRows((rs) => rs.map((r) => (r.id === id && r.original ? { ...r, ...r.original, edited: false, original: undefined } : r))), []);

  const accept = useCallback(async (r: RecordOut) => {
    const fields = Object.fromEntries(FIELDS.map((f) => [f, r[f]])) as Record<string, string | null>;
    try {
      if (r.edited) await sendCorrection(r, fields);
      setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, reviewed: true } : x)));
      const left = rowsRef.current.filter((x) => x.id !== r.id && x.confidence < REVIEW_THRESHOLD && !x.reviewed).length;
      if (left === 0 && r.confidence < REVIEW_THRESHOLD) react("happy", "Очередь проверки пуста.");
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      react("error", `${r.id}: исправление не отправлено.`);
      return false;
    }
  }, [react]);

  /** Accept several records at once; edited ones still send their correction first. Returns how many were accepted. */
  const acceptMany = useCallback(async (ids: string[]) => {
    const want = new Set(ids);
    const targets = rowsRef.current.filter((r) => want.has(r.id) && !r.reviewed);
    const done = new Set<string>();
    for (const r of targets) {
      try {
        if (r.edited) await sendCorrection(r, Object.fromEntries(FIELDS.map((f) => [f, r[f]])) as Record<string, string | null>);
        done.add(r.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
    setRows((rs) => rs.map((x) => (done.has(x.id) ? { ...x, reviewed: true } : x)));
    if (done.size) react("happy", `Принято: ${count(done.size, RECORDS)}.`, undefined, 3500);
    return done.size;
  }, [react]);

  const exportJson = useCallback((ids?: string[]) => {
    const pick = ids ? new Set(ids) : null;
    const out = (pick ? rows.filter((r) => pick.has(r.id)) : rows)
      .map((r) => Object.fromEntries(JURY_KEYS.map((k) => [k, k === "id" ? r.orig_id ?? r.id : r[k as keyof RecordOut] ?? null])));
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "output.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    react("happy", `output.json: ${count(out.length, RECORDS)}.`);
  }, [rows, react]);

  const clear = useCallback(() => { setRows([]); setLastRun(null); setError(null); }, []);

  const value = useMemo<SessionCtx>(() => ({
    ready, rows, stats, busy, progress, error, setError, lastRun,
    load, loadFile, loadText, parseOne, addRow, update, revert, accept, acceptMany, exportJson, clear, showQueue,
  }), [ready, rows, stats, busy, progress, error, lastRun, load, loadFile, loadText, parseOne, addRow, update, revert, accept, acceptMany, exportJson, clear, showQueue]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSession outside <SessionProvider>");
  return ctx;
}
