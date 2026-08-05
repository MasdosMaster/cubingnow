import { afterEach, describe, expect, it, vi } from "vitest";

import { getRecentRecords, getRecords, getWeekendCompetitors } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("getRecords", () => {
  it("returns the paginated API results", async () => {
    const records = [{ id: 1, level: "WR" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ count: 1, results: records })
    }));

    await expect(getRecords({ level: "WR" })).resolves.toEqual(records);
    expect(fetch).toHaveBeenCalledWith("/api/records/?level=WR");
  });

  it("fetches one ingestion pipeline explicitly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ count: 0, results: [] })
    }));

    await expect(getRecentRecords({
      source: "graphql_subscription",
      level: "WR"
    })).resolves.toEqual([]);
    expect(fetch).toHaveBeenCalledWith(
      "/api/recent-records/?source=graphql_subscription&level=WR"
    );
  });

  it("requests the backend-ranked continent result", async () => {
    const payload = { count: 1, results: [{ rank: 1, name: "Alice" }] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload
    }));

    await expect(getWeekendCompetitors({ continent: "North America" })).resolves.toEqual(payload);
    expect(fetch).toHaveBeenCalledWith(
      "/api/competing-this-weekend/?continent=North+America"
    );
  });
});
