"""
Render-oriented notice content helpers.

The crawler keeps the original body_html for auditability and also writes a
frontend-ready content_html field. content_html rewrites downloaded body images
to local file paths and appends image attachments that were not embedded in the
source body.
"""

from __future__ import annotations

import html
from copy import deepcopy
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg"}

_DANGEROUS_TAGS = {"script", "style", "iframe", "object", "embed", "meta", "link"}
_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "caption",
    "code",
    "col",
    "colgroup",
    "dd",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "section",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
_GLOBAL_ATTRS = {"class", "title"}
_TAG_ATTRS = {
    "a": {"href", "target", "rel"},
    "col": {"span"},
    "img": {"src", "alt", "loading", "height", "width"},
    "td": {"colspan", "rowspan", "headers"},
    "th": {"colspan", "rowspan", "headers", "scope"},
}
_SAFE_HREF_SCHEMES = {"", "http", "https", "mailto", "tel"}
_SAFE_SRC_SCHEMES = {"", "http", "https"}
_SAFE_DATA_IMAGE_PREFIXES = (
    "data:image/jpeg;base64,",
    "data:image/png;base64,",
    "data:image/gif;base64,",
    "data:image/webp;base64,",
)


def _normalize_ext(value: str | None) -> str:
    return (value or "").rsplit(".", 1)[-1].lower().strip()


def is_image_attachment(attachment: dict) -> bool:
    mime_type = (attachment.get("mime_type") or "").lower()
    ext = _normalize_ext(attachment.get("ext") or attachment.get("name"))
    return mime_type.startswith("image/") or ext in IMAGE_EXTS


def _is_downloaded(attachment: dict) -> bool:
    return bool(attachment.get("download_ok") and attachment.get("local_path"))


def _is_safe_href(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme.lower() in _SAFE_HREF_SCHEMES


def _is_safe_src(value: str) -> bool:
    src = (value or "").strip()
    if src.startswith(_SAFE_DATA_IMAGE_PREFIXES):
        return True
    if src.startswith("files/"):
        return True
    parsed = urlparse(src)
    return parsed.scheme.lower() in _SAFE_SRC_SCHEMES


def _sanitize_body_html(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")

    for comment in soup.find_all(string=lambda item: isinstance(item, Comment)):
        comment.extract()

    for tag in list(soup.find_all(True)):
        name = (tag.name or "").lower()
        if name in _DANGEROUS_TAGS:
            tag.decompose()
            continue
        if name not in _ALLOWED_TAGS:
            tag.unwrap()
            continue

        allowed_attrs = _GLOBAL_ATTRS | _TAG_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            if attr.lower() not in allowed_attrs:
                del tag.attrs[attr]

        if tag.has_attr("href") and not _is_safe_href(str(tag["href"])):
            del tag.attrs["href"]
        if tag.has_attr("src") and not _is_safe_src(str(tag["src"])):
            del tag.attrs["src"]

        if name == "a" and tag.has_attr("target"):
            tag["rel"] = "noopener noreferrer"
        if name == "img":
            tag["loading"] = tag.get("loading") or "lazy"

    return str(soup)


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
            by_url[html.unescape(url)] = attachment
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

    soup = BeautifulSoup(body_html or "", "html.parser")

    for image in soup.find_all("img"):
        src = (image.get("src") or "").strip()
        attachment = None

        if src.startswith("data:image/"):
            if inline_index < len(inline_images):
                attachment = inline_images[inline_index]
                inline_index += 1
        else:
            attachment = by_url.get(src) or by_url.get(html.unescape(src))

        if not attachment:
            continue

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
        image["src"] = local_path
        image.attrs.pop("srcset", None)
        image.attrs.pop("sizes", None)

    return str(soup), body_images, used_file_keys


def _count_tables(content_html: str) -> int:
    soup = BeautifulSoup(content_html or "", "html.parser")
    return len(soup.find_all("table"))


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
    attachments = [item for item in (attachments or []) if isinstance(item, dict)]
    safe_body_html = _sanitize_body_html(body_html or "")
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
            "table_count": _count_tables(content_html),
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
