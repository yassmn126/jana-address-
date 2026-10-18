/** The external labelers the API can compare with (eval/llm_verify.py · training/label_llm.py PROVIDERS). */
export const PROVIDERS = [
  { id: "openai-compatible", label: "Локальный LLM", model: "" },
  { id: "rules", label: "Только правила", model: "rules" },
] as const;

const PROVIDER_KEY = "jana-address/conflicts/provider";
const MODEL_KEY = "jana-address/conflicts/model";

export function savedProvider(): { provider: string; model: string } {
  try {
    const p = localStorage.getItem(PROVIDER_KEY);
    const known = PROVIDERS.find((x) => x.id === p) ?? PROVIDERS[0];
    return { provider: known.id, model: localStorage.getItem(MODEL_KEY) ?? known.model };
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

export const providerLabel = (id: string) => PROVIDERS.find((p) => p.id === id)?.label ?? id;
