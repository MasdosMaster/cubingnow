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

const CONTINENTAL_RECORD_LABELS = {
  Africa: "AfR",
  Asia: "AsR",
  Europe: "ER",
  "North America": "NAR",
  "South America": "SAR",
  Oceania: "OcR",
};

function recordLevelLabel(record) {
  if (record.achievement.level !== "CR") return record.achievement.level;
  return CONTINENTAL_RECORD_LABELS[record.competitor.continent] || record.achievement.level;
}

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
                  <span className={`level level-${record.achievement.level.toLowerCase()}`}>{recordLevelLabel(record)}</span>
                  <div><strong>{record.event.name}</strong><small>{record.result.kind}</small></div>
                  <strong className="result">{record.result.formatted || record.result.raw}</strong>
                  <div><strong>{record.competitor.name}</strong><small>{flag(record.competitor.country_code)} {record.competitor.wca_id}</small></div>
                  <div><strong>{record.competition.name}</strong><small>{record.competition.wca_id}</small></div>
                  <div><strong>{record.round.name || "—"}</strong><small>{record.round.id || ""}</small></div>
                  <time className="detected-age" dateTime={record.timestamps.entered_at || record.timestamps.first_observed_at} title={absoluteTime(record.timestamps.entered_at || record.timestamps.first_observed_at)}>
                    {formatDetectedAge(record.timestamps.entered_at || record.timestamps.first_observed_at)}<small>{absoluteTime(record.timestamps.entered_at || record.timestamps.first_observed_at)}</small>
                  </time>
                  <span className={`match ${record.validation.status === "verified" ? "yes" : ""}`}>{record.validation.status}</span>
                  <div className="source-stack">
                    {record.sources.pipelines.map((source) => <small key={source}>{source.replaceAll("_", " ")}</small>)}
                    {record.sources.url && <a className="source-link" href={record.sources.url} target="_blank" rel="noreferrer">Result ↗</a>}
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
