import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { health, parseBatch, sendCorrection } from "./api";
import { FIELDS, JURY_KEYS, type Field, type RecordOut } from "./types";
import { computeStats, REVIEW_THRESHOLD } from "./stats";
import { Navbar, PAGES, type Page } from "./components/Navbar";
import { Mascot, type MascotState, type Mood } from "./components/Mascot";
import { Dashboard } from "./pages/Dashboard";
import { Batch, type SortKey } from "./pages/Batch";
import { QuickTest } from "./pages/QuickTest";

/** Hotkeys for click-to-relabel: select text inside the raw string, press a key. */
const HOTKEYS: Record<string, Field> = {
  s: "street_name", h: "house", a: "apartment", e: "entrance", f: "floor",
  d: "district", l: "living_area", m: "landmark", c: "city", p: "province",
};

const readPage = (): Page => {
  const h = window.location.hash.slice(1);
  return (PAGES as string[]).includes(h) ? (h as Page) : "dashboard";
};

/** 1 запись · 2 записи · 5 записей */
const records = (n: number) => {
  const m10 = n % 10, m100 = n % 100;
  const word = m10 === 1 && m100 !== 11 ? "запись" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "записи" : "записей";
  return `${n.toLocaleString("ru-RU")} ${word}`;
};

export default function App() {
  const [page, setPage] = useState<Page>(readPage);
  const [rows, setRows] = useState<RecordOut[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiOk, setApiOk] = useState<boolean | null>(null);
  const [sort, setSort] = useState<SortKey>("conf-asc");
  const [maxConf, setMaxConf] = useState(1);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [paste, setPaste] = useState("");
  const [lastRun, setLastRun] = useState<{ ms: number; count: number } | null>(null);
  const [reaction, setReaction] = useState<MascotState | null>(null);
  const selectionRef = useRef<{ id: string; text: string } | null>(null);
  const reactTimer = useRef<number>();
  const prevApiOk = useRef<boolean | null>(null);

  // ---------------------------------------------------------------- routing (#dashboard · #batch · #quick)
  useEffect(() => {
    const on = () => { setPage(readPage()); window.scrollTo({ top: 0 }); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const navigate = useCallback((p: Page) => {
    if (window.location.hash === `#${p}`) setPage(p);
    else window.location.hash = p;
  }, []);

  // ---------------------------------------------------------------- mascot reactions
  const react = useCallback((mood: Mood, text: string, action?: MascotState["action"], ms = 6500) => {
    window.clearTimeout(reactTimer.current);
    setReaction({ mood, text, action });
    reactTimer.current = window.setTimeout(() => setReaction(null), ms);
  }, []);

  const showQueue = useCallback(() => {
    setMaxConf(Math.round((REVIEW_THRESHOLD - 0.01) * 100) / 100);
    setSort("conf-asc");
    navigate("batch");
  }, [navigate]);
  const queueAction = useMemo(() => ({ label: "Показать очередь", run: showQueue }), [showQueue]);

  // ---------------------------------------------------------------- API health (re-checked so the mascot wakes up with the backend)
  useEffect(() => {
    let alive = true;
    const check = () => health().then((h) => alive && setApiOk(h.status === "ok")).catch(() => alive && setApiOk(false));
    check();
    const t = window.setInterval(check, 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  useEffect(() => {
    if (prevApiOk.current === false && apiOk) react("happy", "Проснулся! API снова на связи.");
    prevApiOk.current = apiOk;
  }, [apiOk, react]);

  // ---------------------------------------------------------------- loading
  const load = useCallback(async (input: { id: string; raw_address: string }[]) => {
    setBusy(true);
    setError(null);
    react("waiting", `Разбираю ${records(input.length)}…`, undefined, 600000);
    const t0 = performance.now();
    try {
      const out: RecordOut[] = [];
      for (let i = 0; i < input.length; i += 200) {
        out.push(...(await parseBatch(input.slice(i, i + 200), true)));
        setRows([...out]);
      }
      setLastRun({ ms: performance.now() - t0, count: out.length });
      const low = out.filter((r) => r.confidence < REVIEW_THRESHOLD).length;
      if (low) react("confused", `Хм… ${low} из ${out.length} нужно проверить вручную.`, queueAction, 9000);
      else react("happy", `Отлично! ${records(out.length)} — все готовы к доставке.`);
    } catch (e) {
      setError(String(e));
      react("confused", "Не получилось разобрать: API вернул ошибку или недоступен.");
    } finally {
      setBusy(false);
    }
  }, [react, queueAction]);

  const onFile = async (file: File) => {
    try {
      const text = await file.text();
      let data: unknown = JSON.parse(text.replace(/^﻿/, ""));
      if (data && typeof data === "object" && "records" in (data as Record<string, unknown>)) data = (data as { records: unknown }).records;
      if (!Array.isArray(data)) throw new Error("JSON must be an array of {id, raw_address}");
      load(data.map((r: Record<string, unknown>, i) => ({ id: String(r.id ?? i), raw_address: String(r.raw_address ?? "") })));
    } catch (e) {
      setError(String(e));
      react("confused", "Не могу прочитать файл. Нужен JSON-массив { id, raw_address }.");
    }
  };

  const onPaste = () => {
    const lines = paste.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length) load(lines.map((l, i) => ({ id: `paste_${i + 1}`, raw_address: l })));
  };

  const parseOne = async (raw: string): Promise<RecordOut | null> => {
    react("waiting", "Разбираю строку…", undefined, 60000);
    try {
      const [rec] = await parseBatch([{ id: `quick_${Date.now()}`, raw_address: raw }], true);
      if (!rec) return null;
      if (rec.confidence < REVIEW_THRESHOLD) react("confused", `Не уверен (${rec.confidence.toFixed(2)}) — лучше проверить вручную.`);
      else react("happy", `Уверенно разобрал: ${rec.confidence.toFixed(2)}.`);
      return rec;
    } catch (e) {
      setError(String(e));
      react("confused", "API недоступен — запустите бэкенд, чтобы разобрать строку.");
      return null;
    }
  };

  const addRow = (rec: RecordOut) => setRows((rs) => [rec, ...rs.filter((x) => x.id !== rec.id)]);

  // ---------------------------------------------------------------- editing
  const update = (id: string, field: Field, value: string | null) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, [field]: value === "" ? null : value, edited: true } : r)));

  const accept = async (r: RecordOut) => {
    const fields = Object.fromEntries(FIELDS.map((f) => [f, r[f]])) as Record<string, string | null>;
    try {
      if (r.edited) await sendCorrection(r, fields);
      setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, reviewed: true } : x)));
      const left = rows.filter((x) => x.id !== r.id && x.confidence < REVIEW_THRESHOLD && !x.reviewed).length;
      if (left === 0) react("happy", "Очередь пуста — всё проверено!");
      else react("happy", `Спасибо! ${r.id} принята, в очереди ещё ${left}.`, undefined, 3500);
    } catch (e) {
      setError(String(e));
    }
  };

  // ---------------------------------------------------------------- derived
  const visible = useMemo(() => {
    let v = rows.filter((r) => r.confidence <= maxConf);
    if (query) {
      const q = query.toLowerCase();
      v = v.filter((r) => r.raw_address.toLowerCase().includes(q) || r.id.toLowerCase().includes(q));
    }
    if (sort === "conf-asc") v = [...v].sort((a, b) => a.confidence - b.confidence);
    if (sort === "conf-desc") v = [...v].sort((a, b) => b.confidence - a.confidence);
    return v;
  }, [rows, maxConf, query, sort]);

  const stats = useMemo(() => computeStats(rows), [rows]);

  // click-to-relabel: remember the selection inside a raw cell; a hotkey assigns it (batch page only)
  const onRawMouseUp = (id: string) => {
    const sel = window.getSelection()?.toString().trim();
    selectionRef.current = sel ? { id, text: sel } : null;
    setSelected(id);
  };
  useEffect(() => {
    if (page !== "batch") return;
    const h = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const field = HOTKEYS[ev.key.toLowerCase()];
      const s = selectionRef.current;
      if (field && s) {
        update(s.id, field, s.text);
        ev.preventDefault();
      } else if (ev.key === "Enter" && selected) {
        const r = rows.find((x) => x.id === selected);
        if (r) accept(r);
      } else if ((ev.key === "j" || ev.key === "k") && visible.length) {
        const idx = visible.findIndex((x) => x.id === selected);
        const next = visible[Math.min(visible.length - 1, Math.max(0, idx + (ev.key === "j" ? 1 : -1)))];
        if (next) setSelected(next.id);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  const exportJson = () => {
    const out = rows.map((r) => Object.fromEntries(JURY_KEYS.map((k) => [k, r[k as keyof RecordOut] ?? null])));
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "output.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    react("happy", `Готово: ${records(out.length)} в output.json.`);
  };

  // ---------------------------------------------------------------- mascot base state
  const base: MascotState =
    apiOk === null ? { mood: "waiting", text: "Проверяю, на связи ли бэкенд…" }
    : apiOk === false ? { mood: "sleeping", text: "API не отвечает. Запустите бэкенд (make api-dev) — я проснусь сам." }
    : !rows.length ? { mood: "idle", text: "Привет! Загрузите JSON или вставьте адреса — разберу каждую строку." }
    : stats.queue ? { mood: "idle", text: `В очереди на проверку: ${stats.queue}. Разберём?`, action: queueAction }
    : { mood: "idle", text: "Очередь пуста. Можно экспортировать JSON." };
  const mascot = reaction ?? base;

  // ---------------------------------------------------------------- render
  return (
    <>
      <Navbar page={page} queue={stats.queue} apiOk={apiOk} />
      <main className="shell">
        {page === "dashboard" && <Dashboard stats={stats} lastRun={lastRun} mascot={mascot} onShowQueue={showQueue} />}
        {page === "batch" && (
          <Batch
            rows={rows} visible={visible} busy={busy} error={error} mascot={mascot}
            sort={sort} setSort={setSort} maxConf={maxConf} setMaxConf={setMaxConf} query={query} setQuery={setQuery}
            paste={paste} setPaste={setPaste} selected={selected} setSelected={setSelected}
            onFile={onFile} onPaste={onPaste} onRawMouseUp={onRawMouseUp} update={update} accept={accept} exportJson={exportJson}
          />
        )}
        {page === "quick" && <QuickTest mascot={mascot} parseOne={parseOne} onAdd={addRow} />}
        <footer className="footer">
          <span>схема · {JURY_KEYS.join(" · ")}</span>
          <span>©2026 Jana Address · Jana Post</span>
        </footer>
      </main>
      {busy && (
        <div className="busy" role="status">
          <span style={{ width: 30 }}><Mascot mood="waiting" /></span>
          Разбор…
        </div>
      )}
    </>
  );
}
