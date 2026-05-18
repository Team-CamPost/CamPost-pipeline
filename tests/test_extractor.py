import unittest
from unittest.mock import patch

from crawler.extractor import (
    extract_apply_method,
    extract_deadline,
    extract_deadline_info,
    extract_deadline_time,
    extract_key_info,
    extract_key_info_with_ai,
    extract_target,
)


class ExtractorTests(unittest.TestCase):
    def test_extract_deadline_from_text(self):
        text = "신청마감일: 2026.03.28"
        self.assertEqual(extract_deadline(text), "2026-03-28")

    def test_extract_deadline_time_from_colon_time(self):
        text = "신청마감일: 2026.03.28 16:30까지"
        self.assertEqual(extract_deadline(text), "2026-03-28")
        self.assertEqual(extract_deadline_time(text), "16:30")

    def test_extract_deadline_time_from_korean_pm_time(self):
        text = "제출 기한은 2026년 5월 24일 오후 4시까지입니다."
        self.assertEqual(extract_deadline_info(text), {
            "deadline": "2026-05-24",
            "deadline_time": "16:00",
            "deadline_at": "2026-05-24T16:00:00+09:00",
        })

    def test_extract_deadline_time_from_period_end(self):
        text = "신청기간: 2026.05.01 09:00 ~ 2026.05.24 18:00"
        self.assertEqual(extract_deadline_info(text), {
            "deadline": "2026-05-24",
            "deadline_time": "18:00",
            "deadline_at": "2026-05-24T18:00:00+09:00",
        })

    def test_extract_deadline_time_from_short_date(self):
        text = "신청기간: 4/1 09:00 ~ 4/13(월) 17시까지"
        self.assertEqual(extract_deadline_info(text, "2026.04.02"), {
            "deadline": "2026-04-13",
            "deadline_time": "17:00",
            "deadline_at": "2026-04-13T17:00:00+09:00",
        })

    def test_event_time_is_not_deadline_time(self):
        text = "예선: 2026. 5. 16(토) 14:00 ~ 16:00 (온라인, 2시간)"
        self.assertEqual(extract_deadline(text), "2026-05-16")
        self.assertIsNone(extract_deadline_time(text))

    def test_event_datetime_end_time_is_fallback_deadline(self):
        text = """
        행사 개요
        1) 행사명 : 2026학년도 전공박람회
        2) 일 시 : 2026. 5. 19.(화) 10:00~16:00
        3) 장 소 : 혜당관~도서관 사이 광장
        """
        self.assertEqual(extract_deadline_info(text, "2026.05.13"), {
            "deadline": "2026-05-19",
            "deadline_time": "16:00",
            "deadline_at": "2026-05-19T16:00:00+09:00",
        })

    def test_extract_target_from_text(self):
        text = "지원 대상: 3~4학년 재학생"
        self.assertEqual(extract_target(text), "3~4학년 재학생")

    def test_extract_target_skips_attachment_table_header(self):
        text = """
        첨부 문서 표 추출 테스트
        나. 인정사유 및 인정기간: 세부내용은 첨부 참조

        대상 사유및인정기간 증빙서류
        체육특기자 전형으로 입학한 자
        """
        self.assertIsNone(extract_target(text))

    def test_extract_target_skips_procedure_sentences(self):
        text = """
        신청 및 승인 절차: 학생(웹정보: 신청)-교학행정팀(접수)-교원(승인/미승인)
        1. [출석과목조회] 버튼 클릭
        2. 수강신청 과목목록 확인
        """
        self.assertIsNone(extract_target(text))

    def test_extract_target_from_excused_absence_notice(self):
        text = "2026학년도 1학기 유고결석 출석인정 안내"
        self.assertEqual(extract_target(text), "유고결석 출석인정을 받고자 하는 학생")

    def test_extract_apply_method_from_text(self):
        text = "신청 방법: 이메일 제출"
        self.assertEqual(extract_apply_method(text), "이메일 제출")

    def test_extract_apply_method_skips_section_heading_and_steps(self):
        text = """
        ※ 신청방법 및 선발계획
        - 신청기간 : 2026.03.27 까지
        - 신청방법 : chois6@dankook.ac.kr로 신청서 첨부 후 메일 작성

        신청[웹정보시스템] 방법
        1. [출석과목조회] 버튼 클릭
        """
        self.assertEqual(
            extract_apply_method(text),
            "chois6@dankook.ac.kr로 신청서 첨부 후 메일 작성",
        )

    def test_extract_apply_method_from_approval_process(self):
        text = "신청 및 승인 절차: 학생(웹정보: 신청)-교학행정팀(접수)-교원(승인/미승인)"
        self.assertEqual(
            extract_apply_method(text),
            "학생(웹정보: 신청)-교학행정팀(접수)-교원(승인/미승인)",
        )

    def test_extract_apply_method_from_webinfo_application(self):
        text = "학생 [신청]\n웹정보시스템 신청\n(증빙서류 업로드)"
        self.assertEqual(extract_apply_method(text), "웹정보시스템 신청")

    def test_extract_apply_method_skips_application_period(self):
        text = """
        신청기간 : 2026. 6. 10(수) 11:00까지
        신청방법 : 홈페이지에서 신청서 제출
        """
        self.assertEqual(extract_apply_method(text), "홈페이지에서 신청서 제출")

    def test_extract_deadline_from_competition_notice(self):
        text = "예선: 2026. 5. 16(토) 14:00 ~ 16:00 (온라인, 2시간)"
        self.assertEqual(extract_deadline(text), "2026-05-16")

    def test_extract_deadline_from_service_period(self):
        # 서비스 기간 종료일은 regex 3번 패턴(범위 끝)으로 추출
        text = "서비스 기간 : 2026.05.01.~2026.10.30."
        self.assertEqual(extract_deadline(text), "2026-10-30")

    def test_extract_key_info_uses_attachments_when_body_is_empty(self):
        attachments = [
            {
                "extracted_text": "마감일: 2026-04-30\n지원 대상: 전학년 재학생\n신청 방법: 구글폼 신청"
            }
        ]
        result = extract_key_info("", attachments)

        self.assertEqual(result["deadline"], "2026-04-30")
        self.assertIsNone(result["deadline_time"])
        self.assertIsNone(result["deadline_at"])
        self.assertEqual(result["target"], "전학년 재학생")
        self.assertEqual(result["apply_method"], "구글폼 신청")

    def test_extract_deadline_from_title_short_date(self):
        result = extract_key_info(
            "",
            [],
            title="[TOPCIT] 단체접수 신청 안내(~ 4/13(월)까지)",
            notice_date="2026.04.02",
        )
        self.assertEqual(result["deadline"], "2026-04-13")

    def test_extract_deadline_from_response_deadline(self):
        text = "■ 응답 기한 2026년 4월 29일(수)까지"
        self.assertEqual(extract_deadline(text, "2026.04.27"), "2026-04-29")

    def test_extract_deadline_from_period_with_short_end_date(self):
        text = "설문기간 2026.04.03(금) ~ 04.15(수)"
        self.assertEqual(extract_deadline(text, "2026.04.06"), "2026-04-15")

    def test_extract_deadline_from_application_approval_period(self):
        text = "신청 및 승인은 2026.03.03.(화) ~ 06.22.(월) 오전11시까지 가능."
        self.assertEqual(extract_deadline_info(text, "2026.04.08"), {
            "deadline": "2026-06-22",
            "deadline_time": "11:00",
            "deadline_at": "2026-06-22T11:00:00+09:00",
        })

    def test_extract_deadline_from_application_reception_period(self):
        text = """
        대회기간 : 2026년 5월 13일(수) ~ 9월 30일(수)
        다. 지원방법
        - 하기 참고 URL 신청 접수 (5.13.(수) ~ 6.26.(금))
        - https://www.metaversedev.kr/
        이벤트 기간: 2026.05.13.(수)~06.26.(금)
        """
        self.assertEqual(extract_deadline(text, "2026.05.13"), "2026-06-26")

    def test_extract_key_info_from_application_reception_period(self):
        result = extract_key_info(
            """
            대회기간 : 2026년 5월 13일(수) ~ 9월 30일(수)
            지원방법: 하기 참고 URL 신청 접수 (5.13.(수) ~ 6.26.(금))
            """,
            [],
            title="2026년 AI·가상융합(XR) 서비스 개발자 경진대회",
            notice_date="2026.05.13",
        )

        self.assertEqual(result["deadline"], "2026-06-26")
        self.assertIsNone(result["deadline_time"])
        self.assertIsNone(result["deadline_at"])

    def test_extract_deadline_from_participant_reception_period_before_event_dates(self):
        text = """
        o 일 정 : 참가자 접수(5.26~7.30), 발대식(8.20), 예선(8.20~10.14), 본선(11.28)
        나. 대회 안내 및 참가접수
        ※ 2026.5.26.(화)~7.30.(목) 기간 내 온라인 참가신청서 제출
        """
        self.assertEqual(extract_deadline(text, "2026.05.14"), "2026-07-30")

    def test_extract_key_info_from_participant_reception_period(self):
        result = extract_key_info(
            """
            참가대상 : 데이터 및 AI 분야에 관심 있는 학생(대학생, 고등학생 2개 부문)
            일 정 : 참가자 접수(5.26~7.30), 발대식(8.20), 예선(8.20~10.14), 본선(11.28)
            장 소 : 온라인(예선) 및 한국지능정보사회진흥원 서울사무소(본선)
            대회 안내 및 참가접수
            ※ 2026.5.26.(화)~7.30.(목) 기간 내 온라인 참가신청서 제출
            """,
            [],
            title="2026년 데이터+AI 크리에이터 캠프 참가자 모집",
            notice_date="2026.05.14",
        )

        self.assertEqual(result["deadline"], "2026-07-30")
        self.assertIsNone(result["deadline_time"])
        self.assertIsNone(result["deadline_at"])

    def test_ignores_general_event_period_before_application_period(self):
        result = extract_key_info(
            """
            대회기간 : 2026년 5월 13일(수) ~ 9월 30일(수)
            지원방법: 홈페이지에서 참가자 접수(5.26~7.30)
            """,
            [],
            title="데이터 경진대회 참가자 모집",
            notice_date="2026.05.13",
        )

        self.assertEqual(result["deadline"], "2026-07-30")

    def test_ai_does_not_override_application_deadline_with_final_event_date(self):
        with patch("crawler.extractor._ai_extract") as ai_extract:
            ai_extract.return_value = {
                "deadline": "2026-11-28",
                "deadline_time": None,
                "deadline_at": None,
                "target": None,
                "apply_method": None,
            }

            result = extract_key_info_with_ai(
                """
                참가자 접수(5.26~7.30), 발대식(8.20), 예선(8.20~10.14), 본선(11.28)
                ※ 2026.5.26.(화)~7.30.(목) 기간 내 온라인 참가신청서 제출
                """,
                [],
                api_key="test-key",
                title="2026년 데이터+AI 크리에이터 캠프 참가자 모집",
                notice_date="2026.05.14",
            )

        self.assertEqual(result["deadline"], "2026-07-30")

    def test_ai_can_override_when_candidate_has_application_evidence(self):
        with patch("crawler.extractor._ai_extract") as ai_extract:
            ai_extract.return_value = {
                "deadline": "2026-07-30",
                "deadline_time": None,
                "deadline_at": None,
                "target": None,
                "apply_method": None,
            }

            result = extract_key_info_with_ai(
                """
                마감일: 2026.08.20
                실제 신청 접수는 2026.5.26.(화)~7.30.(목)까지입니다.
                """,
                [],
                api_key="test-key",
                title="AI override evidence test",
                notice_date="2026.05.14",
            )

        self.assertEqual(result["deadline"], "2026-07-30")


if __name__ == "__main__":
    unittest.main()
