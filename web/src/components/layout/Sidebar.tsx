import { NavLink } from "react-router-dom";
import logo from "../../assets/jana-post-logo.png";
import { count, fmtInt, fmtPct, fmtWhen, RECORDS } from "../../format";
import { PATHS } from "../../routes";
import { useService } from "../../state/service";
import { useSession } from "../../state/session";
import { IconCompare, IconDownload, IconGrid, IconPanel, IconTable, IconTrash, IconZap } from "../ui";

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { apiOk, version, conflictsPending } = useService();
  const s = useSession();
  const { stats } = s;

  const links = [
    { to: PATHS.dashboard, label: "Обзор", icon: <IconGrid /> },
    { to: PATHS.records, label: "Записи", icon: <IconTable />, total: stats.n, badge: stats.queue, tip: `На проверку: ${stats.queue}` },
    { to: PATHS.conflicts, label: "Конфликты", icon: <IconCompare />, badge: conflictsPending, tip: `Ждут решения: ${conflictsPending}` },
    { to: PATHS.parse, label: "Разбор строки", icon: <IconZap /> },
  ];

  const c = version?.components;
  const gz = c?.gazetteer_stats;
  const apiLabel = apiOk === null ? "Подключение к API" : apiOk ? "API на связи" : "API не отвечает";
  const accepted = stats.reviewed;
  const queue = stats.queue;
  const ready = Math.max(0, stats.n - queue - accepted);
  const done = stats.n ? (stats.n - queue) / stats.n : 0;

  const clear = () => {
    if (window.confirm(`Удалить ${count(stats.n, RECORDS)} из этого браузера? Архив API не изменится.`)) s.clear();
  };

  return (
    <aside className={`sidebar ${collapsed ? "is-collapsed" : ""}`}>
      <div className="side-top">
        <NavLink to={PATHS.dashboard} className="brand" aria-label="Jana Post · Address, обзор">
          <img className="brand-logo" src={logo} alt="Jana Post" width={66} height={30} />
          <span className="brand-product">Address</span>
        </NavLink>
        <button type="button" className="icon-btn side-toggle" onClick={onToggle}
          aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"} aria-expanded={!collapsed} data-tip={collapsed ? "Развернуть · [" : "Свернуть · ["}>
          <IconPanel />
        </button>
      </div>

      <nav className="side-nav" aria-label="Разделы">
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} className={({ isActive }) => `side-link ${isActive ? "is-active" : ""}`}
            data-tip={collapsed ? `${l.label}${l.badge ? ` · ${l.tip}` : ""}` : undefined}>
            <span className="side-icon">
              {l.icon}
              {collapsed && l.badge ? <i className="side-dot" aria-hidden="true">{l.badge > 99 ? "99+" : l.badge}</i> : null}
            </span>
            <span className="side-label">{l.label}</span>
            {!collapsed && l.total ? <span className="side-total">{fmtInt(l.total)}</span> : null}
            {!collapsed && l.badge ? <span className="side-badge" title={l.tip}>{fmtInt(l.badge)}</span> : null}
          </NavLink>
        ))}
      </nav>

      <div className="side-bottom">
        {s.ready && stats.n > 0 && (collapsed ? (
          <NavLink to={PATHS.records} className="side-ring" data-tip={`Сессия: ${count(stats.n, RECORDS)} · обработано ${fmtPct(stats.n - queue, stats.n)}`}>
            <svg viewBox="0 0 36 36" width="36" height="36" aria-hidden="true">
              <circle cx="18" cy="18" r="15" className="ring-track" />
              <circle cx="18" cy="18" r="15" className="ring-fill" strokeDasharray={`${(done * 94.25).toFixed(2)} 94.25`} />
            </svg>
            <span>{Math.round(done * 100)}</span>
          </NavLink>
        ) : (
          <section className="side-card" aria-label="Текущая сессия">
            <div className="side-card-head">
              <p className="eyebrow">Сессия</p>
              <span className="mono">{count(stats.n, RECORDS)}</span>
            </div>
            <span className="split-bar" aria-hidden="true">
              <span className="is-ok" style={{ flexGrow: ready }} />
              <span className="is-done" style={{ flexGrow: accepted }} />
              <span className="is-flag" style={{ flexGrow: queue }} />
            </span>
            <ul className="side-legend">
              <li><i className="is-ok" />Готовы<b>{fmtInt(ready)}</b></li>
              <li><i className="is-done" />Приняты<b>{fmtInt(accepted)}</b></li>
              <li><i className="is-flag" />На проверку<b>{fmtInt(queue)}</b></li>
            </ul>
            {s.lastRun && <p className="side-note">загружено {fmtWhen(s.lastRun.at)}</p>}
            <div className="side-actions">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => s.exportJson()}><IconDownload />output.json</button>
              <button type="button" className="icon-btn" onClick={clear} aria-label="Очистить сессию" data-tip="Очистить сессию"><IconTrash /></button>
            </div>
          </section>
        ))}

        <div className={`service ${apiOk === false ? "is-down" : ""}`} role="status" data-tip={collapsed ? apiLabel : undefined}>
          <p className="service-head">
            <i className={`dot ${apiOk === null ? "is-wait" : apiOk ? "is-ok" : "is-flag"}`} aria-hidden="true" />
            <span className="service-title">{apiLabel}</span>
          </p>
          {!collapsed && apiOk === false && <p className="service-line mono">make api-dev</p>}
          {!collapsed && apiOk && c && (
            <>
              <p className="service-line mono">{[c.model, c.mode].filter(Boolean).join(" · ")}</p>
              {gz?.streets ? <p className="service-line">OSM: {fmtInt(gz.streets)} улиц, {fmtInt(gz.house_points ?? gz.houses ?? 0)} домов</p> : null}
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
