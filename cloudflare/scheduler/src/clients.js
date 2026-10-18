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
    latestTrade: async (symbol) => {
      const r = await call(DATA_API, "GET", `/v2/stocks/${encodeURIComponent(symbol)}/trades/latest?feed=iex`);
      return r?.trade?.p ?? null;
    },
  };
}

/** Close of the Coinbase BTC-USD hourly candle that ENDS at `end` (a Date on the hour). */
export async function btcCloseEndingAt(http, end) {
  const start = new Date(end.getTime() - 3600 * 1000);
  const url = `${COINBASE}/products/BTC-USD/candles?granularity=3600&start=${start.toISOString()}&end=${end.toISOString()}`;
  const res = await http(url, { headers: { "User-Agent": "trader-scheduler" } });
  if (!res.ok) throw new Error(`Coinbase answered ${res.status}`);
  const rows = await res.json();   // [[time(s), low, high, open, close, volume], ...]
  const candle = rows.find((r) => r[0] * 1000 === start.getTime());
  if (!candle) throw new Error(`no Coinbase BTC candle for ${start.toISOString()}`);
  return candle[4];
}

export async function telegram(env, http, text) {
  const res = await http(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) throw new Error(`Telegram answered ${res.status}`);
}
