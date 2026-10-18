import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import type { MascotState, Mood } from "../components/Mascot";

/** Short-lived mascot reactions («12 из 300 нужно проверить»), shown as a toast by the app shell. */
interface AssistantCtx {
  reaction: MascotState | null;
  react: (mood: Mood, text: string, action?: MascotState["action"], ms?: number) => void;
  dismiss: () => void;
}

const Ctx = createContext<AssistantCtx | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [reaction, setReaction] = useState<MascotState | null>(null);
  const timer = useRef<number>();

  const dismiss = useCallback(() => {
    window.clearTimeout(timer.current);
    setReaction(null);
  }, []);

  const react = useCallback((mood: Mood, text: string, action?: MascotState["action"], ms = 6500) => {
    window.clearTimeout(timer.current);
    setReaction({ mood, text, action });
    timer.current = window.setTimeout(() => setReaction(null), ms);
  }, []);

  const value = useMemo(() => ({ reaction, react, dismiss }), [reaction, react, dismiss]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssistant() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAssistant outside <AssistantProvider>");
  return ctx;
}
