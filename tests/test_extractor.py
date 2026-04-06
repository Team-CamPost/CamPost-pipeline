import unittest

from crawler.extractor import (
    extract_apply_method,
    extract_deadline,
    extract_key_info,
    extract_target,
)


class ExtractorTests(unittest.TestCase):
    def test_extract_deadline_from_text(self):
        text = "신청마감일: 2026.03.28"
        self.assertEqual(extract_deadline(text), "2026-03-28")

    def test_extract_target_from_text(self):
        text = "지원 대상: 3~4학년 재학생"
        self.assertEqual(extract_target(text), "3~4학년 재학생")

    def test_extract_apply_method_from_text(self):
        text = "신청 방법: 이메일 제출"
        self.assertEqual(extract_apply_method(text), "이메일 제출")

    def test_extract_key_info_uses_attachments_when_body_is_empty(self):
        attachments = [
            {
                "extracted_text": "마감일: 2026-04-30\n지원 대상: 전학년 재학생\n신청 방법: 구글폼 신청"
            }
        ]
        result = extract_key_info("", attachments)

        self.assertEqual(result["deadline"], "2026-04-30")
        self.assertEqual(result["target"], "전학년 재학생")
        self.assertEqual(result["apply_method"], "구글폼 신청")


if __name__ == "__main__":
    unittest.main()
