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
    get_effective_daily_limit,
    get_predefined_tags,
    bulk_add_tags_to_contacts,
    bulk_remove_tags_from_contacts,
    bulk_set_tags_for_contacts,
    bulk_update_contacts_details,
    bulk_delete_contacts,
    get_all_distinct_custom_variable_keys,
    parse_variables_from_text,
    format_variables_as_lines,
    bulk_update_contact_grid,
    advance_contact_followup,
    record_email_open,
    record_email_bounce,
    record_email_reply,
    record_email_click,
    get_outreach_analytics,
    get_bounced_contacts,
    get_replied_contacts,
    is_within_sending_window,
    get_next_valid_sending_datetime
)
from smtp_dispatcher import (
    test_smtp_connection,
    send_smtp_email,
    html_to_plain_text,
    extract_bounced_info_from_msg
)
from tracker import (
    inject_tracking_pixel,
    get_tracking_base_url,
    wrap_links_with_click_tracking,
    inject_tracking_and_links
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
    scan_negative_keywords,
    audit_email_deliverability,
    COMMON_SPAM_TRIGGERS
)
from scheduler import (
    run_scheduler_cycle,
    dispatch_email_hostinger,
    dispatch_email_outlook,
    dispatch_email
)
from mx_checker import (
    verify_email_domain_mx,
    batch_verify_contacts_mx,
    clear_mx_cache,
    get_cached_domain_count,
    get_domain_from_email
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
        self.assertIn("Lead ID,Company,Contact Name,Email Address,Lead Source,Priority", template_str)
        self.assertIn("Status,Follow-Ups Sent,Last Contact Date,Next Follow-Up,Owner,Notes", template_str)
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

    def test_15_custom_variables_line_parser_and_helpers(self):
        """Test intelligent parsing of line-based variables, JSON fallback, and formatting."""
        # 1. Test Key: Value parsing
        raw_lines = """
        Role: Managing Director
        Website: https://peakcoffee.com
        ASIN: B08N5WRWNW
        Location: Denver, CO
        """
        parsed = parse_variables_from_text(raw_lines)
        self.assertEqual(parsed["Role"], "Managing Director")
        self.assertEqual(parsed["Website"], "https://peakcoffee.com")
        self.assertEqual(parsed["ASIN"], "B08N5WRWNW")
        self.assertEqual(parsed["Location"], "Denver, CO")

        # 2. Test JSON parsing fallback
        raw_json = '{"Role": "Founder", "Website": "https://brand.com"}'
        parsed_json = parse_variables_from_text(raw_json)
        self.assertEqual(parsed_json["Role"], "Founder")
        self.assertEqual(parsed_json["Website"], "https://brand.com")

        # 3. Test Key = Value parsing
        raw_eq = "Product = Nitro Cold Brew\nMonthly Revenue = $120,000"
        parsed_eq = parse_variables_from_text(raw_eq)
        self.assertEqual(parsed_eq["Product"], "Nitro Cold Brew")
        self.assertEqual(parsed_eq["Monthly Revenue"], "$120,000")

        # 4. Test format_variables_as_lines
        lines_formatted = format_variables_as_lines(parsed_eq)
        self.assertIn("Product: Nitro Cold Brew", lines_formatted)
        self.assertIn("Monthly Revenue: $120,000", lines_formatted)

        # 5. Test get_all_distinct_custom_variable_keys
        create_contact("Key Lead", "keylead@co.com", "Co", "Tag1", {"CustomField123": "val"}, db_path=TEST_DB)
        all_keys = get_all_distinct_custom_variable_keys(include_predefined=True, db_path=TEST_DB)
        self.assertIn("Role", all_keys)
        self.assertIn("Website", all_keys)
        self.assertIn("ASIN", all_keys)
        self.assertIn("CustomField123", all_keys)

    def test_16_smart_csv_column_auto_absorption(self):
        """Test that CSV import automatically absorbs non-standard columns as custom variables."""
        # CSV with First Name, Last Name, Email, Company, Job Title, Store URL, ASIN, Phone
        csv_data = (
            "First Name,Last Name,Email,Company,Job Title,Store URL,ASIN,Phone\n"
            "Jessica,Miller,jessica@pureglow.com,Pure Glow,Head of Marketing,https://pureglow.com,B09GLOW123,555-0199\n"
        )
        stats = import_contacts_from_csv(csv_data, db_path=TEST_DB)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(stats["errors"], [])

        contacts = get_contacts(db_path=TEST_DB)
        jessica = next(c for c in contacts if c["email"] == "jessica@pureglow.com")
        self.assertEqual(jessica["name"], "Jessica Miller")
        self.assertEqual(jessica["company"], "Pure Glow")
        
        cv = jessica["custom_variables_dict"]
        self.assertEqual(cv["Role"], "Head of Marketing") # normalized from Job Title
        self.assertEqual(cv["Website"], "https://pureglow.com") # normalized from Store URL
        self.assertEqual(cv["ASIN"], "B09GLOW123")
        self.assertEqual(cv["Phone"], "555-0199")

        # Verify export expands these custom variables into their own clean columns
        exported_csv = export_contacts_to_csv([jessica])
        self.assertIn("Role", exported_csv)
        self.assertIn("Website", exported_csv)
        self.assertIn("ASIN", exported_csv)
        self.assertIn("Phone", exported_csv)
        self.assertIn("Head of Marketing", exported_csv)
        self.assertIn("https://pureglow.com", exported_csv)

    def test_17_excel_crm_grid_and_followup_advance(self):
        """Test Excel-like CRM grid bulk update and automated sequence followup advancing."""
        cid, _ = upsert_contact_by_email(
            name="Gregory House",
            email="gregory@princetonplainsboro.org",
            company="Princeton Diagnostics",
            lead_source="LinkedIn",
            priority="High",
            owner="Jack C",
            status="Not Contacted",
            notes="Requires case study teardown",
            db_path=TEST_DB
        )
        self.assertIsNotNone(cid)

        # 1. Bulk update grid
        grid_record = {
            "id": cid,
            "Contact Name": "Dr. Gregory House",
            "Company": "Plainsboro Health",
            "Lead Source": "Referral",
            "Priority": "High",
            "Contacted?": "No",
            "Status": "Not Contacted",
            "Follow-Ups Sent": 0,
            "Owner": "Alex M",
            "Notes": "Scheduled diagnostic audit",
            "Tags": "Medical, Enterprise"
        }
        updated_count = bulk_update_contact_grid([grid_record], db_path=TEST_DB)
        self.assertEqual(updated_count, 1)

        c_after = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_after["name"], "Dr. Gregory House")
        self.assertEqual(c_after["company"], "Plainsboro Health")
        self.assertEqual(c_after["lead_source"], "Referral")
        self.assertEqual(c_after["owner"], "Alex M")
        self.assertEqual(c_after["notes"], "Scheduled diagnostic audit")
        self.assertIn("Enterprise", c_after["tags_list"])

        # 2. Automated sequence advancement (Touchpoint 1: Not Contacted -> Contacted)
        adv_res = advance_contact_followup(cid, delay_days=5, db_path=TEST_DB)
        self.assertTrue(adv_res)

        c_adv = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_adv["contacted"], "Yes")
        self.assertEqual(c_adv["follow_ups_sent"], 1)
        self.assertEqual(c_adv["status"], "Contacted")
        self.assertIsNotNone(c_adv["date_first_emailed"])
        self.assertIsNotNone(c_adv["last_contact_date"])

        expected_next = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        self.assertEqual(c_adv["next_follow_up"], expected_next)

        # Automated sequence advancement (Touchpoint 2: Contacted -> Follow-Up Sent)
        advance_contact_followup(cid, delay_days=5, db_path=TEST_DB)
        c_adv2 = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_adv2["follow_ups_sent"], 2)
        self.assertEqual(c_adv2["status"], "Follow-Up Sent")

    def test_18_open_tracking_pixel_and_record_open(self):
        """Test open tracking pixel injection and database open recording telemetry."""
        # 1. Pixel injection
        raw_html = "<html><body><p>Hello Prospect!</p></body></html>"
        injected = inject_tracking_pixel(raw_html, email_id=456)
        self.assertIn("/track/open/456.png", injected)
        self.assertIn("width=\"1\" height=\"1\"", injected)

        # 2. Record email open
        cid, _ = upsert_contact_by_email(
            name="Rachel Green",
            email="rachel@ralphlauren.com",
            company="Ralph Lauren",
            status="Contacted",
            db_path=TEST_DB
        )
        eid = create_email(
            email_html="<p>Fashion audit</p>",
            subject="Spring 2026 Collection",
            recipient="rachel@ralphlauren.com",
            status="Sent",
            db_path=TEST_DB
        )

        open_res = record_email_open(eid, db_path=TEST_DB)
        self.assertTrue(open_res)

        e_check = get_email_by_id(eid, db_path=TEST_DB)
        self.assertIsNotNone(e_check["opened_at"])
        self.assertEqual(e_check["open_count"], 1)

        # Contact status should be promoted to Opened / Interested
        c_check = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_check["status"], "Opened / Interested")

    def test_19_bounce_recording_and_analytics(self):
        """Test bounce detection recording, deliverability quarantine, and outreach analytics."""
        cid, _ = upsert_contact_by_email(
            name="Invalid User",
            email="invalid.mailbox.doesnotexist@nowhere987.org",
            company="Ghost Inc",
            status="Contacted",
            db_path=TEST_DB
        )
        eid = create_email(
            email_html="<p>Test</p>",
            subject="Delivery Test",
            recipient="invalid.mailbox.doesnotexist@nowhere987.org",
            status="Sent",
            db_path=TEST_DB
        )

        # Record bounce
        bounce_ok = record_email_bounce(
            recipient_email="invalid.mailbox.doesnotexist@nowhere987.org",
            bounce_reason="550 5.1.1 Recipient address rejected: User unknown",
            db_path=TEST_DB
        )
        self.assertTrue(bounce_ok)

        # Verify contact status quarantined
        c_bounced = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_bounced["status"], "Bounced")
        self.assertIn("Bounced", c_bounced["tags_list"])
        self.assertIn("550 5.1.1", c_bounced["notes"])

        # Verify email marked as bounced
        e_bounced = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(e_bounced["is_bounced"], 1)
        self.assertIn("550 5.1.1", e_bounced["bounce_reason"])

        # Verify get_bounced_contacts
        bounced_list = get_bounced_contacts(db_path=TEST_DB)
        self.assertTrue(any(b["email"] == "invalid.mailbox.doesnotexist@nowhere987.org" for b in bounced_list))

        # Verify analytics report metrics
        analytics = get_outreach_analytics(db_path=TEST_DB)
        self.assertGreaterEqual(analytics["total_sent"], 1)
        self.assertGreaterEqual(analytics["total_opened"], 1)
        self.assertGreaterEqual(analytics["total_bounced"], 1)
        self.assertIsInstance(analytics["open_rate"], float)
        self.assertIsInstance(analytics["bounce_rate"], float)

    def test_20_bounce_parsing_from_ndr(self):
        """Test parsing of delivery status notifications (NDRs) to extract bounced address and reason."""
        import email
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["From"] = "MAILER-DAEMON@hostinger.com"
        msg["Subject"] = "Undelivered Mail Returned to Sender"
        body = (
            "This is the mail system at host mailer.hostinger.com.\n\n"
            "I'm sorry to have to inform you that your message could not\n"
            "be delivered to one or more recipients.\n\n"
            "<failed.target@somedomain.com>: host mail.somedomain.com said:\n"
            "550 5.1.1 User unknown (in reply to RCPT TO command)\n"
        )
        msg.attach(MIMEText(body, "plain"))

        failed_email, reason = extract_bounced_info_from_msg(msg)
        self.assertEqual(failed_email, "failed.target@somedomain.com")
        self.assertIn("550 5.1.1 User unknown", reason)

    def test_21_sending_window_validation(self):
        """Test active business days and hours sending window validation."""
        set_config("sending_days", "Monday,Tuesday,Wednesday,Thursday,Friday", db_path=TEST_DB)
        set_config("sending_start_time", "09:00", db_path=TEST_DB)
        set_config("sending_end_time", "18:00", db_path=TEST_DB)
        set_config("enforce_sending_window", "true", db_path=TEST_DB)

        # 1. Tuesday at 14:30 (Valid: weekday & inside 09:00-18:00)
        tue_dt = datetime(2026, 9, 15, 14, 30).astimezone()
        is_ok, msg = is_within_sending_window(tue_dt, db_path=TEST_DB)
        self.assertTrue(is_ok)
        self.assertIn("Inside outbound window", msg)

        # 2. Tuesday at 21:00 (Invalid: past 18:00 cutoff)
        tue_night = datetime(2026, 9, 15, 21, 0).astimezone()
        is_ok, msg = is_within_sending_window(tue_night, db_path=TEST_DB)
        self.assertFalse(is_ok)
        self.assertIn("past daily cutoff time", msg)

        # 3. Saturday at 12:00 (Invalid: weekend)
        sat_dt = datetime(2026, 9, 19, 12, 0).astimezone()
        is_ok, msg = is_within_sending_window(sat_dt, db_path=TEST_DB)
        self.assertFalse(is_ok)
        self.assertIn("outside allowed sending days", msg)

        # 4. Disable enforcement -> 24/7 allowed
        set_config("enforce_sending_window", "false", db_path=TEST_DB)
        is_ok, msg = is_within_sending_window(sat_dt, db_path=TEST_DB)
        self.assertTrue(is_ok)
        self.assertIn("disabled", msg)

        # Re-enable enforcement
        set_config("enforce_sending_window", "true", db_path=TEST_DB)

    def test_22_next_valid_sending_datetime(self):
        """Test calculation of next valid sending datetime skipping weekends and off-hours."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        start_t = "09:00"
        end_t = "18:00"

        # 1. Friday 20:00 (after hours) -> should advance to Monday 09:00
        fri_late = datetime(2026, 9, 18, 20, 0).astimezone()
        next_slot = get_next_valid_sending_datetime(
            base_dt=fri_late,
            sending_days=days,
            start_time_str=start_t,
            end_time_str=end_t,
            db_path=TEST_DB
        )
        self.assertEqual(next_slot.strftime("%A"), "Monday")
        self.assertEqual(next_slot.strftime("%H:%M"), "09:00")

        # 2. Wednesday 07:30 (before start) -> should snap to Wednesday 09:00
        wed_early = datetime(2026, 9, 16, 7, 30).astimezone()
        next_slot = get_next_valid_sending_datetime(
            base_dt=wed_early,
            sending_days=days,
            start_time_str=start_t,
            end_time_str=end_t,
            db_path=TEST_DB
        )
        self.assertEqual(next_slot.strftime("%A"), "Wednesday")
        self.assertEqual(next_slot.strftime("%H:%M"), "09:00")

        # 3. Wednesday 11:00 with 15 min delay -> Wednesday 11:15
        wed_mid = datetime(2026, 9, 16, 11, 0).astimezone()
        next_slot = get_next_valid_sending_datetime(
            base_dt=wed_mid,
            delay_minutes=15,
            sending_days=days,
            start_time_str=start_t,
            end_time_str=end_t,
            db_path=TEST_DB
        )
        self.assertEqual(next_slot.strftime("%A"), "Wednesday")
        self.assertEqual(next_slot.strftime("%H:%M"), "11:15")

    def test_23_scheduler_window_enforcement(self):
        """Test scheduler pausing when outside window and dispatching when window is open."""
        # 1. Ensure an approved due email exists
        now_local = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        eid = create_email(
            email_html="<p>Window test</p>",
            subject="Window Test",
            recipient="test.window@agency.com",
            status="Approved",
            scheduled_time=now_local,
            db_path=TEST_DB
        )

        # 2. Configure sending days to a day that is NOT today so window is closed
        today_name = datetime.now().astimezone().strftime("%A")
        opposite_day = "Sunday" if today_name != "Sunday" else "Monday"
        set_config("sending_days", opposite_day, db_path=TEST_DB)
        set_config("enforce_sending_window", "true", db_path=TEST_DB)

        # 3. Cycle should return 0 (paused)
        processed = run_scheduler_cycle(dry_run=False, db_path=TEST_DB)
        self.assertEqual(processed, 0)

        # Email should remain Approved
        e_check = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(e_check["status"], "Approved")

        # 4. Now open window by adding today_name to sending_days and setting hours 00:00 to 23:59
        set_config("sending_days", f"{today_name},Monday,Tuesday,Wednesday,Thursday,Friday", db_path=TEST_DB)
        set_config("sending_start_time", "00:00", db_path=TEST_DB)
        set_config("sending_end_time", "23:59", db_path=TEST_DB)

        # 5. Cycle in dry_run mode should now process the due email
        processed_open = run_scheduler_cycle(dry_run=True, db_path=TEST_DB)
        self.assertGreaterEqual(processed_open, 1)

    def test_24_reply_detection_and_auto_cancellation(self):
        """Test prospect reply detection, status updating to Replied, and outbox follow-up auto-cancellation."""
        # 1. Create a prospect contact
        cid = create_contact("Alex Mercer", "alex@mercerretail.com", "Mercer Retail", "E-Commerce", {"Role": "Founder"}, db_path=TEST_DB)

        # 2. Simulate initial email sent
        e1 = create_email(
            email_html="<p>Initial Pitch</p>",
            subject="Listing Audit for Mercer Retail",
            recipient="alex@mercerretail.com",
            status="Sent",
            db_path=TEST_DB
        )
        advance_contact_followup("alex@mercerretail.com", delay_days=4, db_path=TEST_DB)
        c_before = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_before["status"], "Contacted")
        self.assertEqual(c_before["follow_ups_sent"], 1)

        # 3. Queue 2 future follow-up drafts in Outbox (1 Approved, 1 Pending)
        e2 = create_email(
            email_html="<p>Follow Up #1</p>",
            subject="Following up on Listing Audit",
            recipient="alex@mercerretail.com",
            status="Approved",
            scheduled_time="2026-09-22 10:00:00",
            db_path=TEST_DB
        )
        e3 = create_email(
            email_html="<p>Follow Up #2</p>",
            subject="Quick video walkthrough",
            recipient="alex@mercerretail.com",
            status="Pending",
            scheduled_time="2026-09-26 10:00:00",
            db_path=TEST_DB
        )

        # Also create an email for a different prospect to verify it is NOT cancelled
        other_e = create_email(
            email_html="<p>Other Pitch</p>",
            subject="Different prospect",
            recipient="other@brand.com",
            status="Approved",
            scheduled_time="2026-09-22 10:00:00",
            db_path=TEST_DB
        )

        # 4. Now simulate incoming reply from alex@mercerretail.com
        reply_res = record_email_reply(
            sender_email="alex@mercerretail.com",
            reply_subject="Re: Listing Audit for Mercer Retail - Let's talk!",
            reply_body_snippet="Sounds interesting, are you free this Thursday at 2pm?",
            received_at="2026-09-19 14:22:00",
            db_path=TEST_DB
        )

        self.assertTrue(reply_res["contact_found"])
        self.assertEqual(reply_res["cancelled_drafts_count"], 2)
        self.assertIn(e2, reply_res["cancelled_email_ids"])
        self.assertIn(e3, reply_res["cancelled_email_ids"])

        # 5. Check contact record: status must be 'Replied', note appended, tag added
        c_after = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_after["status"], "Replied")
        self.assertIn("Replied", c_after["tags_list"])
        self.assertIn("[Replied:", c_after["notes"])
        self.assertEqual(c_after["last_reply_at"], "2026-09-19 14:22:00")
        self.assertIn("Let's talk", c_after["reply_subject"])

        # 6. Check emails table: e2 and e3 must be 'Cancelled', e1 remains 'Sent', other_e remains 'Approved'
        e1_check = get_email_by_id(e1, db_path=TEST_DB)
        self.assertEqual(e1_check["status"], "Sent")

        e2_check = get_email_by_id(e2, db_path=TEST_DB)
        self.assertEqual(e2_check["status"], "Cancelled")
        self.assertIn("Auto-cancelled", e2_check["error_message"])

        e3_check = get_email_by_id(e3, db_path=TEST_DB)
        self.assertEqual(e3_check["status"], "Cancelled")
        self.assertIn("Auto-cancelled", e3_check["error_message"])

        other_check = get_email_by_id(other_e, db_path=TEST_DB)
        self.assertEqual(other_check["status"], "Approved")

        # 7. Check analytics
        analytics = get_outreach_analytics(db_path=TEST_DB)
        self.assertGreaterEqual(analytics["total_replied"], 1)
        self.assertGreater(analytics["reply_rate"], 0.0)

        # 8. Check get_replied_contacts
        replied_list = get_replied_contacts(db_path=TEST_DB)
        self.assertTrue(any(r["email"] == "alex@mercerretail.com" for r in replied_list))

    def test_25_db_indexes_and_performance(self):
        """Test database indexes are properly created for high performance."""
        from database import get_connection
        conn = get_connection(TEST_DB)
        cursor = conn.cursor()

        # Check emails table indexes
        cursor.execute("PRAGMA index_list('emails')")
        email_indexes = [row["name"] for row in cursor.fetchall()]
        self.assertIn("idx_emails_status_sched", email_indexes)
        self.assertIn("idx_emails_recipient", email_indexes)

        # Check contacts table indexes
        cursor.execute("PRAGMA index_list('contacts')")
        contact_indexes = [row["name"] for row in cursor.fetchall()]
        self.assertIn("idx_contacts_email", contact_indexes)
        self.assertIn("idx_contacts_status", contact_indexes)

        conn.close()

    def test_26_click_tracking_redirect_and_telemetry(self):
        """Test link click tracking, contact tag promotion, and analytics CTR calculations."""
        # 1. Create a lead and sent email with a link
        cid = create_contact("Claire Redfield", "claire@terrasave.org", "TerraSave", "Non-Profit", db_path=TEST_DB)
        eid = create_email(
            email_html='<p>Please check our <a href="https://agency.com/case-study">case study</a>!</p>',
            subject="Case Study for TerraSave",
            recipient="claire@terrasave.org",
            status="Sent",
            db_path=TEST_DB
        )

        # 2. Test link wrapping
        wrapped = wrap_links_with_click_tracking(
            html_content='<p>Please check our <a href="https://agency.com/case-study">case study</a>!</p>',
            email_id=eid,
            base_url="http://localhost:8502"
        )
        self.assertIn(f"/track/click/{eid}?url=", wrapped)
        self.assertIn("https%3A%2F%2Fagency.com%2Fcase-study", wrapped)

        # 3. Simulate click event
        success = record_email_click(email_id=eid, clicked_url="https://agency.com/case-study", db_path=TEST_DB)
        self.assertTrue(success)

        # 4. Verify email row
        e_check = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(e_check["click_count"], 1)
        self.assertTrue(bool(e_check["clicked_at"]))
        self.assertEqual(e_check["last_clicked_url"], "https://agency.com/case-study")

        # 5. Verify contact updated with 'Clicked Link' tag and note
        c_check = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertIn("Clicked Link", c_check["tags_list"])
        self.assertIn("[Clicked Link:", c_check["notes"])

        # 6. Verify analytics metrics
        analytics = get_outreach_analytics(db_path=TEST_DB)
        self.assertGreaterEqual(analytics["total_clicked"], 1)
        self.assertGreater(analytics["click_rate"], 0.0)

    def test_27_wrap_links_preserves_special_urls(self):
        """Test that mailto:, tel:, #anchors, and tracking URLs are untouched while web links are wrapped."""
        html_input = (
            '<body>'
            '<p><a href="mailto:support@agency.com">Email Us</a></p>'
            '<p><a href="tel:+123456789">Call Us</a></p>'
            '<p><a href="#section2">Jump</a></p>'
            '<p><a href="https://calendly.com/agency/30min">Book Demo</a></p>'
            '</body>'
        )
        wrapped = inject_tracking_and_links(html_input, email_id=99, base_url="http://localhost:8502")

        # Special URLs must be preserved
        self.assertIn('href="mailto:support@agency.com"', wrapped)
        self.assertIn('href="tel:+123456789"', wrapped)
        self.assertIn('href="#section2"', wrapped)

        # Web URL must be wrapped
        self.assertIn('/track/click/99?url=https%3A%2F%2Fcalendly.com%2Fagency%2F30min', wrapped)

        # Open tracking pixel must also be injected before </body>
        self.assertIn('/track/open/99.png', wrapped)

    def test_28_deliverability_auditor_scoring(self):
        """Test the live deliverability & spam trigger auditor scoring, synonym suggestions, and syntax checks."""
        # 1. Clean professional cold outreach copy
        clean_subject = "Quick question regarding listing expansion"
        clean_body = (
            "<p>Hi David,</p>"
            "<p>I came across your store catalog while researching DTC leaders in the apparel space. "
            "We recently prepared a brief teardown outlining three optimization opportunities that could help your brand "
            "scale customer acquisition on marketplace channels.</p>"
            "<p>Would you be open to reviewing the teardown sometime this week?</p>"
            "<p>Best regards,<br>Sarah</p>"
        )

        clean_report = audit_email_deliverability(body_html=clean_body, subject=clean_subject)
        self.assertGreaterEqual(clean_report["score"], 90)
        self.assertEqual(clean_report["grade"], "Excellent")
        self.assertEqual(len(clean_report["detected_spam_words"]), 0)
        self.assertTrue(any("Optimal subject length" in p for p in clean_report["passes"]))
        self.assertTrue(any("Zero blacklisted spam trigger words" in p for p in clean_report["passes"]))

        # 2. Spam-heavy email with fake Re:, exclamation marks, all caps, and trigger phrases
        spam_subject = "Re: ACT NOW: Guaranteed Pure Profit Winner!!!!"
        spam_body = (
            "<p>Dear Friend,</p>"
            "<p>This is 100% free with no risk! You will earn cash and make money right away with pure profit. "
            "Buy now or order now to claim your bonus cash before it is gone!</p>"
            "<p>Click here to get $$$ today: <a href='https://spam.xyz/deal'>Claim Now</a></p>"
        )

        spam_report = audit_email_deliverability(body_html=spam_body, subject=spam_subject)
        self.assertLess(spam_report["score"], 60)
        self.assertEqual(spam_report["grade"], "Spam Risk")

        # Verify detected spam words
        detected_words = [item["word"] for item in spam_report["detected_spam_words"]]
        self.assertTrue(any(w in detected_words for w in ["guaranteed", "pure profit", "100% free", "make money", "buy now", "click here"]))

        # Verify alternative synonym suggestions are populated
        guaranteed_entry = next((item for item in spam_report["detected_spam_words"] if item["word"] == "guaranteed"), None)
        if guaranteed_entry:
            self.assertGreaterEqual(len(guaranteed_entry["suggestions"]), 1)
            self.assertTrue(any(s in guaranteed_entry["suggestions"] for s in ["proven", "reliable", "consistent"]))

        # Verify issues flagged fake Re:, exclamation mark in subject, multiple '!!', and currency hype
        issues_text = " ".join(spam_report["issues"])
        self.assertIn("fake 'Re:'", issues_text)
        self.assertIn("Exclamation mark '!'", issues_text)
        self.assertIn("Multiple exclamation points '!!'", issues_text)
        self.assertIn("dollar signs", issues_text)

    def test_29_mx_checker_valid_and_invalid_domains(self):
        """Test pre-flight MX record verification, DNS resolution, dead domain detection, and caching."""
        clear_mx_cache()
        self.assertEqual(get_cached_domain_count(), 0)

        # 1. Valid domain with known MX records (e.g. gmail.com)
        is_valid, reason, records = verify_email_domain_mx("outreach.prospect@gmail.com")
        self.assertTrue(is_valid)
        self.assertGreater(len(records), 0)
        self.assertTrue("Valid MX" in reason or "Implicit MX" in reason or "resolved" in reason)

        # Cache should now contain gmail.com
        self.assertGreaterEqual(get_cached_domain_count(), 1)

        # Second lookup must hit cache
        is_valid2, _, _ = verify_email_domain_mx("another.lead@gmail.com")
        self.assertTrue(is_valid2)

        # 2. Malformed email address
        bad_syntax_valid, bad_reason, _ = verify_email_domain_mx("notanemail")
        self.assertFalse(bad_syntax_valid)
        self.assertIn("Invalid email format", bad_reason)

        # 3. Nonexistent domain (NXDOMAIN)
        fake_email = "test@nonexistentdomain9871239847192847.xyz"
        dead_valid, dead_reason, _ = verify_email_domain_mx(fake_email)
        self.assertFalse(dead_valid)
        self.assertTrue("does not exist" in dead_reason or "failed" in dead_reason)

        # 4. Batch verification
        batch_data = [
            {"id": 1, "email": "valid1@gmail.com"},
            {"id": 2, "email": "valid2@gmail.com"},
            {"id": 3, "email": "ghost@nonexistentdomain9871239847192847.xyz"},
            {"id": 4, "email": "bademailformat"}
        ]
        batch_res = batch_verify_contacts_mx(batch_data, update_db=False)
        self.assertEqual(batch_res["total"], 4)
        self.assertEqual(batch_res["valid_count"], 2)
        self.assertEqual(batch_res["invalid_count"], 2)
        self.assertEqual(len(batch_res["invalid_contacts"]), 2)

    def test_30_scheduler_preflight_mx_interception(self):
        """Test that scheduler intercepts dead domain emails before dispatch, marking them Bounced."""
        # 1. Create a contact with a dead domain
        dead_domain_email = "ceo@nonexistentcompany999888777666.org"
        cid = create_contact(
            name="Ghost CEO",
            email=dead_domain_email,
            company="Ghost Corp",
            status="Not Contacted",
            db_path=TEST_DB
        )

        # 2. Queue an approved email due now
        now_local = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        eid = create_email(
            email_html="<p>Pre-flight test</p>",
            subject="Pre-flight Test",
            recipient=dead_domain_email,
            status="Approved",
            scheduled_time=now_local,
            db_path=TEST_DB
        )

        # Ensure enforce_mx_check is enabled
        set_config("enforce_mx_check", "true", db_path=TEST_DB)
        # Ensure window is open
        today_name = datetime.now().astimezone().strftime("%A")
        set_config("sending_days", f"{today_name},Monday,Tuesday,Wednesday,Thursday,Friday", db_path=TEST_DB)
        set_config("sending_start_time", "00:00", db_path=TEST_DB)
        set_config("sending_end_time", "23:59", db_path=TEST_DB)

        # 3. Run scheduler cycle
        processed = run_scheduler_cycle(dry_run=False, db_path=TEST_DB)
        self.assertGreaterEqual(processed, 1)

        # 4. Verify email was intercepted and marked Bounced without sending
        e_check = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(e_check["status"], "Bounced")
        self.assertEqual(e_check["is_bounced"], 1)
        self.assertIn("Pre-flight MX check failed", e_check["error_message"])

        # 5. Verify contact was quarantined as Bounced
        c_check = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_check["status"], "Bounced")
        self.assertIn("Bounced", c_check["tags_list"])
        self.assertIn("[Bounced:", c_check["notes"])

    def test_31_csv_import_with_mx_verification(self):
        """Test CSV import with verify_mx=True automatically flags dead domains with 'Invalid MX'."""
        csv_payload = (
            "Name,Email,Company,Tags\n"
            "Live User,live@gmail.com,Google,Tech\n"
            "Dead Lead,dead@nonexistentcompany999888777666.org,Dead Co,Old Leads\n"
        )
        stats = import_contacts_from_csv(csv_payload, verify_mx=True, db_path=TEST_DB)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["invalid_mx"], 1)

        # Retrieve dead contact and verify 'Invalid MX' tag is attached
        dead_contact = get_contacts(search_query="Dead Co", db_path=TEST_DB)[0]
        self.assertIn("Invalid MX", dead_contact["tags_list"])
        self.assertIn("[MX Pre-Flight Failed:", dead_contact["notes"])

    def test_32_mailbox_warmup_effective_daily_limit(self):
        """Test calculation of active daily sending cap based on mailbox warmup ramp-up schedule."""
        acc_warmup = {
            "daily_limit": 100,
            "warmup_enabled": 1,
            "warmup_start_date": "2026-09-15",
            "warmup_starting_limit": 10,
            "warmup_daily_increment": 5,
            "warmup_target_limit": 50
        }

        # Day 0 (same as start date) -> 10 emails
        lim_day0 = get_effective_daily_limit(acc_warmup, today_str="2026-09-15")
        self.assertEqual(lim_day0, 10)

        # Day 2 -> 10 + (2 * 5) = 20 emails
        lim_day2 = get_effective_daily_limit(acc_warmup, today_str="2026-09-17")
        self.assertEqual(lim_day2, 20)

        # Day 8 -> 10 + (8 * 5) = 50 emails (at target limit)
        lim_day8 = get_effective_daily_limit(acc_warmup, today_str="2026-09-23")
        self.assertEqual(lim_day8, 50)

        # Day 20 -> 10 + (20 * 5) = 110, capped at warmup_target_limit (50)
        lim_day20 = get_effective_daily_limit(acc_warmup, today_str="2026-10-05")
        self.assertEqual(lim_day20, 50)

        # Warmup disabled -> falls back to daily_limit (100)
        acc_disabled = {
            "daily_limit": 100,
            "warmup_enabled": 0,
            "warmup_starting_limit": 10
        }
        self.assertEqual(get_effective_daily_limit(acc_disabled), 100)

    def test_33_scheduler_warmup_capacity_enforcement(self):
        """Test that scheduler respects warmup daily limit when rotating SMTP accounts."""
        today_str = datetime.now().astimezone().strftime("%Y-%m-%d")

        # 1. Add account with warmup enabled and cap of 2 emails/day
        acc_id = add_smtp_account(
            sender_name="Warmup Sender",
            email="warmup.box@agency.com",
            password="pass",
            daily_limit=80,
            warmup_enabled=True,
            warmup_start_date=today_str,
            warmup_starting_limit=2,
            warmup_daily_increment=5,
            warmup_target_limit=50,
            db_path=TEST_DB
        )

        # Verify account is initially available
        acc = get_next_available_smtp_account(db_path=TEST_DB)
        self.assertIsNotNone(acc)
        self.assertEqual(acc["id"], acc_id)
        self.assertEqual(acc["effective_daily_limit"], 2)

        # 2. Increment sent_today to 1
        increment_smtp_sent(acc_id, db_path=TEST_DB)
        acc_check1 = get_next_available_smtp_account(db_path=TEST_DB)
        self.assertIsNotNone(acc_check1)

        # 3. Increment sent_today to 2 (reaches warmup cap for today)
        increment_smtp_sent(acc_id, db_path=TEST_DB)
        acc_check2 = get_next_available_smtp_account(db_path=TEST_DB)
        self.assertIsNone(acc_check2)

        # Clean up
        delete_smtp_account(acc_id, db_path=TEST_DB)

if __name__ == "__main__":
    unittest.main()




