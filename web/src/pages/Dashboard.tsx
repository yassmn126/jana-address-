import { MascotBubble, type MascotState } from "../components/Mascot";
import { Bar, Chip, MetricCard, Tag } from "../components/ui";
import { REVIEW_THRESHOLD, type Stats } from "../stats";
import { FIELDS, FIELD_LABELS } from "../types";

const fmt = (n: number) => n.toLocaleString("ru-RU");
const pct = (k: number, n: number) => (n ? `${Math.round((100 * k) / n)}%` : "0%");

export function Dashboard({ stats, lastRun, mascot, onShowQueue }: {
  stats: Stats;
  lastRun: { ms: number; count: number } | null;
  mascot: MascotState;
  onShowQueue: () => void;
}) {
  const { n } = stats;
  const perRecord = lastRun && lastRun.count ? lastRun.ms / lastRun.count : null;
  const maxBin = Math.max(...stats.bins, 1);
  const peak = stats.bins.indexOf(Math.max(...stats.bins));
  const maxCity = Math.max(...stats.cities.map((c) => c.n), 1);

  return (
    <div className="page">
      {/* Hero · slide 01 (cover): inverse text on the lime band, two-tone title, hud chip, mascot */}
      <section className="hero">
        <div className="hero-top">
          <span>Jana Post · хакатон 2026</span>
          <span>©2026 Jana Address</span>
        </div>
        <div className="hero-body">
          <div>
            <p className="eyebrow">Jana Address · разбор адресов доставки</p>
            <h1 className="title">
              <span className="dim">Адрес одной строкой.</span><br />
              На выходе — одиннадцать полей.
            </h1>
            <div className="hud">
              <i className="hud-dot" aria-hidden="true" />
              <span className="hud-label">{n ? "Последний разбор" : "Ждём данные"}</span>
              <span className="hud-meta">
                {n
                  ? `${fmt(n)} записей · ${pct(stats.ready, n)} готово · ${perRecord == null ? "—" : perRecord.toFixed(1).replace(".", ",")} мс/запись`
                  : "загрузите JSON на странице «Пакетная обработка»"}
              </span>
            </div>
          </div>
          <MascotBubble state={mascot} />
        </div>
      </section>

      {/* Metric cards · slide 06 */}
      <section className="grid-metrics" aria-label="Ключевые показатели">
        <MetricCard label="Записей" value={fmt(n)} tone={n ? undefined : "dim"} caption="загружено и разобрано" />
        <MetricCard label="Готовы" value={fmt(stats.ready)} tone={n ? "accent" : "dim"}
          chip={<Chip status="ok">≥ {REVIEW_THRESHOLD.toFixed(2)}</Chip>} caption={`${pct(stats.ready, n)} без ручной проверки`} />
        <MetricCard label="На проверку" value={fmt(stats.queue)} tone={stats.queue ? "flag" : "dim"}
          chip={<Chip status="flag">&lt; {REVIEW_THRESHOLD.toFixed(2)}</Chip>} caption="нажмите, чтобы открыть очередь"
          onClick={onShowQueue} title="Открыть очередь проверки" />
        <MetricCard label="Средняя уверенность" value={n ? stats.avg.toFixed(2) : "—"} tone={n ? "accent" : "dim"} caption="по всем записям" />
        <MetricCard label="Проверено" value={fmt(stats.reviewed)} tone={stats.reviewed ? undefined : "dim"} caption="принято оператором" />
        <MetricCard label="Скорость" value={perRecord == null ? "—" : perRecord.toFixed(1).replace(".", ",")} tone={perRecord == null ? "dim" : "accent"}
          chip={<Tag>лимит 300 мс</Tag>} caption="мс на запись через API" />
      </section>

      {/* Analytics */}
      <section className="grid-3">
        <article className="card chart-card">
          <p className="eyebrow">Аналитика</p>
          <h2>Распределение уверенности</h2>
          <p className="sub">записей в каждом диапазоне</p>
          {n ? (
            <>
              <div className="legend">
                <Chip status="ok">готово ≥ 0.70</Chip>
                <Chip status="flag">проверка &lt; 0.70</Chip>
              </div>
              <div className="hist">
                {stats.bins.map((k, i) => (
                  <div key={i} className="hist-slot" title={`${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}: ${k}`}>
                    {i === peak && k > 0 && <span className="hist-cap">{k}</span>}
                    <span className={`hist-bar ${i < REVIEW_THRESHOLD * 10 ? "flag" : ""}`} style={{ height: k ? `${Math.max(3, (k / maxBin) * 100)}%` : 0 }} />
                  </div>
                ))}
                <div className="hist-threshold" style={{ left: `calc(${REVIEW_THRESHOLD * 100}% - 1px)` }}><span>порог 0.70</span></div>
              </div>
              <div className="hist-axis"><span>0</span><span>0.5</span><span>1.0</span></div>
            </>
          ) : <div className="chart-empty">Появится после загрузки данных</div>}
        </article>

        <article className="card chart-card">
          <p className="eyebrow">Аналитика</p>
          <h2>Заполненность полей</h2>
          <p className="sub">доля записей, где поле найдено</p>
          {n ? (
            <div className="bars">
              {FIELDS.map((f) => (
                <div key={f} className="bar-row" title={`${FIELD_LABELS[f]}: ${stats.fill[f]}%`}>
                  <span className="label">{FIELD_LABELS[f]}</span>
                  <Bar value={stats.fill[f]} />
                  <span className="value">{stats.fill[f]}%</span>
                </div>
              ))}
            </div>
          ) : <div className="chart-empty">Появится после загрузки данных</div>}
        </article>

        <article className="card chart-card">
          <p className="eyebrow">Аналитика</p>
          <h2>Топ городов</h2>
          <p className="sub">аналитика по городам, которой не было в сырой базе</p>
          {n ? (
            <div className="bars">
              {stats.cities.map((c) => (
                <div key={c.label} className="bar-row" title={`${c.label}: ${c.n} (${pct(c.n, n)})`}>
                  <span className={`label ${c.kind === "other" ? "muted" : ""}`}>{c.kind === "none" ? <Chip status="flag">{c.label}</Chip> : c.label}</span>
                  <Bar value={(c.n / maxCity) * 100} tone={c.kind === "none" ? "flag" : "accent"} />
                  <span className="value">{c.n}</span>
                </div>
              ))}
            </div>
          ) : <div className="chart-empty">Появится после загрузки данных</div>}
        </article>
      </section>
    </div>
  );
}
