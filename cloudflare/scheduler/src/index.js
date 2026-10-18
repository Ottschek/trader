// Time-critical jobs on Cloudflare's clock. See wrangler.toml for the schedule.

import { makeAlpaca, telegram as sendTelegram } from "./clients.js";
import { equitySnapshot, handleCommand, isDecisionTime, morning, paperConfig, refreshOrders, riskCheck } from "./paper.js";
import { makeStore } from "./store.js";
import { HOUR, escapeHtml, isoUtc, localTime, parseTs } from "./util.js";

export { isoUtc, parseTs };

const HYPERLIQUID = "https://api.hyperliquid.xyz/info";

export function summaryDue(date, env) {
  return env.SUMMARY_TIMES.split(",").includes(localTime(date, env.TIMEZONE));
}

const telegram = (env, text) => sendTelegram(env, fetch, text);

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

/** Paper-trading jobs for this firing (only when Alpaca paper keys are configured). */
export function paperJobsAt(now, env) {
  if (!env.ALPACA_API_KEY) return [];
  const jobs = ["paper_orders"];
  if (isDecisionTime(now)) jobs.push("paper_morning");
  jobs.push("paper_risk");
  if (now.getUTCMinutes() < 30) jobs.push("paper_equity");
  return jobs;
}

function paperContext(env, now) {
  return { c: paperConfig(env), env, http: fetch, store: makeStore(env.DB), alpaca: makeAlpaca(env, fetch), now };
}

async function run(now, env) {
  const handlers = {
    snapshot: ["open interest snapshot", () => recordSnapshot(env, now)],
    watchdog: ["watchdog", () => watchdog(env, now)],
    summary: ["daily summary", () => sendSummary(env, now)],
    paper_orders: ["paper: order updates", () => refreshOrders(paperContext(env, now))],
    paper_morning: ["paper: 09:00 decision", () => morning(paperContext(env, now))],
    paper_risk: ["paper: risk check", async () => {
      const ctx = paperContext(env, now);
      if ((await ctx.alpaca.clock()).is_open) await riskCheck(ctx);
    }],
    paper_equity: ["paper: equity snapshot", () => equitySnapshot(paperContext(env, now))],
  };
  for (const name of [...jobsAt(now, env), ...paperJobsAt(now, env)]) {
    const [label, job] = handlers[name];
    try {
      await job();
    } catch (err) {
      // Nothing else watches these jobs, so report failures loudly.
      await telegram(env, `🚨 Scheduler job "${label}" failed: ${escapeHtml(err.message)}`).catch(() => {});
      console.error(label, err);
    }
  }
}

/** Telegram webhook: POST /telegram/<secret>, only from your own chat. */
async function telegramWebhook(request, env) {
  const url = new URL(request.url);
  const secret = env.TELEGRAM_WEBHOOK_SECRET;
  if (request.method !== "POST" || !secret || url.pathname !== `/telegram/${secret}` ||
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== secret) {
    return new Response("Not found", { status: 404 });
  }
  const update = await request.json().catch(() => ({}));
  const msg = update.message;
  if (!msg || String(msg.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return new Response("ok");   // ignore strangers
  let reply;
  try {
    reply = env.ALPACA_API_KEY ? await handleCommand(paperContext(env, new Date()), msg.text) : "Paper trading isn't configured.";
  } catch (err) {
    reply = `Command failed: ${escapeHtml(err.message)}`;
  }
  await telegram(env, reply).catch(() => {});
  return new Response("ok");
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(new Date(event.scheduledTime), env));
  },
  async fetch(request, env) {
    return telegramWebhook(request, env);
  },
};
