"""
CamPost Crawler — 핵심 정보 추출기 (Extractor)

확정 다이어그램 Pipeline_Layer 노드:
    FileHandler → Extractor → SaveRaw

공지 본문 + 첨부파일 텍스트에서 구조화 필드를 규칙 기반으로 추출한다.
AI 요약(Sprint 2) 이전에 수행하는 전처리 단계이며,
추출 결과는 RawStore JSON에 함께 저장된다.

추출 필드:
    deadline     : 마감일 (YYYY-MM-DD 또는 None)
    target       : 지원 대상 (ex: "3~4학년", "전학년 재학생")
    apply_method : 신청 방법 (ex: "이메일 제출", "온라인 신청")
"""

import logging
import re
from datetime import datetime

log = logging.getLogger("campost.extractor")


# ── 마감일 추출 ──────────────────────────────────────────

# (패턴, 그룹 인덱스 순서) — year, month, day 순서로 캡처
_DEADLINE_PATTERNS = [
    # "2026.03.28까지", "2026-03-28 마감", "2026년 3월 28일 접수마감"
    r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})[일]?\s*"
    r"(?:까지|마감|접수마감|신청마감|제출마감|모집마감)",
    # "마감일: 2026.03.28", "신청마감일 - 2026.3.28"
    r"(?:마감일|신청마감일|제출마감일|접수마감|모집마감)\s*[:\-]?\s*"
    r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})",
    # 기간 범위 끝: "~ 2026.03.28", "- 2026.03.28"
    r"[~\-]\s*(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})[일]?\s*(?:\(|$|\s)",
]


def _parse_date(y: str, m: str, d: str) -> str | None:
    try:
        return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def extract_deadline(text: str) -> str | None:
    """본문에서 마감일을 YYYY-MM-DD 형식으로 추출. 없으면 None."""
    for pattern in _DEADLINE_PATTERNS:
        for match in re.finditer(pattern, text):
            groups = match.groups()
            if len(groups) == 3:
                date = _parse_date(groups[0], groups[1], groups[2])
                if date:
                    return date
    return None


# ── 지원 대상 추출 ────────────────────────────────────────

_TARGET_PATTERNS = [
    # "신청 대상: 3~4학년", "지원 대상 - 재학생"
    r"(?:신청|지원|참가|모집)?\s*대상\s*[:\-]?\s*([^\n,。;]{2,40})",
    # "지원 자격: ..."
    r"지원\s*자격\s*[:\-]?\s*([^\n,。;]{2,40})",
    # "1~4학년 재학생", "전학년", "대학원생"
    r"((?:[1-4]학년|전\s*학년|재학생|대학원생|졸업(?:예정)?자)(?:\s*[^\n,。;]{0,20})?)",
]


def extract_target(text: str) -> str | None:
    """공지 지원 대상 추출. 없으면 None."""
    for pattern in _TARGET_PATTERNS:
        m = re.search(pattern, text)
        if m:
            result = m.group(1).strip()
            if 2 <= len(result) <= 40:
                return result
    return None


# ── 신청 방법 추출 ────────────────────────────────────────

_APPLY_PATTERNS = [
    # "신청 방법: 이메일 제출", "참가 방법 - 구글폼"
    r"(?:신청|지원|참가|접수)\s*방법\s*[:\-]?\s*([^\n。;]{3,60})",
    # "신청처: ...", "접수처: ..."
    r"(?:신청처|접수처)\s*[:\-]?\s*([^\n。;]{3,60})",
    # 수단 키워드 기반: "이메일로 신청", "구글폼 접수"
    r"((?:이메일|온라인|구글\s*폼|홈페이지|방문|우편)\s*[^\n]{0,30}(?:접수|신청|제출|등록))",
]


def extract_apply_method(text: str) -> str | None:
    """신청 방법 추출. 없으면 None."""
    for pattern in _APPLY_PATTERNS:
        m = re.search(pattern, text)
        if m:
            result = m.group(1).strip()
            if 3 <= len(result) <= 60:
                return result
    return None


# ── 통합 추출 (다이어그램 Extractor 노드) ───────────────────


def extract_key_info(body_text: str, attachments: list[dict]) -> dict:
    """
    공지 본문 + 첨부파일 텍스트를 종합해 구조화 필드 추출.

    탐색 우선순위: 본문 → 첨부파일 텍스트 (길이 많은 순)

    Args:
        body_text   : 공지 본문 텍스트
        attachments : process_attachments() 반환값 리스트

    Returns:
        {
            "deadline"    : "YYYY-MM-DD" | None,
            "target"      : str | None,
            "apply_method": str | None,
        }
    """
    texts = [body_text] if body_text else []
    att_texts = sorted(
        [a.get("extracted_text", "") for a in attachments if a.get("extracted_text")],
        key=len,
        reverse=True,
    )
    texts.extend(att_texts)
    combined = "\n".join(texts)

    result = {
        "deadline": extract_deadline(combined),
        "target": extract_target(combined),
        "apply_method": extract_apply_method(combined),
    }

    if any(result.values()):
        log.info(
            f"  핵심정보 추출 — "
            f"마감:{result['deadline']} | "
            f"대상:{result['target']} | "
            f"신청:{result['apply_method']}"
        )
    else:
        log.debug("  핵심정보 추출 결과 없음")

    return result
