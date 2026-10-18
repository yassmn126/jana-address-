/**
 * Reads whatever address file an operator drops in: a JSON array, { records: [...] }, JSON Lines, CSV with an
 * address column, or plain text with one address per line. Mirrors the API's own leniency (main.py · _to_records).
 */
export type Input = { id: string; raw_address: string };

const ADDRESS_KEYS = ["raw_address", "address", "raw", "адрес"];
const ID_KEYS = ["id", "record_id", "ID"];

function fromObject(r: unknown, i: number): Input {
  if (typeof r === "string") return { id: String(i), raw_address: r };
  if (r && typeof r === "object") {
    const o = r as Record<string, unknown>;
    const idKey = ID_KEYS.find((k) => o[k] != null);
    const addrKey = ADDRESS_KEYS.find((k) => typeof o[k] === "string");
    return { id: idKey ? String(o[idKey]) : String(i), raw_address: addrKey ? String(o[addrKey]) : "" };
  }
  return { id: String(i), raw_address: r == null ? "" : String(r) };
}

/** RFC 4180-ish: quoted fields, doubled quotes, comma or semicolon separators. */
function parseCsv(text: string): string[][] {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
  const sep = (firstLine.match(/;/g)?.length ?? 0) > (firstLine.match(/,/g)?.length ?? 0) ? ";" : ",";
  const rows: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === sep) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim()));
}

export function parseInputFile(name: string, raw: string): Input[] {
  const text = raw.replace(/^﻿/, "").trim();
  if (!text) throw new Error("Файл пустой");
  const ext = name.toLowerCase().split(".").pop() ?? "";

  if (text.startsWith("[") || text.startsWith("{")) {
    try {
      let data: unknown = JSON.parse(text);
      if (data && typeof data === "object" && !Array.isArray(data)) {
        const o = data as Record<string, unknown>;
        data = Array.isArray(o.records) ? o.records : Array.isArray(o.data) ? o.data : [data];
      }
      if (Array.isArray(data)) return data.map(fromObject);
    } catch {
      // not one JSON document: try JSON Lines below
    }
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    try {
      return lines.map((l, i) => fromObject(JSON.parse(l), i));
    } catch {
      throw new Error("Не JSON и не JSON Lines: проверьте скобки и запятые");
    }
  }

  if (ext === "csv" || ext === "tsv" || /^[^\n]*(raw_address|address|адрес)[^\n]*[,;]/i.test(text)) {
    const rows = parseCsv(text);
    const head = rows[0].map((c) => c.trim().toLowerCase());
    const addrCol = head.findIndex((c) => ADDRESS_KEYS.includes(c));
    const idCol = head.findIndex((c) => ID_KEYS.map((k) => k.toLowerCase()).includes(c));
    if (addrCol >= 0) {
      return rows.slice(1).map((r, i) => ({ id: idCol >= 0 && r[idCol]?.trim() ? r[idCol].trim() : String(i), raw_address: r[addrCol] ?? "" }));
    }
  }

  // plain text: one address per line
  return text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((l, i) => ({ id: `line_${i + 1}`, raw_address: l }));
}

/** Repeated ids get a UI-only suffix (ht_1 → ht_1~2) so edits and links hit one record; export restores the original. */
export function uniqueIds<T extends { id: string }>(rows: T[]): { rows: (T & { orig_id?: string })[]; repeated: number } {
  const seen = new Map<string, number>();
  let repeated = 0;
  const out = rows.map((r) => {
    const n = (seen.get(r.id) ?? 0) + 1;
    seen.set(r.id, n);
    if (n === 1) return r;
    repeated++;
    return { ...r, orig_id: r.id, id: `${r.id}~${n}` };
  });
  return { rows: out, repeated };
}
