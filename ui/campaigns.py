"""
ui/campaigns.py - Campaigns Management Module for Sellomize Reach.
Phase 2 Implementation:
- Campaigns List with real summary KPI tiles (Total, Sent, Avg Open Rate, Converted).
- 2-column responsive grid of campaign cards with progress bars and stat metrics.
- Status filter pills (All, Active, Draft, Paused, Completed) and instant search.
- 4-step New Campaign Wizard with Draft saving and pre-launch safety checklist.
- Campaign Detail view with 4 tabs: Overview, Sequence, Contacts, Activity.
"""

import os
import re
import json
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import streamlit as st

from database import (
    get_all_campaigns,
    get_campaign,
    create_campaign,
    update_campaign,
    delete_campaign,
    duplicate_campaign,
    create_campaign_step,
    get_campaign_steps,
    update_campaign_step,
    delete_campaign_step,
    sync_campaign_steps,
    get_campaign_contacts,
    enroll_contacts_in_campaign,
    mark_campaign_contact_converted,
    mark_campaign_contact_replied,
    get_campaign_events,
    get_campaign_kpis,
    get_campaign_detail_stats,
    get_contacts,
    get_templates,
    create_template,
    DB_FILE,
)
from template_engine import _missing_tokens, inject_variables
from timezone_helper import TARGET_MARKETS, get_engine_now
from ui.components import trigger_toast
from ui.rich_editor import render_rich_editor
from ui.campaign_sequence import render_sequence_builder, validate_sequence_steps


# -----------------------------------------------------------------------------
# HELPER: STATUS BADGE HTML
# -----------------------------------------------------------------------------

def _render_status_badge(status: str) -> str:
    s = (status or "Draft").lower()
    if s == "active":
        return '<span class="sellomize-badge badge-success" style="font-size:11px; padding:3px 8px;">ACTIVE</span>'
    elif s == "paused":
        return '<span class="sellomize-badge badge-alert" style="font-size:11px; padding:3px 8px; background:#FEF3C7; color:#92400E; border:1px solid #FCD34D;">PAUSED</span>'
    elif s == "completed":
        return '<span class="sellomize-badge" style="font-size:11px; padding:3px 8px; background:#E0F2FE; color:#0369A1; border:1px solid #BAE6FD;">COMPLETED</span>'
    else:
        return '<span class="sellomize-badge" style="font-size:11px; padding:3px 8px; background:#F1F5F9; color:#475569; border:1px solid #CBD5E1;">DRAFT</span>'


# -----------------------------------------------------------------------------
# VIEW 1: CAMPAIGNS LIST
# -----------------------------------------------------------------------------

def _render_campaigns_list(all_contacts: List[Dict[str, Any]], all_templates: List[Dict[str, Any]]):
    kpis = get_campaign_kpis()

    # Header row
    h_col1, h_col2 = st.columns([7, 3], vertical_alignment="center")
    with h_col1:
        st.markdown(
            f"<div style='font-size:13px; color:#475569; margin-top:-4px; margin-bottom:12px;'>"
            f"<b>{kpis['active_campaigns']}</b> active · <b>{kpis['draft_campaigns']}</b> drafts ready to launch"
            f"</div>",
            unsafe_allow_html=True
        )
    with h_col2:
        if st.button("➕ New Campaign", type="primary", use_container_width=True, key="btn_open_new_campaign_wizard"):
            st.session_state["campaign_view"] = "wizard"
            st.session_state["wizard_step"] = 1
            st.session_state["wizard_data"] = {
                "name": "",
                "description": "",
                "tags": "",
                "list_id": "All leads",
                "timezone": "America/New_York",
                "send_window_start": "09:00",
                "send_window_end": "18:00",
                "send_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "daily_limit": 50,
                "delay_seconds": 60,
                "steps": [
                    {
                        "position": 1,
                        "subject": "Quick question regarding [Company]",
                        "body_html": "<p>Hi [First Name],</p><p>We noticed [Company] on Amazon and had a quick observation regarding your listing conversion.</p><p>Best regards,<br>Jack Connor</p>",
                        "wait_days": 0,
                        "wait_hours": 0,
                        "condition": "no_reply"
                    }
                ]
            }
            st.rerun()

    # Real KPI Summary Tiles
    kpi_cols = st.columns(4)
    with kpi_cols[0]:
        with st.container(border=True):
            st.caption("TOTAL CAMPAIGNS")
            st.markdown(f"<div style='font-size:1.6rem; font-weight:800; color:#083731; line-height:1.2;'>{kpis['total_campaigns']}</div>", unsafe_allow_html=True)
            st.caption(f"{kpis['active_campaigns']} active sequence{'s' if kpis['active_campaigns'] != 1 else ''}")
    with kpi_cols[1]:
        with st.container(border=True):
            st.caption("TOTAL DISPATCHED")
            st.markdown(f"<div style='font-size:1.6rem; font-weight:800; color:#083731; line-height:1.2;'>{kpis['total_sent']}</div>", unsafe_allow_html=True)
            st.caption("Automated sequence emails")
    with kpi_cols[2]:
        with st.container(border=True):
            st.caption("AVG OPEN RATE")
            st.markdown(f"<div style='font-size:1.6rem; font-weight:800; color:#083731; line-height:1.2;'>{kpis['avg_open_rate']:.1f}%</div>", unsafe_allow_html=True)
            st.caption(f"{kpis['total_opened']} total opens detected")
    with kpi_cols[3]:
        with st.container(border=True):
            st.caption("TOTAL CONVERSIONS")
            st.markdown(f"<div style='font-size:1.6rem; font-weight:800; color:#083731; line-height:1.2;'>{kpis['total_converted']}</div>", unsafe_allow_html=True)
            st.caption("Prospects marked converted")

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    # Filter tabs & search
    if "campaign_filter_status" not in st.session_state:
        st.session_state["campaign_filter_status"] = "All"

    c_filter_row, c_search_row = st.columns([6.5, 3.5], vertical_alignment="center")

    all_raw_camps = get_all_campaigns()
    cnt_all = len(all_raw_camps)
    cnt_active = sum(1 for c in all_raw_camps if c["status"] == "Active")
    cnt_draft = sum(1 for c in all_raw_camps if c["status"] == "Draft")
    cnt_paused = sum(1 for c in all_raw_camps if c["status"] == "Paused")
    cnt_completed = sum(1 for c in all_raw_camps if c["status"] == "Completed")

    filter_defs = [
        ("All", f"All ({cnt_all})"),
        ("Active", f"Active ({cnt_active})"),
        ("Draft", f"Draft ({cnt_draft})"),
        ("Paused", f"Paused ({cnt_paused})"),
        ("Completed", f"Completed ({cnt_completed})")
    ]

    with c_filter_row:
        p_cols = st.columns(len(filter_defs))
        for idx, (f_val, f_lbl) in enumerate(filter_defs):
            with p_cols[idx]:
                is_sel = (st.session_state["campaign_filter_status"] == f_val)
                if st.button(f_lbl, key=f"camp_filter_btn_{idx}", type="primary" if is_sel else "secondary", use_container_width=True):
                    st.session_state["campaign_filter_status"] = f_val
                    st.rerun()

    with c_search_row:
        search_query = st.text_input(
            "Search Campaigns",
            placeholder="Search campaigns by name, tags...",
            label_visibility="collapsed",
            key="camp_search_box"
        )

    # Fetch filtered campaigns
    campaigns = get_all_campaigns(
        status_filter=st.session_state["campaign_filter_status"],
        search=search_query
    )

    if not campaigns:
        st.info("No campaigns found matching your filter. Click **➕ New Campaign** above to create your first multi-step sequence.")
        return

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

    # 2-Column Responsive Card Grid
    for row_idx in range(0, len(campaigns), 2):
        row_camps = campaigns[row_idx:row_idx+2]
        cols = st.columns(2)
        for col_idx, camp in enumerate(row_camps):
            cid = camp["id"]
            stats = get_campaign_detail_stats(cid)
            steps = get_campaign_steps(cid)
            num_steps = len(steps)

            with cols[col_idx]:
                with st.container(border=True):
                    top_c1, top_c2 = st.columns([7, 3], vertical_alignment="center")
                    with top_c1:
                        st.markdown(f"<div style='font-size:16px; font-weight:800; color:#083731;'>{camp['name']}</div>", unsafe_allow_html=True)
                    with top_c2:
                        st.markdown(f"<div style='text-align:right;'>{_render_status_badge(camp['status'])}</div>", unsafe_allow_html=True)

                    if camp.get("description"):
                        st.caption(camp["description"][:95] + ("..." if len(camp["description"]) > 95 else ""))

                    # Tags pill line
                    if camp.get("tags"):
                        tag_badges = "".join([
                            f"<span style='background:#F1F5F9; color:#475569; font-size:11px; padding:2px 6px; border-radius:4px; margin-right:4px;'>#{t.strip()}</span>"
                            for t in camp["tags"].split(",") if t.strip()
                        ])
                        st.markdown(f"<div style='margin-bottom:8px;'>{tag_badges}</div>", unsafe_allow_html=True)

                    # 4 Small Stat Boxes
                    st_c1, st_c2, st_c3, st_c4 = st.columns(4)
                    with st_c1:
                        st.markdown(f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:6px; text-align:center;'><div style='font-size:11px; color:#64748B;'>Contacts</div><div style='font-size:14px; font-weight:700; color:#083731;'>{stats['contacts']}</div></div>", unsafe_allow_html=True)
                    with st_c2:
                        st.markdown(f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:6px; text-align:center;'><div style='font-size:11px; color:#64748B;'>Open Rate</div><div style='font-size:14px; font-weight:700; color:#083731;'>{stats['open_rate']:.1f}%</div></div>", unsafe_allow_html=True)
                    with st_c3:
                        st.markdown(f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:6px; text-align:center;'><div style='font-size:11px; color:#64748B;'>Reply Rate</div><div style='font-size:14px; font-weight:700; color:#083731;'>{stats['reply_rate']:.1f}%</div></div>", unsafe_allow_html=True)
                    with st_c4:
                        st.markdown(f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:6px; text-align:center;'><div style='font-size:11px; color:#64748B;'>Converted</div><div style='font-size:14px; font-weight:700; color:#FD4D1B;'>{stats['converted']}</div></div>", unsafe_allow_html=True)

                    # Subtitle meta line
                    list_label = camp.get("list_id") or "All leads"
                    st.markdown(
                        f"<div style='font-size:12px; color:#475569; margin-top:8px;'>"
                        f"⚡ <b>{num_steps}-step sequence</b> · List: <b>{list_label}</b> · Window: {camp.get('send_window_start', '09:00')}-{camp.get('send_window_end', '18:00')}"
                        f"</div>",
                        unsafe_allow_html=True
                    )

                    # Progress bar
                    pct_sent = (stats["sent"] / max(stats["contacts"] * max(num_steps, 1), 1))
                    pct_clamped = min(max(pct_sent, 0.0), 1.0)
                    st.progress(pct_clamped)

                    # Bottom footer button
                    act_c1, act_c2 = st.columns([6, 4], vertical_alignment="center")
                    with act_c1:
                        st.caption(f"Created: {camp['created_at'][:10]}")
                    with act_c2:
                        if st.button("View Details →", key=f"btn_view_camp_{cid}", use_container_width=True):
                            st.session_state["campaign_view"] = "detail"
                            st.session_state["campaign_active_id"] = cid
                            st.rerun()


# -----------------------------------------------------------------------------
# VIEW 2: NEW CAMPAIGN WIZARD (4 STEPS)
# -----------------------------------------------------------------------------

def _render_campaign_wizard(all_contacts: List[Dict[str, Any]], all_templates: List[Dict[str, Any]]):
    w_data = st.session_state.get("wizard_data", {})
    step = st.session_state.get("wizard_step", 1)

    # Wizard header
    wh_c1, wh_c2 = st.columns([8, 2], vertical_alignment="center")
    with wh_c1:
        st.markdown(f"### 🪄 New Campaign Wizard — Step {step} of 4")
    with wh_c2:
        if st.button("✖️ Cancel Wizard", key="btn_cancel_wizard", use_container_width=True):
            st.session_state["campaign_view"] = "list"
            st.rerun()

    # Stepper progress indicator
    step_labels = ["1. Basic Info", "2. Audience & Schedule", "3. Sequence Steps", "4. Review & Launch"]
    st_cols = st.columns(4)
    for idx, lbl in enumerate(step_labels, 1):
        with st_cols[idx-1]:
            is_cur = (step == idx)
            is_past = (step > idx)
            bg = "#083731" if is_cur else ("#E2E8F0" if not is_past else "#10B981")
            fg = "#FFFFFF" if (is_cur or is_past) else "#475569"
            st.markdown(
                f"<div style='background:{bg}; color:{fg}; padding:8px 12px; border-radius:6px; font-weight:700; font-size:12px; text-align:center;'>"
                f"{lbl}</div>",
                unsafe_allow_html=True
            )

    st.markdown("<hr style='margin:14px 0; border:0; border-top:1px solid #E2E8F0;'>", unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # WIZARD STEP 1: BASIC INFO
    # --------------------------------------------------------------------------
    if step == 1:
        st.markdown("#### Step 1: Campaign Details")
        st.caption("Give your outreach campaign an identifiable name, notes, and tags.")

        c_name = st.text_input("Campaign Name *", value=w_data.get("name", ""), placeholder="e.g. Q4 Amazon Brand Outreach Sequence")
        c_desc = st.text_area("Description / Goal", value=w_data.get("description", ""), placeholder="Targeting beauty & apparel brands for listing optimization services.")
        c_tags = st.text_input("Tags (comma-separated)", value=w_data.get("tags", ""), placeholder="Amazon, Q4, Beauty, Priority")

        w_c1, w_c2 = st.columns([8, 2])
        with w_c2:
            if st.button("Next: Audience →", type="primary", use_container_width=True):
                if not c_name.strip():
                    st.error("Campaign Name is required.")
                else:
                    w_data["name"] = c_name.strip()
                    w_data["description"] = c_desc.strip()
                    w_data["tags"] = c_tags.strip()
                    st.session_state["wizard_data"] = w_data
                    st.session_state["wizard_step"] = 2
                    st.rerun()

    # --------------------------------------------------------------------------
    # WIZARD STEP 2: AUDIENCE & SCHEDULE
    # --------------------------------------------------------------------------
    elif step == 2:
        st.markdown("#### Step 2: Audience & Sending Schedule")
        st.caption("Select which leads to enroll and configure the sending window and rate limit.")

        col_aud, col_sched = st.columns(2)
        with col_aud:
            with st.container(border=True):
                st.markdown("##### 👥 Target Audience")
                # Group leads by source
                lead_sources = sorted(list({c.get("lead_source") or "Amazon scrape" for c in all_contacts}))
                aud_options = ["All leads"] + [f"Source: {s}" for s in lead_sources]
                sel_aud = st.selectbox("Select Lead Segment", options=aud_options, index=0)

                # Compute count
                if sel_aud == "All leads":
                    matching_c = [c for c in all_contacts if (c.get("status") or "").lower() != "do not contact"]
                else:
                    src_val = sel_aud.replace("Source: ", "")
                    matching_c = [c for c in all_contacts if (c.get("lead_source") or "") == src_val and (c.get("status") or "").lower() != "do not contact"]

                st.markdown(
                    f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:10px; margin-top:10px;'>"
                    f"Selected: <strong style='color:#083731;'>{len(matching_c)} contacts</strong> (bounced and suppressed automatically excluded)"
                    f"</div>",
                    unsafe_allow_html=True
                )

        with col_sched:
            with st.container(border=True):
                st.markdown("##### ⏰ Sending Schedule")
                tz_options = ["America/New_York (US East)", "America/Los_Angeles (US West)", "Europe/London (UK)", "Asia/Karachi (Local UTC+5)"]
                tz_val = st.selectbox("Send Timezone", options=tz_options, index=0)

                t1, t2 = st.columns(2)
                with t1:
                    win_start = st.text_input("Window Start", value=w_data.get("send_window_start", "09:00"), placeholder="09:00")
                with t2:
                    win_end = st.text_input("Window End", value=w_data.get("send_window_end", "18:00"), placeholder="18:00")

                sel_days = st.multiselect(
                    "Active Days",
                    options=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    default=w_data.get("send_days", ["Mon", "Tue", "Wed", "Thu", "Fri"])
                )

                d_lim = st.number_input("Max Daily Sends for this Campaign", min_value=1, max_value=500, value=int(w_data.get("daily_limit", 50)))
                d_sec = st.number_input("Delay Between Sends (seconds)", min_value=10, max_value=300, value=int(w_data.get("delay_seconds", 60)))

        w_c1, w_c2, w_c3 = st.columns([2, 6, 2])
        with w_c1:
            if st.button("← Back", use_container_width=True):
                st.session_state["wizard_step"] = 1
                st.rerun()
        with w_c3:
            if st.button("Next: Sequence →", type="primary", use_container_width=True):
                w_data["list_id"] = sel_aud
                w_data["timezone"] = tz_val
                w_data["send_window_start"] = win_start
                w_data["send_window_end"] = win_end
                w_data["send_days"] = sel_days
                w_data["daily_limit"] = d_lim
                w_data["delay_seconds"] = d_sec
                st.session_state["wizard_data"] = w_data
                st.session_state["wizard_step"] = 3
                st.rerun()

    # --------------------------------------------------------------------------
    # WIZARD STEP 3: SEQUENCE BUILDER
    # --------------------------------------------------------------------------
    elif step == 3:
        steps_list = w_data.get("steps", [])
        updated_steps = render_sequence_builder(
            steps=steps_list,
            all_contacts=all_contacts,
            all_templates=all_templates,
            key_prefix="wiz_seq",
            read_only=False
        )
        w_data["steps"] = updated_steps
        st.session_state["wizard_data"] = w_data

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        w_c1, w_c2, w_c3 = st.columns([2, 6, 2])
        with w_c1:
            if st.button("← Back", use_container_width=True):
                st.session_state["wizard_step"] = 2
                st.rerun()
        with w_c3:
            if st.button("Next: Review →", type="primary", use_container_width=True):
                is_valid, errors = validate_sequence_steps(w_data.get("steps", []))
                if not is_valid:
                    for err in errors:
                        st.error(err)
                else:
                    st.session_state["wizard_step"] = 4
                    st.rerun()

    # --------------------------------------------------------------------------
    # WIZARD STEP 4: REVIEW & LAUNCH
    # --------------------------------------------------------------------------
    elif step == 4:
        st.markdown("#### Step 4: Pre-Launch Review & Checklist")
        st.caption("Verify your campaign configuration before launching or saving as a draft.")

        r_col1, r_col2 = st.columns(2)
        with r_col1:
            with st.container(border=True):
                st.markdown("##### 📋 Campaign Summary")
                st.markdown(f"**Name:** {w_data.get('name')}")
                st.markdown(f"**Target Audience:** {w_data.get('list_id')}")
                st.markdown(f"**Timezone:** {w_data.get('timezone')}")
                st.markdown(f"**Window:** {w_data.get('send_window_start')} – {w_data.get('send_window_end')}")
                st.markdown(f"**Active Days:** {', '.join(w_data.get('send_days', []))}")
                st.markdown(f"**Max Daily Limit:** {w_data.get('daily_limit')} emails/day")
                st.markdown(f"**Delay:** {w_data.get('delay_seconds')} seconds between emails")

        with r_col2:
            with st.container(border=True):
                st.markdown("##### 🛡️ Pre-Launch Checklist")
                steps_list = w_data.get("steps", [])

                is_seq_valid, seq_errors = validate_sequence_steps(steps_list)

                chk1 = "✅" if w_data.get("name") else "❌"
                chk2 = "✅" if len(steps_list) >= 1 else "❌"
                chk3 = "✅" if is_seq_valid else "❌"

                st.markdown(f"{chk1} Campaign metadata valid")
                st.markdown(f"{chk2} {len(steps_list)} sequence step(s) configured")
                st.markdown(f"{chk3} Token syntax and send guard check")
                if seq_errors:
                    for err in seq_errors:
                        st.warning(err)
                else:
                    st.success("All sequence steps cleared send guard checks.")

        # Bottom buttons: Save as Draft vs Launch
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        b_c1, b_c2, b_c3 = st.columns([2, 5, 5])
        with b_c1:
            if st.button("← Back to Steps", use_container_width=True):
                st.session_state["wizard_step"] = 3
                st.rerun()

        def _persist_campaign(target_status: str):
            # 1. Create campaign record
            new_cid = create_campaign(
                name=w_data["name"],
                description=w_data.get("description", ""),
                tags=w_data.get("tags", ""),
                status=target_status,
                list_id=w_data.get("list_id", "All leads"),
                timezone=w_data.get("timezone", "America/New_York"),
                send_window_start=w_data.get("send_window_start", "09:00"),
                send_window_end=w_data.get("send_window_end", "18:00"),
                send_days=",".join(w_data.get("send_days", ["Mon", "Tue", "Wed", "Thu", "Fri"])),
                daily_limit=int(w_data.get("daily_limit", 50)),
                delay_seconds=int(w_data.get("delay_seconds", 60))
            )

            # 2. Persist steps
            for stp in w_data.get("steps", []):
                create_campaign_step(
                    campaign_id=new_cid,
                    position=int(stp["position"]),
                    subject=stp["subject"],
                    body_html=stp["body_html"],
                    wait_days=int(stp.get("wait_days", 0)),
                    wait_hours=int(stp.get("wait_hours", 0)),
                    condition=stp.get("condition", "no_reply"),
                    template_id=stp.get("template_id"),
                    is_reply_thread=1 if "Re:" in stp.get("subject", "") else 0
                )

            # 3. Enroll target contacts
            sel_segment = w_data.get("list_id", "All leads")
            if sel_segment == "All leads":
                target_ids = [c["id"] for c in all_contacts if (c.get("status") or "").lower() != "do not contact"]
            else:
                src_val = sel_segment.replace("Source: ", "")
                target_ids = [c["id"] for c in all_contacts if (c.get("lead_source") or "") == src_val and (c.get("status") or "").lower() != "do not contact"]

            enrolled_cnt = enroll_contacts_in_campaign(new_cid, target_ids)

            st.session_state["campaign_view"] = "detail"
            st.session_state["campaign_active_id"] = new_cid
            trigger_toast(f"Campaign '{w_data['name']}' saved as {target_status} with {enrolled_cnt} contacts!", icon="🚀" if target_status == "Active" else "💾")
            st.rerun()

        with b_c2:
            if st.button("💾 Save as Draft", use_container_width=True, key="btn_wizard_save_draft"):
                _persist_campaign("Draft")

        with b_c3:
            if st.button("🚀 Launch Campaign", type="primary", use_container_width=True, key="btn_wizard_launch"):
                is_valid, errors = validate_sequence_steps(w_data.get("steps", []))
                if not is_valid:
                    for err in errors:
                        st.error(f"Cannot launch: {err}")
                else:
                    _persist_campaign("Active")


# -----------------------------------------------------------------------------
# VIEW 3: CAMPAIGN DETAIL (4 TABS)
# -----------------------------------------------------------------------------

def _render_campaign_detail(campaign_id: int, all_contacts: List[Dict[str, Any]], all_templates: List[Dict[str, Any]]):
    camp = get_campaign(campaign_id)
    if not camp:
        st.error(f"Campaign #{campaign_id} not found.")
        if st.button("← Back to Campaigns"):
            st.session_state["campaign_view"] = "list"
            st.rerun()
        return

    stats = get_campaign_detail_stats(campaign_id)
    steps = get_campaign_steps(campaign_id)

    # Top header bar
    dh_c1, dh_c2, dh_c3 = st.columns([6, 3, 3], vertical_alignment="center")
    with dh_c1:
        st.markdown(
            f"<div style='display:flex; align-items:center; gap:10px;'>"
            f"<h2 style='margin:0; color:#083731;'>{camp['name']}</h2>"
            f"{_render_status_badge(camp['status'])}"
            f"</div>",
            unsafe_allow_html=True
        )
        if camp.get("description"):
            st.caption(camp["description"])
    with dh_c2:
        is_active = (camp["status"] == "Active")
        btn_toggle_lbl = "⏸️ Pause Campaign" if is_active else "▶️ Resume / Launch"
        new_status = "Paused" if is_active else "Active"
        if st.button(btn_toggle_lbl, type="secondary" if is_active else "primary", use_container_width=True, key="btn_toggle_camp_status"):
            update_campaign(campaign_id, status=new_status)
            trigger_toast(f"Campaign is now {new_status}!", icon="⏸️" if new_status == "Paused" else "▶️")
            st.rerun()
    with dh_c3:
        with st.popover("⚙️ More Options", use_container_width=True):
            if st.button("📑 Duplicate Campaign", use_container_width=True):
                new_id = duplicate_campaign(campaign_id)
                st.session_state["campaign_active_id"] = new_id
                trigger_toast(f"Campaign duplicated as #{new_id}!", icon="📑")
                st.rerun()
            if st.button("📦 Archive / Mark Completed", use_container_width=True):
                update_campaign(campaign_id, status="Completed")
                trigger_toast("Campaign marked completed.", icon="📦")
                st.rerun()
            if st.button("🗑️ Delete Campaign", type="primary", use_container_width=True):
                delete_campaign(campaign_id)
                st.session_state["campaign_view"] = "list"
                trigger_toast("Campaign deleted.", icon="🗑️")
                st.rerun()

    if st.button("← Back to All Campaigns", key="btn_back_to_list"):
        st.session_state["campaign_view"] = "list"
        st.rerun()

    st.markdown("<hr style='margin:10px 0; border:0; border-top:1px solid #E2E8F0;'>", unsafe_allow_html=True)

    # 4 Detail Tabs
    d_tab1, d_tab2, d_tab3, d_tab4 = st.tabs(["📊 Overview", "✉️ Sequence", "👥 Contacts", "⚡ Activity"])

    # TAB 1: OVERVIEW
    with d_tab1:
        st.markdown("##### Campaign Funnel & KPIs")
        m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
        with m_c1:
            st.metric("Enrolled Contacts", stats["contacts"])
        with m_c2:
            st.metric("Dispatched", stats["sent"])
        with m_c3:
            st.metric("Opens", f"{stats['opened']} ({stats['open_rate']:.1f}%)")
        with m_c4:
            st.metric("Replies", f"{stats['replied']} ({stats['reply_rate']:.1f}%)")
        with m_c5:
            st.metric("Converted", stats["converted"])

        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("##### 📈 Sequence Funnel by Step")
            for stp in steps:
                pos = stp["position"]
                st.markdown(f"**Step {pos}:** {stp['subject']}")
                st.caption(f"Condition: {stp.get('condition', 'no_reply')} · Wait: {stp.get('wait_days', 0)}d {stp.get('wait_hours', 0)}h")

    # TAB 2: SEQUENCE
    with d_tab2:
        st.markdown("##### Sequence Steps & Content")
        is_read_only = (camp["status"] == "Active")
        if is_read_only:
            st.info("💡 **Active campaign:** Sequence steps are currently active and running. Pause the campaign to edit step contents.")
            render_sequence_builder(
                steps=steps,
                all_contacts=all_contacts,
                all_templates=all_templates,
                key_prefix=f"detail_seq_{campaign_id}",
                read_only=True
            )
        else:
            edit_key = f"camp_detail_steps_{campaign_id}"
            if edit_key not in st.session_state:
                st.session_state[edit_key] = [dict(s) for s in steps]

            updated_steps = render_sequence_builder(
                steps=st.session_state[edit_key],
                all_contacts=all_contacts,
                all_templates=all_templates,
                key_prefix=f"detail_seq_{campaign_id}",
                read_only=False
            )
            st.session_state[edit_key] = updated_steps

            st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
            sc_c1, sc_c2 = st.columns([7, 3])
            with sc_c2:
                if st.button("💾 Save Sequence Changes", type="primary", use_container_width=True, key=f"btn_save_seq_{campaign_id}"):
                    is_valid, errors = validate_sequence_steps(updated_steps)
                    if not is_valid:
                        for err in errors:
                            st.error(err)
                    else:
                        sync_campaign_steps(campaign_id, updated_steps)
                        if edit_key in st.session_state:
                            del st.session_state[edit_key]
                        trigger_toast("Sequence steps saved successfully!", icon="💾")
                        st.rerun()

    # TAB 3: CONTACTS
    with d_tab3:
        st.markdown("##### Enrolled Prospects")
        camp_contacts = get_campaign_contacts(campaign_id)

        top_act1, top_act2 = st.columns([8, 2], vertical_alignment="center")
        with top_act1:
            st.caption(f"Showing {len(camp_contacts)} enrolled leads.")
        with top_act2:
            if camp_contacts:
                df_export = pd.DataFrame(camp_contacts)
                csv_bytes = df_export.to_csv(index=False).encode("utf-8")
                st.download_button("📤 Export CSV", data=csv_bytes, file_name=f"campaign_{campaign_id}_contacts.csv", mime="text/csv", use_container_width=True)

        if not camp_contacts:
            st.info("No contacts enrolled in this campaign yet.")
        else:
            for cc in camp_contacts[:40]:
                with st.container(border=True):
                    c_col1, c_col2, c_col3, c_col4 = st.columns([4, 2, 2, 2], vertical_alignment="center")
                    with c_col1:
                        st.markdown(f"**{cc.get('name') or 'Unnamed'}** · {cc.get('email')}")
                        st.caption(f"Brand: {cc.get('company') or 'N/A'}")
                    with c_col2:
                        st.markdown(f"State: `{(cc.get('state') or 'pending').upper()}`")
                    with c_col3:
                        st.markdown(f"Step Reached: **#{cc.get('current_step', 0)}**")
                    with c_col4:
                        if not cc.get("converted"):
                            if st.button("⭐ Convert", key=f"btn_conv_{cc['id']}", use_container_width=True):
                                mark_campaign_contact_converted(cc["id"])
                                trigger_toast(f"Marked {cc.get('name')} as converted!", icon="⭐")
                                st.rerun()
                        else:
                            st.markdown("⭐ **Converted**")

    # TAB 4: ACTIVITY
    with d_tab4:
        st.markdown("##### Live Activity Feed")
        events = get_campaign_events(campaign_id, limit=50)
        if not events:
            st.info("No activity recorded yet for this campaign.")
        else:
            for ev in events:
                dot = "🟢" if ev["event_type"] == "sent" else ("🔵" if ev["event_type"] == "opened" else "⭐")
                st.markdown(
                    f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:8px 12px; margin-bottom:6px; font-size:12px;'>"
                    f"{dot} <b>{ev['event_type'].upper()}</b> · Lead: <b>{ev.get('name') or ev.get('email') or 'Unknown'}</b> "
                    f"<span style='float:right; color:#94A3B8;'>{ev['created_at'][:19]}</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )


# -----------------------------------------------------------------------------
# MAIN TAB ENTRYPOINT
# -----------------------------------------------------------------------------

def render_campaigns_tab(all_contacts: List[Dict[str, Any]], all_templates: List[Dict[str, Any]]):
    """Main tab router for the Campaigns screen."""
    if "campaign_view" not in st.session_state:
        st.session_state["campaign_view"] = "list"

    view = st.session_state["campaign_view"]

    if view == "wizard":
        _render_campaign_wizard(all_contacts, all_templates)
    elif view == "detail":
        cid = st.session_state.get("campaign_active_id", 0)
        _render_campaign_detail(cid, all_contacts, all_templates)
    else:
        _render_campaigns_list(all_contacts, all_templates)
