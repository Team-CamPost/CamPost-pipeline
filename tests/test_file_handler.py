import tempfile
import unittest
import zipfile
from pathlib import Path

from crawler.file_handler import extract_text


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )


class FileHandlerTests(unittest.TestCase):
    def test_extracts_docx_paragraph_table_and_header_text(self):
        document_xml = _docx_xml(
            "<w:p><w:r><w:t>첫 문단</w:t></w:r></w:p>"
            "<w:tbl><w:tr>"
            "<w:tc><w:p><w:r><w:t>표 셀 A</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>표 셀 B</w:t></w:r></w:p></w:tc>"
            "</w:tr></w:tbl>"
            "<w:p><w:r><w:t>탭</w:t><w:tab/><w:t>줄바꿈</w:t><w:br/><w:t>다음</w:t></w:r></w:p>"
        )
        header_xml = _docx_xml("<w:p><w:r><w:t>머리말</w:t></w:r></w:p>")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.docx"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", document_xml)
                z.writestr("word/header1.xml", header_xml)

            text, parser = extract_text(path, "docx")

        self.assertEqual(parser, "docx_xml")
        self.assertIn("첫 문단", text)
        self.assertIn("표 셀 A", text)
        self.assertIn("표 셀 B", text)
        self.assertIn("탭\t줄바꿈\n다음", text)
        self.assertIn("머리말", text)


if __name__ == "__main__":
    unittest.main()
