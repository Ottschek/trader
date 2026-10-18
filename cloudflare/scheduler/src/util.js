// Time and text helpers shared by the Worker's jobs.

export const HOUR = 3600 * 1000;

/** ISO timestamp in the format the Python side writes: 2026-10-16T22:00:00+00:00 */
export const isoUtc = (date) => date.toISOString().replace(/\.\d{3}Z$/, "+00:00");

/** Parse timestamps written by Python, which may carry microseconds. */
export const parseTs = (s) => Date.parse(s.replace(/(\.\d{3})\d+/, "$1"));

export const escapeHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function localTime(date, timeZone) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date);
}

/** Calendar parts of `date` in `timeZone`: {year, month, day, hour, minute, weekday (0=Sun), date: "YYYY-MM-DD"} */
export function localParts(date, timeZone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hourCycle: "h23", weekday: "short",
  }).formatToParts(date).map((p) => [p.type, p.value]));
  const year = Number(parts.year), month = Number(parts.month), day = Number(parts.day);
  return {
    year, month, day, hour: Number(parts.hour), minute: Number(parts.minute),
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(parts.weekday),
    date: `${parts.year}-${parts.month}-${parts.day}`,
  };
}

/** The UTC instant of a wall-clock time ("2026-10-19", "09:30") in `timeZone`, DST-aware. */
export function zonedToUtc(dateStr, hhmm, timeZone) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const [h, mi] = hhmm.split(":").map(Number);
  const wanted = Date.UTC(y, m - 1, d, h, mi);
  let t = wanted;
  for (let i = 0; i < 3; i++) {
    const p = localParts(new Date(t), timeZone);
    t += wanted - Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  }
  return new Date(t);
}

export function addDays(dateStr, days) {
  const t = Date.parse(`${dateStr}T12:00:00Z`) + days * 24 * HOUR;
  return new Date(t).toISOString().slice(0, 10);
}
