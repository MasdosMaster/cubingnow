import { afterEach, describe, expect, it, vi } from "vitest";

import { getRecords } from "./client";

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
});
