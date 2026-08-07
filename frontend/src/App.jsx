import { useEffect, useState } from "react";
import { getIngestionStatus, getRecentRecords, getWeekendCompetitors } from "./api/client";
import { RecordList } from "./components/RecordList";
import { NotificationSettings } from "./components/NotificationSettings";
import { WeekendCompetitorList } from "./components/WeekendCompetitorList";
import "./styles.css";

const levels = ["", "WR", "CR", "NR"];
const REFRESH_INTERVAL_MS = 30_000;

function usePipelineRecords(source, level, query) {
  const [state, setState] = useState({ records: [], loading: true, error: "" });

  useEffect(() => {
    let current = true;
    const refresh = async (showLoading = false) => {
      if (showLoading) setState((previous) => ({ ...previous, loading: true }));
      try {
        const records = await getRecentRecords({ source, level, query });
        if (current) setState({ records, loading: false, error: "" });
      } catch (reason) {
        if (current) {
          setState((previous) => ({ ...previous, loading: false, error: reason.message }));
        }
      }
    };
    refresh(true);
    const interval = window.setInterval(() => refresh(false), REFRESH_INTERVAL_MS);
    return () => {
      current = false;
      window.clearInterval(interval);
    };
  }, [source, level, query]);

  return state;
}

function useWeekendCompetitors(continent) {
  const [state, setState] = useState({ payload: null, loading: true, error: "" });

  useEffect(() => {
    let current = true;
    const refresh = async (showLoading = false) => {
      if (showLoading) setState((previous) => ({ ...previous, loading: true }));
      try {
        const payload = await getWeekendCompetitors({ continent });
        if (current) setState({ payload, loading: false, error: "" });
      } catch (reason) {
        if (current) {
          setState((previous) => ({ ...previous, loading: false, error: reason.message }));
        }
      }
    };
    refresh(true);
    const interval = window.setInterval(() => refresh(false), REFRESH_INTERVAL_MS);
    return () => {
      current = false;
      window.clearInterval(interval);
    };
  }, [continent]);

  return state;
}

export default function App() {
  const [darkMode, setDarkMode] = useState(() => window.localStorage.getItem("cubingnow-theme") === "dark");
  const [level, setLevel] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState("");
  const [continent, setContinent] = useState("");
  const api = usePipelineRecords("api_polling", level, query);
  const subscriptions = usePipelineRecords("graphql_subscription", level, query);
  const cubingChina = usePipelineRecords("cubingchina_websocket", level, query);
  const weekendCompetitors = useWeekendCompetitors(continent);

  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    window.localStorage.setItem("cubingnow-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  useEffect(() => {
    let current = true;
    const refresh = async () => {
      try {
        const payload = await getIngestionStatus();
        if (current) {
          setStatus(payload);
          setStatusError("");
        }
      } catch (reason) {
        if (current) setStatusError(reason.message);
      }
    };
    refresh();
    const interval = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => {
      current = false;
      window.clearInterval(interval);
    };
  }, []);

  return (
    <>
      <header>
        <div className="header-inner">
          <a className="brand" href="/">CubingNow</a>
          <div className="header-actions">
            <span className="api-label">WCA Live verification experiment</span>
            <button
              aria-label={darkMode ? "Use light mode" : "Use dark mode"}
              aria-pressed={darkMode}
              className="theme-toggle"
              onClick={() => setDarkMode((current) => !current)}
              title={darkMode ? "Use light mode" : "Use dark mode"}
              type="button"
            >
              <span aria-hidden="true">{darkMode ? "☾" : "☀"}</span>
            </button>
          </div>
        </div>
      </header>
      <main>
        <section className="heading">
          <div><p className="eyebrow">Independent observations</p><h1>Recent WCA records</h1></div>
          <input aria-label="Search records" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search records" />
        </section>
        <nav className="filters" aria-label="Record level">
          {levels.map((item) => <button className={level === item ? "active" : ""} key={item || "all"} onClick={() => setLevel(item)}>{item || "All"}</button>)}
        </nav>
        <NotificationSettings />
        {statusError && <p className="status-warning">Worker health unavailable: {statusError}</p>}
        <RecordList
          title="Recent records — GraphQL subscriptions"
          subtitle="Persisted full-round snapshot diffs"
          {...subscriptions}
          worker={status?.graphql_subscription}
          roundStatus={status?.subscription_rounds}
        />
        <RecordList
          title="Recent records — API polling"
          subtitle="WCA Live recentRecords query"
          {...api}
          worker={status?.api_polling}
        />
        <RecordList
          title="Recent records — CubingChina live"
          subtitle="CubingChina competition WebSocket"
          {...cubingChina}
          worker={status?.cubingchina_websocket}
        />
        <WeekendCompetitorList
          {...weekendCompetitors}
          continent={continent}
          onContinentChange={setContinent}
        />
      </main>
      <footer><span>CubingNow</span><span>Unofficial observational companion to WCA Live</span></footer>
    </>
  );
}
