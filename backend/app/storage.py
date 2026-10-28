"""Files: uploaded documents, invoice PDFs and photos, rendered digests.

Keyed `{restaurant_id}/{doc_type}/{uuid}.{ext}`. The database holds keys, never bytes.
Local disk under STORAGE_DIR by default (one container, free hosting); an S3-compatible
bucket when S3_BUCKET is set (boto3, installed on demand). Downloads go through
signed URLs that expire after fifteen minutes, so a key in a page is not a public link.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid
from pathlib import Path

from app.config import settings

SIGNED_URL_SECONDS = 15 * 60


def _bucket() -> str | None:
    return os.getenv("S3_BUCKET", "").strip() or None


def new_key(restaurant_id: str | None, doc_type: str, filename: str | None = None) -> str:
    ext = ""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[1].lower()[:8]
    return f"{restaurant_id or 'site'}/{doc_type}/{uuid.uuid4().hex}{ext}"


def _local_path(key: str) -> Path:
    root = Path(settings.storage_dir).resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("bad storage key")
    return path


def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    bucket = _bucket()
    if bucket:
        import boto3  # optional: only where an object store is configured

        boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None).put_object(
            Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return key
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def get(key: str) -> bytes | None:
    bucket = _bucket()
    if bucket:
        import boto3

        try:
            body = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None).get_object(Bucket=bucket, Key=key)["Body"]
            return body.read()
        except Exception:
            return None
    path = _local_path(key)
    return path.read_bytes() if path.exists() else None


def delete(key: str) -> None:
    bucket = _bucket()
    if bucket:
        import boto3

        boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None).delete_object(Bucket=bucket, Key=key)
        return
    path = _local_path(key)
    if path.exists():
        path.unlink()


# --- signed links -------------------------------------------------------------------------


def _signature(key: str, expires: int) -> str:
    secret = (settings.secret_key or "development-only-key").encode()
    digest = hmac.new(secret, f"{key}:{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign(key: str, seconds: int = SIGNED_URL_SECONDS) -> tuple[int, str]:
    expires = int(time.time()) + seconds
    return expires, _signature(key, expires)


def verify(key: str, expires: int, signature: str) -> bool:
    return time.time() <= expires and hmac.compare_digest(_signature(key, expires), signature)


def signed_url(key: str, base: str = "/api/v1/platform/files") -> str:
    expires, signature = sign(key)
    return f"{base}/{key}?expires={expires}&sig={signature}"
