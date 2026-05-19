import json
import tempfile
import unittest
from pathlib import Path

from crawler.quality import (
    RawNotice,
    audit_attachment_quality,
    audit_content_html,
    find_duplicate_attachments,
    load_raw_notices,
    normalize_attachment_metadata,
)


def _notice(article_id: str, **data):
    payload = {"article_id": article_id, "title": f"title {article_id}", **data}
    return RawNotice(Path(f"{article_id}.json"), payload)


class QualityAuditTests(unittest.TestCase):
    def test_load_raw_notices_reports_broken_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            (raw_dir / "ok.json").write_text(json.dumps({"article_id": "SW_1"}), encoding="utf-8")
            (raw_dir / "bad.json").write_text("{", encoding="utf-8")

            notices, errors = load_raw_notices(raw_dir)

        self.assertEqual([notice.article_id for notice in notices], ["SW_1"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["file"], "bad.json")

    def test_audit_content_html_detects_missing_and_unsafe_content(self):
        notices = [
            _notice("SW_1", body_html="<p>Body</p>"),
            _notice(
                "SW_2",
                content_html='<script>x</script><img src="https://example.test/a.png">',
                content_assets={"images": [], "files": []},
                content_stats={"image_count": 1, "file_count": 0, "table_count": 0},
            ),
        ]

        report = audit_content_html(notices)
        codes = {issue["code"] for issue in report["issues"]}

        self.assertIn("missing_content_html", codes)
        self.assertIn("unsafe_tag", codes)
        self.assertIn("external_image_src", codes)
        self.assertIn("image_count_mismatch", codes)

    def test_audit_attachment_quality_detects_metadata_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            files_root = Path(tmp)
            file_path = files_root / "SW_1_sample.pdf"
            file_path.write_bytes(b"pdf")

            notices = [
                _notice(
                    "SW_1",
                    attachments=[
                        {
                            "name": "sample.pdf",
                            "ext": "pdf",
                            "local_path": "files/SW_1_sample.pdf",
                            "download_ok": True,
                            "parser": "pdfplumber",
                            "parse_ok": True,
                            "parse_quality": "none",
                            "extracted_text": "abc",
                            "extracted_chars": 2,
                        }
                    ],
                )
            ]

            report = audit_attachment_quality(notices, files_root)

        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("extracted_chars_mismatch", codes)
        self.assertIn("parse_quality_mismatch", codes)

    def test_find_duplicate_attachments_groups_by_checksum_and_url(self):
        notices = [
            _notice(
                "SW_1",
                attachments=[
                    {"name": "a.pdf", "checksum": "abc", "url": "https://example.test/a", "file_key": "a"},
                    {"name": "a-copy.pdf", "checksum": "abc", "url": "https://example.test/a", "file_key": "b"},
                ],
            ),
            _notice(
                "SW_2",
                attachments=[
                    {"name": "a.pdf", "checksum": "abc", "url": "https://example.test/a", "file_key": "c"}
                ],
            ),
        ]

        report = find_duplicate_attachments(notices)

        self.assertEqual(report["summary"]["duplicate_checksum_groups"], 1)
        self.assertEqual(report["summary"]["duplicate_url_groups"], 1)
        self.assertEqual(report["summary"]["same_notice_duplicates"], 1)

    def test_normalize_attachment_metadata_builds_safe_updates(self):
        notices = [
            _notice(
                "SW_1",
                attachments=[
                    {
                        "name": "sample.pdf",
                        "ext": "pdf",
                        "download_ok": True,
                        "parser": "pdfplumber",
                        "parse_ok": False,
                        "extracted_text": "abc",
                    }
                ],
            )
        ]

        changes = normalize_attachment_metadata(notices, Path("files"))

        updates = changes[0]["changes"][0]["updates"]
        self.assertEqual(updates["extracted_chars"], 3)
        self.assertTrue(updates["parse_ok"])
        self.assertEqual(updates["parse_quality"], "full")
        self.assertFalse(updates["download_cached"])


if __name__ == "__main__":
    unittest.main()
