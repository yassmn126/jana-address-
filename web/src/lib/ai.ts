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
