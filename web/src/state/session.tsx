import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { parseBatch, sendCorrection } from "../api";
import { ADDRESSES, count, RECORDS } from "../format";
import { PATHS } from "../routes";
import { computeStats, REVIEW_THRESHOLD, type Stats } from "../stats";
import { FIELDS, JURY_KEYS, type Field, type RecordOut } from "../types";
import { useAssistant } from "./assistant";
import { useService } from "./service";

type Input = { id: string; raw_address: string };

/** The operator's working set: the parsed records of this browser tab, their edits and the review progress. */
interface SessionCtx {
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
  exportJson: () => void;
  clear: () => void;
  showQueue: () => void;
}

const Ctx = createContext<SessionCtx | null>(null);

/** Records are kept in sessionStorage so a reload on /records does not lose the file. Best effort: quota or privacy mode just skip it. */
const STORE_KEY = "jana-address/session/v1";
const CHUNK = 1000;

function restore(): { rows: RecordOut[]; lastRun: SessionCtx["lastRun"] } {
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    if (raw) {
      const s = JSON.parse(raw);
      if (Array.isArray(s.rows)) return { rows: s.rows, lastRun: s.lastRun ?? null };
    }
  } catch { /* storage blocked or corrupt */ }
  return { rows: [], lastRun: null };
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const { react } = useAssistant();
  const { refreshArchive } = useService();
  const initial = useRef(restore());
  const [rows, setRows] = useState<RecordOut[]>(initial.current.rows);
  const [lastRun, setLastRun] = useState<SessionCtx["lastRun"]>(initial.current.lastRun);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<SessionCtx["progress"]>(null);
  const [error, setError] = useState<string | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  useEffect(() => {
    const t = window.setTimeout(() => {
      try {
        if (rows.length) sessionStorage.setItem(STORE_KEY, JSON.stringify({ rows, lastRun }));
        else sessionStorage.removeItem(STORE_KEY);
      } catch { /* over quota: the session simply is not restored after a reload */ }
    }, 400);
    return () => window.clearTimeout(t);
  }, [rows, lastRun]);

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
      for (let i = 0; i < input.length; i += CHUNK) {
        out.push(...(await parseBatch(input.slice(i, i + CHUNK), true)));
        setRows([...out]);
        setProgress({ done: out.length, total: input.length });
      }
      setLastRun({ ms: performance.now() - t0, count: out.length, at: Date.now() });
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
      const text = await file.text();
      let data: unknown = JSON.parse(text.replace(/^﻿/, ""));
      if (data && typeof data === "object" && "records" in (data as Record<string, unknown>)) data = (data as { records: unknown }).records;
      if (!Array.isArray(data)) throw new Error("Нужен JSON-массив { id, raw_address } или { records: [...] }");
      await load(data.map((r: Record<string, unknown> | string, i) =>
        typeof r === "string"
          ? { id: String(i), raw_address: r }
          : { id: String(r.id ?? i), raw_address: String(r.raw_address ?? "") }));
    } catch (e) {
      setError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
      react("error", "Файл не прочитан. Нужен JSON-массив { id, raw_address }.");
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
      react("sleeping", "API недоступен: строка не разобрана.");
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

  const exportJson = useCallback(() => {
    const out = rows.map((r) => Object.fromEntries(JURY_KEYS.map((k) => [k, r[k as keyof RecordOut] ?? null])));
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
    rows, stats, busy, progress, error, setError, lastRun,
    load, loadFile, loadText, parseOne, addRow, update, revert, accept, exportJson, clear, showQueue,
  }), [rows, stats, busy, progress, error, lastRun, load, loadFile, loadText, parseOne, addRow, update, revert, accept, exportJson, clear, showQueue]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSession outside <SessionProvider>");
  return ctx;
}
