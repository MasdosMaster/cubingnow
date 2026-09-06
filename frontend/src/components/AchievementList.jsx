import { formatCompactDetectedAge, isRecentlyDetected } from "../utils/formatDetectedAge";
import { CountryFlag } from "./CountryFlag";

function absoluteTime(value) {
  return value ? new Date(value).toLocaleString() : "Unknown";
}

const LABELS = {
  WR: "World Records",
  CR: "Continental Records",
  NR: "National Records",
};

const ROW_LIMITS = {
  WR: 6,
  CR: 6,
  NR: 13,
};

function resultKind(kind = "") {
  return { single: "Sgl", average: "Avg" }[kind] || kind || "—";
}

function CompactResult({ eventId, value }) {
  const displayValue = String(value ?? "—");

  if (eventId !== "333mbf") {
    return <strong className="compact-result" role="cell">{displayValue}</strong>;
  }

  const separator = displayValue.indexOf(" ");
  const score = separator === -1 ? displayValue : displayValue.slice(0, separator);
  const time = separator === -1 ? "" : displayValue.slice(separator + 1);

  return (
    <strong className="compact-result compact-result-multiblind" role="cell">
      <span>{score}</span>
      {time && <span>{time}</span>}
    </strong>
  );
}

function BellIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
    </svg>
  );
}

export function AchievementList({ level, records, loading, error }) {
  const title = LABELS[level] || `${level} results`;
  const visibleRecords = records.slice(0, ROW_LIMITS[level] || records.length);

  return (
    <section className={`record-card record-card-${level.toLowerCase()}`} aria-labelledby={`${level}-records-title`}>
      <div className="record-card-heading">
        <span className="record-level-badge">{level}</span>
        <h2 id={`${level}-records-title`}>{title}</h2>
        <a className="notification-link" href="/notificationsettings">
          <BellIcon />
          <span>Get notified</span>
        </a>
      </div>
      <div className="record-card-content">
        {error && <p className="record-card-error">This table could not be updated: {error}</p>}
        {loading ? <p className="record-card-state">Loading {level} results…</p> : (
          visibleRecords.length ? (
            <div className="compact-record-table" role="table" aria-label={title}>
              {visibleRecords.map((record) => {
                const timestamp = record.timestamps.entered_at || record.timestamps.first_observed_at;
                return (
                  <article className="compact-record-row" role="row" key={record.id}>
                    <span className="event-icon" role="cell">
                      <img alt={`${record.event.name} icon`} src={`/event_icons/${record.event.id}.svg`} />
                    </span>
                    <CompactResult eventId={record.event.id} value={record.result.formatted || record.result.raw} />
                    <span className="result-kind" role="cell">
                      <span className="result-kind-label">{resultKind(record.result.kind)}</span>
                      {record.achievement.holding?.shared_tie === false && <span className="tied-indicator">Tied</span>}
                    </span>
                    <span className="record-competitor" role="cell">
                      <CountryFlag code={record.competitor.country_code} />
                      <span className="competitor-name">{record.competitor.name}</span>
                    </span>
                    <time className={`compact-detected-age${isRecentlyDetected(timestamp) ? " is-recent" : ""}`} dateTime={timestamp} role="cell" title={absoluteTime(timestamp)}>
                      {formatCompactDetectedAge(timestamp)}
                    </time>
                  </article>
                );
              })}
            </div>
          ) : <div className="record-card-state"><h3>No {level} achievements</h3><p>No validated result currently qualifies for this projection.</p></div>
        )}
      </div>
      <div className="record-card-footer">
        <span className="coming-soon card-footer-action">
          <button aria-describedby={`${level}-coming-soon-tooltip`} type="button">See all recent</button>
          <span className="coming-soon-tooltip" id={`${level}-coming-soon-tooltip`} role="tooltip">Coming soon</span>
        </span>
      </div>
    </section>
  );
}
