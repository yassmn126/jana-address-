export const FIELDS = [
  "province", "city", "district", "living_area", "street_type", "street_name",
  "house", "apartment", "entrance", "floor", "landmark",
] as const;
export type Field = (typeof FIELDS)[number];

export const FIELD_LABELS: Record<Field, string> = {
  province: "Область", city: "Город", district: "Район", living_area: "Жилой массив",
  street_type: "Тип улицы", street_name: "Улица", house: "Дом", apartment: "Квартира",
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
  field_confidence?: Partial<Record<Field, number>>;
  model_probs?: Record<string, number>;
  geo?: Geo | null;
  // UI state
  edited?: boolean; reviewed?: boolean;
  original?: Record<Field, string | null>;   // field values before the first manual edit
  orig_id?: string;                         // the file's id when it repeated and the UI added a ~N suffix
}

/** Geocoder answer attached in explain mode (api/jana_address/gazetteer/osm.py · geocode). */
export interface Geo {
  lat: number;
  lon: number;
  precision: string;
  source?: string;
  matched?: string;
  in_database?: boolean;
}

/** Where a field value came from (ExplainOut.provenance). */
export function provenanceLabel(p: string | null | undefined): string | null {
  if (!p) return null;
  if (p.startsWith("gazetteer")) return p.endsWith(":house") ? "OSM · дом" : "OSM";
  if (p === "string+table") return "строка · словарь";
  if (p === "string") return "строка";
  return p;
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

/* ---------- Run archive (GET /api/v1/runs*) and service info (GET /version) ---------- */
/** Geocoder precision, most to least exact (api/jana_address/gazetteer/osm.py · geocode). */
export const PRECISIONS = ["house", "house_near", "street", "microdistrict", "district", "locality", "none"] as const;
export type Precision = (typeof PRECISIONS)[number];
export const PRECISION_LABELS: Record<Precision, string> = {
  house: "Дом", house_near: "Дом без корпуса", street: "Улица", microdistrict: "Микрорайон",
  district: "Район", locality: "Нас. пункт", none: "Не найден",
};

export interface Run {
  id: number;
  time: string;                 // server local time, "YYYY-MM-DDTHH:MM:SS"
  source: string;               // "batch" · "single" · "file:<name>"
  records: number;
  elapsed_ms: number | null;
  mean_confidence: number | null;
  in_database: number;          // records geocoded to a building
  in_database_share: number | null;
  by_precision: Partial<Record<Precision, number>>;
  version: string;
}
export interface RunStats {
  runs: number;
  records: number;
  in_database?: number;
  in_database_share?: number | null;
  by_precision?: Partial<Record<Precision, number>>;
  mean_confidence?: number | null;
  disabled?: boolean;
}
export interface VersionInfo {
  version: string;
  components: {
    rules?: string; model?: string; mode?: string; gazetteer?: string;
    gazetteer_stats?: { settlements?: number; streets?: number; houses?: number; house_points?: number };
  };
}

/** Colors per field for span highlighting (CSS classes in styles.css). */
export const SPAN_CLASS: Record<string, string> = {
  PROVINCE: "sp-province", CITY: "sp-city", DISTRICT: "sp-district", LIVING_AREA: "sp-living",
  STREET_TYPE: "sp-stype", STREET_NAME: "sp-street", HOUSE: "sp-house", APARTMENT: "sp-apt",
  ENTRANCE: "sp-entrance", FLOOR: "sp-floor", LANDMARK: "sp-landmark",
};
