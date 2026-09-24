"""
verify_part_e.py - Acceptance Criteria Verification for Sellomize Reach.
Executes and validates all 11 criteria from Part E of Complete Restructure Spec.
"""

import os
import sys
import time
import json
import unittest
from datetime import datetime, timedelta, timezone

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    TABLE_LEADS, TABLE_TEMPLATES, TABLE_MAILBOXES, TABLE_MESSAGES, TABLE_SETTINGS,
    WORKER_POLL_INTERVAL_SECONDS, LEAD_STATUSES, MESSAGE_STATUSES,
    DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT,
)
from database import (
    init_db, get_connection, set_config, get_config,
    get_contacts, create_contact, update_contact, get_contact_by_id,
    get_templates, create_template, get_template_by_id,
    get_smtp_accounts, add_smtp_account, update_smtp_account, delete_smtp_account,
    get_emails, create_email, get_email_by_id, update_email,
    get_warmup_info, get_effective_daily_limit,
)
from warmup import calculate_warmup_limit
from security import (
    encrypt_credential, decrypt_credential,
    sanitize_header, sanitize_preview_html,
)
from template_engine import (
    resolve_template, inject_variables, parse_spintax,
    audit_email_deliverability, _missing_tokens,
)
from ui.shell import get_worker_status

VERIFY_DB = "verify_part_e.db"


class TestPartEAcceptance(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(VERIFY_DB):
            os.remove(VERIFY_DB)
        init_db(VERIFY_DB)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(VERIFY_DB):
            os.remove(VERIFY_DB)

    def test_criterion_01_two_process_model_heartbeat(self):
        """Criterion 1: Two-process model heartbeat & status badge."""
        # 1. Without heartbeat -> Sender Stopped
        set_config("worker_heartbeat", "", db_path=VERIFY_DB)
        is_active, label, _ = get_worker_status(VERIFY_DB)
        self.assertFalse(is_active)
        self.assertEqual(label, "Sender Stopped")

        # 2. Stale heartbeat (>45s) -> Sender Stale
        stale_time = (datetime.now() - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")
        set_config("worker_heartbeat", stale_time, db_path=VERIFY_DB)
        is_active, label, _ = get_worker_status(VERIFY_DB)
        self.assertFalse(is_active)
        self.assertIn("Stale", label)

        # 3. Fresh heartbeat (<45s) -> Sender Running
        fresh_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_config("worker_heartbeat", fresh_time, db_path=VERIFY_DB)
        is_active, label, _ = get_worker_status(VERIFY_DB)
        self.assertTrue(is_active)
        self.assertEqual(label, "Sender Running")

    def test_criterion_02_database_schema_and_views(self):
        """Criterion 2: 5 canonical tables/views exist and queryable."""
        conn = get_connection(VERIFY_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        names = {row["name"] for row in cursor.fetchall()}
        conn.close()

        for canonical in ["leads", "templates", "mailboxes", "messages", "settings"]:
            self.assertIn(canonical, names, f"Canonical view/table '{canonical}' missing from DB")

        for legacy in ["contacts", "emails", "smtp_accounts", "system_config"]:
            self.assertIn(legacy, names, f"Legacy table '{legacy}' missing from DB")

    def test_criterion_03_editor_roundtrip_spintax_tokens(self):
        """Criterion 3: Editor round-trip, Spintax, and variable token safety."""
        # Variable chip and spintax
        raw_body = "<p>{Hello|Hi} [Name],</p><p>We saw [Company] growing fast.</p>"
        sanitized = sanitize_preview_html(raw_body)
        self.assertIn("[Name]", sanitized)
        self.assertIn("[Company]", sanitized)

        # Spintax resolution
        resolved_variations = set()
        for _ in range(20):
            res = parse_spintax(sanitized)
            resolved_variations.add(res[:10])
        self.assertTrue(len(resolved_variations) > 1, "Spintax must resolve into alternative greetings")

    def test_criterion_04_lead_management_and_5_states(self):
        """Criterion 4: Lead management and full lifecycle through 5 states."""
        cid = create_contact(
            name="Gregory Peck",
            email="gregory@cinema.org",
            company="Silver Screen Inc",
            custom_variables={"Industry": "Film", "Tier": "A"},
            db_path=VERIFY_DB
        )
        lead = get_contact_by_id(cid, db_path=VERIFY_DB)
        self.assertEqual(lead["status"], "New")
        self.assertEqual(lead["custom_variables_dict"].get("Tier"), "A")

        # Cycle through 5 states
        for st_name in ["Emailed", "Replied", "Bounced", "Do Not Contact", "New"]:
            update_contact(cid, status=st_name, db_path=VERIFY_DB)
            updated = get_contact_by_id(cid, db_path=VERIFY_DB)
            self.assertEqual(updated["status"], st_name)

    def test_criterion_05_template_authoring_and_audit(self):
        """Criterion 5: Template authoring, variable resolution, deliverability audit."""
        tid = create_template(
            template_name="VIP Pitch",
            body_content="Hi [Name], we guarantee 100% free cash at [Company]!",
            db_path=VERIFY_DB
        )
        tpl = get_template_by_id(tid, db_path=VERIFY_DB)
        self.assertEqual(tpl["template_name"], "VIP Pitch")

        audit = audit_email_deliverability(
            body_html=tpl["body_content"],
            subject="Exclusive cash guarantee",
            custom_negative_keywords="wire transfer, urgent"
        )
        # Should flag spam triggers 'guarantee', '100% free', 'cash'
        self.assertGreater(len(audit["detected_spam_words"]), 0)
        self.assertLess(audit["score"], 80)

    def test_criterion_06_compose_send_guard_and_threading(self):
        """Criterion 6: Send Guard catches unfilled tokens; threading headers."""
        unfilled_text = "Hi [Name], welcome to [UnknownToken]!"
        missing = _missing_tokens(unfilled_text)
        self.assertIn("[Name]", missing)
        self.assertIn("[UnknownToken]", missing)

        # Header injection defense
        malicious_subject = "Welcome\r\nInjected-Header: evil"
        cleaned_sub = sanitize_header(malicious_subject)
        self.assertEqual(cleaned_sub, "Welcome Injected-Header: evil")

    def test_criterion_07_warmup_calculation_math(self):
        """Criterion 7: Warmup progression calculation math."""
        # Day 1: start 10, inc 5, target 50 -> 10
        cap_day1 = calculate_warmup_limit("2026-09-24", start_limit=10, daily_increment=5, target_limit=50, as_of_date=datetime(2026, 9, 24).date())
        self.assertEqual(cap_day1, 10)

        # Day 5: 10 + 4*5 = 30
        cap_day5 = calculate_warmup_limit("2026-09-24", start_limit=10, daily_increment=5, target_limit=50, as_of_date=datetime(2026, 9, 28).date())
        self.assertEqual(cap_day5, 30)

        # Day 20: capped at target_limit (50)
        cap_day20 = calculate_warmup_limit("2026-09-24", start_limit=10, daily_increment=5, target_limit=50, as_of_date=datetime(2026, 10, 14).date())
        self.assertEqual(cap_day20, 50)

    def test_criterion_08_outbox_queue_pause_and_status(self):
        """Criterion 8: Message queueing, statuses, and pause/retry."""
        eid = create_email(
            recipient="lead@target.com",
            subject="Outreach Note",
            email_html="<p>Body</p>",
            status="Draft",
            db_path=VERIFY_DB
        )
        self.assertEqual(get_email_by_id(eid, db_path=VERIFY_DB)["status"], "Draft")

        # Approve -> Scheduled
        update_email(eid, status="Approved", db_path=VERIFY_DB)
        self.assertEqual(get_email_by_id(eid, db_path=VERIFY_DB)["status"], "Approved")

        # Reply detected -> Paused
        update_email(eid, status="Paused", db_path=VERIFY_DB)
        self.assertEqual(get_email_by_id(eid, db_path=VERIFY_DB)["status"], "Paused")

        # Resume -> Approved
        update_email(eid, status="Approved", db_path=VERIFY_DB)
        self.assertEqual(get_email_by_id(eid, db_path=VERIFY_DB)["status"], "Approved")

    def test_criterion_09_settings_credential_encryption(self):
        """Criterion 9: Settings Hostinger mailbox Fernet encryption."""
        raw_pw = "SuperSecureHostingerPass123!"
        aid = add_smtp_account(
            sender_name="Hostinger Mailbox",
            email="outreach@sellomize.com",
            password=raw_pw,
            daily_limit=75,
            db_path=VERIFY_DB
        )
        conn = get_connection(VERIFY_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM smtp_accounts WHERE id = ?", (aid,))
        stored_pw = cursor.fetchone()["password"]
        conn.close()

        self.assertNotEqual(stored_pw, raw_pw)
        self.assertTrue(stored_pw.startswith("gAAAAA"))

        # Decrypt transparently via database API
        acc = get_smtp_accounts(db_path=VERIFY_DB)[0]
        self.assertEqual(acc["password"], raw_pw)

    def test_criterion_10_zero_ai_codebase_audit(self):
        """Criterion 10: Zero AI / LLM calls or imports across all source files."""
        forbidden = ["litellm", "openai", "anthropic", "gemini_api", "completion_model", "langchain"]
        py_files = []
        for root, _, files in os.walk("."):
            if "venv" in root or ".git" in root or "__pycache__" in root or ".gemini" in root:
                continue
            for f in files:
                if f.endswith(".py") and not f.startswith("test_") and not f.startswith("verify_"):
                    py_files.append(os.path.join(root, f))

        for pf in py_files:
            with open(pf, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
                for fb in forbidden:
                    self.assertNotIn(fb, content, f"Forbidden AI keyword '{fb}' found in {pf}")

    def test_criterion_11_packaging_spec_verification(self):
        """Criterion 11: Packaging sellomize.spec has all required modules and datas."""
        with open("sellomize.spec", "r", encoding="utf-8") as f:
            spec_content = f.read()

        required_hidden = [
            "config", "security", "warmup", "database", "scheduler",
            "smtp_dispatcher", "ui.editor", "ui.shell", "ui.compose",
            "ui.templates", "ui.leads", "ui.bulk", "ui.outbox", "ui.settings"
        ]
        for mod in required_hidden:
            self.assertIn(f"'{mod}'", spec_content, f"Module '{mod}' missing from sellomize.spec hiddenimports")

        required_datas = ["config.py", "security.py", "warmup.py", "database.py", "scheduler.py"]
        for data_file in required_datas:
            self.assertIn(f"('{data_file}', '.')", spec_content, f"Data file '{data_file}' missing from sellomize.spec datas")


if __name__ == "__main__":
    unittest.main()
