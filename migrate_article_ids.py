"""
CamPost — article_id prefix 마이그레이션 스크립트
Sprint 2 시작 전 1회 실행.

Sprint 1에서 수집된 SW학과 공지의 article_id를 숫자("170663") 형식에서
prefix 포함("SW_170663") 형식으로 일괄 변환한다.

변경 대상:
  DB 테이블: raw_notices, notices, notice_attachments (file_key 포함), bookmarks
  파일:      data/raw/*.json (파일명 + 내부 article_id 필드)
             data/seen_hashes.json (해시 재생성)

실행 방법:
  docker exec campost-pipeline python migrate_article_ids.py
  또는 로컬:
  cd campost-pipeline && python migrate_article_ids.py
"""

import json
import logging
import os
import re
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("migrate")

# ── 설정 ──────────────────────────────────────────────────
DEPT_CODE = "SW"   # Sprint 1 수집 학과 코드

OUTPUT_DIR   = Path(os.getenv("OUTPUT_DIR", "./data"))
RAW_STORE    = OUTPUT_DIR / "raw"
HASHES_FILE  = OUTPUT_DIR / "seen_hashes.json"

DB_CONN = dict(
    host=os.getenv("DB_HOST", "db"),
    port=int(os.getenv("DB_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "campost"),
    user=os.getenv("POSTGRES_USER", "campost"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
)

import hashlib

def _prefixed(raw_id: str) -> str:
    return f"{DEPT_CODE}_{raw_id}"

def _is_raw_id(article_id: str) -> bool:
    """prefix 없는 숫자 ID인지 확인"""
    return re.match(r"^\d+$", article_id) is not None

def _compute_hash(article_id: str, title: str) -> str:
    return hashlib.sha256(f"{article_id}:{title}".encode()).hexdigest()


# ── DB 마이그레이션 ────────────────────────────────────────

def migrate_db():
    log.info("DB 마이그레이션 시작")
    with psycopg2.connect(**DB_CONN) as conn:
        with conn.cursor() as cur:

            # raw_notices
            cur.execute("SELECT id, article_id FROM raw_notices WHERE article_id ~ '^[0-9]+$'")
            rows = cur.fetchall()
            log.info(f"raw_notices 대상: {len(rows)}건")
            for row_id, old_id in rows:
                new_id = _prefixed(old_id)
                cur.execute("UPDATE raw_notices SET article_id=%s WHERE id=%s", (new_id, row_id))

            # notices
            cur.execute("SELECT id, article_id FROM notices WHERE article_id ~ '^[0-9]+$'")
            rows = cur.fetchall()
            log.info(f"notices 대상: {len(rows)}건")
            for row_id, old_id in rows:
                new_id = _prefixed(old_id)
                cur.execute("UPDATE notices SET article_id=%s WHERE id=%s", (new_id, row_id))

            # notice_attachments — file_key는 "{article_id}_{filename}" 형태
            cur.execute("SELECT id, file_key FROM notice_attachments")
            rows = cur.fetchall()
            for row_id, file_key in rows:
                # file_key가 "170663_파일명.hwp" 형식이면 변환
                m = re.match(r'^(\d+)_(.+)$', file_key)
                if m:
                    new_key = f"{DEPT_CODE}_{m.group(1)}_{m.group(2)}"
                    cur.execute("UPDATE notice_attachments SET file_key=%s WHERE id=%s", (new_key, row_id))

            # bookmarks
            cur.execute("SELECT id, article_id FROM bookmarks WHERE article_id ~ '^[0-9]+$'")
            rows = cur.fetchall()
            for row_id, old_id in rows:
                cur.execute("UPDATE bookmarks SET article_id=%s WHERE id=%s", (_prefixed(old_id), row_id))

        conn.commit()
    log.info("DB 마이그레이션 완료")


# ── 파일 마이그레이션 ─────────────────────────────────────

def migrate_json_files():
    log.info("JSON 파일 마이그레이션 시작")
    json_files = sorted(RAW_STORE.glob("*.json"))
    log.info(f"대상 파일: {len(json_files)}건")

    new_hashes = set()

    for path in json_files:
        if not re.match(r"^\d+\.json$", path.name):
            log.debug(f"스킵 (이미 변환됨 또는 관련 없음): {path.name}")
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        old_id = data.get("article_id", "")

        if not _is_raw_id(old_id):
            continue

        new_id = _prefixed(old_id)
        data["article_id"] = new_id

        # file_key 필드 내부도 변환
        for att in data.get("attachments", []):
            fk = att.get("file_key", "")
            m = re.match(r'^(\d+)_(.+)$', fk)
            if m:
                att["file_key"] = f"{DEPT_CODE}_{m.group(1)}_{m.group(2)}"
            lp = att.get("local_path", "")
            if lp.startswith(f"files/{old_id}_"):
                att["local_path"] = lp.replace(f"files/{old_id}_", f"files/{new_id}_", 1)

        # 새 해시 계산
        new_hash = _compute_hash(new_id, data.get("title", ""))
        data["hash"] = new_hash
        new_hashes.add(new_hash)

        # 파일명 변경 후 저장
        new_path = RAW_STORE / f"{new_id}.json"
        new_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        path.unlink()
        log.info(f"  {path.name} → {new_path.name}")

    # seen_hashes.json 재생성
    HASHES_FILE.write_text(
        json.dumps(sorted(new_hashes), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"seen_hashes.json 재생성: {len(new_hashes)}건")
    log.info("JSON 파일 마이그레이션 완료")


if __name__ == "__main__":
    log.info("=== article_id prefix 마이그레이션 시작 (SW_ 적용) ===")
    migrate_db()
    migrate_json_files()
    log.info("=== 마이그레이션 완료 ===")
