import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.re_extract as re_extract_script
from crawler.reprocess import (
    CONTENT_PAYLOAD_VERSION,
    CONTENT_VERSION_FIELD,
    KEY_INFO_BACKFILL_VERSION,
    KEY_INFO_BACKFILL_VERSION_FIELD,
    needs_key_info_backfill,
    stamp_key_info_backfill,
)


class ReprocessMetadataTests(unittest.TestCase):
    def test_key_info_backfill_version_distinguishes_normal_nulls(self):
        data = {
            "body_text": "Application period: 2026.05.01 ~ 2026.05.10",
            "deadline": "2026-05-10",
            "deadline_time": None,
            "deadline_at": None,
            "target": None,
            "apply_method": None,
        }

        self.assertTrue(needs_key_info_backfill(data))

        stamp_key_info_backfill(data)

        self.assertFalse(needs_key_info_backfill(data))
        self.assertIsNone(data["deadline_time"])
        self.assertIsNone(data["deadline_at"])

    def test_re_extract_only_null_stamps_backfill_without_requiring_deadline_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            raw_path = raw_dir / "SW_1.json"
            raw_path.write_text(
                json.dumps(
                    {
                        "article_id": "SW_1",
                        "title": "No time notice",
                        "date": "2026.05.01",
                        "body_text": "Application period: 2026.05.01 ~ 2026.05.10",
                        "attachments": [],
                        "deadline": "2026-05-10",
                        "deadline_time": None,
                        "deadline_at": None,
                        "target": None,
                        "apply_method": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(re_extract_script, "OUTPUT_DIR", root):
                re_extract_script.re_extract(
                    dry_run=False,
                    no_ai=True,
                    only_null=True,
                    source_filter=None,
                    fields=re_extract_script.KEY_INFO_FIELDS,
                )

            data = json.loads(raw_path.read_text(encoding="utf-8"))

        self.assertEqual(data["deadline"], "2026-05-10")
        self.assertIsNone(data["deadline_time"])
        self.assertIsNone(data["deadline_at"])
        self.assertEqual(data[KEY_INFO_BACKFILL_VERSION_FIELD], KEY_INFO_BACKFILL_VERSION)
        self.assertIn("raw_reprocessed_at", data)

    def test_re_extract_content_fields_stamp_content_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            raw_path = raw_dir / "SW_1.json"
            raw_path.write_text(
                json.dumps(
                    {
                        "article_id": "SW_1",
                        "body_html": '<p><img src="https://example.test/poster.png"></p>',
                        "attachments": [
                            {
                                "name": "poster.png",
                                "url": "https://example.test/poster.png",
                                "ext": "png",
                                "file_key": "SW_1_poster.png",
                                "local_path": "files/SW_1_poster.png",
                                "mime_type": "image/png",
                                "download_ok": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(re_extract_script, "OUTPUT_DIR", root):
                re_extract_script.re_extract(
                    dry_run=False,
                    no_ai=True,
                    only_null=False,
                    source_filter=None,
                    fields=re_extract_script.CONTENT_FIELDS,
                )

            data = json.loads(raw_path.read_text(encoding="utf-8"))

        self.assertIn('src="files/SW_1_poster.png"', data["content_html"])
        self.assertEqual(data[CONTENT_VERSION_FIELD], CONTENT_PAYLOAD_VERSION)
        self.assertIn("raw_reprocessed_at", data)


if __name__ == "__main__":
    unittest.main()
