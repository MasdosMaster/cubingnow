import {
  attendanceDateLabel,
  formatAttendanceWindow
} from "../utils/formatAttendanceWindow";
import { CountryFlag } from "./CountryFlag";

const DEFAULT_CONTINENTS = [
  "Africa",
  "Asia",
  "Europe",
  "North America",
  "South America",
  "Oceania"
];

function competitionUrl(competition) {
  if (competition.wca_id) {
    return `https://www.worldcubeassociation.org/competitions/${competition.wca_id}`;
  }
  if (competition.id?.startsWith("cubingchina:")) {
    return `https://cubing.com/competition/${competition.id.slice("cubingchina:".length)}`;
  }
  return "";
}

function SyncSummary({ payload }) {
  if (!payload) return null;
  if (payload.sync_status === "not_yet_synchronised") {
    return <span className="attendance-sync-state pending">Awaiting first sync</span>;
  }
  if (payload.sync_status === "stale") {
    return <span className="attendance-sync-state stale">Registration data may be stale</span>;
  }
  return <span className="attendance-sync-state fresh">Registration lists synced</span>;
}

export function WeekendCompetitorList({
  payload,
  loading,
  error,
  continent,
  onContinentChange
}) {
  const continents = payload?.continents || DEFAULT_CONTINENTS;
  const rows = payload?.results || [];
  const filters = ["", ...continents];

  return (
    <section className="pipeline-section weekend-section" aria-labelledby="weekend-title">
      <div className="pipeline-heading">
        <div>
          <p className="eyebrow">Accepted registrations · {formatAttendanceWindow(payload?.window)}</p>
          <h2 id="weekend-title">Competing this weekend</h2>
        </div>
        <SyncSummary payload={payload} />
      </div>

      {payload?.sync_status === "stale" && (
        <p className="status-warning">The latest successful registration sync is older than expected.</p>
      )}

      <nav className="filters continent-filters" aria-label="Competitor continent">
        {filters.map((item) => (
          <button
            aria-pressed={continent === item}
            className={continent === item ? "active" : ""}
            key={item || "all"}
            onClick={() => onContinentChange(item)}
            type="button"
          >
            {item || "All"}
          </button>
        ))}
      </nav>

      {error && <p className="error">This table could not be updated: {error}</p>}
      {error && !payload ? null : loading ? (
        <p className="loading">Loading registered competitors…</p>
      ) : payload?.sync_status === "not_yet_synchronised" ? (
        <div className="empty">
          <h3>Attendance sync pending</h3>
          <p>The first WCA and CubingChina registration collection has not completed yet.</p>
        </div>
      ) : rows.length ? (
        <div
          aria-label="Scrollable competitor table"
          className="competitor-table-wrap"
          role="region"
          tabIndex={0}
        >
          <div className="competitor-table" role="table" aria-label="Competing this weekend">
            <div className="competitor-header" role="row">
              <span role="columnheader">Rank</span><span role="columnheader">Competitor</span><span role="columnheader">Country / continent</span><span role="columnheader">Competition(s)</span>
            </div>
            {rows.map((competitor) => (
              <article className="competitor-row" role="row" key={competitor.wca_id}>
                <strong className="competitor-rank" role="cell">{competitor.rank}</strong>
                <div role="cell">
                  <a
                    className="competitor-name"
                    href={`https://www.worldcubeassociation.org/persons/${competitor.wca_id}`}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {competitor.name}
                  </a>
                  <small>{competitor.wca_id}</small>
                </div>
                <div role="cell">
                  <strong className="competitor-country"><CountryFlag code={competitor.country_code} /> <span>{competitor.country_code}</span></strong>
                  <small>{competitor.continent}</small>
                </div>
                <ul className="competition-attendance-list" role="cell">
                  {competitor.competitions.map((competition) => {
                    const url = competitionUrl(competition);
                    const content = <><strong>{competition.name}</strong><small>{attendanceDateLabel(competition.start_date)}{competition.end_date !== competition.start_date ? ` – ${attendanceDateLabel(competition.end_date)}` : ""} · {competition.city || competition.country_code}</small></>;
                    return <li key={competition.id}>{url ? <a href={url} rel="noreferrer" target="_blank">{content}</a> : content}</li>;
                  })}
                </ul>
              </article>
            ))}
          </div>
        </div>
      ) : (
        <div className="empty">
          <h3>No registered competitors found</h3>
          <p>No returning competitors match this continent and attendance window.</p>
        </div>
      )}
    </section>
  );
}
