import type { RecordOut } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

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
