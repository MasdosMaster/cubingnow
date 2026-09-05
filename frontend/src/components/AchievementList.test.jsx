// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
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

it("renders only the requested compact row fields and the real event icon", () => {
  render(<AchievementList level="WR" loading={false} error="" records={[
    baseRecord,
    { ...baseRecord, id: 2, result: { ...baseRecord.result, kind: "average" } },
  ]} />);

  const row = screen.getAllByRole("row")[0];
  expect(within(row).getByRole("img", { name: "3x3x3 Cube icon" }).getAttribute("src")).toBe("/event_icons/333.svg");
  expect(within(row).getByText("3.90")).toBeTruthy();
  expect(within(row).getByText("Sgl")).toBeTruthy();
  expect(screen.getByText("Avg")).toBeTruthy();
  expect(screen.queryByText("Single")).toBeNull();
  expect(screen.queryByText("Average")).toBeNull();
  expect(within(row).getByText("Test Cuber")).toBeTruthy();
  expect(within(row).getByText("🇳🇱")).toBeTruthy();
  expect(screen.queryByText("verified")).toBeNull();
  expect(screen.queryByText("api polling")).toBeNull();
  expect(screen.queryByText("Test Open 2026")).toBeNull();
  expect(screen.queryByText("2020TEST01")).toBeNull();
  expect(screen.getByRole("link", { name: "Get notified" }).getAttribute("href")).toBe("/notificationsettings");
});

it("uses the competitor country flag for continental records", () => {
  render(
    <AchievementList
      level="CR"
      loading={false}
      error=""
      records={[{ ...baseRecord, achievement: { level: "CR" } }]}
    />
  );

  expect(screen.getByText("🇳🇱")).toBeTruthy();
  expect(screen.getByText("CR")).toBeTruthy();
  expect(screen.queryByText("ER")).toBeNull();
});
