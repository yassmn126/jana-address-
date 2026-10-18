import { IconCompare, IconGrid, IconLayers, IconPin, IconZap } from "./ui";

export type Page = "dashboard" | "batch" | "conflicts" | "quick";
export const PAGES: Page[] = ["dashboard", "batch", "conflicts", "quick"];

const LINKS: { page: Page; label: string; icon: JSX.Element }[] = [
  { page: "dashboard", label: "Обзор", icon: <IconGrid /> },
  { page: "batch", label: "Пакетная обработка", icon: <IconLayers /> },
  { page: "conflicts", label: "Конфликты", icon: <IconCompare /> },
  { page: "quick", label: "Быстрый тест", icon: <IconZap /> },
];

export function Navbar({ page, queue, conflicts, apiOk }: { page: Page; queue: number; conflicts: number; apiOk: boolean | null }) {
  const api = apiOk === null
    ? { cls: "waiting", meta: "проверка…" }
    : apiOk ? { cls: "", meta: "на связи" } : { cls: "down", meta: "не отвечает" };
  const badge: Partial<Record<Page, { n: number; title: string }>> = {
    batch: { n: queue, title: `В очереди на проверку: ${queue}` },
    conflicts: { n: conflicts, title: `Конфликтов ждут решения: ${conflicts}` },
  };
  return (
    <header className="navbar">
      <div className="navbar-inner">
        <a href="#dashboard" className="brand" aria-label="Jana Address">
          <span className="brand-mark"><IconPin /></span>
          <span className="brand-name">Jana Address</span>
          <span className="brand-badge">AI</span>
        </a>
        <nav className="nav" aria-label="Разделы">
          {LINKS.map((l) => {
            const b = badge[l.page];
            return (
              <a key={l.page} href={`#${l.page}`} className={`nav-link ${page === l.page ? "is-active" : ""}`} aria-current={page === l.page ? "page" : undefined}>
                {l.icon}
                <span>{l.label}</span>
                {b && b.n > 0 && <span className="nav-badge" title={b.title}>{b.n}</span>}
              </a>
            );
          })}
        </nav>
        <div className="hud" title="Состояние FastAPI-бэкенда (/health)">
          <i className={`hud-dot ${api.cls}`} aria-hidden="true" />
          <span className="hud-label">API</span>
          <span className="hud-meta">{api.meta}</span>
        </div>
      </div>
    </header>
  );
}
