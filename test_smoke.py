"""
test_smoke.py - Behavioral smoke tests for Sellomize Reach.
Exercises end-to-end campaign generation, negative keyword guardrails,
hard vs soft bounce discrimination, bounded LRU MX cache + TTL,
CRLF header injection prevention, and sending window boundary logic.
"""

import os
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from database import (
    init_db,
    get_connection,
    set_config,
    get_config,
    create_contact,
    get_contact_by_id,
    get_contacts,
    create_template,
    create_email,
    get_email_by_id,
    get_approved_due_emails,
    generate_campaign_drafts,
    record_email_bounce,
    is_within_sending_window,
    add_smtp_account,
    get_smtp_accounts,
    get_smtp_account_by_id,
    update_smtp_account,
    validate_identifier,
    encrypt_smtp_password,
    decrypt_smtp_password
)
from smtp_dispatcher import sanitize_header, send_smtp_email
from template_engine import sanitize_email_html
from mx_checker import (
    verify_email_domain_mx,
    clear_mx_cache,
    get_cached_domain_count,
    _MX_CACHE,
    MAX_CACHE_SIZE,
    CACHE_TTL_SECONDS
)

SMOKE_TEST_DB = "test_smoke_reach.db"


class TestBehavioralSmokeSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(SMOKE_TEST_DB):
            os.remove(SMOKE_TEST_DB)
        init_db(SMOKE_TEST_DB)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(SMOKE_TEST_DB):
            os.remove(SMOKE_TEST_DB)

    def setUp(self):
        conn = get_connection(SMOKE_TEST_DB)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM emails")
        cursor.execute("DELETE FROM contacts")
        cursor.execute("DELETE FROM templates")
        conn.commit()
        conn.close()
        clear_mx_cache()

    def test_01_campaign_generation_end_to_end(self):
        """
        1. End-to-end campaign generation:
        Filter N contacts -> generate sequence with spintax + variables ->
        assert N drafts created with correct per-recipient variables,
        HTML paragraph formatting (behavioral), and staggered send times.
        """
        # Create 3 contacts with custom variables
        c1_id = create_contact(
            name="Alice Walker",
            email="alice@apexbrand.com",
            company="Apex Apparel",
            custom_variables={"Role": "VP of Growth", "Category": "Athleisure"},
            db_path=SMOKE_TEST_DB
        )
        c2_id = create_contact(
            name="Bob Martinez",
            email="bob@zenithtech.io",
            company="Zenith Labs",
            custom_variables={"Role": "Chief Executive Officer", "Category": "SaaS Platform"},
            db_path=SMOKE_TEST_DB
        )
        c3_id = create_contact(
            name="Chloe Dubois",
            email="chloe@solaris.fr",
            company="Solaris Clean",
            custom_variables={"Role": "Head of Partnerships", "Category": "Clean Tech"},
            db_path=SMOKE_TEST_DB
        )

        template_id = create_template(
            template_name="Cold Introduction",
            body_content=(
                "{Hi|Hello} [Name],\n\n"
                "I noticed your work as [Role] at [Company] in the [Category] sector.\n\n"
                "Would you be open to a 5-minute chat?"
            ),
            db_path=SMOKE_TEST_DB
        )

        base_dt = datetime(2026, 9, 21, 10, 0, 0)  # A Monday at 10:00 AM
        res = generate_campaign_drafts(
            contact_ids=[c1_id, c2_id, c3_id],
            template_id=template_id,
            subject_template="{Quick question|Brief inquiry} for [Company]",
            sending_days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            start_time_str="09:00",
            end_time_str="18:00",
            spacing_minutes=15,
            auto_stagger=True,
            base_start_dt=base_dt,
            db_path=SMOKE_TEST_DB
        )

        # Assert exactly 3 drafts created
        self.assertEqual(res["created_count"], 3)
        self.assertEqual(len(res["email_ids"]), 3)

        emails = [get_email_by_id(eid, db_path=SMOKE_TEST_DB) for eid in res["email_ids"]]

        # Contact 1 assertions
        e1 = emails[0]
        self.assertEqual(e1["recipient"], "alice@apexbrand.com")
        self.assertTrue(
            e1["subject"].startswith("Quick question for Apex Apparel") or
            e1["subject"].startswith("Brief inquiry for Apex Apparel")
        )
        self.assertNotIn("[Name]", e1["email_html"])
        self.assertNotIn("[Company]", e1["email_html"])
        self.assertNotIn("[Role]", e1["email_html"])
        self.assertIn("Alice Walker", e1["email_html"])
        self.assertIn("Apex Apparel", e1["email_html"])
        self.assertIn("VP of Growth", e1["email_html"])
        # Behavioral HTML paragraph check (Amendment 3: assert behavior, not exact style strings)
        self.assertIn("<p", e1["email_html"])
        self.assertIn("</p>", e1["email_html"])
        self.assertEqual(e1["email_html"].count("<p"), 3)

        # Contact 2 assertions
        e2 = emails[1]
        self.assertEqual(e2["recipient"], "bob@zenithtech.io")
        self.assertIn("Bob Martinez", e2["email_html"])
        self.assertIn("Zenith Labs", e2["email_html"])
        self.assertIn("Chief Executive Officer", e2["email_html"])
        self.assertNotIn("[Name]", e2["email_html"])
        self.assertNotIn("[Company]", e2["email_html"])

        # Contact 3
        e3 = emails[2]
        self.assertEqual(e3["recipient"], "chloe@solaris.fr")
        self.assertIn("Chloe Dubois", e3["email_html"])
        self.assertIn("Solaris Clean", e3["email_html"])

        # Staggered send times assertion: e1 < e2 < e3 by 15 minutes
        t1 = datetime.strptime(e1["scheduled_time"], "%Y-%m-%d %H:%M:%S")
        t2 = datetime.strptime(e2["scheduled_time"], "%Y-%m-%d %H:%M:%S")
        t3 = datetime.strptime(e3["scheduled_time"], "%Y-%m-%d %H:%M:%S")
        self.assertEqual(t2 - t1, timedelta(minutes=15))
        self.assertEqual(t3 - t2, timedelta(minutes=15))

    def test_02_negative_keyword_guardrail(self):
        """
        2. Negative-keyword guardrail:
        Draft containing a banned word -> status becomes 'Flagged' and
        it is strictly excluded from approval / dispatch queues.
        """
        set_config("negative_keywords", "wire transfer, guaranteed profit, secret loophole", db_path=SMOKE_TEST_DB)

        cid = create_contact(
            name="Daniel Craig",
            email="daniel@investments.co",
            company="Craig Capital",
            db_path=SMOKE_TEST_DB
        )

        template_id = create_template(
            template_name="High Risk Offer",
            body_content="Hello [Name],\n\nPlease send funds via wire transfer for guaranteed profit.",
            db_path=SMOKE_TEST_DB
        )

        res = generate_campaign_drafts(
            contact_ids=[cid],
            template_id=template_id,
            subject_template="Investment for [Company]",
            base_start_dt=datetime(2026, 9, 21, 10, 0, 0),
            db_path=SMOKE_TEST_DB
        )

        self.assertEqual(res["flagged_count"], 1)
        self.assertEqual(res["pending_count"], 0)

        flagged_email = get_email_by_id(res["email_ids"][0], db_path=SMOKE_TEST_DB)
        self.assertEqual(flagged_email["status"], "Flagged")
        flag_note = flagged_email.get("revision_notes") or flagged_email.get("flag_reason")
        self.assertIsNotNone(flag_note)
        self.assertIn("wire transfer", flag_note.lower())

        # Assert get_approved_due_emails strictly excludes Flagged drafts
        due_emails = get_approved_due_emails(db_path=SMOKE_TEST_DB)
        due_ids = [e["id"] for e in due_emails]
        self.assertNotIn(flagged_email["id"], due_ids)

    def test_03_hard_vs_soft_bounce_discrimination(self):
        """
        3. Hard vs. soft bounce discrimination:
        - 452 (Soft bounce: Mailbox full) -> flagged for retry, NOT Do Not Contact.
          Amendment 4: Contact status is unchanged from immediately before the bounce,
          status != 'Do Not Contact', status != 'Bounced'.
        - 550 (Hard bounce: User unknown) -> contact quarantined as 'Bounced'.
        """
        # --- PART A: Soft Bounce (452) ---
        c_soft_id = create_contact(
            name="Sam Soft",
            email="sam@activecorp.com",
            company="Active Corp",
            status="Contacted",
            notes="Initial outreach sent on Tuesday.",
            db_path=SMOKE_TEST_DB
        )
        e_soft_id = create_email(
            recipient="sam@activecorp.com",
            subject="Follow-up",
            email_html="<p>Checking in</p>",
            status="Approved",
            db_path=SMOKE_TEST_DB
        )

        # Simulate SMTP 452 bounce
        record_email_bounce(
            recipient_email="sam@activecorp.com",
            bounce_reason="452 4.2.2 Mailbox full; storage quota exceeded",
            smtp_code=452,
            db_path=SMOKE_TEST_DB
        )

        c_soft_after = get_contact_by_id(c_soft_id, db_path=SMOKE_TEST_DB)
        e_soft_after = get_email_by_id(e_soft_id, db_path=SMOKE_TEST_DB)

        # Email is flagged for retry
        self.assertEqual(e_soft_after["status"], "Flagged")
        self.assertEqual(e_soft_after["is_bounced"], 0)

        # Amendment 4 explicit negative assertions:
        # 1. Status is UNCHANGED from what it was immediately before bounce ("Contacted")
        self.assertEqual(c_soft_after["status"], "Contacted")
        # 2. Status is NOT "Do Not Contact"
        self.assertNotEqual(c_soft_after["status"], "Do Not Contact")
        # 3. Status is NOT "Bounced"
        self.assertNotEqual(c_soft_after["status"], "Bounced")
        self.assertNotIn("Bounced", c_soft_after["tags_list"])
        # Audit note logged in contact notes
        self.assertIn("[Soft Bounce:", c_soft_after["notes"])

        # --- PART B: Hard Bounce (550) ---
        c_hard_id = create_contact(
            name="Harry Hard",
            email="harry@deadcompany.com",
            company="Dead Co",
            status="Contacted",
            db_path=SMOKE_TEST_DB
        )
        e_hard_id = create_email(
            recipient="harry@deadcompany.com",
            subject="Intro",
            email_html="<p>Intro</p>",
            status="Approved",
            db_path=SMOKE_TEST_DB
        )

        # Simulate SMTP 550 bounce
        record_email_bounce(
            recipient_email="harry@deadcompany.com",
            bounce_reason="550 5.1.1 User unknown; address does not exist",
            smtp_code=550,
            db_path=SMOKE_TEST_DB
        )

        c_hard_after = get_contact_by_id(c_hard_id, db_path=SMOKE_TEST_DB)
        e_hard_after = get_email_by_id(e_hard_id, db_path=SMOKE_TEST_DB)

        # Hard bounce quarantines lead and marks email as Bounced
        self.assertEqual(e_hard_after["status"], "Bounced")
        self.assertEqual(e_hard_after["is_bounced"], 1)
        self.assertEqual(c_hard_after["status"], "Bounced")
        self.assertIn("Bounced", c_hard_after["tags_list"])
        self.assertIn("[Bounced:", c_hard_after["notes"])

    @patch("dns.resolver.Resolver.resolve")
    def test_04_mx_lru_cache_and_ttl(self, mock_resolve):
        """
        4. LRU cache + TTL:
        - Exceed 1000 entries -> oldest entries evicted, cache size capped at 1000.
        - Expired entry (>24h) -> cache miss + fresh resolve.
        """
        import dns.resolver
        r_mock = MagicMock()
        r_mock.exchange.to_text.return_value = "mail.mock.com."
        mock_resolve.return_value = [r_mock]

        clear_mx_cache()
        self.assertEqual(get_cached_domain_count(), 0)

        # 1. Fill cache beyond MAX_CACHE_SIZE (1000)
        # Insert 1005 distinct domains
        for i in range(1005):
            verify_email_domain_mx(f"user{i}@domain{i}.com")

        # Cache size must strictly be capped at 1000
        self.assertEqual(get_cached_domain_count(), 1000)

        # Oldest 5 entries (domain0.com through domain4.com) must be evicted
        for i in range(5):
            self.assertNotIn(f"domain{i}.com", _MX_CACHE)

        # Recent entries (domain5.com through domain1004.com) must be present
        self.assertIn("domain5.com", _MX_CACHE)
        self.assertIn("domain1004.com", _MX_CACHE)

        # 2. TTL expiration test
        # Inject an entry that is 25 hours old (expired > 24h)
        expired_domain = "old-domain.com"
        expired_ts = time.time() - (25 * 3600)
        _MX_CACHE[expired_domain] = ((True, "Cached MX", ["mail.old.com"]), expired_ts)

        # Reset call count
        mock_resolve.reset_mock()

        # Query expired domain -> must trigger fresh DNS lookup (cache miss due to TTL)
        is_val, reason, recs = verify_email_domain_mx(f"info@{expired_domain}")
        self.assertTrue(is_val)
        mock_resolve.assert_called()

        # Timestamp in cache should now be fresh
        _, new_ts = _MX_CACHE[expired_domain]
        self.assertGreater(new_ts, time.time() - 5)

    def test_05_header_injection_prevention(self):
        """
        5. Email header injection (CRLF) prevention:
        Subject 'Hi\r\nBcc: evil@x.com' -> newlines and control characters stripped,
        preventing injected headers in SMTP MIME envelopes.
        """
        # Test unit sanitizer
        malicious_subject = "Hi\r\nBcc: evil@attacker.com\nAnotherHeader: Injected"
        cleaned_subject = sanitize_header(malicious_subject)
        self.assertNotIn("\r", cleaned_subject)
        self.assertNotIn("\n", cleaned_subject)
        self.assertNotIn("\x00", cleaned_subject)
        self.assertEqual(cleaned_subject, "Hi Bcc: evil@attacker.com AnotherHeader: Injected")

        malicious_recipient = "target@victim.com\r\nBcc: spy@evil.com"
        cleaned_recipient = sanitize_header(malicious_recipient)
        self.assertNotIn("\r", cleaned_recipient)
        self.assertNotIn("\n", cleaned_recipient)

        # Test within send_smtp_email construction (using mock SMTP)
        account = {
            "smtp_host": "smtp.mock.com",
            "smtp_port": 465,
            "email": "outreach@agency.com",
            "password": "pass",
            "sender_name": "Agent\r\nFrom: spoofed@agency.com"
        }

        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_server = MagicMock()
            mock_smtp_ssl.return_value.__enter__.return_value = mock_server

            success, msg = send_smtp_email(
                smtp_account=account,
                recipient="client@prospective.com\r\nCc: leaked@spy.com",
                subject="Proposition\r\nBcc: dark@attacker.com",
                html_content="<p>Message body</p>",
                bcc_email="validbcc@agency.com\r\nInjected: True"
            )
            self.assertTrue(success)

            # Inspect the MIME message passed to send_message
            call_args = mock_server.send_message.call_args
            sent_msg = call_args[0][0]

            # Verify headers have zero CRLF characters
            for header_key, header_val in sent_msg.items():
                self.assertNotIn("\r", str(header_val), f"CR detected in header {header_key}: {header_val}")
                self.assertNotIn("\n", str(header_val), f"LF detected in header {header_key}: {header_val}")

    def test_06_overnight_sending_window_and_boundaries(self):
        """
        6. Overnight sending window and exact boundary testing (Amendment 1):
        Spec Decision: Window is start-inclusive and end-exclusive [start, end).
        For overnight window 21:00 - 05:00 on weekdays:
        - 21:00 (start boundary) -> inside (True)
        - 20:59 (1 min before start) -> outside (False)
        - 00:00 (midnight seam) -> inside (True)
        - 04:59 (1 min before end) -> inside (True)
        - 05:00 (end boundary, end-exclusive cutoff) -> outside (False)
        - 12:00 (midday) -> outside (False)

        For normal window 09:00 - 18:00:
        - 09:00 (start boundary) -> inside (True)
        - 08:59 (1 min before start) -> outside (False)
        - 17:59 (1 min before end) -> inside (True)
        - 18:00 (end boundary, end-exclusive cutoff) -> outside (False)
        """
        set_config("enforce_sending_window", "true", db_path=SMOKE_TEST_DB)
        set_config("sending_days", "Monday,Tuesday,Wednesday,Thursday,Friday", db_path=SMOKE_TEST_DB)

        # --- PART A: Overnight Window (21:00 to 05:00) ---
        set_config("sending_start_time", "21:00", db_path=SMOKE_TEST_DB)
        set_config("sending_end_time", "05:00", db_path=SMOKE_TEST_DB)

        monday_date = datetime(2026, 9, 21).date()  # Monday

        # 21:00 -> inside (start boundary)
        dt_2100 = datetime.combine(monday_date, datetime.min.time().replace(hour=21, minute=0)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_2100, db_path=SMOKE_TEST_DB)
        self.assertTrue(inside, "21:00 must be inside overnight window 21:00-05:00")

        # 20:59 -> outside (1 min before start)
        dt_2059 = datetime.combine(monday_date, datetime.min.time().replace(hour=20, minute=59)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_2059, db_path=SMOKE_TEST_DB)
        self.assertFalse(inside, "20:59 must be outside overnight window 21:00-05:00")

        # 00:00 -> inside (midnight seam)
        dt_0000 = datetime.combine(monday_date, datetime.min.time().replace(hour=0, minute=0)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_0000, db_path=SMOKE_TEST_DB)
        self.assertTrue(inside, "00:00 must be inside overnight window 21:00-05:00 (midnight seam)")

        # 04:59 -> inside (1 min before end)
        dt_0459 = datetime.combine(monday_date, datetime.min.time().replace(hour=4, minute=59)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_0459, db_path=SMOKE_TEST_DB)
        self.assertTrue(inside, "04:59 must be inside overnight window 21:00-05:00")

        # 05:00 -> outside (end cutoff boundary: end-exclusive)
        dt_0500 = datetime.combine(monday_date, datetime.min.time().replace(hour=5, minute=0)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_0500, db_path=SMOKE_TEST_DB)
        self.assertFalse(inside, "05:00 must be outside overnight window (end-exclusive cutoff)")

        # 12:00 -> outside (midday)
        dt_1200 = datetime.combine(monday_date, datetime.min.time().replace(hour=12, minute=0)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_1200, db_path=SMOKE_TEST_DB)
        self.assertFalse(inside, "12:00 noon must be outside overnight window 21:00-05:00")

        # --- PART B: Normal Window (09:00 to 18:00) ---
        set_config("sending_start_time", "09:00", db_path=SMOKE_TEST_DB)
        set_config("sending_end_time", "18:00", db_path=SMOKE_TEST_DB)

        # 09:00 -> inside (start boundary)
        dt_0900 = datetime.combine(monday_date, datetime.min.time().replace(hour=9, minute=0)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_0900, db_path=SMOKE_TEST_DB)
        self.assertTrue(inside, "09:00 must be inside normal window 09:00-18:00")

        # 08:59 -> outside (1 min before start)
        dt_0859 = datetime.combine(monday_date, datetime.min.time().replace(hour=8, minute=59)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_0859, db_path=SMOKE_TEST_DB)
        self.assertFalse(inside, "08:59 must be outside normal window 09:00-18:00")

        # 17:59 -> inside (1 min before cutoff)
        dt_1759 = datetime.combine(monday_date, datetime.min.time().replace(hour=17, minute=59)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_1759, db_path=SMOKE_TEST_DB)
        self.assertTrue(inside, "17:59 must be inside normal window 09:00-18:00")

        # 18:00 -> outside (end cutoff boundary: end-exclusive)
        dt_1800 = datetime.combine(monday_date, datetime.min.time().replace(hour=18, minute=0)).astimezone()
        inside, _ = is_within_sending_window(check_dt=dt_1800, db_path=SMOKE_TEST_DB)
        self.assertFalse(inside, "18:00 must be outside normal window (end-exclusive cutoff)")

    def test_07_smtp_password_encryption_and_undecryptable_fallback(self):
        """
        Part 1.1: Verify real Fernet encryption in SQLite, transparent retrieval decryption,
        legacy plaintext compatibility, and graceful undecryptable handling without crashes.
        """
        raw_pass = "P@ssw0rd_Super_Secret_99#"
        acc_id = add_smtp_account(
            sender_name="Security Tester",
            email="secops@reachagency.com",
            password=raw_pass,
            daily_limit=50,
            db_path=SMOKE_TEST_DB
        )

        # 1. Inspect raw SQLite database row: MUST be encrypted ciphertext, NOT plaintext
        conn = get_connection(SMOKE_TEST_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM smtp_accounts WHERE id = ?", (acc_id,))
        raw_stored = cursor.fetchone()["password"]
        conn.close()

        self.assertNotEqual(raw_stored, raw_pass, "Password stored in SQLite must NOT be plaintext")
        self.assertTrue(raw_stored.startswith("gAAAAA"), f"Password stored in SQLite must be a valid Fernet token starting with 'gAAAAA', got: {raw_stored[:10]}")

        # 2. Transparent retrieval via API: MUST return decrypted plaintext
        acc = get_smtp_account_by_id(acc_id, db_path=SMOKE_TEST_DB)
        self.assertIsNotNone(acc)
        self.assertEqual(acc["password"], raw_pass, "get_smtp_account_by_id must return decrypted password")
        self.assertFalse(acc.get("password_undecryptable", False), "Valid password must not be flagged undecryptable")

        # 3. Legacy unencrypted plaintext record in existing DB: MUST be readable without crashing
        conn = get_connection(SMOKE_TEST_DB)
        cursor = conn.cursor()
        legacy_pass = "old_unencrypted_plaintext_pwd"
        cursor.execute("UPDATE smtp_accounts SET password = ? WHERE id = ?", (legacy_pass, acc_id))
        conn.commit()
        conn.close()

        legacy_acc = get_smtp_account_by_id(acc_id, db_path=SMOKE_TEST_DB)
        self.assertEqual(legacy_acc["password"], legacy_pass, "Legacy plaintext password must be returned transparently")
        self.assertFalse(legacy_acc.get("password_undecryptable", False))

        # 4. Undecryptable / foreign token (e.g. DB moved across machines): MUST NOT CRASH
        conn = get_connection(SMOKE_TEST_DB)
        cursor = conn.cursor()
        corrupted_token = "gAAAAABforeignAlienKeyTokenCorrupted1234567890abcdefghijklmnopqrstuvwxyz=="
        cursor.execute("UPDATE smtp_accounts SET password = ? WHERE id = ?", (corrupted_token, acc_id))
        conn.commit()
        conn.close()

        undec_acc = get_smtp_account_by_id(acc_id, db_path=SMOKE_TEST_DB)
        self.assertIsNotNone(undec_acc)
        self.assertEqual(undec_acc["password"], "", "Undecryptable account password must default to empty string")
        self.assertTrue(undec_acc.get("password_undecryptable"), "Undecryptable account must have password_undecryptable flag set for UI re-entry prompt")

    def test_08_html_sanitizer_and_xss_prevention(self):
        """
        Part 1.2: Verify nh3 allowlist sanitization disarms script injection, onerror handlers,
        and javascript: URIs while preserving legitimate email styling and structure.
        """
        malicious_input = (
            "<p>Dear [Name],</p>"
            "<script>alert('xss_attack_vector');</script>"
            "<img src='https://assets.sellomize.com/logo.png' onerror='malicious_code()' width='150'>"
            "<a href='javascript:void(0)'>Click here for offer</a>"
            "<a href='https://sellomize.com/pricing'>Real Link</a>"
            "<table><tr><td><strong>Audit Score: 98/100</strong></td></tr></table>"
        )

        sanitized = sanitize_email_html(malicious_input)

        # Active exploits must be completely removed
        self.assertNotIn("<script>", sanitized, "Sanitizer must strip <script> tags")
        self.assertNotIn("xss_attack_vector", sanitized, "Sanitizer must strip script body contents")
        self.assertNotIn("onerror", sanitized, "Sanitizer must strip dangerous event handlers like onerror")
        self.assertNotIn("javascript:", sanitized, "Sanitizer must disarm javascript: URL schemes")

        # Legitimate HTML email elements must be preserved
        self.assertIn("<p>Dear [Name],</p>", sanitized, "Sanitizer must preserve safe paragraphs")
        self.assertIn("<strong>Audit Score: 98/100</strong>", sanitized, "Sanitizer must preserve strong formatting")
        self.assertIn("<table>", sanitized, "Sanitizer must preserve table layouts")
        self.assertIn("href=\"https://sellomize.com/pricing\"", sanitized, "Sanitizer must preserve safe https:// links")
        self.assertIn("src=\"https://assets.sellomize.com/logo.png\"", sanitized, "Sanitizer must preserve safe image sources")

    def test_09_sql_identifier_validation(self):
        """
        Part 1.4: Verify SQL migration column identifier validation strictly enforces ^[a-zA-Z0-9_]+$
        to prevent dynamic DDL injection.
        """
        # Valid column identifiers
        self.assertEqual(validate_identifier("lead_source"), "lead_source")
        self.assertEqual(validate_identifier("WarmupDay2"), "WarmupDay2")
        self.assertEqual(validate_identifier("custom_var_123"), "custom_var_123")

        # Malicious / invalid identifiers that must raise ValueError
        invalid_identifiers = [
            "col; DROP TABLE contacts; --",
            "first name",
            "col' OR '1'='1",
            "tags--",
            "col-dash",
            "col$dollar",
            "<script>",
            "",
            "   "
        ]
        for bad_id in invalid_identifiers:
            with self.assertRaises(ValueError, msg=f"validate_identifier must reject '{bad_id}'"):
                validate_identifier(bad_id)

    def test_10_header_injection_prevention_across_all_fields(self):
        """
        Part 1.3: Verify sanitize_header strips carriage returns, newlines, and null bytes across
        Subject, Recipient, Sender, and BCC fields to prevent SMTP header smuggling.
        """
        # 1. Subject injection with CRLF Bcc injection
        raw_subject = "Exclusive Agency Audit\r\nBcc: attacker@infiltrator.com"
        clean_sub = sanitize_header(raw_subject)
        self.assertNotIn("\r", clean_sub)
        self.assertNotIn("\n", clean_sub)
        self.assertEqual(clean_sub, "Exclusive Agency Audit Bcc: attacker@infiltrator.com")

        # 2. Recipient injection with CRLF Cc
        raw_recipient = "ceo@targetcompany.com\r\nCc: spy@competitor.com"
        clean_rcpt = sanitize_header(raw_recipient)
        self.assertNotIn("\r", clean_rcpt)
        self.assertNotIn("\n", clean_rcpt)
        self.assertEqual(clean_rcpt, "ceo@targetcompany.com Cc: spy@competitor.com")

        # 3. Sender name injection
        raw_sender = "Alex Morgan\r\nReply-To: phishing@spoof.com"
        clean_sender = sanitize_header(raw_sender)
        self.assertNotIn("\r", clean_sender)
        self.assertNotIn("\n", clean_sender)
        self.assertEqual(clean_sender, "Alex Morgan Reply-To: phishing@spoof.com")

        # 4. Null byte injection
        raw_bcc = "archive@sellomize.com\x00hidden@domain.com"
        clean_bcc = sanitize_header(raw_bcc)
        self.assertNotIn("\x00", clean_bcc)
        self.assertEqual(clean_bcc, "archive@sellomize.com hidden@domain.com")


if __name__ == "__main__":
    unittest.main()

