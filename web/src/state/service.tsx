import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { aiAvailable, getRunStats, getVersion, health, listConflicts, listRuns } from "../api";
import type { Run, RunStats, VersionInfo } from "../types";
import { useAssistant } from "./assistant";

/** Everything the UI knows about the backend: health, model build, conflict queue size and the run archive. */
interface ServiceCtx {
  apiOk: boolean | null;          // null until the first /health answer
  version: VersionInfo | null;
  ai: boolean | null;             // does this API build carry the AI cross-check tooling (/verify, /conflicts/scan)
  conflictsPending: number;
  setConflictsPending: (n: number) => void;
  runs: Run[] | null;             // null: not loaded or archive unavailable
  runStats: RunStats | null;
  refreshArchive: () => void;
}

const Ctx = createContext<ServiceCtx | null>(null);

const HEALTH_MS = 15000;
const CONFLICTS_MS = 20000;

export function ServiceProvider({ children }: { children: ReactNode }) {
  const { react } = useAssistant();
  const [apiOk, setApiOk] = useState<boolean | null>(null);
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [ai, setAi] = useState<boolean | null>(null);
  const [conflictsPending, setConflictsPending] = useState(0);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [runStats, setRunStats] = useState<RunStats | null>(null);
  const prevOk = useRef<boolean | null>(null);

  // health, re-checked so the UI wakes up with the backend
  useEffect(() => {
    let alive = true;
    const check = () => health().then((h) => alive && setApiOk(h.status === "ok")).catch(() => alive && setApiOk(false));
    check();
    const t = window.setInterval(check, HEALTH_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  useEffect(() => {
    if (prevOk.current === false && apiOk) react("happy", "API снова на связи.");
    prevOk.current = apiOk;
  }, [apiOk, react]);

  // model build and AI tooling: once per reconnect
  useEffect(() => {
    if (!apiOk) return;
    getVersion().then(setVersion).catch(() => setVersion(null));
    aiAvailable().then(setAi);
  }, [apiOk]);

  // conflict queue size for the sidebar badge and the dashboard
  useEffect(() => {
    if (!apiOk) return;
    let alive = true;
    const pull = () => listConflicts("pending", 1).then((r) => alive && setConflictsPending(r.counts.pending)).catch(() => { /* API without the queue */ });
    pull();
    const t = window.setInterval(pull, CONFLICTS_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, [apiOk]);

  const refreshArchive = useCallback(() => {
    Promise.all([listRuns(8), getRunStats()])
      .then(([r, s]) => {
        setRuns(s.disabled ? null : r);
        setRunStats(s.disabled ? null : s);
      })
      .catch(() => { setRuns(null); setRunStats(null); });
  }, []);

  useEffect(() => { if (apiOk) refreshArchive(); }, [apiOk, refreshArchive]);

  const value = useMemo(
    () => ({ apiOk, version, ai, conflictsPending, setConflictsPending, runs, runStats, refreshArchive }),
    [apiOk, version, ai, conflictsPending, runs, runStats, refreshArchive],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useService() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useService outside <ServiceProvider>");
  return ctx;
}
