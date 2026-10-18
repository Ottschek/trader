// GET /api/paper: the shadow paper-trading experiment (written by the Cloudflare Worker and collector).

const parse = (v) => (v == null ? null : JSON.parse(v));

export async function onRequestGet({ env }) {
  const since = new Date(Date.now() - 90 * 24 * 3600 * 1000).toISOString();
  const [plan, decisions, orders, equity, state] = await env.DB.batch([
    env.DB.prepare("SELECT * FROM paper_plans ORDER BY as_of DESC LIMIT 1"),
    env.DB.prepare("SELECT * FROM paper_decisions ORDER BY decided_at DESC LIMIT 100"),
    env.DB.prepare("SELECT * FROM paper_orders ORDER BY submitted_at DESC LIMIT 200"),
    env.DB.prepare("SELECT ts, equity, cash, last_equity, positions FROM paper_equity WHERE ts >= ? ORDER BY ts").bind(since),
    env.DB.prepare("SELECT key, value, updated_at FROM paper_state"),
  ]);
  const p = plan.results[0];
  return Response.json({
    generated_at: new Date().toISOString(),
    plan: p ? { ...p, params: parse(p.params), sizes: parse(p.sizes), ref_prices: parse(p.ref_prices),
                daily_vol: parse(p.daily_vol) } : null,
    decisions: decisions.results.map((d) => ({ ...d, detail: parse(d.detail) })),
    orders: orders.results,
    equity: equity.results.map((e) => ({ ...e, positions: parse(e.positions) })),
    state: Object.fromEntries(state.results.map((s) => [s.key, parse(s.value)])),
  }, { headers: { "Cache-Control": "no-store" } });
}
