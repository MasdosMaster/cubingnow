// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function record(id, level, name = `Cuber ${id}`) {
  return {
    id: `${level}-${id}`,
    achievement: { level },
    event: { id: "333", name: "3x3x3 Cube" },
    result: { kind: id % 2 ? "single" : "average", formatted: `${id}.00`, raw: id * 100 },
    competitor: { name, romanized_name: name, native_name: null, country_code: "NL", continent: "Europe", wca_id: `2026TEST${id}` },
    competition: { name: "Test Open", wca_id: "TestOpen2026" },
    round: { id: "round-1", name: "Final" },
    timestamps: { entered_at: "2026-09-05T12:00:00Z", first_observed_at: "2026-09-05T12:00:01Z" },
    validation: { status: "verified" },
    sources: { pipelines: ["api_polling"], url: "https://example.test/live" },
  };
}

function recordsFetch() {
  return vi.fn(async (url) => {
    const request = new URL(url, "https://cubingnow.test");
    const level = request.searchParams.get("level");
    const counts = { WR: 8, CR: 8, NR: 15 };
    return {
      ok: true,
      json: async () => ({
        results: Array.from({ length: counts[level] || 0 }, (_, index) => (
          record(index + 1, level, request.searchParams.get("q") ? "Filtered Cuber" : undefined)
        )),
      }),
    };
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("homepage", () => {
  it("renders only the limited WR, CR, and NR cards without requesting PR or weekend competitors", async () => {
    const fetchMock = recordsFetch();
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(screen.getByRole("heading", { name: "Live speedcubing, all in one place." })).toBeTruthy();
    expect(screen.getByText("CubingNow combines live competition data from the WCA, WCA Live, and CubingChina with official WCA data to bring you live results, records, rankings, and statistics for you and other cubers.")).toBeTruthy();
    expect(screen.getByLabelText("Beta notice").textContent).toBe("CubingNow is currently in beta. New features and data sources are being added regularly.");

    const world = await screen.findByRole("table", { name: "World Records" });
    const continental = await screen.findByRole("table", { name: "Continental Records" });
    const national = await screen.findByRole("table", { name: "National Records" });

    expect(within(world).getAllByRole("row")).toHaveLength(6);
    expect(within(continental).getAllByRole("row")).toHaveLength(6);
    expect(within(national).getAllByRole("row")).toHaveLength(13);
    expect(screen.queryByText("Personal Records")).toBeNull();
    expect(screen.getByRole("heading", { name: "Competing now" })).toBeTruthy();
    expect(screen.queryByText("Competing this weekend")).toBeNull();

    const requestedUrls = fetchMock.mock.calls.map(([url]) => url);
    expect(requestedUrls).toHaveLength(3);
    expect(requestedUrls.some((url) => url.includes("level=PR"))).toBe(false);
    expect(requestedUrls.some((url) => url.includes("competing-this-weekend"))).toBe(false);
  });

  it("keeps filtering, navigation placeholders, notification links, debug access, and theme behavior", async () => {
    const fetchMock = recordsFetch();
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("table", { name: "World Records" });

    const filter = screen.getByPlaceholderText("Filter for name, event, competition, country, or continent");
    fireEvent.change(filter, { target: { value: "Max Park" } });
    await waitFor(() => {
      const filteredRequests = fetchMock.mock.calls.filter(([url]) => url.includes("q=Max+Park"));
      expect(filteredRequests).toHaveLength(3);
    });
    expect(await screen.findAllByText("Filtered Cuber")).toHaveLength(25);

    const unfinished = screen.getByRole("button", { name: "Competitions" });
    fireEvent.click(unfinished);
    expect(window.location.pathname).toBe("/");
    expect(document.getElementById(unfinished.getAttribute("aria-describedby")).textContent).toBe("Coming soon");

    const notificationLinks = screen.getAllByRole("link", { name: "Get notified" });
    expect(notificationLinks).toHaveLength(3);
    notificationLinks.forEach((link) => expect(link.getAttribute("href")).toBe("/notificationsettings"));
    expect(screen.getByRole("link", { name: "Debug" }).getAttribute("href")).toBe("/debug");

    const themeButton = screen.getByRole("button", { name: "Use dark mode" });
    fireEvent.click(themeButton);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("cubingnow-theme")).toBe("dark");
  });

  it("keeps the competing placeholder static and free of competitor data", async () => {
    vi.stubGlobal("fetch", recordsFetch());
    render(<App />);

    await screen.findByRole("heading", { name: "Competing now" });
    expect(screen.getByText("Live competition data will be available here later.")).toBeTruthy();
    expect(screen.queryByText("All Result")).toBeNull();
    expect(screen.getByRole("button", { name: "See all competing" })).toBeTruthy();
  });
});

describe("public routes", () => {
  it("renders the existing notification settings at /notificationsettings", async () => {
    window.history.replaceState({}, "", "/notificationsettings");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(screen.getByRole("heading", { name: "Record alerts" })).toBeTruthy();
    expect(await screen.findByText("This browser does not currently expose Web Push.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Debug" }).getAttribute("href")).toBe("/debug");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
