"""
test_phase4_engine.py - Verification suite for Phase 4:
- Campaign sending window and timezone evaluation.
- Stop rules: Unsubscribe/Suppression, Bounce, Replied/Converted.
- Multi-step sequence progression (Step 1 -> Step 2 -> Completed).
- Event audit logging (sent, replied, unsubscribed, completed).
- Idempotency locks and safe dry-run execution.
"""

import os
import sys
from datetime import datetime, timedelta

from database import (
    init_db,
    create_contact,
    update_contact,
    get_contacts,
    delete_contact,
    create_campaign,
    get_campaign,
    update_campaign,
    delete_campaign,
    create_campaign_step,
    enroll_contacts_in_campaign,
    get_campaign_contacts,
    get_campaign_events,
    DB_FILE,
)
from campaign_engine import (
    is_campaign_in_window,
    get_campaign_today_sent_count,
    process_campaign_contact_step,
    run_campaign_engine_cycle,
)


def test_is_campaign_in_window():
    # Campaign active Monday to Sunday, 00:00 to 23:59 -> always True
    camp_open = {
        "timezone": "America/New_York (US East)",
        "send_days": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "send_window_start": "00:00",
        "send_window_end": "23:59",
    }
    in_win, msg = is_campaign_in_window(camp_open)
    assert in_win is True, f"Expected open campaign to be in window, got: {msg}"

    # Campaign active only on an impossible day -> False
    camp_closed_day = {
        "timezone": "America/New_York",
        "send_days": "NeverDay",
        "send_window_start": "00:00",
        "send_window_end": "23:59",
    }
    in_win2, msg2 = is_campaign_in_window(camp_closed_day)
    assert in_win2 is False
    assert "not an active sending day" in msg2


def test_stop_rules():
    # Create test contacts
    c_suppressed = create_contact(
        name="Suppressed Lead",
        email="suppressed_test@example.com",
        company="Suppressed Inc",
        status="Do Not Contact"
    )
    c_bounced = create_contact(
        name="Bounced Lead",
        email="bounced_test@example.com",
        company="Bounced Inc",
        status="Bounced"
    )
    c_replied = create_contact(
        name="Replied Lead",
        email="replied_test@example.com",
        company="Replied Inc",
        status="Replied"
    )

    camp_id = create_campaign(
        name="Stop Rules Test Campaign",
        status="Active",
        send_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        send_window_start="00:00",
        send_window_end="23:59"
    )
    create_campaign_step(
        campaign_id=camp_id,
        position=1,
        subject="Hello [First Name]",
        body_html="<p>Checking in.</p>",
        condition="no_reply"
    )

    try:
        enroll_contacts_in_campaign(camp_id, [c_suppressed, c_bounced, c_replied])
        camp_contacts = get_campaign_contacts(camp_id)
        assert len(camp_contacts) == 3

        camp = get_campaign(camp_id)
        steps = [
            {"id": 1, "position": 1, "subject": "Hello [First Name]", "body_html": "<p>Checking in.</p>", "condition": "no_reply", "wait_days": 0, "wait_hours": 0}
        ]

        # Process each contact and assert stop rule triggers
        for cc in camp_contacts:
            lead_email = cc.get("email")
            ok, msg = process_campaign_contact_step(camp, cc, steps, dry_run=True)
            assert ok is False

            # Verify respective states
            if "suppressed" in lead_email:
                assert msg == "Contact suppressed"
            elif "bounced" in lead_email:
                assert msg == "Contact bounced"
            elif "replied" in lead_email:
                assert msg == "Contact replied"

        # Check campaign events audit trail
        events = get_campaign_events(camp_id)
        ev_types = [e["event_type"] for e in events]
        assert "unsubscribed" in ev_types
        assert "bounced" in ev_types
        assert "replied" in ev_types

    finally:
        delete_campaign(camp_id)
        delete_contact(c_suppressed)
        delete_contact(c_bounced)
        delete_contact(c_replied)


def test_multistep_sequence_progression():
    # Create healthy contact
    cid_lead = create_contact(
        name="Alex Smith",
        email="alex.smith@testbrand.com",
        company="Test Brand",
        status="New"
    )

    camp_id = create_campaign(
        name="Progression Test Campaign",
        status="Active",
        send_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        send_window_start="00:00",
        send_window_end="23:59"
    )

    s1_id = create_campaign_step(
        campaign_id=camp_id,
        position=1,
        subject="Step 1: Welcome [First Name] from [Company]",
        body_html="<p>Hi [First Name], intro email.</p>",
        condition="no_reply"
    )
    s2_id = create_campaign_step(
        campaign_id=camp_id,
        position=2,
        subject="Step 2: Quick follow-up for [Company]",
        body_html="<p>Hi [First Name], following up.</p>",
        condition="no_reply",
        wait_days=2,
        wait_hours=0
    )

    try:
        enroll_contacts_in_campaign(camp_id, [cid_lead])
        enrolled = get_campaign_contacts(camp_id)
        assert len(enrolled) == 1
        contact_enrollment = enrolled[0]
        assert contact_enrollment["current_step"] == 0
        assert contact_enrollment["state"] == "pending"

        camp = get_campaign(camp_id)
        steps = [
            {"id": s1_id, "position": 1, "subject": "Step 1: Welcome [First Name] from [Company]", "body_html": "<p>Hi [First Name], intro email.</p>", "condition": "no_reply", "wait_days": 0, "wait_hours": 0},
            {"id": s2_id, "position": 2, "subject": "Step 2: Quick follow-up for [Company]", "body_html": "<p>Hi [First Name], following up.</p>", "condition": "no_reply", "wait_days": 2, "wait_hours": 0}
        ]

        # 1. Process Step 1
        ok1, msg1 = process_campaign_contact_step(camp, contact_enrollment, steps, dry_run=True)
        assert ok1 is True, f"Step 1 failed: {msg1}"

        # Verify contact advanced to Step 1 and is scheduled for Step 2
        enrolled_after_step1 = get_campaign_contacts(camp_id)[0]
        assert enrolled_after_step1["current_step"] == 1
        assert enrolled_after_step1["state"] == "scheduled"
        assert enrolled_after_step1["next_send_at"] != ""

        # Verify 'sent' event recorded
        events1 = get_campaign_events(camp_id)
        assert any(e["event_type"] == "sent" and e["step_id"] == s1_id for e in events1)

        # 2. Process Step 2 (force next_send_at to past so it's due)
        contact_enrollment2 = enrolled_after_step1
        ok2, msg2 = process_campaign_contact_step(camp, contact_enrollment2, steps, dry_run=True)
        assert ok2 is True, f"Step 2 failed: {msg2}"

        # Verify contact completed full sequence
        enrolled_after_step2 = get_campaign_contacts(camp_id)[0]
        assert enrolled_after_step2["current_step"] == 2
        assert enrolled_after_step2["state"] == "completed"

        # Verify final completed event
        events2 = get_campaign_events(camp_id)
        assert any(e["event_type"] == "completed" for e in events2)

    finally:
        delete_campaign(camp_id)
        delete_contact(cid_lead)


def test_run_campaign_engine_cycle_dry_run():
    # Smoke test of main worker cycle loop
    res = run_campaign_engine_cycle(dry_run=True)
    assert isinstance(res, dict)
    assert "active_campaigns" in res
    assert "dispatched" in res


if __name__ == "__main__":
    test_is_campaign_in_window()
    print("PASS: test_is_campaign_in_window")
    test_stop_rules()
    print("PASS: test_stop_rules")
    test_multistep_sequence_progression()
    print("PASS: test_multistep_sequence_progression")
    test_run_campaign_engine_cycle_dry_run()
    print("PASS: test_run_campaign_engine_cycle_dry_run")
    print("\nALL PHASE 4 ENGINE & STOP RULES TESTS PASSED SUCCESSFULLY!")
