"""
Render-oriented notice content helpers.

The crawler keeps the original body_html for auditability and also writes a
frontend-ready content_html field. content_html rewrites downloaded body images
to local file paths and appends image attachments that were not embedded in the
source body.
"""

from __future__ import annotations

import html
import re
from copy import deepcopy

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg"}

_IMG_SRC_RE = re.compile(
    r"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])(.*?)(\2)",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_STYLE_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_SINGLE_TAG_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|meta|link)\b[^>]*?/?>",
    re.IGNORECASE | re.DOTALL,
)
_EVENT_ATTR_RE = re.compile(
    r"\s+on[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE | re.DOTALL,
)
_JS_URL_ATTR_RE = re.compile(
    r"\s+(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_ext(value: str | None) -> str:
    return (value or "").rsplit(".", 1)[-1].lower().strip()


def is_image_attachment(attachment: dict) -> bool:
    mime_type = (attachment.get("mime_type") or "").lower()
    ext = _normalize_ext(attachment.get("ext") or attachment.get("name"))
    return mime_type.startswith("image/") or ext in IMAGE_EXTS


def _is_downloaded(attachment: dict) -> bool:
    return bool(attachment.get("download_ok") and attachment.get("local_path"))


def _strip_unsafe_html(value: str) -> str:
    cleaned = _SCRIPT_STYLE_RE.sub("", value or "")
    cleaned = _DANGEROUS_SINGLE_TAG_RE.sub("", cleaned)
    cleaned = _EVENT_ATTR_RE.sub("", cleaned)
    cleaned = _JS_URL_ATTR_RE.sub("", cleaned)
    return cleaned


def _body_image_attachments(attachments: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    by_url: dict[str, dict] = {}
    inline_images: list[dict] = []

    for attachment in attachments:
        if not (is_image_attachment(attachment) and _is_downloaded(attachment)):
            continue

        url = (attachment.get("url") or "").strip()
        name = attachment.get("name") or ""
        if url:
            by_url[url] = attachment
        elif "_inline_img_" in name or "inline_img_" in name:
            inline_images.append(attachment)

    return by_url, inline_images


def _rewrite_body_image_sources(
    body_html: str,
    attachments: list[dict],
) -> tuple[str, list[dict], set[str]]:
    by_url, inline_images = _body_image_attachments(attachments)
    inline_index = 0
    used_file_keys: set[str] = set()
    body_images: list[dict] = []

    def replace(match: re.Match) -> str:
        nonlocal inline_index

        prefix, quote, src, suffix = match.groups()
        src = (src or "").strip()
        attachment = None

        if src.startswith("data:image/"):
            if inline_index < len(inline_images):
                attachment = inline_images[inline_index]
                inline_index += 1
        else:
            attachment = by_url.get(src)

        if not attachment:
            return match.group(0)

        local_path = attachment["local_path"]
        file_key = attachment.get("file_key") or attachment.get("name") or local_path
        used_file_keys.add(file_key)
        body_images.append(
            {
                "file_key": file_key,
                "name": attachment.get("name") or file_key,
                "src": local_path,
                "original_src": src,
                "mime_type": attachment.get("mime_type"),
                "file_size": attachment.get("file_size"),
            }
        )
        return f'{prefix}{quote}{html.escape(local_path, quote=True)}{suffix}'

    return _IMG_SRC_RE.sub(replace, body_html or ""), body_images, used_file_keys


def _image_attachment_gallery(
    attachments: list[dict],
    used_file_keys: set[str],
) -> tuple[str, list[dict]]:
    figures: list[str] = []
    gallery_images: list[dict] = []

    for attachment in attachments:
        if not (is_image_attachment(attachment) and _is_downloaded(attachment)):
            continue

        file_key = attachment.get("file_key") or attachment.get("name") or attachment["local_path"]
        if file_key in used_file_keys:
            continue

        name = attachment.get("name") or file_key
        src = attachment["local_path"]
        gallery_images.append(
            {
                "file_key": file_key,
                "name": name,
                "src": src,
                "original_src": attachment.get("url") or "",
                "mime_type": attachment.get("mime_type"),
                "file_size": attachment.get("file_size"),
            }
        )
        figures.append(
            "<figure class=\"notice-content-image\">"
            f"<img src=\"{html.escape(src, quote=True)}\" "
            f"alt=\"{html.escape(name, quote=True)}\" loading=\"lazy\">"
            f"<figcaption>{html.escape(name)}</figcaption>"
            "</figure>"
        )

    if not figures:
        return "", gallery_images

    return (
        "<section class=\"notice-content-image-attachments\">"
        + "".join(figures)
        + "</section>"
    ), gallery_images


def build_content_payload(body_html: str, attachments: list[dict] | None) -> dict:
    """
    Build render-ready fields from the raw notice body and downloaded files.

    Returns fields that can be merged into the raw notice JSON:
      - content_html: sanitized, render-ready HTML
      - content_assets: image/file metadata for API consumers
      - content_stats: quick counts for importer/tests
    """
    attachments = attachments or []
    safe_body_html = _strip_unsafe_html(body_html or "")
    rewritten_html, body_images, used_file_keys = _rewrite_body_image_sources(
        safe_body_html,
        attachments,
    )
    gallery_html, gallery_images = _image_attachment_gallery(attachments, used_file_keys)
    content_html = (rewritten_html + gallery_html).strip()

    downloadable_files = [
        {
            "file_key": attachment.get("file_key"),
            "name": attachment.get("name"),
            "local_path": attachment.get("local_path"),
            "mime_type": attachment.get("mime_type"),
            "file_size": attachment.get("file_size"),
            "ext": attachment.get("ext"),
        }
        for attachment in attachments
        if _is_downloaded(attachment) and not is_image_attachment(attachment)
    ]

    images = body_images + gallery_images
    return {
        "content_html": content_html,
        "content_assets": {
            "images": images,
            "files": downloadable_files,
        },
        "content_stats": {
            "image_count": len(images),
            "file_count": len(downloadable_files),
            "table_count": len(re.findall(r"<\s*table\b", content_html, re.IGNORECASE)),
        },
    }


def attach_content_payload(notice: dict) -> dict:
    """Return a notice copy with render-ready content fields attached."""
    result = deepcopy(notice)
    result.update(
        build_content_payload(
            result.get("body_html") or "",
            result.get("attachments") or [],
        )
    )
    return result
