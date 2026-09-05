import { formatCompactDetectedAge } from "../utils/formatDetectedAge";

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
                    <strong className="compact-result" role="cell">{record.result.formatted || record.result.raw}</strong>
                    <span className="result-kind" role="cell">{resultKind(record.result.kind)}</span>
                    <span className="record-competitor" role="cell">
                      <span aria-hidden="true" className="country-flag">{flag(record.competitor.country_code)}</span>
                      <span>{record.competitor.name}</span>
                    </span>
                    <time className="compact-detected-age" dateTime={timestamp} role="cell" title={absoluteTime(timestamp)}>
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
