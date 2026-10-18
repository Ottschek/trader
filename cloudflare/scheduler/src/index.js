// Time-critical jobs on Cloudflare's clock. See wrangler.toml for the schedule.

const HOUR = 3600 * 1000;
const HYPERLIQUID = "https://api.hyperliquid.xyz/info";

// --- helpers -------------------------------------------------------------------

/** ISO timestamp in the same format the Python side writes: 2026-10-16T22:00:00+00:00 */
export const isoUtc = (date) => date.toISOString().replace(/\.\d{3}Z$/, "+00:00");

/** Parse timestamps written by Python, which may carry microseconds. */
export const parseTs = (s) => Date.parse(s.replace(/(\.\d{3})\d+/, "$1"));

export function localTime(date, timeZone) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date);
}

export function summaryDue(date, env) {
  return env.SUMMARY_TIMES.split(",").includes(localTime(date, env.TIMEZONE));
}

const escapeHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function telegram(env, text) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) throw new Error(`Telegram answered ${res.status}`);
}

// --- open interest snapshot ------------------------------------------------------

export function snapshotRows(meta, ctxs, coins, hourIso, takenIso) {
  const index = new Map(meta.universe.map((u, i) => [u.name, i]));
  return coins.filter((c) => index.has(c)).map((coin) => {
    const c = ctxs[index.get(coin)];
    return {
      ts: hourIso, taken_at: takenIso, coin,
      open_interest: Number(c.openInterest),
      funding_rate: Number(c.funding),
      premium: c.premium == null ? null : Number(c.premium),
      mark_px: Number(c.markPx),
      oracle_px: Number(c.oraclePx),
      day_notional_volume: Number(c.dayNtlVlm),
    };
  });
}

async function recordSnapshot(env, now) {
  const res = await fetch(HYPERLIQUID, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "metaAndAssetCtxs" }),
  });
  if (!res.ok) throw new Error(`Hyperliquid answered ${res.status}`);
  const [meta, ctxs] = await res.json();
  const hour = new Date(Math.floor(now.getTime() / HOUR) * HOUR);
  const rows = snapshotRows(meta, ctxs, env.COINS.split(","), isoUtc(hour), isoUtc(now));
  const stmt = env.DB.prepare(
    "INSERT OR REPLACE INTO oi_snapshots (ts, taken_at, coin, open_interest, funding_rate, premium, " +
    "mark_px, oracle_px, day_notional_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
  );
  await env.DB.batch(rows.map((r) => stmt.bind(
    r.ts, r.taken_at, r.coin, r.open_interest, r.funding_rate, r.premium, r.mark_px, r.oracle_px, r.day_notional_volume,
  )));
  return rows.length;
}

// --- watchdog --------------------------------------------------------------------

/** What to tell the user: "down" once when runs stop, "up" once when they resume. */
export function watchdogAction(lagHours, maxLagHours, alertActive) {
  if (lagHours > maxLagHours && !alertActive) return "down";
  if (lagHours <= maxLagHours && alertActive) return "up";
  return null;
}

async function watchdog(env, now) {
  const last = await env.DB.prepare("SELECT max(run_at) AS last FROM collector_runs").first("last");
  const lag = last ? (now.getTime() - parseTs(last)) / HOUR : Infinity;
  const active = (await env.DB.prepare("SELECT value FROM scheduler_state WHERE key = 'collector_down'").first("value")) === "1";
  const action = watchdogAction(lag, Number(env.MAX_COLLECTOR_LAG_HOURS), active);
  if (!action) return;
  await telegram(env, action === "down"
    ? `⚠️ <b>No collector run for ${Number.isFinite(lag) ? lag.toFixed(1) + " h" : "ever"}</b>. GitHub may have skipped its schedule; price bars will catch up on the next run. ${env.DASHBOARD_URL}`
    : "✅ <b>Collector is running again.</b>");
  await env.DB.prepare(
    "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) VALUES ('collector_down', ?, ?)",
  ).bind(action === "down" ? "1" : "0", isoUtc(now)).run();
}

// --- daily summary ---------------------------------------------------------------

export function formatSummary({ now, timeZone, runs, lastRunAt, problems, datasets, oiCount, coins, dashboard }) {
  const local = new Intl.DateTimeFormat("en-GB", {
    timeZone, weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(now);
  const lines = [`📊 <b>Trader status</b> · ${local}`];
  if (!runs.length) {
    lines.push("⚠️ No collector runs in the last 24 h!");
  } else {
    const failed = runs.filter((r) => r.status === "fail").length;
    const added = runs.reduce((s, r) => s + r.rows_added, 0);
    lines.push(`Collector runs (24 h): ${runs.length} · ${failed} with failures · +${added.toLocaleString("en")} rows`);
  }
  if (lastRunAt) {
    const lag = (now.getTime() - parseTs(lastRunAt)) / HOUR;
    lines.push(`Last collector run: ${lag < 1 ? Math.round(lag * 60) + " min" : lag.toFixed(1) + " h"} ago`);
    const fails = problems.filter((p) => p.status === "fail");
    const warns = problems.filter((p) => p.status === "warn");
    lines.push(`Latest run: ${fails.length} fail, ${warns.length} warn`);
    for (const p of fails.slice(0, 10)) {
      lines.push(`• ❌ ${escapeHtml(p.check_name)} · ${escapeHtml(p.dataset)}: ${escapeHtml(p.detail)}`);
    }
  }
  lines.push(`Open interest snapshots (24 h): ${oiCount}/${24 * coins}`);
  for (const d of datasets) lines.push(`${d.kind}: ${d.n} files, ${Number(d.rows || 0).toLocaleString("en")} rows`);
  lines.push(dashboard);
  return lines.join("\n");
}

async function sendSummary(env, now) {
  const since = isoUtc(new Date(now.getTime() - 24 * HOUR));
  const [runs, last, datasets, oi] = await env.DB.batch([
    env.DB.prepare("SELECT status, rows_added FROM collector_runs WHERE run_at >= ?").bind(since),
    env.DB.prepare("SELECT run_at FROM collector_runs ORDER BY run_at DESC LIMIT 1"),
    env.DB.prepare("SELECT kind, count(*) AS n, sum(rows) AS rows FROM dataset_status GROUP BY kind ORDER BY kind"),
    env.DB.prepare("SELECT count(*) AS n FROM oi_snapshots WHERE ts >= ?").bind(since),
  ]);
  const lastRunAt = last.results[0]?.run_at ?? null;
  const problems = lastRunAt
    ? (await env.DB.prepare(
        "SELECT status, check_name, dataset, detail FROM check_results WHERE run_at = ? AND status != 'ok' ORDER BY status, dataset",
      ).bind(lastRunAt).all()).results
    : [];
  await telegram(env, formatSummary({
    now, timeZone: env.TIMEZONE, runs: runs.results, lastRunAt, problems, datasets: datasets.results,
    oiCount: oi.results[0].n, coins: env.COINS.split(",").length, dashboard: env.DASHBOARD_URL,
  }));
}

// --- entry point -----------------------------------------------------------------

/** Which jobs a trigger firing at this time should run (the single cron fires at :00 and :30). */
export function jobsAt(now, env) {
  const jobs = [];
  if (now.getUTCMinutes() < 30) jobs.push("snapshot", "watchdog");
  if (summaryDue(now, env)) jobs.push("summary");
  return jobs;
}

async function run(now, env) {
  const handlers = {
    snapshot: ["open interest snapshot", () => recordSnapshot(env, now)],
    watchdog: ["watchdog", () => watchdog(env, now)],
    summary: ["daily summary", () => sendSummary(env, now)],
  };
  const jobs = jobsAt(now, env).map((j) => handlers[j]);
  for (const [name, job] of jobs) {
    try {
      await job();
    } catch (err) {
      // Nothing else watches these jobs, so report failures loudly.
      await telegram(env, `🚨 Scheduler job "${name}" failed: ${escapeHtml(err.message)}`).catch(() => {});
      console.error(name, err);
    }
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(new Date(event.scheduledTime), env));
  },
};
