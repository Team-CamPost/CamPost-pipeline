"""
CamPost Crawler — 핵심 정보 추출기 (Extractor)

확정 다이어그램 Pipeline_Layer 노드:
    FileHandler → Extractor → SaveRaw

1단계: regex 기반 빠른 추출 (extract_key_info)
2단계: Gemini AI 보정 (extract_key_info_with_ai) — GEMINI_API_KEY 설정 시 활성화

추출 필드:
    deadline     : 마감일 (YYYY-MM-DD 또는 None)
    deadline_time: 마감시간 (HH:MM 또는 None)
    deadline_at  : 마감일시 (YYYY-MM-DDTHH:MM:SS+09:00 또는 None)
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


def _parse_time(
    ampm: str | None,
    hour: str,
    minute_colon: str | None = None,
    minute_korean: str | None = None,
) -> str | None:
    minute = minute_colon if minute_colon is not None else minute_korean
    minute = minute or "00"

    try:
        h = int(hour)
        m = int(minute)
    except ValueError:
        return None

    marker = (ampm or "").lower()
    if marker in {"오전", "am"}:
        if h == 12:
            h = 0
    elif marker in {"오후", "pm"}:
        if h < 12:
            h += 12

    if not 0 <= h <= 23 or not 0 <= m <= 59:
        return None
    return f"{h:02d}:{m:02d}"


def _build_deadline_at(deadline: str | None, deadline_time: str | None) -> str | None:
    if not deadline or not deadline_time:
        return None
    return f"{deadline}T{deadline_time}:00+09:00"


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


def _extract_date_spans(text: str, reference_date: date | None) -> list[tuple[int, int, str]]:
    full_pattern = re.compile(
        r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?"
    )
    short_pattern = re.compile(r"(?<!\d)(\d{1,2})\s*(?:[./]|월)\s*(\d{1,2})\s*(?:일)?")

    dates: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in full_pattern.finditer(text):
        parsed = _parse_date(match.group(1), match.group(2), match.group(3))
        if parsed:
            dates.append((match.start(), match.end(), parsed))
            occupied.append(match.span())

    def overlaps_full_date(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= start or end <= span[0]) for start, end in occupied)

    for match in short_pattern.finditer(text):
        if overlaps_full_date(match.span()):
            continue
        parsed = _parse_short_date(match.group(1), match.group(2), reference_date)
        if parsed:
            dates.append((match.start(), match.end(), parsed))

    return sorted(dates, key=lambda item: item[0])


def _extract_dates(text: str, reference_date: date | None) -> list[tuple[int, str]]:
    return [(start, parsed) for start, _end, parsed in _extract_date_spans(text, reference_date)]


_APPLICATION_RANGE_CONTEXT_RE = re.compile(
    r"(?:"
    r"신청\s*접수|신청\s*기간|신청\s*마감|신청\s*기한|"
    r"신청\s*및\s*승인|"
    r"접수\s*기간|접수\s*마감|접수\s*기한|접수\s*종료|"
    r"모집\s*기간|모집\s*마감|모집\s*기한|"
    r"지원\s*기간|지원\s*마감|지원\s*기한|"
    r"제출\s*기간|제출\s*마감|제출\s*기한|"
    r"등록\s*기간|등록\s*마감|"
    r"참가\s*신청|참가\s*접수|참가자\s*신청|참가자\s*접수|"
    r"참가신청서|온라인\s*참가신청서|"
    r"응답\s*기한|설문\s*기간|수강\s*기간(?:\s*연장)?|단체\s*접수"
    r")"
)


def _first_application_date_range_end(text: str, reference_date: date | None) -> str | None:
    dates = _extract_date_spans(text, reference_date)
    if len(dates) < 2:
        return None

    for first, second in zip(dates, dates[1:]):
        gap = text[first[1]:second[0]]
        before = text[max(0, first[0] - 80):first[0]]
        after = text[second[1]:min(len(text), second[1] + 80)]
        if (
            len(gap) <= 24
            and re.search(r"[~\-–]", gap)
            and (
                _APPLICATION_RANGE_CONTEXT_RE.search(before)
                or re.search(r"기간\s*내.*(?:참가신청서|신청서)\s*제출", after)
            )
        ):
            return second[2]

    return None


_TIME_RE = (
    r"(?:(오전|오후|AM|PM|am|pm)\s*)?"
    r"(\d{1,2})"
    r"(?::(\d{2})|시\s*(?:(\d{1,2})분?)?)"
)


def _extract_datetime_records(
    text: str,
    reference_date: date | None,
) -> list[tuple[int, int, str, str | None]]:
    full_pattern = re.compile(
        r"(?<!\d)"
        r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?"
        r"(?:\.)?"
        r"(?:\([월화수목금토일]\))?"
        r"(?:\.|\))?"
        rf"(?:\s*{_TIME_RE})?"
    )
    short_pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,2})\s*(?:[./]|월)\s*(\d{1,2})\s*(?:일)?"
        r"(?:\.)?"
        r"(?:\([월화수목금토일]\))?"
        r"(?:\.|\))?"
        rf"(?:\s*{_TIME_RE})?"
    )

    records: list[tuple[int, int, str, str | None]] = []
    occupied: list[tuple[int, int]] = []

    for match in full_pattern.finditer(text):
        parsed_date = _parse_date(match.group(1), match.group(2), match.group(3))
        if not parsed_date:
            continue
        parsed_time = None
        if match.group(5):
            parsed_time = _parse_time(match.group(4), match.group(5), match.group(6), match.group(7))
        records.append((match.start(), match.end(), parsed_date, parsed_time))
        occupied.append(match.span())

    def overlaps_full_date(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= start or end <= span[0]) for start, end in occupied)

    for match in short_pattern.finditer(text):
        if overlaps_full_date(match.span()):
            continue
        parsed_date = _parse_short_date(match.group(1), match.group(2), reference_date)
        if not parsed_date:
            continue
        parsed_time = None
        if match.group(4):
            parsed_time = _parse_time(match.group(3), match.group(4), match.group(5), match.group(6))
        records.append((match.start(), match.end(), parsed_date, parsed_time))

    return sorted(records, key=lambda item: item[0])


_DEADLINE_CONTEXT_RE = re.compile(
    r"(?:"
    r"신청\s*기간|신청\s*마감|신청\s*기한|신청\s*접수|"
    r"신청\s*및\s*승인|"
    r"지원\s*기간|지원\s*마감|지원\s*기한|지원\s*방법|"
    r"접수\s*기간|접수\s*마감|접수\s*기한|접수\s*종료|"
    r"모집\s*기간|모집\s*마감|모집\s*기한|"
    r"제출\s*기간|제출\s*마감|제출\s*기한|"
    r"응답\s*기한|설문\s*기간|수강\s*기간(?:\s*연장)?|"
    r"등록\s*기간|등록\s*마감|참가\s*신청|참가\s*접수|"
    r"참가자\s*신청|참가자\s*접수|참가신청서|단체\s*접수|"
    r"마감일|기한"
    r")"
)

_EVENT_DATETIME_CONTEXT_RE = re.compile(
    r"(?:"
    r"행사\s*일시|교육\s*일시|운영\s*일시|개최\s*일시|"
    r"설명회\s*일시|세미나\s*일시|박람회\s*일시|일\s*시"
    r")"
)

_EVENT_TIME_VALUE_RE = re.compile(
    r"(?:(오전|오후|AM|PM|am|pm)\s*)?"
    r"(\d{1,2})"
    r"(?::(\d{2})|시\s*(?:(\d{1,2})분?)?)"
)


def _extract_event_deadline_from_context(
    text: str,
    reference_date: date | None,
) -> tuple[str, str | None] | None:
    normalized = re.sub(r"\s+", " ", text)
    date_pattern = re.compile(
        r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?"
        r"(?:\.)?"
        r"(?:\([월화수목금토일]\))?"
        r"(?:\.|\))?"
    )

    for context_match in _EVENT_DATETIME_CONTEXT_RE.finditer(normalized):
        snippet = normalized[context_match.start():min(len(normalized), context_match.end() + 160)]
        date_match = date_pattern.search(snippet)
        if date_match:
            parsed_date = _parse_date(date_match.group(1), date_match.group(2), date_match.group(3))
            after_date = snippet[date_match.end():min(len(snippet), date_match.end() + 50)]
        else:
            dates = _extract_dates(snippet, reference_date)
            if not dates:
                continue
            parsed_date = dates[0][1]
            after_date = snippet[dates[0][0]:min(len(snippet), dates[0][0] + 80)]

        times = list(_EVENT_TIME_VALUE_RE.finditer(after_date))
        parsed_time = None
        if len(times) >= 2 and re.search(r"[~\-–]\s*$", after_date[times[0].end():times[1].start()]):
            first_marker = times[0].group(1)
            second_marker = times[1].group(1) or first_marker
            parsed_time = _parse_time(
                second_marker,
                times[1].group(2),
                times[1].group(3),
                times[1].group(4),
            )

        if parsed_date:
            return parsed_date, parsed_time

    return None


def _has_application_deadline_evidence(
    text: str,
    candidate_deadline: str | None,
    reference_date: date | None,
) -> bool:
    if not candidate_deadline:
        return False

    normalized = re.sub(r"\s+", " ", text)
    dates = _extract_date_spans(normalized, reference_date)

    for idx, current in enumerate(dates):
        if current[2] != candidate_deadline:
            continue

        window_start = max(0, current[0] - 100)
        window_end = min(len(normalized), current[1] + 100)
        before = normalized[window_start:current[0]]
        after = normalized[current[1]:window_end]

        if idx > 0:
            previous = dates[idx - 1]
            gap = normalized[previous[1]:current[0]]
            range_before = normalized[max(0, previous[0] - 100):previous[0]]
            range_after = normalized[current[1]:min(len(normalized), current[1] + 100)]
            if (
                len(gap) <= 24
                and re.search(r"[~\-–]", gap)
                and (
                    _APPLICATION_RANGE_CONTEXT_RE.search(range_before)
                    or re.search(r"기간\s*내.*(?:참가신청서|신청서)\s*제출", range_after)
                )
            ):
                return True

        if _DEADLINE_CONTEXT_RE.search(before) and re.search(r"(?:까지|마감|기한|종료)", after):
            return True

    return False


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
            range_end = _first_application_date_range_end(snippet, reference_date)
            if range_end:
                return range_end
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
    event_deadline = _extract_event_deadline_from_context(text, reference_date)
    if event_deadline:
        return event_deadline[0]
    return None


def extract_deadline_time(
    text: str,
    deadline: str | None = None,
    notice_date: str | None = None,
) -> str | None:
    """명시된 마감시간을 HH:MM 형식으로 추출. 날짜만 있으면 None."""
    deadline = deadline or extract_deadline(text, notice_date)
    if not deadline:
        return None

    reference_date = _parse_reference_date(notice_date)
    normalized = re.sub(r"\s+", " ", text)
    records = _extract_datetime_records(normalized, reference_date)
    candidates = [record for record in records if record[2] == deadline and record[3]]
    event_deadline = _extract_event_deadline_from_context(text, reference_date)
    if event_deadline and event_deadline[0] == deadline and event_deadline[1]:
        return event_deadline[1]
    if not candidates:
        return None

    for start, end, _, parsed_time in reversed(candidates):
        before = normalized[max(0, start - 90):start]
        after = normalized[end:min(len(normalized), end + 40)]
        if re.search(r"(?:까지|마감|기한|종료)", after):
            return parsed_time
        if _DEADLINE_CONTEXT_RE.search(before):
            return parsed_time

    return None


def extract_deadline_info(text: str, notice_date: str | None = None) -> dict:
    """마감일, 마감시간, 한국시간 기준 마감일시를 함께 추출."""
    deadline = extract_deadline(text, notice_date)
    deadline_time = extract_deadline_time(text, deadline, notice_date)
    return {
        "deadline": deadline,
        "deadline_time": deadline_time,
        "deadline_at": _build_deadline_at(deadline, deadline_time),
    }


# ── 지원 대상 추출 (regex) ────────────────────────────────

_TARGET_PATTERNS = [
    r"(?:신청|지원|참가|모집)?\s*대상\s*[:\-]?\s*([^\n,。;]{2,40})",
    r"지원\s*자격\s*[:\-]?\s*([^\n,。;]{2,40})",
    r"((?:[1-4]학년|전\s*학년|재학생|대학원생|졸업(?:예정)?자)(?:\s*[^\n,。;]{0,20})?)",
]

_INVALID_TARGET_RE = re.compile(
    r"(?:"
    r"사유\s*및\s*인정기간|인정기간|증빙서류|유의사항|"
    r"장학금액|지원금액|신청방법|제출방법|선발계획|"
    r"설문조사\s*참여\s*안내|웹정보|수강신청|과목|버튼|체크|"
    r"신청|접수|제출|출력|공지|안내|바랍니다|실시|"
    r"학점|시험\s*일정|상담|서류|금액|기간|평가하여|입니다|께서는"
    r")"
)


def _clean_extracted_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n-:：,。.;·ㆍ")


def _is_valid_target_candidate(value: str) -> bool:
    if not (2 <= len(value) <= 40):
        return False
    if _INVALID_TARGET_RE.search(value):
        return False
    if re.search(r"https?://|www\.|@[A-Za-z0-9.-]+", value):
        return False
    if re.match(r"^(?:으로|기관으로|자는|자\s*:|에\s+준하는|\(|\*)", value):
        return False
    if re.fullmatch(r"(?:으로|자|대상|학생|교강사|기간|내용)(?:\s*[^\w가-힣].*)?", value):
        return False
    return True


def extract_target(text: str) -> str | None:
    """공지 지원 대상 추출. 없으면 None."""
    if re.search(r"유고결석\s*출석인정", text):
        return "유고결석 출석인정을 받고자 하는 학생"

    for pattern in _TARGET_PATTERNS:
        for m in re.finditer(pattern, text):
            result = _clean_extracted_phrase(m.group(1))
            if _is_valid_target_candidate(result):
                return result
    return None


# ── 신청 방법 추출 (regex) ────────────────────────────────

_APPLY_PATTERNS = [
    r"신청\s*및\s*승인\s*절차\s*[:\-]?\s*([^\n。;]{3,60})",
    r"(?:신청|지원|참가|접수)\s*방법\s*[:\-]?\s*([^\n。;]{3,60})",
    r"(?:신청처|접수처)\s*[:\-]?\s*([^\n。;]{3,60})",
    r"((?:웹정보시스템|웹정보)\s*신청(?:\s*및\s*증빙서류\s*업로드)?)",
    r"((?:이메일|온라인|구글\s*폼|홈페이지|방문|우편)\s*[^\n]{0,30}(?:접수|신청|제출|등록))",
]

_APPLY_ACTION_RE = re.compile(
    r"(?:"
    r"이메일|메일|온라인|구글|폼|홈페이지|웹정보|방문|우편|제출|접수|신청|등록|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r")"
)
_INVALID_APPLY_RE = re.compile(
    r"^(?:"
    r"및\s+|[0-9]+[.)]\s*|\[[^\]]+\]\s*버튼|[가-힣]\s*[>⇒→]|"
    r"(?:신청|접수|지원|모집)\s*기간\s*:"
    r")"
)


def _is_valid_apply_candidate(value: str) -> bool:
    if not (3 <= len(value) <= 60):
        return False
    if _INVALID_APPLY_RE.search(value):
        return False
    if value in {"및 선발계획", "선발계획"}:
        return False
    return bool(_APPLY_ACTION_RE.search(value))


def extract_apply_method(text: str) -> str | None:
    """신청 방법 추출. 없으면 None."""
    for pattern in _APPLY_PATTERNS:
        for m in re.finditer(pattern, text):
            result = _clean_extracted_phrase(m.group(1))
            if _is_valid_apply_candidate(result):
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

    deadline_info = extract_deadline_info(combined, notice_date)
    result = {
        **deadline_info,
        "target": extract_target(combined),
        "apply_method": extract_apply_method(combined),
    }

    if any(result.values()):
        log.info(
            f"  [regex] 핵심정보 추출 — "
            f"마감:{result['deadline']} | "
            f"시간:{result['deadline_time']} | "
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
  "deadline_time": "마감시간이 명시되어 있으면 HH:MM 형식, 없으면 null",
  "target": "지원/신청 대상을 40자 이내로, 없으면 null",
  "apply_method": "신청 방법을 60자 이내로, 없으면 null"
}}

=== deadline 추출 규칙 ===

[deadline의 의미]
- deadline은 사용자가 신청·접수·지원·모집·제출·등록·응답·참가신청을 완료해야 하는 마지막 날짜입니다.
- 단순히 행사가 열리는 날짜, 대회가 진행되는 날짜, 예선/본선/결선/시상식 날짜는 deadline이 아닙니다.

[추출 우선순위 — 가장 높은 순위의 날짜 1개만 반환]
1순위: 신청·접수·지원·모집·제출·등록·응답·참가신청의 마감일 또는 기간 종료일
  키워드 예: "~까지", "마감", "기한", "접수 종료", "신청 마감", "모집 마감", "신청 접수", "참가자 접수", "참가신청서 제출"
  날짜 범위 예: "참가자 접수(5.26~7.30)" → 2026-07-30
  날짜 범위 예: "2026.5.26.(화)~7.30.(목) 기간 내 온라인 참가신청서 제출" → 2026-07-30
2순위: 별도 신청·접수·제출 기한이 전혀 없고, 공지가 단순 행사 참석/운영 안내이면 행사 일시의 날짜 사용
  예: "일시: 2026.5.19 10:00~16:00" → deadline=2026-05-19, deadline_time=16:00
3순위: 서비스·프로그램 이용 기간 자체가 신청 대상이고 별도 신청기한이 없으면 그 기간의 종료일
  예: "이용 기간: 2026.05.01~2026.10.30" → 2026-10-30

[반드시 제외 — deadline으로 쓰지 말 것]
- 대회기간·행사기간·운영기간의 종료일. 단, 그 문장이 신청/접수/제출 기간이면 제외 아님
- 발대식·오리엔테이션·교육일·예선·본선·결선·시상식·발표회 날짜
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
- 신청/접수/지원/제출 문맥의 연도 없는 날짜 범위도 끝 날짜 사용
  예: "신청 접수 (5.13.(수) ~ 6.26.(금))" → 2026-06-26
- 명시된 마감시간이 있으면 deadline_time으로 반환
  예: "2026.5.24 16:00까지" → deadline=2026-05-24, deadline_time=16:00
  예: "2026년 5월 24일 오후 4시 마감" → deadline=2026-05-24, deadline_time=16:00
  예: "2026.5.1 09:00 ~ 2026.5.24 18:00" → deadline=2026-05-24, deadline_time=18:00
- 행사 일시 fallback을 쓰는 경우에는 시간 범위의 종료 시간을 deadline_time으로 반환
- 신청/접수 마감이 따로 있으면 행사 진행 시간은 deadline_time으로 쓰지 말고 null

[신청 마감 vs 행사 일시가 함께 있을 때]
공지에 신청 마감일과 행사 개최일이 모두 있으면 반드시 신청 마감일을 사용.
예: "신청: ~5/15 / 행사 일시: 6/1" → 2026-05-15
예: "일정: 참가자 접수(5.26~7.30), 발대식(8.20), 예선(8.20~10.14), 본선(11.28)" → 2026-07-30
예: "대회기간: 5.13~9.30 / 신청 접수: 5.13~6.26" → 2026-06-26

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
_TIME_VALUE_RE = re.compile(r"^\d{2}:\d{2}$")


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

    deadline_time = data.get("deadline_time") or None
    if deadline_time is not None and not _TIME_VALUE_RE.match(deadline_time):
        log.debug(f"  [AI] deadline_time 포맷 불일치, null 처리: {deadline_time!r}")
        deadline_time = None
    if deadline_time is not None:
        try:
            parsed = _parse_time(None, deadline_time[:2], deadline_time[3:])
        except Exception:
            parsed = None
        deadline_time = parsed

    return {
        "deadline": deadline,
        "deadline_time": deadline_time,
        "deadline_at": _build_deadline_at(deadline, deadline_time),
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
            f"시간:{result['deadline_time']} | "
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
        {deadline, deadline_time, deadline_at, target, apply_method}
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

    if regex_result["deadline"] is None:
        deadline = ai_result["deadline"]
    elif ai_result["deadline"] is None or ai_result["deadline"] == regex_result["deadline"]:
        deadline = regex_result["deadline"]
    elif _has_application_deadline_evidence(combined, ai_result["deadline"], _parse_reference_date(notice_date)):
        log.info(
            "  [AI] regex deadline overridden after application-context evidence check: "
            f"{regex_result['deadline']} -> {ai_result['deadline']}"
        )
        deadline = ai_result["deadline"]
    else:
        log.info(
            "  [AI] keeping regex deadline; AI date lacked application-context evidence: "
            f"{regex_result['deadline']} vs {ai_result['deadline']}"
        )
        deadline = regex_result["deadline"]

    if ai_result["deadline"] == deadline and ai_result["deadline_time"] is not None:
        deadline_time = ai_result["deadline_time"]
    elif regex_result["deadline"] == deadline:
        deadline_time = regex_result["deadline_time"]
    else:
        deadline_time = None

    merged = {
        "deadline": deadline,
        "deadline_time": deadline_time,
        "deadline_at": _build_deadline_at(deadline, deadline_time),
        "target":       ai_result["target"]       if ai_result["target"]       is not None else regex_result["target"],
        "apply_method": ai_result["apply_method"] if ai_result["apply_method"] is not None else regex_result["apply_method"],
    }
    return merged
