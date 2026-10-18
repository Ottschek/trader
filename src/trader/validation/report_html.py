"""Self-contained HTML report card for one validation run (written to reports/)."""

from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from trader.config import PROJECT_ROOT

VERDICT = {
    "rejected": ("fail", "Rejected", "Failed at least one research gate. Into the graveyard."),
    "candidate": ("ok", "Candidate", "Passed every research gate. It may now look at the holdout, once."),
    "holdout_passed": ("ok", "Passed holdout", "Passed every gate and the one-time holdout. Next: paper trading."),
    "holdout_failed": ("fail", "Failed holdout", "Passed research but failed on the unseen holdout. Into the graveyard."),
}


def _pct(x, signed=True):
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def _daily(equity: pd.Series) -> pd.Series:
    return equity.resample("1D").last().dropna()


def _equity_svg(strategy: pd.Series, benchmark: pd.Series) -> str:
    s, b = _daily(strategy), _daily(benchmark).reindex(_daily(strategy).index).ffill()
    if len(s) < 2:
        return "<p class='muted'>Not enough data for a chart.</p>"
    W, H, L, R, T, B = 760, 300, 56, 92, 12, 28
    lo = math.log(min(s.min(), b.min()) * 0.95)
    hi = math.log(max(s.max(), b.max()) * 1.05)
    x = lambda i: L + (W - L - R) * i / (len(s) - 1)  # noqa: E731
    y = lambda v: T + (H - T - B) * (1 - (math.log(v) - lo) / (hi - lo))  # noqa: E731
    path = lambda ser: " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(ser.values))  # noqa: E731
    ticks = [10 ** k * m for k in range(-3, 6) for m in (1, 2, 5) if lo <= math.log(10 ** k * m) <= hi]
    grid = "".join(
        f"<line x1='{L}' x2='{W - R}' y1='{y(t):.1f}' y2='{y(t):.1f}' class='grid'/>"
        f"<text x='{L - 8}' y='{y(t) + 4:.1f}' class='axis' text-anchor='end'>{t:g}×</text>" for t in ticks)
    years = sorted({d.year for d in s.index})
    xt = "".join(
        f"<text x='{x(s.index.searchsorted(pd.Timestamp(f'{yr}-01-01', tz='UTC'))):.1f}' y='{H - 8}' "
        f"class='axis' text-anchor='middle'>{yr}</text>"
        for yr in years if s.index[0] <= pd.Timestamp(f"{yr}-01-01", tz="UTC") <= s.index[-1])
    data = json.dumps({"d": [d.strftime("%Y-%m-%d") for d in s.index],
                       "s": [round(v, 4) for v in s.values], "b": [round(v, 4) for v in b.values]})
    return f"""
<div class="chart" id="eq">
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Out-of-sample equity, strategy vs buy-and-hold, log scale">
  {grid}{xt}
  <polyline points="{path(b)}" class="line bench"/>
  <polyline points="{path(s)}" class="line strat"/>
  <text x="{W - R + 6}" y="{y(s.iloc[-1]) + 4:.1f}" class="label strat-t">Strategy {s.iloc[-1]:.2f}×</text>
  <text x="{W - R + 6}" y="{y(b.iloc[-1]) + 4:.1f}" class="label bench-t">Hold {b.iloc[-1]:.2f}×</text>
  <line id="eq-x" y1="{T}" y2="{H - B}" class="cross" visibility="hidden"/>
</svg>
<div class="tip" id="eq-tip" hidden></div>
</div>
<script>
(() => {{
  const D = {data}, svg = document.querySelector('#eq svg'), tip = document.getElementById('eq-tip'),
        line = document.getElementById('eq-x'), L = {L}, R = {R}, W = {W};
  svg.addEventListener('mousemove', (e) => {{
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W;
    const i = Math.max(0, Math.min(D.d.length - 1, Math.round((px - L) / (W - L - R) * (D.d.length - 1))));
    const cx = L + (W - L - R) * i / (D.d.length - 1);
    line.setAttribute('x1', cx); line.setAttribute('x2', cx); line.setAttribute('visibility', 'visible');
    tip.hidden = false; tip.style.left = (cx / W * 100) + '%';
    tip.innerHTML = `<b>${{D.d[i]}}</b><br><span class="sw strat-bg"></span>Strategy ${{D.s[i].toFixed(2)}}×` +
                    `<br><span class="sw bench-bg"></span>Hold ${{D.b[i].toFixed(2)}}×`;
  }});
  svg.addEventListener('mouseleave', () => {{ tip.hidden = true; line.setAttribute('visibility', 'hidden'); }});
}})();
</script>"""


def _random_svg(random_sharpes, actual: float) -> str:
    vals = np.asarray(random_sharpes or [])
    if not len(vals):
        return ""
    W, H, L, B, T = 760, 200, 40, 30, 16
    lo, hi = min(vals.min(), actual), max(vals.max(), actual)
    pad = (hi - lo) * 0.05 or 0.5
    lo, hi = lo - pad, hi + pad
    counts, edges = np.histogram(vals, bins=30, range=(lo, hi))
    x = lambda v: L + (W - 2 * L) * (v - lo) / (hi - lo)  # noqa: E731
    bw = (W - 2 * L) / len(counts)
    top = counts.max() or 1
    bars = "".join(
        f"<rect x='{x(edges[i]) + 1:.1f}' y='{T + (H - T - B) * (1 - c / top):.1f}' width='{max(bw - 2, 1):.1f}' "
        f"height='{(H - T - B) * c / top:.1f}' rx='2' class='hist'><title>Sharpe {edges[i]:.2f} to {edges[i + 1]:.2f}: "
        f"{c} shuffles</title></rect>" for i, c in enumerate(counts) if c)
    ax = x(actual)
    zero = f"<line x1='{x(0):.1f}' x2='{x(0):.1f}' y1='{T}' y2='{H - B}' class='grid'/>" if lo < 0 < hi else ""
    return f"""
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Sharpe of {len(vals)} random-timing shuffles vs the strategy">
  {zero}{bars}
  <line x1='{L}' x2='{W - L}' y1='{H - B}' y2='{H - B}' class='baseline'/>
  <line x1='{ax:.1f}' x2='{ax:.1f}' y1='{T - 6}' y2='{H - B}' class='marker'/>
  <text x='{ax:.1f}' y='{T - 8 + 6}' class='label strat-t' text-anchor='{"end" if ax > W * 0.7 else "start"}' dx='{"-6" if ax > W * 0.7 else "6"}'>Strategy {actual:.2f}</text>
  <text x='{L}' y='{H - 8}' class='axis'>{lo:.1f}</text>
  <text x='{W - L}' y='{H - 8}' class='axis' text-anchor='end'>{hi:.1f}</text>
  <text x='{W / 2}' y='{H - 8}' class='axis' text-anchor='middle'>Sharpe (annualized) of random-timing shuffles</text>
</svg>"""


def _metrics_rows(card) -> str:
    rows = [("Total return", "total_return", _pct), ("CAGR", "cagr", _pct),
            ("Sharpe", "sharpe", lambda v: f"{v:.2f}"), ("Max drawdown", "max_drawdown", lambda v: _pct(v, False)),
            ("Volatility", "volatility", lambda v: _pct(v, False)), ("Avg exposure", "exposure", lambda v: f"{v:.0%}"),
            ("Turnover / year", "turnover_per_year", lambda v: f"{v:.1f}×"),
            ("Costs / year", "cost_per_year", lambda v: f"{v:.2%}")]
    return "".join(f"<tr><td>{label}</td><td class='num'>{fmt(card.oos[k])}</td>"
                   f"<td class='num'>{fmt(card.benchmark_oos[k])}</td></tr>" for label, k, fmt in rows)


def render(card) -> str:
    cls, title, blurb = VERDICT[card.verdict]
    icon = {"ok": "✓", "fail": "✕"}[cls]
    gates = "".join(
        f"<tr><td><span class='badge badge-{'ok' if g.passed else 'fail'}'><span class='bi'>{'✓' if g.passed else '✕'}</span>"
        f"{'Pass' if g.passed else 'Fail'}</span></td><td>{escape(g.label)}</td><td class='num'>{escape(g.value)}</td>"
        f"<td class='muted'>{escape(g.threshold)}</td></tr>" for g in card.gates)
    folds = "".join(
        f"<tr><td>{f['test_start'][:10]} → {f['test_end'][:10]}</td><td>{escape(json.dumps(f['params']))}</td>"
        f"<td class='num'>{_pct(f['return'])}</td></tr>" for f in card.folds)
    problems = "".join(f"<li>{escape(p)}</li>" for p in card.lookahead_problems)
    holdout = ""
    if card.holdout:
        h = card.holdout
        holdout = (f"<section class='card'><h2>Holdout (one-time look)</h2><p>{h['start'][:10]} → {h['end'][:10]} · "
                   f"params {escape(json.dumps(h['params']))} · Sharpe {h['metrics']['sharpe']:.2f} vs hold "
                   f"{h['benchmark']['sharpe']:.2f} · max drawdown {_pct(h['metrics']['max_drawdown'], False)}</p></section>")
    commit = (card.code.get("commit") or "no git")[:10] + (" (uncommitted changes)" if card.code.get("dirty") else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Report card · {escape(card.strategy)}</title>
<style>
:root {{ --bg:#f6f6f4; --surface:#fcfcfb; --border:#e6e5e0; --text:#0b0b0b; --text2:#52514e;
  --strat:#2a78d6; --bench:#eb6834; --good:#0ca30c; --crit:#d03b3b; --goodbg:#e9f6e9; --critbg:#fbeaea;
  --grid:#e6e5e0; --hist:#b9b8b2; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#121211; --surface:#1a1a19; --border:#2e2e2b;
  --text:#fff; --text2:#c3c2b7; --strat:#3987e5; --bench:#d95926; --goodbg:#163016; --critbg:#3a1a1a; --grid:#2e2e2b;
  --hist:#5a5954; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }}
.page {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; display:grid; gap:16px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; letter-spacing:-0.02em; }} h2 {{ font-size: 15px; margin: 0 0 12px; }}
.muted {{ color: var(--text2); }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:18px 20px; min-width:0; }}
.verdict {{ display:flex; gap:14px; align-items:center; }}
.big {{ width:44px; height:44px; border-radius:50%; display:grid; place-items:center; color:#fff; font-size:22px; font-weight:800; flex:none; }}
.big.ok {{ background:var(--good); }} .big.fail {{ background:var(--crit); }}
table {{ width:100%; border-collapse:collapse; }} td, th {{ padding:7px 8px; border-bottom:1px solid var(--border); text-align:left; }}
th {{ font-size:12px; color:var(--text2); font-weight:550; }} .num {{ text-align:right; font-variant-numeric: tabular-nums; }}
.tw {{ overflow-x:auto; }}
.badge {{ display:inline-flex; gap:6px; align-items:center; padding:2px 8px 2px 3px; border-radius:999px; font-size:12.5px; font-weight:550; }}
.bi {{ width:16px; height:16px; border-radius:50%; display:grid; place-items:center; color:#fff; font-size:10px; font-weight:800; }}
.badge-ok {{ background:var(--goodbg); }} .badge-ok .bi {{ background:var(--good); }}
.badge-fail {{ background:var(--critbg); }} .badge-fail .bi {{ background:var(--crit); }}
svg {{ width:100%; height:auto; display:block; }}
.grid {{ stroke:var(--grid); stroke-width:1; }} .baseline {{ stroke:var(--text2); stroke-width:1; }}
.axis {{ fill:var(--text2); font-size:11px; }} .label {{ font-size:12px; font-weight:600; fill:var(--text); }}
.line {{ fill:none; stroke-width:2; stroke-linejoin:round; }} .strat {{ stroke:var(--strat); }} .bench {{ stroke:var(--bench); }}
.hist {{ fill:var(--hist); }} .marker {{ stroke:var(--strat); stroke-width:2; }}
.cross {{ stroke:var(--text2); stroke-width:1; stroke-dasharray:3 3; }}
.chart {{ position:relative; }} .tip {{ position:absolute; top:8px; transform:translateX(-50%); background:#111; color:#fff;
  padding:6px 9px; border-radius:8px; font-size:12px; pointer-events:none; white-space:nowrap; }}
.sw {{ display:inline-block; width:10px; height:3px; border-radius:2px; margin-right:6px; vertical-align:middle; }}
.strat-bg {{ background:var(--strat); }} .bench-bg {{ background:var(--bench); }}
.legend {{ display:flex; gap:16px; font-size:12.5px; color:var(--text2); margin-top:8px; }}
</style></head>
<body><main class="page">
<header><h1>{escape(card.strategy)} · {escape(", ".join(card.symbols))}</h1>
<div class="muted">Report card {card.id} · {card.created_at[:16].replace("T", " ")} UTC · code {escape(commit)} ·
research data {card.research_window[0][:10]} → {card.research_window[1][:10]} (holdout locked) ·
walk-forward {card.train_years * 12:.0f}-month train / {card.test_months}-month test</div></header>
<section class="card verdict"><div class="big {cls}" aria-hidden="true">{icon}</div>
<div><h2 style="margin:0">{title}</h2><div class="muted">{blurb}</div></div></section>
<section class="card"><h2>Gates</h2><div class="tw"><table><thead><tr><th>Result</th><th>Gate</th><th class="num">Value</th><th>Needs</th></tr></thead>
<tbody>{gates}</tbody></table></div>{f"<ul class='muted'>{problems}</ul>" if problems else ""}</section>
<section class="card"><h2>Out-of-sample equity (walk-forward, log scale)</h2>{_equity_svg(card.equity, card.benchmark_equity)}
<div class="legend"><span><span class="sw strat-bg"></span>Strategy</span><span><span class="sw bench-bg"></span>Buy-and-hold {escape(card.benchmark)}</span></div></section>
<section class="card"><h2>Out-of-sample metrics</h2><div class="tw"><table><thead><tr><th></th><th class="num">Strategy</th>
<th class="num">Hold {escape(card.benchmark)}</th></tr></thead><tbody>{_metrics_rows(card)}</tbody></table></div></section>
<section class="card"><h2>Random-timing test</h2><p class="muted">The strategy's in-market periods shuffled
{card.random_entry["trials"]} times. p = {card.random_entry["p_value"]:.3f}: share of shuffles that did at least as well.</p>
{_random_svg(card.random_sharpes, card.random_entry["strategy_sharpe"])}</section>
<section class="card"><h2>Walk-forward folds</h2><p class="muted">{card.trials} parameter sets tried this run ·
{card.total_trials} trials in the ledger in total · deflated Sharpe {card.deflated["probability"]:.2f}</p>
<div class="tw"><table><thead><tr><th>Test window</th><th>Parameters picked on the prior {card.train_years * 12:.0f} months</th><th class="num">Return</th></tr></thead>
<tbody>{folds}</tbody></table></div></section>
{holdout}
</main></body></html>"""


def write(card, directory: Path | None = None) -> Path:
    directory = directory or PROJECT_ROOT / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{card.created_at[:10]}-{card.strategy}-{card.id}.html"
    path.write_text(render(card))
    return path
