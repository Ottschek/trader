import { test } from "node:test";
import assert from "node:assert/strict";
import { briefingDue, coinPair, parseReport, quickSnapshot, reportCommand } from "../src/reports.js";

const COINS = ["BTC", "ETH", "SOL", "DOGE", "BCH"];

test("report commands are recognised in every spelling", () => {
  assert.deepEqual(parseReport("/analise apple"), { cmd: "analise", query: "apple" });
  assert.deepEqual(parseReport("/analyze@MyBot  Bank of America "), { cmd: "analise", query: "Bank of America" });
  assert.deepEqual(parseReport("/news"), { cmd: "news", query: "" });
  assert.equal(parseReport("/status"), null);
  assert.equal(parseReport("hello"), null);
});

test("coin names and tickers map to our pairs", () => {
  assert.equal(coinPair("btc", COINS), "BTC/USD");
  assert.equal(coinPair("Bitcoin", COINS), "BTC/USD");
  assert.equal(coinPair("bitcoin cash", COINS), "BCH/USD");
  assert.equal(coinPair("ETH/USD", COINS), "ETH/USD");
  assert.equal(coinPair("AAPL", COINS), null);
});

function fakeAlpaca() {
  const calls = [];
  return {
    calls,
    data: async (path) => {
      calls.push(path);
      if (path.startsWith("/v2/stocks/snapshots")) return path.includes("AAPL") ? { AAPL: { latestTrade: { p: 210 }, prevDailyBar: { c: 200 } } } : {};
      if (path.startsWith("/v2/stocks/AAPL/bars")) return { bars: [{ l: 150, h: 230 }, { l: 160, h: 220 }] };
      if (path.startsWith("/v1beta3/crypto/us/snapshots")) return { snapshots: { "BTC/USD": { latestTrade: { p: 80000 }, prevDailyBar: { c: 82000 } } } };
      if (path.startsWith("/v1beta3/crypto/us/bars")) return { bars: { "BTC/USD": [{ l: 58000, h: 115000 }] } };
      return {};
    },
  };
}

test("instant snapshot for a ticker, a coin, and none for a company name", async () => {
  const a = fakeAlpaca();
  assert.equal(await quickSnapshot(a, "AAPL", COINS), "🔎 <b>AAPL</b> $210 (+5.0% today) · 1-year range $150–$230");
  assert.match(await quickSnapshot(a, "bitcoin", COINS), /<b>BTC<\/b> \$80,000 \(-2\.4% today\) · 1-year range \$58,000–\$115,000/);
  assert.equal(await quickSnapshot(a, "bank of america", COINS), null);
  assert.ok(a.calls.some((c) => c.includes("feed=sip") && c.includes("adjustment=all")));
});

test("commands start the GitHub report workflow and answer at once", async () => {
  const sent = [];
  const http = async (url, opts) => { sent.push([url, JSON.parse(opts.body)]); return { status: 204 }; };
  const env = { GITHUB_DISPATCH_TOKEN: "t", GITHUB_REPO: "o/r", COINS: COINS.join(",") };
  const reply = await reportCommand(env, http, fakeAlpaca(), { cmd: "analise", query: "AAPL" });
  assert.match(reply, /AAPL.*\nFull report in about 2 minutes/s);
  assert.match(sent[0][0], /actions\/workflows\/report\.yml\/dispatches$/);
  assert.deepEqual(sent[0][1], { ref: "main", inputs: { kind: "analise", query: "AAPL", when: "Latest" } });
  assert.match(await reportCommand(env, http, null, { cmd: "news", query: "" }), /briefing/);
  assert.deepEqual(sent[1][1].inputs, { kind: "news", query: "", when: "Latest" });
  assert.match(await reportCommand(env, http, null, { cmd: "analise", query: "" }), /Which company/);
  assert.match(await reportCommand({ ...env, GITHUB_DISPATCH_TOKEN: "" }, http, null, { cmd: "news", query: "" }), /token/);
});

test("briefings go out at the summary times", () => {
  assert.equal(briefingDue("08:00"), "Morning");
  assert.equal(briefingDue("22:30"), "Evening");
  assert.equal(briefingDue("12:00"), null);
});
