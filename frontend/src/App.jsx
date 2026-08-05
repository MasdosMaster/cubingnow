import { useEffect, useState } from "react";
import { getIngestionStatus, getRecentRecords } from "./api/client";
import { RecordList } from "./components/RecordList";
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

export default function App() {
  const [level, setLevel] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState("");
  const api = usePipelineRecords("api_polling", level, query);
  const subscriptions = usePipelineRecords("graphql_subscription", level, query);

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
        <a className="brand" href="/"><span className="brand-mark"><i /><i /><i /><i /></span>CubingNow</a>
        <span className="api-label">WCA Live verification experiment</span>
      </header>
      <main>
        <section className="heading">
          <div><p className="eyebrow">Independent observations</p><h1>Recent WCA records</h1></div>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search records" />
        </section>
        <nav className="filters" aria-label="Record level">
          {levels.map((item) => <button className={level === item ? "active" : ""} key={item || "all"} onClick={() => setLevel(item)}>{item || "All"}</button>)}
        </nav>
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
      </main>
      <footer><span>CubingNow</span><span>Unofficial observational companion to WCA Live</span></footer>
    </>
  );
}
