const FALLBACK = {
  title: "CubingNow record alert",
  body: "A new speedcubing record was detected.",
  target_url: "/",
  tag: "cubingnow-record-alert",
  icon: "/icons/icon-192.png",
  badge: "/icons/badge-96.png"
};

function safePayload(event) {
  if (!event.data) return FALLBACK;
  try {
    const payload = event.data.json();
    return payload && typeof payload === "object" ? { ...FALLBACK, ...payload } : FALLBACK;
  } catch {
    return FALLBACK;
  }
}

function sameOriginPath(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const target = new URL(value, self.location.origin);
    return target.origin === self.location.origin ? `${target.pathname}${target.search}${target.hash}` : "/";
  } catch {
    return "/";
  }
}

self.addEventListener("push", (event) => {
  const payload = safePayload(event);
  event.waitUntil(self.registration.showNotification(payload.title || FALLBACK.title, {
    body: payload.body || FALLBACK.body,
    icon: payload.icon || FALLBACK.icon,
    badge: payload.badge || FALLBACK.badge,
    tag: payload.tag || FALLBACK.tag,
    data: {
      target_url: sameOriginPath(payload.target_url),
      notification_event_id: payload.notification_event_id || null
    }
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetPath = sameOriginPath(event.notification.data?.target_url);
  const targetUrl = new URL(targetPath, self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const existing = windows.find((client) => new URL(client.url).origin === self.location.origin);
    if (existing) {
      if ("navigate" in existing) await existing.navigate(targetUrl);
      return existing.focus();
    }
    return self.clients.openWindow(targetUrl);
  })());
});
