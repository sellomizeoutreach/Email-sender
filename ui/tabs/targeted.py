"""
ui/tabs/targeted.py - Dedicated 1:1 Single Targeted Email & Follow-Up Workspace.

Strictly separated from Bulk Campaigns:
- Level 1: Pipeline Home (leads grouped by action needed: Drafting, Scheduled/Sent, Follow-up Due, Replied, Closed)
- Level 2: Thread Workspace (Focused 3-Pane Environment: Who + History | Editor | Sequence Builder)

Zero external AI/API dependencies. Fast, deterministic, 100% offline-capable.
"""

from datetime import datetime, timedelta
import html
import json
import re
import streamlit as st
from typing import Dict, Any, List, Optional

from database import (
    get_contacts,
    get_contact_by_id,
    get_contact_by_email,
    create_contact,
    update_contact,
    create_email,
    get_emails,
    update_email,
    mark_email_sent,
    get_thread_history,
    get_targeted_pipeline_leads,
    get_proof_stories,
    get_smtp_accounts,
    get_next_available_smtp_account,
    increment_smtp_sent,
    create_sequence_rule,
    get_config,
    advance_contact_followup,
    DB_FILE,
)
from smtp_dispatcher import send_smtp_email, sanitize_header
from tracker import wrap_links_with_click_tracking, inject_tracking_pixel
import template_engine as te

resolve_template = getattr(te, "resolve_template", lambda b, c: str(b))
format_email_html = getattr(te, "format_email_html", lambda b: f"<p>{b}</p>")
inject_variables = getattr(te, "inject_variables", lambda t, c: str(t))
parse_spintax = getattr(te, "parse_spintax", lambda t: str(t))
scan_all_negative_keywords = getattr(te, "scan_all_negative_keywords", lambda t, k: [])
audit_email_deliverability = getattr(te, "audit_email_deliverability", lambda t, k: (100, []))
_missing_tokens = getattr(te, "_missing_tokens", lambda t: [])


# Built-in deterministic 1:1 outreach frameworks (Zero AI required)
TARGETED_FRAMEWORKS = {
    1: [
        {
            "name": "Specific Observation & Soft Question (Recommended)",
            "subject": "Quick question regarding {company}'s listing",
            "body": (
                "Hi {first_name},<br><br>"
                "I was looking at {company}'s store and noticed {observation}.<br><br>"
                "Usually when that happens, {pain_point}. "
                "{proof_story}<br><br>"
                "Are you open to a quick 3-minute video breakdown of how to fix this?<br><br>"
                "Best,"
            )
        },
        {
            "name": "Compliment + Growth Opportunity",
            "subject": "Loved {company}'s approach to {offer_angle}",
            "body": (
                "Hi {first_name},<br><br>"
                "Really impressed by {compliment}.<br><br>"
                "While reviewing your category, I spotted {observation}. "
                "We recently helped another brand solve this exact issue: {proof_story}<br><br>"
                "Worth a quick chat this week to share the findings?<br><br>"
                "Best,"
            )
        },
        {
            "name": "Trigger Event Hook",
            "subject": "{trigger_event} & {company}",
            "body": (
                "Hi {first_name},<br><br>"
                "With {trigger_event} coming up, I took a look at {company}'s catalog.<br><br>"
                "One thing stood out right away: {observation}. "
                "If unaddressed, {pain_point}.<br><br>"
                "We tackled this for another partner: {proof_story}<br><br>"
                "Would it make sense to connect for 10 minutes on Thursday?<br><br>"
                "Best,"
            )
        }
    ],
    2: [
        {
            "name": "Quick Bump (Threaded Follow-up 1)",
            "subject": "Re: Quick question regarding {company}'s listing",
            "body": (
                "Hi {first_name},<br><br>"
                "Wanted to make sure my previous note about {observation} didn't get buried.<br><br>"
                "Did you get a chance to take a look?<br><br>"
                "Best,"
            )
        },
        {
            "name": "Additional Context & Asset",
            "subject": "Re: Quick question regarding {company}'s listing",
            "body": (
                "Hi {first_name},<br><br>"
                "Following up on {company}. I put together a quick mockup showing how we fixed {observation} "
                "and prevented {pain_point}.<br><br>"
                "Happy to send over the screenshot if you're curious.<br><br>"
                "Best,"
            )
        }
    ],
    3: [
        {
            "name": "Relevant Case Study Snippet (Follow-up 2)",
            "subject": "Re: Quick question regarding {company}'s listing",
            "body": (
                "Hi {first_name},<br><br>"
                "Thought of {company} today—{proof_story}<br><br>"
                "Figured this might be top of mind given {pain_point}. "
                "Open to a brief 10-minute sync sometime next week?<br><br>"
                "Best,"
            )
        }
    ],
    4: [
        {
            "name": "Clean Break-Up (Follow-up 3)",
            "subject": "Re: Quick question regarding {company}'s listing",
            "body": (
                "Hi {first_name},<br><br>"
                "I know you're busy growing {company}, so I won't keep following up.<br><br>"
                "If fixing {observation} or tackling {pain_point} becomes a priority down the road, "
                "feel free to reach back out.<br><br>"
                "Wishing you and the team continued success!<br><br>"
                "Best,"
            )
        }
    ]
}


def _calculate_reading_time_secs(text: str) -> int:
    """Estimate reading time in seconds based on 200 words per minute."""
    if not text:
        return 0
    clean = re.sub(r'<[^>]+>', ' ', text)
    words = [w for w in clean.split() if w.strip()]
    count = len(words)
    secs = int((count / 200.0) * 60.0)
    return max(5, secs) if count > 0 else 0


def render_targeted_tab():
    """Main entrypoint for 1:1 Targeted Outreach Thread Workspace."""
    active_cid = st.session_state.get("targeted_active_contact_id")

    if active_cid:
        _render_thread_workspace(int(active_cid))
    else:
        _render_pipeline_home()


# ==============================================================================
# LEVEL 1: PIPELINE HOME
# ==============================================================================

def _render_pipeline_home():
    """Level 1: Pipeline Home showing leads grouped by action needed."""
    st.markdown("""
        <div style="margin-bottom: 1.2rem;">
            <div style="font-size: 1.5rem; font-weight: 800; color: #083731; letter-spacing: -0.02em;">
                🎯 1:1 Targeted Outreach Pipeline
            </div>
            <div style="font-size: 0.88rem; color: #4A5568; margin-top: 2px;">
                Personalized, high-conviction 1:1 threads. Every prospect has a dedicated conversation timeline.
            </div>
        </div>
    """, unsafe_allow_html=True)

    pipeline = get_targeted_pipeline_leads()
    c_draft = pipeline["drafting"]
    c_sched = pipeline["scheduled_sent"]
    c_due = pipeline["followup_due"]
    c_replied = pipeline["replied"]
    c_closed = pipeline["closed"]

    # 5 High-Impact Metric Cards
    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    with col_m1:
        st.markdown(f"""
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 12px; text-align: center;">
                <div style="font-size: 0.76rem; font-weight: 700; color: #64748B; text-transform: uppercase;">📝 Drafting</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #1E293B; margin-top: 4px;">{len(c_draft)}</div>
                <div style="font-size: 0.72rem; color: #94A3B8;">Ready to research & pitch</div>
            </div>
        """, unsafe_allow_html=True)

    with col_m2:
        st.markdown(f"""
            <div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 10px; padding: 12px; text-align: center;">
                <div style="font-size: 0.76rem; font-weight: 700; color: #15803D; text-transform: uppercase;">⏳ Scheduled / Sent</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #166534; margin-top: 4px;">{len(c_sched)}</div>
                <div style="font-size: 0.72rem; color: #4ADE80;">Awaiting reply</div>
            </div>
        """, unsafe_allow_html=True)

    with col_m3:
        st.markdown(f"""
            <div style="background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 10px; padding: 12px; text-align: center;">
                <div style="font-size: 0.76rem; font-weight: 700; color: #B45309; text-transform: uppercase;">⏰ Follow-up Due</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #92400E; margin-top: 4px;">{len(c_due)}</div>
                <div style="font-size: 0.72rem; color: #F59E0B;">Action today / upcoming</div>
            </div>
        """, unsafe_allow_html=True)

    with col_m4:
        st.markdown(f"""
            <div style="background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 10px; padding: 12px; text-align: center;">
                <div style="font-size: 0.76rem; font-weight: 700; color: #1D4ED8; text-transform: uppercase;">💬 Replied</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #1E40AF; margin-top: 4px;">{len(c_replied)}</div>
                <div style="font-size: 0.72rem; color: #3B82F6;">Hot leads needing response</div>
            </div>
        """, unsafe_allow_html=True)

    with col_m5:
        st.markdown(f"""
            <div style="background: #F1F5F9; border: 1px solid #CBD5E1; border-radius: 10px; padding: 12px; text-align: center;">
                <div style="font-size: 0.76rem; font-weight: 700; color: #475569; text-transform: uppercase;">🏁 Closed</div>
                <div style="font-size: 1.6rem; font-weight: 800; color: #334155; margin-top: 4px;">{len(c_closed)}</div>
                <div style="font-size: 0.72rem; color: #64748B;">Won, Booked, or Ended</div>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height: 18px;'></div>", unsafe_allow_html=True)

    # Action bar: Filter stage & New Thread
    col_search, col_filter, col_btn = st.columns([1.6, 1.2, 1.2], vertical_alignment="center")

    with col_search:
        search_query = st.text_input("🔍 Search prospect or company...", key="pipeline_search", placeholder="Type name, company, or email...").strip().lower()

    with col_filter:
        stage_options = ["All Stages", "📝 Drafting", "⏳ Scheduled / Sent", "⏰ Follow-up Due", "💬 Replied", "🏁 Closed"]
        selected_stage = st.selectbox("Pipeline Filter", stage_options, key="pipeline_stage_filter")

    with col_btn:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        open_new_thread_modal = st.button("➕ New Targeted Thread", type="primary", use_container_width=True)

    # New Targeted Thread Form Expander
    if open_new_thread_modal or st.session_state.get("show_new_thread_form"):
        st.session_state["show_new_thread_form"] = True
        with st.container(border=True):
            st.markdown("<strong style='font-size: 1.05rem; color: #083731;'>🎯 Start a New Targeted Outreach Thread</strong>", unsafe_allow_html=True)
            st.caption("Target an existing contact from your CRM or add a fresh prospect directly.")

            crm_contacts = get_contacts()
            contact_choices = ["-- Add New Prospect Directly --"] + [f"{c['name']} ({c['company'] or 'No Company'} - {c['email']})" for c in crm_contacts]
            chosen_crm = st.selectbox("Select Contact", contact_choices, key="new_thread_choice")

            if chosen_crm == "-- Add New Prospect Directly --":
                c1, c2, c3 = st.columns(3)
                with c1:
                    new_name = st.text_input("Prospect Full Name*", key="nt_name", placeholder="e.g. Sarah Jenkins")
                with c2:
                    new_company = st.text_input("Company / Brand Name*", key="nt_company", placeholder="e.g. Apex Wellness")
                with c3:
                    new_email = st.text_input("Prospect Email Address*", key="nt_email", placeholder="sarah@apexwellness.com")

                c4, c5 = st.columns(2)
                with c4:
                    new_role = st.text_input("Role / Title", key="nt_role", placeholder="Founder & CMO")
                with c5:
                    new_website = st.text_input("Website / Store URL", key="nt_website", placeholder="https://apexwellness.com")

                new_obs = st.text_input("Specific Observation (What caught your eye?)", key="nt_obs", placeholder="e.g. Hero image is blurry and mobile A+ module is missing ingredient comparison")

                btn_c1, btn_c2 = st.columns([1.5, 3])
                with btn_c1:
                    if st.button("🚀 Open Workspace & Start Thread", type="primary", key="nt_submit"):
                        if not new_name.strip() or not new_email.strip():
                            st.error("Name and Email are required to start a targeted thread.")
                        else:
                            clean_em = new_email.strip().lower()
                            existing = get_contact_by_email(clean_em)
                            if existing:
                                target_id = existing["id"]
                            else:
                                custom_vars = {
                                    "role": new_role.strip(),
                                    "website": new_website.strip(),
                                    "observation": new_obs.strip(),
                                    "specific_observation": new_obs.strip(),
                                }
                                target_id = create_contact(
                                    name=new_name.strip(),
                                    email=clean_em,
                                    company=new_company.strip(),
                                    custom_variables=custom_vars,
                                    status="Drafted",
                                    notes="Created via 1:1 Targeted Thread Builder"
                                )
                            st.session_state["targeted_active_contact_id"] = target_id
                            st.session_state["show_new_thread_form"] = False
                            st.rerun()
                with btn_c2:
                    if st.button("Cancel", key="nt_cancel"):
                        st.session_state["show_new_thread_form"] = False
                        st.rerun()
            else:
                # Picked existing contact
                sel_idx = contact_choices.index(chosen_crm) - 1
                picked = crm_contacts[sel_idx]
                if st.button(f"🚀 Open Thread for {picked['name']}", type="primary", key="nt_open_existing"):
                    st.session_state["targeted_active_contact_id"] = picked["id"]
                    st.session_state["show_new_thread_form"] = False
                    st.rerun()

    # Collect leads for display based on filter
    displayed_leads: List[Tuple[str, Dict[str, Any]]] = []

    def _matches_search(ld: Dict[str, Any]) -> bool:
        if not search_query:
            return True
        return (search_query in (ld.get("name") or "").lower() or
                search_query in (ld.get("company") or "").lower() or
                search_query in (ld.get("email") or "").lower())

    if selected_stage in ["All Stages", "💬 Replied"]:
        for ld in c_replied:
            if _matches_search(ld):
                displayed_leads.append(("Replied", ld))

    if selected_stage in ["All Stages", "⏰ Follow-up Due"]:
        for ld in c_due:
            if _matches_search(ld):
                displayed_leads.append(("Follow-up Due", ld))

    if selected_stage in ["All Stages", "⏳ Scheduled / Sent"]:
        for ld in c_sched:
            if _matches_search(ld):
                displayed_leads.append(("Scheduled / Sent", ld))

    if selected_stage in ["All Stages", "📝 Drafting"]:
        for ld in c_draft:
            if _matches_search(ld):
                displayed_leads.append(("Drafting", ld))

    if selected_stage in ["All Stages", "🏁 Closed"]:
        for ld in c_closed:
            if _matches_search(ld):
                displayed_leads.append(("Closed", ld))

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

    if not displayed_leads:
        st.info("No leads found matching your filter criteria. Click '➕ New Targeted Thread' above to begin.")
        return

    # Render Clean Leads List
    st.markdown(f"<div style='font-size: 0.88rem; font-weight: 700; color: #475569; margin-bottom: 8px;'>Showing {len(displayed_leads)} prospect thread(s)</div>", unsafe_allow_html=True)

    badge_styles = {
        "Replied": "background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE;",
        "Follow-up Due": "background: #FFFBEB; color: #B45309; border: 1px solid #FDE68A;",
        "Scheduled / Sent": "background: #F0FDF4; color: #15803D; border: 1px solid #BBF7D0;",
        "Drafting": "background: #F8FAFC; color: #475569; border: 1px solid #E2E8F0;",
        "Closed": "background: #F1F5F9; color: #334155; border: 1px solid #CBD5E1;"
    }

    for stage_label, lead in displayed_leads:
        with st.container(border=True):
            r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns([1.8, 1.1, 1.1, 1.1, 1.0], vertical_alignment="center")

            with r_col1:
                st.markdown(f"""
                    <div style="font-weight: 700; font-size: 0.95rem; color: #0F172A;">
                        {lead['name']}
                    </div>
                    <div style="font-size: 0.8rem; color: #64748B;">
                        {lead['company'] or 'Independent'} &bull; <span style="font-family: monospace;">{lead['email']}</span>
                    </div>
                """, unsafe_allow_html=True)

            with r_col2:
                b_style = badge_styles.get(stage_label, "background: #F1F5F9; color: #334155;")
                unread_badge = f"<span style='background:#EF4444; color:#fff; border-radius:10px; padding:1px 6px; font-size:0.7rem; margin-left:4px;'>{lead['unread_replies_count']} reply</span>" if lead.get("unread_replies_count", 0) > 0 else ""
                st.markdown(f"""
                    <span style="{b_style} padding: 3px 8px; border-radius: 6px; font-size: 0.76rem; font-weight: 700;">
                        {stage_label}
                    </span>
                    {unread_badge}
                """, unsafe_allow_html=True)

            with r_col3:
                last_dt = lead.get("last_contact_date") or "Never"
                st.markdown(f"""
                    <div style="font-size: 0.72rem; color: #94A3B8; text-transform: uppercase; font-weight: 600;">Last Activity</div>
                    <div style="font-size: 0.82rem; color: #334155; font-weight: 500;">{last_dt}</div>
                """, unsafe_allow_html=True)

            with r_col4:
                next_dt = lead.get("next_follow_up") or "None"
                st.markdown(f"""
                    <div style="font-size: 0.72rem; color: #94A3B8; text-transform: uppercase; font-weight: 600;">Next Action</div>
                    <div style="font-size: 0.82rem; color: #334155; font-weight: 500;">{next_dt}</div>
                """, unsafe_allow_html=True)

            with r_col5:
                if st.button("🎯 Open Thread", key=f"btn_open_thread_{lead['id']}", use_container_width=True, type="secondary"):
                    st.session_state["targeted_active_contact_id"] = lead["id"]
                    st.rerun()


# ==============================================================================
# LEVEL 2: THREAD WORKSPACE (FOCUSED 3-PANE ENVIRONMENT)
# ==============================================================================

def _render_thread_workspace(contact_id: int):
    """
    Level 2: Focused 3-Pane Thread Workspace for a single prospect.
    Left Pane: Who + History (Compact profile, 10 research fields, conversation timeline)
    Center Pane: Unified Editor (Write / Generate, variable chips, snippets, live preview, deliverability, send guard)
    Right Pane: Sequence Builder & Timeline (Touches 1-4, delay, threading toggle, full narrative preview)
    """
    history_data = get_thread_history_by_id(contact_id)
    contact = history_data.get("contact")

    if not contact:
        st.error(f"Contact #{contact_id} not found.")
        if st.button("⬅ Back to Pipeline"):
            st.session_state["targeted_active_contact_id"] = None
            st.rerun()
        return

    # Top Navigation Breadcrumb & Quick Status Bar
    b_col1, b_col2, b_col3 = st.columns([1.5, 2.5, 1.2], vertical_alignment="center")
    with b_col1:
        if st.button("⬅ Back to Pipeline", key="btn_back_to_pipeline", type="secondary"):
            st.session_state["targeted_active_contact_id"] = None
            st.rerun()

    with b_col2:
        st.markdown(f"""
            <div style="font-size: 1.15rem; font-weight: 800; color: #083731;">
                {contact['name']} <span style="font-weight: 400; color: #64748B;">({contact.get('company') or 'Independent'})</span>
            </div>
        """, unsafe_allow_html=True)

    with b_col3:
        status_options = [
            "New", "Researched", "Drafted", "Sent", "Follow-Up 1", "Follow-Up 2",
            "Replied", "Interested", "Meeting Booked", "Not Interested", "Bounced", "Do Not Contact"
        ]
        curr_status = contact.get("status") or "Drafted"
        curr_idx = status_options.index(curr_status) if curr_status in status_options else 0
        new_status = st.selectbox("Stage", status_options, index=curr_idx, key=f"ws_status_{contact_id}")
        if new_status != curr_status:
            update_contact(contact_id, status=new_status)
            st.toast(f"Status updated to '{new_status}'", icon="🏷️")
            st.rerun()

    st.markdown("<hr style='margin: 0.6rem 0 1.2rem 0; border: none; border-top: 1px solid #E2E8F0;'>", unsafe_allow_html=True)

    # 3-Pane Layout
    col_who, col_editor, col_seq = st.columns([1.1, 1.8, 1.1])

    # --------------------------------------------------------------------------
    # LEFT PANE: WHO + HISTORY
    # --------------------------------------------------------------------------
    with col_who:
        st.markdown("<strong style='font-size: 0.95rem; color: #083731;'>👤 Prospect & Research</strong>", unsafe_allow_html=True)
        st.caption("10 structured research fields powering deterministic personalization.")

        custom_vars = contact.get("custom_variables_dict") or {}

        # 10 Structured Research Fields
        first_name_val = custom_vars.get("first_name") or contact["name"].split()[0] if contact.get("name") else ""
        edit_first_name = st.text_input("First Name", value=first_name_val, key="rf_first_name")
        edit_company = st.text_input("Company / Brand", value=contact.get("company") or "", key="rf_company")
        edit_role = st.text_input("Role / Title", value=custom_vars.get("role") or "", key="rf_role")
        edit_website = st.text_input("Website / Store URL", value=custom_vars.get("website") or "", key="rf_website")
        edit_obs = st.text_area("Specific Observation", value=custom_vars.get("observation") or custom_vars.get("specific_observation") or "", height=70, key="rf_obs", placeholder="What did you observe on their store/listing?")
        edit_pain = st.text_area("Pain Point", value=custom_vars.get("pain_point") or "", height=60, key="rf_pain", placeholder="What negative consequence is happening?")
        edit_comp = st.text_input("Compliment / Praise", value=custom_vars.get("compliment") or "", key="rf_comp", placeholder="Genuine praise about their brand/product")
        edit_angle = st.text_input("Offer Angle", value=custom_vars.get("offer_angle") or "Listing Optimization", key="rf_angle", placeholder="Creative / A+, PPC, Listing, Full")
        edit_trigger = st.text_input("Trigger Event", value=custom_vars.get("trigger_event") or "", key="rf_trigger", placeholder="e.g. Q4 prep, launched new line")

        # Proof Story Selector
        proof_stories = get_proof_stories()
        ps_names = ["-- Custom / Freeform --"] + [f"{ps['client_name']} ({ps['metric_highlight']})" for ps in proof_stories]
        curr_ps = custom_vars.get("proof_story") or ""
        ps_idx = 0
        for i, opt in enumerate(ps_names):
            if i > 0 and proof_stories[i-1]["headline"] in curr_ps:
                ps_idx = i
                break
        selected_ps_opt = st.selectbox("Proof Story Snippet", ps_names, index=ps_idx, key="rf_ps_select")
        if selected_ps_opt != "-- Custom / Freeform --":
            ps_obj = proof_stories[ps_names.index(selected_ps_opt) - 1]
            edit_proof = ps_obj["full_story_snippet"]
        else:
            edit_proof = st.text_area("Proof Snippet", value=curr_ps, height=60, key="rf_proof", placeholder="Case study metric highlight")

        if st.button("💾 Save Research", type="primary", key="btn_save_research", use_container_width=True):
            updated_cv = dict(custom_vars)
            updated_cv["first_name"] = edit_first_name.strip()
            updated_cv["role"] = edit_role.strip()
            updated_cv["website"] = edit_website.strip()
            updated_cv["observation"] = edit_obs.strip()
            updated_cv["specific_observation"] = edit_obs.strip()
            updated_cv["pain_point"] = edit_pain.strip()
            updated_cv["compliment"] = edit_comp.strip()
            updated_cv["offer_angle"] = edit_angle.strip()
            updated_cv["trigger_event"] = edit_trigger.strip()
            updated_cv["proof_story"] = edit_proof.strip()

            update_contact(
                contact_id=contact_id,
                company=edit_company.strip(),
                custom_variables=updated_cv
            )
            st.toast("Research fields saved!", icon="💾")
            st.rerun()

        st.markdown("<div style='height: 18px;'></div>", unsafe_allow_html=True)
        st.markdown("<strong style='font-size: 0.92rem; color: #083731;'>📜 Conversation Timeline</strong>", unsafe_allow_html=True)

        timeline = history_data.get("timeline") or []
        if not timeline:
            st.caption("No emails sent or received yet. You are at the start of this conversation.")
        else:
            with st.container(height=320):
                for event in timeline:
                    ev_type = event["type"]
                    ev_date = event["timestamp"][:16] if event.get("timestamp") else "Unknown date"

                    if ev_type == "reply_received":
                        st.markdown(f"""
                            <div style="background: #EFF6FF; border-left: 3px solid #3B82F6; padding: 6px 10px; margin-bottom: 8px; border-radius: 4px;">
                                <div style="display: flex; justify-content: space-between; font-size: 0.74rem; color: #1E40AF; font-weight: 700;">
                                    <span>💬 Incoming Reply</span>
                                    <span>{ev_date}</span>
                                </div>
                                <div style="font-size: 0.8rem; font-weight: 600; color: #1E3A8A; margin-top: 2px;">{html.escape(event['subject'])}</div>
                                <div style="font-size: 0.74rem; color: #3B82F6; margin-top: 2px;">{html.escape(event['body'][:120])}...</div>
                            </div>
                        """, unsafe_allow_html=True)
                    elif ev_type == "email_sent":
                        step_lbl = f"Touch {event.get('step') or 1}"
                        open_info = f" • 👁️ {event.get('open_count')} opens" if event.get("open_count", 0) > 0 else ""
                        click_info = f" • 🔗 {event.get('click_count')} clicks" if event.get("click_count", 0) > 0 else ""
                        st.markdown(f"""
                            <div style="background: #F0FDF4; border-left: 3px solid #22C55E; padding: 6px 10px; margin-bottom: 8px; border-radius: 4px;">
                                <div style="display: flex; justify-content: space-between; font-size: 0.74rem; color: #15803D; font-weight: 700;">
                                    <span>📤 Sent ({step_lbl}){open_info}{click_info}</span>
                                    <span>{ev_date}</span>
                                </div>
                                <div style="font-size: 0.8rem; font-weight: 600; color: #064E3B; margin-top: 2px;">{html.escape(event['subject'])}</div>
                            </div>
                        """, unsafe_allow_html=True)
                    elif ev_type == "email_scheduled":
                        step_lbl = f"Touch {event.get('step') or 1}"
                        st.markdown(f"""
                            <div style="background: #FFFBEB; border-left: 3px solid #F59E0B; padding: 6px 10px; margin-bottom: 8px; border-radius: 4px;">
                                <div style="display: flex; justify-content: space-between; font-size: 0.74rem; color: #B45309; font-weight: 700;">
                                    <span>⏳ Scheduled ({step_lbl})</span>
                                    <span>{ev_date}</span>
                                </div>
                                <div style="font-size: 0.8rem; font-weight: 600; color: #78350F; margin-top: 2px;">{html.escape(event['subject'])}</div>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
                            <div style="background: #F8FAFC; border-left: 3px solid #94A3B8; padding: 6px 10px; margin-bottom: 8px; border-radius: 4px;">
                                <div style="display: flex; justify-content: space-between; font-size: 0.74rem; color: #64748B; font-weight: 700;">
                                    <span>📝 Draft</span>
                                    <span>{ev_date}</span>
                                </div>
                                <div style="font-size: 0.8rem; color: #334155; margin-top: 2px;">{html.escape(event['subject'])}</div>
                            </div>
                        """, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # CENTER PANE: UNIFIED EDITOR
    # --------------------------------------------------------------------------
    with col_editor:
        st.markdown("<strong style='font-size: 1.05rem; color: #083731;'>✍️ The Active Touch Editor</strong>", unsafe_allow_html=True)

        # Active Touch Step Selector
        touch_keys = {
            "Initial Pitch (Touch 1)": 1,
            "Follow-up 1 (Touch 2)": 2,
            "Follow-up 2 (Touch 3)": 3,
            "Follow-up 3 (Touch 4)": 4
        }
        active_step_label = st.radio(
            "Select Active Touch",
            list(touch_keys.keys()),
            horizontal=True,
            key=f"ws_active_touch_{contact_id}"
        )
        active_step_num = touch_keys[active_step_label]

        # Mode Selector: Write (Freeform) vs Generate (Framework)
        ed_col1, ed_col2 = st.columns([1.2, 2.0], vertical_alignment="center")
        with ed_col1:
            editor_mode = st.radio("Mode", ["✍️ Manual Write", "⚡ Generate from Framework"], horizontal=True, key=f"ws_mode_{contact_id}_{active_step_num}")

        # If Generate mode: offer framework picker
        draft_state_key = f"ws_draft_{contact_id}_{active_step_num}"
        subject_state_key = f"ws_subject_{contact_id}_{active_step_num}"

        if "⚡ Generate" in editor_mode:
            step_frameworks = TARGETED_FRAMEWORKS.get(active_step_num, TARGETED_FRAMEWORKS[1])
            fw_names = [f["name"] for f in step_frameworks]
            selected_fw_name = st.selectbox("Choose Framework", fw_names, key=f"ws_fw_choice_{contact_id}_{active_step_num}")
            selected_fw = next(f for f in step_frameworks if f["name"] == selected_fw_name)

            if st.button("Apply Framework Template", key=f"btn_apply_fw_{contact_id}_{active_step_num}", type="secondary"):
                st.session_state[subject_state_key] = selected_fw["subject"]
                st.session_state[draft_state_key] = selected_fw["body"]
                st.rerun()

        # Variable Chips & Quick Insertion
        with st.expander("🏷️ Variable Chips & Snippet Shortcuts", expanded=False):
            st.markdown("""
                <div style="font-size: 0.8rem; color: #475569; margin-bottom: 6px;">
                    Click to reference any prospect research field:
                </div>
            """, unsafe_allow_html=True)

            chip_cols = st.columns(4)
            with chip_cols[0]:
                st.code("{first_name}")
                st.code("{company}")
            with chip_cols[1]:
                st.code("{role}")
                st.code("{website}")
            with chip_cols[2]:
                st.code("{observation}")
                st.code("{pain_point}")
            with chip_cols[3]:
                st.code("{proof_story}")
                st.code("{compliment}")

            st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
            snip_col1, snip_col2 = st.columns(2)
            with snip_col1:
                if st.button("+ Insert CTA Button", key=f"btn_ins_cta_{contact_id}_{active_step_num}"):
                    curr_b = st.session_state.get(draft_state_key, "")
                    cta_html = '<p style="text-align: center; margin: 18px 0;"><a href="https://calendly.com" style="background-color: #083731; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">Book 15-Minute Audit Call</a></p>'
                    st.session_state[draft_state_key] = curr_b + "<br>" + cta_html
                    st.rerun()
            with snip_col2:
                if st.button("+ Insert Saved Signature", key=f"btn_ins_sig_{contact_id}_{active_step_num}"):
                    curr_b = st.session_state.get(draft_state_key, "")
                    sig_html = get_config("signature_html") or "<p>Best regards,<br>Outreach Team</p>"
                    st.session_state[draft_state_key] = curr_b + "<br><br>" + sig_html
                    st.rerun()

        # Load defaults if empty
        if subject_state_key not in st.session_state:
            if active_step_num == 1:
                st.session_state[subject_state_key] = f"Quick question regarding {contact.get('company') or contact['name']}'s listing"
            else:
                st.session_state[subject_state_key] = f"Re: Quick question regarding {contact.get('company') or contact['name']}'s listing"

        if draft_state_key not in st.session_state:
            default_fw = TARGETED_FRAMEWORKS.get(active_step_num, TARGETED_FRAMEWORKS[1])[0]
            st.session_state[draft_state_key] = default_fw["body"]

        # Subject & Body Inputs
        subj_val = st.text_input("Subject Line", value=st.session_state[subject_state_key], key=f"in_subj_{contact_id}_{active_step_num}")
        st.session_state[subject_state_key] = subj_val

        body_val = st.text_area(
            "Email Body (HTML / Rich Text)",
            value=st.session_state[draft_state_key],
            height=200,
            key=f"in_body_{contact_id}_{active_step_num}"
        )
        st.session_state[draft_state_key] = body_val

        # Resolved Live Preview & Deliverability Analysis
        resolved_subject = inject_variables(parse_spintax(subj_val), contact)
        resolved_body = resolve_template(body_val, contact)
        final_html = format_email_html(resolved_body)

        with st.expander("👁️ Live Resolved Preview (Exact Recipient View)", expanded=True):
            st.markdown(f"""
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px;">
                    <div style="font-size: 0.78rem; color: #64748B; margin-bottom: 4px;">
                        <strong>To:</strong> {contact['email']} &nbsp;|&nbsp; <strong>Subject:</strong> {html.escape(resolved_subject)}
                    </div>
                    <div style="border-top: 1px solid #E2E8F0; margin: 6px 0 10px;"></div>
                    <div style="font-size: 0.9rem; color: #1E293B; line-height: 1.5;">
                        {resolved_body}
                    </div>
                </div>
            """, unsafe_allow_html=True)

        # Inline Deliverability, Quality & Send Guard
        neg_keywords_str = get_config("negative_keywords") or "free, guarantee, 100%, act now, cash"
        triggers = scan_all_negative_keywords(f"{resolved_subject} {resolved_body}", neg_keywords_str)
        read_secs = _calculate_reading_time_secs(resolved_body)

        deliv_col1, deliv_col2 = st.columns(2)
        with deliv_col1:
            if triggers:
                st.markdown(f"<div style='color: #DC2626; font-size: 0.8rem; font-weight: 700;'>⚠️ Negative Trigger Words Found: {', '.join(triggers)}</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='color: #16A34A; font-size: 0.8rem; font-weight: 600;'>✅ No spam trigger keywords detected</div>", unsafe_allow_html=True)

        with deliv_col2:
            st.markdown(f"<div style='font-size: 0.8rem; color: #475569; text-align: right;'>⏱️ Est. reading time: <strong>~{read_secs}s</strong></div>", unsafe_allow_html=True)

        # Send Guard Missing Token Check
        missing_tokens = _missing_tokens(resolved_subject) + _missing_tokens(resolved_body)
        # Also check for unfilled curly variables like {first_name}
        curly_missing = re.findall(r'\{([a-zA-Z0-9_\s-]+)\}', resolved_body)
        for cm in curly_missing:
            if "|" not in cm:
                missing_tokens.append(f"{{{cm}}}")

        missing_tokens = list(set(missing_tokens))

        if missing_tokens:
            st.error(f"🛡️ Send Guard Alert: Unfilled placeholders detected: {', '.join(missing_tokens)}. Please fill the research fields or edit the text before sending.")

        # Action Buttons
        st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
        act_col1, act_col2, act_col3 = st.columns([1.2, 1.2, 1.0])

        with act_col1:
            send_disabled = len(missing_tokens) > 0
            if st.button("🚀 Send Now", type="primary", disabled=send_disabled, key=f"btn_send_now_{contact_id}_{active_step_num}", use_container_width=True):
                # 1. Fetch available SMTP account
                smtp_acc = get_next_available_smtp_account()
                if not smtp_acc:
                    st.error("No active SMTP account available or daily limit reached. Please check Settings.")
                else:
                    # Check threading header
                    thread_info = history_data.get("emails") or []
                    prev_msg_id = ""
                    for em in reversed(thread_info):
                        if (em.get("status") or "").lower() == "sent" and em.get("message_id"):
                            prev_msg_id = em["message_id"]
                            break

                    is_threaded = st.session_state.get(f"ws_thread_reply_{contact_id}_{active_step_num}", True)
                    in_reply_to_hdr = prev_msg_id if (is_threaded and prev_msg_id) else None

                    # Create and send email
                    msg_id_tracker = []
                    success, send_msg = send_smtp_email(
                        smtp_account=smtp_acc,
                        recipient=contact["email"],
                        subject=resolved_subject,
                        html_content=final_html,
                        in_reply_to=in_reply_to_hdr,
                        message_id_out=msg_id_tracker
                    )

                    if success:
                        sent_mid = msg_id_tracker[0] if msg_id_tracker else ""
                        eid = create_email(
                            email_html=final_html,
                            subject=resolved_subject,
                            recipient=contact["email"],
                            status="Sent",
                            sequence_step=active_step_num,
                            message_id=sent_mid,
                            in_reply_to=in_reply_to_hdr or ""
                        )
                        mark_email_sent(eid, message_id=sent_mid)
                        increment_smtp_sent(smtp_acc["id"])
                        update_contact(contact_id, status=f"Sent" if active_step_num == 1 else f"Follow-Up {active_step_num - 1}")
                        st.toast(f"Touch {active_step_num} dispatched to {contact['email']}!", icon="🚀")
                        st.rerun()
                    else:
                        st.error(f"Dispatch failed: {send_msg}")

        with act_col2:
            with st.popover("🕒 Schedule...", use_container_width=True):
                st.markdown("<strong style='font-size: 0.9rem;'>Schedule Send Time</strong>", unsafe_allow_html=True)
                default_send_dt = datetime.now() + timedelta(days=(3 if active_step_num > 1 else 0), hours=2)
                sched_date = st.date_input("Date", value=default_send_dt.date(), key=f"ws_sched_date_{contact_id}_{active_step_num}")
                sched_time = st.time_input("Time", value=default_send_dt.time(), key=f"ws_sched_time_{contact_id}_{active_step_num}")

                if st.button("Confirm Schedule", type="primary", key=f"ws_btn_confirm_sched_{contact_id}_{active_step_num}"):
                    combined_sched = datetime.combine(sched_date, sched_time).strftime("%Y-%m-%d %H:%M:%S")
                    eid = create_email(
                        email_html=final_html,
                        subject=resolved_subject,
                        recipient=contact["email"],
                        status="Approved",
                        scheduled_time=combined_sched,
                        sequence_step=active_step_num
                    )
                    st.toast(f"Scheduled for {combined_sched}!", icon="🕒")
                    st.rerun()

        with act_col3:
            if st.button("💾 Draft", key=f"btn_save_draft_{contact_id}_{active_step_num}", use_container_width=True):
                eid = create_email(
                    email_html=final_html,
                    subject=resolved_subject,
                    recipient=contact["email"],
                    status="Draft",
                    sequence_step=active_step_num
                )
                st.toast(f"Draft saved!", icon="💾")
                st.rerun()

    # --------------------------------------------------------------------------
    # RIGHT PANE: SEQUENCE BUILDER & TIMELINE
    # --------------------------------------------------------------------------
    with col_seq:
        st.markdown("<strong style='font-size: 0.95rem; color: #083731;'>⛓️ Sequence Timeline</strong>", unsafe_allow_html=True)
        st.caption("Unfolding 1:1 conversation narrative across touches.")

        touches_info = [
            (1, "Initial Pitch", "Day 0 (Immediate or Scheduled)"),
            (2, "Follow-up 1", "+3 days after Touch 1"),
            (3, "Follow-up 2", "+4 days after Touch 2"),
            (4, "Follow-up 3", "+5 days (Break-Up)")
        ]

        for step_idx, step_name, step_timing in touches_info:
            is_active = (step_idx == active_step_num)
            card_border = "#083731" if is_active else "#E2E8F0"
            card_bg = "#F0FDF4" if is_active else "#F8FAFC"

            with st.container(border=True):
                st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 700; font-size: 0.86rem; color: #0F172A;">Touch {step_idx}: {step_name}</span>
                        {'<span style="background:#083731; color:#fff; border-radius:4px; padding:1px 5px; font-size:0.68rem; font-weight:700;">ACTIVE</span>' if is_active else ''}
                    </div>
                    <div style="font-size: 0.72rem; color: #64748B; margin-top: 2px;">{step_timing}</div>
                """, unsafe_allow_html=True)

                if step_idx > 1:
                    # Delay and Threading settings
                    del_col1, del_col2 = st.columns(2)
                    with del_col1:
                        delay_days = st.number_input(f"Delay (days)", min_value=1, max_value=30, value=3 if step_idx == 2 else 4, key=f"ws_delay_{contact_id}_{step_idx}")
                    with del_col2:
                        thread_opt = st.toggle("Thread as Re:", value=True, key=f"ws_thread_reply_{contact_id}_{step_idx}")

                if not is_active:
                    if st.button(f"Load Touch {step_idx} in Editor", key=f"btn_load_step_{contact_id}_{step_idx}", use_container_width=True):
                        st.session_state[f"ws_active_touch_{contact_id}"] = list(touch_keys.keys())[step_idx - 1]
                        st.rerun()

        # Preview Whole Sequence Expander
        with st.expander("👁️ Preview Whole Sequence Narrative", expanded=False):
            st.markdown("<strong style='font-size: 0.88rem; color: #083731;'>Continuous Conversation Flow</strong>", unsafe_allow_html=True)
            for s_num in [1, 2, 3, 4]:
                s_subj = st.session_state.get(f"ws_subject_{contact_id}_{s_num}")
                s_body = st.session_state.get(f"ws_draft_{contact_id}_{s_num}")
                if not s_subj or not s_body:
                    def_fw = TARGETED_FRAMEWORKS.get(s_num, TARGETED_FRAMEWORKS[1])[0]
                    s_subj = s_subj or def_fw["subject"]
                    s_body = s_body or def_fw["body"]

                r_s = inject_variables(parse_spintax(s_subj), contact)
                r_b = resolve_template(s_body, contact)

                st.markdown(f"""
                    <div style="border: 1px solid #E2E8F0; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; background: #fff;">
                        <div style="font-size: 0.76rem; font-weight: 700; color: #083731;">Touch {s_num}: {html.escape(r_s)}</div>
                        <div style="font-size: 0.78rem; color: #334155; margin-top: 4px; line-height: 1.35;">{r_b}</div>
                    </div>
                """, unsafe_allow_html=True)

        # Persistent Auto-Stop Thread Protection Notice
        st.markdown("""
            <div style="background: #F0FDF4; border: 1px solid #86EFAC; border-radius: 8px; padding: 10px; margin-top: 14px;">
                <div style="font-weight: 700; font-size: 0.78rem; color: #166534;">🛡️ Auto-Cancel Thread Safety</div>
                <div style="font-size: 0.72rem; color: #15803D; margin-top: 2px; line-height: 1.35;">
                    If {name} replies at any time, all remaining scheduled follow-ups are automatically cancelled instantly.
                </div>
            </div>
        """.replace("{name}", contact["name"].split()[0] if contact.get("name") else "the prospect"), unsafe_allow_html=True)


def get_thread_history_by_id(contact_id: int) -> Dict[str, Any]:
    """Helper to fetch thread history given contact ID."""
    contact = get_contact_by_id(contact_id)
    if not contact:
        return {"contact": None, "emails": [], "notifications": [], "timeline": []}
    return get_thread_history(contact["email"])
