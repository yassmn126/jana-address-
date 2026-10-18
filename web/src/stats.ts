import { FIELDS, type Field, type RecordOut } from "./types";

export const REVIEW_THRESHOLD = 0.7;

export interface Stats {
  n: number;
  avg: number;
  low: number;          // confidence below the threshold
  queue: number;        // low and not yet accepted by an operator
  ready: number;
  reviewed: number;
  fill: Record<Field, number>;   // % of records where the field is filled
  bins: number[];       // ten 0.1-wide confidence bands
  cities: { label: string; n: number; kind?: "other" | "none" }[];
}

export function computeStats(rows: RecordOut[]): Stats {
  const n = rows.length;
  const pct = (k: number) => (n ? Math.round((100 * k) / n) : 0);
  const low = rows.filter((r) => r.confidence < REVIEW_THRESHOLD).length;
  const fill = Object.fromEntries(FIELDS.map((f) => [f, pct(rows.filter((r) => r[f]).length)])) as Record<Field, number>;

  const bins = Array(10).fill(0) as number[];
  for (const r of rows) bins[Math.min(9, Math.floor(r.confidence * 10 + 1e-9))]++;

  const byCity = new Map<string, number>();
  let noCity = 0;
  for (const r of rows) { if (r.city) byCity.set(r.city, (byCity.get(r.city) ?? 0) + 1); else noCity++; }
  const sorted = [...byCity.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const cities: Stats["cities"] = sorted.slice(0, 5).map(([label, k]) => ({ label, n: k }));
  const other = sorted.slice(5).reduce((s, [, k]) => s + k, 0);
  if (other) cities.push({ label: "другие", n: other, kind: "other" });
  if (noCity) cities.push({ label: "без города", n: noCity, kind: "none" });

  return {
    n,
    avg: n ? rows.reduce((s, r) => s + r.confidence, 0) / n : 0,
    low,
    queue: rows.filter((r) => r.confidence < REVIEW_THRESHOLD && !r.reviewed).length,
    ready: n - low,
    reviewed: rows.filter((r) => r.reviewed).length,
    fill,
    bins,
    cities,
  };
}
