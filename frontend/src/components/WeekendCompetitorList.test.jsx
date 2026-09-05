// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { formatAttendanceWindow } from "../utils/formatAttendanceWindow";
import { WeekendCompetitorList } from "./WeekendCompetitorList";


const payload = {
  window: { start_date: "2026-08-05", end_date: "2026-08-11" },
  sync_status: "fresh",
  continents: ["Africa", "Asia", "Europe", "North America", "South America", "Oceania"],
  results: [
    {
      rank: 1,
      wca_id: "2020ALPH01",
      name: "Alice Alpha",
      country_code: "NL",
      continent: "Europe",
      competitions: [
        { id: "wca:One2026", wca_id: "One2026", name: "One Open", country_code: "NL", city: "Utrecht", start_date: "2026-08-08", end_date: "2026-08-08" },
        { id: "wca:Two2026", wca_id: "Two2026", name: "Two Open", country_code: "BE", city: "Ghent", start_date: "2026-08-09", end_date: "2026-08-10" }
      ]
    }
  ]
};

afterEach(cleanup);


describe("WeekendCompetitorList", () => {
  it("shows the date range, ranking, multiple competitions, and accessible filters", () => {
    const onContinentChange = vi.fn();
    render(
      <WeekendCompetitorList
        payload={payload}
        loading={false}
        error=""
        continent=""
        onContinentChange={onContinentChange}
      />
    );

    expect(screen.getByRole("heading", { name: "Competing this weekend" })).toBeTruthy();
    expect(screen.getByText("Accepted registrations · 5 Aug – 11 Aug 2026")).toBeTruthy();
    expect(screen.getByText("Alice Alpha")).toBeTruthy();
    expect(document.querySelector(".country-flag.fi.fi-nl.country-flag-rounded")).toBeTruthy();
    expect(screen.queryByText("🇳🇱")).toBeNull();
    expect(screen.getByText("One Open")).toBeTruthy();
    expect(screen.getByText("Two Open")).toBeTruthy();
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Europe" }));
    expect(onContinentChange).toHaveBeenCalledWith("Europe");
  });

  it.each([
    [{ loading: true, error: "", payload: null }, "Loading registered competitors…"],
    [{ loading: false, error: "Network down", payload: null }, "This table could not be updated: Network down"],
    [{ loading: false, error: "", payload: { ...payload, results: [] } }, "No registered competitors found"],
    [{ loading: false, error: "", payload: { ...payload, sync_status: "stale" } }, "Registration data may be stale"],
    [{ loading: false, error: "", payload: { ...payload, sync_status: "not_yet_synchronised", results: [] } }, "Attendance sync pending"]
  ])("renders the requested state", (state, expected) => {
    render(
      <WeekendCompetitorList
        {...state}
        continent=""
        onContinentChange={() => {}}
      />
    );
    expect(screen.getByText(expected)).toBeTruthy();
  });
});


it("formats a cross-year window without losing either year", () => {
  expect(formatAttendanceWindow({ start_date: "2026-12-30", end_date: "2027-01-05" })).toBe(
    "30 Dec 2026 – 5 Jan 2027"
  );
});
