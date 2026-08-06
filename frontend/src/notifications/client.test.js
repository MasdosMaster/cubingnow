// @vitest-environment jsdom

import { readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  disableNotifications,
  enableNotifications,
  inspectNotificationState,
  updateNotificationPreferences
} from "./client";

const config = {
  vapid_public_key: "AQIDBA",
  web_push_configured: true,
  csrf_token: "csrf-token",
  supported_notification_types: [
    { value: "record_wr", label: "World Records", default: true },
    { value: "record_cr", label: "Continental Records", default: true },
    { value: "record_nr", label: "National Records", default: true }
  ]
};

const preferences = { record_wr: true, record_cr: false, record_nr: true };
let originalServiceWorker;

function jsonResponse(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

function installPushEnvironment({ permission = "default", subscription = null } = {}) {
  const subscribe = vi.fn();
  const pushManager = {
    getSubscription: vi.fn(async () => subscription),
    subscribe
  };
  const serviceWorker = {
    register: vi.fn(async () => ({})),
    ready: Promise.resolve({ pushManager })
  };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: serviceWorker
  });
  vi.stubGlobal("PushManager", function PushManager() {});
  vi.stubGlobal("Notification", {
    permission,
    requestPermission: vi.fn(async () => "granted")
  });
  return { serviceWorker, pushManager, subscribe };
}

beforeEach(() => {
  originalServiceWorker = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");
  window.localStorage.clear();
});

afterEach(() => {
  if (originalServiceWorker) {
    Object.defineProperty(navigator, "serviceWorker", originalServiceWorker);
  } else {
    delete navigator.serviceWorker;
  }
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("guest Web Push client", () => {
  it("reports an unsupported browser without calling the API", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const state = await inspectNotificationState();
    expect(state.status).toBe("unsupported");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("does not request permission while inspecting initial state", async () => {
    const { serviceWorker } = installPushEnvironment();
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(config)));

    const state = await inspectNotificationState();

    expect(state.status).toBe("not_requested");
    expect(Notification.requestPermission).not.toHaveBeenCalled();
    expect(serviceWorker.register).not.toHaveBeenCalled();
  });

  it("represents denied permission without prompting again", async () => {
    installPushEnvironment({ permission: "denied" });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(config)));
    const state = await inspectNotificationState();
    expect(state.status).toBe("denied");
    expect(Notification.requestPermission).not.toHaveBeenCalled();
  });

  it("reuses an existing browser subscription instead of recreating it", async () => {
    const existing = { toJSON: () => ({ endpoint: "https://push.test/one", keys: {} }) };
    const { subscribe } = installPushEnvironment({ permission: "granted", subscription: existing });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      endpoint_id: "96d9379c-b387-4497-80e3-30919924c112",
      management_token: "management-token",
      active: true,
      preferences
    })));

    const result = await enableNotifications(config, preferences);

    expect(result.status).toBe("registered");
    expect(subscribe).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("updates preferences without touching PushManager", async () => {
    const { pushManager } = installPushEnvironment({ permission: "granted" });
    window.localStorage.setItem("cubingnow.push.management.v1", JSON.stringify({
      endpoint_id: "96d9379c-b387-4497-80e3-30919924c112",
      management_token: "management-token"
    }));
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ preferences })));

    await updateNotificationPreferences(config, preferences);

    expect(pushManager.getSubscription).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith(
      "/api/notifications/preferences/",
      expect.objectContaining({ method: "PATCH" })
    );
  });

  it("deactivates the backend and unsubscribes the browser", async () => {
    const subscription = { unsubscribe: vi.fn(async () => true) };
    installPushEnvironment({ permission: "granted", subscription });
    window.localStorage.setItem("cubingnow.push.management.v1", JSON.stringify({
      endpoint_id: "96d9379c-b387-4497-80e3-30919924c112",
      management_token: "management-token"
    }));
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 204 })));

    const result = await disableNotifications(config);

    expect(result.status).toBe("disabled");
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith(
      "/api/notifications/subscriptions/",
      expect.objectContaining({ method: "DELETE" })
    );
  });

  it("retries backend cleanup after browser unsubscribe succeeds first", async () => {
    const subscription = { unsubscribe: vi.fn(async () => true) };
    installPushEnvironment({ permission: "granted", subscription });
    window.localStorage.setItem("cubingnow.push.management.v1", JSON.stringify({
      endpoint_id: "96d9379c-b387-4497-80e3-30919924c112",
      management_token: "management-token"
    }));
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));

    await expect(disableNotifications(config)).rejects.toThrow("will retry next visit");
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();

    installPushEnvironment({ permission: "granted", subscription: null });
    vi.stubGlobal("fetch", vi.fn(async (url, options) => (
      options?.method === "DELETE" ? { ok: true, status: 204 } : jsonResponse(config)
    )));
    const state = await inspectNotificationState();
    expect(state.status).toBe("disabled");
    expect(window.localStorage.getItem("cubingnow.push.pending-disable.v1")).toBeNull();
  });

  it("keeps same-origin validation in notification click handling", () => {
    const source = readFileSync("public/service-worker.js", "utf8");
    expect(source).toContain("target.origin === self.location.origin");
    expect(source).toContain("value.startsWith(\"//\")");
    expect(source).toContain("event.waitUntil");
  });
});
