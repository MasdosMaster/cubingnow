// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { AchievementList } from "./AchievementList";

afterEach(cleanup);

const baseRecord = {
  id: 1,
  achievement: { level: "WR", holding: { shared_tie: false } },
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
  expect(row.querySelector(".country-flag.fi.fi-nl.country-flag-rounded")).toBeTruthy();
  expect(within(row).queryByText("🇳🇱")).toBeNull();
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

  expect(document.querySelector(".country-flag.fi.fi-nl.country-flag-rounded")).toBeTruthy();
  expect(screen.queryByText("🇳🇱")).toBeNull();
  expect(screen.getByText("CR")).toBeTruthy();
  expect(screen.queryByText("ER")).toBeNull();
});

it("keeps Nepal's non-rectangular flag unrounded like CubingStats", () => {
  render(
    <AchievementList
      level="NR"
      loading={false}
      error=""
      records={[{ ...baseRecord, competitor: { ...baseRecord.competitor, country_code: "NP" } }]}
    />
  );

  const flag = document.querySelector(".country-flag.fi.fi-np");
  expect(flag).toBeTruthy();
  expect(flag.classList.contains("country-flag-rounded")).toBe(false);
});

it("renders Multi-Blind scores and times on separate lines without changing the value", () => {
  render(
    <AchievementList
      level="WR"
      loading={false}
      error=""
      records={[{
        ...baseRecord,
        event: { id: "333mbf", name: "3x3x3 Multi-Blind" },
        result: { ...baseRecord.result, formatted: "13/13 58:03" },
        achievement: { ...baseRecord.achievement, holding: { shared_tie: true } },
      }]}
    />
  );

  const result = document.querySelector(".compact-result-multiblind");
  expect(result).toBeTruthy();
  expect(result.children).toHaveLength(2);
  expect(result.children[0].textContent).toBe("13/13");
  expect(result.children[1].textContent).toBe("58:03");
  expect(result.textContent).toBe("13/1358:03");
  expect(screen.queryByText("Sgl")).toBeNull();
  expect(screen.queryByText("Tied")).toBeNull();
});

it("shows Tied in the result metadata for non-tied holdings during the preview", () => {
  render(
    <AchievementList
      level="WR"
      loading={false}
      error=""
      records={[
        { ...baseRecord, achievement: { ...baseRecord.achievement, holding: { shared_tie: false } } },
        { ...baseRecord, id: 2, result: { ...baseRecord.result, kind: "average" }, achievement: { ...baseRecord.achievement, holding: { shared_tie: true } } },
      ]}
    />
  );

  const rows = screen.getAllByRole("row");
  expect(within(rows[0]).getByText("Sgl")).toBeTruthy();
  expect(within(rows[0]).getByText("Tied").classList.contains("tied-indicator")).toBe(true);
  expect(within(rows[0]).getByText("Tied").closest(".compact-result-meta")).toBeTruthy();
  expect(within(rows[1]).getByText("Avg")).toBeTruthy();
  expect(within(rows[1]).queryByText("Tied")).toBeNull();
});
