export const FIELDS = [
  "province", "city", "district", "living_area", "street_type", "street_name",
  "house", "apartment", "entrance", "floor", "landmark",
] as const;
export type Field = (typeof FIELDS)[number];

export const FIELD_LABELS: Record<Field, string> = {
  province: "Область", city: "Город", district: "Район", living_area: "Жилой массив",
  street_type: "Тип улицы", street_name: "Улица", house: "Дом", apartment: "Кв",
  entrance: "Подъезд", floor: "Этаж", landmark: "Ориентир",
};

export interface Span { field: string; start: number; end: number; text: string; rule: string; score: number }
export interface Token { text: string; start: number; end: number; kind: string; label: string | null; role: string | null }

export interface RecordOut {
  id: string;
  raw_address: string;
  province: string | null; city: string | null; district: string | null; living_area: string | null;
  street_type: string | null; street_name: string | null; house: string | null; apartment: string | null;
  entrance: string | null; floor: string | null; landmark: string | null;
  confidence: number;
  // explain mode
  spans?: Span[]; tokens?: Token[]; flags?: string[]; provenance?: Record<string, string | null>;
  // UI state
  edited?: boolean; reviewed?: boolean;
}

export const JURY_KEYS = ["id", "raw_address", ...FIELDS, "confidence"] as const;

/* ---------- Human-in-the-loop conflict queue (GET/POST /api/v1/conflicts*) ---------- */
export type ConflictReason = "disagreement" | "low_confidence" | "ai_issue" | "ai_error";
export type ConflictStatus = "pending" | "resolved" | "dismissed";
export type Side = "ours" | "ai";
export type FieldValues = Record<Field, string | null>;

export interface Conflict {
  conflict_id: string;
  record_id: string;
  raw_address: string;
  ours: FieldValues;            // local parser (string-derived fields)
  ai: FieldValues;              // external AI labeler
  disagreements: Field[];
  issues: string[];
  reasons: ConflictReason[];
  confidence: number | null;    // our confidence
  provider: string | null;
  model: string | null;
  status: ConflictStatus;
  resolution: {
    choice: "ours" | "ai" | "custom" | "dismiss";
    picks: Partial<Record<Field, Side>>;
    fields: FieldValues | null;
    reviewer: string | null;
    note: string | null;
  } | null;
  created_at: number;
  updated_at: number;
  resolved_at: number | null;
}
export interface ConflictCounts { pending: number; resolved: number; dismissed: number }
export interface ConflictList { items: Conflict[]; total: number; counts: ConflictCounts }
export interface ScanResult {
  scanned: number; needs_review: number; queued: number; updated: number; kept_decided: number;
  conflict_ids: string[]; counts: ConflictCounts;
}
export interface Decision {
  choice: "ours" | "ai" | "custom" | "dismiss";
  picks?: Partial<Record<Field, Side>>;
  fields?: Partial<FieldValues>;
  reviewer?: string;
  note?: string;
}

/** Colors per field for span highlighting (CSS classes in styles.css). */
export const SPAN_CLASS: Record<string, string> = {
  PROVINCE: "sp-province", CITY: "sp-city", DISTRICT: "sp-district", LIVING_AREA: "sp-living",
  STREET_TYPE: "sp-stype", STREET_NAME: "sp-street", HOUSE: "sp-house", APARTMENT: "sp-apt",
  ENTRANCE: "sp-entrance", FLOOR: "sp-floor", LANDMARK: "sp-landmark",
};
