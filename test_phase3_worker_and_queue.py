"""
test_phase3_worker_and_queue.py - Test suite for Phase 3 components:
- send_jobs schema & CRUD
- Idempotency guard (prevents double creation and double sends)
- Atomic claim for sending
- Delivery worker execution cycle
- Sheets sync interface
"""

import os
import unittest
import tempfile
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from database import (
    init_db,
    create_send_job,
    get_send_job_by_id,
    get_due_send_jobs,
    claim_send_job_for_sending,
    mark_send_job_sent,
    mark_send_job_failed,
    get_send_jobs,
    save_lead_image,
)
from worker import execute_send_job, run_worker_cycle
from sheets_sync import is_sheets_sync_enabled, sync_lead_sent_to_sheets


class TestPhase3WorkerAndQueue(unittest.TestCase):
    def setUp(self):
        self.tmp_fd, self.tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)
        init_db(self.tmp_db)

    def tearDown(self):
        if os.path.exists(self.tmp_db):
            try:
                os.remove(self.tmp_db)
            except Exception:
                pass

    def test_send_job_creation_and_idempotency(self):
        """Test creating send_job and asserting idempotency key prevents duplicates."""
        job1 = create_send_job(
            lead_id="L-0147",
            to_addrs=["alice@example.com", "bob@example.com"],
            subject="Exclusive Amazon Audit",
            body_html="<p>Hello team {{img:test-uuid}}</p>",
            send_at="2026-10-07 10:00:00",
            image_ids=["test-uuid"],
            bcc_addrs="audit-bcc@sellomize.com",
            idempotency_key="L-0147:2026-10-07T10:00:00",
            db_path=self.tmp_db
        )
        self.assertEqual(job1["lead_id"], "L-0147")
        self.assertEqual(job1["status"], "scheduled")
        self.assertEqual(job1["to_addrs"], ["alice@example.com", "bob@example.com"])
        self.assertEqual(job1["bcc_addrs"], ["audit-bcc@sellomize.com"])

        # Attempt duplicate insertion with same idempotency_key
        job2 = create_send_job(
            lead_id="L-0147",
            to_addrs=["alice@example.com"],
            subject="Different subject",
            body_html="<p>Different body</p>",
            send_at="2026-10-07 10:00:00",
            idempotency_key="L-0147:2026-10-07T10:00:00",
            db_path=self.tmp_db
        )
        # Should return existing job without creating a new record
        self.assertEqual(job2["id"], job1["id"])
        self.assertEqual(job2["subject"], "Exclusive Amazon Audit")

        all_jobs = get_send_jobs(db_path=self.tmp_db)
        self.assertEqual(len(all_jobs), 1)

    def test_due_jobs_and_atomic_claim(self):
        """Test fetching due send_jobs and atomic claim transition to 'sending'."""
        past_time = datetime.now() - timedelta(minutes=5)
        future_time = datetime.now() + timedelta(hours=2)

        j_past = create_send_job(
            lead_id="L-PAST",
            to_addrs="past@example.com",
            subject="Past Due Outreach",
            body_html="<p>Past</p>",
            send_at=past_time,
            db_path=self.tmp_db
        )
        j_future = create_send_job(
            lead_id="L-FUT",
            to_addrs="future@example.com",
            subject="Future Outreach",
            body_html="<p>Future</p>",
            send_at=future_time,
            db_path=self.tmp_db
        )

        due = get_due_send_jobs(cutoff_dt=datetime.now(), db_path=self.tmp_db)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], j_past["id"])

        # First claim succeeds
        claimed = claim_send_job_for_sending(j_past["id"], db_path=self.tmp_db)
        self.assertTrue(claimed)

        # Second concurrent claim fails
        claimed_again = claim_send_job_for_sending(j_past["id"], db_path=self.tmp_db)
        self.assertFalse(claimed_again)

    def test_worker_execute_send_job_success(self):
        """Test worker execution flow with mock SMTP."""
        now = datetime.now() - timedelta(minutes=1)
        job = create_send_job(
            lead_id="L-TEST",
            to_addrs="client@example.com",
            subject="Listing Audit",
            body_html="<p>Here is your audit report.</p>",
            send_at=now,
            db_path=self.tmp_db
        )

        with patch("worker.send_smtp_email") as mock_send, \
             patch("worker.get_next_available_smtp_account") as mock_acc, \
             patch("worker.verify_email_domain_mx", return_value=(True, "MX records valid", None)):

            mock_acc.return_value = {
                "id": 1,
                "email": "outreach@sellomize.com",
                "sender_name": "Sellomize",
                "daily_limit": 50,
                "sent_today": 0
            }
            mock_send.return_value = (True, "Email queued and dispatched successfully")

            success = execute_send_job(job, dry_run=False, db_path=self.tmp_db)
            self.assertTrue(success)

            updated = get_send_job_by_id(job["id"], db_path=self.tmp_db)
            self.assertEqual(updated["status"], "sent")
            self.assertIsNotNone(updated["sent_at"])

    def test_worker_execute_send_job_bounce(self):
        """Test worker handles SMTP bounce responses appropriately."""
        now = datetime.now() - timedelta(minutes=1)
        job = create_send_job(
            lead_id="L-BOUNCE",
            to_addrs="deadbox@example.com",
            subject="Listing Audit",
            body_html="<p>Audit</p>",
            send_at=now,
            db_path=self.tmp_db
        )

        with patch("worker.send_smtp_email") as mock_send, \
             patch("worker.get_next_available_smtp_account") as mock_acc, \
             patch("worker.verify_email_domain_mx", return_value=(True, "MX records valid", None)):

            mock_acc.return_value = {
                "id": 1,
                "email": "outreach@sellomize.com",
                "sender_name": "Sellomize",
                "daily_limit": 50,
                "sent_today": 0
            }
            mock_send.return_value = (False, "550 5.1.1 User unknown")

            success = execute_send_job(job, dry_run=False, db_path=self.tmp_db)
            self.assertFalse(success)

            updated = get_send_job_by_id(job["id"], db_path=self.tmp_db)
            self.assertEqual(updated["status"], "bounced")
            self.assertIn("550", updated["last_error"])

    def test_sheets_sync_graceful_when_disabled(self):
        """Test sheets_sync returns False gracefully when not configured."""
        self.assertFalse(is_sheets_sync_enabled())
        res = sync_lead_sent_to_sheets("test@example.com")
        self.assertFalse(res)


if __name__ == "__main__":
    unittest.main()
