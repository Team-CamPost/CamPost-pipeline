"""
CamPost Crawler — 스토리지 레이어

확정 다이어그램 Pipeline_Layer 노드: SaveRaw
Python Crawler의 저장 책임:
  - seen_hashes.json        : 중복 수집 방지 해시 영속화
  - data/raw/{article_id}.json : 공지 1건 = 파일 1개 (RawStore)

DB 쓰기는 Spring Boot Importer가 전담한다.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import HASHES_FILE, RAW_STORE_DIR

log = logging.getLogger("campost.storage")


# ── 해시 유틸 ────────────────────────────────────────────


def compute_hash(article_id: str, title: str) -> str:
    """SHA-256(article_id:title) — 중복 수집 방지 키"""
    return hashlib.sha256(f"{article_id}:{title}".encode("utf-8")).hexdigest()


# ── 해시 영속성 ──────────────────────────────────────────


def load_seen_hashes() -> set[str]:
    if HASHES_FILE.exists():
        return set(json.loads(HASHES_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen_hashes(hashes: set[str]) -> None:
    HASHES_FILE.write_text(
        json.dumps(sorted(hashes), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.debug(f"해시 저장 완료: {len(hashes)}건 → {HASHES_FILE}")


# ── Raw JSON 저장소 (RawStore / SaveRaw 노드) ─────────────
#
# 파일명: {article_id}.json
# Spring Boot Importer가 이 디렉터리를 폴링하여 DB에 적재한다.


def save_raw_json(notice: dict) -> Path:
    """
    공지 1건을 data/raw/{article_id}.json 으로 저장.
    이미 존재하면 덮어쓴다 (재크롤링 시 최신 내용 반영).

    Returns:
        저장된 파일 경로
    """
    article_id = notice["article_id"]
    payload = {
        **notice,
        "raw_saved_at": datetime.now(timezone.utc).isoformat(),
    }
    dest = RAW_STORE_DIR / f"{article_id}.json"
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.debug(f"RawStore 저장: {dest.name}")
    return dest


def list_raw_json() -> list[Path]:
    """RawStore의 모든 JSON 파일 목록 반환 (테스트/검증용)"""
    return sorted(RAW_STORE_DIR.glob("*.json"))
