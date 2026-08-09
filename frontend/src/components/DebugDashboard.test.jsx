// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";

const payload = {
  generated_at: new Date().toISOString(),
  response_generation_ms: 12,
  websocket_queues: {
    wca_live: {
      connected: true,
      connection_count: 1,
      message_queue_size: 4,
      peak_message_queue_size: 9,
      queue_capacity: null,
      captured_at: new Date().toISOString(),
      counters: { frames_received: 120, bytes_received: 4000, subscription_messages_queued: 14, subscription_messages_dequeued: 10 }
    },
    cubingchina: {
      connected: true,
      connection_count: 2,
      message_queue_size: 2,
      peak_message_queue_size: 5,
      queue_capacity: null,
      captured_at: new Date().toISOString(),
      counters: { frames_received: 80, bytes_received: 2400, messages_queued: 12, messages_dequeued: 10 }
    }
  },
  graphql_subscription: { status: "running", connected: true, observations_count: 12 },
  api_polling: { status: "running", observations_count: 8 },
  cubingchina_websocket: { status: "running", connected: true, observations_count: 3, connected_competition_count: 2, target_competition_count: 2, target_round_count: 14, metadata: { competitions: [] } },
  subscription_rounds: { discovered: 12, subscribed: 12, errors: 0 },
  classification: { pending_scope_count: 0, claimed_scope_count: 0, failed_scope_count: 0, oldest_observation_lag_seconds: 0 },
  notifications: { deliveries: {}, queued_count: 0, due_count: 0, active_endpoint_count: 5, events_last_24h: 2 },
  record_pipeline: { source_observation_count: 100, result_observation_count: 80, canonical_result_count: 70, active_achievement_count: 6 }
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("debug dashboard", () => {
  it("renders live queue and operations telemetry at /debug", async () => {
    window.history.replaceState({}, "", "/debug");
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => payload })));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Debug dashboard" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "WebSocket queues" })).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("2 competition sockets")).toBeTruthy();
    expect(screen.getByText("Operational")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Notifications" })).toBeTruthy();
    const graphqlCard = screen.getByRole("heading", { name: "WCA Live GraphQL" }).closest("article");
    const apiCard = screen.getByRole("heading", { name: "WCA Live API" }).closest("article");
    expect(graphqlCard.textContent).toContain("Last message");
    expect(graphqlCard.textContent).toContain("Last snapshot");
    expect(apiCard.textContent).toContain("Last poll");
    expect(apiCard.textContent).not.toContain("Last message");
    expect(apiCard.textContent).not.toContain("Last snapshot");

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Resume live updates" })).toBeTruthy());
  });
});
