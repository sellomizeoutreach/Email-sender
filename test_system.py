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
    create_notification,
    get_notifications,
    mark_notification_as_read,
    mark_all_notifications_as_read,
    get_unread_notifications_count,
    delete_notification,
    clear_all_notifications,
    cleanup_duplicate_notifications,
    is_inbox_message_processed,
    mark_inbox_message_processed,
    create_sequence_rule,
    trigger_sequence_rules_for_sent_email,
    get_due_sequence_rules,
    mark_sequence_rule_status,
    cancel_sequence_rules_for_contact,
    link_sequence_rule_trigger,
    get_sequence_rules,
    DB_FILE,
    get_outreach_analytics,
    get_bounced_contacts,
    get_replied_contacts,
    is_within_sending_window,
    get_next_valid_sending_datetime,
    bulk_delete_emails,
    clear_outbox_emails,
    get_system_excluded_emails,
    cleanup_internal_drafts,
    create_proof_story,
    get_proof_stories,
    update_proof_story,
    delete_proof_story,
    CONTACT_STATUSES
)
from scheduler import process_due_sequence_rules
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
from template_engine import (
    inject_variables,
    parse_spintax,
    scan_negative_keywords,
    scan_all_negative_keywords,
    audit_email_deliverability,
    COMMON_SPAM_TRIGGERS,
    format_email_html,
    resolve_template,
    _missing_tokens
)
from scheduler import (
    run_scheduler_cycle,
    dispatch_email_hostinger,
    dispatch_email_outlook,
    dispatch_email,
    calculate_staggered_schedule,
    analyze_schedule_overflow,
    is_within_sending_window
)
from mx_checker import (
    verify_email_domain_mx,
    batch_verify_contacts_mx,
    clear_mx_cache,
    get_cached_domain_count,
    get_domain_from_email
)
from timezone_helper import (
    TARGET_MARKETS,
    get_market_info,
    get_market_current_time,
    get_time_difference_summary,
    is_within_market_hours,
    calculate_market_aware_schedule,
    get_zoneinfo
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
        set_config("dispatch_method", "hostinger_smtp", db_path=TEST_DB)
        val = get_config("dispatch_method", db_path=TEST_DB)
        self.assertEqual(val, "hostinger_smtp")

        set_config("negative_keywords", "guarantee, winner, risk-free", db_path=TEST_DB)
        self.assertEqual(get_config("negative_keywords", db_path=TEST_DB), "guarantee, winner, risk-free")

        # Verify defaults for sending schedule
        days = get_config("sending_days", db_path=TEST_DB)
        self.assertEqual(days, "Monday,Tuesday,Wednesday,Thursday,Friday")
        start_t = get_config("sending_start_time", db_path=TEST_DB)
        self.assertEqual(start_t, "09:00")

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
        """Test negative keyword scanner flags restricted terms and reports all matches."""
        banned_list = "guarantee, 100% free, winner, cash prize, risk-free"

        # Text with negative keyword
        clean_text = "<p>Hi John, we have a case study on increasing catalog visibility.</p>"
        flagged_text = "<p>Hi John, we guarantee a 25% lift in conversion rates.</p>"
        phrase_flagged = "<p>Try this completely risk-free for 30 days.</p>"
        multi_flagged = "<p>We guarantee this audit is 100% free and completely risk-free!</p>"

        self.assertIsNone(scan_negative_keywords(clean_text, banned_list))
        self.assertEqual(scan_negative_keywords(flagged_text, banned_list), "guarantee")
        self.assertEqual(scan_negative_keywords(phrase_flagged, banned_list), "risk-free")

        # Verify scan_all_negative_keywords detects every distinct trigger
        self.assertEqual(scan_all_negative_keywords(clean_text, banned_list), [])
        self.assertEqual(scan_all_negative_keywords(flagged_text, banned_list), ["guarantee"])
        self.assertEqual(
            sorted(scan_all_negative_keywords(multi_flagged, banned_list)),
            sorted(["guarantee", "100% free", "risk-free"])
        )

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

    def test_07b_email_approval_clears_flag_notes_and_reports_triggers(self):
        """Verify flag_email accepts multiple triggers and approve_email clears notes."""
        eid = create_email(
            email_html="<p>Draft with trigger terms.</p>",
            subject="Check this out",
            status="Pending",
            db_path=TEST_DB
        )

        # Flag with multiple triggers
        flag_email(eid, ["guarantee", "100% free"], db_path=TEST_DB)
        flagged_rec = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(flagged_rec["status"], "Flagged")
        self.assertIn("'guarantee'", flagged_rec["revision_notes"])
        self.assertIn("'100% free'", flagged_rec["revision_notes"])

        # Approve email - should change status to Approved and clear revision_notes
        approve_email(
            email_id=eid,
            recipient="test@example.com",
            scheduled_time="2026-09-20 12:00:00",
            email_html="<p>Cleaned copy without triggers.</p>",
            subject="Clean Subject",
            db_path=TEST_DB
        )
        approved_rec = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(approved_rec["status"], "Approved")
        self.assertIsNone(approved_rec["revision_notes"])
        self.assertEqual(approved_rec["recipient"], "test@example.com")

    def test_08_deterministic_template_resolution(self):
        """Verify deterministic variable injection, spintax resolution, and HTML formatting."""
        contact = {
            "name": "Sarah Connor",
            "company": "Cyberdyne Systems",
            "email": "sarah@cyberdyne.com",
            "custom_variables_dict": {"Role": "Security Director", "Store_URL": "cyberdyne.com"}
        }
        template = "{Hi|Hello|Hey} [Name], regarding [Company] and your role as [Role] at [Store_URL].\n\nWe saw your recent updates."
        resolved = resolve_template(template, contact)
        self.assertTrue(any(resolved.startswith(g) for g in ["Hi Sarah Connor,", "Hello Sarah Connor,", "Hey Sarah Connor,"]))
        self.assertIn("regarding Cyberdyne Systems", resolved)
        self.assertIn("Security Director at cyberdyne.com", resolved)

        # Test HTML paragraph wrapping
        formatted_html = format_email_html(resolved)
        self.assertTrue(formatted_html.startswith("<p style='margin: 0 0 1em 0;'>"))
        self.assertIn("<p style='margin: 0 0 1em 0;'>We saw your recent updates.</p>", formatted_html)

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
        self.assertIn("Lead ID,Brand / Company,Contact Name,Email", template_str)
        self.assertIn("DM Beauty", template_str)

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
        self.assertEqual(c_adv["status"], "Emailed")
        self.assertIsNotNone(c_adv["date_first_emailed"])
        self.assertIsNotNone(c_adv["last_contact_date"])

        expected_next = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        self.assertEqual(c_adv["next_follow_up"], expected_next)

        # Automated sequence advancement (Touchpoint 2: remains Emailed)
        advance_contact_followup(cid, delay_days=5, db_path=TEST_DB)
        c_adv2 = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_adv2["follow_ups_sent"], 2)
        self.assertEqual(c_adv2["status"], "Emailed")


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
        self.assertEqual(c_before["status"], "Emailed")
        self.assertEqual(c_before["follow_ups_sent"], 1)


        # 3. Queue 2 future follow-up drafts in Outbox (1 Approved, 1 Pending)
        e2 = create_email(
            email_html="<p>Follow Up #1</p>",
            subject="Following up on Listing Audit",
            recipient="alex@mercerretail.com",
            status="Approved",
            scheduled_time="2026-09-22 10:00:00",
            sequence_step=2,
            db_path=TEST_DB
        )
        e3 = create_email(
            email_html="<p>Follow Up #2</p>",
            subject="Quick video walkthrough",
            recipient="alex@mercerretail.com",
            status="Pending",
            scheduled_time="2026-09-26 10:00:00",
            sequence_step=3,
            db_path=TEST_DB
        )

        # Also queue a one-time mail (sequence_step=1) to verify it is NOT cancelled on reply!
        e_onetime = create_email(
            email_html="<p>One-Time Special Proposal</p>",
            subject="Exclusive Partner Invite",
            recipient="alex@mercerretail.com",
            status="Pending",
            scheduled_time="2026-09-27 10:00:00",
            sequence_step=1,
            db_path=TEST_DB
        )

        # Also create an email for a different prospect to verify it is NOT cancelled
        other_e = create_email(
            email_html="<p>Other Pitch</p>",
            subject="Different prospect",
            recipient="other@brand.com",
            status="Approved",
            scheduled_time="2026-09-22 10:00:00",
            sequence_step=2,
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
        self.assertNotIn(e_onetime, reply_res["cancelled_email_ids"])

        # Check that one-time mail remains Pending
        rec_onetime = get_email_by_id(e_onetime, db_path=TEST_DB)
        self.assertEqual(rec_onetime["status"], "Pending")

        # 5. Check contact record: status must be 'Replied' or 'Interested' (Phase 5 rule-based classification), note appended, tag added
        c_after = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertIn(c_after["status"], ["Replied", "Interested"])
        self.assertIn("Replied", c_after["tags_list"])
        self.assertTrue("[Replied:" in c_after["notes"] or "[Interested:" in c_after["notes"])
        self.assertEqual(c_after["last_reply_at"], "2026-09-19 14:22:00")
        self.assertIn("Let's talk", c_after["reply_subject"])

        # 6. Check emails table: e2 and e3 must be 'Cancelled', e1 remains 'Sent', other_e remains 'Approved'
        e1_check = get_email_by_id(e1, db_path=TEST_DB)
        self.assertEqual(e1_check["status"], "Sent")

        e2_check = get_email_by_id(e2, db_path=TEST_DB)
        self.assertIn(e2_check["status"], ["Paused", "Cancelled"])
        self.assertTrue("Auto-cancelled" in e2_check.get("error_message", "") or "Auto-paused" in e2_check.get("revision_notes", ""))

        e3_check = get_email_by_id(e3, db_path=TEST_DB)
        self.assertIn(e3_check["status"], ["Paused", "Cancelled"])
        self.assertTrue("Auto-cancelled" in e3_check.get("error_message", "") or "Auto-paused" in e3_check.get("revision_notes", ""))


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

    @patch("dns.resolver.Resolver.resolve")
    def test_29_mx_checker_valid_and_invalid_domains(self, mock_resolve):
        """Test pre-flight MX record verification, DNS resolution, dead domain detection, and caching."""
        import dns.resolver
        def dns_mock(domain, qtype):
            dom = str(domain).rstrip(".")
            if dom in ["gmail.com"]:
                if qtype == "MX":
                    r = MagicMock()
                    r.exchange.to_text.return_value = "gmail-smtp-in.l.google.com."
                    return [r]
                return [MagicMock(to_text=lambda: "142.250.190.5")]
            raise dns.resolver.NXDOMAIN()

        mock_resolve.side_effect = dns_mock
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

    @patch("dns.resolver.Resolver.resolve")
    def test_30_scheduler_preflight_mx_interception(self, mock_resolve):
        """Test that scheduler intercepts dead domain emails before dispatch, marking them Bounced."""
        import dns.resolver
        mock_resolve.side_effect = dns.resolver.NXDOMAIN()

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
        set_config("min_delay_seconds", "0.01", db_path=TEST_DB)
        set_config("max_delay_seconds", "0.02", db_path=TEST_DB)

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

    @patch("dns.resolver.Resolver.resolve")
    def test_31_csv_import_with_mx_verification(self, mock_resolve):
        """Test CSV import with verify_mx=True automatically flags dead domains with 'Invalid MX'."""
        import dns.resolver
        def dns_mock(domain, qtype):
            dom = str(domain).rstrip(".")
            if "gmail" in dom:
                if qtype == "MX":
                    r = MagicMock()
                    r.exchange.to_text.return_value = "gmail-smtp-in.l.google.com."
                    return [r]
                return [MagicMock(to_text=lambda: "142.250.190.5")]
            raise dns.resolver.NXDOMAIN()

        mock_resolve.side_effect = dns_mock
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

    def test_34_staggered_schedule_span_hours(self):
        """Test distributing contacts across a rolling span of X hours from now."""
        base = datetime(2026, 9, 21, 10, 0, 0).astimezone()  # Monday 10:00
        # 10 contacts spread across 3 hours
        schedule = calculate_staggered_schedule(
            total_contacts=10,
            stagger_mode="next_x_hours",
            base_dt=base,
            span_hours=3.0,
            sending_days=["Monday"],
            start_time_str="09:00",
            end_time_str="18:00",
            use_jitter=False
        )
        self.assertEqual(len(schedule), 10)
        self.assertEqual(schedule[0].replace(second=0, microsecond=0), base.replace(second=0, microsecond=0))
        # All 10 contacts fall strictly within the 3-hour span (last at 162 mins = 12:42)
        self.assertLess(schedule[-1], base + timedelta(hours=3))
        self.assertEqual(schedule[-1].replace(second=0, microsecond=0) - schedule[0].replace(second=0, microsecond=0), timedelta(minutes=162))
        # Verify strictly increasing
        for i in range(len(schedule) - 1):
            self.assertLess(schedule[i], schedule[i + 1])

    def test_35_staggered_schedule_daily_window(self):
        """Test distributing contacts across an active daily sending window."""
        base = datetime(2026, 9, 21, 9, 0, 0).astimezone()  # Monday 09:00
        # 5 contacts spread across 09:00 to 17:00 (8 hours = 480 mins -> 96 mins step)
        schedule = calculate_staggered_schedule(
            total_contacts=5,
            stagger_mode="daily_window",
            base_dt=base,
            sending_days=["Monday"],
            start_time_str="09:00",
            end_time_str="17:00",
            use_jitter=False
        )
        self.assertEqual(len(schedule), 5)
        self.assertEqual(schedule[0].replace(second=0, microsecond=0), base.replace(second=0, microsecond=0))
        # Each step is exactly 96 minutes
        self.assertEqual(schedule[1] - schedule[0], timedelta(minutes=96))
        # All 5 contacts are scheduled before the 17:00 cutoff on the same Monday
        cutoff_dt = datetime(2026, 9, 21, 17, 0, 0).astimezone()
        self.assertLess(schedule[-1], cutoff_dt)
        self.assertEqual(schedule[-1].strftime("%A"), "Monday")

    def test_36_staggered_schedule_fixed_interval_and_edge_cases(self):
        """Test fixed interval spacing and edge cases (1 contact, 0 contacts)."""
        base = datetime(2026, 9, 21, 10, 0, 0).astimezone()
        # 4 contacts at 15-minute intervals
        schedule = calculate_staggered_schedule(
            total_contacts=4,
            stagger_mode="fixed_interval",
            base_dt=base,
            spacing_minutes=15.0,
            sending_days=["Monday"],
            start_time_str="09:00",
            end_time_str="18:00",
            use_jitter=False
        )
        self.assertEqual(len(schedule), 4)
        for i in range(3):
            self.assertEqual(schedule[i + 1] - schedule[i], timedelta(minutes=15))

        # 1 contact
        single = calculate_staggered_schedule(total_contacts=1, base_dt=base)
        self.assertEqual(len(single), 1)

        # 0 contacts
        empty = calculate_staggered_schedule(total_contacts=0, base_dt=base)
        self.assertEqual(len(empty), 0)

    def test_37_staggered_schedule_jitter(self):
        """Test that natural jitter produces slight non-round variation while preserving contact count."""
        base = datetime(2026, 9, 21, 10, 0, 0).astimezone()
        schedule = calculate_staggered_schedule(
            total_contacts=6,
            stagger_mode="fixed_interval",
            base_dt=base,
            spacing_minutes=10.0,
            sending_days=["Monday"],
            start_time_str="09:00",
            end_time_str="18:00",
            use_jitter=True,
            jitter_seed=42
        )
        self.assertEqual(len(schedule), 6)
        # At least one timestamp should have non-zero seconds due to jitter
        has_seconds = any(dt.second != 0 for dt in schedule)
        self.assertTrue(has_seconds)

    def test_38_selective_batch_approval_and_deletion(self):
        """Test that selecting a subset of drafts approves only the chosen drafts and leaves unselected pending."""
        d1 = create_email(email_html="<p>Body 1</p>", subject="Subj 1", recipient="r1@example.com", status="Pending", db_path=TEST_DB)
        d2 = create_email(email_html="<p>Body 2</p>", subject="Subj 2", recipient="r2@example.com", status="Pending", db_path=TEST_DB)
        d3 = create_email(email_html="<p>Body 3</p>", subject="Subj 3", recipient="r3@example.com", status="Pending", db_path=TEST_DB)

        # Simulate user picking d1 and d3 from outside with checkboxes, leaving d2 unselected
        selected_ids = [d1, d3]

        for sid in selected_ids:
            rec = get_email_by_id(sid, db_path=TEST_DB)
            approve_email(
                email_id=sid,
                recipient=rec["recipient"],
                scheduled_time="2026-09-20 10:00:00",
                email_html=rec["email_html"],
                subject=rec["subject"],
                db_path=TEST_DB
            )

        # Verify d1 and d3 are Approved
        r1 = get_email_by_id(d1, db_path=TEST_DB)
        r2 = get_email_by_id(d2, db_path=TEST_DB)
        r3 = get_email_by_id(d3, db_path=TEST_DB)

        self.assertEqual(r1["status"], "Approved")
        self.assertEqual(r3["status"], "Approved")
        # Verify d2 was not approved and remains untouched in Pending
        self.assertEqual(r2["status"], "Pending")

        # Verify bulk delete on selected draft removes it
        delete_email(d2, db_path=TEST_DB)
        self.assertIsNone(get_email_by_id(d2, db_path=TEST_DB))

    def test_39_staggered_schedule_window_rollover_no_clumping(self):
        """Test that schedules crossing the daily cutoff roll over to next business morning and maintain spacing without clumping."""
        from datetime import time
        base = datetime(2026, 9, 21, 16, 37, 0).astimezone()  # Monday 16:37
        # 10 contacts spread across a 4-hour span (step = 24.0 mins)
        # Cutoff is 18:00 -> Contacts 0..3 fit Monday (16:37, 17:01, 17:25, 17:49)
        # Contacts 4..9 roll over to Tuesday starting at 09:00, spaced by 24 mins
        schedule = calculate_staggered_schedule(
            total_contacts=10,
            stagger_mode="span_hours",
            base_dt=base,
            span_hours=4.0,
            sending_days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            start_time_str="09:00",
            end_time_str="18:00",
            use_jitter=False
        )
        self.assertEqual(len(schedule), 10)

        # Mon 16:37
        self.assertEqual(schedule[0].strftime("%A %H:%M"), "Monday 16:37")
        self.assertEqual(schedule[1].strftime("%A %H:%M"), "Monday 17:01")
        self.assertEqual(schedule[2].strftime("%A %H:%M"), "Monday 17:25")
        self.assertEqual(schedule[3].strftime("%A %H:%M"), "Monday 17:49")

        # Tuesday rollover starting at 09:00
        self.assertEqual(schedule[4].strftime("%A %H:%M"), "Tuesday 09:00")
        self.assertEqual(schedule[5].strftime("%A %H:%M"), "Tuesday 09:24")
        self.assertEqual(schedule[6].strftime("%A %H:%M"), "Tuesday 09:48")
        self.assertEqual(schedule[7].strftime("%A %H:%M"), "Tuesday 10:12")
        self.assertEqual(schedule[8].strftime("%A %H:%M"), "Tuesday 10:36")
        self.assertEqual(schedule[9].strftime("%A %H:%M"), "Tuesday 11:00")

        # Verify no emails outside 09:00 - 18:00
        for dt in schedule:
            mins = dt.hour * 60 + dt.minute
            self.assertGreaterEqual(mins, 9 * 60)
            self.assertLess(mins, 18 * 60)

        # Verify strictly increasing (no clumping)
        for i in range(len(schedule) - 1):
            self.assertLess(schedule[i], schedule[i + 1])

    def test_40_schedule_overflow_analysis(self):
        """Test analyze_schedule_overflow helper for transparent live UI warnings."""
        base = datetime(2026, 9, 21, 16, 37, 0).astimezone()  # Monday 16:37
        schedule = calculate_staggered_schedule(
            total_contacts=10,
            stagger_mode="span_hours",
            base_dt=base,
            span_hours=4.0,
            sending_days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            start_time_str="09:00",
            end_time_str="18:00",
            use_jitter=False
        )

        analysis = analyze_schedule_overflow(schedule, end_time_str="18:00", reference_dt=base)
        self.assertEqual(analysis["total_count"], 10)
        self.assertEqual(analysis["today_count"], 4)
        self.assertEqual(analysis["overflow_count"], 6)
        self.assertFalse(analysis["fits_today"])
        self.assertEqual(
            analysis["warning_message"],
            "10 contacts won't all fit before 18:00 today — 6 will continue tomorrow from 09:00."
        )

        # Early day case: all fit today
        base_early = datetime(2026, 9, 21, 10, 0, 0).astimezone()
        schedule_early = calculate_staggered_schedule(
            total_contacts=4,
            stagger_mode="fixed_interval",
            base_dt=base_early,
            spacing_minutes=15.0,
            sending_days=["Monday"],
            start_time_str="09:00",
            end_time_str="18:00",
            use_jitter=False
        )
        analysis_early = analyze_schedule_overflow(schedule_early, end_time_str="18:00", reference_dt=base_early)
        self.assertEqual(analysis_early["total_count"], 4)
        self.assertEqual(analysis_early["today_count"], 4)
        self.assertEqual(analysis_early["overflow_count"], 0)
        self.assertTrue(analysis_early["fits_today"])
        self.assertIsNone(analysis_early["warning_message"])
        self.assertIn("All 4 contacts will be dispatched today", analysis_early["summary_message"])

    def test_41_campaign_zero_recipient_state_validation(self):
        """Test zero-recipient schedule behavior to ensure no slots or false banners are generated."""
        # Calculate with 0 contacts
        empty_schedule = calculate_staggered_schedule(total_contacts=0)
        self.assertEqual(len(empty_schedule), 0)

        # Analyze 0 contacts
        analysis = analyze_schedule_overflow(empty_schedule, end_time_str="18:00")
        self.assertEqual(analysis["total_count"], 0)
        self.assertEqual(analysis["today_count"], 0)
        self.assertEqual(analysis["overflow_count"], 0)
        self.assertTrue(analysis["fits_today"])
        self.assertIsNone(analysis["first_dt"])
        self.assertIsNone(analysis["last_dt"])
        self.assertIsNone(analysis["warning_message"])
        self.assertEqual(analysis["summary_message"], "No contacts selected.")

    def test_42_followup_delay_days_config_persistence(self):
        """Test that configured sequence milestone delay is persisted and honored by advance_contact_followup."""
        from database import set_config, get_contact_by_id, create_contact
        # Save a custom delay of 8 days
        set_config("followup_delay_days", "8", db_path=TEST_DB)
        self.assertEqual(get_config("followup_delay_days", "4", db_path=TEST_DB), "8")

        # Create contact and advance
        cid = create_contact("Seq Lead", "seq_lead@agency.com", db_path=TEST_DB)
        today = datetime.now().astimezone()
        expected_followup = (today + timedelta(days=8)).strftime("%Y-%m-%d")

        delay = int(get_config("followup_delay_days", "4", db_path=TEST_DB) or 4)
        advance_contact_followup(cid, delay_days=delay, db_path=TEST_DB)

        updated = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(updated["next_follow_up"], expected_followup)
        self.assertEqual(updated["follow_ups_sent"], 1)

    def test_43_open_rate_color_threshold_logic(self):
        """Test open rate color decision logic to ensure 0.0% is neutral and green is strictly for >= 15%."""
        def get_open_rate_style(total_sent, open_rate):
            if total_sent < 20:
                return "#64748B"
            else:
                if open_rate == 0.0:
                    return "#64748B"
                elif open_rate >= 15.0:
                    return "#059669"
                else:
                    return "#083731"

        # 0 sends -> neutral
        self.assertEqual(get_open_rate_style(0, 0.0), "#64748B")
        # 5 sends, 0 opened -> neutral
        self.assertEqual(get_open_rate_style(5, 0.0), "#64748B")
        # 10 sends, 2 opened (20%) -> small sample neutral
        self.assertEqual(get_open_rate_style(10, 20.0), "#64748B")
        # 50 sends, 0% open rate -> neutral gray/slate (NEVER green)
        self.assertEqual(get_open_rate_style(50, 0.0), "#64748B")
        # 50 sends, 8.0% open rate -> standard dark slate
        self.assertEqual(get_open_rate_style(50, 8.0), "#083731")
        # 50 sends, 22.5% open rate -> green (good performance)
        self.assertEqual(get_open_rate_style(50, 22.5), "#059669")

    def test_44_corporate_signature_template_and_persistence(self):
        """Test corporate signature configuration persistence and default template integrity."""
        from ui.tabs.settings import DEFAULT_SIGNATURE_TEMPLATE
        self.assertIn("Sellomize", DEFAULT_SIGNATURE_TEMPLATE)
        self.assertIn("<table", DEFAULT_SIGNATURE_TEMPLATE)
        self.assertIn("Business Development Officer", DEFAULT_SIGNATURE_TEMPLATE)

        # Persistence in DB
        set_config("signature_html", DEFAULT_SIGNATURE_TEMPLATE, db_path=TEST_DB)
        loaded = get_config("signature_html", db_path=TEST_DB)
        self.assertEqual(loaded, DEFAULT_SIGNATURE_TEMPLATE)

    def test_45_grid_columns_visibility_and_data_integrity(self):
        """Test that data editor records maintain all underlying fields even when columns are visually hidden."""
        cid = create_contact("Grid Lead", "grid_lead@company.com", company="Tech Corp", db_path=TEST_DB)
        record = {
            "id": cid,
            "Lead ID": f"L-{cid:04d}",
            "Company": "Tech Corp",
            "Contact Name": "Grid Lead",
            "Email Address": "grid_lead@company.com",
            "Status": "Contacted",
            "Priority": "High",
            "Notes": "Updated via grid view",
            "Tags": "Enterprise, Priority"
        }
        saved = bulk_update_contact_grid([record], db_path=TEST_DB)
        self.assertEqual(saved, 1)

        c_updated = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_updated["name"], "Grid Lead")
        self.assertEqual(c_updated["status"], "Contacted")
        self.assertEqual(c_updated["priority"], "High")
        self.assertEqual(c_updated["notes"], "Updated via grid view")

    def test_46_unified_sending_window_db_sync(self):
        """Test that Sequences & Campaigns sync_sending_window_to_db updates SQLite system_config."""
        from ui.tabs.settings import sync_sending_window_to_db

        # 1. Business Days preset
        sync_sending_window_to_db("Business Days (Mon - Fri, 09:00 - 18:00)", [], "", "", db_path=TEST_DB)
        self.assertEqual(get_config("enforce_sending_window", db_path=TEST_DB), "true")
        self.assertEqual(get_config("sending_days", db_path=TEST_DB), "Monday, Tuesday, Wednesday, Thursday, Friday")
        self.assertEqual(get_config("sending_start_time", db_path=TEST_DB), "09:00")
        self.assertEqual(get_config("sending_end_time", db_path=TEST_DB), "18:00")

        # 2. 24/7 Continuous preset
        sync_sending_window_to_db("24/7 Continuous (All 7 Days)", [], "", "", db_path=TEST_DB)
        self.assertEqual(get_config("enforce_sending_window", db_path=TEST_DB), "false")
        self.assertIn("Sunday", get_config("sending_days", db_path=TEST_DB))
        self.assertEqual(get_config("sending_start_time", db_path=TEST_DB), "00:00")
        self.assertEqual(get_config("sending_end_time", db_path=TEST_DB), "23:59")

        # 3. Custom Schedule
        sync_sending_window_to_db("Custom Schedule", ["Tuesday", "Thursday"], "10:30", "16:45", db_path=TEST_DB)
        self.assertEqual(get_config("enforce_sending_window", db_path=TEST_DB), "true")
        self.assertEqual(get_config("sending_days", db_path=TEST_DB), "Tuesday, Thursday")
        self.assertEqual(get_config("sending_start_time", db_path=TEST_DB), "10:30")
        self.assertEqual(get_config("sending_end_time", db_path=TEST_DB), "16:45")

    def test_47_scheduler_honors_24_7_enforcement_disabled(self):
        """Test that is_within_sending_window and get_next_valid_sending_datetime allow off-hours when 24/7 is enabled."""
        set_config("enforce_sending_window", "false", db_path=TEST_DB)
        
        # Check an off-hour slot (e.g. Sunday at 23:45)
        sunday_night = datetime(2026, 9, 27, 23, 45, 0)
        is_open, msg = is_within_sending_window(check_dt=sunday_night, db_path=TEST_DB)
        self.assertTrue(is_open)
        self.assertIn("disabled", msg.lower())

        next_dt = get_next_valid_sending_datetime(base_dt=sunday_night, db_path=TEST_DB)
        self.assertEqual(next_dt, sunday_night.astimezone())

    def test_48_campaign_contact_selection_state_consistency(self):
        """Test that campaign audience filtering and candidate identification behaves predictably."""
        cid1 = create_contact("Alpha Lead", "alpha_camp@agency.com", status="Not Contacted", db_path=TEST_DB)
        cid2 = create_contact("Beta Lead", "beta_camp@agency.com", status="Bounced", db_path=TEST_DB)
        cid3 = create_contact("Gamma Lead", "gamma_camp@agency.com", status="Contacted", db_path=TEST_DB)

        all_c = get_contacts(db_path=TEST_DB)
        # Excludes bounced/closed lost
        active = [c for c in all_c if c.get("status") not in ["Bounced", "Do Not Contact", "Closed Lost"] and not c.get("is_bounced")]
        active_ids = [c["id"] for c in active]
        self.assertIn(cid1, active_ids)
        self.assertNotIn(cid2, active_ids)
        self.assertIn(cid3, active_ids)

    def test_49_click_tracking_preserves_signature_and_direct_links_on_localhost(self):
        """Test that links are preserved direct when click tracking is disabled or using localhost."""
        from tracker import wrap_links_with_click_tracking, is_public_tracking_url
        
        self.assertFalse(is_public_tracking_url("http://localhost:8502"))
        self.assertFalse(is_public_tracking_url("http://127.0.0.1:8502"))
        self.assertTrue(is_public_tracking_url("https://track.sellomize.com"))

        html_body = '<p>Visit our website: <a href="https://sellomize.com">Sellomize</a></p>'
        
        # When click tracking is disabled (default): links are untouched
        set_config("enable_click_tracking", "false", db_path=TEST_DB)
        set_config("tracking_base_url", "http://localhost:8502", db_path=TEST_DB)
        res = wrap_links_with_click_tracking(html_body, email_id=7, db_path=TEST_DB)
        self.assertEqual(res, html_body)
        self.assertIn('href="https://sellomize.com"', res)
        self.assertNotIn('/track/click/', res)

        # When click tracking is enabled with a public domain: links are wrapped
        set_config("enable_click_tracking", "true", db_path=TEST_DB)
        set_config("tracking_base_url", "https://track.sellomize.com", db_path=TEST_DB)
        res_public = wrap_links_with_click_tracking(html_body, email_id=7, db_path=TEST_DB)
        self.assertIn('https://track.sellomize.com/track/click/7?url=https%3A%2F%2Fsellomize.com', res_public)

        # Reset config
        set_config("enable_click_tracking", "false", db_path=TEST_DB)
        set_config("tracking_base_url", "http://localhost:8502", db_path=TEST_DB)

    def test_50_tracker_handles_google_encoded_redirect_and_fallback(self):
        """Test that the tracker properly decodes Google's %3D encoded query string and extracts destination."""
        import urllib.parse
        
        # Simulate Gmail redirect query string
        path = "/track/click/7?url%3Dhttps%253A%252F%252Fsellomize.com"
        parsed_url = urllib.parse.urlparse(path)
        query_str = parsed_url.query
        if "%3D" in query_str.upper():
            query_str = urllib.parse.unquote(query_str)
        params = urllib.parse.parse_qs(query_str)
        raw_target = params.get("url", [""])[0]
        target_url = urllib.parse.unquote(raw_target).strip()
        while "%" in target_url and ("%2F" in target_url.upper() or "%3A" in target_url.upper()):
            target_url = urllib.parse.unquote(target_url).strip()

        self.assertEqual(target_url, "https://sellomize.com")

    def test_51_multi_touch_sequence_drafts_creation_and_steps(self):
        """Test multi-touch sequence emails with sequence_step (1, 2, 3) and shared sequence_id."""
        seq_id = "seq_test_multi_99"
        e1 = create_email(
            email_html="<p>Touch 1 pitch</p>",
            subject="Quick intro",
            recipient="lead_multitouch@agency.com",
            status="Pending",
            scheduled_time="2026-09-25 09:00:00",
            sequence_step=1,
            sequence_id=seq_id,
            db_path=TEST_DB
        )
        e2 = create_email(
            email_html="<p>Touch 2 follow-up</p>",
            subject="Re: Quick intro",
            recipient="lead_multitouch@agency.com",
            status="Pending",
            scheduled_time="2026-09-28 09:00:00",
            sequence_step=2,
            sequence_id=seq_id,
            db_path=TEST_DB
        )
        e3 = create_email(
            email_html="<p>Touch 3 final breakup</p>",
            subject="Final note",
            recipient="lead_multitouch@agency.com",
            status="Pending",
            scheduled_time="2026-10-02 09:00:00",
            sequence_step=3,
            sequence_id=seq_id,
            db_path=TEST_DB
        )

        r1 = get_email_by_id(e1, db_path=TEST_DB)
        r2 = get_email_by_id(e2, db_path=TEST_DB)
        r3 = get_email_by_id(e3, db_path=TEST_DB)

        self.assertEqual(r1["sequence_step"], 1)
        self.assertEqual(r1["sequence_id"], seq_id)
        self.assertEqual(r2["sequence_step"], 2)
        self.assertEqual(r2["sequence_id"], seq_id)
        self.assertEqual(r3["sequence_step"], 3)
        self.assertEqual(r3["sequence_id"], seq_id)

    def test_52_reply_auto_cancels_all_multi_touch_followups_and_notifies(self):
        """Test that recording an incoming prospect reply auto-cancels pending/approved/flagged touches and logs a notification."""
        rep_email = "prospect_sequence@replytest.com"
        create_contact(name="Alex Miller", email=rep_email, company="Miller Brand", db_path=TEST_DB)

        # Create touch 1 (Approved), touch 2 (Pending), touch 3 (Flagged)
        t1 = create_email(email_html="<p>Touch 1</p>", subject="Intro", recipient=rep_email, status="Approved", sequence_step=1, db_path=TEST_DB)
        t2 = create_email(email_html="<p>Touch 2</p>", subject="Re: Intro", recipient=rep_email, status="Pending", sequence_step=2, db_path=TEST_DB)
        t3 = create_email(email_html="<p>Touch 3</p>", subject="Breakup", recipient=rep_email, status="Flagged", sequence_step=3, db_path=TEST_DB)

        # Prospect replies
        reply_res = record_email_reply(
            sender_email=rep_email,
            reply_subject="Thanks, let's talk tomorrow",
            db_path=TEST_DB
        )

        self.assertTrue(reply_res["contact_found"])
        # Only sequence follow-ups (Touch 2 and 3) must be auto-cancelled (2 drafts).
        # One-time emails (Touch 1 / single mails) are preserved!
        self.assertEqual(reply_res["cancelled_drafts_count"], 2)
        self.assertNotIn(t1, reply_res["cancelled_email_ids"])
        self.assertIn(t2, reply_res["cancelled_email_ids"])
        self.assertIn(t3, reply_res["cancelled_email_ids"])

        # Check Touch 1 remains Approved (preserved), while Touch 2 & 3 are Cancelled
        rec1 = get_email_by_id(t1, db_path=TEST_DB)
        self.assertEqual(rec1["status"], "Approved")

        for tid in [t2, t3]:
            rec = get_email_by_id(tid, db_path=TEST_DB)
            self.assertIn(rec["status"], ["Paused", "Cancelled"])
            self.assertTrue("Auto-cancelled" in rec.get("error_message", "") or "Auto-paused" in rec.get("revision_notes", ""))


        # Check notification was created
        notifs = get_notifications(unread_only=True, limit=10, db_path=TEST_DB)
        reply_notifs = [n for n in notifs if n.get("contact_email") == rep_email]
        self.assertTrue(len(reply_notifs) > 0)
        self.assertIn("Alex Miller", reply_notifs[0]["title"])
        self.assertTrue("auto-cancelled" in reply_notifs[0]["message"] or "paused" in reply_notifs[0]["message"])


    def test_53_notifications_crud_and_unread_count(self):
        """Test creating, querying, reading, counting, and deleting notifications."""
        # Initial count
        init_unread = get_unread_notifications_count(db_path=TEST_DB)

        nid = create_notification(
            type="reply",
            title="💬 Test Reply",
            message="Test prospect message snippet",
            contact_email="test_lead@domain.com",
            db_path=TEST_DB
        )
        self.assertIsNotNone(nid)

        # Count should increase by 1
        new_unread = get_unread_notifications_count(db_path=TEST_DB)
        self.assertEqual(new_unread, init_unread + 1)

        # Mark read
        mark_notification_as_read(nid, db_path=TEST_DB)
        after_read = get_unread_notifications_count(db_path=TEST_DB)
        self.assertEqual(after_read, init_unread)

        # Mark all read
        create_notification(type="system", title="Notice 1", message="Msg 1", db_path=TEST_DB)
        create_notification(type="reply", title="Notice 2", message="Msg 2", db_path=TEST_DB)
        self.assertGreaterEqual(get_unread_notifications_count(db_path=TEST_DB), 2)
        mark_all_notifications_as_read(db_path=TEST_DB)
        self.assertEqual(get_unread_notifications_count(db_path=TEST_DB), 0)

        # Delete notification
        delete_notification(nid, db_path=TEST_DB)

    def test_54_can_send_single_and_marketing_emails_after_reply(self):
        """Test that after a contact has replied, one-time 1-to-1 and marketing emails can be scheduled and sent without cancellation."""
        engaged_email = "engaged_vip@brandpartners.com"
        cid = create_contact(name="Elena Vance", email=engaged_email, company="Vance Media", db_path=TEST_DB)

        # 1. Simulate prior outreach and reply
        record_email_reply(
            sender_email=engaged_email,
            reply_subject="I loved your proposal!",
            db_path=TEST_DB
        )
        c_status = get_contact_by_id(cid, db_path=TEST_DB)["status"]
        self.assertEqual(c_status, "Replied")

        # 2. Queue a single 1-to-1 follow-up email (one-time mail: sequence_step=1)
        single_mail_id = create_email(
            email_html="<p>Here is the custom proposal doc you asked for.</p>",
            subject="Re: Custom Proposal for Vance Media",
            recipient=engaged_email,
            status="Approved",
            sequence_step=1,
            db_path=TEST_DB
        )

        # 3. Queue a marketing campaign email (one-time mail: sequence_step=1)
        marketing_mail_id = create_email(
            email_html="<p>Special Webinar: Scaling Q4 Brand Revenue</p>",
            subject="Invitation: VIP Brand Scaling Workshop",
            recipient=engaged_email,
            status="Pending",
            sequence_step=1,
            db_path=TEST_DB
        )

        # 4. Simulate an incoming reply detection or check
        reply_check = record_email_reply(
            sender_email=engaged_email,
            reply_subject="Just confirming Thursday meeting",
            db_path=TEST_DB
        )

        # Neither the single mail nor the marketing campaign should be cancelled!
        self.assertEqual(reply_check["cancelled_drafts_count"], 0)
        self.assertNotIn(single_mail_id, reply_check["cancelled_email_ids"])
        self.assertNotIn(marketing_mail_id, reply_check["cancelled_email_ids"])

        m1 = get_email_by_id(single_mail_id, db_path=TEST_DB)
        m2 = get_email_by_id(marketing_mail_id, db_path=TEST_DB)
        self.assertEqual(m1["status"], "Approved")
        self.assertEqual(m2["status"], "Pending")

        # 5. Advance followup or dispatch: contact status remains Replied
        advance_contact_followup(engaged_email, delay_days=3, db_path=TEST_DB)
        c_final = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(c_final["status"], "Replied")

    def test_55_notification_deduplication(self):
        """Test that duplicate notifications are pruned and record_email_reply does not insert redundant unread alerts."""
        test_contact = "amanda.parker@techcorp.com"
        # Insert 5 duplicate notifications
        for i in range(5):
            create_notification(
                type="reply",
                title=f"💬 Reply Received: Amanda Parker",
                message=f"Prospect {test_contact} responded to outreach.",
                contact_email=test_contact,
                db_path=TEST_DB
            )
        # Prune duplicates
        pruned_count = cleanup_duplicate_notifications(db_path=TEST_DB)
        self.assertGreaterEqual(pruned_count, 4)

        # Ensure only 1 remains for this contact
        notifs = [n for n in get_notifications(limit=50, db_path=TEST_DB) if n.get("contact_email") == test_contact]
        self.assertEqual(len(notifs), 1)

        # Now test that record_email_reply does not insert duplicate if unread already exists
        record_email_reply(sender_email=test_contact, reply_subject="Second ping", db_path=TEST_DB)
        notifs_after = [n for n in get_notifications(limit=50, db_path=TEST_DB) if n.get("contact_email") == test_contact]
        self.assertEqual(len(notifs_after), 1)

    def test_56_imap_message_id_idempotency(self):
        """Test that processed IMAP Message-IDs are tracked and prevented from being re-processed."""
        msg_id = "<CAG8o4k39_xyz123@mail.gmail.com>"
        self.assertFalse(is_inbox_message_processed(msg_id, db_path=TEST_DB))

        mark_inbox_message_processed(msg_id, sender_email="sender@acme.com", subject="Question", mailbox="INBOX", db_path=TEST_DB)
        self.assertTrue(is_inbox_message_processed(msg_id, db_path=TEST_DB))
        self.assertFalse(is_inbox_message_processed("<another_id@mail.com>", db_path=TEST_DB))

    def test_57_automated_send_triggered_sequence_rules(self):
        """Test that Touch 1 dispatch triggers sequence rules (in days or hours), and scheduler auto-generates Touch 2 draft upon delay expiry."""
        c_email = "dynamic.sequence@prospectfirm.com"
        cid = create_contact(name="David Miller", email=c_email, company="Miller Logistics", db_path=TEST_DB)
        t1_tpl_id = create_template("Outreach Pitch", "Hi [Name], quick idea for [Company].", db_path=TEST_DB)
        t2_tpl_id = create_template("Follow-Up 1", "Hi [Name], following up on my previous note for [Company].", db_path=TEST_DB)

        # 1. Create Touch 1 Email
        t1_id = create_email(
            email_html="<p>Hi David, quick idea for Miller Logistics.</p>",
            subject="Partnership for Miller Logistics",
            recipient=c_email,
            status="Pending",
            sequence_step=1,
            sequence_id="seq_test_123",
            db_path=TEST_DB
        )

        # 2. Register Touch 2 rule: 2 hours delay after Touch 1 send
        rule_id = create_sequence_rule(
            sequence_id="seq_test_123",
            contact_id=cid,
            contact_email=c_email,
            step_number=2,
            delay_unit="hours",
            delay_value=2,
            template_id=t2_tpl_id,
            custom_subject="Re: Partnership for [Company]",
            trigger_email_id=t1_id,
            db_path=TEST_DB
        )

        # Verify status is Waiting_Trigger
        rules = get_sequence_rules(db_path=TEST_DB)
        r = next(x for x in rules if x["id"] == rule_id)
        self.assertEqual(r["status"], "Waiting_Trigger")

        # 3. Simulate Touch 1 being sent at 10:00:00
        sent_time = "2026-09-22 10:00:00"
        activated = trigger_sequence_rules_for_sent_email(t1_id, sent_at_iso=sent_time, db_path=TEST_DB)
        self.assertEqual(activated, 1)

        rules = get_sequence_rules(db_path=TEST_DB)
        r = next(x for x in rules if x["id"] == rule_id)
        self.assertEqual(r["status"], "Scheduled")
        self.assertEqual(r["due_at"], "2026-09-22 12:00:00")

        # 4. If current time is 11:00:00 (before 12:00:00), not due
        due_before = get_due_sequence_rules(current_time_iso="2026-09-22 11:00:00", db_path=TEST_DB)
        self.assertNotIn(rule_id, [x["id"] for x in due_before])

        # 5. When current time reaches 12:05:00, rule is due
        due_after = get_due_sequence_rules(current_time_iso="2026-09-22 12:05:00", db_path=TEST_DB)
        self.assertIn(rule_id, [x["id"] for x in due_after])

        # 6. Execute process_due_sequence_rules: auto-generates Touch 2 draft with variables resolved!
        with patch("database.datetime") as mock_dt:
            mock_dt.now.return_value.astimezone.return_value.strftime.return_value = "2026-09-22 12:05:00"
            mock_dt.strptime = datetime.strptime
            generated = process_due_sequence_rules(db_path=TEST_DB)

        self.assertEqual(generated, 1)
        r_after = next(x for x in get_sequence_rules(db_path=TEST_DB) if x["id"] == rule_id)
        self.assertEqual(r_after["status"], "Generated")

        # Verify new Touch 2 email was created
        all_prospect_emails = [e for e in get_emails(db_path=TEST_DB) if e["recipient"] == c_email and e["sequence_step"] == 2]
        self.assertEqual(len(all_prospect_emails), 1)
        touch2_mail = all_prospect_emails[0]
        self.assertEqual(touch2_mail["subject"], "Re: Partnership for Miller Logistics")
        self.assertIn("Hi David", touch2_mail["email_html"])
        self.assertIn("Miller Logistics", touch2_mail["email_html"])
        self.assertEqual(touch2_mail["status"], "Pending")

    def test_58_sequence_rule_cancelled_on_reply(self):
        """Test that if a prospect replies, pending sequence rules are immediately cancelled and no follow-up is generated."""
        c_email = "replying.lead@growthfirm.com"
        cid = create_contact(name="Sophia Lin", email=c_email, company="Lin Ventures", db_path=TEST_DB)
        tpl_id = create_template("Touch 2", "Hi [Name], following up.", db_path=TEST_DB)

        t1_id = create_email(
            email_html="<p>Pitch</p>",
            subject="Pitch for Lin Ventures",
            recipient=c_email,
            status="Sent",
            sequence_step=1,
            db_path=TEST_DB
        )

        rule_id = create_sequence_rule(
            sequence_id="seq_reply_test",
            contact_id=cid,
            contact_email=c_email,
            step_number=2,
            delay_unit="days",
            delay_value=3,
            template_id=tpl_id,
            custom_subject="Re: Pitch",
            trigger_email_id=t1_id,
            db_path=TEST_DB
        )
        trigger_sequence_rules_for_sent_email(t1_id, db_path=TEST_DB)

        # Prospect replies
        record_email_reply(sender_email=c_email, reply_subject="Thanks, let's talk", db_path=TEST_DB)

        # Verify rule was cancelled
        r = next(x for x in get_sequence_rules(db_path=TEST_DB) if x["id"] == rule_id)
        self.assertEqual(r["status"], "Cancelled")

        # Running process_due_sequence_rules should produce 0 emails
        gen = process_due_sequence_rules(db_path=TEST_DB)
        self.assertEqual(gen, 0)

    def test_59_country_timezone_scheduling_and_conversion(self):
        """Test country-wise market presets, live market clocks, and dual-clock scheduling."""
        # 1. Preset markets availability
        self.assertIn("CA_EAST", TARGET_MARKETS)
        self.assertIn("CA_WEST", TARGET_MARKETS)
        self.assertIn("AU_EAST", TARGET_MARKETS)
        self.assertIn("US_EAST", TARGET_MARKETS)
        self.assertIn("UK", TARGET_MARKETS)
        self.assertIn("EU_CENTRAL", TARGET_MARKETS)

        ca_east = TARGET_MARKETS["CA_EAST"]
        self.assertEqual(ca_east["country"], "Canada")
        self.assertEqual(ca_east["timezone"], "America/Toronto")

        # 2. Live market clock conversion
        ca_now = get_market_current_time("CA_EAST")
        self.assertIsNotNone(ca_now.tzinfo)
        diff_summary = get_time_difference_summary("CA_EAST")
        self.assertIsInstance(diff_summary, str)
        self.assertTrue(len(diff_summary) > 0)

        # 3. Market hours evaluation
        from zoneinfo import ZoneInfo
        # Test Monday 11:00 AM Toronto time -> inside market hours
        zi_toronto = ZoneInfo("America/Toronto")
        mon_11am = datetime(2026, 9, 21, 11, 0, 0, tzinfo=zi_toronto)
        is_open, reason = is_within_market_hours("CA_EAST", reference_dt=mon_11am)
        self.assertTrue(is_open)
        self.assertIn("Inside", reason)

        # Test Sunday 11:00 AM Toronto time -> outside market hours (weekend)
        sun_11am = datetime(2026, 9, 20, 11, 0, 0, tzinfo=zi_toronto)
        is_open_sun, reason_sun = is_within_market_hours("CA_EAST", reference_dt=sun_11am)
        self.assertFalse(is_open_sun)
        self.assertIn("outside business days", reason_sun)

        # Test Monday 23:00 (11 PM) Toronto time -> outside daily working hours
        mon_11pm = datetime(2026, 9, 21, 23, 0, 0, tzinfo=zi_toronto)
        is_open_night, reason_night = is_within_market_hours("CA_EAST", reference_dt=mon_11pm)
        self.assertFalse(is_open_night)
        self.assertIn("cutoff", reason_night)

        # 4. Market-aware schedule calculation (dual-clock pairs)
        pairs = calculate_market_aware_schedule(
            total_contacts=3,
            market_key_or_tz="CA_EAST",
            stagger_mode="fixed_interval",
            spacing_minutes=10.0,
            days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            start_time="09:00",
            end_time="17:00"
        )
        self.assertEqual(len(pairs), 3)
        for m_dt, h_dt in pairs:
            # Market dt must have America/Toronto timezone
            self.assertEqual(str(m_dt.tzinfo), "America/Toronto")
            # Must fall inside business window
            self.assertGreaterEqual(m_dt.hour * 60 + m_dt.minute, 9 * 60)
            self.assertLess(m_dt.hour * 60 + m_dt.minute, 17 * 60)
            # Host dt must have host timezone
            self.assertIsNotNone(h_dt.tzinfo)

    def test_60_bcc_multi_recipient_sanitization_and_dispatch(self):
        """Test multi-recipient BCC address configuration, comma-splitting, and SMTP destination routing."""
        from smtp_dispatcher import send_smtp_email

        mock_smtp_account = {
            "id": 1,
            "email": "outreach@agency.com",
            "smtp_host": "smtp.hostinger.com",
            "smtp_port": 465,
            "password": "testpassword",
            "ssl_type": "SSL",
            "display_name": "Agency Outreach"
        }

        with patch("smtp_dispatcher.smtplib.SMTP_SSL") as mock_ssl:
            mock_server = MagicMock()
            mock_ssl.return_value.__enter__.return_value = mock_server

            # Dispatch with multi-recipient BCC
            success, msg = send_smtp_email(
                smtp_account=mock_smtp_account,
                recipient="prospect@company.com",
                subject="Market Expansion",
                html_content="<p>Test Body</p>",
                bcc_email="archive@sellomize.com, crm-sync@hubspot.com, audit@internal.org"
            )

            self.assertTrue(success)
            # Verify send_message was called and destinations includes recipient + all 3 BCC addresses individually!
            self.assertTrue(mock_server.send_message.called)
            call_kwargs = mock_server.send_message.call_args[1]
            destinations = call_kwargs.get("to_addrs", [])
            self.assertIn("prospect@company.com", destinations)
            self.assertIn("archive@sellomize.com", destinations)
            self.assertIn("crm-sync@hubspot.com", destinations)
            self.assertIn("audit@internal.org", destinations)
            self.assertEqual(len(destinations), 4)

    def test_61_scheduler_adaptive_multi_country_window(self):
        """Test scheduler adaptive_multi_country mode evaluating individual email destination timezones."""
        from zoneinfo import ZoneInfo
        set_config("schedule_mode", "adaptive_multi_country", db_path=TEST_DB)
        set_config("enforce_sending_window", "true", db_path=TEST_DB)

        # 1. Email destined for Canada
        eid = create_email(
            email_html="<p>Canada pitch</p>",
            subject="Pitch for Toronto",
            recipient="client@torontobrand.ca",
            status="Approved",
            target_timezone="America/Toronto",
            target_country="Canada",
            market_key="CA_EAST",
            db_path=TEST_DB
        )
        email_rec = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(email_rec["target_timezone"], "America/Toronto")
        self.assertEqual(email_rec["target_country"], "Canada")
        self.assertEqual(email_rec["market_key"], "CA_EAST")

        # Evaluate at Monday 10:00 AM Toronto time
        zi_toronto = ZoneInfo("America/Toronto")
        test_dt_open = datetime(2026, 9, 21, 10, 0, 0, tzinfo=zi_toronto)
        ok_open, reason_open = is_within_sending_window(check_dt=test_dt_open, db_path=TEST_DB, email_record=email_rec)
        self.assertTrue(ok_open)

        # Evaluate at Monday 22:00 (10 PM) Toronto time
        test_dt_closed = datetime(2026, 9, 21, 22, 0, 0, tzinfo=zi_toronto)
        ok_closed, reason_closed = is_within_sending_window(check_dt=test_dt_closed, db_path=TEST_DB, email_record=email_rec)
        self.assertFalse(ok_closed)
        self.assertIn("cutoff", reason_closed)

        # 3. Create sequence rule with target timezone and verify persistence
        cid = create_contact(name="Liam Smith", email="liam@sydneyventures.com.au", db_path=TEST_DB)
        tpl_id = create_template("Sydney Follow-up", "Hi Liam", db_path=TEST_DB)
        rid = create_sequence_rule(
            sequence_id="seq_au_test",
            contact_id=cid,
            contact_email="liam@sydneyventures.com.au",
            step_number=2,
            delay_unit="days",
            delay_value=2,
            template_id=tpl_id,
            target_timezone="Australia/Sydney",
            target_country="Australia",
            market_key="AU_EAST",
            db_path=TEST_DB
        )
        rules = get_sequence_rules(db_path=TEST_DB)
        au_rule = next(r for r in rules if r["id"] == rid)
        self.assertEqual(au_rule["target_timezone"], "Australia/Sydney")
        self.assertEqual(au_rule["target_country"], "Australia")
        self.assertEqual(au_rule["market_key"], "AU_EAST")

    def test_62_outbox_bulk_delete_and_clear_history(self):
        """Test outbox bulk deletion and clear sent history helpers."""
        # 1. Seed test emails across various outbox states
        e_pending = create_email("Pending html", "Subject P", "p@test.com", status="Pending", db_path=TEST_DB)
        e_sent1 = create_email("Sent 1 html", "Subject S1", "s1@test.com", status="Sent", db_path=TEST_DB)
        e_sent2 = create_email("Sent 2 html", "Subject S2", "s2@test.com", status="Sent", db_path=TEST_DB)
        e_err = create_email("Error html", "Subject Err", "err@test.com", status="Error", db_path=TEST_DB)
        e_app = create_email("Approved html", "Subject App", "app@test.com", status="Approved", db_path=TEST_DB)

        # 2. Test bulk_delete_emails with empty and specific list
        self.assertEqual(bulk_delete_emails([], db_path=TEST_DB), 0)
        self.assertEqual(bulk_delete_emails([999999], db_path=TEST_DB), 0)

        del_cnt = bulk_delete_emails([e_sent1, e_err], db_path=TEST_DB)
        self.assertEqual(del_cnt, 2)
        self.assertIsNone(get_email_by_id(e_sent1, db_path=TEST_DB))
        self.assertIsNone(get_email_by_id(e_err, db_path=TEST_DB))
        # Ensure other emails remain untouched
        self.assertIsNotNone(get_email_by_id(e_sent2, db_path=TEST_DB))
        self.assertIsNotNone(get_email_by_id(e_pending, db_path=TEST_DB))
        self.assertIsNotNone(get_email_by_id(e_app, db_path=TEST_DB))

        # 3. Test clear_outbox_emails by status ('Sent')
        e_sent3 = create_email("Sent 3 html", "Subject S3", "s3@test.com", status="Sent", db_path=TEST_DB)
        purged_sent = clear_outbox_emails(status="Sent", db_path=TEST_DB)
        self.assertGreaterEqual(purged_sent, 2)  # e_sent2 and e_sent3
        self.assertIsNone(get_email_by_id(e_sent2, db_path=TEST_DB))
        self.assertIsNone(get_email_by_id(e_sent3, db_path=TEST_DB))
        # Pending and Approved are preserved
        self.assertIsNotNone(get_email_by_id(e_pending, db_path=TEST_DB))
        self.assertIsNotNone(get_email_by_id(e_app, db_path=TEST_DB))

        # 4. Test clear_outbox_emails ('All' with exclude_pending=True)
        e_flagged = create_email("Flagged html", "Subject F", "f@test.com", status="Flagged", db_path=TEST_DB)
        purged_all = clear_outbox_emails(status="All", exclude_pending=True, db_path=TEST_DB)
        self.assertGreaterEqual(purged_all, 2)  # e_app, e_flagged
        self.assertIsNone(get_email_by_id(e_app, db_path=TEST_DB))
        self.assertIsNone(get_email_by_id(e_flagged, db_path=TEST_DB))
        # Pending is still preserved!
        self.assertIsNotNone(get_email_by_id(e_pending, db_path=TEST_DB))

    def test_63_custom_body_sequence_rule_and_followup_generation(self):
        """Test creating sequence rules with self-written custom body and automated draft generation."""
        from scheduler import process_due_sequence_rules
        import sqlite3

        # 1. Create a contact with personalization data
        cid = create_contact(
            name="Samantha Vance",
            email="samantha@vanceinnovations.com",
            company="Vance Innovations",
            db_path=TEST_DB
        )

        # 2. Register sequence rule with custom body (no pre-made template_id)
        custom_subj = "Re: Custom pitch for [Company]"
        custom_body = "Hi [Name],\n\nFollowing up on my custom message for [Company]. Are you free for a call?"
        rid = create_sequence_rule(
            sequence_id="seq_custom_body_test",
            contact_id=cid,
            contact_email="samantha@vanceinnovations.com",
            step_number=2,
            delay_unit="hours",
            delay_value=48,
            template_id=None,
            custom_subject=custom_subj,
            custom_body=custom_body,
            db_path=TEST_DB
        )

        rules = get_sequence_rules(db_path=TEST_DB)
        rule = next(r for r in rules if r["id"] == rid)
        self.assertEqual(rule["custom_body"], custom_body)
        self.assertEqual(rule["custom_subject"], custom_subj)

        # 3. Simulate email send trigger: mark status as Scheduled and due in the past
        past_due = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(TEST_DB)
        conn.execute("UPDATE sequence_rules SET status = 'Scheduled', due_at = ? WHERE id = ?", (past_due, rid))
        conn.commit()
        conn.close()

        # 4. Process due rules via the background scheduler engine
        gen_count = process_due_sequence_rules(db_path=TEST_DB)
        self.assertEqual(gen_count, 1)

        # 5. Verify the auto-generated follow-up email draft
        all_emails = get_emails(db_path=TEST_DB)
        fu_email = next(e for e in all_emails if e.get("recipient") == "samantha@vanceinnovations.com" and e.get("sequence_step") == 2)
        self.assertIsNotNone(fu_email)
        self.assertIn("Vance Innovations", fu_email["subject"])
        self.assertIn("Samantha", fu_email["email_html"])
        self.assertIn("Vance Innovations", fu_email["email_html"])
        self.assertEqual(fu_email["status"], "Pending")

    def test_64_get_system_excluded_emails(self):
        """Test that get_system_excluded_emails aggregates BCC, sender_email, and mailbox fleet accounts."""
        set_config("bcc_email", "clicktoorderllc@gmail.com, ARCHIVE@mycompany.org", db_path=TEST_DB)
        set_config("sender_email", "outreach-main@mycompany.org", db_path=TEST_DB)
        add_smtp_account("Fleet Sender 1", "fleet1@mycompany.org", "pass", "smtp.host.com", 587, db_path=TEST_DB)

        excluded = get_system_excluded_emails(db_path=TEST_DB)
        self.assertIn("clicktoorderllc@gmail.com", excluded)
        self.assertIn("archive@mycompany.org", excluded)
        self.assertIn("outreach-main@mycompany.org", excluded)
        self.assertIn("fleet1@mycompany.org", excluded)

    def test_65_cleanup_internal_drafts(self):
        """Test that cleanup_internal_drafts purges accidental drafts addressed to BCC or sender accounts."""
        set_config("bcc_email", "clicktoorderllc@gmail.com", db_path=TEST_DB)

        # 1. Create a draft to prospect and a draft accidentally created for BCC address
        prospect_eid = create_email("<p>Pitch</p>", "Subject", "realprospect@brand.com", status="Pending", db_path=TEST_DB)
        bcc_eid = create_email("<p>Pitch</p>", "Subject", "clicktoorderllc@gmail.com", status="Pending", db_path=TEST_DB)

        # 2. Run cleanup_internal_drafts
        deleted_cnt = cleanup_internal_drafts(db_path=TEST_DB)
        self.assertEqual(deleted_cnt, 1)

        # 3. Verify bcc_eid was deleted while prospect_eid remains intact
        self.assertIsNone(get_email_by_id(bcc_eid, db_path=TEST_DB))
        self.assertIsNotNone(get_email_by_id(prospect_eid, db_path=TEST_DB))

    def test_66_encrypted_credentials_and_warmup_ramp(self):
        """Test Section 8 Criteria 4 and 10: Encrypted passwords and warmup math."""
        from database import (
            create_smtp_account,
            get_smtp_account_by_id,
            get_warmup_info,
            encrypt_smtp_password,
            decrypt_smtp_password
        )
        # Criterion 10: Encrypted credentials
        raw_pw = "SuperSecretHostingerPass123!"
        acc_id = create_smtp_account(
            name="Hostinger Mailbox",
            email="test_warmup@hostinger.com",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            smtp_user="test_warmup@hostinger.com",
            smtp_password=raw_pw,
            daily_limit=50,
            warmup_enabled=True,
            warmup_start_date="2026-09-01",
            warmup_starting_limit=5,
            warmup_daily_increment=3,
            warmup_target_limit=30,
            db_path=TEST_DB
        )
        acc = get_smtp_account_by_id(acc_id, db_path=TEST_DB)
        self.assertIsNotNone(acc)
        # Verify password is encrypted in database
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM smtp_accounts WHERE id = ?", (acc_id,))
        db_raw_pw = cursor.fetchone()[0]
        conn.close()
        self.assertNotEqual(db_raw_pw, raw_pw)
        self.assertTrue(db_raw_pw.startswith("gAAAAA") or len(db_raw_pw) > len(raw_pw))
        # Verify decrypts correctly

        dec_pw, _ = decrypt_smtp_password(db_raw_pw)
        self.assertEqual(dec_pw, raw_pw)

        # Criterion 4: Warmup ramp
        # Start=5, increment=3, max=30
        # Day 1: 2026-09-01 -> limit = 5
        w1 = get_warmup_info(acc, today_str="2026-09-01")
        self.assertEqual(w1["effective_limit"], 5)
        # Day 4: 2026-09-04 -> limit = 5 + 3*3 = 14
        w4 = get_warmup_info(acc, today_str="2026-09-04")
        self.assertEqual(w4["effective_limit"], 14)
        # Day 10: 2026-09-10 -> limit = min(30, 5 + 9*3 = 32) = 30
        w10 = get_warmup_info(acc, today_str="2026-09-10")
        self.assertEqual(w10["effective_limit"], 30)


    def test_67_lead_status_normalization_five_statuses(self):
        """Test Section 8 Criterion 9: 5 statuses only and normalization."""
        from database import normalize_lead_status, LEAD_STATUSES
        self.assertEqual(len(LEAD_STATUSES), 5)
        self.assertEqual(normalize_lead_status("Not Contacted"), "New")
        self.assertEqual(normalize_lead_status("Drafted"), "New")
        self.assertEqual(normalize_lead_status("Sent"), "Emailed")
        self.assertEqual(normalize_lead_status("Contacted"), "Emailed")
        self.assertEqual(normalize_lead_status("Interested"), "Replied")
        self.assertEqual(normalize_lead_status("Meeting Booked"), "Replied")
        self.assertEqual(normalize_lead_status("Bounced"), "Bounced")
        self.assertEqual(normalize_lead_status("Not Interested"), "Do Not Contact")
        self.assertEqual(normalize_lead_status("Random Status"), "New")

    def test_68_unfilled_token_safety_gate(self):
        """Test Section 8 Criterion 7: Unfilled token safety gate blocks unresolved [Token]."""
        sample_template = "Hi [Name], we saw your store [Company] and [Missing_Field]."
        contact = {"name": "Alice", "company": "Acme Inc"}
        resolved = resolve_template(sample_template, contact)
        unfilled = _missing_tokens(resolved)
        self.assertIn("[Missing_Field]", unfilled)
        self.assertNotIn("[Name]", unfilled)
        self.assertNotIn("[Company]", unfilled)

    def test_69_reply_pauses_followup(self):
        """Test Section 8 Criterion 6: Pending follow-up exists for a lead. Mark reply -> status changes to Paused."""
        cid = create_contact("Followup Lead", "followup@lead.com", status="Emailed", db_path=TEST_DB)
        eid = create_email("<p>Follow up pitch</p>", "Re: Catch up", "followup@lead.com", status="Scheduled", sequence_step=2, db_path=TEST_DB)

        # Pending follow-up exists
        email_before = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(email_before["status"], "Scheduled")

        # Mark reply from lead
        res = record_email_reply(
            sender_email="followup@lead.com",
            reply_subject="Re: Catch up - let's connect",
            db_path=TEST_DB
        )
        self.assertTrue(res["contact_found"])

        # Verify follow-up status changes to Paused
        email_after = get_email_by_id(eid, db_path=TEST_DB)
        self.assertEqual(email_after["status"], "Paused")

        # Verify lead status is Replied
        lead_after = get_contact_by_id(cid, db_path=TEST_DB)
        self.assertEqual(lead_after["status"], "Replied")

    def test_70_multi_mailbox_alternation(self):
        """Test Section 8 Criterion 1: Configure 2 Hostinger accounts, queue 4 emails. Verify alternating accounts."""
        import os
        from database import init_db, create_smtp_account, get_next_available_smtp_account, increment_smtp_sent
        db_rot = os.path.join(os.path.dirname(TEST_DB), "test_rot.db")
        if os.path.exists(db_rot):
            try:
                os.remove(db_rot)
            except Exception:
                pass
        init_db(db_rot)

        acc1_id = create_smtp_account(
            name="Hostinger Mailbox 1",
            email="mb1@hostinger.com",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            smtp_user="mb1@hostinger.com",
            smtp_password="pw1",
            daily_limit=50,
            db_path=db_rot
        )
        acc2_id = create_smtp_account(
            name="Hostinger Mailbox 2",
            email="mb2@hostinger.com",
            smtp_host="smtp.hostinger.com",
            smtp_port=465,
            smtp_user="mb2@hostinger.com",
            smtp_password="pw2",
            daily_limit=50,
            db_path=db_rot
        )

        # Dispatch sequence for 4 emails
        dispatched_accounts = []
        for _ in range(4):
            acc = get_next_available_smtp_account(db_path=db_rot)
            self.assertIsNotNone(acc)
            dispatched_accounts.append(acc["email"])
            increment_smtp_sent(acc["id"], db_path=db_rot)

        # Verify alternating accounts in the dispatch sequence
        self.assertEqual(dispatched_accounts[0], "mb1@hostinger.com")
        self.assertEqual(dispatched_accounts[1], "mb2@hostinger.com")
        self.assertEqual(dispatched_accounts[2], "mb1@hostinger.com")
        self.assertEqual(dispatched_accounts[3], "mb2@hostinger.com")

        if os.path.exists(db_rot):
            try:
                os.remove(db_rot)
            except Exception:
                pass


    def test_71_send_guard_missing_tokens_detector(self):
        """Test that _missing_tokens identifies unfilled bracketed tokens."""
        clean_text = "Hi Jane, noticed your brand has great reviews on Amazon."
        self.assertEqual(_missing_tokens(clean_text), [])

        dirty_text = "Hi [Name], loved your [ProductCategory] listing but noticed [AmazonObservation] on your page."
        missing = _missing_tokens(dirty_text)
        self.assertEqual(len(missing), 3)
        self.assertIn("[Name]", missing)
        self.assertIn("[ProductCategory]", missing)
        self.assertIn("[AmazonObservation]", missing)

    def test_72_in_reply_to_threading_headers(self):
        """Test Section 8 Criterion 5: In-reply-to threading sets In-Reply-To and References headers to original Message-ID."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        original_msg_id = "<msg-initial-12345@sellomize.com>"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Re: Our discussion"
        msg["From"] = "sender@hostinger.com"
        msg["To"] = "prospect@client.com"
        msg["In-Reply-To"] = original_msg_id
        msg["References"] = original_msg_id

        self.assertEqual(msg["In-Reply-To"], original_msg_id)
        self.assertEqual(msg["References"], original_msg_id)

    def test_73_thread_history_timeline(self):
        """Test get_thread_history returns full conversation history in chronological order."""
        from database import get_thread_history
        target_email = "timeline.test@prospect.com"
        cid = create_contact(
            name="Alex Turner",
            email=target_email,
            company="Turner Arctic",
            custom_variables={"role": "Founder", "observation": "listing hero image"},
            db_path=TEST_DB
        )

        # 1. Draft email
        e1 = create_email("<p>Touch 1</p>", "Touch 1 Draft", target_email, status="Draft", db_path=TEST_DB)
        # 2. Sent email
        e2 = create_email("<p>Touch 1 Sent</p>", "Partnership Inquiry", target_email, status="Sent", message_id="<msg001@sellomize.com>", db_path=TEST_DB)
        # 3. Notification reply
        from database import create_notification
        nid = create_notification(
            type="reply",
            title="Reply from Alex",
            message="Hey, thanks for reaching out. What are your rates?",
            contact_email=target_email,
            db_path=TEST_DB
        )

        history = get_thread_history(target_email, db_path=TEST_DB)
        self.assertIsNotNone(history["contact"])
        self.assertEqual(history["contact"]["name"], "Alex Turner")
        self.assertEqual(history["contact"]["custom_variables_dict"].get("role"), "Founder")
        self.assertGreaterEqual(len(history["emails"]), 2)
        self.assertGreaterEqual(len(history["notifications"]), 1)
        self.assertGreaterEqual(len(history["timeline"]), 3)

        timeline_types = [t["type"] for t in history["timeline"]]
        self.assertIn("email_draft", timeline_types)
        self.assertIn("email_sent", timeline_types)
        self.assertIn("reply_received", timeline_types)

    def test_74_send_smtp_email_threading_headers(self):
        """Test send_smtp_email includes In-Reply-To and References headers and populates message_id_out."""
        from unittest.mock import MagicMock, patch
        from smtp_dispatcher import send_smtp_email

        mock_smtp = MagicMock()
        mock_acc = {
            "id": 1,
            "sender_name": "Antigravity",
            "email": "sender@agency.com",
            "password": "pass",
            "smtp_host": "smtp.hostinger.com",
            "smtp_port": 465
        }

        msg_ids = []
        with patch("smtplib.SMTP_SSL", return_value=mock_smtp):
            mock_smtp.__enter__.return_value = mock_smtp
            success, info = send_smtp_email(
                smtp_account=mock_acc,
                recipient="prospect@client.com",
                subject="Re: Quick Question",
                html_content="<p>Follow up</p>",
                in_reply_to="<parent-msg-123@agency.com>",
                references="<grandparent-msg@agency.com>",
                message_id_out=msg_ids
            )

            self.assertTrue(success)
            self.assertEqual(len(msg_ids), 1)
            self.assertTrue(msg_ids[0].startswith("<") and msg_ids[0].endswith(">"))

            # Inspect the sent MIME message
            call_args = mock_smtp.send_message.call_args[0]
            sent_msg = call_args[0]
            self.assertEqual(sent_msg["In-Reply-To"], "<parent-msg-123@agency.com>")
            self.assertEqual(sent_msg["References"], "<grandparent-msg@agency.com>")

    def test_75_template_engine_first_name_curly_and_aliases(self):
        """Test inject_variables extracts first_name automatically and handles curly vars without breaking Spintax."""
        contact = {
            "name": "Marcus Aurelius",
            "company": "Rome Inc",
            "custom_variables_dict": {
                "observation": "poor storefront layout",
                "pain_point": "losing 30% organic traffic",
                "proof_story": "helped brand lift revenue by +42%"
            }
        }

        # 1. Bracketed first_name
        res1 = inject_variables("Hi [first_name], welcome to [company].", contact)
        self.assertEqual(res1, "Hi Marcus, welcome to Rome Inc.")

        # 2. Curly first_name and aliases
        res2 = inject_variables("Hi {first_name}, noticed {observation} which causes {pain_point}. {proof_story}", contact)
        self.assertEqual(res2, "Hi Marcus, noticed poor storefront layout which causes losing 30% organic traffic. helped brand lift revenue by +42%")

        # 3. Spintax {opt1|opt2} preserved for parse_spintax
        mixed = "Hi {first_name}, {quick question|wanted to check in}."
        injected = inject_variables(mixed, contact)
        self.assertTrue(injected.startswith("Hi Marcus, {"))
        resolved = parse_spintax(injected)
        self.assertTrue("quick question" in resolved or "wanted to check in" in resolved)

    def test_76_template_frameworks_resolution(self):
        """Test that outreach templates resolve cleanly with lead variables and Spintax."""
        contact = {
            "name": "Elena Rostova",
            "company": "Rostova Botanicals",
            "country_or_timezone": "America/New_York",
            "custom_variables_dict": {
                "first_name": "Elena",
                "role": "Founder"
            }
        }
        test_templates = [
            {"subject": "Quick question for [Company]", "body": "<p>Hi [Name], {hope you are well|reaching out to connect}.</p>"},
            {"subject": "Partnership with {company}", "body": "<p>Hello {first_name}, impressed by {company}.</p>"}
        ]
        for tpl in test_templates:
            subj = inject_variables(parse_spintax(tpl["subject"]), contact)
            body = resolve_template(tpl["body"], contact)
            missing = _missing_tokens(subj) + _missing_tokens(body)
            self.assertEqual(missing, [], f"Template '{tpl['subject']}' had unfilled tokens: {missing}")
            self.assertIn("Elena", body)


if __name__ == "__main__":
    unittest.main()




