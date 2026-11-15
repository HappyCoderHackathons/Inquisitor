// Shared display formatting for the dashboard, devices, and demo pages.

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

/** "Nov 15, 2026, 2:03 PM" for an ISO timestamp, or the fallback when there is none. */
export function formatTime(iso: string | null | undefined, fallback = "Not recorded"): string {
  if (!iso) return fallback;
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? fallback : dateTime.format(value);
}

/** "just now", "5 min ago", "3 h ago", or a date for anything older than a day. */
export function formatAgo(iso: string | null | undefined, fallback = "never"): string {
  if (!iso) return fallback;
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return fallback;
  const seconds = Math.max(0, (Date.now() - value.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return formatTime(iso);
}

/** Scores and thresholds to two decimals. */
export function formatScore(value: number | null | undefined, fallback = "not available"): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : fallback;
}

/** "45 s", "3 min 20 s", or "2 h 5 min". */
export function formatDuration(seconds: number | null | undefined, fallback = "not recorded"): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return fallback;
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60),
    rest = total % 60;
  if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}
