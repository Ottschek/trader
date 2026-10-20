// Pure helpers that turn the /api/health payload into what the page shows.

const HOUR = 3600 * 1000;

// How old the newest bar may be before a dataset counts as stale (mirrors scripts/collect.py).
const ALT_MAX_AGE = { stablecoins: 72, pageviews: 96, etf_flows: null, polymarket: 3, listings: null };   // etf_flows: frozen (Farside blocks bots since 2026-10-19)

export function maxAgeHours(row) {
  if (row.kind === "corporate_actions") return null;
  if (row.kind === "alt") return ALT_MAX_AGE[row.source] ?? null;   // listings only change when something lists
  if (row.kind === "snapshots") return 2;
  if (row.kind === "funding") return 3;
  if (row.source.startsWith("alpaca_stocks")) return 96; // weekends and holidays
  return 3;
}

export function ageHours(iso, now) {
  return iso ? (now - new Date(iso).getTime()) / HOUR : null;
}

export function overallStatus(checks) {
  if (checks.some((c) => c.status === "fail")) return "fail";
  if (checks.some((c) => c.status === "warn")) return "warn";
  return "ok";
}

/** One cell per hour for the last `hours` hours (oldest first), with the worst run in that hour. */
export function hourlyCells(runs, now, hours = 72) {
  const rank = { ok: 0, warn: 1, fail: 2 };
  const end = Math.floor(now / HOUR) * HOUR;
  const cells = [];
  for (let i = hours - 1; i >= 0; i--) {
    const start = end - i * HOUR;
    const inHour = runs.filter((r) => {
      const t = new Date(r.run_at).getTime();
      return t >= start && t < start + HOUR;
    });
    const worst = inHour.reduce((w, r) => (w && rank[w.status] >= rank[r.status] ? w : r), null);
    cells.push({ start, run: worst, count: inHour.length });
  }
  return cells;
}

const DATASET_GROUPS = [
  ["bars", "coinbase", "Crypto · Coinbase (signals)"],
  ["bars", "alpaca_crypto", "Crypto · Alpaca (fill prices)"],
  ["bars", "alpaca_stocks", "Stocks · Alpaca, session hourly"],
  ["bars", "alpaca_stocks_30m", "Stocks · Alpaca, raw 30-minute"],
  ["funding", "hyperliquid", "Funding rates · Hyperliquid"],
  ["snapshots", "hyperliquid", "Open interest snapshots · Hyperliquid"],
  ["corporate_actions", "alpaca", "Splits & dividends · Alpaca"],
  ["alt", "*", "Alternative data · stablecoins, Wikipedia attention, ETF flows, Polymarket, listings"],
];

export function groupDatasets(datasets) {
  const groups = DATASET_GROUPS.map(([kind, source, label]) => ({
    label,
    rows: datasets.filter((d) => d.kind === kind && (source === "*" || d.source === source)),
  })).filter((g) => g.rows.length);
  const known = new Set(DATASET_GROUPS.map(([k, s]) => `${k}/${s}`));
  const other = datasets.filter((d) => !known.has(`${d.kind}/${d.source}`) && !known.has(`${d.kind}/*`));
  if (other.length) groups.push({ label: "Other", rows: other });
  return groups;
}

/** Parse "median 0.4 bps, p95 1.2 bps over 721 bars" from the drift checks. */
export function driftRows(checks) {
  return checks
    .filter((c) => c.check_name === "drift")
    .map((c) => {
      const m = c.detail.match(/median ([\d.]+) bps, p95 ([\d.]+) bps over (\d+) bars/);
      return {
        symbol: c.dataset.replace("alpaca vs coinbase ", ""),
        status: c.status,
        median: m ? Number(m[1]) : null,
        p95: m ? Number(m[2]) : null,
        bars: m ? Number(m[3]) : null,
        detail: c.detail,
      };
    })
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}

export function relativeTime(iso, now) {
  const mins = Math.round((now - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = mins / 60;
  if (hrs < 48) return `${Math.round(hrs)} h ago`;
  return `${Math.round(hrs / 24)} days ago`;
}

export function formatAge(hours) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

export const compact = (n) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
export const grouped = (n) => new Intl.NumberFormat("en").format(n);

export function shortDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function shortDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}
