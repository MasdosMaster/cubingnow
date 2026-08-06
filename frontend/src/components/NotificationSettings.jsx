import { useEffect, useState } from "react";

import {
  DEFAULT_NOTIFICATION_TYPES,
  disableNotifications,
  enableNotifications,
  inspectNotificationState,
  updateNotificationPreferences
} from "../notifications/client";

const STATUS_TEXT = {
  unsupported: "This browser does not currently expose Web Push.",
  not_configured: "Record alerts are not configured on this deployment yet.",
  not_requested: "Choose the record levels you want, then enable alerts.",
  denied: "Notifications are blocked. Enable them in browser or system settings to continue.",
  disabled: "Notifications are allowed, but this browser is not subscribed.",
  sync_failed: "The browser subscription exists, but server synchronization failed.",
  registered: "Record alerts are active on this browser."
};

export function NotificationSettings() {
  const [state, setState] = useState({
    status: "loading",
    config: null,
    preferences: Object.fromEntries(DEFAULT_NOTIFICATION_TYPES.map((item) => [item.value, item.default])),
    error: ""
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    inspectNotificationState()
      .then((next) => current && setState({ ...next, error: next.error || "" }))
      .catch((error) => current && setState((previous) => ({ ...previous, status: "sync_failed", error: error.message })));
    return () => { current = false; };
  }, []);

  const types = state.config?.supported_notification_types || DEFAULT_NOTIFICATION_TYPES;

  async function togglePreference(notificationType) {
    const preferences = {
      ...state.preferences,
      [notificationType]: !state.preferences[notificationType]
    };
    setState((previous) => ({ ...previous, preferences, error: "" }));
    if (state.status !== "registered") return;
    try {
      const payload = await updateNotificationPreferences(state.config, preferences);
      if (payload) setState((previous) => ({ ...previous, preferences: payload.preferences }));
    } catch (error) {
      setState((previous) => ({ ...previous, status: "sync_failed", error: error.message }));
    }
  }

  async function enable() {
    setBusy(true);
    setState((previous) => ({ ...previous, error: "" }));
    try {
      const result = await enableNotifications(state.config, state.preferences);
      setState((previous) => ({
        ...previous,
        status: result.status,
        preferences: result.preferences || previous.preferences
      }));
    } catch (error) {
      setState((previous) => ({ ...previous, status: "sync_failed", error: error.message }));
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    setState((previous) => ({ ...previous, error: "" }));
    try {
      const result = await disableNotifications(state.config);
      setState((previous) => ({ ...previous, status: result.status }));
    } catch (error) {
      setState((previous) => ({ ...previous, status: "disabled", error: error.message }));
    } finally {
      setBusy(false);
    }
  }

  const canEnable = ["not_requested", "disabled", "sync_failed"].includes(state.status)
    && state.config?.web_push_configured;

  return (
    <section className="notification-settings" aria-labelledby="record-alerts-heading">
      <div>
        <p className="eyebrow">Guest Web Push</p>
        <h2 id="record-alerts-heading">Record alerts</h2>
        <p className="notification-status" role="status">
          {state.status === "loading" ? "Checking browser support…" : STATUS_TEXT[state.status]}
        </p>
        {state.needsIosInstallation && (
          <p className="notification-help">On iPhone or iPad, add CubingNow to the Home Screen, open the installed app, then enable alerts here.</p>
        )}
        {state.error && <p className="status-warning">{state.error}</p>}
      </div>
      <div className="notification-controls">
        <fieldset disabled={busy || state.status === "loading"}>
          <legend>Notify me about</legend>
          {types.map((item) => (
            <label key={item.value}>
              <input
                type="checkbox"
                checked={Boolean(state.preferences[item.value])}
                onChange={() => togglePreference(item.value)}
              />
              {item.label}
            </label>
          ))}
        </fieldset>
        <div className="notification-actions">
          {state.status === "registered" ? (
            <button type="button" className="secondary" onClick={disable} disabled={busy}>Disable notifications</button>
          ) : (
            <button type="button" onClick={enable} disabled={busy || !canEnable}>Enable record alerts</button>
          )}
        </div>
        {state.status === "registered" && !Object.values(state.preferences).some(Boolean) && (
          <small>The endpoint stays active with no selected alerts until you explicitly disable it.</small>
        )}
      </div>
    </section>
  );
}
