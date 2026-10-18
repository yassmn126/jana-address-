/** Russian number, date and plural formatting shared by every page. */

export const fmtInt = (n: number) => n.toLocaleString("ru-RU");

/** Confidence and other 0..1 scores: two decimals, Russian comma (0,93). */
export const fmtScore = (v: number) => v.toFixed(2).replace(".", ",");

export const fmtPct = (part: number, whole: number) => (whole ? `${Math.round((100 * part) / whole)}%` : "0%");

export const fmtMs = (ms: number) => (ms >= 100 ? Math.round(ms).toString() : ms.toFixed(1).replace(".", ","));

/** plural(5, ["адрес", "адреса", "адресов"]) → "адресов" */
export function plural(n: number, forms: [string, string, string]) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return forms[0];
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return forms[1];
  return forms[2];
}

export const ADDRESSES: [string, string, string] = ["адрес", "адреса", "адресов"];
export const RECORDS: [string, string, string] = ["запись", "записи", "записей"];
export const BATCHES: [string, string, string] = ["пакет", "пакета", "пакетов"];

export const count = (n: number, forms: [string, string, string]) => `${fmtInt(n)} ${plural(n, forms)}`;

/** "сегодня, 10:26" · "17 окт, 22:05" for the archive's local timestamps. */
export function fmtWhen(iso: string | number | Date) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return `сегодня, ${time}`;
  return `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "")}, ${time}`;
}

/** Archive `source`: "batch" · "single" · "file:<name>". */
export const fmtSource = (s: string) =>
  s.startsWith("file:") ? s.slice(5) || "файл" : s === "single" ? "Одна строка" : "Пакет JSON";
