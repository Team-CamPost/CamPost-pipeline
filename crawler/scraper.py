"""
CamPost Crawler — Playwright 기반 스크래퍼
목록 페이지 + 상세 페이지 파싱 담당

card 타입 (SW학과):  .dku-list-body-item 행, .item-title h4 a 제목
table 타입 (나머지): tr 행, a[href="#none"] 제목
두 타입 모두 동일 포틀릿(dku_bbs_web_BbsPortlet) 사용, 상세 URL 패턴 동일.
"""

import asyncio
import logging

from playwright.async_api import Page

from .config import (
    DETAIL_URL_TEMPLATE,
    PAGE_TIMEOUT,
    REQUEST_DELAY,
    SELECTOR_TIMEOUT,
    SELECTORS_CARD,
    SELECTORS_TABLE,
)

log = logging.getLogger("campost.scraper")


def _make_detail_url(base_url: str, raw_id: str) -> str:
    return DETAIL_URL_TEMPLATE.format(base_url=base_url, raw_id=raw_id)


async def fetch_list(page: Page, source: dict) -> list[dict]:
    """
    공지사항 목록 페이지에서 게시글 기본 정보 추출.

    Args:
        source: SOURCES 리스트의 소스 딕셔너리
                {"id", "name", "code", "base_url", "crawler_type"}

    Returns:
        [
            {
                article_id,   # "{code}_{raw_id}" — 전역 유일 ID
                raw_id,       # 숫자 문자열 — 상세 URL 빌드용
                title, is_pinned, post_number,
                author, date, views, has_attachment
            },
            ...
        ]
    """
    code = source["code"]
    base_url = source["base_url"]
    crawler_type = source.get("crawler_type", "card")

    if crawler_type == "table":
        sel = SELECTORS_TABLE
        list_fetcher = _fetch_list_table
    elif crawler_type == "card":
        sel = SELECTORS_CARD
        list_fetcher = _fetch_list_card
    else:
        log.warning(
            f"[{source['name']}] 알 수 없는 crawler_type={crawler_type}. card 타입으로 fallback"
        )
        sel = SELECTORS_CARD
        list_fetcher = _fetch_list_card

    await page.goto(base_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    await page.wait_for_selector(sel["title_anchor"], timeout=SELECTOR_TIMEOUT)

    items = await list_fetcher(page, sel)

    # article_id prefix 부착 — fetch_list 단계에서 즉시 확정
    for item in items:
        item["article_id"] = f"{code}_{item['raw_id']}"

    log.info(f"[{source['name']}] 목록 수집 완료: {len(items)}건")
    return items


async def _fetch_list_card(page: Page, sel: dict) -> list[dict]:
    """card 타입 목록 파싱 (SW학과, PoC 검증 완료)."""
    return await page.evaluate(
        """(sel) => {
            const rows = document.querySelectorAll(sel.list_item);
            const results = [];

            rows.forEach(row => {
                const titleEl = row.querySelector(sel.title_anchor);
                if (!titleEl) return;

                const onclick = titleEl.getAttribute('onclick') || '';
                const idMatch = onclick.match(/viewMessage\\((\\d+)/);
                if (!idMatch) return;

                const cols = row.querySelectorAll('.dku-list-body-item-col');
                const colTexts = Array.from(cols).map(c => c.textContent.trim());
                const isPinned = !!row.querySelector('.badge-primary');

                results.push({
                    raw_id:         idMatch[1],
                    title:          titleEl.getAttribute('title') || titleEl.textContent.trim(),
                    is_pinned:      isPinned,
                    post_number:    isPinned ? null : (colTexts[0] || null),
                    author:         colTexts[2] || '',
                    date:           colTexts[3] || '',
                    views:          colTexts[4] || '0',
                    has_attachment: colTexts[5] !== '',
                });
            });

            return results;
        }""",
        sel,
    )


async def _fetch_list_table(page: Page, sel: dict) -> list[dict]:
    """table 타입 목록 파싱 (ACE, MOBILE, STAT, INDSEC, SWCU)."""
    return await page.evaluate(
        """(sel) => {
            const rows = document.querySelectorAll('tr');
            const results = [];

            rows.forEach(row => {
                const titleEl = row.querySelector('a[href="#none"]');
                if (!titleEl) return;

                const onclick = titleEl.getAttribute('onclick') || '';
                const idMatch = onclick.match(/viewMessage\\((\\d+)/);
                if (!idMatch) return;

                const cols = row.querySelectorAll('td');
                const colTexts = Array.from(cols).map(c => c.textContent.trim());

                // 고정글 여부: 번호 컬럼이 숫자가 아니거나 공지/notice 텍스트인 경우
                const firstCol = colTexts[0] || '';
                const isPinned = !/^\\d+$/.test(firstCol);

                // 첨부파일: img 또는 아이콘 존재 여부
                const hasAttachment = !!row.querySelector('img') || colTexts.some(t => t === 'Y');

                results.push({
                    raw_id:         idMatch[1],
                    title:          titleEl.textContent.trim(),
                    is_pinned:      isPinned,
                    post_number:    isPinned ? null : (firstCol || null),
                    author:         colTexts[2] || '',
                    date:           colTexts[3] || '',
                    views:          colTexts[4] || '0',
                    has_attachment: hasAttachment,
                });
            });

            return results;
        }""",
        sel,
    )


async def fetch_detail(page: Page, raw_id: str, base_url: str) -> dict:
    """
    상세 페이지에서 본문·카테고리·첨부파일 추출.

    Args:
        raw_id:   숫자 문자열 (prefix 없는 원본 ID)
        base_url: 소스별 base_url

    Returns:
        {body_text, category, attachments: [{name, url, ext}]}
    """
    url = _make_detail_url(base_url, raw_id)

    try:
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        await page.wait_for_selector(SELECTORS_CARD["detail_table"], timeout=10_000)
    except Exception as exc:
        log.warning(f"상세 페이지 로드 실패 (raw_id={raw_id}): {exc}")
        return {"body_text": "", "category": "", "attachments": []}

    result: dict = await page.evaluate(
        """(sel) => {
            const bodyEl = document.querySelector(sel.body);
            const bodyText = bodyEl ? bodyEl.innerText.trim() : '';

            let category = '';
            const rows = document.querySelectorAll('table[summary*="게시판"] tr');
            rows.forEach(row => {
                const th = row.querySelector('th');
                const td = row.querySelector('td');
                if (th && td && th.textContent.includes('분류')) {
                    category = td.textContent.trim();
                }
            });

            const fileLinks = document.querySelectorAll(sel.attachment);
            const attachments = Array.from(fileLinks).map(a => ({
                name: a.textContent.trim(),
                url:  a.href,
                ext:  a.textContent.trim().split('.').pop().toLowerCase(),
            }));

            return { body_text: bodyText, category, attachments };
        }""",
        SELECTORS_CARD,
    )

    log.debug(
        f"상세 수집 완료 (raw_id={raw_id}): "
        f"본문 {len(result['body_text'])}자, 첨부 {len(result['attachments'])}개"
    )
    await asyncio.sleep(REQUEST_DELAY)
    return result
