import type { Conflict, ConflictCounts, ConflictList, ConflictStatus, Decision, FieldValues, RecordOut, Run, RunStats, ScanResult, VersionInfo } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

const STATUS_RU: Record<number, string> = {
  400: "неверный запрос",
  404: "не найдено",
  413: "слишком много записей за один запрос",
  422: "неверный формат данных",
  500: "ошибка сервера",
  502: "внешний сервис не ответил",
  503: "сервис недоступен",
};

/** Every call goes through here: network failures and API errors become one readable Russian message with the API's detail. */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new Error(`Нет связи с API${BASE ? ` (${BASE})` : ""}`);
  }
  if (!r.ok) {
    let detail = await r.text();
    try {
      const parsed = JSON.parse(detail);
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail ?? parsed);
    } catch { /* plain-text or HTML error */ }
    if (detail.trimStart().startsWith("<")) detail = "";
    throw Object.assign(new Error(`API ${r.status}: ${STATUS_RU[r.status] ?? "ошибка"}${detail ? ` · ${detail.slice(0, 300)}` : ""}`), { status: r.status });
  }
  return r.json() as Promise<T>;
}

const postJson = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// ---------------------------------------------------------------- parsing
export const parseBatch = (records: { id: string; raw_address: string }[], explain = true): Promise<RecordOut[]> =>
  call<RecordOut[]>(`/api/v1/parse/batch?explain=${explain ? 1 : 0}`, postJson(records));

export const sendCorrection = (rec: RecordOut, fields: Record<string, string | null>, reviewer?: string) =>
  call<{ ok?: boolean }>("/api/v1/corrections", postJson({ id: rec.orig_id ?? rec.id, raw_address: rec.raw_address, fields, reviewer }));

// ---------------------------------------------------------------- service
export const health = (): Promise<{ status: string }> => call("/health");
export const getVersion = (): Promise<VersionInfo> => call("/version");

// ---------------------------------------------------------------- run archive (every batch the API parsed, kept in SQLite)
export const listRuns = (limit = 8): Promise<Run[]> => call(`/api/v1/runs?limit=${limit}`);
export const getRunStats = (): Promise<RunStats> => call("/api/v1/runs/stats");

// ---------------------------------------------------------------- AI second opinion and the conflict queue
export interface VerifyRow {
  id: string;
  raw_address: string;
  ours: Partial<FieldValues>;
  llm: Partial<FieldValues>;
  disagreements: string[];
  issues: string[];
}

/** Side-effect free cross-check of ≤ 50 records against the external labeler. */
export const verifyRecords = (records: { id: string; raw_address: string }[], provider: string, model: string) =>
  call<{ summary: unknown; records: VerifyRow[] }>(`/api/v1/verify?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`, postJson(records));

/** Is the AI tooling in this API build? An empty batch costs no LLM call: 200 = yes, 503 = the jury image without it. */
export async function aiAvailable(): Promise<boolean> {
  try {
    await verifyRecords([], "rules", "rules");
    return true;
  } catch {
    return false;
  }
}

export const listConflicts = (status: ConflictStatus | "all" = "pending", limit = 200): Promise<ConflictList> =>
  call(`/api/v1/conflicts?status=${status}&limit=${limit}`);

export const scanConflicts = (records: { id: string; raw_address: string }[], provider: string, model: string): Promise<ScanResult> =>
  call(`/api/v1/conflicts/scan?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`, postJson(records));

export const resolveConflict = (id: string, decision: Decision): Promise<{ item: Conflict; correction_path: string | null; counts: ConflictCounts }> =>
  call(`/api/v1/conflicts/${encodeURIComponent(id)}/resolve`, postJson(decision));
