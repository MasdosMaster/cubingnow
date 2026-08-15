import { formatDetectedAge } from "../utils/formatDetectedAge";

function flag(code = "") {
  if (!/^[a-z]{2}$/i.test(code)) return code;
  return code.toUpperCase().replace(/./g, (character) =>
    String.fromCodePoint(127397 + character.charCodeAt())
  );
}

function absoluteTime(value) {
  return value ? new Date(value).toLocaleString() : "Unknown";
}

const LABELS = {
  WR: "World records",
  CR: "Continental records",
  NR: "National records",
  PR: "Personal records",
};

export function AchievementList({ level, records, loading, error }) {
  const title = LABELS[level] || `${level} results`;
  return (
    <section className="pipeline-section public-achievement-section">
      <div className="pipeline-heading">
        <div>
          <p className="eyebrow">Canonical, validated achievements</p>
          <h2>{title}</h2>
        </div>
      </div>
      {error && <p className="error">This table could not be updated: {error}</p>}
      {loading ? <p className="loading">Loading {level} results…</p> : (
        records.length ? (
          <div className="record-table-wrap">
            <div className="record-table" role="table" aria-label={title}>
              <div className="record-header" role="row">
                <span>Level</span><span>Event / type</span><span>Result</span><span>Competitor</span>
                <span>Competition</span><span>Round</span><span>Entered</span><span>Validation</span><span>Sources</span>
              </div>
              {records.map((record) => (
                <article className="record-row" role="row" key={record.id}>
                  <span className={`level level-${record.record_level.toLowerCase()}`}>{record.record_level}</span>
                  <div><strong>{record.event_name}</strong><small>{record.kind}</small></div>
                  <strong className="result">{record.formatted_result || record.raw_result}</strong>
                  <div><strong>{record.competitor_name}</strong><small>{flag(record.country_code)} {record.competitor_wca_id}</small></div>
                  <div><strong>{record.competition_name}</strong><small>{record.wca_competition_id}</small></div>
                  <div><strong>{record.round_name || "—"}</strong><small>{record.round_id || ""}</small></div>
                  <time className="detected-age" dateTime={record.detected_at} title={absoluteTime(record.detected_at)}>
                    {formatDetectedAge(record.detected_at)}<small>{absoluteTime(record.detected_at)}</small>
                  </time>
                  <span className={`match ${record.validation_status === "verified" ? "yes" : ""}`}>{record.validation_status}</span>
                  <div className="source-stack">
                    {(record.sources || []).map((source) => <small key={source}>{source.replaceAll("_", " ")}</small>)}
                    {record.source_url && <a className="source-link" href={record.source_url} target="_blank" rel="noreferrer">Result ↗</a>}
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : <div className="empty"><h3>No {level} achievements</h3><p>No validated result currently qualifies for this projection.</p></div>
      )}
    </section>
  );
}
