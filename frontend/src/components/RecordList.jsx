import { formatDetectedAge } from "../utils/formatDetectedAge";

function flag(code = "") {
  if (!/^[a-z]{2}$/i.test(code)) return code;
  return code.toUpperCase().replace(/./g, (character) =>
    String.fromCodePoint(127397 + character.charCodeAt())
  );
}

function absoluteTime(value) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function sourceName(record) {
  if (record.source_name) return record.source_name;
  if (record.ingestion_method === "cubingchina_websocket") return "CubingChina";
  if (["api_polling", "graphql_subscription"].includes(record.ingestion_method)) return "WCA Live";
  return "Source";
}

function WorkerSummary({ worker, roundStatus }) {
  if (!worker) return <span className="worker-state unknown">Status unknown</span>;
  const lastUpdate = worker.last_successful_poll_at || worker.last_successful_snapshot_at || worker.last_message_at || worker.last_successful_discovery_at;
  return (
    <div className="worker-summary">
      <span className={`worker-state ${worker.status}`}>{worker.status}</span>
      {Object.prototype.hasOwnProperty.call(worker, "connected") && (
        <span>{worker.connected ? "Connected" : "Disconnected"}</span>
      )}
      {roundStatus && <span>{roundStatus.subscribed}/{roundStatus.discovered} rounds subscribed</span>}
      {Object.prototype.hasOwnProperty.call(worker, "connected_competition_count") && (
        <span>{worker.connected_competition_count}/{worker.target_competition_count} competitions connected</span>
      )}
      <span title={absoluteTime(lastUpdate)}>Last successful update: {lastUpdate ? formatDetectedAge(lastUpdate) : "never"}</span>
      {worker.last_error && <span className="worker-error" title={worker.last_error}>Latest error</span>}
    </div>
  );
}

export function RecordList({ title, subtitle, records, loading, error, worker, roundStatus }) {
  return (
    <section className="pipeline-section">
      <div className="pipeline-heading">
        <div><p className="eyebrow">{subtitle}</p><h2>{title}</h2></div>
        <WorkerSummary worker={worker} roundStatus={roundStatus} />
      </div>
      {error && <p className="error">This table could not be updated: {error}</p>}
      {loading ? <p className="loading">Loading this pipeline…</p> : (
        records.length ? (
          <div className="record-table-wrap">
            <div className="record-table" role="table" aria-label={title}>
              <div className="record-header" role="row">
                <span>Level</span><span>Event / type</span><span>Result</span><span>Competitor</span>
                <span>Competition</span><span>Round</span><span>Detected</span><span>Match</span><span>Source</span>
              </div>
              {records.map((record) => (
                <article className={`record-row ${record.status === "withdrawn" ? "withdrawn" : ""}`} role="row" key={record.id}>
                  <div><span className={`level level-${record.record_level.toLowerCase()}`}>{record.record_level}</span>{record.status === "withdrawn" && <small>corrected</small>}</div>
                  <div><strong>{record.event_name}</strong><small>{record.kind}</small></div>
                  <strong className="result">{record.formatted_result || record.raw_result}</strong>
                  <div><strong>{record.competitor_name}</strong><small>{flag(record.country_code)} {record.competitor_wca_id}</small></div>
                  <div><strong>{record.competition_name}</strong><small>{record.wca_competition_id}</small></div>
                  <div><strong>{record.round_name || "—"}</strong><small>{record.round_id || ""}</small></div>
                  <time className="detected-age" dateTime={record.detected_at} title={absoluteTime(record.detected_at)}>
                    {formatDetectedAge(record.detected_at)}<small>{absoluteTime(record.detected_at)}</small>
                  </time>
                  <span className={record.matched_in_other_pipeline ? "match yes" : "match"}>{record.matched_in_other_pipeline ? "Matched" : "Waiting"}</span>
                  {record.source_url ? <a className="source-link" href={record.source_url} target="_blank" rel="noreferrer">{sourceName(record)} ↗</a> : <span>—</span>}
                </article>
              ))}
            </div>
          </div>
        ) : <div className="empty"><h3>No records found</h3><p>This pipeline has not independently detected a matching record yet.</p></div>
      )}
    </section>
  );
}
