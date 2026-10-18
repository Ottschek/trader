// Paper trading for the SHADOW EXPERIMENT (weekend_gap_sized, frozen in config/paper.yaml).
//
// The Python collector computes position sizes with the strategy code (paper_plans in D1).
// This module decides *whether* to trade, places market-on-open orders on the Alpaca paper
// account, enforces the risk limits, and logs every decision and order to D1.
//
// ctx = { c (config), env, http, store, alpaca, now }

import { btcCloseEndingAt, telegram } from "./clients.js";
import { HOUR, addDays, escapeHtml, isoUtc, localParts, parseTs, zonedToUtc } from "./util.js";

const FINAL = new Set(["filled", "canceled", "expired", "rejected"]);
const TZ = "America/New_York";

export function paperConfig(env) {
  return {
    experiment: env.PAPER_EXPERIMENT,
    label: "SHADOW EXPERIMENT (not a validated candidate)",
    symbols: env.PAPER_SYMBOLS.split(","),
    threshold: Number(env.PAPER_THRESHOLD),
    start: env.PAPER_START,
    gapHours: Number(env.PAPER_WEEKEND_GAP_HOURS),
    maxPos: Number(env.RISK_MAX_POSITION),
    maxDaily: Number(env.RISK_MAX_DAILY_LOSS),
    killDd: Number(env.RISK_KILL_DRAWDOWN),
  };
}

// --- pure helpers (unit-tested) ---------------------------------------------------

/** The 09:00 run: the 08:00-09:00 BTC bar has closed, and market-on-open orders are accepted until 09:28. */
export function isDecisionTime(now) {
  const p = localParts(now, TZ);
  return p.hour === 9 && p.minute < 28;
}

/** Hours from the last stock bar's start to the 08:00 decision bar's start (as in the backtest). */
export function weekendGapHours(today, prevSession) {
  const decisionBar = zonedToUtc(today, "08:00", TZ).getTime();
  const lastBar = zonedToUtc(prevSession.date, prevSession.close, TZ).getTime() - HOUR / 2;
  return (decisionBar - lastBar) / HOUR;
}

/** Whole shares for a target weight, after the risk manager's per-position cap. */
export function shareQty(weight, equity, price, maxPos) {
  const w = Math.min(weight, maxPos);
  return price > 0 && w > 0 ? Math.floor((w * equity) / price) : 0;
}

/** Risk manager: "kill" (drawdown from peak), "daily_stop" (loss since yesterday's close) or null. */
export function riskAction({ equity, lastEquity, peak, maxDaily, killDd }) {
  if (peak > 0 && equity / peak - 1 <= -killDd) return "kill";
  if (lastEquity > 0 && equity / lastEquity - 1 <= -maxDaily) return "daily_stop";
  return null;
}

export function isHalted(halt, today) {
  return Boolean(halt) && (halt.until == null || halt.until >= today);
}

export function parseCommand(text) {
  const word = String(text || "").trim().split(/\s+/)[0].toLowerCase();
  return word.split("@")[0];   // "/status@MyBot" -> "/status"
}

const pct = (x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`;

function orderRow(o, decisionId, refPrice, now, submittedAt) {
  return {
    id: o.id, client_order_id: o.client_order_id, decision_id: decisionId,
    submitted_at: submittedAt || o.submitted_at || isoUtc(now), symbol: o.symbol, side: o.side,
    qty: Number(o.qty), order_type: o.type || o.order_type, time_in_force: o.time_in_force,
    ref_price: refPrice ?? null, status: o.status, filled_qty: Number(o.filled_qty || 0),
    filled_avg_price: o.filled_avg_price ? Number(o.filled_avg_price) : null,
    filled_at: o.filled_at || null, updated_at: isoUtc(now),
  };
}

async function notify(ctx, text) {
  await telegram(ctx.env, ctx.http, `🧪 <i>Shadow paper</i> · ${text}`);
}

// --- the 09:00 decision -----------------------------------------------------------

export async function morning(ctx) {
  const { c, store, alpaca, now } = ctx;
  const today = localParts(now, TZ).date;
  if (today < c.start) return { skipped: "before the experiment start" };
  if ((await store.getState("last_morning")) === today) return { skipped: "already decided today" };
  const calendar = await alpaca.calendar(addDays(today, -10), today);
  if (!calendar.some((d) => d.date === today)) return { skipped: "market closed today" };
  await store.setState("last_morning", today, isoUtc(now));

  const done = [];
  const open = await store.getState("open_trade");
  if (open && open.session_date < today) {
    done.push(await exitTrade(ctx, open, today));
    await store.setState("open_trade", null, isoUtc(now));
  }

  const prev = calendar.filter((d) => d.date < today).pop();
  const gap = weekendGapHours(today, prev);
  if (gap <= c.gapHours) return { done, entry: "not after a weekend or holiday" };

  const id = `${today}-entry`;
  const halt = await store.getState("halted");
  const refEnd = zonedToUtc(prev.date, prev.close, TZ);
  const nowEnd = zonedToUtc(today, "09:00", TZ);
  const base = { label: c.label, gap_hours: Number(gap.toFixed(1)), threshold: c.threshold,
                 btc_ref_time: isoUtc(refEnd), btc_now_time: isoUtc(nowEnd) };
  if (isHalted(halt, today)) {
    await store.addDecision({ id, decided_at: isoUtc(now), session_date: today, kind: "skipped",
                              detail: { ...base, reason: `halted: ${halt.reason}` }, experiment: c.experiment });
    await notify(ctx, `Weekend decision skipped: trading is halted (${escapeHtml(halt.reason)}). Send /resume to restart.`);
    return { done, entry: "halted" };
  }

  const btcRef = await btcCloseEndingAt(ctx.http, refEnd);
  const btcNow = await btcCloseEndingAt(ctx.http, nowEnd);
  const move = btcNow / btcRef - 1;
  const detail = { ...base, btc_ref: btcRef, btc_now: btcNow, move: Number(move.toFixed(6)) };

  if (move <= c.threshold) {
    await store.addDecision({ id, decided_at: isoUtc(now), session_date: today, kind: "no_trade", detail, experiment: c.experiment });
    await notify(ctx, `No trade today: BTC moved ${pct(move)} since the last stock close (needs more than ${pct(c.threshold)}).`);
    return { done, entry: "no_trade", move };
  }

  const plan = await store.latestPlan();
  if (!plan || parseTs(plan.as_of) < refEnd.getTime() - HOUR) {
    await store.addDecision({ id, decided_at: isoUtc(now), session_date: today, kind: "skipped",
                              detail: { ...detail, reason: "no plan computed from the last session", plan_as_of: plan?.as_of ?? null },
                              experiment: c.experiment });
    await notify(ctx, `⚠️ BTC moved ${pct(move)}, but there is no fresh position plan (the collector hasn't run since the last close). Skipped.`);
    return { done, entry: "no_plan" };
  }

  const equity = Number((await alpaca.account()).equity);
  const orders = [];
  for (const symbol of c.symbols) {
    const weight = plan.sizes[symbol];
    if (!weight) continue;
    let price = null;
    try { price = await alpaca.latestTrade(symbol); } catch { /* fall back to the plan's last close */ }
    price = price || plan.ref_prices[symbol];
    const qty = shareQty(weight, equity, price, c.maxPos);
    if (qty < 1) continue;
    const o = await alpaca.submitOrder({ symbol, qty: String(qty), side: "buy", type: "market",
                                         time_in_force: "opg", client_order_id: `${id}-${symbol}` });
    await store.addOrder(orderRow(o, id, price, now));
    orders.push({ symbol, qty, strategy_weight: weight, weight: Math.min(weight, c.maxPos), price });
  }
  await store.addDecision({ id, decided_at: isoUtc(now), session_date: today, kind: "entry",
                            detail: { ...detail, plan_as_of: plan.as_of, equity, orders }, experiment: c.experiment });
  await store.setState("open_trade", { session_date: today, decision_id: id, symbols: orders.map((o) => o.symbol) }, isoUtc(now));
  const lines = orders.map((o) => `• BUY ${o.qty} ${o.symbol} (~${(o.weight * 100).toFixed(1)}% of equity)`).join("\n");
  await notify(ctx, `<b>Entry</b>: BTC moved ${pct(move)} over the weekend (threshold ${pct(c.threshold)}). ` +
                    `Market-on-open orders for 09:30:\n${lines}\nExit: next trading morning.`);
  return { done, entry: "entry", orders };
}

async function exitTrade(ctx, open, today) {
  const { store, alpaca, now, c } = ctx;
  const id = `${today}-exit`;
  const held = Object.fromEntries((await alpaca.positions()).map((p) => [p.symbol, Number(p.qty)]));
  const sells = [];
  for (const o of await store.ordersForDecision(open.decision_id)) {
    const qty = Math.min(Number(o.filled_qty || 0), held[o.symbol] || 0);
    if (qty < 1) continue;
    const sell = await alpaca.submitOrder({ symbol: o.symbol, qty: String(qty), side: "sell", type: "market",
                                            time_in_force: "opg", client_order_id: `${id}-${o.symbol}` });
    await store.addOrder(orderRow(sell, id, null, now));
    sells.push({ symbol: o.symbol, qty });
  }
  await store.addDecision({ id, decided_at: isoUtc(now), session_date: today, kind: "exit",
                            detail: { label: c.label, entry_decision: open.decision_id, orders: sells }, experiment: c.experiment });
  await notify(ctx, `<b>Exit</b> at today's open:\n${sells.map((s) => `• SELL ${s.qty} ${s.symbol}`).join("\n") || "nothing was filled"}`);
  return { exit: sells };
}

// --- every run: orders, equity, risk ------------------------------------------------

export async function refreshOrders(ctx) {
  const { store, alpaca, now } = ctx;
  for (const o of await ctx.store.openOrders()) {
    const a = await alpaca.getOrder(o.id);
    await store.addOrder(orderRow(a, o.decision_id, o.ref_price, now, o.submitted_at));
    if (a.status === "filled" && o.status !== "filled") {
      const px = Number(a.filled_avg_price);
      const slip = o.ref_price ? ` (sizing price ${o.ref_price}, ${pct(px / o.ref_price - 1)})` : "";
      await notify(ctx, `Filled ${a.side.toUpperCase()} ${Number(a.filled_qty)} ${a.symbol} @ ${px.toFixed(2)}${slip}`);
    } else if (FINAL.has(a.status) && a.status !== "filled" && o.status !== a.status) {
      await notify(ctx, `⚠️ Order ${a.side} ${a.qty} ${a.symbol} ended as <b>${a.status}</b>.`);
    }
  }
}

export async function equitySnapshot(ctx) {
  const { store, alpaca, now } = ctx;
  const [acct, positions] = await Promise.all([alpaca.account(), alpaca.positions()]);
  const equity = Number(acct.equity);
  await store.addEquity({
    ts: isoUtc(now), equity, cash: Number(acct.cash), last_equity: Number(acct.last_equity),
    positions: positions.map((p) => ({ symbol: p.symbol, qty: Number(p.qty), market_value: Number(p.market_value),
                                       unrealized_pl: Number(p.unrealized_pl) })),
  });
  const peak = (await store.getState("peak_equity")) || 0;
  if (equity > peak) await store.setState("peak_equity", equity, isoUtc(now));
}

export async function riskCheck(ctx) {
  const { c, store, alpaca, now } = ctx;
  const acct = await alpaca.account();
  const equity = Number(acct.equity);
  const peak = Math.max((await store.getState("peak_equity")) || 0, equity);
  const action = riskAction({ equity, lastEquity: Number(acct.last_equity), peak, maxDaily: c.maxDaily, killDd: c.killDd });
  if (!action) return null;
  const today = localParts(now, TZ).date;
  if (isHalted(await store.getState("halted"), today)) return null;   // already acted
  const halt = action === "kill"
    ? { reason: `kill switch: drawdown ${pct(equity / peak - 1)} from peak`, until: null }
    : { reason: `daily loss ${pct(equity / Number(acct.last_equity) - 1)}`, until: today };
  await closeAll(ctx, action === "kill" ? "risk_close" : "risk_close", halt);
  return action;
}

/** Close the strategy's positions now (market) or at the next open (if the market is closed), then halt. */
export async function closeAll(ctx, kind, halt) {
  const { c, store, alpaca, now } = ctx;
  const today = localParts(now, TZ).date;
  const id = `${today}-${kind}-${now.getTime()}`;
  const clock = await alpaca.clock();
  const closed = [];
  for (const p of await alpaca.positions()) {
    if (!c.symbols.includes(p.symbol) || Number(p.qty) <= 0) continue;
    const o = clock.is_open
      ? await alpaca.closePosition(p.symbol)
      : await alpaca.submitOrder({ symbol: p.symbol, qty: String(Number(p.qty)), side: "sell", type: "market",
                                   time_in_force: "opg", client_order_id: `${id}-${p.symbol}` });
    await store.addOrder(orderRow(o, id, null, now));
    closed.push({ symbol: p.symbol, qty: Number(p.qty) });
  }
  await store.setState("halted", halt, isoUtc(now));
  await store.setState("open_trade", null, isoUtc(now));
  await store.addDecision({ id, decided_at: isoUtc(now), session_date: today, kind,
                            detail: { label: c.label, reason: halt.reason, closed, market_open: clock.is_open },
                            experiment: c.experiment });
  await notify(ctx, `🚨 <b>Trading halted</b>: ${escapeHtml(halt.reason)}. Closing ${closed.length} position(s)` +
                    `${clock.is_open ? " now" : " at the next open"}. ${halt.until ? "Resumes tomorrow." : "Send /resume to restart."}`);
  return closed;
}

// --- Telegram commands --------------------------------------------------------------

export async function handleCommand(ctx, text) {
  const { c, store, alpaca, now } = ctx;
  const cmd = parseCommand(text);
  if (cmd === "/stop") {
    const closed = await closeAll(ctx, "manual_stop", { reason: "stopped from Telegram", until: null });
    return `Stopped. ${closed.length} position(s) being closed. Send /resume to restart.`;
  }
  if (cmd === "/resume") {
    await store.setState("halted", null, isoUtc(now));
    return "Resumed. The next weekend decision runs at 09:00 New York on the first trading day after a weekend.";
  }
  if (cmd === "/status") {
    const [acct, positions, halt, open, last] = await Promise.all([
      alpaca.account(), alpaca.positions(), store.getState("halted"), store.getState("open_trade"), store.lastDecision(),
    ]);
    const equity = Number(acct.equity), lastEq = Number(acct.last_equity);
    const mine = positions.filter((p) => c.symbols.includes(p.symbol));
    return [
      `🧪 <b>${c.label}</b>`,
      `Equity ${equity.toLocaleString("en", { maximumFractionDigits: 0 })} USD (${pct(equity / lastEq - 1)} today)`,
      `Positions: ${mine.length ? mine.map((p) => `${p.symbol} ${Number(p.qty)} (${Number(p.unrealized_pl).toFixed(0)} USD)`).join(", ") : "none"}`,
      `Open trade: ${open ? `entered ${open.session_date}` : "none"}`,
      `Halted: ${isHalted(halt, localParts(now, TZ).date) ? escapeHtml(halt.reason) : "no"}`,
      `Last decision: ${last ? `${last.session_date} ${last.kind}` : "none yet"}`,
    ].join("\n");
  }
  return "Commands: /status · /stop (close positions and halt) · /resume";
}
