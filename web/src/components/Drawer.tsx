import { useEffect, useRef, type ReactNode } from "react";
import { IconChevronDown, IconChevronUp, IconClose } from "./ui";

/**
 * Right-hand inspector. Non-modal on wide screens (the table stays clickable next to it),
 * a full-height sheet with a scrim below 1100 px. Esc closes; ↑/↓ and J/K step through the list.
 */
export function Drawer({ open, label, position, onClose, onPrev, onNext, header, children, footer }: {
  open: boolean;
  label: string;                 // accessible name, e.g. «Запись ht_084»
  position?: string;             // «12 из 416»
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  header: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (ev: KeyboardEvent) => {
      const el = ev.target as HTMLElement;
      const typing = el?.tagName === "INPUT" || el?.tagName === "TEXTAREA" || el?.tagName === "SELECT" || el?.isContentEditable;
      if (ev.key === "Escape") {
        if (typing) { el.blur(); return; }
        onClose();
        return;
      }
      if (typing || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if ((ev.key === "j" || ev.key === "ArrowDown") && onNext) { ev.preventDefault(); onNext(); }
      if ((ev.key === "k" || ev.key === "ArrowUp") && onPrev) { ev.preventDefault(); onPrev(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose, onNext, onPrev]);

  // new record → body back to the top
  useEffect(() => { ref.current?.querySelector(".drawer-body")?.scrollTo({ top: 0 }); }, [label]);

  if (!open) return null;
  return (
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside ref={ref} className="drawer" role="complementary" aria-label={label}>
        <header className="drawer-head">
          <div className="drawer-nav">
            {position && <span className="drawer-pos">{position}</span>}
            <button type="button" className="icon-btn" onClick={onPrev} disabled={!onPrev} aria-label="Предыдущая" title="Предыдущая · K"><IconChevronUp /></button>
            <button type="button" className="icon-btn" onClick={onNext} disabled={!onNext} aria-label="Следующая" title="Следующая · J"><IconChevronDown /></button>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Закрыть" title="Закрыть · Esc"><IconClose /></button>
          </div>
          {header}
        </header>
        <div className="drawer-body">{children}</div>
        {footer && <footer className="drawer-foot">{footer}</footer>}
      </aside>
    </>
  );
}
