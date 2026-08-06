// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";


afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});


describe("homepage", () => {
  it("renders the weekend table underneath all three record tables", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      if (url.includes("ingestion-status")) {
        return { ok: true, json: async () => ({}) };
      }
      if (url.includes("competing-this-weekend")) {
        return {
          ok: true,
          json: async () => ({
            window: { start_date: "2026-08-05", end_date: "2026-08-11" },
            sync_status: "fresh",
            continents: ["Africa", "Asia", "Europe", "North America", "South America", "Oceania"],
            results: []
          })
        };
      }
      return { ok: true, json: async () => ({ results: [] }) };
    }));

    render(<App />);
    await screen.findByRole("heading", { name: "Competing this weekend" });

    await waitFor(() => {
      const headings = Array.from(document.querySelectorAll("main h2")).map((node) => node.textContent);
      expect(headings).toEqual([
        "Record alerts",
        "Recent records — GraphQL subscriptions",
        "Recent records — API polling",
        "Recent records — CubingChina live",
        "Competing this weekend"
      ]);
    });
  });

  it("uses the newly backend-ranked payload when a continent filter changes", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      if (url.includes("ingestion-status")) {
        return { ok: true, json: async () => ({}) };
      }
      if (url.includes("competing-this-weekend")) {
        const europe = url.includes("continent=Europe");
        return {
          ok: true,
          json: async () => ({
            window: { start_date: "2026-08-05", end_date: "2026-08-11" },
            sync_status: "fresh",
            continents: ["Africa", "Asia", "Europe", "North America", "South America", "Oceania"],
            results: [{
              rank: europe ? 1 : 7,
              wca_id: europe ? "2020EURO01" : "2020WORLD01",
              name: europe ? "Europe Result" : "All Result",
              country_code: europe ? "NL" : "US",
              continent: europe ? "Europe" : "North America",
              competitions: []
            }]
          })
        };
      }
      return { ok: true, json: async () => ({ results: [] }) };
    }));

    render(<App />);
    await screen.findByText("All Result");
    fireEvent.click(screen.getByRole("button", { name: "Europe" }));

    expect(await screen.findByText("Europe Result")).toBeTruthy();
    expect(screen.queryByText("All Result")).toBeNull();
    expect(screen.getByRole("button", { name: "Europe" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("1")).toBeTruthy();
  });
});
