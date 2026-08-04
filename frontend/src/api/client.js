const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export async function getRecords({ level = "", query = "" } = {}) {
  const params = new URLSearchParams();
  if (level) params.set("level", level);
  if (query) params.set("q", query);
  const response = await fetch(`${API_BASE_URL}/records/?${params}`);
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  const payload = await response.json();
  return payload.results || payload;
}

