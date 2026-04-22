"""
CamPost Crawler — 핵심 정보 추출기 (Extractor)

확정 다이어그램 Pipeline_Layer 노드:
    FileHandler → Extractor → SaveRaw

1단계: regex 기반 빠른 추출 (extract_key_info)
2단계: Gemini AI 보정 (extract_key_info_with_ai) — GEMINI_API_KEY 설정 시 활성화

추출 필드:
    deadline     : 마감일 (YYYY-MM-DD 또는 None)
    target       : 지원 대상 (ex: "3~4학년", "전학년 재학생")
    apply_method : 신청 방법 (ex: "이메일 제출", "온라인 신청")
"""

import json
import logging
import re
from datetime import datetime

log = logging.getLogger("campost.extractor")

# ── 마감일 추출 (regex) ───────────────────────────────────

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


# ── 지원 대상 추출 (regex) ────────────────────────────────

_TARGET_PATTERNS = [
    r"(?:신청|지원|참가|모집)?\s*대상\s*[:\-]?\s*([^\n,。;]{2,40})",
    r"지원\s*자격\s*[:\-]?\s*([^\n,。;]{2,40})",
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


# ── 신청 방법 추출 (regex) ────────────────────────────────

_APPLY_PATTERNS = [
    r"(?:신청|지원|참가|접수)\s*방법\s*[:\-]?\s*([^\n。;]{3,60})",
    r"(?:신청처|접수처)\s*[:\-]?\s*([^\n。;]{3,60})",
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


# ── regex 통합 추출 ───────────────────────────────────────

def extract_key_info(body_text: str, attachments: list[dict]) -> dict:
    """
    regex 기반 1차 추출.
    탐색 우선순위: 본문 → 첨부파일 텍스트 (길이 많은 순)
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
            f"  [regex] 핵심정보 추출 — "
            f"마감:{result['deadline']} | "
            f"대상:{result['target']} | "
            f"신청:{result['apply_method']}"
        )
    else:
        log.debug("  [regex] 핵심정보 추출 결과 없음")

    return result


# ── Gemini AI 추출 ────────────────────────────────────────

_AI_MAX_CHARS = 3_000


def _build_prompt(text: str) -> str:
    truncated = text[:_AI_MAX_CHARS]
    return f"""대학교 공지사항에서 핵심 정보를 추출하세요.
반드시 JSON만 출력하세요 (마크다운 코드블록, 설명 없이).

출력 형식:
{{
  "deadline": "마감일이 있으면 YYYY-MM-DD 형식, 없으면 null",
  "target": "지원/신청 대상을 40자 이내로, 없으면 null",
  "apply_method": "신청 방법을 60자 이내로, 없으면 null"
}}

규칙:
- deadline: 날짜가 명시된 경우만 추출. "이번 달 말", "추후 공지" 등 불명확한 경우 null
- 확인되지 않는 정보는 추측하지 말고 null로 처리

공지 내용:
{truncated}"""


def _parse_ai_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    data = json.loads(cleaned)
    return {
        "deadline": data.get("deadline") or None,
        "target": data.get("target") or None,
        "apply_method": data.get("apply_method") or None,
    }


def _ai_extract(text: str, api_key: str, model_name: str) -> dict | None:
    """
    Gemini API 호출 (google-genai SDK). 실패 시 None 반환.
    """
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=_build_prompt(text),
        )
        result = _parse_ai_response(response.text)
        log.info(
            f"  [AI] 핵심정보 추출 — "
            f"마감:{result['deadline']} | "
            f"대상:{result['target']} | "
            f"신청:{result['apply_method']}"
        )
        return result
    except Exception as exc:
        log.warning(f"  [AI] 추출 실패 (regex fallback 사용): {exc}")
        return None


# ── 통합 추출 (AI + regex fallback) ─────────────────────

def extract_key_info_with_ai(
    body_text: str,
    attachments: list[dict],
    api_key: str = "",
    model_name: str = "gemini-1.5-flash",
) -> dict:
    """
    1단계: regex로 빠른 추출
    2단계: GEMINI_API_KEY가 있으면 AI로 보정
           AI 결과가 있으면 우선 사용, null이면 regex 결과 유지

    Returns:
        {deadline, target, apply_method}
    """
    regex_result = extract_key_info(body_text, attachments)

    if not api_key:
        return regex_result

    texts = [body_text] if body_text else []
    att_texts = [a.get("extracted_text", "") for a in attachments if a.get("extracted_text")]
    combined = "\n".join(texts + att_texts).strip()

    if not combined:
        return regex_result

    ai_result = _ai_extract(combined, api_key, model_name)

    if ai_result is None:
        return regex_result

    merged = {
        "deadline":     ai_result["deadline"]     or regex_result["deadline"],
        "target":       ai_result["target"]       or regex_result["target"],
        "apply_method": ai_result["apply_method"] or regex_result["apply_method"],
    }
    return merged
