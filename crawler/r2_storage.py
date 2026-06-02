"""Cloudflare R2 upload helpers for downloaded notice files."""

import logging
from pathlib import Path
from urllib.parse import quote

from .config import (
    R2_ACCESS_KEY_ID,
    R2_ACCOUNT_ID,
    R2_BUCKET_NAME,
    R2_PUBLIC_URL,
    R2_SECRET_ACCESS_KEY,
    R2_UPLOAD_ENABLED,
)

log = logging.getLogger("campost.r2_storage")


def _is_configured() -> bool:
    return bool(
        R2_UPLOAD_ENABLED
        and R2_ACCOUNT_ID
        and R2_ACCESS_KEY_ID
        and R2_SECRET_ACCESS_KEY
        and R2_BUCKET_NAME
        and R2_PUBLIC_URL
    )


def _public_url(key: str) -> str:
    return f"{R2_PUBLIC_URL}/{quote(key, safe='/-_.~')}"


def upload_file_to_r2(path: Path, key: str, content_type: str | None = None) -> str | None:
    """Upload a file to R2 and return its public URL.

    Upload failures are non-fatal for crawling. The caller keeps local metadata
    and the deployed frontend will simply omit the cloud-backed link.
    """
    if not _is_configured():
        return None
    if not path.is_file():
        log.warning("R2 upload skipped; file is missing: %s", path)
        return None

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        log.warning("R2 upload skipped; boto3 is not installed")
        return None

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    try:
        client.upload_file(str(path), R2_BUCKET_NAME, key, ExtraArgs=extra_args)
    except Exception as exc:
        log.warning("R2 upload failed (%s): %s", key, exc)
        return None

    return _public_url(key)
