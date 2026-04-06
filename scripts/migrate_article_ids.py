"""
CamPost - article_id prefix migration script
Run once before Sprint 2 rollout.

Converts Sprint 1 article IDs from numeric form ("170663")
to prefixed form ("SW_170663") for SW data.

Targets:
  DB tables: raw_notices, notices, notice_attachments(file_key), bookmarks
  Files:     data/raw/*.json (filename + article_id field)
             data/seen_hashes.json (recomputed)

Run:
  docker exec campost-pipeline python scripts/migrate_article_ids.py
  or local:
  cd CamPost-pipeline && python scripts/migrate_article_ids.py
"""

import hashlib
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

# -- Config -----------------------------------------------------------------
DEPT_CODE = "SW"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "data")))
RAW_STORE = OUTPUT_DIR / "raw"
HASHES_FILE = OUTPUT_DIR / "seen_hashes.json"

DB_CONN = dict(
    host=os.getenv("DB_HOST", "db"),
    port=int(os.getenv("DB_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "campost"),
    user=os.getenv("POSTGRES_USER", "campost"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
)


def _prefixed(raw_id: str) -> str:
    return f"{DEPT_CODE}_{raw_id}"


def _is_raw_id(article_id: str) -> bool:
    return re.match(r"^\d+$", article_id) is not None


def _compute_hash(article_id: str, title: str) -> str:
    return hashlib.sha256(f"{article_id}:{title}".encode()).hexdigest()


# -- DB migration -------------------------------------------------------------


def migrate_db() -> None:
    log.info("DB migration start")
    with psycopg2.connect(**DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, article_id FROM raw_notices WHERE article_id ~ '^[0-9]+$'")
            rows = cur.fetchall()
            log.info(f"raw_notices targets: {len(rows)}")
            for row_id, old_id in rows:
                cur.execute(
                    "UPDATE raw_notices SET article_id=%s WHERE id=%s",
                    (_prefixed(old_id), row_id),
                )

            cur.execute("SELECT id, article_id FROM notices WHERE article_id ~ '^[0-9]+$'")
            rows = cur.fetchall()
            log.info(f"notices targets: {len(rows)}")
            for row_id, old_id in rows:
                cur.execute(
                    "UPDATE notices SET article_id=%s WHERE id=%s",
                    (_prefixed(old_id), row_id),
                )

            cur.execute("SELECT id, file_key FROM notice_attachments")
            rows = cur.fetchall()
            for row_id, file_key in rows:
                match = re.match(r"^(\d+)_(.+)$", file_key)
                if match:
                    new_key = f"{DEPT_CODE}_{match.group(1)}_{match.group(2)}"
                    cur.execute(
                        "UPDATE notice_attachments SET file_key=%s WHERE id=%s",
                        (new_key, row_id),
                    )

            cur.execute("SELECT id, article_id FROM bookmarks WHERE article_id ~ '^[0-9]+$'")
            rows = cur.fetchall()
            for row_id, old_id in rows:
                cur.execute(
                    "UPDATE bookmarks SET article_id=%s WHERE id=%s",
                    (_prefixed(old_id), row_id),
                )

        conn.commit()
    log.info("DB migration complete")


# -- File migration -----------------------------------------------------------


def migrate_json_files() -> None:
    log.info("JSON migration start")
    json_files = sorted(RAW_STORE.glob("*.json"))
    log.info(f"JSON targets: {len(json_files)}")

    new_hashes = set()

    for path in json_files:
        if not re.match(r"^\d+\.json$", path.name):
            log.debug(f"skip (already migrated or unrelated): {path.name}")
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        old_id = data.get("article_id", "")

        if not _is_raw_id(old_id):
            continue

        new_id = _prefixed(old_id)
        data["article_id"] = new_id

        for att in data.get("attachments", []):
            file_key = att.get("file_key", "")
            match = re.match(r"^(\d+)_(.+)$", file_key)
            if match:
                att["file_key"] = f"{DEPT_CODE}_{match.group(1)}_{match.group(2)}"
            local_path = att.get("local_path", "")
            if local_path.startswith(f"files/{old_id}_"):
                att["local_path"] = local_path.replace(
                    f"files/{old_id}_",
                    f"files/{new_id}_",
                    1,
                )

        new_hash = _compute_hash(new_id, data.get("title", ""))
        data["hash"] = new_hash
        new_hashes.add(new_hash)

        new_path = RAW_STORE / f"{new_id}.json"
        new_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        path.unlink()
        log.info(f"  {path.name} -> {new_path.name}")

    HASHES_FILE.write_text(
        json.dumps(sorted(new_hashes), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"seen_hashes.json rebuilt: {len(new_hashes)}")
    log.info("JSON migration complete")


if __name__ == "__main__":
    log.info("=== article_id prefix migration start (SW_) ===")
    migrate_db()
    migrate_json_files()
    log.info("=== migration done ===")
