import { useEffect, useState } from "react";
import { getRecords } from "./api/client";
import { AchievementList } from "./components/AchievementList";
import { ComingSoonButton } from "./components/ComingSoonButton";
import { NotificationSettings } from "./components/NotificationSettings";
import { DebugDashboard } from "./components/DebugDashboard";
import MoonIcon from "./assets/icons/Moon_of_May_complex.svg";
import SunIcon from "./assets/icons/Sun_of_May_simplified.svg";
import "./styles.css";

const levels = ["WR", "CR", "NR"];
const REFRESH_INTERVAL_MS = 30_000;

function useAchievementRecords(level, query) {
  const [state, setState] = useState({ records: [], loading: true, error: "" });

  useEffect(() => {
    let current = true;
    const refresh = async (showLoading = false) => {
      if (showLoading) setState((previous) => ({ ...previous, loading: true }));
      try {
        const records = await getRecords({ level, query });
        if (current) setState({ records, loading: false, error: "" });
      } catch (reason) {
        if (current) setState((previous) => ({ ...previous, loading: false, error: reason.message }));
      }
    };
    refresh(true);
    const interval = window.setInterval(() => refresh(false), REFRESH_INTERVAL_MS);
    return () => {
      current = false;
      window.clearInterval(interval);
    };
  }, [level, query]);

  return state;
}

function PublicAchievementSection({ level, query }) {
  const state = useAchievementRecords(level, query);
  return <AchievementList level={level} {...state} />;
}

function Header({ darkMode, home = false, onThemeToggle, wide = false }) {
  return (
    <header className="public-header">
      <div className={`header-inner public-header-inner ${wide ? "homepage-width" : ""}`}>
        <a className="brand public-brand" href="/">Cubing<span>Now</span></a>
        <nav aria-label="Primary navigation" className="public-navigation">
          {['Records', 'Competitions', 'Rankings', 'Statistics', 'Alerts'].map((item) => (
            <ComingSoonButton active={home && item === "Records"} key={item}>{item}</ComingSoonButton>
          ))}
        </nav>
        <div className="header-actions">
          <a className="header-link public-debug-link" href="/debug">Debug</a>
          <button
            aria-label={darkMode ? "Use light mode" : "Use dark mode"}
            aria-pressed={darkMode}
            className="theme-toggle"
            onClick={onThemeToggle}
            title={darkMode ? "Use light mode" : "Use dark mode"}
            type="button"
          >
            <img alt="" aria-hidden="true" src={darkMode ? MoonIcon : SunIcon} />
          </button>
        </div>
      </div>
    </header>
  );
}

function PublicPage({ children, home = false, wide = false }) {
  const [darkMode, setDarkMode] = useState(() => window.localStorage.getItem("cubingnow-theme") === "dark");

  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    window.localStorage.setItem("cubingnow-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  return (
    <>
      <Header
        darkMode={darkMode}
        home={home}
        onThemeToggle={() => setDarkMode((current) => !current)}
        wide={wide}
      />
      {children}
      <footer className={wide ? "homepage-width" : ""}><span>CubingNow</span><span>Unofficial observational companion to WCA Live</span></footer>
    </>
  );
}

function SearchIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="7" />
      <path d="m16 16 5 5" />
    </svg>
  );
}

function CompetingIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 64 64">
      <circle cx="32" cy="18" r="9" />
      <circle cx="13" cy="24" r="6" />
      <circle cx="51" cy="24" r="6" />
      <path d="M18 51v-8c0-9 6-15 14-15s14 6 14 15v8H18ZM3 49v-7c0-7 4-12 10-12 3 0 5 1 7 3M61 49v-7c0-7-4-12-10-12-3 0-5 1-7 3" />
    </svg>
  );
}

function CompetingNow() {
  return (
    <section aria-labelledby="competing-now-title" className="competing-card">
      <div className="competing-card-heading">
        <CompetingIcon />
        <h2 id="competing-now-title">Competing now</h2>
      </div>
      <div className="competing-placeholder">
        <CompetingIcon />
        <h3>Coming soon</h3>
        <p>Live competition data will be available here later.</p>
      </div>
      <div className="record-card-footer">
        <ComingSoonButton className="card-footer-action">See all competing</ComingSoonButton>
      </div>
    </section>
  );
}

function HomePage() {
  const [query, setQuery] = useState("");

  return (
    <PublicPage home wide>
      <main className="homepage-main">
        <div className="homepage-filter">
          <SearchIcon />
          <input
            aria-label="Search records"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter for name, event, competition, country, or continent"
            type="search"
            value={query}
          />
        </div>
        <div className="homepage-dashboard">
          {levels.map((item) => <PublicAchievementSection key={item} level={item} query={query} />)}
          <CompetingNow />
        </div>
      </main>
    </PublicPage>
  );
}

function NotificationSettingsPage() {
  return (
    <PublicPage>
      <main className="notification-settings-main">
        <NotificationSettings />
      </main>
    </PublicPage>
  );
}

export default function App() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/debug") return <DebugDashboard />;
  if (path === "/notificationsettings") return <NotificationSettingsPage />;
  return <HomePage />;
}
