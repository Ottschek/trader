// Paper-trading storage in D1. Tests use an in-memory fake with the same methods.

export function makeStore(db) {
  return {
    async getState(key) {
      const v = await db.prepare("SELECT value FROM paper_state WHERE key = ?").bind(key).first("value");
      return v == null ? null : JSON.parse(v);
    },
    async setState(key, value, now) {
      await db.prepare("INSERT OR REPLACE INTO paper_state (key, value, updated_at) VALUES (?, ?, ?)")
        .bind(key, JSON.stringify(value), now).run();
    },
    async latestPlan() {
      const r = await db.prepare("SELECT * FROM paper_plans ORDER BY as_of DESC LIMIT 1").first();
      if (!r) return null;
      return { ...r, params: JSON.parse(r.params), sizes: JSON.parse(r.sizes),
               ref_prices: JSON.parse(r.ref_prices), daily_vol: JSON.parse(r.daily_vol) };
    },
    async addDecision(d) {
      await db.prepare("INSERT OR REPLACE INTO paper_decisions (id, decided_at, session_date, kind, detail, experiment) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(d.id, d.decided_at, d.session_date, d.kind, JSON.stringify(d.detail), d.experiment).run();
    },
    async lastDecision() {
      const r = await db.prepare("SELECT * FROM paper_decisions ORDER BY decided_at DESC LIMIT 1").first();
      return r ? { ...r, detail: JSON.parse(r.detail) } : null;
    },
    async addOrder(o) {
      await db.prepare(
        "INSERT OR REPLACE INTO paper_orders (id, client_order_id, decision_id, submitted_at, symbol, side, qty, order_type, " +
        "time_in_force, ref_price, status, filled_qty, filled_avg_price, filled_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      ).bind(o.id, o.client_order_id, o.decision_id, o.submitted_at, o.symbol, o.side, o.qty, o.order_type,
             o.time_in_force, o.ref_price, o.status, o.filled_qty, o.filled_avg_price, o.filled_at, o.updated_at).run();
    },
    async openOrders() {
      return (await db.prepare(
        "SELECT * FROM paper_orders WHERE status NOT IN ('filled', 'canceled', 'expired', 'rejected')",
      ).all()).results;
    },
    async ordersForDecision(decisionId) {
      return (await db.prepare("SELECT * FROM paper_orders WHERE decision_id = ?").bind(decisionId).all()).results;
    },
    async addEquity(e) {
      await db.prepare("INSERT OR REPLACE INTO paper_equity (ts, equity, cash, last_equity, positions) VALUES (?, ?, ?, ?, ?)")
        .bind(e.ts, e.equity, e.cash, e.last_equity, JSON.stringify(e.positions)).run();
    },
  };
}
