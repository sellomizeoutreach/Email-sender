"""
ui/bulk.py - Simple, Focused Bulk Send Flow for Sellomize Reach.
Coordinates multi-mailbox cold outreach with lead-local timezone scheduling,
mailbox warmup limits, and safety gates in a clean 3-step layout.
"""

import streamlit as st
import html
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from database import (
    get_templates,
    get_contacts,
    get_smtp_accounts,
    get_effective_daily_limit,
    get_config,
    create_email,
    LEAD_STATUSES,
)
from template_engine import (
    resolve_template,
    inject_variables,
    parse_spintax,
    audit_email_deliverability,
    format_email_html,
)
from timezone_helper import get_next_valid_market_datetime
from ui.components import render_tab_header, trigger_toast


def render_bulk_tab():
    """Render the simplified 3-step bulk outreach dispatcher."""
    render_tab_header(
        "🚀 Bulk Outreach Dispatcher",
        "Distribute targeted outreach across your Hostinger mailboxes with automatic warmup pacing and business hours enforcement."
    )

    all_templates = get_templates()
    all_leads = get_contacts()
    all_mailboxes = get_smtp_accounts(active_only=True)

    if not all_templates:
        st.warning("⚠️ No templates found. Create your outreach copy in the **Templates** tab first.")
        return

    if not all_mailboxes:
        st.error("⚠️ No active Hostinger mailboxes configured. Connect a mailbox in **Settings**.")
        return

    # ==========================================================================
    # 1. SELECT TEMPLATE & AUDIENCE
    # ==========================================================================
    col_tpl, col_aud = st.columns([1.5, 1.5])

    with col_tpl:
        st.markdown("### 1. Outreach Template")
        tpl_options = {
            f"{t.get('template_name') or t.get('name') or 'Template'} — ({t.get('subject') or 'No Subject'})": t
            for t in all_templates
        }
        selected_tpl_label = st.selectbox("Select Template", list(tpl_options.keys()), key="bulk_tpl_select")
        selected_tpl = tpl_options[selected_tpl_label]

        with st.expander("👁️ Preview Template Copy", expanded=False):
            st.markdown(f"**Subject:** {selected_tpl.get('subject') or 'N/A'}")
            st.markdown(
                f"""<div style="background:#FFFFFF; border:1px solid #CBD5E1; border-radius:6px; padding:10px; font-size:0.85rem;">
                    {selected_tpl.get('body_content') or selected_tpl.get('body_html') or ''}
                </div>""",
                unsafe_allow_html=True
            )

    with col_aud:
        st.markdown("### 2. Target Audience")
        st_filter = st.selectbox(
            "Filter Leads",
            ["All Active Leads (New + Emailed)", "New Only"] + [s for s in LEAD_STATUSES if s not in ["New", "Emailed"]],
            key="bulk_st_filter"
        )
        eligible = all_leads
        if st_filter == "All Active Leads (New + Emailed)":
            eligible = [c for c in eligible if (c.get("status") or "New") in ["New", "Emailed"]]
        elif st_filter == "New Only":
            eligible = [c for c in eligible if (c.get("status") or "New") == "New"]
        else:
            eligible = [c for c in eligible if (c.get("status") or "New") == st_filter]

        search_kw = st.text_input("Quick Filter", placeholder="Search name or company...", key="bulk_kw_filter")
        if search_kw.strip():
            q = search_kw.strip().lower()
            eligible = [c for c in eligible if q in (c.get("name") or "").lower() or q in (c.get("company") or "").lower()]

        select_all = st.checkbox(f"Select All {len(eligible)} Filtered Leads", value=True, key="bulk_select_all")
        selected_leads = eligible if select_all else []
        st.caption(f"**{len(selected_leads)} leads** queued for dispatch.")

    if not selected_leads:
        st.info("Select at least one lead above to proceed.")
        return

    st.markdown("---")

    # ==========================================================================
    # 2. FLEET CAPACITY & DELIVERABILITY CHECK
    # ==========================================================================
    st.markdown("### 3. Mailbox Fleet & Schedule")

    total_fleet_cap = 0
    total_sent_today = 0
    mb_cols = st.columns(min(4, len(all_mailboxes)))

    for idx, mb in enumerate(all_mailboxes):
        eff_limit = get_effective_daily_limit(mb)
        sent_today = mb.get("sent_today", 0)
        remaining = max(0, eff_limit - sent_today)
        total_fleet_cap += remaining
        total_sent_today += sent_today

        with mb_cols[idx % len(mb_cols)]:
            st.markdown(
                f"""<div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.18); border-radius:8px; padding:10px; margin-bottom:8px;">
                    <div style="font-weight:700; font-size:0.82rem; color:#083731;">{html.escape(mb.get('sender_name') or mb['email'])}</div>
                    <div style="font-size:0.75rem; color:#64748B;">{html.escape(mb['email'])}</div>
                    <div style="margin-top:4px; font-size:0.8rem; font-weight:800; color:#FD4D1B;">
                        {remaining} remaining today
                    </div>
                </div>""",
                unsafe_allow_html=True
            )

    st.markdown(f"**Total Fleet Capacity Remaining Today:** `{total_fleet_cap}` emails")
    if len(selected_leads) > total_fleet_cap:
        st.warning(
            f"⚠️ Audience ({len(selected_leads)}) exceeds today's remaining capacity ({total_fleet_cap}). "
            f"Excess emails will automatically queue for the next open sending window."
        )

    # Deliverability pre-check
    test_subject = selected_tpl.get("subject") or ""
    test_body = selected_tpl.get("body_content") or selected_tpl.get("body_html") or ""
    neg_keywords = get_config("negative_keywords", "")
    audit = audit_email_deliverability(body_html=test_body, subject=test_subject, custom_negative_keywords=neg_keywords)
    triggers = audit.get("detected_spam_words", [])

    if triggers:
        st.warning(f"⚠️ Template contains {len(triggers)} spam keyword(s): {', '.join(w.get('word', '') for w in triggers)}")
        allow_override = st.checkbox("Override spam shield warning", value=False)
        if not allow_override:
            st.info("Edit your template to remove spam keywords or check the override box.")
            return

    # Pacing and scheduling options
    col_pacing, col_launch = st.columns([2, 1], vertical_alignment="bottom")

    with col_pacing:
        spread_hours = st.slider("Spread outreach over (Hours)", min_value=1, max_value=8, value=4, step=1,
                                 help="Distributes emails evenly across the chosen duration.")
        window_start = get_config("sending_start_time", "09:00") or "09:00"
        window_end = get_config("sending_end_time", "18:00") or "18:00"
        st.caption(f"🕒 Sending Window Enforced: **{window_start} - {window_end} (Local PC Time)**")

    with col_launch:
        launch_clicked = st.button(
            f"🚀 Launch Campaign ({len(selected_leads)} Leads)",
            type="primary",
            use_container_width=True
        )

    if launch_clicked:
        with st.spinner("Queueing emails into Outbox..."):
            queued_count = 0
            base_now = datetime.now()
            step_seconds = max(45, int((spread_hours * 3600) / max(1, len(selected_leads))))

            for i, lead in enumerate(selected_leads):
                lead_tz = lead.get("country_or_timezone") or "LOCAL"
                staggered_target = base_now + timedelta(seconds=(i * step_seconds))
                local_dt = get_next_valid_market_datetime(
                    market_key_or_tz=lead_tz,
                    base_market_dt=staggered_target,
                    start_time=window_start,
                    end_time=window_end
                )
                sched_time_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")

                lead_subj = inject_variables(parse_spintax(test_subject), lead)
                lead_body = resolve_template(test_body, lead)
                formatted_body = format_email_html(lead_body)

                create_email(
                    email_html=formatted_body,
                    subject=lead_subj,
                    recipient=lead["email"].strip(),
                    status="Approved",
                    scheduled_time=sched_time_str,
                    target_timezone=lead_tz
                )
                queued_count += 1

        trigger_toast(f"Queued {queued_count} emails into Outbox!", icon="🚀")
        st.session_state["main_app_tabs"] = "📥 Outbox"
        st.rerun()
