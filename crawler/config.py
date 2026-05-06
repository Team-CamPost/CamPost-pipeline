"""
CamPost Crawler — 설정 관리

V3 아키텍처: Python Crawler는 Raw JSON 파일 저장소만 사용.
DB 접근은 crawl_jobs/parse_logs 쓰기 전용 (psycopg2).
crawl_sources DB 읽기는 Sprint 3에서 추가 예정.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 크롤링 대상 소스 목록 ──────────────────────────────────
# Sprint 3에서 crawl_sources 테이블 읽기로 전환 예정.
# id 값은 V4__seed_initial_data.sql INSERT 순서와 일치해야 한다.
_ALL_SOURCES: list[dict] = [
    {
        "id": 1,
        "name": "소프트웨어학과",
        "code": "SW",
        "base_url": "https://cms.dankook.ac.kr/web/sw/-1",
        "crawler_type": "card",
    },
    {
        "id": 2,
        "name": "컴퓨터공학과",
        "code": "ACE",
        "base_url": "https://cms.dankook.ac.kr/web/ace/notice",
        "crawler_type": "card",
    },
    {
        "id": 3,
        "name": "모바일시스템공학과",
        "code": "MOBILE",
        "base_url": "https://cms.dankook.ac.kr/web/mobilesystems/-8",
        "crawler_type": "card",
    },
    {
        "id": 4,
        "name": "통계사이언스학과",
        "code": "STAT",
        "base_url": "https://cms.dankook.ac.kr/web/dkustat/-6",
        "crawler_type": "card",
    },
    {
        "id": 5,
        "name": "사이버보안학과",
        "code": "INDSEC",
        "base_url": "https://cms.dankook.ac.kr/web/indsec/-4",
        "crawler_type": "card",
    },
    {
        "id": 6,
        "name": "SW중심대학사업단",
        "code": "SWCU",
        "base_url": "https://swcu.dankook.ac.kr/en/-5",
        "crawler_type": "card",
    },
]

# CRAWL_SOURCES 환경변수로 활성 소스를 쉼표 구분 코드로 지정한다.
# 미설정 시 전체 소스 크롤링.
# 예) CRAWL_SOURCES=SW,ACE  → 소프트웨어학과 + 컴퓨터공학과만 크롤링
_crawl_sources_env = os.getenv("CRAWL_SOURCES", "").strip()
if _crawl_sources_env:
    _enabled_codes = {c.strip().upper() for c in _crawl_sources_env.split(",") if c.strip()}
    SOURCES: list[dict] = [s for s in _ALL_SOURCES if s["code"] in _enabled_codes]
else:
    SOURCES = _ALL_SOURCES

# ── 상세 URL 패턴 (모든 학과 동일) ───────────────────────────
# {base_url}에 각 소스의 base_url을 대입한다.
# {raw_id}에 prefix 없는 숫자 ID를 대입한다.
DETAIL_URL_TEMPLATE = (
    "{base_url}"
    "?p_p_id=dku_bbs_web_BbsPortlet"
    "&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
    "&_dku_bbs_web_BbsPortlet_action=view_message"
    "&_dku_bbs_web_BbsPortlet_bbsMessageId={raw_id}"
)

# ── DOM 셀렉터 ────────────────────────────────────────────
# card 타입: SW학과 (PoC 검증 완료)
SELECTORS_CARD = {
    "list_item": ".dku-list-body-item:not(.header)",
    "title_anchor": ".item-title h4 a",
    "detail_table": 'table[summary*="게시판"]',
    "body": "td.r_cont",
    "attachment": 'a[href*="download=true"]',
}

# table 타입: ACE, MOBILE, STAT, INDSEC, SWCU
# (다중 크롤러 분석 2026-04-02 기준)
SELECTORS_TABLE = {
    "list_item": 'tr:has(a[href="#none"])',
    "title_anchor": 'a[href="#none"]',
    "detail_table": 'table[summary*="게시판"]',
    "body": "td.r_cont",
    "attachment": 'a[href*="download=true"]',
}

# ── 스케줄러 ─────────────────────────────────────────────
CRAWL_INTERVAL_MINUTES: int = int(os.getenv("CRAWL_INTERVAL_MINUTES", "60"))

# ── Playwright ───────────────────────────────────────────
HEADLESS: bool = os.getenv("HEADLESS", "true").lower() != "false"
PAGE_TIMEOUT: int = 30_000  # ms
SELECTOR_TIMEOUT: int = 15_000  # ms
REQUEST_DELAY: float = 1.0  # 게시글 간 대기 (초)

USER_AGENT = "Mozilla/5.0 (compatible; CamPost-Crawler/1.0; +https://campost.dku.ac.kr/bot)"

# ── 저장 경로 ────────────────────────────────────────────
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./data"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HASHES_FILE = OUTPUT_DIR / "seen_hashes.json"

FILES_DIR = OUTPUT_DIR / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)

RAW_STORE_DIR = OUTPUT_DIR / "raw"
RAW_STORE_DIR.mkdir(parents=True, exist_ok=True)

EXTRACTABLE_EXTS = {"pdf", "hwp", "hwpx", "docx"}

# ── DB 연결 (crawl_jobs / parse_logs 쓰기 전용) ──────────
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "campost")
DB_USER = os.getenv("POSTGRES_USER", "campost")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

# ── Gemini AI 설정 ────────────────────────────────────────
# GEMINI_API_KEY 미설정 시 AI 추출 비활성화 → regex fallback 사용
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AI_ENABLED: bool = bool(GEMINI_API_KEY)
