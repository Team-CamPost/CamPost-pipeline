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
import os
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

from .config import AI_ENABLED, CRAWL_INTERVAL_MINUTES, DETAIL_URL_TEMPLATE, GEMINI_API_KEY, GEMINI_MODEL, HEADLESS, PAGE_TIMEOUT, RAW_STORE_DIR, SOURCES
from .content import build_content_payload
from .db import create_crawl_job, finish_crawl_job, log_parse
from .extractor import extract_key_info_with_ai
from .file_handler import extract_external_images, extract_inline_images, process_attachments
from .scraper import fetch_detail, fetch_list
from .storage import compute_hash, load_seen_hashes, save_raw_json, save_seen_hashes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("campost.runner")


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


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

                inline_images = extract_inline_images(
                    detail.get("body_html", ""),
                    item["article_id"],
                )
                attachments.extend(inline_images)

                external_images = await extract_external_images(
                    detail.get("body_html", ""),
                    item["article_id"],
                )
                attachments.extend(external_images)

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

                key_info = extract_key_info_with_ai(
                    body_text=detail.get("body_text", ""),
                    attachments=attachments,
                    api_key=GEMINI_API_KEY,
                    model_name=GEMINI_MODEL,
                    title=item.get("title", ""),
                    notice_date=item.get("date", ""),
                )
                content_payload = build_content_payload(
                    detail.get("body_html", ""),
                    attachments,
                )

                notice = {
                    **item,
                    "body_text": detail.get("body_text", ""),
                    "body_html": detail.get("body_html", ""),
                    **content_payload,
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
                    "deadline_time": key_info["deadline_time"],
                    "deadline_at": key_info["deadline_at"],
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

    ai_status = f"AI 마감일 추출: {'ON (' + GEMINI_MODEL + ')' if AI_ENABLED else 'OFF (regex only)'}"
    log.info(
        f"크롤링 시작 — 활성 소스 {len(SOURCES)}개: "
        + ", ".join(f"{s['name']}({s['code']})" for s in SOURCES)
        + f" | {ai_status}"
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


def run_startup_reextract() -> None:
    """
    컨테이너 시작 시 기존 raw JSON 중 null 필드가 있는 파일을 현재 extractor로 재처리.
    변경된 파일은 덮어써서 Importer가 자동으로 DB를 갱신하게 한다.
    기존 값은 보존하고 null 필드만 채운다.

    AI 호출 간 4초 딜레이로 Gemini free tier RPM(15회/분) 초과를 방지한다.
    """
    import json
    import time

    files = list(RAW_STORE_DIR.glob("*.json"))
    if not files:
        return

    # null 필드가 있는 파일만 추려서 처리 대상 파악
    backfill_content = _env_flag("CAMPOST_BACKFILL_CONTENT_ON_STARTUP")
    todo = []
    content_updated = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(f"[startup] {path.name} 읽기 실패: {exc}")
            continue
        raw_attachments = data.get("attachments") or []
        attachments = raw_attachments if isinstance(raw_attachments, list) else []
        if backfill_content:
            try:
                content_payload = build_content_payload(data.get("body_html") or "", attachments)
            except Exception as exc:
                log.warning(f"[startup] {path.name} content_html generation failed: {exc}")
            else:
                if any(data.get(k) != v for k, v in content_payload.items()):
                    data.update(content_payload)
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    content_updated += 1
        if not (
            data.get("deadline") is not None
            and data.get("deadline_time") is not None
            and data.get("deadline_at") is not None
            and data.get("target") is not None
            and data.get("apply_method") is not None
        ):
            body_text = data.get("body_text") or ""
            if body_text or any(a.get("extracted_text") for a in attachments if isinstance(a, dict)):
                todo.append(path)

    if content_updated:
        log.info(f"[startup] content_html updated: {content_updated}")

    if not todo:
        log.info(f"[startup] 재추출 불필요 — {len(files)}개 파일 이미 최신 상태")
        return

    log.info(f"[startup] null 필드 있는 파일 {len(todo)}개 재추출 시작 (AI 간 4초 딜레이)")
    updated = 0
    ai_call_count = 0

    for path in todo:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(f"[startup] {path.name} 읽기 실패: {exc}")
            continue

        old_deadline     = data.get("deadline")
        old_deadline_time = data.get("deadline_time")
        old_deadline_at = data.get("deadline_at")
        old_target       = data.get("target")
        old_apply_method = data.get("apply_method")

        body_text   = data.get("body_text") or ""
        raw_attachments = data.get("attachments") or []
        attachments = raw_attachments if isinstance(raw_attachments, list) else []

        # AI 호출 전 딜레이 (첫 번째 호출 제외)
        if GEMINI_API_KEY and ai_call_count > 0:
            time.sleep(4)

        try:
            result = extract_key_info_with_ai(
                body_text,
                attachments,
                GEMINI_API_KEY,
                GEMINI_MODEL,
                title=data.get("title") or "",
                notice_date=data.get("date") or "",
            )
            if GEMINI_API_KEY:
                ai_call_count += 1
        except Exception as exc:
            log.warning(f"[startup] {path.stem} 추출 실패: {exc}")
            continue

        new_deadline     = old_deadline     if old_deadline     is not None else result["deadline"]
        new_deadline_time = old_deadline_time if old_deadline_time is not None else result["deadline_time"]
        new_deadline_at = old_deadline_at if old_deadline_at is not None else result["deadline_at"]
        new_target       = old_target       if old_target       is not None else result["target"]
        new_apply_method = old_apply_method if old_apply_method is not None else result["apply_method"]

        if (
            new_deadline,
            new_deadline_time,
            new_deadline_at,
            new_target,
            new_apply_method,
        ) == (
            old_deadline,
            old_deadline_time,
            old_deadline_at,
            old_target,
            old_apply_method,
        ):
            continue

        data["deadline"]     = new_deadline
        data["deadline_time"] = new_deadline_time
        data["deadline_at"] = new_deadline_at
        data["target"]       = new_target
        data["apply_method"] = new_apply_method
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        updated += 1
        log.info(
            f"[startup] {path.stem} — "
            f"마감: {old_deadline!r} → {new_deadline!r} | "
            f"대상: {old_target!r} → {new_target!r}"
        )

    if updated:
        log.info(f"[startup] {updated}개 업데이트 완료 → Importer가 30초 내 DB 자동 반영")
    else:
        log.info(f"[startup] {len(todo)}개 처리했으나 변경 없음 (모두 null이거나 동일 값)")


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
    run_startup_reextract()
    loop_mode = "--loop" in sys.argv
    if loop_mode:
        run_scheduler()
    else:
        asyncio.run(run_all())
