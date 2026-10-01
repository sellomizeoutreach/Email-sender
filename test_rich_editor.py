"""
test_rich_editor.py - Standalone Unit Verification for ui/rich_editor.py
Phase 1 acceptance tests.
"""

import os
import io
import unittest
from PIL import Image

from ui.rich_editor import (
    process_and_store_image,
    sanitize_pasted_html,
    is_rich_editor_enabled,
    MAX_IMAGE_WIDTH
)


class TestRichEditor(unittest.TestCase):

    def test_image_processing_downscale(self):
        # Create a large dummy 1200x800 image
        img = Image.new("RGB", (1200, 800), color=(255, 90, 31))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw_bytes = buf.getvalue()

        data_uri, fpath, w, h = process_and_store_image(raw_bytes, mime_type="image/jpeg")

        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))
        self.assertTrue(os.path.exists(fpath))
        self.assertEqual(w, MAX_IMAGE_WIDTH)  # Downscaled to 600px
        self.assertEqual(h, 400)  # Proportional height (800 * 600 / 1200)

        # File size under 2MB cap
        fsize = os.path.getsize(fpath)
        self.assertLess(fsize, 2 * 1024 * 1024)

        # Clean up test file
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass

    def test_sanitize_pasted_html(self):
        messy_html = """
        <!-- [if gte mso 9]><xml><w:WordDocument></w:WordDocument></xml><![endif]-->
        <script>alert('malicious')</script>
        <p style="mso-special-format:bullet; color:red;" class="MsoNormal">
            <strong>Hello [Name]</strong>, check this <a href="https://sellomize.com">link</a>!
        </p>
        <o:p></o:p>
        """
        cleaned = sanitize_pasted_html(messy_html)

        self.assertNotIn("<script>", cleaned)
        self.assertNotIn("alert", cleaned)
        self.assertNotIn("mso-special-format", cleaned)
        self.assertNotIn("<o:p>", cleaned)
        self.assertIn("<strong>Hello [Name]</strong>", cleaned)
        self.assertIn("https://sellomize.com", cleaned)

    def test_feature_flag(self):
        self.assertTrue(is_rich_editor_enabled())


if __name__ == "__main__":
    unittest.main()
