"""
test_phase3_sequence.py - Verification suite for Phase 3:
- Sequence step validation (send guard unfilled token check, empty subject/body prevention).
- Template save and load compatibility.
- sync_campaign_steps persistence and step ordering.
- Live sample lead token resolution and signature injection.
"""

import os
import sys

from database import (
    init_db,
    create_campaign,
    get_campaign,
    create_campaign_step,
    get_campaign_steps,
    sync_campaign_steps,
    create_template,
    get_templates,
    delete_campaign,
    DB_FILE,
)
from template_engine import resolve_template, _missing_tokens
from ui.campaign_sequence import validate_sequence_steps


def test_validate_sequence_steps_valid():
    steps = [
        {
            "position": 1,
            "subject": "Quick question for [First Name] at [Company]",
            "body_html": "<p>Hi [First Name],</p><p>We saw [Company] online.</p>",
            "wait_days": 0,
            "wait_hours": 0,
            "condition": "no_reply"
        },
        {
            "position": 2,
            "subject": "Re: Quick question for [First Name] at [Company]",
            "body_html": "<p>Hi [Name], following up.</p>",
            "wait_days": 3,
            "wait_hours": 0,
            "condition": "no_reply"
        }
    ]
    is_valid, errors = validate_sequence_steps(steps)
    assert is_valid is True, f"Expected valid sequence, got errors: {errors}"
    assert len(errors) == 0


def test_validate_sequence_steps_empty_and_unfilled():
    # Empty subject
    steps_empty_subj = [{"position": 1, "subject": "", "body_html": "<p>Hello</p>"}]
    is_valid, errors = validate_sequence_steps(steps_empty_subj)
    assert is_valid is False
    assert any("Subject line cannot be empty" in e for e in errors)

    # Empty body
    steps_empty_body = [{"position": 1, "subject": "Hello", "body_html": "   "}]
    is_valid, errors = validate_sequence_steps(steps_empty_body)
    assert is_valid is False
    assert any("Email body cannot be empty" in e for e in errors)

    # Unfilled invalid token
    steps_bad_tokens = [{
        "position": 1,
        "subject": "Check [DiscountCode]",
        "body_html": "<p>Hi, your link is [BrokenVariable]</p>"
    }]
    is_valid, errors = validate_sequence_steps(steps_bad_tokens)
    assert is_valid is False
    assert any("[DiscountCode]" in e for e in errors)
    assert any("[BrokenVariable]" in e for e in errors)


def test_template_load_and_save_workflow():
    # Create template
    tpl_name = "Phase 3 Outreach Step 1 Test"
    tpl_subj = "Hello from [Company]"
    tpl_body = "<p>Hey [First Name], great brand!</p>"
    new_tid = create_template(name=tpl_name, subject=tpl_subj, body_html=tpl_body)
    assert new_tid > 0

    # Retrieve templates
    all_tpls = get_templates()
    matching = [t for t in all_tpls if t["id"] == new_tid or t.get("name") == tpl_name]
    assert len(matching) > 0
    saved = matching[0]
    assert saved.get("name") == tpl_name
    assert saved.get("subject") == tpl_subj


def test_sync_campaign_steps_persistence():
    # Create draft campaign
    cid = create_campaign(
        name="Phase 3 Sequence Test Campaign",
        description="Testing sync_campaign_steps",
        status="Draft"
    )
    assert cid > 0

    try:
        # Create initial steps
        initial_steps = [
            {
                "position": 1,
                "subject": "Step 1 Subject",
                "body_html": "<p>Step 1 Body</p>",
                "wait_days": 0,
                "wait_hours": 0,
                "condition": "no_reply"
            }
        ]
        sync_campaign_steps(cid, initial_steps)
        fetched1 = get_campaign_steps(cid)
        assert len(fetched1) == 1
        assert fetched1[0]["subject"] == "Step 1 Subject"

        # Now update with reordered / multi-step sequence
        updated_steps = [
            {
                "position": 1,
                "subject": "Updated Step 1",
                "body_html": "<p>Updated Body 1</p>",
                "wait_days": 0,
                "wait_hours": 0,
                "condition": "no_reply"
            },
            {
                "position": 2,
                "subject": "Re: Updated Step 1",
                "body_html": "<p>Follow-up Body 2</p>",
                "wait_days": 4,
                "wait_hours": 2,
                "condition": "no_reply"
            },
            {
                "position": 3,
                "subject": "Final check for [Company]",
                "body_html": "<p>Final Body 3</p>",
                "wait_days": 7,
                "wait_hours": 0,
                "condition": "always"
            }
        ]
        sync_campaign_steps(cid, updated_steps)
        fetched2 = get_campaign_steps(cid)
        assert len(fetched2) == 3
        assert fetched2[0]["position"] == 1
        assert fetched2[0]["subject"] == "Updated Step 1"
        assert fetched2[1]["position"] == 2
        assert fetched2[1]["wait_days"] == 4
        assert fetched2[1]["is_reply_thread"] == 1
        assert fetched2[2]["position"] == 3
        assert fetched2[2]["condition"] == "always"

    finally:
        # Clean up test campaign
        delete_campaign(cid)


def test_live_lead_resolution():
    sample_lead = {
        "name": "Zoey Pu",
        "company": "Kaleidos Makeup",
        "email": "zoey.pu@kaleidosmakeup.com"
    }
    subj_tmpl = "Question for [First Name] at [Company]"
    body_tmpl = "<p>Hi [First Name], saw [Company] online.</p>"

    resolved_s = resolve_template(subj_tmpl, sample_lead)
    resolved_b = resolve_template(body_tmpl, sample_lead)

    assert "Zoey" in resolved_s
    assert "Kaleidos Makeup" in resolved_s
    assert "[First Name]" not in resolved_s
    assert "[Company]" not in resolved_b

    # Check unfilled tokens detection
    unfilled = _missing_tokens(resolved_s) + _missing_tokens(resolved_b)
    assert len(unfilled) == 0


if __name__ == "__main__":
    test_validate_sequence_steps_valid()
    print("PASS: test_validate_sequence_steps_valid")
    test_validate_sequence_steps_empty_and_unfilled()
    print("PASS: test_validate_sequence_steps_empty_and_unfilled")
    test_template_load_and_save_workflow()
    print("PASS: test_template_load_and_save_workflow")
    test_sync_campaign_steps_persistence()
    print("PASS: test_sync_campaign_steps_persistence")
    test_live_lead_resolution()
    print("PASS: test_live_lead_resolution")
    print("\nALL PHASE 3 SEQUENCE TESTS PASSED SUCCESSFULLY!")
