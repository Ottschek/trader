// Run: npm test (in cloudflare/scheduler)
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatSummary, formatWeekly, isoUtc, jobsAt, parseTs, snapshotRows, summaryDue, watchdogAction, weeklyDue } from "../src/index.js";

const env = { TIMEZONE: "Europe/Vienna", SUMMARY_TIMES: "08:00,22:30" };
const at = (iso) => new Date(iso);

test("summaries fire at 08:00 and 22:30 Vienna in summer (UTC+2) and winter (UTC+1)", () => {
  assert.equal(summaryDue(at("2026-07-01T06:00:00Z"), env), true);
  assert.equal(summaryDue(at("2026-07-01T20:30:00Z"), env), true);
  assert.equal(summaryDue(at("2026-07-01T07:00:00Z"), env), false);
  assert.equal(summaryDue(at("2026-01-15T07:00:00Z"), env), true);
  assert.equal(summaryDue(at("2026-01-15T21:30:00Z"), env), true);
  assert.equal(summaryDue(at("2026-01-15T20:30:00Z"), env), false);
});

test("timestamps match the Python format and Python microseconds parse", () => {
  assert.equal(isoUtc(at("2026-10-16T22:00:00.000Z")), "2026-10-16T22:00:00+00:00");
  assert.equal(parseTs("2026-10-16T22:00:57.927120+00:00"), Date.parse("2026-10-16T22:00:57.927Z"));
});

test("snapshot rows pick our coins from Hyperliquid's response", () => {
  const meta = { universe: [{ name: "BTC" }, { name: "XYZ" }, { name: "ETH" }] };
  const ctx = (oi) => ({ openInterest: oi, funding: "0.0000125", premium: null, markPx: "1", oraclePx: "1", dayNtlVlm: "5" });
  const rows = snapshotRows(meta, [ctx("10"), ctx("99"), ctx("20")], ["BTC", "ETH", "NOPE"], "h", "t");
  assert.deepEqual(rows.map((r) => [r.coin, r.open_interest]), [["BTC", 10], ["ETH", 20]]);
  assert.equal(rows[0].funding_rate, 0.0000125);
  assert.equal(rows[0].premium, null);
});

test("watchdog alerts once when runs stop and once when they resume", () => {
  assert.equal(watchdogAction(7, 6, false), "down");
  assert.equal(watchdogAction(8, 6, true), null);     // already alerted: stay quiet
  assert.equal(watchdogAction(0.5, 6, true), "up");
  assert.equal(watchdogAction(0.5, 6, false), null);
  assert.equal(watchdogAction(Infinity, 6, false), "down");
});

test("summary text reports runs, lag, failures and escapes HTML", () => {
  const text = formatSummary({
    now: at("2026-10-18T06:00:00Z"), timeZone: "Europe/Vienna",
    runs: [{ status: "ok", rows_added: 100 }, { status: "fail", rows_added: 5 }],
    lastRunAt: "2026-10-18T04:30:00.123456+00:00",
    problems: [{ status: "fail", check_name: "staleness", dataset: "a<b>", detail: "x & y" }],
    datasets: [{ kind: "bars", n: 60, rows: 1234567 }],
    oiCount: 200, coins: 10, dashboard: "https://example.pages.dev",
  });
  assert.match(text, /08:00/);
  assert.match(text, /Collector runs \(24 h\): 2 · 1 with failures · \+105 rows/);
  assert.match(text, /Last collector run: 1\.5 h ago/);
  assert.match(text, /a&lt;b&gt;: x &amp; y/);
  assert.match(text, /Open interest snapshots \(24 h\): 200\/240/);
});

test("one trigger: :00 runs snapshot + watchdog, summaries only at their times", () => {
  assert.deepEqual(jobsAt(at("2026-07-01T13:00:00Z"), env), ["snapshot", "watchdog"]);
  assert.deepEqual(jobsAt(at("2026-07-01T13:30:00Z"), env), []);
  assert.deepEqual(jobsAt(at("2026-07-01T06:00:00Z"), env), ["snapshot", "watchdog", "summary"]); // 08:00 Vienna
  assert.deepEqual(jobsAt(at("2026-07-01T20:30:00Z"), env), ["summary"]);                         // 22:30 Vienna
});

test("weekly report fires Sunday 20:00 Vienna only", () => {
  const w = { ...env, WEEKLY_REPORT: "Sun 20:00" };
  assert.equal(weeklyDue(at("2026-10-25T19:00:00Z"), w), true);    // Sun 20:00 CET (after DST ends)
  assert.equal(weeklyDue(at("2026-10-18T18:00:00Z"), w), true);    // Sun 20:00 CEST
  assert.equal(weeklyDue(at("2026-10-19T18:00:00Z"), w), false);   // Monday
  assert.deepEqual(jobsAt(at("2026-10-18T18:00:00Z"), w), ["snapshot", "watchdog", "weekly"]);
});

test("weekly report text", () => {
  const text = formatWeekly({
    now: at("2026-10-25T19:00:00Z"), timeZone: "Europe/Vienna", equityNow: 101_200, equityWeekAgo: 100_000,
    decisions: [{ kind: "entry" }, { kind: "no_trade" }], openTrade: null, halted: null,
    recon: { tracking_difference: 0.001, mismatches: [], avg_slippage_bps: 5.5 },
    runs: [{ status: "ok" }, { status: "fail" }], failingNow: [], oiCount: 1675,
    experiments: [{ verdict: "rejected" }], totalTrials: 29, dashboard: "https://example.pages.dev",
  });
  assert.match(text, /Equity 101,200 USD \(\+1\.20% this week\)/);
  assert.match(text, /1 entry, 1 no-trade/);
  assert.match(text, /tracking \+0\.10%, 0 decision mismatch/);
  assert.match(text, /Collector runs: 2 \(1 with failures\)/);
  assert.match(text, /All checks pass now/);
  assert.match(text, /29 trials in the ledger/);
  assert.doesNotMatch(text, /news analyst/);
});

test("weekly report includes the news analyst once it has run", () => {
  const base = {
    now: at("2026-10-25T19:00:00Z"), timeZone: "Europe/Vienna", equityNow: null, equityWeekAgo: null,
    decisions: [], openTrade: null, halted: null, recon: null, runs: [], failingNow: [], oiCount: 0,
    experiments: [], totalTrials: 0, dashboard: "https://example.pages.dev",
  };
  const text = formatWeekly({ ...base, news: { runs: 28, signals: 5, cost: 1.234, scored: 4, avgNet: 0.0031, hitRate: 0.75 },
                              reversal: { equity: 1.012, weeks: 2, lastWeek: -0.004 } });
  assert.match(text, /\+1\.20% since 19 Oct over 2 finished week\(s\) · last week -0\.40%/);
  assert.match(text, /28 run\(s\), 5 signal\(s\), \$1\.23 spent/);
  assert.match(text, /4, avg 1-day net excess \+0\.31%, 75% winners/);
  assert.match(formatWeekly({ ...base, news: { runs: 2, signals: 0, cost: 0.1, scored: 0, avgNet: null, hitRate: null } }),
               /No signal scored yet/);
});

test("collector kick: only when the last run is old and nothing is queued", async () => {
  const { shouldKick, kickCollector } = await import("../src/index.js");
  const now = at("2026-10-19T09:00:00Z");
  assert.equal(shouldKick("2026-10-19T08:20:00Z", now, []), false);           // 40 min ago
  assert.equal(shouldKick("2026-10-19T08:00:00Z", now, []), true);            // 60 min ago
  assert.equal(shouldKick("2026-10-19T05:14:00Z", now, [{ status: "queued" }]), false);
  assert.equal(shouldKick(null, now, [{ status: "completed" }]), true);
  const calls = [];
  const http = async (url, opts = {}) => {
    calls.push([url, opts.method ?? "GET"]);
    return url.endsWith("/dispatches") ? { status: 204 } : { ok: true, json: async () => ({ workflow_runs: [{ status: "completed" }] }) };
  };
  const env = { GITHUB_REPO: "o/r", GITHUB_DISPATCH_TOKEN: "t",
                DB: { prepare: () => ({ first: async () => "2026-10-19T05:14:00Z" }) } };
  assert.equal(await kickCollector(env, now, http), true);
  assert.deepEqual(calls.map((c) => c[1]), ["GET", "POST"]);
  assert.match(calls[1][0], /repos\/o\/r\/actions\/workflows\/collect\.yml\/dispatches$/);
});
