import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crawler import file_handler
from crawler.file_handler import extract_text, process_attachments


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )


def _write_docx(path: Path, document_body: str, header_body: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", _docx_xml(document_body))
        if header_body is not None:
            z.writestr("word/header1.xml", _docx_xml(header_body))


class FileHandlerTests(unittest.TestCase):
    def test_extracts_docx_paragraph_table_and_header_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.docx"
            _write_docx(
                path,
                "<w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>"
                "<w:tbl><w:tr>"
                "<w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc>"
                "<w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc>"
                "</w:tr></w:tbl>"
                "<w:p><w:r><w:t>Before</w:t><w:tab/><w:t>After tab</w:t>"
                "<w:br/><w:t>After break</w:t></w:r></w:p>",
                "<w:p><w:r><w:t>Header text</w:t></w:r></w:p>",
            )

            text, parser = extract_text(path, "docx")

        self.assertEqual(parser, "docx_xml")
        self.assertIn("First paragraph", text)
        self.assertIn("Cell A", text)
        self.assertIn("Cell B", text)
        self.assertIn("Before\tAfter tab\nAfter break", text)
        self.assertIn("Header text", text)


class AttachmentCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_attachments_reuses_existing_non_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            files_dir = Path(tmp)
            cached_path = files_dir / "SW_1_cached.docx"
            _write_docx(
                cached_path,
                "<w:p><w:r><w:t>Cached document text</w:t></w:r></w:p>",
            )

            with (
                patch.object(file_handler, "FILES_DIR", files_dir),
                patch.object(file_handler, "download_file", new=AsyncMock(return_value=True)) as download,
            ):
                results = await process_attachments(
                    [{"name": "cached.docx", "url": "https://example.test/cached.docx", "ext": "docx"}],
                    "SW_1",
                )

        download.assert_not_awaited()
        self.assertEqual(len(results), 1)
        attachment = results[0]
        self.assertTrue(attachment["download_ok"])
        self.assertTrue(attachment["download_cached"])
        self.assertEqual(attachment["parser"], "docx_xml")
        self.assertEqual(attachment["parse_quality"], "full")
        self.assertIn("Cached document text", attachment["extracted_text"])
        self.assertGreater(attachment["file_size"], 0)
        self.assertRegex(attachment["checksum"], r"^[0-9a-f]{64}$")

    async def test_process_attachments_redownloads_zero_byte_file(self):
        async def fake_download(_url: str, save_path: Path) -> bool:
            save_path.write_bytes(b"fresh")
            return True

        with tempfile.TemporaryDirectory() as tmp:
            files_dir = Path(tmp)
            cached_path = files_dir / "SW_1_empty.txt"
            cached_path.write_bytes(b"")

            with (
                patch.object(file_handler, "FILES_DIR", files_dir),
                patch.object(file_handler, "download_file", side_effect=fake_download) as download,
            ):
                results = await process_attachments(
                    [{"name": "empty.txt", "url": "https://example.test/empty.txt", "ext": "txt"}],
                    "SW_1",
                )

        self.assertEqual(download.await_count, 1)
        self.assertEqual(len(results), 1)
        attachment = results[0]
        self.assertTrue(attachment["download_ok"])
        self.assertFalse(attachment["download_cached"])
        self.assertEqual(attachment["file_size"], 5)
        self.assertEqual(attachment["parser"], "none")
        self.assertEqual(attachment["parse_quality"], "none")


if __name__ == "__main__":
    unittest.main()
