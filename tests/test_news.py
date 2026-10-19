import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trader.data import alt, news
from trader.data.store import read, upsert
from trader.paper import news_analyst as na


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


NOW = pd.Timestamp("2026-10-19 12:07", tz="UTC")
RSS = """<?xml version="1.0"?><rss><channel><title>Yahoo! Finance: NVDA News</title>
<item><title>Nvidia wins huge contract</title><link>https://x/1</link><guid>g1</guid><pubDate>Mon, 19 Oct 2026 11:00:00 +0000</pubDate></item>
<item><title>Chip stocks to watch</title><link>https://x/2</link><guid>g2</guid><pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title></title><guid>g3</guid><pubDate>Mon, 19 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""

CFG = {"start": "2026-10-19", "every_hours": 6, "model": "claude-opus-5-5", "effort": "low",
       "monthly_budget_usd": 5.0, "signal_threshold": 2,
       "assets": {"NVDA": "Nvidia", "SPY": "S&P 500", "BTC/USD": "Bitcoin"}}


def test_rss_parsing_and_first_seen_is_kept_when_a_feed_adds_a_tag():
    df = news.parse_rss(RSS, "NVDA")
    assert list(df["guid"]) == ["g1", "g2"] and str(df["published"].dt.tz) == "UTC"
    first = news.merge_new(pd.DataFrame(), df, NOW)
    assert (first["ts"] == NOW).all()
    upsert(news.headlines_path(), first, key=["guid"])
    later = NOW + pd.Timedelta(hours=1)
    again = news.merge_new(read(news.headlines_path()), news.parse_rss(RSS, "SPY").head(1), later)
    assert list(again["guid"]) == ["g1"] and again.at[0, "tags"] == "NVDA,SPY" and again.at[0, "ts"] == NOW
    assert news.merge_new(read(news.headlines_path()), df, later).empty     # nothing new
    assert news.yahoo_ticker("BTC/USD") == "BTC-USD" and news.yahoo_ticker("UNI/USD", {"UNI/USD": "UNI7083-USD"}) == "UNI7083-USD"


def test_headline_selection_uses_first_seen_and_drops_stale_stories():
    h = pd.DataFrame({"guid": ["a", "b", "c", "d"], "title": ["A", "B", "C", "A"], "link": "", "tags": "NVDA",
                      "published": [NOW - pd.Timedelta(hours=1), NOW - pd.Timedelta(days=3), NOW - pd.Timedelta(hours=2), NOW],
                      "ts": [NOW - pd.Timedelta(hours=1), NOW, NOW - pd.Timedelta(hours=9), NOW]})
    rows = na.select_headlines(h, NOW - pd.Timedelta(hours=6), NOW)
    assert list(rows["guid"]) == ["a"]       # b too old, c seen before the cutoff, d repeats A's title


def test_budget_is_paced_through_the_month_and_capped():
    assert na.allowance(5.0, pd.Timestamp("2026-10-16 00:00", tz="UTC")) == pytest.approx(5.0 * 15 / 31)
    assert na.allowance(5.0, pd.Timestamp("2026-10-01 01:00", tz="UTC")) == pytest.approx(5.0 / 31)
    assert na.budget_block(0.0, 5.0, NOW, "claude-opus-5-5", 3000) is None
    assert na.budget_block(4.0, 5.0, NOW, "claude-opus-5-5", 3000).startswith("paced")   # 4 > 5 x 18.5/31
    assert na.budget_block(4.95, 5.0, pd.Timestamp("2026-10-31 23:00", tz="UTC"), "claude-opus-5-5", 3000).startswith("monthly")
    assert na.cost_usd("claude-opus-5-5", 1_000_000, 0) == 4.0 and na.cost_usd("unknown", 0, 1_000_000) == 50.0


class FakeClient:
    def __init__(self, flags, stop_reason="end_turn", model="claude-opus-5-5"):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._flags, self._stop, self._model = flags, stop_reason, model

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(model=self._model, stop_reason=self._stop,
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=500),
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=json.dumps({"flags": self._flags}))])


def headline_rows():
    return pd.DataFrame({"guid": ["a", "b", "c"], "title": ["Nvidia wins contract", "Nvidia beats", "BTC ETF outflows"],
                         "link": ["l1", "l2", "l3"], "tags": ["NVDA", "NVDA", "BTC/USD"],
                         "published": [NOW - pd.Timedelta(hours=1)] * 3, "ts": [NOW - pd.Timedelta(hours=1)] * 3})


def test_claude_flags_become_signals_only_above_the_threshold():
    flags = [{"id": 0, "symbol": "NVDA", "impact": 1, "reason": "r"}, {"id": 1, "symbol": "NVDA", "impact": 2, "reason": "r"},
             {"id": 2, "symbol": "BTC/USD", "impact": -1, "reason": "r"}, {"id": 9, "symbol": "NVDA", "impact": 2, "reason": "bad id"}]
    client = FakeClient(flags)
    out = na.ask_claude(client, CFG, headline_rows(), NOW)
    assert out["status"] == "ok" and len(out["flags"]) == 3
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["schema"]["properties"]["flags"]["items"]["properties"]["symbol"]["enum"] == list(CFG["assets"])
    sig = na.signals_from_flags(out["flags"], headline_rows(), NOW, 2)
    assert list(sig["symbol"]) == ["NVDA"] and sig.at[0, "side"] == 1 and sig.at[0, "score"] == 3   # BTC -1 is below 2
    assert len(json.loads(sig.at[0, "headlines"])) == 2
    assert na.ask_claude(FakeClient([], stop_reason="refusal"), CFG, headline_rows(), NOW)["status"] == "refused"


def prices():
    idx = pd.date_range("2026-10-19 00:00", periods=24 * 8, freq="h", tz="UTC")
    t = np.arange(len(idx))
    open_ = pd.DataFrame({"NVDA": 100 * 1.001 ** t, "SPY": 100 * 1.0005 ** t}, index=idx)
    close = open_ * 1.001
    return open_, close


def test_signals_are_scored_from_the_next_open_against_the_benchmark():
    open_, close = prices()
    sig = pd.DataFrame([{"id": "x", "decided_at": NOW, "symbol": "NVDA", "side": 1, "score": 2, "headlines": "[]"},
                        {"id": "y", "decided_at": NOW, "symbol": "SPY", "side": -1, "score": -2, "headlines": "[]"}])
    bench = {"NVDA": "SPY", "SPY": "SPY"}
    costs = {"NVDA": 5.0, "SPY": 5.0}
    early = na.score_signals(sig, open_, close, NOW + pd.Timedelta(days=2), bench, costs)
    entry = pd.Timestamp("2026-10-19 13:00", tz="UTC")
    assert early.at[0, "entry_ts"] == entry and early.at[0, "entry_px"] == pytest.approx(open_.at[entry, "NVDA"])
    assert not np.isnan(early.at[0, "net_1d"]) and np.isnan(early.at[0, "net_5d"])        # 5 days not over yet
    exit_bar = entry + pd.Timedelta(hours=23)
    ret = close.at[exit_bar, "NVDA"] / open_.at[entry, "NVDA"] - 1
    ref = close.at[exit_bar, "SPY"] / open_.at[entry, "SPY"] - 1
    assert early.at[0, "net_1d"] == pytest.approx(ret - ref - 0.001)
    assert early.at[1, "net_1d"] == pytest.approx(-ref - 0.001)                           # benchmark: raw, short
    late = na.score_signals(early, open_, close, open_.index[-1] + na.BAR, bench, costs)
    assert not np.isnan(late.at[0, "net_5d"]) and late.at[0, "net_1d"] == early.at[0, "net_1d"]
    s = na.summary(late)
    assert s["1d"]["n"] == 2 and s["5d"]["n"] == 2


def test_a_collector_run_calls_claude_once_records_spend_and_waits_until_due(monkeypatch):
    settings = SimpleNamespace(news=CFG)
    upsert(news.headlines_path(), headline_rows(), key=["guid"])
    client = FakeClient([{"id": 0, "symbol": "NVDA", "impact": 2, "reason": "big contract"}])
    msg = na.run(settings, NOW, client=client)
    assert msg.startswith("ok: 3 headlines, 1 flagged, long NVDA")
    state = alt.load_state()["news"]
    assert state["spend"]["2026-10"] == pytest.approx(na.cost_usd("claude-opus-5-5", 2000, 500))
    assert read(na.signals_path())["symbol"].tolist() == ["NVDA"]
    assert read(na.runs_path())["status"].tolist() == ["ok"]
    assert na.run(settings, NOW + pd.Timedelta(hours=1), client=client).startswith("not due")
    later = NOW + pd.Timedelta(hours=6)
    assert na.run(settings, later, client=client) == "no new headlines"     # nothing new since the cutoff
    assert len(client.calls) == 1
    assert na.run(SimpleNamespace(news=CFG), pd.Timestamp("2026-10-18", tz="UTC"), client=client) == "not started"


def test_without_a_key_the_analyst_is_skipped(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("trader.config.load_dotenv", lambda *a, **k: None)
    assert na.run(SimpleNamespace(news=CFG), NOW) == "skipped: no ANTHROPIC_API_KEY"


# --- word-list score ----------------------------------------------------------------------

from trader.paper import news_lexicon as lex   # noqa: E402


def test_word_list_scores_events_not_price_moves_or_clickbait():
    assert lex.headline_score("Nvidia beats estimates and raises guidance") == 2
    assert lex.headline_score("Tesla cuts outlook as SEC charges executives with fraud") == -2      # capped
    assert lex.headline_score("Nvidia stock surges 8% today") == 0                                 # price move only
    assert lex.headline_score("Don't miss these 3 AI stocks") == 0
    assert lex.headline_score("Coinbase misses revenue estimates") == -1


def test_word_list_needs_the_feed_tag_and_a_mention():
    rows = pd.DataFrame({"title": ["Coinbase wins approval for derivatives", "Chainlink hacked in exploit",
                                   "Crypto exchange hacked", "Nvidia upgraded at Morgan Stanley"],
                         "link": ["a", "b", "c", "d"], "tags": ["COIN", "LINK/USD,ETH/USD", "LINK/USD", "AAPL"]})
    scored = lex.score_headlines(rows, list(lex.ALIASES))
    assert [(s["symbol"], s["score"]) for s in scored] == [("COIN", 1), ("LINK/USD", -2)]   # no mention / wrong feed -> skipped
    sig = lex.signals_from_scores(scored, NOW, 2)
    assert list(sig["symbol"]) == ["LINK/USD"] and sig.at[0, "side"] == -1 and sig.at[0, "id"].startswith("lex-")


def test_word_list_run_is_free_and_paced_like_the_analyst():
    settings = SimpleNamespace(news=CFG | {"assets": {"NVDA": "Nvidia", "SPY": "S&P 500"}})
    rows = pd.DataFrame({"guid": ["a", "b"], "title": ["Nvidia beats estimates, raises outlook", "Nvidia wins approval"],
                         "link": ["l1", "l2"], "tags": ["NVDA", "NVDA"],
                         "published": [NOW - pd.Timedelta(hours=1)] * 2, "ts": [NOW - pd.Timedelta(hours=1)] * 2})
    upsert(news.headlines_path(), rows, key=["guid"])
    assert lex.run(settings, NOW) == "2 headlines, 2 scored, long NVDA (+3)"
    assert lex.run(settings, NOW + pd.Timedelta(hours=2)).startswith("not due")
    assert read(lex.signals_path())["score"].tolist() == [3]


def test_d1_mirror_labels_each_signal_with_its_source():
    sig = pd.DataFrame([{"id": "x", "decided_at": NOW, "symbol": "NVDA", "side": 1, "score": 2, "headlines": "[]"}])
    upsert(na.signals_path(), sig, key=["id"])
    upsert(lex.signals_path(), sig.assign(id="lex-x"), key=["id"])
    na.record_run({"ts": NOW, "model": "m", "headlines": 1, "flagged": 1, "signals": 1, "input_tokens": 1,
                   "output_tokens": 1, "cost_usd": 0.01, "status": "ok"})
    inserted = []
    d1 = SimpleNamespace(insert_many=lambda table, cols, rows: inserted.append((table, cols, rows)) or len(rows))
    assert na.mirror_to_d1(d1, NOW) == 3
    signals = [dict(zip(cols, r)) for t, cols, rows in inserted if t == "news_signals" for r in rows]
    assert {(s["id"], s["source"]) for s in signals} == {("x", "claude"), ("lex-x", "lexicon")}
    assert signals[0]["entry_ts"] is None and signals[0]["decided_at"].startswith("2026-10-19T12:07")


# --- free AI (Workers AI Llama) -------------------------------------------------------------

from trader.paper import news_llama as llama   # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def test_llama_flags_are_kept_only_when_the_quote_matches_and_ids_get_repaired(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    rows = headline_rows()            # 0 "Nvidia wins contract", 1 "Nvidia beats", 2 "BTC ETF outflows"
    raw = {"flags": [
        {"id": 0, "quote": "Nvidia wins contract", "symbol": "NVDA", "impact": 2, "reason": "ok"},
        {"id": 0, "quote": "BTC ETF outflows", "symbol": "BTC/USD", "impact": -1, "reason": "wrong id, fixable"},
        {"id": 1, "quote": "Something else entirely", "symbol": "NVDA", "impact": 1, "reason": "no match"},
        {"id": 1, "quote": "Nvidia beats", "symbol": "XYZ", "impact": 1, "reason": "unknown asset"}]}
    text = "Here you go:\n" + json.dumps(raw)
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return FakeResponse({"result": {"response": text, "usage": {"neurons": 150.0}}})
    out = llama.ask_llama(CFG, rows, NOW, post)
    assert out["status"] == "ok" and out["dropped"] == 2 and out["usage"] == 150.0
    assert [(f["id"], f["symbol"]) for f in out["flags"]] == [(0, "NVDA"), (2, "BTC/USD")]
    assert calls[0][0].endswith("/accounts/acct/ai/run/@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    chat = FakeResponse({"result": {"choices": [{"message": {"content": text}}]}})
    assert llama.ask_llama(CFG, rows, NOW, lambda url, **kw: chat)["status"] == "ok"
    assert llama.ask_llama(CFG, rows, NOW, lambda url, **kw: FakeResponse({}, 429))["status"] == "HTTP 429"
    assert llama.ask_llama(CFG, rows, NOW, lambda url, **kw: FakeResponse({"result": {"response": "no json"}}))["status"] == "bad json"


def test_llama_run_keeps_headlines_queued_when_the_call_fails(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    settings = SimpleNamespace(news=CFG)
    upsert(news.headlines_path(), headline_rows(), key=["guid"])
    assert llama.run(settings, NOW, lambda url, **kw: FakeResponse({}, 500)).startswith("failed (HTTP 500)")
    assert "news_llama" not in alt.load_state() or "cutoff" not in alt.load_state()["news_llama"]
    good = {"flags": [{"id": 0, "quote": "Nvidia wins contract", "symbol": "NVDA", "impact": 2, "reason": "r"}]}
    msg = llama.run(settings, NOW, lambda url, **kw: FakeResponse({"result": {"response": json.dumps(good)}}))
    assert "long NVDA (+2)" in msg and read(llama.signals_path())["id"].str.startswith("llama-").all()
