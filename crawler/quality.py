"""Quality audit helpers for CAMPOST raw notice JSON files."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
EXTRACTABLE_EXTS = {"pdf", "hwp", "hwpx", "docx"}
FULL_PARSERS = {"pyhwp_bodytext", "pdfplumber", "hwpx_xml", "docx_xml"}
PREVIEW_PARSERS = {"olefile_prvtext"}
ALLOWED_PARSE_QUALITIES = {"full", "preview", "none"}
ALLOWED_CONVERSION_STATUSES = {
    "success",
    "failed",
    "timeout",
    "unavailable",
    "disabled",
    "download_failed",
    "not_applicable",
}


@dataclass(frozen=True)
class RawNotice:
    path: Path
    data: dict[str, Any]

    @property
    def article_id(self) -> str:
        return str(self.data.get("article_id") or self.path.stem)

    @property
    def title(self) -> str:
        return str(self.data.get("title") or "")


def load_raw_notices(raw_dir: Path) -> tuple[list[RawNotice], list[dict[str, str]]]:
    notices: list[RawNotice] = []
    errors: list[dict[str, str]] = []
    for path in sorted(raw_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})
            continue
        if not isinstance(data, dict):
            errors.append({"file": path.name, "error": "top-level JSON is not an object"})
            continue
        notices.append(RawNotice(path=path, data=data))
    return notices, errors


def _issue(
    code: str, article_id: str, message: str, severity: str = "warning", **extra: Any
) -> dict[str, Any]:
    result = {
        "severity": severity,
        "code": code,
        "article_id": article_id,
        "message": message,
    }
    result.update(extra)
    return result


def _counter(items: list[dict[str, Any]], key: str = "code") -> dict[str, int]:
    return dict(Counter(str(item.get(key)) for item in items))


def _file_sha256(path: Path) -> str | None:
    try:
        sha256 = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except OSError:
        return None


def _resolve_local_path(files_root: Path, local_path: str | None) -> Path | None:
    if not local_path:
        return None
    normalized = local_path.replace("\\", "/")
    if normalized.startswith("files/"):
        return files_root / normalized.removeprefix("files/")
    return files_root / Path(normalized).name


def audit_content_html(notices: list[RawNotice], files_root: Path | None = None) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    stats = {
        "notices": len(notices),
        "with_content_html": 0,
        "empty_content_html": 0,
        "with_tables": 0,
        "with_images": 0,
    }

    for notice in notices:
        article_id = notice.article_id
        data = notice.data
        content_html = data.get("content_html")
        content_assets = data.get("content_assets")
        content_stats = data.get("content_stats")
        body_html = data.get("body_html")
        attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []

        if "content_html" not in data:
            issues.append(
                _issue("missing_content_html", article_id, "content_html field is missing", "error")
            )
            continue
        if not isinstance(content_html, str):
            issues.append(
                _issue("invalid_content_html", article_id, "content_html is not a string", "error")
            )
            continue

        if content_html.strip():
            stats["with_content_html"] += 1
        else:
            stats["empty_content_html"] += 1
            if body_html or any(
                str(a.get("ext", "")).lower() in IMAGE_EXTS
                for a in attachments
                if isinstance(a, dict)
            ):
                issues.append(
                    _issue(
                        "empty_content_html",
                        article_id,
                        "content_html is empty despite source body or image attachments",
                    )
                )

        if not isinstance(content_assets, dict):
            issues.append(
                _issue(
                    "missing_content_assets",
                    article_id,
                    "content_assets is missing or invalid",
                    "error",
                )
            )
            content_assets = {}
        if not isinstance(content_stats, dict):
            issues.append(
                _issue(
                    "missing_content_stats",
                    article_id,
                    "content_stats is missing or invalid",
                    "error",
                )
            )
            content_stats = {}

        soup = BeautifulSoup(content_html, "html.parser")
        tables = soup.find_all("table")
        images = soup.find_all("img")
        if tables:
            stats["with_tables"] += 1
        if images:
            stats["with_images"] += 1

        if soup.find("script") or soup.find("iframe") or soup.find("object") or soup.find("embed"):
            issues.append(
                _issue("unsafe_tag", article_id, "content_html still contains unsafe tags", "error")
            )

        for tag in soup.find_all(True):
            for attr_name, attr_value in list(tag.attrs.items()):
                if attr_name.lower().startswith("on"):
                    issues.append(
                        _issue(
                            "unsafe_event_attr",
                            article_id,
                            f"event handler attribute remains: {attr_name}",
                            "error",
                        )
                    )
                values = attr_value if isinstance(attr_value, list) else [attr_value]
                if any(
                    isinstance(value, str) and value.strip().lower().startswith("javascript:")
                    for value in values
                ):
                    issues.append(
                        _issue(
                            "unsafe_javascript_url",
                            article_id,
                            f"javascript URL remains on {tag.name}.{attr_name}",
                            "error",
                        )
                    )

        for image in images:
            src = str(image.get("src") or "")
            if src.startswith("http://") or src.startswith("https://"):
                issues.append(
                    _issue(
                        "external_image_src",
                        article_id,
                        f"image still points to external URL: {src[:120]}",
                    )
                )
            if src.startswith("data:image"):
                issues.append(
                    _issue(
                        "inline_data_image_src",
                        article_id,
                        "inline data image remains in content_html",
                    )
                )
            if files_root and src.startswith("files/"):
                resolved = _resolve_local_path(files_root, src)
                if resolved and not resolved.exists():
                    issues.append(
                        _issue(
                            "missing_content_image_file",
                            article_id,
                            f"content image file is missing: {src}",
                        )
                    )

        expected_image_count = len(content_assets.get("images") or [])
        expected_file_count = len(content_assets.get("files") or [])
        actual_image_count = len(images)
        actual_table_count = len(tables)
        if content_stats.get("image_count") != expected_image_count:
            issues.append(
                _issue(
                    "image_count_mismatch",
                    article_id,
                    "content_stats.image_count does not match content_assets.images",
                    expected=expected_image_count,
                    actual=content_stats.get("image_count"),
                )
            )
        if content_stats.get("file_count") != expected_file_count:
            issues.append(
                _issue(
                    "file_count_mismatch",
                    article_id,
                    "content_stats.file_count does not match content_assets.files",
                    expected=expected_file_count,
                    actual=content_stats.get("file_count"),
                )
            )
        if content_stats.get("table_count") != actual_table_count:
            issues.append(
                _issue(
                    "table_count_mismatch",
                    article_id,
                    "content_stats.table_count does not match content_html tables",
                    expected=actual_table_count,
                    actual=content_stats.get("table_count"),
                )
            )
        if expected_image_count and actual_image_count == 0:
            issues.append(
                _issue(
                    "asset_images_not_rendered",
                    article_id,
                    "content_assets has images but content_html has no img tags",
                )
            )

    return {
        "summary": {
            **stats,
            "issue_count": len(issues),
            "issue_counts": _counter(issues),
        },
        "issues": issues,
    }


def _expected_parse_quality(parser: str, parse_ok: bool) -> str:
    if parse_ok and parser in FULL_PARSERS:
        return "full"
    if parse_ok and parser in PREVIEW_PARSERS:
        return "preview"
    return "none"


def audit_attachment_quality(notices: list[RawNotice], files_root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    parser_counts: Counter[str] = Counter()
    ext_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    conversion_counts: Counter[str] = Counter()
    total_attachments = 0

    for notice in notices:
        article_id = notice.article_id
        attachments = notice.data.get("attachments")
        if attachments is None:
            continue
        if not isinstance(attachments, list):
            issues.append(
                _issue("invalid_attachments", article_id, "attachments is not a list", "error")
            )
            continue

        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                issues.append(
                    _issue(
                        "invalid_attachment_item",
                        article_id,
                        "attachment item is not an object",
                        "error",
                        index=index,
                    )
                )
                continue

            total_attachments += 1
            ext = str(attachment.get("ext") or "").lower()
            parser = str(attachment.get("parser") or "missing")
            quality = str(attachment.get("parse_quality") or "missing")
            download_ok = bool(attachment.get("download_ok"))
            parse_ok = bool(attachment.get("parse_ok"))
            extracted_text = attachment.get("extracted_text") or ""
            extracted_chars = attachment.get("extracted_chars")
            local_path = attachment.get("local_path")
            conversion_status = attachment.get("conversion_status")

            ext_counts[ext] += 1
            parser_counts[parser] += 1
            quality_counts[quality] += 1
            if conversion_status is not None:
                conversion_counts[str(conversion_status)] += 1

            if download_ok and not local_path:
                issues.append(
                    _issue(
                        "missing_local_path",
                        article_id,
                        "downloaded attachment has no local_path",
                        "error",
                        index=index,
                    )
                )

            resolved = _resolve_local_path(files_root, local_path)
            if download_ok and resolved and not resolved.exists():
                issues.append(
                    _issue(
                        "missing_attachment_file",
                        article_id,
                        f"downloaded attachment file is missing: {local_path}",
                        "error",
                        index=index,
                    )
                )
                resolved = None

            if resolved and resolved.exists():
                actual_size = resolved.stat().st_size
                if (
                    attachment.get("file_size") is not None
                    and attachment.get("file_size") != actual_size
                ):
                    issues.append(
                        _issue(
                            "file_size_mismatch",
                            article_id,
                            "attachment file_size does not match disk file",
                            index=index,
                            expected=actual_size,
                            actual=attachment.get("file_size"),
                        )
                    )
                checksum = attachment.get("checksum")
                if checksum:
                    actual_checksum = _file_sha256(resolved)
                    if actual_checksum and checksum != actual_checksum:
                        issues.append(
                            _issue(
                                "checksum_mismatch",
                                article_id,
                                "attachment checksum does not match disk file",
                                "error",
                                index=index,
                            )
                        )

            if quality not in ALLOWED_PARSE_QUALITIES:
                issues.append(
                    _issue(
                        "invalid_parse_quality",
                        article_id,
                        f"invalid parse_quality: {quality}",
                        "error",
                        index=index,
                    )
                )
            if parser == "missing":
                issues.append(
                    _issue(
                        "missing_parser", article_id, "attachment parser is missing", index=index
                    )
                )
            if "parse_quality" not in attachment:
                issues.append(
                    _issue(
                        "missing_parse_quality",
                        article_id,
                        "attachment parse_quality is missing",
                        index=index,
                    )
                )
            if "extracted_chars" not in attachment:
                issues.append(
                    _issue(
                        "missing_extracted_chars",
                        article_id,
                        "attachment extracted_chars is missing",
                        index=index,
                    )
                )
            elif extracted_chars != len(extracted_text):
                issues.append(
                    _issue(
                        "extracted_chars_mismatch",
                        article_id,
                        "extracted_chars does not match extracted_text length",
                        index=index,
                        expected=len(extracted_text),
                        actual=extracted_chars,
                    )
                )

            if parse_ok != bool(extracted_text):
                issues.append(
                    _issue(
                        "parse_ok_mismatch",
                        article_id,
                        "parse_ok does not match extracted_text presence",
                        index=index,
                    )
                )

            expected_quality = _expected_parse_quality(parser, parse_ok)
            if quality in ALLOWED_PARSE_QUALITIES and quality != expected_quality:
                issues.append(
                    _issue(
                        "parse_quality_mismatch",
                        article_id,
                        "parse_quality does not match parser/parse_ok",
                        index=index,
                        expected=expected_quality,
                        actual=quality,
                    )
                )

            if download_ok and ext in EXTRACTABLE_EXTS and parser == "none":
                issues.append(
                    _issue(
                        "extractable_not_parsed",
                        article_id,
                        f"extractable attachment was not parsed: .{ext}",
                        index=index,
                    )
                )

            if (
                conversion_status is not None
                and conversion_status not in ALLOWED_CONVERSION_STATUSES
            ):
                issues.append(
                    _issue(
                        "invalid_conversion_status",
                        article_id,
                        f"invalid conversion_status: {conversion_status}",
                        "error",
                        index=index,
                    )
                )

            preview_pdf_path = attachment.get("preview_pdf_path")
            if conversion_status == "success":
                if not preview_pdf_path:
                    issues.append(
                        _issue(
                            "missing_preview_pdf_path",
                            article_id,
                            "successful PDF conversion has no preview_pdf_path",
                            "error",
                            index=index,
                        )
                    )
                    continue
                resolved_pdf = _resolve_local_path(files_root, str(preview_pdf_path))
                if not resolved_pdf or not resolved_pdf.exists():
                    issues.append(
                        _issue(
                            "missing_preview_pdf_file",
                            article_id,
                            f"preview PDF file is missing: {preview_pdf_path}",
                            "error",
                            index=index,
                        )
                    )
                    continue

                actual_pdf_size = resolved_pdf.stat().st_size
                if attachment.get("preview_pdf_size") is None:
                    issues.append(
                        _issue(
                            "missing_preview_pdf_size",
                            article_id,
                            "successful PDF conversion has no preview_pdf_size",
                            "error",
                            index=index,
                        )
                    )
                elif attachment.get("preview_pdf_size") != actual_pdf_size:
                    issues.append(
                        _issue(
                            "preview_pdf_size_mismatch",
                            article_id,
                            "preview_pdf_size does not match disk file",
                            index=index,
                            expected=actual_pdf_size,
                            actual=attachment.get("preview_pdf_size"),
                        )
                    )
                preview_checksum = attachment.get("preview_pdf_checksum")
                if not preview_checksum:
                    issues.append(
                        _issue(
                            "missing_preview_pdf_checksum",
                            article_id,
                            "successful PDF conversion has no preview_pdf_checksum",
                            "error",
                            index=index,
                        )
                    )
                else:
                    actual_preview_checksum = _file_sha256(resolved_pdf)
                    if actual_preview_checksum and preview_checksum != actual_preview_checksum:
                        issues.append(
                            _issue(
                                "preview_pdf_checksum_mismatch",
                                article_id,
                                "preview_pdf_checksum does not match disk file",
                                "error",
                                index=index,
                            )
                        )
            elif preview_pdf_path:
                issues.append(
                    _issue(
                        "preview_pdf_without_success",
                        article_id,
                        "preview_pdf_path is set but conversion_status is not success",
                        index=index,
                    )
                )

    return {
        "summary": {
            "notices": len(notices),
            "attachments": total_attachments,
            "issue_count": len(issues),
            "issue_counts": _counter(issues),
            "ext_counts": dict(ext_counts),
            "parser_counts": dict(parser_counts),
            "parse_quality_counts": dict(quality_counts),
            "conversion_status_counts": dict(conversion_counts),
        },
        "issues": issues,
    }


def normalize_attachment_metadata(
    notices: list[RawNotice],
    files_root: Path,
    *,
    refresh_file_metadata: bool = False,
) -> list[dict[str, Any]]:
    """Return per-notice attachment metadata changes without writing files."""
    changes: list[dict[str, Any]] = []
    for notice in notices:
        attachments = notice.data.get("attachments")
        if not isinstance(attachments, list):
            continue

        notice_changes: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                continue

            parser = str(attachment.get("parser") or "none")
            extracted_text = attachment.get("extracted_text") or ""
            parse_ok = bool(extracted_text)
            updates: dict[str, Any] = {}

            expected_chars = len(extracted_text)
            if attachment.get("extracted_chars") != expected_chars:
                updates["extracted_chars"] = expected_chars
            if attachment.get("parse_ok") != parse_ok:
                updates["parse_ok"] = parse_ok

            expected_quality = _expected_parse_quality(parser, parse_ok)
            if attachment.get("parse_quality") != expected_quality:
                updates["parse_quality"] = expected_quality

            if "download_cached" not in attachment:
                updates["download_cached"] = False

            if refresh_file_metadata and attachment.get("download_ok"):
                resolved = _resolve_local_path(files_root, attachment.get("local_path"))
                if resolved and resolved.exists():
                    file_size = resolved.stat().st_size
                    checksum = _file_sha256(resolved)
                    if attachment.get("file_size") != file_size:
                        updates["file_size"] = file_size
                    if checksum and attachment.get("checksum") != checksum:
                        updates["checksum"] = checksum

            if updates:
                notice_changes.append(
                    {
                        "index": index,
                        "name": attachment.get("name"),
                        "updates": updates,
                    }
                )

        if notice_changes:
            changes.append(
                {
                    "path": str(notice.path),
                    "article_id": notice.article_id,
                    "changes": notice_changes,
                }
            )

    return changes


def find_duplicate_attachments(notices: list[RawNotice]) -> dict[str, Any]:
    by_checksum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    same_notice_issues: list[dict[str, Any]] = []
    total_attachments = 0

    for notice in notices:
        attachments = notice.data.get("attachments")
        if not isinstance(attachments, list):
            continue

        seen_in_notice: dict[tuple[str, str], int] = {}
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                continue
            total_attachments += 1
            record = {
                "article_id": notice.article_id,
                "title": notice.title,
                "index": index,
                "name": attachment.get("name"),
                "file_key": attachment.get("file_key"),
                "local_path": attachment.get("local_path"),
                "url": attachment.get("url"),
                "checksum": attachment.get("checksum"),
            }
            checksum = attachment.get("checksum")
            if checksum:
                by_checksum[str(checksum)].append(record)
            url = attachment.get("url")
            if url:
                by_url[str(url)].append(record)

            duplicate_key = (
                str(
                    attachment.get("checksum")
                    or attachment.get("url")
                    or attachment.get("file_key")
                    or ""
                ),
                str(attachment.get("local_path") or ""),
            )
            if duplicate_key[0]:
                if duplicate_key in seen_in_notice:
                    same_notice_issues.append(
                        {
                            "article_id": notice.article_id,
                            "first_index": seen_in_notice[duplicate_key],
                            "duplicate_index": index,
                            "name": attachment.get("name"),
                            "file_key": attachment.get("file_key"),
                            "local_path": attachment.get("local_path"),
                        }
                    )
                else:
                    seen_in_notice[duplicate_key] = index

    checksum_groups = [items for items in by_checksum.values() if len(items) > 1]
    url_groups = [items for items in by_url.values() if len(items) > 1]
    return {
        "summary": {
            "notices": len(notices),
            "attachments": total_attachments,
            "duplicate_checksum_groups": len(checksum_groups),
            "duplicate_url_groups": len(url_groups),
            "same_notice_duplicates": len(same_notice_issues),
        },
        "duplicate_checksum_groups": checksum_groups,
        "duplicate_url_groups": url_groups,
        "same_notice_duplicates": same_notice_issues,
    }
