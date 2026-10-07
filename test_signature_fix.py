import os
os.environ["SELLOMIZE_FORCE_SQLITE"] = "1"
import unittest
from datetime import datetime

from template_engine import (
    deduplicate_email_signature,
    has_signature_marker,
    count_signature_occurrences,
    strip_all_signatures,
)
from database import (
    init_db,
    create_email,
    get_email_by_id,
    sanitize_all_scheduled_signatures,
    set_config,
    add_smtp_account
)
from scheduler import dispatch_email_hostinger

TEST_DB = "test_sig_fix.db"

class TestSignatureDeduplicationSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass
        init_db(TEST_DB)
        cls.sample_sig = (
            '<div><table cellpadding="0" cellspacing="0">'
            '<tr><td><img src="https://sellomize.com/wp-content/uploads/2026/05/cropped-amazon-aligators.png" alt="Sellomize Logo"></td>'
            '<td><div>Jack Connor</div><div>Business Development Officer</div></td></tr>'
            '</table></div>'
        )
        set_config("signature_html", cls.sample_sig, db_path=TEST_DB)
        set_config("enforce_mx_check", "false", db_path=TEST_DB)
        add_smtp_account(
            sender_name="Jack Connor",
            email="jack@sellomize.com",
            password="mockpassword",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            daily_limit=50,
            db_path=TEST_DB
        )

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass

    def test_01_deduplicate_email_signature_unit(self):
        # Case 1: Double table signature
        body_double = f"<p>Hey Sarah,</p><p>We can help.</p><br><br>{self.sample_sig}<br><br>{self.sample_sig}"
        self.assertEqual(count_signature_occurrences(body_double), 2)
        cleaned = deduplicate_email_signature(body_double, self.sample_sig, include_signature=True)
        self.assertEqual(count_signature_occurrences(cleaned), 1)
        self.assertEqual(cleaned.count("Jack Connor"), 1)

        # Case 2: Zero signature, include_signature=True
        body_zero = "<p>Hey Sarah,</p><p>We can help.</p>"
        self.assertEqual(count_signature_occurrences(body_zero), 0)
        res = deduplicate_email_signature(body_zero, self.sample_sig, include_signature=True)
        self.assertEqual(count_signature_occurrences(res), 1)

        # Case 3: Follow-up without signature (include_signature=False)
        res_fu_no_sig = deduplicate_email_signature(body_double, self.sample_sig, include_signature=False)
        self.assertEqual(count_signature_occurrences(res_fu_no_sig), 0)
        self.assertNotIn("Jack Connor", res_fu_no_sig)
        self.assertIn("Hey Sarah", res_fu_no_sig)

        # Case 4: Follow-up with double signature, user wanted signature (include_signature=True)
        res_fu_sig = deduplicate_email_signature(body_double, self.sample_sig, include_signature=True)
        self.assertEqual(count_signature_occurrences(res_fu_sig), 1)

    def test_02_sanitize_all_scheduled_signatures_db(self):
        # 1. Initial email with double signature
        body_double = f"<p>Hi Jack,</p><p>First message.</p><br><br>{self.sample_sig}<br><br>{self.sample_sig}"
        e1_id = create_email(
            email_html=body_double,
            subject="Step 1 Intro",
            recipient="test1@example.com",
            status="Approved",
            scheduled_time="2026-10-10 10:00:00",
            sequence_step=1,
            db_path=TEST_DB
        )

        # 2. Follow-up email with double signature
        fu_double = f"<p>Hi Jack,</p><p>Following up.</p><br><br>{self.sample_sig}<br><br>{self.sample_sig}"
        e2_id = create_email(
            email_html=fu_double,
            subject="Step 2 Follow up",
            recipient="test2@example.com",
            status="Scheduled",
            scheduled_time="2026-10-14 10:00:00",
            sequence_step=2,
            db_path=TEST_DB
        )

        # 3. Follow up without any signature
        fu_clean = "<p>Quick check-in, did you see my last note?</p>"
        e3_id = create_email(
            email_html=fu_clean,
            subject="Step 3 Follow up",
            recipient="test3@example.com",
            status="Approved",
            scheduled_time="2026-10-18 10:00:00",
            sequence_step=3,
            db_path=TEST_DB
        )

        # Run database sanitizer
        res = sanitize_all_scheduled_signatures(db_path=TEST_DB)
        self.assertGreaterEqual(res["fixed_emails"], 2)

        # Verify e1 has exactly 1 signature
        e1_rec = get_email_by_id(e1_id, db_path=TEST_DB)
        self.assertEqual(count_signature_occurrences(e1_rec["email_html"]), 1)
        self.assertEqual(e1_rec["email_html"].count("Jack Connor"), 1)

        # Verify e2 has exactly 1 signature
        e2_rec = get_email_by_id(e2_id, db_path=TEST_DB)
        self.assertEqual(count_signature_occurrences(e2_rec["email_html"]), 1)
        self.assertEqual(e2_rec["email_html"].count("Jack Connor"), 1)

        # Verify e3 remains 0 signatures (follow up wasn't forced to have one)
        e3_rec = get_email_by_id(e3_id, db_path=TEST_DB)
        self.assertEqual(count_signature_occurrences(e3_rec["email_html"]), 0)

    def test_03_scheduler_dispatch_dry_run_no_duplicate(self):
        # Initial email that already has a signature
        body_with_sig = f"<p>Hello there</p><br><br>{self.sample_sig}"
        e_id = create_email(
            email_html=body_with_sig,
            subject="Testing dispatch",
            recipient="test4@example.com",
            status="Approved",
            scheduled_time="2026-10-10 10:00:00",
            sequence_step=1,
            db_path=TEST_DB
        )
        rec = get_email_by_id(e_id, db_path=TEST_DB)

        # Dispatch with dry_run
        ok = dispatch_email_hostinger(rec, dry_run=True, db_path=TEST_DB)
        self.assertTrue(ok)

if __name__ == "__main__":
    unittest.main()
