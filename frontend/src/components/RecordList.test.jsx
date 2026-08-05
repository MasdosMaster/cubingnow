// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { formatDetectedAge } from "../utils/formatDetectedAge";
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
});
