"""Sync the local data directory with the private R2 bucket (S3-compatible API).

GitHub Actions starts every run with an empty disk, so each run pulls the
stored data from R2, adds the new bars, and pushes back only changed files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import boto3

from trader.config import secret
from trader.data.store import data_dir

SYNCED_SUFFIXES = {".parquet", ".json"}


def client():
    account = secret("CLOUDFLARE_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=secret("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=secret("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_index(s3, bucket: str) -> dict[str, str]:
    """{key: md5} for every object in the bucket (single-part uploads have md5 ETags)."""
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["ETag"].strip('"')
    return out


def local_files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and p.suffix in SYNCED_SUFFIXES}


def pull(bucket: str, s3=None, root: Path | None = None) -> int:
    """Download every data file that is missing or different locally (report cards and
    other non-data objects in the bucket are skipped)."""
    s3, root = s3 or client(), root or data_dir()
    n = 0
    for key, etag in remote_index(s3, bucket).items():
        if Path(key).suffix not in SYNCED_SUFFIXES:
            continue
        path = root / key
        if path.exists() and _md5(path) == etag:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(path))
        n += 1
    return n


def push(bucket: str, s3=None, root: Path | None = None) -> int:
    """Upload every local file that is missing or different in the bucket."""
    s3, root = s3 or client(), root or data_dir()
    remote = remote_index(s3, bucket)
    n = 0
    for key, path in local_files(root).items():
        if remote.get(key) == _md5(path):
            continue
        # put_object (not upload_file) so files are single-part and keep an md5 ETag
        s3.put_object(Bucket=bucket, Key=key, Body=path.read_bytes())
        n += 1
    return n
