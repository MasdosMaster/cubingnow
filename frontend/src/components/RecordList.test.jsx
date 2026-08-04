import { describe, expect, it } from "vitest";

import { formatDetectedAge } from "../utils/formatDetectedAge";

const now = new Date("2026-08-05T12:00:00Z");

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
