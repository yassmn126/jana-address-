import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/layout/PageHeader";
import { Mascot, type Pose } from "../components/Mascot";
import {
  Bar, Chip, ConfChip, FileButton, IconArrowRight, IconDownload, IconSearch, IconText, IconUpload, StatCard, useDropTarget,
} from "../components/ui";
import { ADDRESSES, BATCHES, count, fmtInt, fmtMs, fmtPct, fmtScore, fmtSource, fmtWhen, RECORDS } from "../format";
import { PATHS } from "../routes";
import { useService } from "../state/service";
import { useSession } from "../state/session";
import { REVIEW_THRESHOLD } from "../stats";
import { FIELDS, FIELD_LABELS, PRECISIONS, PRECISION_LABELS, type RecordOut } from "../types";

export function Dashboard() {
  const session = useSession();
  const { apiOk, runs, runStats, conflictsPending, refreshArchive } = useService();
  const { rows, stats, lastRun } = session;
  const hasRows = rows.length > 0;

  useEffect(() => { refreshArchive(); }, [refreshArchive]);

  const meta = hasRows && lastRun
    ? `${count(lastRun.count, RECORDS)} · загружено ${fmtWhen(lastRun.at)} · ${fmtMs(lastRun.ms / Math.max(1, lastRun.count))} мс на запись`
    : hasRows ? count(rows.length, RECORDS) : undefined;

  return (
    <>
      <PageHeader title="Обзор" meta={meta}>
        {hasRows && <RecordSearch />}
        <FileButton onFile={session.loadFile} disabled={session.busy || apiOk === false}>
          <IconUpload />Загрузить JSON
        </FileButton>
      </PageHeader>

      <div className="content">
        <NextStep />
        {session.error && <div className="error" role="alert">{session.error}</div>}

        {hasRows ? (
          <section className="grid-4" aria-label="Текущая сессия">
            <StatCard label="Записей" value={fmtInt(stats.n)} caption={`${fmtInt(stats.ready)} готовы без проверки`} />
            <StatCard label="На проверку" value={fmtInt(stats.queue)} tone={stats.queue ? "flag" : undefined}
              caption={stats.reviewed ? `проверено оператором: ${fmtInt(stats.reviewed)}` : "уверенность ниже 0,70"}
              onClick={stats.queue ? session.showQueue : undefined} />
            <StatCard label="Конфликты" value={fmtInt(conflictsPending)} tone={conflictsPending ? "flag" : undefined}
              caption="модель и LLM разошлись" to={PATHS.conflicts} />
            <StatCard label="Средняя уверенность" value={fmtScore(stats.avg)} tone={stats.avg >= REVIEW_THRESHOLD ? "ok" : "flag"}
              caption={`${fmtPct(stats.ready, stats.n)} выше порога 0,70`} />
          </section>
        ) : runStats && runStats.runs > 0 ? (
          <section className="grid-4" aria-label="Архив API">
            <StatCard label="Пакетов" value={fmtInt(runStats.runs)} caption={runs?.[0] ? `последний ${fmtWhen(runs[0].time)}` : undefined} />
            <StatCard label="Записей" value={fmtInt(runStats.records)} caption={`${fmtPct(runStats.in_database ?? 0, runStats.records)} найдены до дома`} />
            <StatCard label="Средняя уверенность" value={runStats.mean_confidence != null ? fmtScore(runStats.mean_confidence) : "0"}
              tone={(runStats.mean_confidence ?? 0) >= REVIEW_THRESHOLD ? "ok" : "flag"} caption="порог проверки 0,70" />
            <StatCard label="Конфликты" value={fmtInt(conflictsPending)} tone={conflictsPending ? "flag" : undefined}
              caption="модель и LLM разошлись" to={PATHS.conflicts} />
          </section>
        ) : null}

        {hasRows && <SessionDetail rows={rows} />}
        <ArchiveSection />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ next step banner */

function NextStep() {
  const s = useSession();
  const { apiOk } = useService();
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const drop = useDropTarget(s.loadFile);
  const { stats } = s;

  let pose: Pose, tone = "", eyebrow: string, title: string, body: string, actions: JSX.Element | null = null;

  if (apiOk === false) {
    pose = "sleeping"; tone = "is-flag";
    eyebrow = "API не отвечает";
    title = "Разбор остановлен";
    body = "Бэкенд недоступен. Локально он запускается командой make api-dev; страница проверяет связь каждые 15 секунд.";
  } else if (s.busy && s.progress) {
    pose = "thinking";
    eyebrow = "Разбор";
    title = `${fmtInt(s.progress.done)} из ${fmtInt(s.progress.total)}`;
    body = "";
  } else if (!stats.n) {
    pose = "hello";
    eyebrow = "Новый файл";
    title = "Загрузите адреса";
    body = "JSON-массив { id, raw_address } или список адресов по одному в строке. На выходе 11 полей и confidence в схеме жюри.";
    actions = (
      <>
        <FileButton onFile={s.loadFile} disabled={apiOk === null}><IconUpload />Выбрать файл</FileButton>
        <button type="button" className="btn btn-ghost" onClick={() => setPasting((p) => !p)} aria-expanded={pasting}><IconText />Вставить список</button>
      </>
    );
  } else if (stats.queue) {
    pose = "magnifier";
    eyebrow = "Очередь проверки";
    title = `${count(stats.queue, ADDRESSES)} ждут проверки`;
    body = "Уверенность ниже 0,70. Подтвердите или исправьте поля, затем выгрузите output.json.";
    actions = (
      <>
        <button type="button" className="btn btn-primary" onClick={s.showQueue}>Открыть очередь<IconArrowRight /></button>
        <button type="button" className="btn btn-ghost" onClick={() => s.exportJson()}><IconDownload />output.json</button>
      </>
    );
  } else {
    pose = "celebrating";
    eyebrow = "Готово";
    title = `${count(stats.n, ADDRESSES)} готовы к доставке`;
    body = stats.reviewed
      ? `Все записи выше порога 0,70 или подтверждены оператором (${fmtInt(stats.reviewed)}).`
      : "Все записи выше порога 0,70, ручная проверка не нужна.";
    actions = (
      <>
        <button type="button" className="btn btn-primary" onClick={() => s.exportJson()}><IconDownload />Скачать output.json</button>
        <FileButton onFile={s.loadFile} className="btn btn-ghost"><IconUpload />Новый файл</FileButton>
      </>
    );
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    s.loadText(text);
    setText("");
    setPasting(false);
  };

  return (
    <section className={`next ${tone} ${drop.over ? "is-drag" : ""}`} {...drop.handlers} aria-live="polite">
      <Mascot pose={pose} className="next-mascot" />
      <div className="next-text">
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        {body && <p>{body}</p>}
        {s.busy && s.progress && (
          <span className="progress" aria-hidden="true"><span style={{ width: `${(100 * s.progress.done) / Math.max(1, s.progress.total)}%` }} /></span>
        )}
      </div>
      {actions && <div className="next-actions">{actions}</div>}
      {pasting && !stats.n && (
        <form className="next-paste" onSubmit={submit}>
          <textarea className="field" rows={4} value={text} onChange={(e) => setText(e.target.value)} autoFocus spellCheck={false}
            aria-label="Адреса, по одному в строке" />
          <div className="next-paste-actions">
            <span className="muted">{count(text.split(/\r?\n/).filter((l) => l.trim()).length, ADDRESSES)}</span>
            <button type="button" className="btn btn-quiet" onClick={() => setPasting(false)}>Отмена</button>
            <button type="submit" className="btn btn-primary" disabled={!text.trim()}>Разобрать</button>
          </div>
        </form>
      )}
      {drop.over && <div className="next-drop" aria-hidden="true">Отпустите файл</div>}
    </section>
  );
}

/* ------------------------------------------------------------------ search → records page */

function RecordSearch() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  return (
    <form className="search" role="search" onSubmit={(e) => { e.preventDefault(); navigate(q.trim() ? `${PATHS.records}?q=${encodeURIComponent(q.trim())}` : PATHS.records); }}>
      <IconSearch />
      <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Адрес или ID" aria-label="Найти запись" />
    </form>
  );
}

/* ------------------------------------------------------------------ current session */

function SessionDetail({ rows }: { rows: RecordOut[] }) {
  const queue = rows.filter((r) => r.confidence < REVIEW_THRESHOLD && !r.reviewed).sort((a, b) => a.confidence - b.confidence);

  return (
    <>
      <div className={queue.length ? "grid-main" : "grid-3"}>
        {queue.length > 0 && <QueueTable queue={queue} />}
        <ConfidenceCard />
        {!queue.length && <FieldFillCard />}
        {!queue.length && <CitiesCard />}
      </div>
      {queue.length > 0 && (
        <div className="grid-main">
          <FieldFillCard wide />
          <CitiesCard />
        </div>
      )}
    </>
  );
}

function QueueTable({ queue }: { queue: RecordOut[] }) {
  const navigate = useNavigate();
  const { showQueue } = useSession();
  const top = queue.slice(0, 6);
  return (
    <article className="card table-card">
      <div className="card-head">
        <h3>Очередь проверки <span className="count">{fmtInt(queue.length)}</span></h3>
        <button type="button" className="btn btn-ghost btn-sm" onClick={showQueue}>Все записи<IconArrowRight /></button>
      </div>
      <div className="table-scroll">
        <table className="dtable">
          <thead>
            <tr><th>Адрес</th><th>Город</th><th>Улица, дом</th><th className="num">Уверенность</th><th aria-label="Открыть" /></tr>
          </thead>
          <tbody>
            {top.map((r) => (
              <tr key={r.id} onClick={() => navigate(`${PATHS.record(r.id)}?view=queue`)} tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter") navigate(`${PATHS.record(r.id)}?view=queue`); }}>
                <td className="cell-main">
                  <span className="cell-title" title={r.raw_address}>{r.raw_address}</span>
                  <span className="cell-sub mono">{r.id}</span>
                </td>
                <td className="cell-clip">{r.city ?? <Chip status="flag">не найден</Chip>}</td>
                <td className="cell-clip">{[r.street_type, r.street_name].filter(Boolean).join(" ") || <span className="muted">нет улицы</span>}{r.house ? `, ${r.house}` : ""}</td>
                <td className="num"><ConfChip c={r.confidence} /></td>
                <td className="cell-go"><IconArrowRight /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function ConfidenceCard() {
  const { stats } = useSession();
  const maxBin = Math.max(...stats.bins, 1);
  return (
    <article className="card chart-card">
      <div className="card-head">
        <h3>Уверенность</h3>
        <span className="card-meta">порог 0,70</span>
      </div>
      <div className="hist" role="img" aria-label={`Ниже порога ${stats.low}, выше ${stats.ready}`}>
        {stats.bins.map((k, i) => (
          <div key={i} className="hist-slot" title={`${fmtScore(i / 10)}–${fmtScore((i + 1) / 10)}: ${k}`}>
            {k > 0 && <span className="hist-cap">{k}</span>}
            <span className={`hist-bar ${i < REVIEW_THRESHOLD * 10 ? "is-flag" : ""}`} style={{ height: k ? `${Math.max(4, (k / maxBin) * 100)}%` : 0 }} />
          </div>
        ))}
        <div className="hist-threshold" style={{ left: `${REVIEW_THRESHOLD * 100}%` }} />
      </div>
      <div className="hist-axis"><span>0</span><span>0,5</span><span>1</span></div>
      <div className="legend">
        <Chip status="flag">{fmtInt(stats.low)} на проверку</Chip>
        <Chip status="ok">{fmtInt(stats.ready)} готовы</Chip>
      </div>
    </article>
  );
}

function FieldFillCard({ wide = false }: { wide?: boolean }) {
  const { stats } = useSession();
  return (
    <article className="card chart-card">
      <div className="card-head">
        <h3>Заполненность полей</h3>
        <span className="card-meta">доля записей</span>
      </div>
      <div className={`bars ${wide ? "bars-2" : ""}`}>
        {FIELDS.map((f) => (
          <div key={f} className="bar-row">
            <span className="label">{FIELD_LABELS[f]}</span>
            <Bar value={stats.fill[f]} />
            <span className="value">{stats.fill[f]}%</span>
          </div>
        ))}
      </div>
    </article>
  );
}

function CitiesCard() {
  const { stats } = useSession();
  const max = Math.max(...stats.cities.map((c) => c.n), 1);
  return (
    <article className="card chart-card">
      <div className="card-head">
        <h3>Города</h3>
        <span className="card-meta">записей</span>
      </div>
      <div className="bars">
        {stats.cities.map((c) => (
          <div key={c.label} className="bar-row">
            <span className={`label ${c.kind ? "muted" : ""}`}>{c.label}</span>
            <Bar value={(c.n / max) * 100} tone={c.kind === "none" ? "flag" : c.kind === "other" ? "muted" : "accent"} />
            <span className="value">{fmtInt(c.n)}</span>
          </div>
        ))}
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ run archive (server side, survives reloads) */

function ArchiveSection() {
  const { runs, runStats } = useService();
  if (!runs?.length || !runStats) return null;
  const by = runStats.by_precision ?? {};
  const total = PRECISIONS.reduce((s, p) => s + (by[p] ?? 0), 0);

  return (
    <>
      <div className="section-head">
        <h2>Архив API</h2>
        <p>{count(runStats.runs, BATCHES)} · {count(runStats.records, RECORDS)}</p>
      </div>
      <div className="grid-main">
        <article className="card table-card">
          <div className="card-head">
            <h3>Последние пакеты</h3>
          </div>
          <div className="table-scroll">
            <table className="dtable">
              <thead>
                <tr><th>Пакет</th><th>Время</th><th className="num">Записей</th><th className="num">Уверенность</th><th className="num">Скорость</th><th>До дома</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="is-static">
                    <td className="cell-main">
                      <span className="cell-title">{fmtSource(r.source)}</span>
                      <span className="cell-sub mono">#{r.id}{r.version ? ` · v${r.version}` : ""}</span>
                    </td>
                    <td className="nowrap">{fmtWhen(r.time)}</td>
                    <td className="num mono">{fmtInt(r.records)}</td>
                    <td className="num">{r.mean_confidence != null ? <ConfChip c={r.mean_confidence} /> : <span className="muted">нет</span>}</td>
                    <td className="num mono nowrap">{r.elapsed_ms != null && r.records ? `${fmtMs(r.elapsed_ms / r.records)} мс` : "нет"}</td>
                    <td>
                      <span className="share">
                        <Bar value={100 * (r.in_database_share ?? 0)} />
                        <span className="mono">{fmtPct(r.in_database, r.records)}</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="card chart-card">
          <div className="card-head">
            <h3>Геокодирование</h3>
            <span className="card-meta">{count(total, ADDRESSES)}</span>
          </div>
          <div className="bars">
            {PRECISIONS.filter((p) => by[p]).map((p) => (
              <div key={p} className="bar-row">
                <span className="label">{PRECISION_LABELS[p]}</span>
                <Bar value={total ? (100 * (by[p] ?? 0)) / total : 0} tone={p === "none" ? "flag" : p.startsWith("house") ? "accent" : "muted"} />
                <span className="value">{fmtPct(by[p] ?? 0, total)}</span>
              </div>
            ))}
          </div>
        </article>
      </div>
    </>
  );
}
