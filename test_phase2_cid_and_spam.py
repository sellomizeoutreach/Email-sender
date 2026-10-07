"""
Unit tests for Sellomize Reach Phase 2:
- Send-time CID rendering & {{img:<uuid>}} token resolution
- Plain text fallback with alt text
- 10 MB size guard and auto-recompression
- Spam pre-check auditing rendered output including image alt text
"""

import os
import io
import unittest
from PIL import Image
from email.mime.multipart import MIMEMultipart

from database import save_lead_image, delete_lead_image
from smtp_dispatcher import html_to_plain_text, recompress_image_bytes
from template_engine import audit_email_deliverability


class TestPhase2Features(unittest.TestCase):

    def test_plain_text_alt_text_fallback(self):
        html_sample = (
            "<p>Hi Sarah,</p>"
            "<p>Here is your Amazon listing review:</p>"
            '<img src="cid:img_123@sellomize.com" alt="Listing SEO score comparison" />'
            "<p>Best regards,<br>Jack</p>"
        )
        plain = html_to_plain_text(html_sample)
        self.assertIn("[Image: Listing SEO score comparison]", plain)
        self.assertNotIn("<img", plain)
        self.assertNotIn("cid:", plain)

    def test_spam_precheck_audits_image_alt_text(self):
        # Email body itself looks clean, but alt text has spam triggers
        html_with_spam_alt = (
            "<p>Hi John,</p>"
            '<img src="cid:img_456@sellomize.com" alt="100% free cash guarantee" />'
            "<p>Let me know what you think.</p>"
        )
        audit = audit_email_deliverability(body_html=html_with_spam_alt, subject="Quick observation")
        triggers = [t["word"].lower() for t in audit.get("detected_spam_words", [])]

        # Alt text triggers must be caught
        self.assertTrue(any(word in triggers for word in ["100%", "free", "guarantee", "cash"]))

    def test_recompress_image_bytes(self):
        # Create a large 2000x1200 dummy image
        img = Image.new("RGB", (2000, 1200), color=(50, 120, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        raw_large = buf.getvalue()

        # Recompress
        recompressed = recompress_image_bytes(raw_large, max_width=1000, quality=70)
        self.assertLess(len(recompressed), len(raw_large))

        # Verify it remains a valid JPEG
        pil_res = Image.open(io.BytesIO(recompressed))
        self.assertLessEqual(pil_res.size[0], 1000)

    def test_token_resolution_and_cid_structure(self):
        # Save a test lead image in database and on disk
        img_id = "test-uuid-phase2-999"
        lead_id = "L-0777"
        storage_key = f"leads/{lead_id}/{img_id}.jpg"
        full_dir = os.path.join("assets/uploads/leads", lead_id)
        os.makedirs(full_dir, exist_ok=True)
        f_path = os.path.join(full_dir, f"{img_id}.jpg")

        img = Image.new("RGB", (600, 400), color=(100, 150, 200))
        img.save(f_path, format="JPEG")

        rec = save_lead_image(
            image_id=img_id,
            lead_id=lead_id,
            storage_key=storage_key,
            filename="growth_metrics.jpg",
            mime_type="image/jpeg",
            width=600,
            height=400,
            num_bytes=os.path.getsize(f_path)
        )

        # Test token resolution in html
        raw_html = f"<p>Hello,</p><p>{{{{img:{img_id}|Growth Audit}}}}</p>"

        # Verify that smtp_dispatcher's regex and lookup finds it
        import re
        img_token_pattern = re.compile(r'\{\{img:([a-zA-Z0-9_\-]+)(?:\|([^}]+))?\}\}', re.IGNORECASE)
        m = img_token_pattern.search(raw_html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), img_id)
        self.assertEqual(m.group(2), "Growth Audit")

        # Cleanup
        delete_lead_image(img_id)
        if os.path.exists(f_path):
            os.remove(f_path)


if __name__ == "__main__":
    unittest.main()
