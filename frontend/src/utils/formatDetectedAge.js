export function formatDetectedAge(detectedAt, now = new Date()) {
  const elapsedMinutes = Math.max(
    0,
    Math.floor((now.getTime() - new Date(detectedAt).getTime()) / 60_000)
  );

  if (elapsedMinutes < 60) return `${elapsedMinutes}m ago`;

  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 6) {
    return `${elapsedHours}h${elapsedMinutes % 60}m ago`;
  }
  if (elapsedHours < 24) return `${elapsedHours}h ago`;

  return `${Math.floor(elapsedHours / 24)}d${elapsedHours % 24}h ago`;
}

export function formatCompactDetectedAge(detectedAt, now = new Date()) {
  const elapsedSeconds = Math.max(
    0,
    Math.floor((now.getTime() - new Date(detectedAt).getTime()) / 1_000)
  );

  if (elapsedSeconds < 60) return `${elapsedSeconds}s ago`;

  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) return `${elapsedMinutes}m ago`;

  const elapsedHours = Math.floor(elapsedSeconds / 3_600);
  if (elapsedHours < 24) return `${elapsedHours}h ago`;

  const elapsedDays = Math.floor(elapsedSeconds / 86_400);
  if (elapsedDays < 7) return `${elapsedDays}d ago`;

  return `${Math.floor(elapsedDays / 7)}w ago`;
}

export function isRecentlyDetected(detectedAt, now = new Date()) {
  return now.getTime() - new Date(detectedAt).getTime() < 96 * 60 * 60 * 1000;
}
