const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

async function getJson(path) {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.json();
}

export async function getRecords({ level = "", query = "" } = {}) {
  const params = new URLSearchParams();
  if (level) params.set("level", level);
  if (query) params.set("q", query);
  const payload = await getJson(`/records/?${params}`);
  return payload.results || payload;
}

export async function getRecentRecords({ source, level = "", query = "" }) {
  const params = new URLSearchParams({ source });
  if (level) params.set("level", level);
  if (query) params.set("q", query);
  const payload = await getJson(`/recent-records/?${params}`);
  return payload.results || payload;
}

export async function getIngestionStatus() {
  return getJson("/ingestion-status/");
}

export async function getWeekendCompetitors({ continent = "" } = {}) {
  const params = new URLSearchParams();
  if (continent) params.set("continent", continent);
  const query = params.toString();
  return getJson(`/competing-this-weekend/${query ? `?${query}` : ""}`);
}
