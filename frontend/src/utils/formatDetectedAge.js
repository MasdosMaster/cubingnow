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
