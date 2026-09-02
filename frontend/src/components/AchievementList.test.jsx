// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { AchievementList } from "./AchievementList";

afterEach(cleanup);

const baseRecord = {
  id: 1,
  achievement: { level: "WR" },
  event: { id: "333", name: "3x3x3 Cube" },
  result: { kind: "single", formatted: "3.90", raw: 390 },
  competitor: {
    name: "Test Cuber",
    country_code: "NL",
    continent: "Europe",
    wca_id: "2020TEST01",
  },
  competition: { name: "Test Open 2026", wca_id: "TestOpen2026" },
  round: { id: "round-1", number: 2, name: "Final" },
  timestamps: {
    entered_at: "2026-08-08T12:00:00Z",
    first_observed_at: "2026-08-08T12:00:01Z",
  },
  validation: { status: "verified" },
  sources: {
    pipelines: ["api_polling", "graphql_subscription"],
    url: "https://example.test/live",
  },
};

it("renders canonical validation and all contributing sources", () => {
  render(
    <AchievementList
      level="WR"
      loading={false}
      error=""
      records={[baseRecord]}
    />
  );

  expect(screen.getByText("verified")).toBeTruthy();
  expect(screen.getByText("api polling")).toBeTruthy();
  expect(screen.getByText("graphql subscription")).toBeTruthy();
  expect(screen.getByText("single")).toBeTruthy();
});

it.each([
  ["North America", "NAR"],
  ["South America", "SAR"],
  ["Europe", "ER"],
  ["Asia", "AsR"],
  ["Africa", "AfR"],
  ["Oceania", "OcR"],
])("renders the %s continental record label as %s", (continent, label) => {
  render(
    <AchievementList
      level="CR"
      loading={false}
      error=""
      records={[{
        ...baseRecord,
        achievement: { level: "CR" },
        competitor: { ...baseRecord.competitor, continent },
      }]}
    />
  );

  expect(screen.getByText(label)).toBeTruthy();
  expect(screen.queryByText("CR")).toBeNull();
});
