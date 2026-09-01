import { useCallback, useEffect, useMemo, useState } from "react";

import { getIngestionStatus, getRecentRecords } from "../api/client";
import MoonIcon from "../assets/icons/Moon_of_May_complex.svg";
import SunIcon from "../assets/icons/Sun_of_May_simplified.svg";
import { RecordList } from "./RecordList";

const REFRESH_INTERVAL_MS = 3_000;
const HISTORY_LIMIT = 120;

function number(value) {
  return new Intl.NumberFormat("en-GB").format(Number(value) || 0);
}

function age(value) {
  if (!value) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function competitionDateRange(start, end) {
  if (!start) return "—";
  const format = (value) => new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC"
  }).format(new Date(`${value}T00:00:00Z`));
  return !end || end === start ? format(start) : `${format(start)} – ${format(end)}`;
}

function rate(history, source, counter) {
  if (history.length < 2) return 0;
  const previous = history.at(-2);
  const current = history.at(-1);
  const elapsed = Math.max((current.at - previous.at) / 1000, 0.001);
  const before = previous[source]?.counters?.[counter] || 0;
  const after = current[source]?.counters?.[counter] || 0;
  return after >= before ? (after - before) / elapsed : 0;
}

function Metric({ label, value, detail }) {
  return (
    <div className="debug-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function StatusPill({ state, children }) {
  return <span className={`debug-status ${state || "neutral"}`}>{children}</span>;
}

function Sparkline({ values, label }) {
  const width = 320;
  const height = 72;
  const maximum = Math.max(...values, 1);
  const points = values.map((value, index) => {
    const x = values.length === 1 ? width : (index / (values.length - 1)) * width;
    const y = height - (value / maximum) * (height - 8) - 4;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg aria-label={label} className="queue-sparkline" role="img" viewBox={`0 0 ${width} ${height}`}>
      <line x1="0" x2={width} y1={height - 4} y2={height - 4} />
      <polyline points={points || `0,${height - 4} ${width},${height - 4}`} />
    </svg>
  );
}

function queueState(queue) {
  if (!queue?.connected) return "neutral";
  if ((queue.message_queue_size || 0) >= 100) return "critical";
  if ((queue.message_queue_size || 0) >= 25) return "warning";
  return "healthy";
}

function QueueCard({ title, subtitle, queue, history, source, incomingCounter, outgoingCounter }) {
  const state = queueState(queue);
  const incomingRate = rate(history, source, incomingCounter);
  const outgoingRate = rate(history, source, outgoingCounter);
  const values = history.map((sample) => sample[source]?.message_queue_size || 0);
  return (
    <article className={`queue-card ${state}`}>
      <div className="queue-card-heading">
        <div>
          <p className="debug-kicker">{subtitle}</p>
          <h2>{title}</h2>
        </div>
        <StatusPill state={state}>{queue?.connected ? "connected" : "disconnected"}</StatusPill>
      </div>
      <div className="queue-number">
        <strong>{number(queue?.message_queue_size)}</strong>
        <span>messages waiting</span>
      </div>
      <Sparkline values={values} label={`${title} queue depth over this session`} />
      <div className="queue-stats">
        <Metric label="Incoming" value={`${incomingRate.toFixed(1)}/s`} />
        <Metric label="Processing" value={`${outgoingRate.toFixed(1)}/s`} />
        <Metric label="Peak" value={number(queue?.peak_message_queue_size)} />
        <Metric label="Capacity" value={queue?.queue_capacity ?? "Unbounded"} />
      </div>
      <p className="telemetry-age">Telemetry {age(queue?.captured_at)}</p>
    </article>
  );
}

function WorkerCard({ title, description, worker, extra, showMessage = true, showSnapshot = true }) {
  const running = worker?.status === "running";
  const state = worker?.last_error ? "critical" : running ? "healthy" : "neutral";
  return (
    <article className="worker-card">
      <div className="worker-card-heading">
        <div><h3>{title}</h3><p>{description}</p></div>
        <StatusPill state={state}>{worker?.status || "unknown"}</StatusPill>
      </div>
      <dl className="debug-definition-list">
        {"connected" in (worker || {}) && <><dt>Connection</dt><dd>{worker.connected ? "Connected" : "Disconnected"}</dd></>}
        <dt>Heartbeat</dt><dd title={worker?.heartbeat_at || ""}>{age(worker?.heartbeat_at)}</dd>
        {showMessage && <><dt>Last message</dt><dd title={worker?.last_message_at || ""}>{age(worker?.last_message_at)}</dd></>}
        {showSnapshot && <><dt>Last round snapshot</dt><dd title={worker?.last_successful_snapshot_at || ""}>{age(worker?.last_successful_snapshot_at)}</dd></>}
        <dt>Observations</dt><dd>{number(worker?.observations_count)}</dd>
        {extra}
      </dl>
      {worker?.last_error && <p className="debug-inline-error">{worker.last_error}</p>}
    </article>
  );
}

function usePipelineRecords(source, enabled) {
  const [state, setState] = useState({ records: [], loading: true, error: "" });
  useEffect(() => {
    if (!enabled) return undefined;
    let current = true;
    const refresh = async () => {
      try {
        const records = await getRecentRecords({ source });
        if (current) setState({ records, loading: false, error: "" });
      } catch (reason) {
        if (current) setState((previous) => ({ ...previous, loading: false, error: reason.message }));
      }
    };
    refresh();
    const interval = window.setInterval(refresh, 30_000);
    return () => { current = false; window.clearInterval(interval); };
  }, [enabled, source]);
  return state;
}

function SourceMonitoring({ status }) {
  const [open, setOpen] = useState(false);
  const subscriptions = usePipelineRecords("graphql_subscription", open);
  const api = usePipelineRecords("api_polling", open);
  const cubingChina = usePipelineRecords("cubingchina_websocket", open);
  return (
    <details className="source-monitoring debug-source-monitoring" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Provider claims and source observations</summary>
      <p>Persisted provider evidence for diagnosis. These rows are not CubingNow&apos;s final classification.</p>
      {open && <>
        <RecordList title="Source observations — WCA Live GraphQL" subtitle="Persisted full-round snapshot diffs" {...subscriptions} worker={status?.graphql_subscription} roundStatus={status?.subscription_rounds} />
        <RecordList title="Source observations — WCA Live API" subtitle="WCA Live recentRecords query" {...api} worker={status?.api_polling} />
        <RecordList title="Source observations — CubingChina" subtitle="Untrusted provider claims retained for comparison" {...cubingChina} worker={status?.cubingchina_websocket} />
      </>}
    </details>
  );
}

function overallState(status, requestError) {
  if (requestError || !status) return { state: "critical", label: "Unavailable", note: requestError || "Waiting for telemetry" };
  const queues = Object.values(status.websocket_queues || {});
  const criticalQueue = queues.some((queue) => (queue.message_queue_size || 0) >= 100);
  const workerError = [status.api_polling, status.graphql_subscription, status.cubingchina_websocket].find((worker) => worker?.last_error);
  if (criticalQueue || status.classification?.failed_scope_count || workerError) {
    return { state: "critical", label: "Degraded", note: workerError?.last_error || "A pipeline needs attention" };
  }
  const warningQueue = queues.some((queue) => (queue.message_queue_size || 0) >= 25);
  const runningDisconnected = [status.graphql_subscription, status.cubingchina_websocket].some((worker) => worker?.status === "running" && !worker.connected);
  if (warningQueue || runningDisconnected) return { state: "warning", label: "Attention", note: "Data is available, but one signal is outside its normal state" };
  return { state: "healthy", label: "Operational", note: "No active backlogs or worker errors detected" };
}

export function DebugDashboard() {
  const [darkMode, setDarkMode] = useState(() => window.localStorage.getItem("cubingnow-theme") === "dark");
  const [status, setStatus] = useState(null);
  const [requestError, setRequestError] = useState("");
  const [history, setHistory] = useState([]);
  const [paused, setPaused] = useState(false);
  const [copyState, setCopyState] = useState("");

  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    window.localStorage.setItem("cubingnow-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  const refresh = useCallback(async () => {
    try {
      const payload = await getIngestionStatus();
      setStatus(payload);
      setRequestError("");
      const at = new Date(payload.generated_at || Date.now()).getTime();
      setHistory((previous) => {
        if (previous.at(-1)?.at === at) return previous;
        return [...previous, { at, ...payload.websocket_queues }].slice(-HISTORY_LIMIT);
      });
    } catch (reason) {
      setRequestError(reason.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (paused) return undefined;
    const interval = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [paused, refresh]);

  const health = overallState(status, requestError);
  const competitions = status?.cubingchina_websocket?.metadata?.competitions || [];
  const frameTotals = useMemo(() => ({
    wca: status?.websocket_queues?.wca_live?.counters || {},
    china: status?.websocket_queues?.cubingchina?.counters || {},
  }), [status]);

  const copySnapshot = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(status, null, 2));
      setCopyState("Copied");
    } catch {
      setCopyState("Copy unavailable");
    }
    window.setTimeout(() => setCopyState(""), 1800);
  };

  return (
    <div className="debug-page">
      <header>
        <div className="header-inner debug-header-inner">
          <a className="brand" href="/">CubingNow</a>
          <div className="header-actions">
            <a className="header-link" href="/">Achievements</a>
            <button aria-label={darkMode ? "Use light mode" : "Use dark mode"} aria-pressed={darkMode} className="theme-toggle" onClick={() => setDarkMode((value) => !value)} type="button">
              <img alt="" aria-hidden="true" src={darkMode ? MoonIcon : SunIcon} />
            </button>
          </div>
        </div>
      </header>
      <main className="debug-main">
        <section className="debug-hero">
          <div>
            <p className="eyebrow">Read-only operations</p>
            <h1>Debug dashboard</h1>
            <p className="debug-intro">Live ingestion, queue, classification, and delivery telemetry.</p>
          </div>
          <div className="debug-actions">
            <button className="debug-button" onClick={() => setPaused((value) => !value)} type="button">{paused ? "Resume live updates" : "Pause"}</button>
            <button className="debug-button" onClick={refresh} type="button">Refresh now</button>
            <button className="debug-button" disabled={!status} onClick={copySnapshot} type="button">{copyState || "Copy snapshot"}</button>
          </div>
        </section>

        <section className={`health-banner ${health.state}`}>
          <span className="health-orb" />
          <div><strong>{health.label}</strong><p>{health.note}</p></div>
          <div className="health-meta"><span>{paused ? "Updates paused" : "Updating every 3 seconds"}</span><span>Server snapshot {age(status?.generated_at)}</span><span>{status?.response_generation_ms ?? "—"} ms API</span></div>
        </section>

        {requestError && <p className="debug-request-error">Could not refresh telemetry: {requestError}. Showing the most recent successful snapshot.</p>}

        <section className="debug-section">
          <div className="debug-section-heading"><div><p className="debug-kicker">Backpressure</p><h2>WebSocket queues</h2></div><p>Session charts hold the latest six minutes. Queue capacity is deliberately unbounded.</p></div>
          <div className="queue-grid">
            <QueueCard title="WCA Live" subtitle="GraphQL subscription" queue={status?.websocket_queues?.wca_live} history={history} source="wca_live" incomingCounter="subscription_messages_queued" outgoingCounter="subscription_messages_dequeued" />
            <QueueCard title="CubingChina" subtitle={`${number(status?.websocket_queues?.cubingchina?.connection_count)} competition sockets`} queue={status?.websocket_queues?.cubingchina} history={history} source="cubingchina" incomingCounter="messages_queued" outgoingCounter="messages_dequeued" />
          </div>
          <div className="metric-strip">
            <Metric label="WCA frames" value={number(frameTotals.wca.frames_received)} detail={`${number(frameTotals.wca.bytes_received)} bytes`} />
            <Metric label="WCA malformed" value={number(frameTotals.wca.malformed_frames)} detail={`${number(frameTotals.wca.unexpected_frames)} unexpected`} />
            <Metric label="CubingChina frames" value={number(frameTotals.china.frames_received)} detail={`${number(frameTotals.china.bytes_received)} bytes`} />
            <Metric label="CubingChina malformed" value={number(frameTotals.china.malformed_frames)} detail={`${number(frameTotals.china.error_frames)} errors`} />
          </div>
        </section>

        <section className="debug-section">
          <div className="debug-section-heading"><div><p className="debug-kicker">Workers</p><h2>Ingestion pipelines</h2></div></div>
          <div className="worker-grid">
            <WorkerCard title="WCA Live GraphQL" description="Full-round snapshot subscriptions" worker={status?.graphql_subscription} extra={<><dt>Subscribed rounds</dt><dd>{number(status?.subscription_rounds?.subscribed)} / {number(status?.subscription_rounds?.discovered)}</dd><dt>Round errors</dt><dd>{number(status?.subscription_rounds?.errors)}</dd></>} />
            <WorkerCard title="WCA Live API" description="Recent-record polling and trusted claims" worker={status?.api_polling} showMessage={false} showSnapshot={false} extra={<><dt>Last poll</dt><dd>{age(status?.api_polling?.last_successful_poll_at)}</dd></>} />
            <WorkerCard title="CubingChina" description="Read-only competition sockets" worker={status?.cubingchina_websocket} extra={<><dt>Connections</dt><dd>{number(status?.cubingchina_websocket?.connected_competition_count)} / {number(status?.cubingchina_websocket?.target_competition_count)}</dd><dt>Target rounds</dt><dd>{number(status?.cubingchina_websocket?.target_round_count)}</dd></>} />
          </div>
        </section>

        <section className="debug-section split-section">
          <article className="debug-panel">
            <div className="debug-panel-heading"><div><p className="debug-kicker">Durable work</p><h2>Classification</h2></div><StatusPill state={status?.classification?.failed_scope_count ? "critical" : status?.classification?.pending_scope_count ? "warning" : "healthy"}>{status?.classification?.failed_scope_count ? "failed" : "ready"}</StatusPill></div>
            <div className="panel-metrics">
              <Metric label="Pending scopes" value={number(status?.classification?.pending_scope_count)} />
              <Metric label="Claimed" value={number(status?.classification?.claimed_scope_count)} />
              <Metric label="Failed" value={number(status?.classification?.failed_scope_count)} />
              <Metric label="Oldest lag" value={`${number(Math.round(status?.classification?.oldest_observation_lag_seconds || 0))}s`} />
              <Metric label="Slowest recent" value={`${number(status?.classification?.max_last_duration_ms)}ms`} />
              <Metric label="Last completion" value={age(status?.classification?.last_completed_at)} />
            </div>
          </article>
          <article className="debug-panel">
            <div className="debug-panel-heading"><div><p className="debug-kicker">Outbox</p><h2>Notifications</h2></div><StatusPill state={status?.notifications?.due_count ? "warning" : "healthy"}>{status?.notifications?.due_count ? "work due" : "clear"}</StatusPill></div>
            <div className="panel-metrics">
              <Metric label="Queued" value={number(status?.notifications?.queued_count)} detail={`${number(status?.notifications?.due_count)} due now`} />
              <Metric label="Processing" value={number(status?.notifications?.deliveries?.processing)} />
              <Metric label="Sent" value={number(status?.notifications?.deliveries?.sent)} />
              <Metric label="Permanent failures" value={number(status?.notifications?.deliveries?.permanently_failed)} />
              <Metric label="Active endpoints" value={number(status?.notifications?.active_endpoint_count)} />
              <Metric label="Events · 24h" value={number(status?.notifications?.events_last_24h)} />
            </div>
          </article>
        </section>

        <section className="debug-section">
          <div className="debug-section-heading"><div><p className="debug-kicker">Data flow</p><h2>Reconciled pipeline</h2></div></div>
          <div className="pipeline-flow">
            <Metric label="Raw observations" value={number(status?.record_pipeline?.source_observation_count)} />
            <span aria-hidden="true">→</span>
            <Metric label="Result evidence" value={number(status?.record_pipeline?.result_observation_count)} />
            <span aria-hidden="true">→</span>
            <Metric label="Canonical results" value={number(status?.record_pipeline?.canonical_result_count)} detail={`${number(status?.record_pipeline?.pending_validation_count)} pending validation`} />
            <span aria-hidden="true">→</span>
            <Metric label="Active achievements" value={number(status?.record_pipeline?.active_achievement_count)} detail={`${number(status?.record_pipeline?.rejected_validation_count)} rejected`} />
          </div>
        </section>

        <section className="debug-section">
          <div className="debug-section-heading"><div><p className="debug-kicker">Per socket</p><h2>CubingChina competitions</h2></div><p>{number(competitions.length)} active targets</p></div>
          <div className="debug-table-wrap">
            <table className="debug-table">
              <thead><tr><th>Competition</th><th>Date</th><th>State</th><th>Queue</th><th>Frames</th><th>Rounds</th><th>Last message</th><th>Last round snapshot</th><th>Error</th></tr></thead>
              <tbody>
                {competitions.map((competition) => <tr key={competition.slug}>
                  <td><strong>{competition.competition_name || competition.slug}</strong><small>{competition.wca_competition_id || competition.slug}</small></td>
                  <td>{competitionDateRange(competition.competition_start_date, competition.competition_end_date)}</td>
                  <td><StatusPill state={competition.last_error ? "critical" : competition.connected ? "healthy" : "neutral"}>{competition.connected ? "connected" : competition.status}</StatusPill></td>
                  <td className="numeric-cell">{number(competition.websocket?.message_queue_size)}<small>peak {number(competition.websocket?.peak_message_queue_size)}</small></td>
                  <td className="numeric-cell">{number(competition.websocket?.counters?.frames_received)}</td>
                  <td className="numeric-cell">{number(competition.active_round_count)}</td>
                  <td title={competition.last_message_at || ""}>{age(competition.last_message_at)}</td>
                  <td title={competition.last_snapshot_at || ""}>{age(competition.last_snapshot_at)}</td>
                  <td className={competition.last_error ? "table-error" : ""}>{competition.last_error || "—"}</td>
                </tr>)}
                {!competitions.length && <tr><td className="debug-empty-row" colSpan="9">No active CubingChina competition targets.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <SourceMonitoring status={status} />
      </main>
      <footer className="debug-footer"><span>CubingNow operations</span><span>Public, read-only, payload-safe telemetry</span></footer>
    </div>
  );
}
