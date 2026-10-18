// Run: npm test (in cloudflare/scheduler)
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatSummary, isoUtc, jobsAt, parseTs, snapshotRows, summaryDue, watchdogAction } from "../src/index.js";

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
