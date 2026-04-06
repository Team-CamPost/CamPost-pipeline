import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler import storage


class StorageTests(unittest.TestCase):
    def test_compute_hash_is_deterministic(self):
        h1 = storage.compute_hash("SW_100", "Notice title")
        h2 = storage.compute_hash("SW_100", "Notice title")
        self.assertEqual(h1, h2)

    def test_save_and_load_seen_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            hashes_file = Path(tmp) / "seen_hashes.json"
            with patch.object(storage, "HASHES_FILE", hashes_file):
                storage.save_seen_hashes({"c", "a", "b"})
                loaded = storage.load_seen_hashes()

            self.assertEqual(loaded, {"a", "b", "c"})

    def test_save_raw_json_and_list_raw_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)

            notice = {
                "article_id": "SW_170663",
                "title": "Test notice",
                "body_text": "Body",
                "attachments": [],
            }

            with patch.object(storage, "RAW_STORE_DIR", raw_dir):
                saved = storage.save_raw_json(notice)
                files = storage.list_raw_json()

            self.assertTrue(saved.exists())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, "SW_170663.json")

            payload = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(payload["article_id"], "SW_170663")
            self.assertIn("raw_saved_at", payload)


if __name__ == "__main__":
    unittest.main()
