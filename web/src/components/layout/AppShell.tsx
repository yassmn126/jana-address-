import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { fmtInt } from "../../format";
import { useAssistant } from "../../state/assistant";
import { useSession } from "../../state/session";
import { Mascot, MOOD_LABEL } from "../Mascot";
import { IconClose } from "../ui";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  const { pathname } = useLocation();
  useEffect(() => { window.scrollTo({ top: 0 }); }, [pathname]);

  return (
    <div className="app">
      <Sidebar />
      <main className="main" key={pathname.split("/")[1]}>
        <Outlet />
      </main>
      <AssistantToast />
    </div>
  );
}

/** Bottom-right toast: parsing progress while busy, otherwise the latest mascot reaction. */
function AssistantToast() {
  const { reaction, dismiss } = useAssistant();
  const { busy, progress } = useSession();

  if (busy && progress) {
    return (
      <div className="toast" role="status" aria-live="polite">
        <Mascot mood="waiting" className="toast-mascot" />
        <div className="toast-body">
          <p className="eyebrow">{MOOD_LABEL.waiting}</p>
          <p>Разбор: {fmtInt(progress.done)} из {fmtInt(progress.total)}</p>
          <span className="toast-progress"><span style={{ width: `${(100 * progress.done) / Math.max(1, progress.total)}%` }} /></span>
        </div>
      </div>
    );
  }
  if (!reaction) return null;
  return (
    <div className="toast pop" role="status" aria-live="polite" key={reaction.text} data-mood={reaction.mood}>
      <Mascot mood={reaction.mood} className="toast-mascot" />
      <div className="toast-body">
        <p className="eyebrow">{MOOD_LABEL[reaction.mood]}</p>
        <p>{reaction.text}</p>
        {reaction.action && (
          <button type="button" className="link-btn" onClick={() => { reaction.action?.run(); dismiss(); }}>{reaction.action.label}</button>
        )}
      </div>
      <button type="button" className="toast-close" onClick={dismiss} aria-label="Закрыть"><IconClose /></button>
    </div>
  );
}
