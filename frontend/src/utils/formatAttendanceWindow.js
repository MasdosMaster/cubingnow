export function attendanceDateLabel(value, includeYear = false) {
  if (!value) return "";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    ...(includeYear ? { year: "numeric" } : {}),
    timeZone: "UTC"
  }).format(new Date(`${value}T00:00:00Z`));
}

export function formatAttendanceWindow(window) {
  if (!window?.start_date || !window?.end_date) return "Wednesday–Tuesday";
  const crossesYear = window.start_date.slice(0, 4) !== window.end_date.slice(0, 4);
  return `${attendanceDateLabel(window.start_date, crossesYear)} – ${attendanceDateLabel(window.end_date, true)}`;
}
