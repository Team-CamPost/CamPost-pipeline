"""Cloudflare R2 upload helper (S3-compatible API via boto3)."""

import logging
from pathlib import Path

log = logging.getLogger("campost.r2")

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    import boto3

    from .config import R2_ACCESS_KEY_ID, R2_ACCOUNT_ID, R2_SECRET_ACCESS_KEY

    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    return _client


def upload_to_r2(local_path: Path, object_key: str, content_type: str) -> str | None:
    """Upload a local file to R2. Returns public URL or None on failure/disabled."""
    from .config import R2_BUCKET_NAME, R2_ENABLED, R2_PUBLIC_URL

    if not R2_ENABLED:
        return None
    try:
        client = _get_client()
        client.upload_file(
            str(local_path),
            R2_BUCKET_NAME,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
        url = f"{R2_PUBLIC_URL.rstrip('/')}/{object_key}"
        log.debug(f"R2 업로드 완료: {object_key}")
        return url
    except Exception as exc:
        log.warning(f"R2 업로드 실패 ({object_key}): {exc}")
        return None
