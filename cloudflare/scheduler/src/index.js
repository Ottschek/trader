// Time-critical jobs on Cloudflare's clock. See wrangler.toml for the schedule.

import { COINS as CARRY_COINS, initialState as carryInitial, priceSnapshot, step as carryStep } from "./carry.js";
import { makeAlpaca, telegram as sendTelegram } from "./clients.js";
import { equitySnapshot, handleCommand, isDecisionTime, morning, paperConfig, refreshOrders, riskCheck } from "./paper.js";
import { briefingDue, dispatchReport, parseReport, reportCommand } from "./reports.js";
import { makeStore } from "./store.js";
import { HOUR, escapeHtml, isoUtc, localTime, parseTs } from "./util.js";

export { isoUtc, parseTs };

const HYPERLIQUID = "https://api.hyperliquid.xyz/info";

export function summaryDue(date, env) {
  return env.SUMMARY_TIMES.split(",").includes(localTime(date, env.TIMEZONE));
}

/** WEEKLY_REPORT like "Sun 20:00" in TIMEZONE. */
export function weeklyDue(date, env) {
  if (!env.WEEKLY_REPORT) return false;
  const [day, time] = env.WEEKLY_REPORT.split(" ");
  const weekday = new Intl.DateTimeFormat("en-US", { timeZone: env.TIMEZONE, weekday: "short" }).format(date);
  return weekday === day && localTime(date, env.TIMEZONE) === time;
}

const fmtPct = (x, d = 2) => (x == null ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%`);

export function formatWeekly({ now, timeZone, equityNow, equityWeekAgo, decisions, openTrade, halted, recon,
                               runs, failingNow, oiCount, experiments, totalTrials, dashboard, carry = null, news = null, reversal = null }) {
  const local = new Intl.DateTimeFormat("en-GB", { timeZone, day: "2-digit", month: "short", year: "numeric" }).format(now);
  const lines = [`🗓 <b>Weekly report</b> · week ending ${local}`, "", "🧪 <b>Shadow paper experiment</b> (not a validated candidate)"];
  if (equityNow != null) {
    lines.push(`Equity ${Math.round(equityNow).toLocaleString("en")} USD` +
               (equityWeekAgo ? ` (${fmtPct(equityNow / equityWeekAgo - 1)} this week)` : ""));
  }
  const trades = decisions.filter((d) => d.kind === "entry").length;
  const noTrades = decisions.filter((d) => d.kind === "no_trade").length;
  lines.push(`Decisions this week: ${trades} entr${trades === 1 ? "y" : "ies"}, ${noTrades} no-trade` +
             (decisions.some((d) => d.kind === "skipped") ? ", some skipped ⚠️" : ""));
  lines.push(`Open trade: ${openTrade ? `since ${openTrade.session_date}` : "none"} · Trading: ${halted ? `halted (${escapeHtml(halted.reason)})` : "active"}`);
  if (recon) {
    lines.push(`Live vs backtest: tracking ${fmtPct(recon.tracking_difference)}, ${recon.mismatches.length} decision mismatch(es)` +
               (recon.avg_slippage_bps != null ? `, avg fill vs open ${recon.avg_slippage_bps} bps` : ""));
  }
  if (carry) {
    lines.push("", "🧮 <b>Carry simulation</b> (shadow, no real orders)");
    lines.push(`Equity ${Math.round(carry.equity).toLocaleString("en")} USD (${fmtPct(carry.equity / 100000 - 1)} since start; ` +
               `model ${fmtPct(carry.model_equity / 100000 - 1)}) · ${carry.rebalances} margin rebalance(s)`);
  }
  if (reversal && reversal.equity != null) {
    lines.push("", "🔁 <b>Weekly reversal</b> (simulation, no orders)");
    lines.push(`${fmtPct(reversal.equity - 1)} since 19 Oct over ${reversal.weeks} finished week(s)` +
               (reversal.lastWeek != null ? ` · last week ${fmtPct(reversal.lastWeek)}` : ""));
  }
  if (news && news.runs > 0) {
    lines.push("", "📰 <b>LLM news analyst</b> (shadow, no real orders)");
    lines.push(`This week: ${news.runs} run(s), ${news.signals} signal(s), $${news.cost.toFixed(2)} spent`);
    lines.push(news.scored > 0
      ? `All scored signals so far: ${news.scored}, avg 1-day net excess ${fmtPct(news.avgNet)}, ${Math.round(news.hitRate * 100)}% winners`
      : "No signal scored yet (each needs 1 day of prices)");
  }
  lines.push("", "📡 <b>Data</b>");
  const failedRuns = runs.filter((r) => r.status === "fail").length;
  lines.push(`Collector runs: ${runs.length} (${failedRuns} with failures) · open interest snapshots: ${oiCount}/1680`);
  lines.push(failingNow.length ? `Failing now: ${failingNow.slice(0, 5).map((f) => escapeHtml(f.dataset)).join(", ")}` : "All checks pass now ✅");
  lines.push("", "🔬 <b>Research</b>");
  const candidates = experiments.filter((e) => e.verdict === "candidate" || e.verdict === "holdout_passed").length;
  lines.push(`${experiments.length} validation run(s) this week · ${candidates} candidate(s) · ${totalTrials} trials in the ledger`);
  lines.push("", dashboard);
  return lines.join("\n");
}

async function sendWeekly(env, now) {
  const weekAgo = isoUtc(new Date(now.getTime() - 7 * 24 * HOUR));
  const [eqNow, eqThen, decisions, state, recon, runs, latest, oi, experiments, trials, carry, newsWeek, newsAll, revEq, revWeek] = await env.DB.batch([
    env.DB.prepare("SELECT equity FROM paper_equity ORDER BY ts DESC LIMIT 1"),
    env.DB.prepare("SELECT equity FROM paper_equity WHERE ts <= ? ORDER BY ts DESC LIMIT 1").bind(weekAgo),
    env.DB.prepare("SELECT kind FROM paper_decisions WHERE decided_at >= ?").bind(weekAgo),
    env.DB.prepare("SELECT key, value FROM paper_state"),
    env.DB.prepare("SELECT summary FROM paper_reconcile ORDER BY computed_at DESC LIMIT 1"),
    env.DB.prepare("SELECT status FROM collector_runs WHERE run_at >= ?").bind(weekAgo),
    env.DB.prepare("SELECT run_at FROM collector_runs ORDER BY run_at DESC LIMIT 1"),
    env.DB.prepare("SELECT count(*) AS n FROM oi_snapshots WHERE ts >= ?").bind(weekAgo),
    env.DB.prepare("SELECT verdict FROM experiments WHERE created_at >= ?").bind(weekAgo),
    env.DB.prepare("SELECT coalesce(sum(trials), 0) AS n FROM experiments"),
    env.DB.prepare("SELECT equity, model_equity, rebalances FROM carry_sim ORDER BY ts DESC LIMIT 1"),
    env.DB.prepare("SELECT count(*) AS runs, coalesce(sum(signals), 0) AS signals, coalesce(sum(cost_usd), 0) AS cost FROM news_runs WHERE ts >= ?").bind(weekAgo),
    env.DB.prepare("SELECT count(net_1d) AS scored, avg(net_1d) AS avg_net, avg(CASE WHEN net_1d > 0 THEN 1.0 ELSE 0.0 END) AS hit FROM news_signals WHERE net_1d IS NOT NULL"),
    env.DB.prepare("SELECT equity FROM reversal_equity ORDER BY ts DESC LIMIT 1"),
    env.DB.prepare("SELECT count(*) AS weeks, (SELECT ret FROM reversal_weeks WHERE complete = 1 ORDER BY decided DESC LIMIT 1) AS last_ret FROM reversal_weeks WHERE complete = 1"),
  ]);
  const st = Object.fromEntries(state.results.map((r) => [r.key, JSON.parse(r.value)]));
  const lastRun = latest.results[0]?.run_at;
  const failingNow = lastRun
    ? (await env.DB.prepare("SELECT dataset FROM check_results WHERE run_at = ? AND status = 'fail'").bind(lastRun).all()).results
    : [];
  await telegram(env, formatWeekly({
    now, timeZone: env.TIMEZONE, equityNow: eqNow.results[0]?.equity ?? null, equityWeekAgo: eqThen.results[0]?.equity ?? null,
    decisions: decisions.results, openTrade: st.open_trade ?? null, halted: st.halted ?? null,
    recon: recon.results[0] ? JSON.parse(recon.results[0].summary) : null, runs: runs.results, failingNow,
    oiCount: oi.results[0].n, experiments: experiments.results, totalTrials: trials.results[0].n, dashboard: env.DASHBOARD_URL,
    carry: carry.results[0] ?? null,
    reversal: { equity: revEq.results[0]?.equity ?? null, weeks: revWeek.results[0].weeks, lastWeek: revWeek.results[0].last_ret },
    news: { ...newsWeek.results[0], scored: newsAll.results[0].scored, avgNet: newsAll.results[0].avg_net, hitRate: newsAll.results[0].hit },
  }));
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
  try {
    await carryTick(env, hour, meta, ctxs);      // reuse the same Hyperliquid data for the carry simulation
  } catch (err) {
    await telegram(env, `🚨 Carry simulation tick failed: ${escapeHtml(err.message)}`).catch(() => {});
  }
  return rows.length;
}

/** One hour of the carry shadow simulation (no real orders): see src/carry.js. */
async function carryTick(env, hour, meta, ctxs) {
  const symbols = CARRY_COINS.map((c) => `${c}/USD`).join(",");
  const q = await fetch(`https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes?symbols=${encodeURIComponent(symbols)}`);
  if (!q.ok) throw new Error(`Alpaca quotes answered ${q.status}`);
  const prices = priceSnapshot((await q.json()).quotes || {}, meta, ctxs);
  if (Object.keys(prices).length < CARRY_COINS.length / 2) throw new Error("too few coins priced");
  const stored = await env.DB.prepare("SELECT value FROM scheduler_state WHERE key = 'carry_sim'").first("value");
  const state = stored ? JSON.parse(stored) : carryInitial();
  if (stored && state.lastHour === isoUtc(hour)) return;            // already ran this hour
  const r = carryStep(state, prices);
  r.state.lastHour = isoUtc(hour);
  await env.DB.batch([
    env.DB.prepare("INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) VALUES ('carry_sim', ?, ?)")
      .bind(JSON.stringify(r.state), isoUtc(hour)),
    env.DB.prepare("INSERT OR REPLACE INTO carry_sim (ts, equity, model_equity, funding_cum, fees_cum, rebalances, " +
                   "max_margin_use, basis, funding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(isoUtc(hour), r.equity, r.state.modelEquity, r.state.fundingCum, r.state.feesCum, r.state.rebalances || 0,
            r.state.maxMarginUse || 0, JSON.stringify(r.basis),
            JSON.stringify(Object.fromEntries(Object.entries(prices).map(([c, p]) => [c, p.funding])))),
  ]);
  for (const e of r.events) {
    if (e.kind === "opened") {
      await telegram(env, `🧮 <b>Carry simulation started</b> (shadow, no real orders): long spot on Alpaca + short perp on ` +
                          `Hyperliquid in ${e.coins.length} coins, $100,000 virtual. Results: ${env.DASHBOARD_URL}/#/paper`);
    } else if (e.kind === "rebalanced") {
      await telegram(env, `🧮 Carry simulation: ${e.coin} pump used ${(e.marginUse * 100).toFixed(0)}% of its perp margin; ` +
                          "pair closed and reopened at current prices (fees paid again).");
    }
  }
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

// --- collector kick ----------------------------------------------------------------
// GitHub often skips scheduled runs for hours. With a token (GITHUB_DISPATCH_TOKEN: fine-grained,
// this repo only, Actions read/write), the Worker starts the collector itself whenever the last run
// is older than KICK_AFTER_MINUTES and none is queued or running. GitHub's own schedule stays as a backup.

const KICK_AFTER_MINUTES = 50;
const ACTIVE = new Set(["queued", "in_progress", "waiting", "pending", "requested"]);

export function shouldKick(lastRunAt, now, recentRuns) {
  if (recentRuns.some((r) => ACTIVE.has(r.status))) return false;
  if (!lastRunAt) return true;
  return now.getTime() - parseTs(lastRunAt) > KICK_AFTER_MINUTES * 60 * 1000;
}

export async function kickCollector(env, now, http = fetch) {
  const api = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/collect.yml`;
  const headers = { Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
                    "User-Agent": "trader-scheduler", "X-GitHub-Api-Version": "2022-11-28" };
  const last = await env.DB.prepare("SELECT max(run_at) AS last FROM collector_runs").first("last");
  const list = await http(`${api}/runs?per_page=5`, { headers });
  if (!list.ok) throw new Error(`GitHub runs list: HTTP ${list.status}`);
  if (!shouldKick(last, now, (await list.json()).workflow_runs ?? [])) return false;
  const r = await http(`${api}/dispatches`, { method: "POST", headers, body: JSON.stringify({ ref: "main" }) });
  if (r.status !== 204) throw new Error(`GitHub dispatch: HTTP ${r.status}`);
  return true;
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
  if (summaryDue(now, env) && env.GITHUB_DISPATCH_TOKEN && briefingDue(localTime(now, env.TIMEZONE))) jobs.push("briefing");
  if (weeklyDue(now, env)) jobs.push("weekly");
  if (env.GITHUB_DISPATCH_TOKEN) jobs.push("kick");
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
    briefing: ["market briefing", () => dispatchReport(env, fetch, { kind: "news", query: "", when: briefingDue(localTime(now, env.TIMEZONE)) })],
    weekly: ["weekly report", () => sendWeekly(env, now)],
    kick: ["collector kick", () => kickCollector(env, now)],
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
    const report = parseReport(msg.text);
    if (report) {
      reply = await reportCommand(env, fetch, env.ALPACA_API_KEY ? makeAlpaca(env, fetch) : null, report);
    } else {
      reply = env.ALPACA_API_KEY ? await handleCommand(paperContext(env, new Date()), msg.text) : "Paper trading isn't configured.";
    }
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
