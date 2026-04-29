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
from datetime import date, datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("campost.extractor")

_AI_DISABLED_FOR_RUN = False

# ── 마감일 추출 (regex) ───────────────────────────────────

_DEADLINE_PATTERNS = [
    # [1] 날짜 뒤에 마감 키워드 — (요일) 및 시간(HH:MM) 허용
    # "2026.04.10(금) 15:00까지", "2026.03.28까지", "2026년 3월 28일 마감"
    (
        r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})[일]?"
        r"(?:\([월화수목금토일]\))?"   # optional (요일)
        r"(?:\.|\))?"                  # optional trailing dot/paren
        r"(?:\s*\d{1,2}:\d{2})?"       # optional 시간 HH:MM
        r"\s*(?:까지|마감|접수마감|신청마감|제출마감|모집마감)"
    ),
    # [2] 기간 키워드 + 범위 → 끝 날짜 캡처
    # "신청기간 : 2026.04.01(화) ~ 2026.04.30(수)", "모집기간: 2026-04-01~2026-04-30"
    (
        r"(?:마감일|신청마감일|제출마감일|접수마감|모집마감|신청기간|모집기간|접수기간)"
        r"\s*[:\-]?\s*"
        r"\d{4}[.\-/년]\s*\d{1,2}[.\-/월]\s*\d{1,2}[일.)]*(?:\([월화수목금토일]\))?"
        r"\s*[~\-]\s*"
        r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})"
    ),
    # [3] 기간/마감 키워드 + 단일 날짜
    # "신청기간 : 2026.04.10", "마감일: 2026.03.28", "신청마감일 - 2026.3.28"
    (
        r"(?:마감일|신청마감일|제출마감일|접수마감|모집마감|신청기간|모집기간|접수기간)"
        r"\s*[:\-]?\s*"
        r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})"
    ),
    # [4] 기간 범위 끝: "~ 2026.03.28", "~2026.10.30."
    r"[~\-]\s*(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})[일]?\s*(?:\.|,|\(|$|\s)",
    # [5] 대회/공모전 예선: "예선: 2026. 5. 16", "예선 2026.5.16"
    r"예선\s*[:\-]?\s*(\d{4})[.\-/ 년]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})",
]


def _parse_date(y: str, m: str, d: str) -> str | None:
    try:
        return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_reference_date(value: str | None) -> date | None:
    if not value:
        return None

    for pattern in (
        r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})",
        r"(\d{4})(\d{2})(\d{2})",
    ):
        match = re.search(pattern, value)
        if not match:
            continue
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
    return None


def _parse_short_date(month: str, day: str, reference_date: date | None) -> str | None:
    base = reference_date or datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        candidate = date(base.year, int(month), int(day))
    except ValueError:
        return None

    if reference_date and candidate < reference_date and reference_date.month >= 10 and candidate.month <= 2:
        try:
            candidate = date(base.year + 1, candidate.month, candidate.day)
        except ValueError:
            return None
    elif reference_date and candidate < reference_date:
        return None

    return candidate.isoformat()


def _extract_dates(text: str, reference_date: date | None) -> list[tuple[int, str]]:
    full_pattern = re.compile(
        r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?"
    )
    short_pattern = re.compile(r"(?<!\d)(\d{1,2})\s*(?:[./]|월)\s*(\d{1,2})\s*(?:일)?")

    dates: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in full_pattern.finditer(text):
        parsed = _parse_date(match.group(1), match.group(2), match.group(3))
        if parsed:
            dates.append((match.start(), parsed))
            occupied.append(match.span())

    def overlaps_full_date(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= start or end <= span[0]) for start, end in occupied)

    for match in short_pattern.finditer(text):
        if overlaps_full_date(match.span()):
            continue
        parsed = _parse_short_date(match.group(1), match.group(2), reference_date)
        if parsed:
            dates.append((match.start(), parsed))

    return sorted(dates, key=lambda item: item[0])


_DEADLINE_CONTEXT_RE = re.compile(
    r"(?:"
    r"신청\s*기간|신청\s*마감|신청\s*기한|"
    r"접수\s*기간|접수\s*마감|접수\s*기한|접수\s*종료|"
    r"모집\s*기간|모집\s*마감|모집\s*기한|"
    r"제출\s*기간|제출\s*마감|제출\s*기한|"
    r"응답\s*기한|설문\s*기간|수강\s*기간(?:\s*연장)?|"
    r"등록\s*기간|등록\s*마감|참가\s*신청|단체\s*접수|"
    r"마감일|기한"
    r")"
)


def _extract_deadline_from_context(text: str, reference_date: date | None) -> str | None:
    normalized = re.sub(r"\s+", " ", text)

    for match in _DEADLINE_CONTEXT_RE.finditer(normalized):
        end = min(len(normalized), match.end() + 180)
        snippet = normalized[match.start():end]
        dates = _extract_dates(snippet, reference_date)
        if dates:
            marker = re.search(r"(?:까지|마감|기한|종료)", snippet)
            if marker:
                bounded_dates = [item for item in dates if item[0] <= marker.end()]
                if bounded_dates:
                    return bounded_dates[-1][1]
            return dates[-1][1]

    for pattern in (
        r"[~\-]\s*((?:\d{4}\s*[.\-/년]\s*)?\d{1,2}\s*(?:[./월])\s*\d{1,2}\s*(?:일)?(?:\([월화수목금토일]\))?(?:\s*\d{1,2}:\d{2})?)\s*(?:까지|마감)",
        r"((?:\d{4}\s*[.\-/년]\s*)?\d{1,2}\s*(?:[./월])\s*\d{1,2}\s*(?:일)?(?:\([월화수목금토일]\))?(?:\s*\d{1,2}:\d{2})?)\s*(?:까지|마감)",
    ):
        match = re.search(pattern, normalized)
        if match:
            dates = _extract_dates(match.group(1), reference_date)
            if dates:
                return dates[-1][1]

    return None


def extract_deadline(text: str, notice_date: str | None = None) -> str | None:
    """본문에서 마감일을 YYYY-MM-DD 형식으로 추출. 없으면 None."""
    reference_date = _parse_reference_date(notice_date)

    contextual_deadline = _extract_deadline_from_context(text, reference_date)
    if contextual_deadline:
        return contextual_deadline

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

def extract_key_info(
    body_text: str,
    attachments: list[dict],
    *,
    title: str = "",
    notice_date: str | None = None,
) -> dict:
    """
    regex 기반 1차 추출.
    탐색 우선순위: 본문 → 첨부파일 텍스트 (길이 많은 순)
    """
    texts = [f"제목: {title}"] if title else []
    if body_text:
        texts.append(body_text)
    att_texts = sorted(
        [a.get("extracted_text", "") for a in attachments if a.get("extracted_text")],
        key=len,
        reverse=True,
    )
    texts.extend(att_texts)
    combined = "\n".join(texts)

    result = {
        "deadline": extract_deadline(combined, notice_date),
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
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    return f"""대학교 공지사항에서 핵심 정보를 추출하세요.
오늘 날짜: {today}
반드시 JSON만 출력하세요 (마크다운 코드블록, 설명 없이).

출력 형식:
{{
  "deadline": "마감일이 있으면 YYYY-MM-DD 형식, 없으면 null",
  "target": "지원/신청 대상을 40자 이내로, 없으면 null",
  "apply_method": "신청 방법을 60자 이내로, 없으면 null"
}}

=== deadline 추출 규칙 ===

[추출 우선순위 — 가장 높은 순위의 날짜 1개만 반환]
1순위: 신청·접수·지원·모집·제출·등록·참가 마감일
  키워드 예: "~까지", "마감", "기한", "접수 종료", "신청 마감", "모집 마감"
2순위: 행사·아카데미·설명회·특강·세미나에서 별도 신청 마감이 없으면
  → 행사 개최일 또는 첫 번째 행사 날짜를 deadline으로 사용
3순위: 서비스·프로그램 이용 기간 범위의 종료일
  예: "2026.05.01~2026.10.30" → 2026-10-30
4순위: 대회·공모전·해커톤·챌린지에서 신청 마감이 없으면 예선 또는 첫 행사 날짜

[반드시 제외 — deadline으로 쓰지 말 것]
- 발표일·합격자 발표·결과 발표·선정 결과 날짜
- 공지 등록일·작성일·게시일
- 정기 강의·수업 일정·시험 날짜 (단, 수강신청 마감은 제외 아님)
- "추후 공지", "미정", "별도 안내", "상시", "수시" → null
- 오늘({today})보다 1년 이상 과거 날짜 → null

[날짜 표현 변환]
- 연도 있음: 2026.5.11 / 2026-05-11 / 2026년 5월 11일 → 2026-05-11
- 연도 없음: 5/11 / 5월 11일 → {today} 이후 가장 가까운 해당 날짜
- 상대 표현:
    "이번 달 말" → 해당 월의 마지막 날
    "이번 주 금요일" / "이번 주 금" → 가장 가까운 금요일
    "금일" / "오늘" → {today}
    "내일" → {today}의 다음 날
    "N일 이내" / "N일 내" → {today} + N일
- 날짜 범위: "YYYY.MM.DD ~ YYYY.MM.DD" → 끝 날짜 사용

[신청 마감 vs 행사 일시가 함께 있을 때]
공지에 신청 마감일과 행사 개최일이 모두 있으면 반드시 신청 마감일을 사용.
예: "신청: ~5/15 / 행사 일시: 6/1" → 2026-05-15

=== target 추출 규칙 ===
- 신청·참가 가능한 구체적인 대상을 40자 이내로 추출
  예: "전학년 재학생", "3~4학년", "대학원생 포함 전학년"
- 행사 이름·주최 기관·공지 설명 문장은 target이 아님 — null로 처리
  예: "통계아카데미를 개최합니다" / "프로그램을 운영합니다" → null
- 명확하지 않으면 null

=== apply_method 추출 규칙 ===
- 신청 방법을 60자 이내로 추출
  예: "이메일(abc@dku.ac.kr) 제출", "구글폼 작성", "학과 사무실 방문"
- 이메일 주소가 있으면 반드시 포함
- 불명확하면 null

확인되지 않는 정보는 추측하지 말고 null로 처리.

공지 내용:
{truncated}"""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_ai_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    data = json.loads(cleaned)

    deadline = data.get("deadline") or None
    if deadline is not None and not _DATE_RE.match(deadline):
        log.debug(f"  [AI] deadline 포맷 불일치, null 처리: {deadline!r}")
        deadline = None
    if deadline is not None:
        from datetime import date as _date
        try:
            dl = _date.fromisoformat(deadline)
            today = datetime.now(ZoneInfo("Asia/Seoul")).date()
            if dl < today.replace(year=today.year - 1):
                log.warning(f"  [AI] deadline이 1년 이상 과거 날짜, null 처리: {deadline}")
                deadline = None
        except ValueError:
            deadline = None

    return {
        "deadline": deadline,
        "target": data.get("target") or None,
        "apply_method": data.get("apply_method") or None,
    }


def _ai_extract(text: str, api_key: str, model_name: str) -> dict | None:
    """
    Gemini API 호출 (google-genai SDK). 실패 시 None 반환.
    """
    global _AI_DISABLED_FOR_RUN
    if _AI_DISABLED_FOR_RUN:
        return None

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
        if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
            _AI_DISABLED_FOR_RUN = True
            log.warning("  [AI] quota exhausted; disabling AI calls for this process")
        log.warning(f"  [AI] 추출 실패 (regex fallback 사용): {exc}")
        return None


# ── 통합 추출 (AI + regex fallback) ─────────────────────

def extract_key_info_with_ai(
    body_text: str,
    attachments: list[dict],
    api_key: str = "",
    model_name: str = "gemini-1.5-flash",
    *,
    title: str = "",
    notice_date: str | None = None,
) -> dict:
    """
    1단계: regex로 빠른 추출
    2단계: GEMINI_API_KEY가 있으면 AI로 보정
           AI 결과가 있으면 우선 사용, null이면 regex 결과 유지

    Returns:
        {deadline, target, apply_method}
    """
    regex_result = extract_key_info(
        body_text,
        attachments,
        title=title,
        notice_date=notice_date,
    )

    if not api_key:
        return regex_result

    texts = [f"제목: {title}"] if title else []
    if body_text:
        texts.append(body_text)
    att_texts = [a.get("extracted_text", "") for a in attachments if a.get("extracted_text")]
    combined = "\n".join(texts + att_texts).strip()

    if not combined:
        return regex_result

    ai_result = _ai_extract(combined, api_key, model_name)

    if ai_result is None:
        return regex_result

    merged = {
        "deadline":     ai_result["deadline"]     if ai_result["deadline"]     is not None else regex_result["deadline"],
        "target":       ai_result["target"]       if ai_result["target"]       is not None else regex_result["target"],
        "apply_method": ai_result["apply_method"] if ai_result["apply_method"] is not None else regex_result["apply_method"],
    }
    return merged
