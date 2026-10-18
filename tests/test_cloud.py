import hashlib


from trader.data import quality as q
from trader.data import r2
from trader.monitor import report
from trader.monitor.d1 import D1


def fail(dataset):
    return q.CheckResult("staleness", dataset, q.FAIL, "no data")


def ok(dataset):
    return q.CheckResult("staleness", dataset, q.OK, "fine")


# --- alerts ------------------------------------------------------------------

def test_alert_only_when_failures_change():
    first = [fail("coinbase BTC/USD"), ok("x")]
    msg = report.failure_alert(first, previous=[])
    assert msg and "1 failing" in msg and "coinbase BTC/USD" in msg
    # same failure next hour: stay quiet
    assert report.failure_alert(first, previous=[r.to_dict() for r in first]) is None
    # a second failure appears: alert again and mark the new one
    second = first + [fail("funding BTC")]
    msg = report.failure_alert(second, previous=[r.to_dict() for r in first])
    assert "2 failing" in msg and "1 new" in msg and "🆕" in msg
    # everything fixed: recovery message
    assert "recovered" in report.failure_alert([ok("x")], previous=[r.to_dict() for r in second])


def test_no_alert_when_all_good_from_the_start():
    assert report.failure_alert([ok("x")], previous=None) is None


def test_alert_escapes_html():
    msg = report.failure_alert([q.CheckResult("download", "a<b>", q.FAIL, "x & y")], previous=[])
    assert "a&lt;b&gt;" in msg and "x &amp; y" in msg


# --- D1 chunking -------------------------------------------------------------

def test_d1_insert_many_respects_parameter_limit(monkeypatch):
    d1 = D1("db", account_id="acct", token="tok")
    calls = []
    monkeypatch.setattr(d1, "query", lambda sql, params=None: calls.append((sql, params)) or [])
    rows = [(i, f"v{i}", i * 2) for i in range(100)]
    assert d1.insert_many("t", ["a", "b", "c"], rows) == 100
    assert all(len(params) <= 90 for _, params in calls)
    assert sum(len(params) for _, params in calls) == 300
    assert calls[0][0].startswith("INSERT OR REPLACE INTO t (a,b,c) VALUES (?,?,?),")


# --- R2 sync -----------------------------------------------------------------

class FakeS3:
    """Just enough of boto3's S3 client for pull/push."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts = 0

    def get_paginator(self, _):
        objects = self.objects

        class P:
            def paginate(self, Bucket):
                yield {"Contents": [{"Key": k, "ETag": f'"{hashlib.md5(v).hexdigest()}"'} for k, v in objects.items()]}
        return P()

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body
        self.puts += 1

    def download_file(self, Bucket, Key, Filename):
        with open(Filename, "wb") as f:
            f.write(self.objects[Key])


def test_r2_push_only_uploads_changed_files_and_pull_restores_them(tmp_path):
    s3 = FakeS3()
    (tmp_path / "bars" / "coinbase").mkdir(parents=True)
    a = tmp_path / "bars" / "coinbase" / "BTC-USD.parquet"
    a.write_bytes(b"one")
    (tmp_path / "notes.txt").write_text("not synced")

    assert r2.push("b", s3, tmp_path) == 1
    assert set(s3.objects) == {"bars/coinbase/BTC-USD.parquet"}
    assert r2.push("b", s3, tmp_path) == 0          # unchanged: nothing uploaded
    a.write_bytes(b"two")
    assert r2.push("b", s3, tmp_path) == 1          # changed: uploaded again

    fresh = tmp_path / "fresh"
    assert r2.pull("b", s3, fresh) == 1
    assert (fresh / "bars" / "coinbase" / "BTC-USD.parquet").read_bytes() == b"two"
    assert r2.pull("b", s3, fresh) == 0             # already up to date


# --- Worker snapshots ----------------------------------------------------------

def test_worker_snapshots_merge_into_parquet(tmp_path, monkeypatch):
    from trader.data import hyperliquid, store
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    row = {"ts": "2026-10-18T05:00:00+00:00", "taken_at": "2026-10-18T05:05:01+00:00", "coin": "BTC",
           "open_interest": 42000.0, "funding_rate": 1.25e-05, "premium": None, "mark_px": 81636.0,
           "oracle_px": 81656.7, "day_notional_volume": 2.4e9}
    assert hyperliquid.merge_worker_snapshots([row, {**row, "coin": "ETH"}]) == 2
    assert hyperliquid.merge_worker_snapshots([{**row, "open_interest": 43000.0}]) == 0   # same hour+coin: replaced
    df = store.read(store.snapshots_path())
    assert len(df) == 2
    assert df.loc[df["coin"] == "BTC", "open_interest"].item() == 43000.0
    assert str(df["ts"].dt.tz) == "UTC"
    assert hyperliquid.merge_worker_snapshots([]) == 0
