"""
test_system.py - Verification suite for local email automation system.
Tests database persistence, CRM contacts, Spintax & variable templates,
negative keyword scanner, and scheduler polling workflows.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from database import (
    init_db,
    get_config,
    set_config,
    save_all_configs,
    create_contact,
    get_contacts,
    get_contact_by_id,
    delete_contact,
    upsert_contact_by_email,
    get_all_distinct_tags,
    create_template,
    get_templates,
    get_template_by_id,
    delete_template,
    create_email,
    get_emails,
    get_email_by_id,
    update_email,
    approve_email,
    flag_email,
    get_approved_due_emails,
    mark_email_sent,
    mark_email_error,
    delete_email,
    add_smtp_account,
    get_smtp_accounts,
    get_smtp_account_by_id,
    update_smtp_account,
    delete_smtp_account,
    get_next_available_smtp_account,
    increment_smtp_sent,
    get_predefined_tags,
    bulk_add_tags_to_contacts,
    bulk_remove_tags_from_contacts,
    bulk_set_tags_for_contacts,
    bulk_update_contacts_details,
    bulk_delete_contacts
)
from smtp_dispatcher import (
    test_smtp_connection,
    send_smtp_email,
    html_to_plain_text
)
from contacts_handler import (
    generate_csv_template,
    export_contacts_to_csv,
    import_contacts_from_csv
)
from llm_engine import (
    _build_spam_instruction,
    _parse_variations_json,
    _parse_single_email_json,
    inject_variables,
    parse_spintax,
    scan_negative_keywords
)
from scheduler import (
    run_scheduler_cycle,
    dispatch_email_hostinger,
    dispatch_email_outlook,
    dispatch_email
)

TEST_DB = "test_email_system.db"

class TestEmailAutomationSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        init_db(TEST_DB)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    def test_01_configuration_crud(self):
        """Verify configuration reading, writing, and defaults."""
        set_config("primary_model", "gemini/gemini-1.5-pro", db_path=TEST_DB)
        val = get_config("primary_model", db_path=TEST_DB)
        self.assertEqual(val, "gemini/gemini-1.5-pro")

        set_config("negative_keywords", "guarantee, winner, risk-free", db_path=TEST_DB)
        self.assertEqual(get_config("negative_keywords", db_path=TEST_DB), "guarantee, winner, risk-free")

        # Verify default Gemini API key and GCP project
        gemini_key = get_config("gemini_api_key", db_path=TEST_DB)
        self.assertEqual(gemini_key, "AQ.Ab8RN6JyptGhhfk8w83PSpKVcFpmNJOA7aoEJtiB2BCEEiuwVw")
        gcp_proj = get_config("gcp_project_id", db_path=TEST_DB)
        self.assertEqual(gcp_proj, "606768026327")

    def test_02_contact_manager_crud(self):
        """Verify contact CRM persistence and custom variables JSON parsing."""
        cid = create_contact(
            name="David Vance",
            email="david@vancemedia.com",
            company="Vance Media",
            custom_variables={"Role": "CMO", "Audience": "B2B SaaS"},
            db_path=TEST_DB
        )
        self.assertIsNotNone(cid)

        contact = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(contact["name"], "David Vance")
        self.assertEqual(contact["company"], "Vance Media")
        self.assertEqual(contact["custom_variables_dict"]["Role"], "CMO")
        self.assertEqual(contact["custom_variables_dict"]["Audience"], "B2B SaaS")

        contacts = get_contacts(db_path=TEST_DB)
        self.assertTrue(any(c["id"] == cid for c in contacts))

    def test_03_template_builder_crud(self):
        """Verify template creation and retrieval."""
        tid = create_template(
            template_name="SaaS Outreach",
            body_content="{Hey|Hello} [Name], how is [Company] scaling [Audience]?",
            db_path=TEST_DB
        )
        self.assertIsNotNone(tid)

        tpl = get_template_by_id(tid, db_path=TEST_DB)
        self.assertEqual(tpl["template_name"], "SaaS Outreach")
        self.assertIn("[Company]", tpl["body_content"])

    def test_04_variable_injection(self):
        """Test replacement of bracketed standard and custom variables."""
        template_text = "Hi [Name], I noticed [Company] is hiring a [Role] for [Niche]!"
        contact_data = {
            "name": "Marcus Kane",
            "company": "Kane Logistics",
            "email": "marcus@kane.com",
            "custom_variables_dict": {
                "Role": "Supply Chain Director",
                "Niche": "Cold Storage"
            }
        }
        injected = inject_variables(template_text, contact_data)
        self.assertEqual(injected, "Hi Marcus Kane, I noticed Kane Logistics is hiring a Supply Chain Director for Cold Storage!")

    def test_05_spintax_parser(self):
        """Test single and nested Spintax {a|b|c} resolution."""
        # Simple spintax
        simple_spintax = "{Hi|Hello|Hey} team"
        resolved_simple = parse_spintax(simple_spintax)
        self.assertIn(resolved_simple, ["Hi team", "Hello team", "Hey team"])

        # Multiple spintax blocks
        multi_spintax = "{Good morning|Greetings}, we are {thrilled|excited} to connect."
        resolved_multi = parse_spintax(multi_spintax)
        self.assertTrue(resolved_multi.startswith(("Good morning,", "Greetings,")))
        self.assertTrue("thrilled to connect." in resolved_multi or "excited to connect." in resolved_multi)

        # Nested spintax
        nested_spintax = "{Hi {there|friend}|Welcome}"
        resolved_nested = parse_spintax(nested_spintax)
        self.assertIn(resolved_nested, ["Hi there", "Hi friend", "Welcome"])

    def test_06_negative_keyword_scanner(self):
        """Test negative keyword scanner flags restricted terms."""
        banned_list = "guarantee, 100% free, winner, cash prize, risk-free"

        # Text with negative keyword
        clean_text = "<p>Hi John, we have a case study on increasing catalog visibility.</p>"
        flagged_text = "<p>Hi John, we guarantee a 25% lift in conversion rates.</p>"
        phrase_flagged = "<p>Try this completely risk-free for 30 days.</p>"

        self.assertIsNone(scan_negative_keywords(clean_text, banned_list))
        self.assertEqual(scan_negative_keywords(flagged_text, banned_list), "guarantee")
        self.assertEqual(scan_negative_keywords(phrase_flagged, banned_list), "risk-free")

    def test_07_email_flagging_and_auto_rewrite_status(self):
        """Verify Flagged email lifecycle in SQLite."""
        draft_id = create_email(
            email_html="<p>Get a guarantee on listing audit results.</p>",
            subject="Special Guarantee",
            status="Flagged",
            revision_notes="Flagged for negative keyword: 'guarantee'",
            db_path=TEST_DB
        )
        rec = get_email_by_id(draft_id, db_path=TEST_DB)
        self.assertEqual(rec["status"], "Flagged")
        self.assertIn("guarantee", rec["revision_notes"])

        # Simulate clearing after Auto-Rewrite
        update_email(
            email_id=draft_id,
            email_html="<p>Get a dependable improvement on listing audit results.</p>",
            subject="Listing Audit Results",
            status="Pending",
            revision_notes="Cleaned via Auto-Rewrite",
            db_path=TEST_DB
        )
        cleaned = get_email_by_id(draft_id, db_path=TEST_DB)
        self.assertEqual(cleaned["status"], "Pending")

    def test_08_llm_json_parser_robustness(self):
        """Test variations parser handles strict variations schema and markdown fences."""
        strict_schema_json = '''{
            "variations": [
                {"subject": "Variation 1", "body_html": "<p>Body 1</p>"},
                {"subject": "Variation 2", "body_html": "<p>Body 2</p>"}
            ]
        }'''
        parsed_strict = _parse_variations_json(strict_schema_json, expected_count=2)
        self.assertEqual(len(parsed_strict), 2)
        self.assertEqual(parsed_strict[0]["subject"], "Variation 1")
        self.assertEqual(parsed_strict[1]["body_html"], "<p>Body 2</p>")

    def test_09_tags_and_upsert(self):
        """Test contact tagging, deduplicated upsert, and distinct tags retrieval."""
        # 1. Insert new contact
        cid1, is_new1 = upsert_contact_by_email(
            name="Alice Smith",
            email="alice@beautyco.com",
            company="Beauty Co",
            tags="Beauty Brands, Q4 Leads",
            custom_variables={"Role": "Founder"},
            db_path=TEST_DB
        )
        self.assertTrue(is_new1)

        # 2. Upsert same email with new tag and extra custom variable
        cid2, is_new2 = upsert_contact_by_email(
            name="Alice Smith",
            email="alice@beautyco.com",
            company="Beauty Co Inc",
            tags="High Priority",
            custom_variables={"Niche": "Organic Skincare"},
            db_path=TEST_DB
        )
        self.assertFalse(is_new2)
        self.assertEqual(cid1, cid2)

        # Verify merged tags and variables
        contact = get_contact_by_id(cid1, db_path=TEST_DB)
        self.assertIn("Beauty Brands", contact["tags_list"])
        self.assertIn("High Priority", contact["tags_list"])
        self.assertEqual(contact["custom_variables_dict"]["Role"], "Founder")
        self.assertEqual(contact["custom_variables_dict"]["Niche"], "Organic Skincare")

        # 3. Query all distinct tags
        all_tags = get_all_distinct_tags(db_path=TEST_DB)
        self.assertIn("Beauty Brands", all_tags)
        self.assertIn("High Priority", all_tags)

    def test_10_csv_handler_import_export(self):
        """Test CSV template generation, bulk import with deduplication, and export."""
        # 1. Template generation
        template_str = generate_csv_template()
        self.assertIn("Name,Email,Company,Tags,Custom_Variables", template_str)
        self.assertIn("Skinfix", template_str)

        # 2. CSV Import
        sample_csv = (
            "Name,Email,Company,Tags,Custom_Variables\n"
            'Jessica Ray,jessica@glowlab.com,Glow Lab,"Beauty Brands, Skincare","{\\"Role\\": \\"VP Growth\\"}"\n'
            'Alice Smith,alice@beautyco.com,Beauty Co Inc,"New Tag","{\\"Tier\\": \\"Enterprise\\"}"\n'
        )
        stats = import_contacts_from_csv(sample_csv, db_path=TEST_DB)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["inserted"], 1)  # Jessica Ray is new
        self.assertEqual(stats["updated"], 1)   # Alice Smith already exists, so updated

        # 3. CSV Export
        contacts = get_contacts(db_path=TEST_DB)
        exported_csv = export_contacts_to_csv(contacts)
        self.assertIn("jessica@glowlab.com", exported_csv)
        self.assertIn("alice@beautyco.com", exported_csv)

    def test_11_smtp_account_crud(self):
        """Test adding, retrieving, updating, and deleting SMTP accounts."""
        acc_id = add_smtp_account(
            sender_name="Alex Morgan",
            email="alex@sellomize.com",
            password="secretpassword123",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            daily_limit=75,
            db_path=TEST_DB
        )
        self.assertIsNotNone(acc_id)

        acc = get_smtp_account_by_id(acc_id, db_path=TEST_DB)
        self.assertEqual(acc["email"], "alex@sellomize.com")
        self.assertEqual(acc["daily_limit"], 75)
        self.assertEqual(acc["is_active"], 1)

        # Update daily limit and pause
        update_smtp_account(acc_id, daily_limit=100, is_active=False, db_path=TEST_DB)
        updated = get_smtp_account_by_id(acc_id, db_path=TEST_DB)
        self.assertEqual(updated["daily_limit"], 100)
        self.assertEqual(updated["is_active"], 0)

        # Reactivate
        update_smtp_account(acc_id, is_active=True, db_path=TEST_DB)
        reactivated = get_smtp_account_by_id(acc_id, db_path=TEST_DB)
        self.assertEqual(reactivated["is_active"], 1)

        # Clean up
        delete_smtp_account(acc_id, db_path=TEST_DB)
        self.assertIsNone(get_smtp_account_by_id(acc_id, db_path=TEST_DB))

    def test_12_smtp_rotation_and_limits(self):
        """Test multi-account round-robin load balancing and daily limit rollover."""
        acc1 = add_smtp_account(
            sender_name="Sender 1",
            email="sender1@domain.com",
            password="pass1",
            daily_limit=2,
            db_path=TEST_DB
        )
        acc2 = add_smtp_account(
            sender_name="Sender 2",
            email="sender2@domain.com",
            password="pass2",
            daily_limit=2,
            db_path=TEST_DB
        )

        # Next account should pick sender1 or sender2 (both have sent_today = 0)
        next_acc = get_next_available_smtp_account(db_path=TEST_DB)
        self.assertIsNotNone(next_acc)
        self.assertEqual(next_acc["sent_today"], 0)

        # Increment sent for acc1
        increment_smtp_sent(acc1, db_path=TEST_DB)

        # Next account must now be acc2 because acc2 has sent_today=0 while acc1 has 1
        next_acc2 = get_next_available_smtp_account(db_path=TEST_DB)
        self.assertEqual(next_acc2["id"], acc2)

        # Exhaust both accounts to limit (2 each)
        increment_smtp_sent(acc1, db_path=TEST_DB)  # acc1 now at 2/2
        increment_smtp_sent(acc2, db_path=TEST_DB)  # acc2 now at 1/2
        increment_smtp_sent(acc2, db_path=TEST_DB)  # acc2 now at 2/2

        # Both full -> next account should be None
        exhausted = get_next_available_smtp_account(db_path=TEST_DB)
        self.assertIsNone(exhausted)

        # Clean up
        delete_smtp_account(acc1, db_path=TEST_DB)
        delete_smtp_account(acc2, db_path=TEST_DB)

    def test_13_smtp_dispatch_and_mime(self):
        """Test plain text conversion and mocked SMTP dispatch."""
        html_input = "<p>Hello <b>World</b>!</p><br><p>Check <a href='https://example.com'>this link</a>.</p>"
        plain = html_to_plain_text(html_input)
        self.assertIn("Hello World!", plain)
        self.assertNotIn("<p>", plain)

        mock_acc = {
            "id": 99,
            "sender_name": "Test Agency",
            "email": "outreach@testagency.com",
            "password": "fake_password",
            "smtp_host": "smtp.hostinger.com",
            "smtp_port": 465
        }

        # Mock smtplib.SMTP_SSL
        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_server_instance = MagicMock()
            mock_smtp_ssl.return_value.__enter__.return_value = mock_server_instance

            success, msg = send_smtp_email(
                smtp_account=mock_acc,
                recipient="client@prospectivebrand.com",
                subject="Partnership Opportunity",
                html_content="<p>Hi Client, let's connect.</p>",
                bcc_email="archive@testagency.com"
            )

            self.assertTrue(success)
            self.assertIn("Sent via Hostinger SMTP", msg)
            mock_server_instance.login.assert_called_once_with("outreach@testagency.com", "fake_password")
            mock_server_instance.send_message.assert_called_once()

            # Verify connection test with mock
            test_ok, test_msg = test_smtp_connection("smtp.hostinger.com", 465, "outreach@testagency.com", "fake_password")
            self.assertTrue(test_ok)
            self.assertIn("Authentication successful", test_msg)

    def test_14_bulk_contact_operations_and_predefined_tags(self):
        """Test predefined outreach tags, bulk tagging, bulk details edit, and bulk delete."""
        # 1. Verify predefined tags list
        predefined = get_predefined_tags()
        self.assertIn("Amazon Brand", predefined)
        self.assertIn("Shopify DTC", predefined)
        self.assertIn("High Priority", predefined)
        self.assertIn("Cold Outreach", predefined)

        # 2. Create 3 test contacts
        c1 = create_contact("Lead One", "one@brand.com", "Brand 1", "Cold Outreach", {"Role": "CEO"}, db_path=TEST_DB)
        c2 = create_contact("Lead Two", "two@brand.com", "Brand 2", "Cold Outreach", {"Role": "Director"}, db_path=TEST_DB)
        c3 = create_contact("Lead Three", "three@brand.com", "Brand 3", "Other", {"Role": "Owner"}, db_path=TEST_DB)

        # 3. Bulk Add Tags
        added_count = bulk_add_tags_to_contacts([c1, c2], ["Amazon Brand", "Audit Ready"], db_path=TEST_DB)
        self.assertEqual(added_count, 2)
        rec1 = get_contact_by_id(c1, db_path=TEST_DB)
        self.assertIn("Amazon Brand", rec1["tags_list"])
        self.assertIn("Audit Ready", rec1["tags_list"])
        self.assertIn("Cold Outreach", rec1["tags_list"]) # preserved

        # 4. Bulk Remove Tags
        rem_count = bulk_remove_tags_from_contacts([c1, c2], ["Cold Outreach"], db_path=TEST_DB)
        self.assertEqual(rem_count, 2)
        rec1 = get_contact_by_id(c1, db_path=TEST_DB)
        self.assertNotIn("Cold Outreach", rec1["tags_list"])

        # 5. Bulk Set Tags (replace all)
        set_count = bulk_set_tags_for_contacts([c1, c2], ["Shopify DTC", "High Priority"], db_path=TEST_DB)
        self.assertEqual(set_count, 2)
        rec1 = get_contact_by_id(c1, db_path=TEST_DB)
        self.assertEqual(sorted(rec1["tags_list"]), ["High Priority", "Shopify DTC"])

        # 6. Bulk Update Details (Company & Variables)
        up_count = bulk_update_contacts_details(
            contact_ids=[c1, c2],
            company="Unified Brand Corp",
            custom_vars_to_merge={"Niche": "Beauty & Personal Care"},
            db_path=TEST_DB
        )
        self.assertEqual(up_count, 2)
        rec1 = get_contact_by_id(c1, db_path=TEST_DB)
        self.assertEqual(rec1["company"], "Unified Brand Corp")
        self.assertEqual(rec1["custom_variables_dict"]["Role"], "CEO") # preserved
        self.assertEqual(rec1["custom_variables_dict"]["Niche"], "Beauty & Personal Care") # merged

        # 7. Bulk Delete
        del_count = bulk_delete_contacts([c1, c2, c3], db_path=TEST_DB)
        self.assertEqual(del_count, 3)
        self.assertIsNone(get_contact_by_id(c1, db_path=TEST_DB))
        self.assertIsNone(get_contact_by_id(c2, db_path=TEST_DB))
        self.assertIsNone(get_contact_by_id(c3, db_path=TEST_DB))

if __name__ == "__main__":
    unittest.main()

