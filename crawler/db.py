"""
CamPost Crawler — DB 쓰기 레이어 (Pipeline 전용)

쓰기 대상: crawl_jobs, parse_logs 만.
공지 데이터(raw_notices, notices 등)는 Spring Boot Importer 전담.

psycopg2 직접 사용 — SQLAlchemy는 Pipeline 범위에 과도함.
"""

import logging

import psycopg2

from .config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_SSLMODE, DB_USER

log = logging.getLogger("campost.db")


def _connect():
    kwargs = {
        "host": DB_HOST,
        "port": DB_PORT,
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "connect_timeout": 5,
    }
    if DB_SSLMODE:
        kwargs["sslmode"] = DB_SSLMODE
    return psycopg2.connect(**kwargs)


def create_crawl_job(source_id: int) -> int | None:
    """
    crawl_jobs에 running 상태 레코드 삽입.
    Returns job_id. DB 연결 실패 시 None 반환 (크롤링은 계속 진행).
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO crawl_jobs (source_id, status) "
                    "VALUES (%s, 'running') RETURNING id",
                    (source_id,),
                )
                job_id = cur.fetchone()[0]
        log.debug(f"crawl_job 생성: id={job_id}, source_id={source_id}")
        return job_id
    except Exception as exc:
        log.warning(f"crawl_job 생성 실패 (source_id={source_id}): {exc}")
        return None


def finish_crawl_job(
    job_id: int,
    status: str,
    total_found: int = 0,
    new_count: int = 0,
    skip_count: int = 0,
    fail_count: int = 0,
    error_msg: str | None = None,
) -> None:
    """crawl_jobs 레코드를 완료 상태로 업데이트."""
    if job_id is None:
        return
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE crawl_jobs
                       SET status=%s, finished_at=now(),
                           total_found=%s, new_count=%s,
                           skip_count=%s, fail_count=%s, error_msg=%s
                     WHERE id=%s
                    """,
                    (status, total_found, new_count, skip_count, fail_count, error_msg, job_id),
                )
        log.debug(f"crawl_job 완료: id={job_id}, status={status}")
    except Exception as exc:
        log.warning(f"crawl_job 업데이트 실패 (job_id={job_id}): {exc}")


def log_parse(
    crawl_job_id: int | None,
    file_key: str,
    parser: str,
    success: bool,
    chars_extracted: int = 0,
    error_msg: str | None = None,
) -> None:
    """parse_logs에 파싱 시도 이력 기록."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO parse_logs
                        (crawl_job_id, file_key, parser, success, chars_extracted, error_msg)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (crawl_job_id, file_key, parser, success, chars_extracted, error_msg),
                )
    except Exception as exc:
        log.warning(f"parse_log 기록 실패 (file_key={file_key}): {exc}")
