import type { ReactNode } from "react";

/** Sticky page bar: title, one line of live context, page actions on the right. */
export function PageHeader({ title, meta, children }: { title: string; meta?: ReactNode; children?: ReactNode }) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <h1>{title}</h1>
        {meta && <p className="topbar-meta">{meta}</p>}
      </div>
      {children && <div className="topbar-actions">{children}</div>}
    </header>
  );
}
