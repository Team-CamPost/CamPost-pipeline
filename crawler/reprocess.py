"""Shared helpers for raw JSON reprocessing metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

KEY_INFO_EXTRACTION_VERSION = 2
KEY_INFO_BACKFILL_VERSION = 1
CONTENT_PAYLOAD_VERSION = 1

KEY_INFO_VERSION_FIELD = "key_info_extraction_version"
KEY_INFO_BACKFILL_VERSION_FIELD = "key_info_backfill_version"
CONTENT_VERSION_FIELD = "content_version"
RAW_REPROCESSED_AT_FIELD = "raw_reprocessed_at"

KEY_INFO_FIELDS = {"deadline", "deadline_time", "deadline_at", "target", "apply_method"}
CONTENT_FIELDS = {"content_html", "content_assets", "content_stats"}


def build_deadline_at(deadline: str | None, deadline_time: str | None) -> str | None:
    if not deadline or not deadline_time:
        return None
    return f"{deadline}T{deadline_time}:00+09:00"


def has_key_info_input(data: dict[str, Any]) -> bool:
    body_text = data.get("body_text") or ""
    attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
    return bool(
        body_text
        or any(
            isinstance(attachment, dict) and attachment.get("extracted_text")
            for attachment in attachments
        )
    )


def needs_key_info_backfill(data: dict[str, Any]) -> bool:
    return (
        has_key_info_input(data)
        and data.get(KEY_INFO_BACKFILL_VERSION_FIELD) != KEY_INFO_BACKFILL_VERSION
    )


def needs_content_backfill(data: dict[str, Any]) -> bool:
    return (
        data.get(CONTENT_VERSION_FIELD) != CONTENT_PAYLOAD_VERSION
        or "content_html" not in data
        or "content_assets" not in data
        or "content_stats" not in data
    )


def stamp_key_info_backfill(data: dict[str, Any]) -> None:
    data[KEY_INFO_BACKFILL_VERSION_FIELD] = KEY_INFO_BACKFILL_VERSION


def stamp_key_info_extraction(data: dict[str, Any]) -> None:
    data[KEY_INFO_VERSION_FIELD] = KEY_INFO_EXTRACTION_VERSION
    stamp_key_info_backfill(data)


def stamp_content(data: dict[str, Any]) -> None:
    data[CONTENT_VERSION_FIELD] = CONTENT_PAYLOAD_VERSION


def stamp_reprocessed_at(data: dict[str, Any]) -> None:
    data[RAW_REPROCESSED_AT_FIELD] = datetime.now(timezone.utc).isoformat()
