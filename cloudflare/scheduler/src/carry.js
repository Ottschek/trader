// Carry SHADOW SIMULATION: always-on cash-and-carry, no real orders, no wallet.
//
// For each coin: buy spot on Alpaca at the real ask and short the same quantity of the
// Hyperliquid perp at its real impact bid (the price for actually selling into the book),
// paying both venues' taker fees and posting 25% margin for the perp. Every hour:
//   + funding received by the short (on oracle-price notional, at the rate predicted for the
//     hour that just ended)
//   + mark-to-market of both legs at real prices from both venues (cross-venue basis included:
//     spot at Alpaca's bid, perp at Hyperliquid's mark)
//   - if a pump eats MARGIN_REBALANCE of a pair's margin, the pair is closed and reopened at
//     current prices (paying all fees again): the price of not getting liquidated
// A "model" equity grows by the backtest formula (funding - change in premium) x margin factor,
// so the gap between simulated reality and the model is visible.

export const COINS = ["BTC", "ETH", "SOL", "LTC", "DOGE", "AVAX", "LINK", "UNI", "AAVE", "BCH"];
export const START_EQUITY = 100_000;
export const MARGIN = 0.25;              // perp margin as a fraction of notional
export const SPOT_FEE = 0.0025;          // Alpaca crypto taker
export const PERP_FEE = 0.00045;         // Hyperliquid taker
export const MARGIN_REBALANCE = 0.8;     // reopen a pair once losses use 80% of its margin

/** Live prices per coin from Alpaca quotes + Hyperliquid contexts. */
export function priceSnapshot(quotes, meta, ctxs, coins = COINS) {
  const index = new Map(meta.universe.map((u, i) => [u.name, i]));
  const out = {};
  for (const coin of coins) {
    const q = quotes[`${coin}/USD`];
    const i = index.get(coin);
    if (!q || i == null) continue;
    const c = ctxs[i];
    const impact = c.impactPxs || [c.midPx, c.midPx];
    out[coin] = {
      spotBid: Number(q.bp), spotAsk: Number(q.ap),
      perpBid: Number(impact[0]), perpAsk: Number(impact[1]), mark: Number(c.markPx), oracle: Number(c.oraclePx),
      funding: Number(c.funding), premium: c.premium == null ? 0 : Number(c.premium),
    };
  }
  return out;
}

/** Open one pair with `capital` dollars (spot purchase + perp margin). Returns position and fees. */
export function openPair(p, capital) {
  const notional = capital / (1 + MARGIN);
  const qty = notional / p.spotAsk;
  const fees = notional * SPOT_FEE + qty * p.perpBid * PERP_FEE;
  return { position: { qty, spotEntry: p.spotAsk, perpEntry: p.perpBid, margin: notional * MARGIN, capital }, fees };
}

/** Value of a pair if closed now: spot sold at bid, perp bought back at ask, margin returned, fees paid. */
export function closeValue(pos, p) {
  const spot = pos.qty * p.spotBid;
  const perpPnl = pos.qty * (pos.perpEntry - p.perpAsk);
  const fees = spot * SPOT_FEE + pos.qty * p.perpAsk * PERP_FEE;
  return spot + pos.margin + perpPnl - fees;
}

/** Mark-to-market (no exit costs): spot at bid, perp at mark. */
export function markValue(pos, p) {
  return pos.qty * p.spotBid + pos.margin + pos.qty * (pos.perpEntry - p.mark);
}

export function marginUse(pos, p) {
  const loss = Math.max(0, pos.qty * (p.mark - pos.perpEntry));
  return loss / pos.margin;
}

/**
 * One hourly step. state: { positions: {coin: pos}, cash, fundingCum, feesCum, rebalances,
 * modelEquity, last: {coin: {funding, premium}}, maxMarginUse }.
 */
export function step(state, prices) {
  const s = structuredClone(state);
  const events = [];
  const coins = Object.keys(prices);
  // first run: open every pair with an equal share
  if (!s.positions || Object.keys(s.positions).length === 0) {
    s.positions = {};
    const share = s.cash / coins.length;
    for (const coin of coins) {
      const { position, fees } = openPair(prices[coin], share);
      s.positions[coin] = position;
      s.cash -= share;          // capital moved into the pair
      s.cash -= fees;
      s.feesCum += fees;
    }
    s.modelEquity = s.cash + coins.reduce((sum, c) => sum + s.positions[c].capital, 0);
    events.push({ kind: "opened", coins });
  } else {
    let modelReturn = 0;
    for (const coin of coins) {
      const pos = s.positions[coin];
      const p = prices[coin];
      if (!pos) continue;
      const prev = s.last?.[coin];
      if (prev) {
        const funding = pos.qty * p.oracle * prev.funding;      // the hour that just ended
        s.cash += funding;
        s.fundingCum += funding;
        modelReturn += (prev.funding - (p.premium - prev.premium)) / (1 + MARGIN) / coins.length;
      }
      const use = marginUse(pos, p);
      s.maxMarginUse = Math.max(s.maxMarginUse || 0, use);
      if (use >= MARGIN_REBALANCE) {
        const value = closeValue(pos, p);
        const { position, fees } = openPair(p, value);
        s.feesCum += fees + (pos.qty * p.spotBid * SPOT_FEE + pos.qty * p.perpAsk * PERP_FEE);
        s.cash -= fees;
        s.positions[coin] = position;
        s.rebalances = (s.rebalances || 0) + 1;
        events.push({ kind: "rebalanced", coin, marginUse: use });
      }
    }
    s.modelEquity *= 1 + modelReturn;
  }
  s.last = Object.fromEntries(coins.map((c) => [c, { funding: prices[c].funding, premium: prices[c].premium }]));
  const pairs = coins.map((c) => [c, markValue(s.positions[c], prices[c])]);
  const equity = s.cash + pairs.reduce((sum, [, v]) => sum + v, 0);
  const basis = Object.fromEntries(coins.map((c) => [c, prices[c].mark / ((prices[c].spotBid + prices[c].spotAsk) / 2) - 1]));
  return { state: s, equity, events, basis };
}

export function initialState() {
  return { positions: {}, cash: START_EQUITY, fundingCum: 0, feesCum: 0, rebalances: 0, modelEquity: START_EQUITY,
           last: {}, maxMarginUse: 0 };
}
