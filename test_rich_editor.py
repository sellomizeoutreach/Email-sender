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
        # Create a large dummy 2400x1600 image
        img = Image.new("RGB", (2400, 1600), color=(255, 90, 31))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw_bytes = buf.getvalue()

        res = process_and_store_image(raw_bytes, mime_type="image/jpeg", lead_id="test_lead_99")
        data_uri, fpath, w, h = res

        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))
        self.assertTrue(os.path.exists(fpath))
        self.assertEqual(w, MAX_IMAGE_WIDTH)  # Downscaled to 1200px
        self.assertEqual(h, 800)  # Proportional height (1600 * 1200 / 2400)
        self.assertTrue(hasattr(res, "image_id"))
        self.assertTrue(bool(res.image_id))

        # Check DB images record
        from database import get_lead_images, delete_lead_image
        lead_imgs = get_lead_images("test_lead_99")
        self.assertTrue(any(img["id"] == res.image_id for img in lead_imgs))

        # File size under 10MB cap
        fsize = os.path.getsize(fpath)
        self.assertLess(fsize, 10 * 1024 * 1024)

        # Clean up test
        delete_lead_image(res.image_id)
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
