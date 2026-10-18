"""Copy the whole ledger to D1 and upload any local report cards to R2 (idempotent).

Run:  .venv/bin/python scripts/sync_ledger.py
The hourly collector also syncs ledger entries that are missing from D1.
"""

from __future__ import annotations

import sys

from trader.config import PROJECT_ROOT, load_settings
from trader.data import r2
from trader.ledger import ledger, sync
from trader.monitor.d1 import D1


def main() -> int:
    cloud = load_settings().cloud
    entries = ledger.entries()
    s3 = r2.client()
    reported = set()
    for e in entries:
        path = sync.local_report(e["id"], PROJECT_ROOT / "reports")
        if path:
            sync.upload_report(s3, cloud.r2_bucket, e["id"], path.read_text())
            reported.add(e["id"])
    remote = {k for k in r2.remote_index(s3, cloud.r2_bucket) if k.startswith(sync.REPORT_PREFIX)}
    reported |= {e["id"] for e in entries if f"{sync.REPORT_PREFIX}{e['id']}.html" in remote}
    n = sync.sync_ledger(D1(cloud.d1_database_id), entries, reported=reported, only_missing=False)
    print(f"{n} ledger entries synced to D1, {len(reported)} with report cards in R2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
