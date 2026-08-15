// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { AchievementList } from "./AchievementList";

afterEach(cleanup);

it("renders canonical validation and all contributing sources", () => {
  render(
    <AchievementList
      level="WR"
      loading={false}
      error=""
      records={[{
        id: 1,
        record_level: "WR",
        event_name: "3x3x3 Cube",
        kind: "single",
        formatted_result: "3.90",
        raw_result: 390,
        competitor_name: "Test Cuber",
        country_code: "NL",
        competitor_wca_id: "2020TEST01",
        competition_name: "Test Open 2026",
        wca_competition_id: "TestOpen2026",
        round_name: "Final",
        round_id: "round-1",
        detected_at: "2026-08-08T12:00:00Z",
        validation_status: "verified",
        sources: ["api_polling", "graphql_subscription"],
        source_url: "https://example.test/live",
      }]}
    />
  );

  expect(screen.getByText("verified")).toBeTruthy();
  expect(screen.getByText("api polling")).toBeTruthy();
  expect(screen.getByText("graphql subscription")).toBeTruthy();
  expect(screen.getByText("single")).toBeTruthy();
});
