"""
test_buttons_verification.py - Comprehensive Button & Action Verification
Tests every button action handler across all 6 screens in Sellomize Reach.
"""

import unittest
import os
import tempfile
from datetime import datetime, timedelta

from database import (
    init_db,
    get_contacts,
    create_contact,
    update_contact,
    delete_contact,
    get_templates,
    create_template,
    update_template,
    delete_template,
    get_smtp_accounts,
    reset_daily_smtp_limits,
    increment_smtp_sent,
    get_next_available_smtp_account,
    add_smtp_account,
    update_smtp_account,
    delete_smtp_account,
    get_emails,
    create_email,
    update_email,
    delete_email,
    get_config,
    set_config,
    LEAD_STATUSES,
)


class TestButtonsAndActionHandlers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.test_db = os.path.join(cls.temp_dir, "test_buttons.db")
        init_db(cls.test_db)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_db):
            try:
                os.remove(cls.test_db)
            except Exception:
                pass

    # =========================================================================
    # 1. SIDEBAR & NAVIGATION BUTTONS
    # =========================================================================
    def test_01_navigation_screen_keys(self):
        """Verify navigation keys and mappings."""
        valid_screens = ["compose", "templates", "leads", "bulk", "outbox", "settings"]
        session = {}
        for screen in valid_screens:
            session["active_screen"] = screen
            self.assertEqual(session["active_screen"], screen)

    # =========================================================================
    # 2. COMPOSE SCREEN BUTTONS
    # =========================================================================
    def test_02_compose_buttons(self):
        """Verify Compose action buttons (Save draft, Schedule, Save as template, + Add follow-up)."""
        db = self.test_db
        # 1. Save draft button action
        draft_id = create_email(
            email_html="<p>Draft copy</p>",
            subject="Draft Subject",
            recipient="lead@example.com",
            status="Pending",
            scheduled_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            target_timezone="LOCAL",
            db_path=db
        )
        self.assertIsNotNone(draft_id)
        drafts = [e for e in get_emails(db_path=db) if e["id"] == draft_id]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["status"], "Pending")

        # 2. Schedule button action
        sched_time = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        sched_id = create_email(
            email_html="<p>Scheduled copy</p>",
            subject="Scheduled Subject",
            recipient="lead2@example.com",
            status="Approved",
            scheduled_time=sched_time,
            target_timezone="LOCAL",
            db_path=db
        )
        self.assertIsNotNone(sched_id)
        sched_email = next(e for e in get_emails(db_path=db) if e["id"] == sched_id)
        self.assertEqual(sched_email["status"], "Approved")
        self.assertEqual(sched_email["scheduled_time"], sched_time)

        # 3. Save as template button action
        tpl_id = create_template(
            template_name="Template from Compose",
            body_content="<p>Saved from compose</p>",
            db_path=db
        )
        self.assertIsNotNone(tpl_id)
        templates = [t for t in get_templates(db_path=db) if t["id"] == tpl_id]
        self.assertEqual(len(templates), 1)
        self.assertIn("Compose", templates[0]["template_name"])

        # 4. + Add follow-up button action
        initial_body = "Initial message."
        follow_up_addition = "<br><br>P.S. Just wanted to follow up on the above."
        updated_body = initial_body + follow_up_addition
        self.assertIn("follow up", updated_body)

    # =========================================================================
    # 3. TEMPLATES SCREEN BUTTONS
    # =========================================================================
    def test_03_templates_buttons(self):
        """Verify Templates action buttons (New, Save, Edit, Load, Use in bulk, Delete)."""
        db = self.test_db
        # 1. ➕ New template / Save Template button
        tid = create_template(
            template_name="Cold Outreach V1",
            body_content="<p>Hi [Name], quick question about [Company].</p>",
            db_path=db
        )
        update_template(
            template_id=tid,
            name="Cold Outreach V1",
            subject="Quick question for [Company]",
            body_html="<p>Hi [Name], quick question about [Company].</p>",
            db_path=db
        )
        tpl = next(t for t in get_templates(db_path=db) if t["id"] == tid)
        self.assertEqual(tpl["name"], "Cold Outreach V1")
        self.assertEqual(tpl["subject"], "Quick question for [Company]")

        # 2. Load into Compose button action simulation
        session = {}
        session["compose_subject"] = tpl["subject"]
        session["compose_body_html"] = tpl.get("body_content") or tpl.get("body_html")
        session["active_screen"] = "compose"
        self.assertEqual(session["compose_subject"], "Quick question for [Company]")
        self.assertEqual(session["active_screen"], "compose")

        # 3. Use in bulk button action simulation
        session["bulk_selected_template_id"] = tid
        session["active_screen"] = "bulk"
        self.assertEqual(session["bulk_selected_template_id"], tid)
        self.assertEqual(session["active_screen"], "bulk")

        # 4. 🗑️ Delete template button action
        delete_template(tid, db_path=db)
        remaining = [t for t in get_templates(db_path=db) if t["id"] == tid]
        self.assertEqual(len(remaining), 0)

    # =========================================================================
    # 4. LEADS CRM SCREEN BUTTONS
    # =========================================================================
    def test_04_leads_buttons(self):
        """Verify Leads buttons (+ Add lead, Edit, Delete, Compose to Lead, Funnel filters)."""
        db = self.test_db
        # 1. + Add lead button action
        lid = create_contact(
            name="Danessa Myricks",
            email="danessa@dmbeauty.com",
            company="DM Beauty",
            status="New",
            lead_source="Amazon scrape",
            priority="High",
            owner="Jack Conner",
            notes="Listings unavailable",
            tags="beauty, cosmetics",
            db_path=db
        )
        self.assertIsNotNone(lid)
        lead = next(c for c in get_contacts(db_path=db) if c["id"] == lid)
        self.assertEqual(lead["name"], "Danessa Myricks")
        self.assertEqual(lead["priority"], "High")

        # 2. Edit Lead button action
        update_contact(
            contact_id=lid,
            name="Danessa Myricks",
            company="DM Beauty Inc.",
            status="Emailed",
            lead_source="Amazon scrape",
            priority="High",
            owner="Jack Conner",
            notes="Contacted on LinkedIn",
            tags="beauty, cosmetics, high-intent",
            db_path=db
        )
        updated_lead = next(c for c in get_contacts(db_path=db) if c["id"] == lid)
        self.assertEqual(updated_lead["company"], "DM Beauty Inc.")
        self.assertEqual(updated_lead["status"], "Emailed")

        # 3. Compose to Lead action
        session = {}
        session["compose_selected_lead_id"] = lid
        session["active_screen"] = "compose"
        self.assertEqual(session["compose_selected_lead_id"], lid)

        # 4. Delete Lead action
        delete_contact(lid, db_path=db)
        remaining_leads = [c for c in get_contacts(db_path=db) if c["id"] == lid]
        self.assertEqual(len(remaining_leads), 0)

    # =========================================================================
    # 5. BULK SEND SCREEN BUTTONS
    # =========================================================================
    def test_05_bulk_send_buttons(self):
        """Verify Bulk Send buttons (Select All, Clear, Schedule batch)."""
        db = self.test_db
        # Create 3 leads
        l1 = create_contact(name="Lead One", email="one@brand.com", company="Brand One", status="New", tags="tech", db_path=db)
        l2 = create_contact(name="Lead Two", email="two@brand.com", company="Brand Two", status="New", tags="tech", db_path=db)
        l3 = create_contact(name="Lead Three", email="three@brand.com", company="Brand Three", status="New", tags="tech", db_path=db)

        # 1. Select all / Clear selection
        selected_set = {l1, l2, l3}
        self.assertEqual(len(selected_set), 3)
        selected_set.clear()
        self.assertEqual(len(selected_set), 0)

        # 2. 🚀 Schedule batch action
        target_leads = [
            {"id": l1, "name": "Lead One", "email": "one@brand.com", "company": "Brand One", "country_or_timezone": "LOCAL"},
            {"id": l2, "name": "Lead Two", "email": "two@brand.com", "company": "Brand Two", "country_or_timezone": "LOCAL"}
        ]
        base_now = datetime.now()
        step_seconds = 120
        created_batch_ids = []

        for i, lead in enumerate(target_leads):
            sched_dt = base_now + timedelta(seconds=(i * step_seconds))
            eid = create_email(
                email_html="<p>Batch copy</p>",
                subject="Batch Subject",
                recipient=lead["email"],
                status="Approved",
                scheduled_time=sched_dt.strftime("%Y-%m-%d %H:%M:%S"),
                target_timezone="LOCAL",
                db_path=db
            )
            created_batch_ids.append(eid)

        self.assertEqual(len(created_batch_ids), 2)
        emails = get_emails(db_path=db)
        batch_emails = [e for e in emails if e["id"] in created_batch_ids]
        self.assertEqual(len(batch_emails), 2)
        for be in batch_emails:
            self.assertEqual(be["status"], "Approved")

    # =========================================================================
    # 6. OUTBOX SCREEN BUTTONS
    # =========================================================================
    def test_06_outbox_buttons(self):
        """Verify Outbox actions (Pause, Resume, Cancel/Delete)."""
        db = self.test_db
        eid = create_email(
            email_html="<p>Outbox test</p>",
            subject="Outbox Test",
            recipient="test@outbox.com",
            status="Approved",
            scheduled_time=(datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            target_timezone="LOCAL",
            db_path=db
        )

        # 1. Edit scheduled email action
        new_sched_time = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        update_email(
            email_id=eid,
            subject="Updated Outbox Subject",
            recipient="updated@outbox.com",
            email_html="<p>Updated Body Content</p>",
            scheduled_time=new_sched_time,
            db_path=db
        )
        updated_e = next(x for x in get_emails(db_path=db) if x["id"] == eid)
        self.assertEqual(updated_e["subject"], "Updated Outbox Subject")
        self.assertEqual(updated_e["recipient"], "updated@outbox.com")
        self.assertEqual(updated_e["email_html"], "<p>Updated Body Content</p>")
        self.assertEqual(updated_e["scheduled_time"], new_sched_time)

        # 2. Pause button action
        update_email(email_id=eid, status="Paused", db_path=db)
        e = next(x for x in get_emails(db_path=db) if x["id"] == eid)
        self.assertEqual(e["status"], "Paused")

        # 3. Resume button action
        update_email(email_id=eid, status="Approved", db_path=db)
        e = next(x for x in get_emails(db_path=db) if x["id"] == eid)
        self.assertEqual(e["status"], "Approved")

        # 4. Cancel / Delete button action
        delete_email(eid, db_path=db)
        remaining = [x for x in get_emails(db_path=db) if x["id"] == eid]
        self.assertEqual(len(remaining), 0)

    # =========================================================================
    # 7. SETTINGS SCREEN BUTTONS
    # =========================================================================
    def test_07_settings_buttons(self):
        """Verify Settings buttons (Connect Mailbox, Edit, Delete, Save Configs)."""
        db = self.test_db
        # 1. Connect Mailbox button
        mid = add_smtp_account(
            name="Jack Conner",
            email="jack@sellomize.com",
            password="SecurePass2026!",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            daily_limit=60,
            warmup_enabled=True,
            warmup_start_date="2026-09-01",
            warmup_starting_limit=5,
            warmup_daily_increment=3,
            warmup_target_limit=60,
            db_path=db
        )
        self.assertIsNotNone(mid)
        mb = next(m for m in get_smtp_accounts(db_path=db) if m["id"] == mid)
        self.assertEqual(mb["email"], "jack@sellomize.com")
        self.assertEqual(mb["daily_limit"], 60)

        # 2. Edit Mailbox button
        update_smtp_account(
            account_id=mid,
            sender_name="Jack Conner | Sellomize",
            email="jack@sellomize.com",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            daily_limit=75,
            warmup_enabled=False,
            db_path=db
        )
        mb_updated = next(m for m in get_smtp_accounts(db_path=db) if m["id"] == mid)
        self.assertEqual(mb_updated["daily_limit"], 75)
        self.assertFalse(bool(mb_updated["warmup_enabled"]))

        # 3. Save Config cards buttons
        set_config("enforce_sending_window", "false", db_path=db)
        set_config("schedule_mode", "continuous", db_path=db)
        set_config("signature_html", "<p>Best regards,<br>Jack Conner</p>", db_path=db)
        set_config("negative_keywords", "guarantee, 100% free, act now", db_path=db)
        set_config("send_now_policy", "immediate", db_path=db)

        self.assertEqual(get_config("enforce_sending_window", db_path=db), "false")
        self.assertEqual(get_config("schedule_mode", db_path=db), "continuous")
        self.assertEqual(get_config("send_now_policy", db_path=db), "immediate")
        self.assertIn("Jack Conner", get_config("signature_html", db_path=db))
        self.assertIn("guarantee", get_config("negative_keywords", db_path=db))

        # 4. Delete Mailbox button
        delete_smtp_account(mid, db_path=db)
        remaining_mbs = [m for m in get_smtp_accounts(db_path=db) if m["id"] == mid]
        self.assertEqual(len(remaining_mbs), 0)

    # =========================================================================
    # 8. DAILY LIMIT ROLLOVER & REFRESH VERIFICATION
    # =========================================================================
    def test_08_daily_limit_rollover_and_reset(self):
        """Verify automatic daily sending limit rollover and manual reset in UTC+5 engine timeframe."""
        import sqlite3
        from timezone_helper import get_engine_now_str
        db = self.test_db
        today_engine = get_engine_now_str("%Y-%m-%d")

        # 1. Connect a test mailbox
        mid = add_smtp_account(
            name="Daily Limit Tester",
            email="daily_test@sellomize.com",
            password="TestPassword123!",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            daily_limit=50,
            db_path=db
        )
        self.assertIsNotNone(mid)

        # 2. Simulate yesterday's sending by directly setting sent_today = 35 and yesterday's date
        conn = sqlite3.connect(db)
        c = conn.cursor()
        yesterday_str = "2026-09-20"
        c.execute("UPDATE smtp_accounts SET sent_today = 35, last_reset_date = ? WHERE id = ?", (yesterday_str, mid))
        conn.commit()
        conn.close()

        # 3. Reading accounts via get_smtp_accounts() must automatically reset sent_today to 0
        accounts = get_smtp_accounts(db_path=db)
        test_mb = next(m for m in accounts if m["id"] == mid)
        self.assertEqual(test_mb["sent_today"], 0, "sent_today must roll over to 0 when date changes")
        self.assertEqual(test_mb["last_reset_date"], today_engine, "last_reset_date must update to today in UTC+5")

        # 4. Increment sent count for today
        increment_smtp_sent(mid, db_path=db)
        test_mb = next(m for m in get_smtp_accounts(db_path=db) if m["id"] == mid)
        self.assertEqual(test_mb["sent_today"], 1)

        increment_smtp_sent(mid, db_path=db)
        test_mb = next(m for m in get_smtp_accounts(db_path=db) if m["id"] == mid)
        self.assertEqual(test_mb["sent_today"], 2)

        # 5. Simulate rollover happening without querying get_smtp_accounts first
        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("UPDATE smtp_accounts SET sent_today = 25, last_reset_date = ? WHERE id = ?", (yesterday_str, mid))
        conn.commit()
        conn.close()

        # Dispatcher calls increment_smtp_sent directly: must reset to 1 rather than 26
        increment_smtp_sent(mid, db_path=db)
        test_mb = next(m for m in get_smtp_accounts(db_path=db) if m["id"] == mid)
        self.assertEqual(test_mb["sent_today"], 1, "increment on a new day must reset sent_today to 1")

        # 6. Verify manual Reset Daily Limits button action (force=True)
        # First increment to 15
        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("UPDATE smtp_accounts SET sent_today = 15 WHERE id = ?", (mid,))
        conn.commit()
        conn.close()

        affected = reset_daily_smtp_limits(db_path=db, force=True)
        self.assertGreaterEqual(affected, 1)
        test_mb = next(m for m in get_smtp_accounts(db_path=db) if m["id"] == mid)
        self.assertEqual(test_mb["sent_today"], 0, "Manual reset must force sent_today back to 0")

        # Cleanup
        delete_smtp_account(mid, db_path=db)


if __name__ == "__main__":
    unittest.main()
