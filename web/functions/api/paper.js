// GET /api/paper: the shadow paper-trading experiment (written by the Cloudflare Worker and collector).

const parse = (v) => (v == null ? null : JSON.parse(v));

export async function onRequestGet({ env }) {
  const since = new Date(Date.now() - 90 * 24 * 3600 * 1000).toISOString();
  const [plan, decisions, orders, equity, state, recon, carry, newsRuns, newsSignals, revEq, revWeeks, resBooks, resEq] = await env.DB.batch([
    env.DB.prepare("SELECT * FROM paper_plans ORDER BY as_of DESC LIMIT 1"),
    env.DB.prepare("SELECT * FROM paper_decisions ORDER BY decided_at DESC LIMIT 100"),
    env.DB.prepare("SELECT * FROM paper_orders ORDER BY submitted_at DESC LIMIT 200"),
    env.DB.prepare("SELECT ts, equity, cash, last_equity, positions FROM paper_equity WHERE ts >= ? ORDER BY ts").bind(since),
    env.DB.prepare("SELECT key, value, updated_at FROM paper_state"),
    env.DB.prepare("SELECT summary FROM paper_reconcile ORDER BY computed_at DESC LIMIT 1"),
    env.DB.prepare("SELECT * FROM carry_sim WHERE ts >= ? ORDER BY ts").bind(since),
    env.DB.prepare("SELECT * FROM news_runs ORDER BY ts DESC LIMIT 200"),
    env.DB.prepare("SELECT * FROM news_signals ORDER BY decided_at DESC LIMIT 1000"),
    env.DB.prepare("SELECT ts, equity FROM reversal_equity ORDER BY ts"),
    env.DB.prepare("SELECT * FROM reversal_weeks ORDER BY decided DESC LIMIT 60"),
    env.DB.prepare("SELECT book FROM research_books ORDER BY decided DESC LIMIT 3"),
    env.DB.prepare("SELECT ts, ai, rules, spy FROM research_equity ORDER BY ts"),
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
    reconcile: recon.results[0] ? parse(recon.results[0].summary) : null,
    carry: carry.results.map((c) => ({ ...c, basis: parse(c.basis), funding: parse(c.funding) })),
    news: { runs: newsRuns.results, signals: newsSignals.results.map((s) => ({ ...s, headlines: parse(s.headlines) })) },
    research: { books: resBooks.results.map((b) => parse(b.book)), equity: resEq.results },
    reversal: { equity: revEq.results,
                weeks: revWeeks.results.map((w) => ({ ...w, longs: parse(w.longs), shorts: parse(w.shorts) })) },
  }, { headers: { "Cache-Control": "no-store" } });
}
