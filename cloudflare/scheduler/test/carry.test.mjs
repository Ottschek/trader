import { test } from "node:test";
import assert from "node:assert/strict";
import { MARGIN, PERP_FEE, SPOT_FEE, START_EQUITY, closeValue, initialState, marginUse, openPair, priceSnapshot, step } from "../src/carry.js";

const P = (o = {}) => ({ spotBid: 99.9, spotAsk: 100.1, perpBid: 100.0, perpAsk: 100.2, mark: 100.1, oracle: 100.0,
                         funding: 0.00002, premium: 0.0005, ...o });

test("opening a pair pays both fees and posts margin", () => {
  const { position, fees } = openPair(P(), 1250);
  assert.ok(Math.abs(position.qty * 100.1 - 1000) < 1e-9);        // $1000 notional, $250 margin
  assert.ok(Math.abs(position.margin - 250) < 1e-9);
  assert.ok(Math.abs(fees - (1000 * SPOT_FEE + position.qty * 100.0 * PERP_FEE)) < 1e-9);
});

test("price moves cancel out; only spreads, fees and basis remain", () => {
  const { position } = openPair(P(), 1250);
  const up = P({ spotBid: 149.9, spotAsk: 150.1, perpBid: 150.0, perpAsk: 150.2, mark: 150.1 });
  const v0 = closeValue(position, P()), v1 = closeValue(position, up);
  assert.ok(Math.abs(v1 - v0) < 2.5);                             // a 50% rally barely moves the hedged pair
});

test("first step opens equal pairs, later steps collect funding", () => {
  const prices = { BTC: P(), ETH: P() };
  const a = step(initialState(), prices);
  assert.deepEqual(Object.keys(a.state.positions), ["BTC", "ETH"]);
  assert.ok(a.equity < START_EQUITY && a.equity > START_EQUITY * 0.99);   // fees + spread paid
  const b = step(a.state, prices);
  const expected = 2 * a.state.positions.BTC.qty * 100.0 * 0.00002;
  assert.ok(Math.abs(b.state.fundingCum - expected) < 1e-9);
  assert.ok(b.state.modelEquity > a.state.modelEquity);
});

test("a big pump triggers a margin rebalance instead of a liquidation", () => {
  const a = step(initialState(), { BTC: P() });
  const pump = P({ spotBid: 124.9, spotAsk: 125.1, perpBid: 125.0, perpAsk: 125.2, mark: 125.1 });   // +25%
  assert.ok(marginUse(a.state.positions.BTC, pump) >= 0.8);
  const b = step(a.state, { BTC: pump });
  assert.equal(b.state.rebalances, 1);
  assert.equal(b.events[0].kind, "rebalanced");
  assert.ok(Math.abs(b.state.positions.BTC.perpEntry - 125.0) < 1e-9);    // re-hedged at the new price
  assert.ok(marginUse(b.state.positions.BTC, pump) < 0.01);              // only the bid-to-mark sliver
});

test("price snapshot joins Alpaca quotes with Hyperliquid contexts", () => {
  const snap = priceSnapshot(
    { "BTC/USD": { ap: 101, bp: 100 } },
    { universe: [{ name: "ETH" }, { name: "BTC" }] },
    [{}, { impactPxs: ["100.5", "100.7"], markPx: "100.6", oraclePx: "100.4", funding: "0.0000125", premium: "0.0002" }],
    ["BTC", "SOL"],
  );
  assert.deepEqual(Object.keys(snap), ["BTC"]);
  assert.equal(snap.BTC.perpBid, 100.5);
  assert.equal(snap.BTC.funding, 0.0000125);
  assert.ok(MARGIN === 0.25);
});
