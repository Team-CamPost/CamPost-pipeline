import unittest

from crawler.content import build_content_payload


class ContentTests(unittest.TestCase):
    def test_rewrites_downloaded_external_image_src(self):
        payload = build_content_payload(
            '<p><img src="https://example.com/poster.png"></p>',
            [
                {
                    "name": "poster.png",
                    "url": "https://example.com/poster.png",
                    "ext": "png",
                    "file_key": "SW_1_poster.png",
                    "local_path": "files/SW_1_poster.png",
                    "mime_type": "image/png",
                    "file_size": 100,
                    "download_ok": True,
                }
            ],
        )

        self.assertIn('src="files/SW_1_poster.png"', payload["content_html"])
        self.assertEqual(payload["content_stats"]["image_count"], 1)
        self.assertEqual(payload["content_assets"]["images"][0]["src"], "files/SW_1_poster.png")

    def test_rewrites_inline_image_src_by_order(self):
        payload = build_content_payload(
            '<img src="data:image/png;base64,AAA=">',
            [
                {
                    "name": "SW_1_inline_img_0.png",
                    "url": "",
                    "ext": "png",
                    "file_key": "SW_1_inline_img_0.png",
                    "local_path": "files/SW_1_inline_img_0.png",
                    "mime_type": "image/png",
                    "download_ok": True,
                }
            ],
        )

        self.assertIn('src="files/SW_1_inline_img_0.png"', payload["content_html"])
        self.assertNotIn("data:image", payload["content_html"])

    def test_appends_unembedded_image_attachment_gallery(self):
        payload = build_content_payload(
            "<p>Body</p>",
            [
                {
                    "name": "poster.png",
                    "url": "https://example.com/download=true",
                    "ext": "png",
                    "file_key": "SW_1_poster.png",
                    "local_path": "files/SW_1_poster.png",
                    "mime_type": "image/png",
                    "download_ok": True,
                }
            ],
        )

        self.assertIn("notice-content-image-attachments", payload["content_html"])
        self.assertIn('src="files/SW_1_poster.png"', payload["content_html"])
        self.assertEqual(payload["content_stats"]["image_count"], 1)

    def test_counts_tables_and_strips_script(self):
        payload = build_content_payload(
            '<script>alert(1)</script><table><tr><td>A</td></tr></table>',
            [],
        )

        self.assertNotIn("<script", payload["content_html"].lower())
        self.assertEqual(payload["content_stats"]["table_count"], 1)


if __name__ == "__main__":
    unittest.main()
