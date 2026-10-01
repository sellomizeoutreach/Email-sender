"""
test_phase2_campaigns.py - Unit tests for Phase 2 Campaigns data model and helpers.
"""

import unittest
from database import (
    init_db,
    create_campaign,
    get_campaign,
    get_all_campaigns,
    update_campaign,
    delete_campaign,
    duplicate_campaign,
    create_campaign_step,
    get_campaign_steps,
    update_campaign_step,
    delete_campaign_step,
    enroll_contacts_in_campaign,
    get_campaign_contacts,
    get_campaign_kpis,
    get_campaign_detail_stats,
    mark_campaign_contact_converted,
    get_contacts,
)
from ui.campaigns import render_campaigns_tab


class TestCampaignsPhase2(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_campaign_lifecycle(self):
        # 1. Create Campaign
        camp_id = create_campaign(
            name="Test Outreach Campaign",
            description="Testing automated sequence",
            tags="Test, Automation",
            status="Draft",
            list_id="All leads",
            timezone="America/New_York",
            send_window_start="09:00",
            send_window_end="18:00",
            send_days="Mon,Tue,Wed",
            daily_limit=30,
            delay_seconds=45
        )
        self.assertGreater(camp_id, 0)

        # 2. Retrieve Campaign
        camp = get_campaign(camp_id)
        self.assertIsNotNone(camp)
        self.assertEqual(camp["name"], "Test Outreach Campaign")
        self.assertEqual(camp["status"], "Draft")
        self.assertEqual(camp["daily_limit"], 30)

        # 3. Add Steps
        step1_id = create_campaign_step(
            campaign_id=camp_id,
            position=1,
            subject="Step 1 Subject",
            body_html="<p>Step 1 Body</p>",
            wait_days=0,
            wait_hours=0
        )
        step2_id = create_campaign_step(
            campaign_id=camp_id,
            position=2,
            subject="Re: Step 1 Subject",
            body_html="<p>Step 2 Body</p>",
            wait_days=3,
            wait_hours=2,
            condition="no_reply"
        )
        self.assertGreater(step1_id, 0)
        self.assertGreater(step2_id, 0)

        steps = get_campaign_steps(camp_id)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["position"], 1)
        self.assertEqual(steps[1]["position"], 2)
        self.assertEqual(steps[1]["wait_days"], 3)

        # 4. Enroll contacts
        contacts = get_contacts()
        if contacts:
            cids = [contacts[0]["id"]]
            enrolled = enroll_contacts_in_campaign(camp_id, cids)
            self.assertEqual(enrolled, 1)

            camp_contacts = get_campaign_contacts(camp_id)
            self.assertEqual(len(camp_contacts), 1)

            # Mark converted
            marked = mark_campaign_contact_converted(camp_contacts[0]["id"])
            self.assertTrue(marked)

        # 5. Duplicate Campaign
        copy_id = duplicate_campaign(camp_id)
        self.assertGreater(copy_id, 0)
        copy_camp = get_campaign(copy_id)
        self.assertIn("(Copy)", copy_camp["name"])
        copy_steps = get_campaign_steps(copy_id)
        self.assertEqual(len(copy_steps), 2)

        # 6. KPI stats calculation
        kpis = get_campaign_kpis()
        self.assertIn("total_campaigns", kpis)
        self.assertGreaterEqual(kpis["total_campaigns"], 2)

        # 7. Clean up
        delete_campaign(camp_id)
        delete_campaign(copy_id)

        self.assertIsNone(get_campaign(camp_id))
        self.assertIsNone(get_campaign(copy_id))


if __name__ == "__main__":
    unittest.main()
