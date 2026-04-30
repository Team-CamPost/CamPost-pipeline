"""
Recover one raw JSON notice by crawling a detail page directly.

Usage:
  python scripts/recover_article.py <SOURCE_CODE> <RAW_ID>
Example:
  python scripts/recover_article.py SW 173021
"""

import asyncio
import glob
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

from crawler.config import GEMINI_API_KEY, GEMINI_MODEL, OUTPUT_DIR, SOURCES
from crawler.content import build_content_payload
from crawler.extractor import extract_key_info_with_ai
from crawler.file_handler import extract_external_images, extract_inline_images, process_attachments


def _detail_url(base_url: str, raw_id: str) -> str:
    return (
        f"{base_url}"
        "?p_p_id=dku_bbs_web_BbsPortlet"
        "&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
        "&_dku_bbs_web_BbsPortlet_action=view_message"
        f"&_dku_bbs_web_BbsPortlet_bbsMessageId={raw_id}"
    )


def _find_headless_shell() -> str | None:
    shells = glob.glob(
        "/root/.cache/ms-playwright/chromium_headless_shell-*/"
        "chrome-headless-shell-linux64/chrome-headless-shell"
    )
    return shells[0] if shells else None


async def recover(code: str, raw_id: str) -> None:
    code = code.upper()
    source = next((item for item in SOURCES if item["code"] == code), None)
    if source is None:
        print(f"[error] unknown source code: {code}")
        sys.exit(1)

    article_id = f"{code}_{raw_id}"
    detail_url = _detail_url(source["base_url"], raw_id)

    print(f"[1/4] crawling {detail_url}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=_find_headless_shell(),
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
            ],
        )
        page = await browser.new_page()
        await page.goto(detail_url, wait_until="networkidle", timeout=30000)

        title = ""
        for selector in [
            "table[summary*='게시판'] tr:first-child td",
            "table[summary*='게시판'] th",
            ".dku-bbs-view-title",
            ".item-title h4",
        ]:
            element = await page.query_selector(selector)
            if not element:
                continue
            text = (await element.inner_text()).strip()
            if len(text) > 5:
                title = text
                break

        author, date_str, views = "", "", "0"
        meta_elements = await page.query_selector_all("table[summary*='게시판'] tr:nth-child(2) td")
        if len(meta_elements) >= 3:
            author = (await meta_elements[0].inner_text()).strip()
            date_str = (await meta_elements[1].inner_text()).strip()
            views = (await meta_elements[2].inner_text()).strip()

        body_element = await page.query_selector("td.r_cont")
        body_text = (await body_element.inner_text()).strip() if body_element else ""
        body_html = (await body_element.inner_html()).strip() if body_element else ""

        attachment_links = await page.query_selector_all('a[href*="download=true"]')
        raw_attachments = []
        for link in attachment_links:
            href = await link.get_attribute("href") or ""
            name = (await link.inner_text()).strip()
            raw_attachments.append(
                {
                    "url": href,
                    "name": name,
                    "ext": name.split(".")[-1].lower() if "." in name else "",
                }
            )

        await browser.close()

    if not title:
        print("[warn] title extraction failed; using fallback title")
        title = f"[recovered] {article_id}"

    attachments = await process_attachments(raw_attachments, article_id)
    attachments.extend(extract_inline_images(body_html, article_id))
    attachments.extend(await extract_external_images(body_html, article_id))
    content_payload = build_content_payload(body_html, attachments)

    print(
        f"[2/4] extracted title={title[:60]!r} | body={len(body_text)} chars | "
        f"attachments={len(attachments)}"
    )

    print(f"[3/4] extracting key info with {GEMINI_MODEL}")
    extracted = extract_key_info_with_ai(
        body_text,
        attachments,
        GEMINI_API_KEY,
        GEMINI_MODEL,
        title=title,
        notice_date=date_str,
    )
    print(
        f"      deadline={extracted['deadline']} | "
        f"target={extracted['target']} | "
        f"apply_method={extracted['apply_method']}"
    )

    now = datetime.now(timezone.utc)
    hash_val = hashlib.sha256(f"{article_id}:{title}".encode("utf-8")).hexdigest()
    notice = {
        "article_id": article_id,
        "raw_id": raw_id,
        "title": title,
        "is_pinned": False,
        "post_number": "",
        "author": author,
        "date": date_str,
        "views": views,
        "has_attachment": len(attachments) > 0,
        "body_text": body_text,
        "body_html": body_html,
        **content_payload,
        "category": "",
        "source_id": source["id"],
        "source_url": detail_url,
        "hash": hash_val,
        "crawled_at": now.isoformat(),
        "deadline": extracted["deadline"],
        "target": extracted["target"],
        "apply_method": extracted["apply_method"],
        "attachments": attachments,
        "raw_saved_at": now.isoformat(),
    }

    raw_dir = Path(OUTPUT_DIR) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"{article_id}.json"
    out_path.write_text(json.dumps(notice, ensure_ascii=False, indent=2), encoding="utf-8")

    hashes_path = Path(OUTPUT_DIR) / "seen_hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8")) if hashes_path.exists() else []
    if hash_val not in hashes:
        hashes.append(hash_val)
        hashes_path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[4/4] saved {out_path}")
    print("      Importer should pick up the changed raw JSON file on its next cycle.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/recover_article.py <SOURCE_CODE> <RAW_ID>")
        sys.exit(1)
    asyncio.run(recover(sys.argv[1], sys.argv[2]))
