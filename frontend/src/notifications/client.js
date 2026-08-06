const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const MANAGEMENT_KEY = "cubingnow.push.management.v1";
const PREFERENCES_KEY = "cubingnow.push.preferences.v1";
const PENDING_DISABLE_KEY = "cubingnow.push.pending-disable.v1";
let inspectionPromise = null;
let preferenceUpdateChain = Promise.resolve();

export const DEFAULT_NOTIFICATION_TYPES = [
  { value: "record_wr", label: "World Records", default: true },
  { value: "record_cr", label: "Continental Records", default: true },
  { value: "record_nr", label: "National Records", default: true }
];

function storageGet(key) {
  try {
    return JSON.parse(window.localStorage.getItem(key));
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  window.localStorage.setItem(key, JSON.stringify(value));
}

function storageRemove(key) {
  window.localStorage.removeItem(key);
}

async function responseJson(response) {
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const message = payload?.detail || `Notification request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

async function mutation(path, method, body, csrfToken, { allowMissing = false } = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken
    },
    body: JSON.stringify(body)
  });
  if (allowMissing && response.status === 404) return null;
  return responseJson(response);
}

export function pushFeaturesSupported() {
  return Boolean(
    "serviceWorker" in navigator
    && "PushManager" in window
    && "Notification" in window
  );
}

export function readPreferences(types = DEFAULT_NOTIFICATION_TYPES) {
  const stored = storageGet(PREFERENCES_KEY) || {};
  return Object.fromEntries(
    types.map((item) => [item.value, typeof stored[item.value] === "boolean" ? stored[item.value] : item.default])
  );
}

export function isStandaloneDisplay() {
  return window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;
}

export function appearsToNeedIosInstallation() {
  const iosLike = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  return iosLike && !isStandaloneDisplay() && !pushFeaturesSupported();
}

export async function getNotificationConfig() {
  const response = await fetch(`${API_BASE_URL}/notifications/config/`, {
    credentials: "include"
  });
  const payload = await responseJson(response);
  payload.supported_notification_types = payload.supported_notification_types?.length
    ? payload.supported_notification_types
    : DEFAULT_NOTIFICATION_TYPES;
  return payload;
}

function applicationServerKey(value) {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const binary = window.atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function registration() {
  await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
  return navigator.serviceWorker.ready;
}

async function registerWithBackend(subscription, preferences, config) {
  const existing = storageGet(MANAGEMENT_KEY);
  const payload = await mutation(
    "/notifications/subscriptions/",
    "POST",
    {
      subscription: subscription.toJSON(),
      preferences,
      management_token: existing?.management_token || ""
    },
    config.csrf_token
  );
  const management = {
    endpoint_id: payload.endpoint_id,
    management_token: payload.management_token || existing?.management_token
  };
  storageSet(MANAGEMENT_KEY, management);
  storageSet(PREFERENCES_KEY, payload.preferences);
  return payload;
}

async function retryPendingDisable(config) {
  const pending = storageGet(PENDING_DISABLE_KEY);
  if (!pending) return { handled: false, error: "" };
  let cleanupError = "";
  if (!pending.browser_only) {
    try {
      await mutation(
        "/notifications/subscriptions/",
        "DELETE",
        pending,
        config.csrf_token,
        { allowMissing: true }
      );
    } catch {
      cleanupError = "Server cleanup is still pending";
    }
  }
  try {
    const serviceWorker = await registration();
    const subscription = await serviceWorker.pushManager.getSubscription();
    if (subscription) await subscription.unsubscribe();
  } catch {
    cleanupError = cleanupError || "Browser unsubscribe is still pending";
  }
  if (!cleanupError) {
    storageRemove(PENDING_DISABLE_KEY);
    storageRemove(MANAGEMENT_KEY);
  }
  return { handled: true, error: cleanupError };
}

async function inspectNotificationStateOnce() {
  const supported = pushFeaturesSupported();
  if (!supported) {
    return {
      status: "unsupported",
      config: null,
      preferences: readPreferences(),
      needsIosInstallation: appearsToNeedIosInstallation()
    };
  }
  const config = await getNotificationConfig();
  const preferences = readPreferences(config.supported_notification_types);
  if (!config.web_push_configured) return { status: "not_configured", config, preferences };
  const cleanup = await retryPendingDisable(config);
  if (cleanup.handled) {
    return { status: "disabled", config, preferences, error: cleanup.error };
  }
  if (Notification.permission === "denied") return { status: "denied", config, preferences };
  if (Notification.permission !== "granted") return { status: "not_requested", config, preferences };

  const serviceWorker = await registration();
  const subscription = await serviceWorker.pushManager.getSubscription();
  if (!subscription) return { status: "disabled", config, preferences };
  try {
    const server = await registerWithBackend(subscription, preferences, config);
    return { status: "registered", config, preferences: server.preferences };
  } catch (error) {
    return { status: "sync_failed", config, preferences, error: error.message };
  }
}

export function inspectNotificationState() {
  if (!inspectionPromise) {
    inspectionPromise = inspectNotificationStateOnce().finally(() => {
      inspectionPromise = null;
    });
  }
  return inspectionPromise;
}

export async function enableNotifications(config, preferences) {
  // This function is called directly by the click handler; keep permission first.
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { status: permission === "denied" ? "denied" : "not_requested" };
  }
  const serviceWorker = await registration();
  let subscription = await serviceWorker.pushManager.getSubscription();
  if (!subscription) {
    subscription = await serviceWorker.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey(config.vapid_public_key)
    });
  }
  const server = await registerWithBackend(subscription, preferences, config);
  return { status: "registered", preferences: server.preferences };
}

async function updateNotificationPreferencesNow(config, preferences) {
  storageSet(PREFERENCES_KEY, preferences);
  const management = storageGet(MANAGEMENT_KEY);
  if (!management) return null;
  const payload = await mutation(
    "/notifications/preferences/",
    "PATCH",
    { ...management, preferences },
    config.csrf_token
  );
  storageSet(PREFERENCES_KEY, payload.preferences);
  return payload;
}

export function updateNotificationPreferences(config, preferences) {
  preferenceUpdateChain = preferenceUpdateChain
    .catch(() => null)
    .then(() => updateNotificationPreferencesNow(config, preferences));
  return preferenceUpdateChain;
}

export async function disableNotifications(config) {
  const serviceWorker = await registration();
  const subscription = await serviceWorker.pushManager.getSubscription();
  const management = storageGet(MANAGEMENT_KEY);
  let backendError = null;
  let browserError = null;
  if (management) {
    storageSet(PENDING_DISABLE_KEY, management);
    try {
      await mutation(
        "/notifications/subscriptions/",
        "DELETE",
        management,
        config.csrf_token,
        { allowMissing: true }
      );
    } catch (error) {
      backendError = error;
    }
  } else if (subscription) {
    storageSet(PENDING_DISABLE_KEY, { browser_only: true });
  }
  try {
    if (subscription) await subscription.unsubscribe();
  } catch (error) {
    browserError = error;
  }
  if (!backendError && !browserError) {
    storageRemove(MANAGEMENT_KEY);
    storageRemove(PENDING_DISABLE_KEY);
  }
  if (backendError || browserError) {
    throw new Error("Disable cleanup is incomplete and will retry next visit");
  }
  return { status: "disabled" };
}
