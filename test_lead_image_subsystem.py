"""
Unit tests for Sellomize Reach Image / Screenshot Subsystem (Phase 1).
"""

import os
import io
import unittest
from PIL import Image

from database import (
    save_lead_image,
    get_lead_images,
    get_image_by_id,
    update_image_filename,
    delete_lead_image,
)
from ui.rich_editor import process_and_store_image, ProcessedImage, MAX_IMAGE_WIDTH
from ui.compose import get_lead_identifier, insert_lead_image_into_editor


class TestLeadImageSubsystem(unittest.TestCase):

    def test_lead_identifier_resolution(self):
        # 1. With custom variables lead_code
        lead1 = {"id": 147, "name": "Sarah", "custom_variables": '{"lead_code": "L-0147"}'}
        self.assertEqual(get_lead_identifier(lead1), "L-0147")

        # 2. With ID only
        lead2 = {"id": 42, "name": "Alex", "email": "alex@brand.com"}
        self.assertEqual(get_lead_identifier(lead2), "L-0042")

        # 3. With Email only
        lead3 = {"id": None, "email": "founder@brand.co.uk"}
        self.assertEqual(get_lead_identifier(lead3), "founder_brand_co_uk")

        # 4. None / fallback
        self.assertEqual(get_lead_identifier(None), "general")

    def test_image_metadata_crud(self):
        img_id = "test-uuid-12345"
        lead_id = "L-0999"
        storage_key = f"leads/{lead_id}/{img_id}.jpg"

        # 1. Save
        rec = save_lead_image(
            image_id=img_id,
            lead_id=lead_id,
            storage_key=storage_key,
            filename="amazon_audit.jpg",
            mime_type="image/jpeg",
            width=1200,
            height=700,
            num_bytes=85000,
            created_by="tester"
        )
        self.assertEqual(rec["id"], img_id)
        self.assertEqual(rec["lead_id"], lead_id)

        # 2. Get by lead
        imgs = get_lead_images(lead_id)
        self.assertTrue(any(i["id"] == img_id for i in imgs))

        # 3. Get by ID
        single = get_image_by_id(img_id)
        self.assertIsNotNone(single)
        self.assertEqual(single["filename"], "amazon_audit.jpg")
        self.assertEqual(single["width"], 1200)

        # 4. Rename
        ok_rename = update_image_filename(img_id, "renamed_audit.jpg")
        self.assertTrue(ok_rename)
        updated = get_image_by_id(img_id)
        self.assertEqual(updated["filename"], "renamed_audit.jpg")

        # 5. Delete
        ok_del = delete_lead_image(img_id)
        self.assertTrue(ok_del)
        self.assertIsNone(get_image_by_id(img_id))

    def test_process_and_store_image_lead_path(self):
        # Create test image
        img = Image.new("RGB", (1600, 1000), color=(10, 80, 150))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw = buf.getvalue()

        lead_code = "L-0888"
        res = process_and_store_image(raw, mime_type="image/jpeg", lead_id=lead_code, original_filename="storefront.jpg")

        self.assertIsInstance(res, ProcessedImage)
        self.assertEqual(res.width, MAX_IMAGE_WIDTH)  # 1200px
        self.assertEqual(res.height, 750)  # Proportional downscaling
        self.assertIn(f"leads/{lead_code}/", res.filepath.replace("\\", "/"))
        self.assertTrue(os.path.exists(res.filepath))

        # Metadata was saved in DB
        db_imgs = get_lead_images(lead_code)
        self.assertTrue(any(i["id"] == res.image_id for i in db_imgs))

        # Cleanup
        delete_lead_image(res.image_id)
        if os.path.exists(res.filepath):
            os.remove(res.filepath)


if __name__ == "__main__":
    unittest.main()
