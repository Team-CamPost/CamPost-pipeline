"""
특정 article_id를 직접 크롤링해서 raw JSON 복구하는 스크립트.
사용법: python scripts/recover_article.py <SOURCE_CODE> <RAW_ID>
예시:   python scripts/recover_article.py SW 173021
"""

import asyncio
import glob as _glob
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OUTPUT_DIR,
    SOURCES,
)
from crawler.extractor import extract_key_info_with_ai


async def recover(code: str, raw_id: str) -> None:
    source = next((s for s in SOURCES if s["code"] == code), None)
    if source is None:
        print(f"[오류] 소스 코드 '{code}' 를 찾을 수 없습니다.")
        sys.exit(1)

    article_id = f"{code}_{raw_id}"
    base_url = source["base_url"]
    detail_url = (
        f"{base_url}"
        "?p_p_id=dku_bbs_web_BbsPortlet"
        "&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
        "&_dku_bbs_web_BbsPortlet_action=view_message"
        f"&_dku_bbs_web_BbsPortlet_bbsMessageId={raw_id}"
    )

    shells = _glob.glob(
        "/root/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
    )

    from playwright.async_api import async_playwright

    print(f"[1/4] 브라우저 실행 → {detail_url}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=shells[0] if shells else None,
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

        # 제목 추출
        title = ""
        for sel in [
            "table[summary*='게시판'] tr:first-child td",
            "table[summary*='게시판'] th",
            ".dku-bbs-view-title",
            ".item-title h4",
        ]:
            el = await page.query_selector(sel)
            if el:
                t = (await el.inner_text()).strip()
                if len(t) > 5:
                    title = t
                    break

        # 작성자/날짜/조회수 추출 (두 번째 행)
        author, date_str, views = "", "", "0"
        meta_els = await page.query_selector_all(
            "table[summary*='게시판'] tr:nth-child(2) td"
        )
        if len(meta_els) >= 3:
            author = (await meta_els[0].inner_text()).strip()
            date_str = (await meta_els[1].inner_text()).strip()
            views = (await meta_els[2].inner_text()).strip()

        # 본문
        body_el = await page.query_selector("td.r_cont")
        body_text = (await body_el.inner_text()).strip() if body_el else ""
        body_html = (await body_el.inner_html()).strip() if body_el else ""

        # 첨부파일
        att_els = await page.query_selector_all('a[href*="download=true"]')
        attachments = []
        for att in att_els:
            href = await att.get_attribute("href") or ""
            name = (await att.inner_text()).strip()
            attachments.append(
                {
                    "url": href,
                    "name": name,
                    "file_key": "",
                    "download_ok": False,
                    "extracted_text": "",
                    "parser": "none",
                    "parse_ok": False,
                }
            )

        await browser.close()

    if not title:
        print("[경고] 제목 추출 실패 — 임시 제목 사용")
        title = f"[복구] {article_id}"

    print(f"[2/4] 추출 완료 — 제목: {title[:60]} | 본문: {len(body_text)}자 | 첨부: {len(attachments)}개")

    # AI 추출
    print(f"[3/4] AI 추출 중 (모델: {GEMINI_MODEL}) ...")
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

    # raw JSON 저장
    now = datetime.now(timezone.utc)
    hash_val = hashlib.sha256(f"{article_id}:{title}".encode()).hexdigest()

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
    out_path.write_text(json.dumps(notice, ensure_ascii=False, indent=2))

    # seen_hashes 갱신
    hf = Path(OUTPUT_DIR) / "seen_hashes.json"
    hashes = json.loads(hf.read_text()) if hf.exists() else []
    if hash_val not in hashes:
        hashes.append(hash_val)
        hf.write_text(json.dumps(hashes, indent=2))

    print(f"[4/4] 저장 완료 → {out_path}")
    print("      Spring Boot Importer가 30초 내 자동 적재합니다.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("사용법: python scripts/recover_article.py <SOURCE_CODE> <RAW_ID>")
        sys.exit(1)
    asyncio.run(recover(sys.argv[1], sys.argv[2]))
