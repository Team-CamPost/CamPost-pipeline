"""
CamPost Crawler — 패키지 진입점

Pipeline 흐름:
  Scheduler → run_all() → [소스별] run_source()
    → FetchList → HashFilter → FetchDetail
    → FileHandler → Extractor → SaveRaw → RawStore
    → DB: crawl_jobs/parse_logs 기록

사용법:
    python main.py          # 1회 실행 (전 학과)
    python main.py --loop   # APScheduler 반복 실행
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

from .config import CRAWL_INTERVAL_MINUTES, DETAIL_URL_TEMPLATE, HEADLESS, PAGE_TIMEOUT, SOURCES
from .db import create_crawl_job, finish_crawl_job, log_parse
from .extractor import extract_key_info
from .file_handler import process_attachments
from .scraper import fetch_detail, fetch_list
from .storage import compute_hash, load_seen_hashes, save_raw_json, save_seen_hashes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("campost.runner")


def _launch_args() -> list[str]:
    return [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-zygote",
    ]


def _find_headless_shell() -> str | None:
    import glob as _glob

    shells = _glob.glob(
        "/root/.cache/ms-playwright/chromium_headless_shell-*"
        "/chrome-headless-shell-linux64/chrome-headless-shell"
    )
    return shells[0] if shells else None


async def run_source(browser, source: dict, seen_hashes: set) -> dict:
    """
    단일 소스 크롤링 실행.

    Returns:
        {"total_found": int, "new_count": int, "skip_count": int, "fail_count": int,
         "parse_records": [{"file_key", "parser", "success", "chars", "error_msg"}]}
    """
    stats = {"total_found": 0, "new_count": 0, "skip_count": 0, "fail_count": 0}
    parse_records: list[dict] = []

    page = await browser.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)

    try:
        items = await fetch_list(page, source)
        stats["total_found"] = len(items)

        for item in items:
            h = compute_hash(item["article_id"], item["title"])

            if h in seen_hashes:
                log.debug(f"중복 스킵: {item['title'][:40]}")
                stats["skip_count"] += 1
                continue

            try:
                detail = await fetch_detail(page, item["raw_id"], source["base_url"])

                attachments = await process_attachments(
                    detail.get("attachments", []),
                    item["article_id"],
                )

                # parse_logs 기록 대상 수집
                for att in attachments:
                    if att["download_ok"] and att["parser"] != "none":
                        parse_records.append(
                            {
                                "file_key": att["file_key"],
                                "parser": att["parser"],
                                "success": att["parse_ok"],
                                "chars": len(att.get("extracted_text", "")),
                                "error_msg": None,
                            }
                        )

                key_info = extract_key_info(
                    body_text=detail.get("body_text", ""),
                    attachments=attachments,
                )

                notice = {
                    **item,
                    "body_text": detail.get("body_text", ""),
                    "category": detail.get("category", ""),
                    "source_id": source["id"],
                    "attachments": attachments,
                    "source_url": DETAIL_URL_TEMPLATE.format(
                        base_url=source["base_url"],
                        raw_id=item["raw_id"],
                    ),
                    "hash": h,
                    "crawled_at": datetime.now(timezone.utc).isoformat(),
                    "deadline": key_info["deadline"],
                    "target": key_info["target"],
                    "apply_method": key_info["apply_method"],
                }

                save_raw_json(notice)
                seen_hashes.add(h)
                stats["new_count"] += 1
                log.info(
                    f"[{source['name']}] 수집 완료: [{item['article_id']}] {item['title'][:45]}"
                )

            except Exception as exc:
                log.error(f"[{source['name']}] 게시글 처리 실패 ({item['article_id']}): {exc}")
                stats["fail_count"] += 1

    finally:
        await page.close()

    stats["parse_records"] = parse_records
    return stats


async def run_all() -> None:
    """
    전체 소스 순차 크롤링.
    소스별 try-except로 에러 격리 — 한 학과 실패가 다른 학과에 영향 없음.
    """
    if not SOURCES:
        log.error("활성 소스가 없습니다. CRAWL_SOURCES 환경변수를 확인하세요.")
        return

    log.info(
        f"크롤링 시작 — 활성 소스 {len(SOURCES)}개: "
        + ", ".join(f"{s['name']}({s['code']})" for s in SOURCES)
    )

    seen_hashes = load_seen_hashes()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            executable_path=_find_headless_shell(),
            args=_launch_args(),
        )

        try:
            for source in SOURCES:
                log.info(f"━━ [{source['name']}] 크롤링 시작 ━━")
                job_id = create_crawl_job(source["id"])

                try:
                    stats = await run_source(browser, source, seen_hashes)
                    finish_crawl_job(
                        job_id,
                        status="success",
                        total_found=stats["total_found"],
                        new_count=stats["new_count"],
                        skip_count=stats["skip_count"],
                        fail_count=stats["fail_count"],
                    )
                    # parse_logs 일괄 기록
                    for rec in stats.get("parse_records", []):
                        log_parse(
                            crawl_job_id=job_id,
                            file_key=rec["file_key"],
                            parser=rec["parser"],
                            success=rec["success"],
                            chars_extracted=rec["chars"],
                            error_msg=rec["error_msg"],
                        )
                    log.info(
                        f"[{source['name']}] 완료 — "
                        f"수집 {stats['total_found']}건 / "
                        f"신규 {stats['new_count']}건 / "
                        f"스킵 {stats['skip_count']}건 / "
                        f"실패 {stats['fail_count']}건"
                    )

                except Exception as exc:
                    log.error(f"[{source['name']}] 크롤링 전체 실패: {exc}")
                    finish_crawl_job(job_id, status="failed", error_msg=str(exc))

        finally:
            await browser.close()

    save_seen_hashes(seen_hashes)
    log.info("전체 소스 크롤링 완료")


def run_scheduler() -> None:
    """APScheduler로 주기적 크롤링 실행."""

    async def _loop():
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            run_all,
            trigger="interval",
            minutes=CRAWL_INTERVAL_MINUTES,
            next_run_time=datetime.now(timezone.utc),
        )
        scheduler.start()
        log.info(
            f"스케줄러 시작 — {CRAWL_INTERVAL_MINUTES}분 간격, 활성 소스 {len(SOURCES)}개: "
            + ", ".join(f"{s['name']}({s['code']})" for s in SOURCES)
        )
        try:
            while True:
                await asyncio.sleep(60)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            log.info("스케줄러 종료")
            scheduler.shutdown()

    asyncio.run(_loop())


def main() -> None:
    loop_mode = "--loop" in sys.argv
    if loop_mode:
        run_scheduler()
    else:
        asyncio.run(run_all())
