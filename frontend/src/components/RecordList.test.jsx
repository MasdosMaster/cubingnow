// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { formatCompactDetectedAge, formatDetectedAge, isRecentlyDetected } from "../utils/formatDetectedAge";
import { RecordList } from "./RecordList";

const now = new Date("2026-08-05T12:00:00Z");

afterEach(cleanup);

describe("formatDetectedAge", () => {
  it.each([
    ["2026-08-05T11:43:00Z", "17m ago"],
    ["2026-08-05T10:16:00Z", "1h44m ago"],
    ["2026-08-05T06:00:00Z", "6h ago"],
    ["2026-08-04T08:00:00Z", "1d4h ago"],
  ])("formats %s as %s", (detectedAt, expected) => {
    expect(formatDetectedAge(detectedAt, now)).toBe(expected);
  });
});

describe("formatCompactDetectedAge", () => {
  it.each([
    ["2026-08-05T11:59:01Z", "59s ago"],
    ["2026-08-05T11:59:00Z", "1m ago"],
    ["2026-08-05T11:00:01Z", "59m ago"],
    ["2026-08-05T11:00:00Z", "1h ago"],
    ["2026-08-04T12:01:00Z", "23h ago"],
    ["2026-08-04T12:00:00Z", "1d ago"],
    ["2026-07-29T13:00:00Z", "6d ago"],
    ["2026-07-29T12:00:00Z", "1w ago"],
    ["2026-07-23T12:00:00Z", "1w ago"],
    ["2026-07-22T12:00:00Z", "2w ago"],
  ])("formats %s as %s", (detectedAt, expected) => {
    expect(formatCompactDetectedAge(detectedAt, now)).toBe(expected);
  });
});

describe("isRecentlyDetected", () => {
  it("treats timestamps less than 96 hours old as recent", () => {
    expect(isRecentlyDetected("2026-08-01T12:00:01Z", now)).toBe(true);
    expect(isRecentlyDetected("2026-08-01T12:00:00Z", now)).toBe(false);
  });
});

it("renders a source-aware CubingChina link", () => {
  render(
    <RecordList
      title="Recent records — CubingChina live"
      subtitle="CubingChina competition WebSocket"
      loading={false}
      error=""
      records={[{
        id: 1,
        status: "active",
        record_level: "WR",
        event_name: "3x3x3 Cube",
        kind: "single",
        formatted_result: "3.26",
        raw_result: 326,
        competitor_name: "Test Cuber",
        country_code: "NL",
        competitor_wca_id: "2020TEST01",
        competition_name: "China Open 2026",
        wca_competition_id: "ChinaOpen2026",
        round_name: "First round",
        round_id: "1",
        detected_at: "2026-08-05T12:00:00Z",
        matched_in_other_pipeline: false,
        source_url: "https://cubing.com/live/China-Open-2026#!/event/333/1/all",
        ingestion_method: "cubingchina_websocket",
      }]}
    />
  );
  const link = screen.getByRole("link", { name: "CubingChina ↗" });
  expect(link.getAttribute("href")).toContain("cubing.com/live/China-Open-2026");
  expect(document.querySelector(".country-flag.fi.fi-nl.country-flag-rounded")).toBeTruthy();
  expect(screen.queryByText("🇳🇱")).toBeNull();
});
