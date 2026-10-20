import { PageState, StatusBadge, useJson } from "./ui.jsx";
import { shortDate, shortDateTime } from "./health.js";

const pct = (v, d = 1) => (v == null || Number.isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const usd = (v) => (v == null ? "—" : `$${Number(v).toLocaleString("en", { maximumFractionDigits: 0 })}`);

function ago(iso) {
  if (!iso) return "never";
  const min = (Date.now() - Date.parse(iso)) / 60000;
  if (min < 90) return `${Math.max(1, Math.round(min))} min ago`;
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
}

function LinkTile({ href, label, value, sub, status }) {
  return (
    <a className="tile tile-link" href={href}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-sub">{status ? <StatusBadge status={status}>{sub}</StatusBadge> : sub}</div>
    </a>
  );
}

function avg(xs) {
  const v = xs.filter((x) => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

/** Everything the page shows, derived from the three API responses. */
export function overview(health, lab, paper, now = Date.now()) {
  const lagMin = health.latest_run_at ? (now - Date.parse(health.latest_run_at)) / 60000 : Infinity;
  const fails = health.checks.filter((c) => c.status === "fail").length;
  const warns = health.checks.filter((c) => c.status === "warn").length;
  const dataStatus = lagMin > 6 * 60 || fails ? "fail" : lagMin > 90 || warns ? "warn" : "ok";

  const exps = lab.experiments;
  const passed = exps.filter((e) => e.verdict === "candidate" || e.verdict === "holdout_passed");
  const trials = exps.length ? Math.max(...exps.map((e) => e.total_trials ?? 0)) : 0;

  const eq = paper.equity;
  const paperRet = eq.length > 1 ? eq.at(-1).equity / eq[0].equity - 1 : null;
  const entries = paper.decisions.filter((d) => d.kind === "entry").length;

  const carry = paper.carry.at(-1) ?? null;
  const revEq = paper.reversal?.equity ?? [];
  const resEq = paper.research?.equity ?? [];
  const revWeeks = (paper.reversal?.weeks ?? []).filter((w) => w.complete).length;
  const signals = paper.news?.signals ?? [];
  const lex = signals.filter((s) => s.source === "lexicon");
  const llama = signals.filter((s) => s.source === "llama");
  const llmRuns = paper.news?.runs ?? [];

  return {
    data: { lagMin, fails, warns, status: dataStatus, latest: health.latest_run_at, datasets: health.datasets.length },
    research: { runs: exps.length, passed: passed.length, trials, recent: exps.slice(0, 5) },
    paper: { equity: eq.at(-1)?.equity ?? null, ret: paperRet, since: eq[0]?.ts ?? null, entries,
             open: paper.state.open_trade ?? null, halted: paper.state.halted ?? null },
    research: { ai: resEq.length ? resEq.at(-1).ai - 1 : null, rules: resEq.length ? resEq.at(-1).rules - 1 : null },
    reversal: { ret: revEq.length ? revEq.at(-1).equity - 1 : null, weeks: revWeeks },
    carry: carry && { ret: carry.equity / 100000 - 1, model: carry.model_equity / 100000 - 1, since: paper.carry[0].ts },
    news: { llama: llama.length, llamaScored: llama.filter((s) => s.net_1d != null).length, llamaAvg: avg(llama.map((s) => s.net_1d)),
            lex: lex.length, lexScored: lex.filter((s) => s.net_1d != null).length, lexAvg: avg(lex.map((s) => s.net_1d)),
            llmOn: llmRuns.length > 0 },
  };
}

function Overview({ health, lab, paper }) {
  const o = overview(health, lab, paper);
  const verdict = o.research.passed > 0
    ? `${o.research.passed} idea(s) passed all six research tests and wait for their one look at the locked holdout.`
    : `No idea has passed all six research tests yet (${o.research.runs} validation runs, ${o.research.trials.toLocaleString("en")} trials). The locked holdout (Oct 2024 on) is untouched.`;
  return (
    <>
      <section className="card overview-banner">
        <h2>Research only, no real money</h2>
        <p className="muted">{verdict} Meanwhile six shadow experiments collect forward evidence with fake money.</p>
      </section>

      <div className="tiles tiles-5">
        <LinkTile href="#/health" label="Data" value={ago(o.data.latest)}
                  sub={o.data.fails ? `${o.data.fails} failing check(s)` : o.data.warns ? `${o.data.warns} warning(s)` : `${o.data.datasets} datasets OK`}
                  status={o.data.status} />
        <LinkTile href="#/lab" label="Research" value={`${o.research.passed} / ${o.research.runs}`}
                  sub={`ideas passed · ${o.research.trials.toLocaleString("en")} trials`} />
        <LinkTile href="#/paper" label="Paper trade" value={o.paper.ret == null ? usd(o.paper.equity) : pct(o.paper.ret, 2)}
                  sub={o.paper.halted ? "halted" : o.paper.open ? `open since ${o.paper.open.session_date}` : `${o.paper.entries} trade(s) · waiting for a big BTC weekend`}
                  status={o.paper.halted ? "fail" : undefined} />
        <LinkTile href="#/paper" label="Carry simulation" value={o.carry ? pct(o.carry.ret, 2) : "—"}
                  sub={o.carry ? `model ${pct(o.carry.model, 2)} · since ${shortDate(o.carry.since)}` : "starts with the next hourly tick"} />
        <LinkTile href="#/paper" label="News signals" value={String(o.news.lex)}
                  sub={o.news.lexScored ? `1-day avg ${pct(o.news.lexAvg, 2)} after costs` : "word list · none scored yet"} />
      </div>

      <section className="card">
        <div className="card-head"><h2>Forward experiments</h2><span className="muted">fake money, frozen rules, not validated</span></div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Experiment</th><th>What it does</th><th className="num">So far</th></tr></thead>
            <tbody>
              <tr><td className="exp-name">Weekend gap<span className="muted small">paper · since {o.paper.since ? shortDate(o.paper.since) : "19 Oct"}</span></td>
                <td className="small">BTC up &gt; 6% over a weekend → buy COIN, MSTR, MARA, RIOT Monday, sell next open</td>
                <td className="num">{pct(o.paper.ret, 2)}<span className="muted small"> · {o.paper.entries} trade(s)</span></td></tr>
              <tr><td className="exp-name">Carry simulation<span className="muted small">$100k virtual · since {o.carry ? shortDate(o.carry.since) : "—"}</span></td>
                <td className="small">Long spot + short perp in 10 coins, collecting funding</td>
                <td className="num">{o.carry ? pct(o.carry.ret, 2) : "—"}<span className="muted small">{o.carry ? ` · model ${pct(o.carry.model, 2)}` : ""}</span></td></tr>
              <tr><td className="exp-name">Weekly reversal<span className="muted small">simulation · since 19 Oct 2026</span></td>
                <td className="small">Every Friday: long the 50 biggest losers, short the 50 biggest winners (liquid stocks)</td>
                <td className="num">{pct(o.reversal.ret, 2)}<span className="muted small"> · {o.reversal.weeks} week(s)</span></td></tr>
              <tr><td className="exp-name">AI research desk<span className="muted small">simulation · since 20 Oct 2026</span></td>
                <td className="small">Quality + value picks from SEC filings; the AI buys only with fact-checked reasons</td>
                <td className="num">{pct(o.research.ai, 2)}<span className="muted small"> · rules {pct(o.research.rules, 2)}</span></td></tr>
              <tr><td className="exp-name">News word list<span className="muted small">since 19 Oct 2026</span></td>
                <td className="small">News-event words in headlines, 27 assets, every 6 hours</td>
                <td className="num">{o.news.lex} signal(s)<span className="muted small">{o.news.lexScored ? ` · 1-day ${pct(o.news.lexAvg, 2)}` : ""}</span></td></tr>
              <tr><td className="exp-name">Free AI news (Llama)<span className="muted small">since 19 Oct 2026</span></td>
                <td className="small">Llama 3.3 70B on Cloudflare's free allowance reads the same headlines</td>
                <td className="num">{o.news.llama} signal(s)<span className="muted small">{o.news.llamaScored ? ` · 1-day ${pct(o.news.llamaAvg, 2)}` : ""}</span></td></tr>
              <tr><td className="exp-name muted">LLM news analyst<span className="small">no API key</span></td>
                <td className="small muted">Claude reads the same headlines</td>
                <td className="num muted">{o.news.llmOn ? "running" : "off"}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <div className="two-col">
        <section className="card">
          <div className="card-head"><h2>Latest validation runs</h2><a className="muted small" href="#/lab">all runs →</a></div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Idea</th><th className="num">Sharpe (unseen)</th><th>Verdict</th></tr></thead>
              <tbody>
                {o.research.recent.map((e) => (
                  <tr key={e.id}>
                    <td>{e.strategy}<span className="muted small"> · {shortDate(e.created_at)}</span></td>
                    <td className="num">{e.oos_sharpe == null ? "—" : e.oos_sharpe.toFixed(2)}</td>
                    <td><StatusBadge status={e.verdict === "rejected" ? "fail" : "ok"}>{e.verdict === "rejected" ? "Rejected" : "Passed"}</StatusBadge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="card">
          <div className="card-head"><h2>Coming up</h2></div>
          <ul className="upcoming">
            <li><strong>Every Monday 09:00 New York</strong><span className="muted small">weekend-gap decision (trades only after a BTC weekend above +6%)</span></li>
            <li><strong>Every Sunday 20:00 Vienna</strong><span className="muted small">weekly report on Telegram</span></li>
            <li><strong>Every Sunday 05:37 UTC</strong><span className="muted small">the brain: the free AI invents 2 strategies, the six tests decide which die</span></li>
            <li><strong>2 Nov</strong><span className="muted small">paper trading two weeks without a crash: milestone M5 done</span></li>
            <li><strong>3 Nov</strong><span className="muted small">monthly refresh of SEC filings and daily prices</span></li>
            <li><strong>19 Nov</strong><span className="muted small">one-month review: paper trade and carry simulation against their backtests</span></li>
          </ul>
        </section>
      </div>
      <p className="muted small">Updated {shortDateTime(new Date().toISOString())} · refreshes every minute.</p>
    </>
  );
}

export default function OverviewPage() {
  const health = useJson("/api/health"), lab = useJson("/api/experiments"), paper = useJson("/api/paper");
  const states = [health, lab, paper];
  const failed = states.find((s) => s.phase === "error" || s.phase === "locked");
  const state = failed ?? (states.every((s) => s.phase === "ready")
    ? { phase: "ready", data: { health: health.data, lab: lab.data, paper: paper.data } } : { phase: "loading" });
  return <PageState state={state}>{(d) => <Overview {...d} />}</PageState>;
}
