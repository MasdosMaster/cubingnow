import { useEffect, useState } from "react";
import { getRecords } from "./api/client";
import { RecordList } from "./components/RecordList";
import "./styles.css";

const levels = ["", "WR", "CR", "NR"];

export default function App() {
  const [records, setRecords] = useState([]);
  const [level, setLevel] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let current = true;
    setLoading(true);
    getRecords({ level, query })
      .then((items) => { if (current) { setRecords(items); setError(""); } })
      .catch((reason) => { if (current) setError(reason.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [level, query]);

  return (
    <>
      <header>
        <a className="brand" href="/"><span className="brand-mark"><i /><i /><i /><i /></span>CubingNow</a>
        <span className="api-label">Django API + React</span>
      </header>
      <main>
        <section className="heading">
          <div><p className="eyebrow">Official results</p><h1>Newly detected records</h1></div>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search records" />
        </section>
        <nav className="filters" aria-label="Record level">
          {levels.map((item) => <button className={level === item ? "active" : ""} key={item || "all"} onClick={() => setLevel(item)}>{item || "All"}</button>)}
        </nav>
        {error && <p className="error">Could not reach the CubeRecord API: {error}</p>}
        {loading ? <p className="loading">Loading records…</p> : <RecordList records={records} />}
      </main>
      <footer><span>CubingNow</span><span>Unofficial companion to WCA Live</span></footer>
    </>
  );
}
