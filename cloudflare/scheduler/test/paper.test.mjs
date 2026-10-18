// Scenario tests for paper trading: the real logic in src/paper.js against a fake broker,
// fake D1 store and fake Coinbase/Telegram. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  closeAll, handleCommand, isDecisionTime, morning, refreshOrders, riskAction, riskCheck, shareQty, weekendGapHours,
} from "../src/paper.js";
import worker from "../src/index.js";

const C = {
  experiment: "shadow-test", label: "SHADOW EXPERIMENT (not a validated candidate)",
  symbols: ["COIN", "MSTR", "MARA", "RIOT"], threshold: 0.06, start: "2026-10-19",
  gapHours: 40, maxPos: 0.10, maxDaily: 0.03, killDd: 0.15,
};
const MON_0900 = new Date("2026-10-19T13:00:00Z");   // 09:00 New York (EDT)
const TUE_0900 = new Date("2026-10-20T13:00:00Z");
const FRI_CLOSE = Date.parse("2026-10-16T20:00:00Z");  // 16:00 New York

const SESSIONS = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16",
                  "2026-10-19", "2026-10-20", "2026-10-21"].map((date) => ({ date, open: "09:30", close: "16:00" }));

function fakeStore(plan) {
  const state = new Map(), decisions = [], orders = new Map(), equity = [];
  return {
    state, decisions, orders, equity,
    getState: async (k) => (state.has(k) ? state.get(k) : null),
    setState: async (k, v) => { state.set(k, v); },
    latestPlan: async () => plan,
    addDecision: async (d) => { decisions.push(d); },
    lastDecision: async () => decisions.at(-1) ?? null,
    addOrder: async (o) => { orders.set(o.id, { ...o }); },
    openOrders: async () => [...orders.values()].filter((o) => !["filled", "canceled", "expired", "rejected"].includes(o.status)),
    ordersForDecision: async (id) => [...orders.values()].filter((o) => o.decision_id === id),
    addEquity: async (e) => { equity.push(e); },
  };
}

function fakeAlpaca({ sessions = SESSIONS, equity = 100_000, lastEquity = 100_000, open = false, prices = {} } = {}) {
  const submitted = [], closedPositions = [], held = new Map();
  let n = 0;
  const a = {
    submitted, closedPositions, held, equity, lastEquity, open,
    account: async () => ({ equity: String(a.equity), last_equity: String(a.lastEquity), cash: String(a.equity) }),
    clock: async () => ({ is_open: a.open }),
    positions: async () => [...held].map(([symbol, qty]) => ({ symbol, qty: String(qty), market_value: "0", unrealized_pl: "0" })),
    calendar: async (start, end) => sessions.filter((s) => s.date >= start && s.date <= end),
    latestTrade: async (s) => prices[s] ?? null,
    submitOrder: async (o) => {
      const order = { ...o, id: `ord-${++n}`, status: "accepted", filled_qty: "0", type: o.type };
      submitted.push(order);
      return order;
    },
    getOrder: async (id) => submitted.find((o) => o.id === id),
    closePosition: async (symbol) => {
      closedPositions.push(symbol);
      const order = { id: `ord-${++n}`, client_order_id: `close-${symbol}`, symbol, side: "sell", qty: String(held.get(symbol)),
                      type: "market", time_in_force: "day", status: "accepted" };
      submitted.push(order);
      return order;
    },
    fill: (price) => {   // fill every open order at `price`
      for (const o of submitted) {
        if (o.status === "filled") continue;
        Object.assign(o, { status: "filled", filled_qty: o.qty, filled_avg_price: String(price), filled_at: "2026-10-19T13:30:00Z" });
        held.set(o.symbol, (held.get(o.symbol) || 0) + (o.side === "buy" ? 1 : -1) * Number(o.qty));
      }
    },
  };
  return a;
}

function fakeHttp({ btcRef = 60_000, btcNow = 60_000 } = {}) {
  const messages = [];
  const http = async (url, opts = {}) => {
    if (url.startsWith("https://api.telegram.org/")) {
      messages.push(JSON.parse(opts.body).text);
      return new Response("{}", { status: 200 });
    }
    if (url.startsWith("https://api.exchange.coinbase.com/")) {
      const start = Date.parse(new URL(url).searchParams.get("start"));
      const close = start === FRI_CLOSE - 3600_000 ? btcRef : btcNow;
      return new Response(JSON.stringify([[start / 1000, 0, 0, 0, close, 1]]), { status: 200 });
    }
    throw new Error(`unexpected request ${url}`);
  };
  return { http, messages };
}

const PLAN = {
  as_of: "2026-10-16T19:30:00+00:00",
  sizes: { COIN: 0.131514, MSTR: 0.109654, MARA: 0.108665, RIOT: 0.099099 },
  ref_prices: { COIN: 173.12, MSTR: 136.825, MARA: 7.535, RIOT: 13.74 },
  daily_vol: {},
};

function ctx({ now = MON_0900, plan = PLAN, alpaca = fakeAlpaca(), btcMove = 0.08, store } = {}) {
  const { http, messages } = fakeHttp({ btcRef: 60_000, btcNow: 60_000 * (1 + btcMove) });
  return { c: C, env: { TELEGRAM_BOT_TOKEN: "t", TELEGRAM_CHAT_ID: "1" }, http, store: store ?? fakeStore(plan),
           alpaca, now, messages };
}

// --- pure helpers ------------------------------------------------------------------

test("decision time is the 09:00 New York run only", () => {
  assert.equal(isDecisionTime(new Date("2026-10-19T13:00:00Z")), true);    // 09:00 EDT
  assert.equal(isDecisionTime(new Date("2026-10-19T13:30:00Z")), false);   // 09:30: too late for opening orders
  assert.equal(isDecisionTime(new Date("2026-01-12T14:00:00Z")), true);    // 09:00 EST
  assert.equal(isDecisionTime(new Date("2026-10-19T12:00:00Z")), false);
});

test("weekend gap matches the backtest rule", () => {
  assert.equal(weekendGapHours("2026-10-19", { date: "2026-10-16", close: "16:00" }), 64.5);
  assert.equal(weekendGapHours("2026-10-20", { date: "2026-10-19", close: "16:00" }), 16.5);
});

test("share counts respect the 10% position cap", () => {
  assert.equal(shareQty(0.131514, 100_000, 173.12, 0.10), 57);   // capped at 10% -> 10,000 / 173.12
  assert.equal(shareQty(0.05, 100_000, 50, 0.10), 100);
  assert.equal(shareQty(0.05, 100_000, 0, 0.10), 0);
});

test("risk actions", () => {
  assert.equal(riskAction({ equity: 96_000, lastEquity: 100_000, peak: 100_000, maxDaily: 0.03, killDd: 0.15 }), "daily_stop");
  assert.equal(riskAction({ equity: 84_000, lastEquity: 85_000, peak: 100_000, maxDaily: 0.03, killDd: 0.15 }), "kill");
  assert.equal(riskAction({ equity: 99_000, lastEquity: 100_000, peak: 101_000, maxDaily: 0.03, killDd: 0.15 }), null);
});

// --- weekend scenarios ---------------------------------------------------------------

test("big BTC weekend move: buys at the open, sells the next morning", async () => {
  const alpaca = fakeAlpaca({ prices: { COIN: 175, MSTR: 140, MARA: 7.6, RIOT: 14 } });
  const store = fakeStore(PLAN);
  const mon = ctx({ alpaca, store, btcMove: 0.08 });
  const r = await morning(mon);
  assert.equal(r.entry, "entry");
  assert.deepEqual(alpaca.submitted.map((o) => [o.symbol, o.side, o.qty, o.time_in_force]),
    [["COIN", "buy", "57", "opg"], ["MSTR", "buy", "71", "opg"], ["MARA", "buy", "1315", "opg"], ["RIOT", "buy", "707", "opg"]]);
  assert.equal(store.decisions[0].kind, "entry");
  assert.ok(Math.abs(store.decisions[0].detail.move - 0.08) < 1e-9);
  assert.equal(store.state.get("open_trade").session_date, "2026-10-19");
  assert.match(mon.messages[0], /Entry/);

  alpaca.fill(100);                       // 09:30 opening auction
  await refreshOrders(mon);               // the 10:00 run records fills
  assert.ok([...store.orders.values()].every((o) => o.status === "filled"));

  const tue = ctx({ now: TUE_0900, alpaca, store });
  const r2 = await morning(tue);
  assert.equal(r2.entry, "not after a weekend or holiday");
  const sells = alpaca.submitted.filter((o) => o.side === "sell");
  assert.deepEqual(sells.map((o) => [o.symbol, o.qty, o.time_in_force]),
    [["COIN", "57", "opg"], ["MSTR", "71", "opg"], ["MARA", "1315", "opg"], ["RIOT", "707", "opg"]]);
  assert.equal(store.state.get("open_trade"), null);
  assert.equal(store.decisions.at(-1).kind, "exit");
});

test("a duplicate firing never places a second set of orders", async () => {
  const alpaca = fakeAlpaca();
  const store = fakeStore(PLAN);
  await morning(ctx({ alpaca, store }));
  const again = await morning(ctx({ alpaca, store }));
  assert.equal(again.skipped, "already decided today");
  assert.equal(alpaca.submitted.length, 4);
});

test("small BTC move: no trade, logged and reported", async () => {
  const c = ctx({ btcMove: 0.02 });
  const r = await morning(c);
  assert.equal(r.entry, "no_trade");
  assert.equal(c.alpaca.submitted.length, 0);
  assert.equal(c.store.decisions[0].kind, "no_trade");
  assert.match(c.messages[0], /No trade/);
});

test("stale plan: skip and alert instead of trading on old sizes", async () => {
  const c = ctx({ plan: { ...PLAN, as_of: "2026-10-15T19:30:00+00:00" } });
  const r = await morning(c);
  assert.equal(r.entry, "no_plan");
  assert.equal(c.alpaca.submitted.length, 0);
  assert.match(c.messages[0], /no fresh position plan/);
});

test("Monday holiday: no decision Monday, the weekend decision happens Tuesday", async () => {
  const sessions = SESSIONS.filter((s) => s.date !== "2026-10-19");
  const store = fakeStore(PLAN);
  const mon = await morning(ctx({ alpaca: fakeAlpaca({ sessions }), store }));
  assert.equal(mon.skipped, "market closed today");
  const tue = await morning(ctx({ now: TUE_0900, alpaca: fakeAlpaca({ sessions }), store, btcMove: 0.08 }));
  assert.equal(tue.entry, "entry");
});

test("before the experiment start nothing happens", async () => {
  const c = ctx({ now: new Date("2026-10-16T13:00:00Z") });
  assert.equal((await morning(c)).skipped, "before the experiment start");
});

test("halted: the weekend decision is skipped", async () => {
  const store = fakeStore(PLAN);
  store.state.set("halted", { reason: "stopped from Telegram", until: null });
  const c = ctx({ store });
  assert.equal((await morning(c)).entry, "halted");
  assert.equal(c.alpaca.submitted.length, 0);
});

// --- risk manager ----------------------------------------------------------------------

test("daily loss stop closes positions now and halts for the day", async () => {
  const alpaca = fakeAlpaca({ equity: 96_500, lastEquity: 100_000, open: true });
  alpaca.held.set("COIN", 57);
  alpaca.held.set("SPY", 3);                   // not ours: left alone
  const c = ctx({ alpaca });
  assert.equal(await riskCheck(c), "daily_stop");
  assert.deepEqual(alpaca.closedPositions, ["COIN"]);
  assert.equal(c.store.state.get("halted").until, "2026-10-19");
  assert.equal(await riskCheck(c), null);           // already acted: no repeat
});

test("kill switch halts until /resume", async () => {
  const alpaca = fakeAlpaca({ equity: 84_000, lastEquity: 84_500, open: false });
  alpaca.held.set("MARA", 1000);
  const store = fakeStore(PLAN);
  store.state.set("peak_equity", 100_000);
  const c = ctx({ alpaca, store });
  assert.equal(await riskCheck(c), "kill");
  assert.equal(alpaca.submitted[0].time_in_force, "opg");   // market closed: sell at the next open
  assert.equal(store.state.get("halted").until, null);
});

// --- Telegram commands -------------------------------------------------------------------

test("/stop closes and halts, /resume clears, /status reports", async () => {
  const alpaca = fakeAlpaca({ open: true });
  alpaca.held.set("RIOT", 700);
  const c = ctx({ alpaca });
  assert.match(await handleCommand(c, "/stop"), /Stopped/);
  assert.deepEqual(alpaca.closedPositions, ["RIOT"]);
  assert.equal(c.store.state.get("halted").reason, "stopped from Telegram");
  assert.match(await handleCommand(c, "/status@MyBot"), /Halted: stopped from Telegram/);
  assert.match(await handleCommand(c, "/resume"), /Resumed/);
  assert.match(await handleCommand(c, "/status"), /Halted: no/);
  assert.match(await handleCommand(c, "hello"), /Commands/);
});

test("closeAll with nothing open still halts and logs", async () => {
  const c = ctx({ alpaca: fakeAlpaca({ open: true }) });
  const closed = await closeAll(c, "manual_stop", { reason: "test", until: null });
  assert.deepEqual(closed, []);
  assert.equal(c.store.decisions.at(-1).kind, "manual_stop");
});

// --- webhook ---------------------------------------------------------------------------

test("webhook ignores wrong secrets and other chats", async () => {
  const env = { TELEGRAM_WEBHOOK_SECRET: "s3cret", TELEGRAM_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "t" };
  const post = (path, secretHeader, chatId) => worker.fetch(new Request(`https://w.example${path}`, {
    method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secretHeader },
    body: JSON.stringify({ message: { chat: { id: chatId }, text: "/stop" } }),
  }), env);
  assert.equal((await post("/telegram/wrong", "s3cret", 1)).status, 404);
  assert.equal((await post("/telegram/s3cret", "wrong", 1)).status, 404);
  assert.equal((await worker.fetch(new Request("https://w.example/"), env)).status, 404);
  const stranger = await post("/telegram/s3cret", "s3cret", 999);   // a stranger's /stop: ignored silently
  assert.equal(stranger.status, 200);
});
