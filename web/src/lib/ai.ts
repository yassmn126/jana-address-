/** The external labelers the API can compare with (eval/llm_verify.py · training/label_llm.py PROVIDERS).
 *  Claude needs ANTHROPIC_API_KEY (and ANTHROPIC_WORKSPACE_ID for a user-scoped key) on the API server — set in the
 *  Render dashboard or a local .env; the offline jury image answers 503 and the UI falls back to «Только правила». */
export const PROVIDERS = [
  { key: "fable", id: "anthropic", label: "Claude Fable 5.1 (самая сильная)", model: "claude-fable-5-1" },
  { key: "opus", id: "anthropic", label: "Claude Opus 5.5", model: "claude-opus-5-5" },
  { key: "haiku", id: "anthropic", label: "Claude Haiku 5.5 (быстрая, дешёвая)", model: "claude-haiku-5-5" },
  { key: "local", id: "openai-compatible", label: "Локальный LLM", model: "" },
  { key: "rules", id: "rules", label: "Только правила", model: "rules" },
] as const;

/** The list entry for a provider + model pair (the model decides between the Claude entries). */
export const providerEntry = (provider: string, model: string) =>
  PROVIDERS.find((p) => p.id === provider && (p.id !== "anthropic" || p.model === model))
  ?? PROVIDERS.find((p) => p.id === provider) ?? PROVIDERS[0];

const PROVIDER_KEY = "jana-address/conflicts/provider";
const MODEL_KEY = "jana-address/conflicts/model";

export function savedProvider(): { provider: string; model: string } {
  try {
    const p = localStorage.getItem(PROVIDER_KEY);
    const known = PROVIDERS.find((x) => x.id === p) ?? PROVIDERS[0];
    const m = localStorage.getItem(MODEL_KEY);
    return { provider: known.id, model: m && (known.id !== "anthropic" || m.startsWith("claude-")) ? m : known.model };
  } catch {
    return { provider: PROVIDERS[0].id, model: PROVIDERS[0].model };
  }
}

export function saveProvider(provider: string, model: string) {
  try {
    localStorage.setItem(PROVIDER_KEY, provider);
    localStorage.setItem(MODEL_KEY, model);
  } catch { /* storage blocked */ }
}

export const providerLabel = (id: string, model = "") => providerEntry(id, model).label;

/* ---------------------------------------------------------------- bring your own Claude key
 * Kept in this browser's localStorage only; sent to the API in request headers (never a URL) when the provider is
 * Claude. The API uses it for that request and neither stores nor logs it (api/jana_address/main.py). */
const KEY_KEY = "jana-address/anthropic/key";
const WS_KEY = "jana-address/anthropic/workspace";

export function savedKey(): { apiKey: string; workspaceId: string } {
  try {
    return { apiKey: localStorage.getItem(KEY_KEY) ?? "", workspaceId: localStorage.getItem(WS_KEY) ?? "" };
  } catch {
    return { apiKey: "", workspaceId: "" };
  }
}

export function saveKey(apiKey: string, workspaceId: string) {
  try {
    const k = apiKey.trim();
    const w = workspaceId.trim();
    if (k) localStorage.setItem(KEY_KEY, k); else localStorage.removeItem(KEY_KEY);
    if (w) localStorage.setItem(WS_KEY, w); else localStorage.removeItem(WS_KEY);
  } catch { /* storage blocked */ }
}

/** Headers for a call that may reach Claude: the browser key when the provider is Claude and a key is saved. */
export function aiHeaders(provider = "anthropic"): Record<string, string> {
  if (provider !== "anthropic") return {};
  const { apiKey, workspaceId } = savedKey();
  if (!apiKey) return {};
  return { "X-Anthropic-Api-Key": apiKey, ...(workspaceId ? { "X-Anthropic-Workspace-Id": workspaceId } : {}) };
}
