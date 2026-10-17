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

/** Colors per field for span highlighting (CSS classes in styles.css). */
export const SPAN_CLASS: Record<string, string> = {
  PROVINCE: "sp-province", CITY: "sp-city", DISTRICT: "sp-district", LIVING_AREA: "sp-living",
  STREET_TYPE: "sp-stype", STREET_NAME: "sp-street", HOUSE: "sp-house", APARTMENT: "sp-apt",
  ENTRANCE: "sp-entrance", FLOOR: "sp-floor", LANDMARK: "sp-landmark",
};
