import { Link, useLocation } from "react-router-dom";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot } from "../components/Mascot";
import { PATHS } from "../routes";

export function NotFound() {
  const { pathname } = useLocation();
  return (
    <>
      <PageHeader title="Страница не найдена" />
      <div className="content">
        <section className="card empty-state">
          <Mascot pose="oops" className="empty-mascot" />
          <h2>Адреса <span className="mono">{pathname}</span> нет</h2>
          <Link to={PATHS.dashboard} className="btn btn-primary">На обзор</Link>
        </section>
      </div>
    </>
  );
}
