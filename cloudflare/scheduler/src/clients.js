// Thin clients for the outside services. `http` is fetch (injected so tests can fake it).

const PAPER_API = "https://paper-api.alpaca.markets";   // paper only, hard-coded: never the live endpoint
const DATA_API = "https://data.alpaca.markets";
const COINBASE = "https://api.exchange.coinbase.com";

export function makeAlpaca(env, http) {
  if (!String(env.ALPACA_API_KEY || "").startsWith("PK")) {
    throw new Error("refusing to trade: ALPACA_API_KEY is not a paper key (must start with PK)");
  }
  const headers = {
    "APCA-API-KEY-ID": env.ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": env.ALPACA_SECRET_KEY,
    "Content-Type": "application/json",
  };
  const call = async (base, method, path, body) => {
    const res = await http(base + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    if (!res.ok) throw new Error(`Alpaca ${method} ${path} -> ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return res.status === 204 ? null : res.json();
  };
  return {
    account: () => call(PAPER_API, "GET", "/v2/account"),
    clock: () => call(PAPER_API, "GET", "/v2/clock"),
    positions: () => call(PAPER_API, "GET", "/v2/positions"),
    calendar: (start, end) => call(PAPER_API, "GET", `/v2/calendar?start=${start}&end=${end}`),
    submitOrder: (order) => call(PAPER_API, "POST", "/v2/orders", order),
    getOrder: (id) => call(PAPER_API, "GET", `/v2/orders/${id}`),
    closePosition: (symbol) => call(PAPER_API, "DELETE", `/v2/positions/${encodeURIComponent(symbol)}`),
    cryptoBar: async (symbol, start) => {
      const end = new Date(start.getTime() + 3600 * 1000).toISOString();
      const r = await call(DATA_API, "GET", `/v1beta3/crypto/us/bars?symbols=${encodeURIComponent(symbol)}&timeframe=1Hour` +
                                            `&start=${start.toISOString()}&end=${end}`);
      return (r?.bars?.[symbol] ?? []).find((b) => Date.parse(b.t) === start.getTime()) ?? null;
    },
    data: (path) => call(DATA_API, "GET", path),
    latestTrade: async (symbol) => {
      const r = await call(DATA_API, "GET", `/v2/stocks/${encodeURIComponent(symbol)}/trades/latest?feed=iex`);
      return r?.trade?.p ?? null;
    },
  };
}

/** Close of the Coinbase BTC-USD hourly candle that ENDS at `end` (a Date on the hour). */
/**
 * Close of the hourly BTC/USD candle that ends at `end`. Coinbase (what the backtest used) can take a
 * little while to publish a candle that closed seconds ago, so it is asked again every `waitMs` up to
 * `tries` times; if it still has none, Alpaca's BTC/USD bar is used (cross-checked: ~3 bps apart).
 * Returns { close, source }.
 */
export async function btcCloseEndingAt(http, end, { alpaca = null, tries = 7, waitMs = 20_000,
                                                    sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  const start = new Date(end.getTime() - 3600 * 1000);
  const url = `${COINBASE}/products/BTC-USD/candles?granularity=3600&start=${start.toISOString()}&end=${end.toISOString()}`;
  let problem = "no candle yet";
  for (let i = 0; i < tries; i++) {
    try {
      const res = await http(url, { headers: { "User-Agent": "trader-scheduler" } });
      if (res.ok) {
        const rows = await res.json();   // [[time(s), low, high, open, close, volume], ...]
        const candle = rows.find((r) => r[0] * 1000 === start.getTime());
        if (candle) return { close: candle[4], source: "coinbase" };
      } else {
        problem = `Coinbase answered ${res.status}`;
      }
    } catch (err) {
      problem = `Coinbase request failed: ${err.message}`;
    }
    if (i < tries - 1) await sleep(waitMs);
  }
  if (alpaca) {
    const bar = await alpaca.cryptoBar("BTC/USD", start).catch(() => null);
    if (bar) return { close: bar.c, source: "alpaca" };
  }
  throw new Error(`no BTC candle for ${start.toISOString()} (${problem}${alpaca ? "; Alpaca had none either" : ""})`);
}

export async function telegram(env, http, text) {
  const res = await http(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) throw new Error(`Telegram answered ${res.status}`);
}
