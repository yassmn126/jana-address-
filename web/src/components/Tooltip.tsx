import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

/**
 * One tooltip layer for every element with `data-tip`. It is fixed-positioned in <body>, so no stacking context
 * (the sticky sidebar, the animated page) can paint over it. Shows to the right of the element when there is room,
 * otherwise below it.
 */
export function TooltipLayer() {
  const [tip, setTip] = useState<{ text: string; x: number; y: number; side: "right" | "below" } | null>(null);

  useEffect(() => {
    let current: Element | null = null;
    const show = (el: Element) => {
      const text = el.getAttribute("data-tip");
      if (!text) return;
      current = el;
      const r = el.getBoundingClientRect();
      const roomRight = window.innerWidth - r.right > 220;
      setTip(roomRight
        ? { text, x: r.right + 10, y: r.top + r.height / 2, side: "right" }
        : { text, x: r.left + r.width / 2, y: r.bottom + 8, side: "below" });
    };
    const hide = () => { current = null; setTip(null); };
    const over = (ev: Event) => {
      const el = (ev.target as Element | null)?.closest?.("[data-tip]");
      if (el && el !== current) show(el);
      else if (!el && current) hide();
    };
    const focus = (ev: FocusEvent) => {
      const el = (ev.target as Element | null)?.closest?.("[data-tip]");
      if (el && (ev.target as HTMLElement).matches(":focus-visible")) show(el);
    };
    document.addEventListener("pointerover", over);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    document.addEventListener("pointerdown", hide);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      document.removeEventListener("pointerover", over);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("pointerdown", hide);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, []);

  if (!tip) return null;
  return createPortal(
    <div className={`tip tip-${tip.side}`} role="tooltip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>,
    document.body,
  );
}
