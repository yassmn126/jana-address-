import type { Conflict, ConflictCounts, ConflictList, ConflictStatus, Decision, RecordOut, Run, RunStats, ScanResult, VersionInfo } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

/** JSON or a readable error carrying the API's `detail` (e.g. «no Claude credentials», «tooling not in this image»). */
async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = await r.text();
    try {
      const parsed = JSON.parse(detail);
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail ?? parsed);
    } catch { /* plain-text error */ }
    throw new Error(`API ${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

// ---------------------------------------------------------------- conflict queue (local parser vs AI labeler)
export const listConflicts = (status: ConflictStatus | "all" = "pending", limit = 200): Promise<ConflictList> =>
  fetch(`${BASE}/api/v1/conflicts?status=${status}&limit=${limit}`).then((r) => json<ConflictList>(r));

export const scanConflicts = (records: { id: string; raw_address: string }[], provider: string, model: string): Promise<ScanResult> =>
  fetch(`${BASE}/api/v1/conflicts/scan?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(records),
  }).then((r) => json<ScanResult>(r));

export const resolveConflict = (id: string, decision: Decision): Promise<{ item: Conflict; correction_path: string | null; counts: ConflictCounts }> =>
  fetch(`${BASE}/api/v1/conflicts/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  }).then((r) => json(r));

export async function parseBatch(records: { id: string; raw_address: string }[], explain = true): Promise<RecordOut[]> {
  const r = await fetch(`${BASE}/api/v1/parse/batch?explain=${explain ? 1 : 0}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(records),
  });
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function sendCorrection(rec: RecordOut, fields: Record<string, string | null>, reviewer?: string) {
  const r = await fetch(`${BASE}/api/v1/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: rec.id, raw_address: rec.raw_address, fields, reviewer }),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
}

export async function health(): Promise<{ status: string }> {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

export const getVersion = (): Promise<VersionInfo> => fetch(`${BASE}/version`).then((r) => json<VersionInfo>(r));

// ---------------------------------------------------------------- run archive (every batch the API parsed, kept in SQLite)
export const listRuns = (limit = 8): Promise<Run[]> =>
  fetch(`${BASE}/api/v1/runs?limit=${limit}`).then((r) => json<Run[]>(r));

export const getRunStats = (): Promise<RunStats> => fetch(`${BASE}/api/v1/runs/stats`).then((r) => json<RunStats>(r));
