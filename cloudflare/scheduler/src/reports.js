// Telegram report commands: /analise <company or coin> and /news.
//
// The Worker answers at once with a price snapshot, then starts the GitHub "Telegram report" workflow
// (.github/workflows/report.yml) that builds the full picture report and sends it ~2 minutes later.
// The same workflow makes the 08:00 and 22:30 (Vienna) market briefings.

import { escapeHtml } from "./util.js";

const ANALISE = new Set(["/analise", "/analyse", "/analyze", "/analysis"]);
const COIN_NAMES = {
  BITCOIN: "BTC", ETHEREUM: "ETH", ETHER: "ETH", SOLANA: "SOL", LITECOIN: "LTC", DOGECOIN: "DOGE", DOGE: "DOGE",
  AVALANCHE: "AVAX", CHAINLINK: "LINK", UNISWAP: "UNI", AAVE: "AAVE", "BITCOINCASH": "BCH",
};

/** {cmd: "analise" | "news", query} for a report command, else null. */
export function parseReport(text) {
  const m = String(text || "").trim().match(/^(\/[a-z]+)(?:@\w+)?\s*(.*)$/is);
  if (!m) return null;
  const cmd = m[1].toLowerCase();
  if (ANALISE.has(cmd)) return { cmd: "analise", query: m[2].trim().slice(0, 60) };
  if (cmd === "/news") return { cmd: "news", query: "" };
  return null;
}

/** The coin pair a query means (BTC, bitcoin, BTC/USD ...), if it is one of our coins. */
export function coinPair(query, coins) {
  const key = query.toUpperCase().replace(/[\s-]/g, "").replace(/\/?USD$/, "");
  const coin = COIN_NAMES[key] ?? key;
  return coins.includes(coin) ? `${coin}/USD` : null;
}

const pct = (x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
const usd = (x) => `$${Number(x).toLocaleString("en", { maximumFractionDigits: x >= 100 ? 0 : 2 })}`;

/** The instant snapshot line, or null when the query isn't a ticker/coin we can price right away. */
export async function quickSnapshot(alpaca, query, coins, now = new Date()) {
  const yearAgo = new Date(now.getTime() - 365 * 86400_000).toISOString().slice(0, 10);
  const pair = coinPair(query, coins);
  if (pair) {
    const snap = (await alpaca.data(`/v1beta3/crypto/us/snapshots?symbols=${encodeURIComponent(pair)}`)).snapshots?.[pair];
    const bars = (await alpaca.data(`/v1beta3/crypto/us/bars?symbols=${encodeURIComponent(pair)}&timeframe=1Day&start=${yearAgo}&limit=400`)).bars?.[pair] ?? [];
    if (!snap) return null;
    return line(pair.split("/")[0], snap.latestTrade?.p, snap.prevDailyBar?.c, bars);
  }
  if (!/^[A-Za-z]{1,5}([.-][A-Za-z])?$/.test(query)) return null;      // a name: the full report looks it up
  const sym = query.toUpperCase().replace("-", ".");
  const snap = (await alpaca.data(`/v2/stocks/snapshots?symbols=${encodeURIComponent(sym)}&feed=iex`))?.[sym];
  if (!snap) return null;
  const end = new Date(now.getTime() - 20 * 60_000).toISOString();     // SIP data needs a 15-minute delay on the free plan
  const bars = (await alpaca.data(`/v2/stocks/${encodeURIComponent(sym)}/bars?timeframe=1Day&start=${yearAgo}&end=${end}&limit=400&feed=sip&adjustment=all`)).bars ?? [];
  return line(sym, snap.latestTrade?.p, snap.prevDailyBar?.c, bars);
}

function line(name, price, prevClose, bars) {
  if (!price) return null;
  const today = prevClose ? ` (${pct(price / prevClose - 1)} today)` : "";
  const range = bars.length ? ` · 1-year range ${usd(Math.min(...bars.map((b) => b.l)))}–${usd(Math.max(...bars.map((b) => b.h)))}` : "";
  return `🔎 <b>${escapeHtml(name)}</b> ${usd(price)}${today}${range}`;
}

/** Start the GitHub report workflow. */
export async function dispatchReport(env, http, inputs) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/report.yml/dispatches`;
  const res = await http(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
               "User-Agent": "trader-scheduler", "X-GitHub-Api-Version": "2022-11-28" },
    body: JSON.stringify({ ref: "main", inputs }),
  });
  if (res.status !== 204) throw new Error(`GitHub dispatch: HTTP ${res.status}`);
}

/** Handle /analise or /news: instant reply text (the full report follows from GitHub). */
export async function reportCommand(env, http, alpaca, parsed, now = new Date()) {
  if (!env.GITHUB_DISPATCH_TOKEN) return "Reports need the GitHub token on the Worker (GITHUB_DISPATCH_TOKEN).";
  if (parsed.cmd === "news") {
    await dispatchReport(env, http, { kind: "news", query: "", when: "Latest" });
    return "📰 Building the market briefing… it arrives in about 2 minutes.";
  }
  if (!parsed.query) return "Which company or coin? For example: /analise AAPL · /analise apple · /analise bitcoin";
  let snap = null;
  try {
    snap = alpaca ? await quickSnapshot(alpaca, parsed.query, env.COINS.split(","), now) : null;
  } catch { /* the full report still comes */ }
  await dispatchReport(env, http, { kind: "analise", query: parsed.query, when: "Latest" });
  return `${snap ?? `🔎 Looking up <b>${escapeHtml(parsed.query)}</b>…`}\nFull report in about 2 minutes.`;
}

/** Which scheduled briefing (if any) is due at this Worker run: the daily-summary times. */
export function briefingDue(summaryTime) {
  return summaryTime === "08:00" ? "Morning" : summaryTime === "22:30" ? "Evening" : null;
}
