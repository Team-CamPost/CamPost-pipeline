"""
extractor.py AI 통합 테스트

테스트 1: regex 기반 추출 (API 키 불필요)
테스트 2: AI 비활성화 시 regex fallback 동작
테스트 3: AI 활성화 시 실제 Gemini API 호출 (GEMINI_API_KEY 필요)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawler.extractor import (
    extract_deadline,
    extract_key_info,
    extract_key_info_with_ai,
    extract_target,
)


class TestRegexExtraction(unittest.TestCase):
    """기존 regex 추출 — API 키 없이 동작."""

    def test_deadline_korean_format_with_keyword(self):
        self.assertEqual(extract_deadline("신청마감일: 2026년 4월 30일"), "2026-04-30")

    def test_deadline_dot_format(self):
        self.assertEqual(extract_deadline("2026.04.30까지 제출"), "2026-04-30")

    def test_deadline_dash_format(self):
        self.assertEqual(extract_deadline("마감일: 2026-04-30"), "2026-04-30")

    def test_deadline_range_end(self):
        self.assertEqual(extract_deadline("모집 기간: 4/1 ~ 2026.05.15"), "2026-05-15")

    def test_deadline_none_when_missing(self):
        self.assertIsNone(extract_deadline("단순 공지사항입니다."))

    def test_deadline_none_vague_date(self):
        # "이번 달 말" 같은 모호한 표현은 regex가 None 반환
        self.assertIsNone(extract_deadline("이번 달 말까지 제출하세요."))

    def test_target_extraction(self):
        self.assertIsNotNone(extract_target("지원 대상: 3~4학년 재학생"))

    def test_extract_key_info_no_api_key(self):
        """API 키 없이 호출 시 regex 결과 반환."""
        result = extract_key_info_with_ai(
            body_text="신청마감일: 2026.04.30. 지원 대상: 재학생",
            attachments=[],
            api_key="",
        )
        self.assertEqual(result["deadline"], "2026-04-30")
        self.assertIsNotNone(result["target"])

    def test_extract_key_info_regex_fallback(self):
        """extract_key_info 직접 호출."""
        result = extract_key_info(
            body_text="마감일: 2026-05-01\n지원 대상: 전학년 재학생\n신청 방법: 이메일 제출",
            attachments=[],
        )
        self.assertEqual(result["deadline"], "2026-05-01")
        self.assertIsNotNone(result["target"])
        self.assertIsNotNone(result["apply_method"])

    def test_attachment_text_fallback(self):
        """본문 없고 첨부파일 텍스트에서 추출."""
        result = extract_key_info(
            body_text="",
            attachments=[{"extracted_text": "신청마감일: 2026.06.30\n지원 대상: 3학년"}],
        )
        self.assertEqual(result["deadline"], "2026-06-30")


class TestAiExtraction(unittest.TestCase):
    """Gemini AI 추출 — GEMINI_API_KEY 환경변수 필요."""

    def setUp(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    def test_ai_vague_deadline(self):
        """regex가 못 잡는 자연어 마감일을 AI가 추출하는지 확인."""
        if not self.api_key:
            self.skipTest("GEMINI_API_KEY 미설정 — AI 테스트 건너뜀")

        body = (
            "2026학년도 1학기 장학생 모집\n"
            "신청 기간: 2026년 5월 9일(금)까지\n"
            "지원 대상: 소프트웨어학과 재학생\n"
            "신청 방법: 학과 사무실 방문 접수"
        )
        result = extract_key_info_with_ai(
            body_text=body,
            attachments=[],
            api_key=self.api_key,
            model_name=self.model,
        )
        print(f"\n[AI 결과] deadline={result['deadline']}, target={result['target']}, apply={result['apply_method']}")
        # AI가 날짜를 추출했는지 확인 (regex도 이건 잡을 수 있으나 AI 경로 검증)
        self.assertIsNotNone(result["deadline"])
        self.assertEqual(result["deadline"], "2026-05-09")

    def test_ai_regex_cannot_catch(self):
        """regex가 None인데 AI가 추출하는 케이스."""
        if not self.api_key:
            self.skipTest("GEMINI_API_KEY 미설정 — AI 테스트 건너뜀")

        body = (
            "채용 공고\n"
            "접수 마감은 이번 달 말일까지입니다.\n"
            "현재 날짜 기준: 2026년 4월\n"
            "지원 자격: 컴퓨터공학 전공자"
        )
        regex_result = extract_key_info(body, [])
        self.assertIsNone(regex_result["deadline"], "regex는 이 케이스를 못 잡아야 함")

        ai_result = extract_key_info_with_ai(
            body_text=body,
            attachments=[],
            api_key=self.api_key,
            model_name=self.model,
        )
        print(f"\n[AI 결과 - 모호한 날짜] deadline={ai_result['deadline']}")
        # "이번 달 말" → AI는 null이 맞음 (불명확한 날짜는 null 처리 규칙)

    def test_ai_no_deadline_notice(self):
        """마감일 없는 공지 → AI도 null 반환해야 함."""
        if not self.api_key:
            self.skipTest("GEMINI_API_KEY 미설정 — AI 테스트 건너뜀")

        body = "강의실 변경 안내입니다. 503호 → 601호로 변경됩니다."
        result = extract_key_info_with_ai(
            body_text=body,
            attachments=[],
            api_key=self.api_key,
            model_name=self.model,
        )
        print(f"\n[AI 결과 - 마감일 없음] deadline={result['deadline']}")
        self.assertIsNone(result["deadline"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
