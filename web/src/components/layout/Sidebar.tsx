import { NavLink } from "react-router-dom";
import { fmtInt } from "../../format";
import { PATHS } from "../../routes";
import { useService } from "../../state/service";
import { useSession } from "../../state/session";
import { IconCompare, IconGrid, IconPin, IconTable, IconZap } from "../ui";

export function Sidebar() {
  const { apiOk, version, conflictsPending } = useService();
  const { stats } = useSession();

  const links = [
    { to: PATHS.dashboard, label: "Обзор", icon: <IconGrid /> },
    { to: PATHS.records, label: "Записи", icon: <IconTable />, badge: stats.queue, title: `На проверку: ${stats.queue}` },
    { to: PATHS.conflicts, label: "Конфликты", icon: <IconCompare />, badge: conflictsPending, title: `Ждут решения: ${conflictsPending}` },
    { to: PATHS.parse, label: "Разбор строки", icon: <IconZap /> },
  ];

  const c = version?.components;
  const gz = c?.gazetteer_stats;

  return (
    <aside className="sidebar">
      <NavLink to={PATHS.dashboard} className="brand" aria-label="Jana Address, обзор">
        <span className="brand-mark"><IconPin /></span>
        <span className="brand-text">
          <span className="brand-name">Jana Address</span>
          <span className="brand-sub">Jana Post</span>
        </span>
      </NavLink>

      <nav className="side-nav" aria-label="Разделы">
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} className={({ isActive }) => `side-link ${isActive ? "is-active" : ""}`}>
            {l.icon}
            <span className="side-label">{l.label}</span>
            {l.badge ? <span className="side-badge" title={l.title}>{fmtInt(l.badge)}</span> : null}
          </NavLink>
        ))}
      </nav>

      <div className={`service ${apiOk === false ? "is-down" : ""}`} role="status">
        <p className="service-head">
          <i className={`dot ${apiOk === null ? "is-wait" : apiOk ? "is-ok" : "is-flag"}`} aria-hidden="true" />
          {apiOk === null ? "Подключение к API" : apiOk ? "API на связи" : "API не отвечает"}
        </p>
        {apiOk === false && <p className="service-line mono">make api-dev</p>}
        {apiOk && c && (
          <>
            <p className="service-line mono">{[c.model, c.mode].filter(Boolean).join(" · ")}</p>
            {gz?.streets ? <p className="service-line">OSM: {fmtInt(gz.streets)} улиц, {fmtInt(gz.house_points ?? gz.houses ?? 0)} домов</p> : null}
          </>
        )}
      </div>
    </aside>
  );
}
