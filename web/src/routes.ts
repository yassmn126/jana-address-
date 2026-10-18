/** Every page has its own path; the old single-page build used #hash links, kept working by LEGACY_HASH. */
export const PATHS = {
  dashboard: "/dashboard",
  records: "/records",
  record: (id: string) => `/records/${encodeURIComponent(id)}`,
  queue: "/records?view=queue",
  conflicts: "/conflicts",
  parse: "/parse",
} as const;

export const LEGACY_HASH: Record<string, string> = {
  dashboard: PATHS.dashboard,
  batch: PATHS.records,
  conflicts: PATHS.conflicts,
  quick: PATHS.parse,
};
