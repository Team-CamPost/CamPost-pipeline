import unittest

from crawler.extractor import (
    extract_apply_method,
    extract_deadline,
    extract_deadline_info,
    extract_deadline_time,
    extract_key_info,
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

    def test_extract_target_from_text(self):
        text = "지원 대상: 3~4학년 재학생"
        self.assertEqual(extract_target(text), "3~4학년 재학생")

    def test_extract_apply_method_from_text(self):
        text = "신청 방법: 이메일 제출"
        self.assertEqual(extract_apply_method(text), "이메일 제출")

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


if __name__ == "__main__":
    unittest.main()
