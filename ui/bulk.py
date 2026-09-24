"""
ui/bulk.py - Reference 3-Section Bulk Outreach Dispatcher for Sellomize Reach.
Matches sellomize_reference.html:
1. Message: Load a template OR write your own in dual-mode editor.
2. Recipients: Filter by tags, status/stage, hand-pick with checkboxes + manual address option.
3. Mailboxes & Pacing: Round-robin fleet, review card, overflow calculation, Schedule batch CTA.
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
    _missing_tokens,
)
from timezone_helper import get_next_valid_market_datetime, get_engine_now
from ui.editor import render_dual_mode_editor
from ui.components import trigger_toast


def render_bulk_tab():
    """Render the 3-section Bulk Send screen matching sellomize_reference.html."""
    all_templates = get_templates()
    all_leads = get_contacts()
    all_mailboxes = get_smtp_accounts(active_only=True)

    if not all_mailboxes:
        st.error("⚠️ No active Hostinger mailboxes configured. Connect a mailbox in **Settings**.")
        return

    # =========================================================================
    # STEP 1: MESSAGE — Template OR Write your own
    # =========================================================================
    st.markdown("<span class='lbl' style='font-size:14px; font-weight:700; color:#083731;'>1 · Message</span>", unsafe_allow_html=True)

    if "bulk_mode" not in st.session_state:
        st.session_state["bulk_mode"] = "template" if all_templates else "custom"

    # Message mode toggle pills
    mode_c1, mode_c2, _ = st.columns([1.2, 1.2, 4])
    with mode_c1:
        if st.button("📄 Load a template", key="bulk_mode_tpl", type="primary" if st.session_state["bulk_mode"] == "template" else "secondary", use_container_width=True):
            st.session_state["bulk_mode"] = "template"
            st.rerun()
    with mode_c2:
        if st.button("✍️ Write your own", key="bulk_mode_custom", type="primary" if st.session_state["bulk_mode"] == "custom" else "secondary", use_container_width=True):
            st.session_state["bulk_mode"] = "custom"
            st.rerun()

    # Pre-select template if navigated from Templates screen
    preselected_id = st.session_state.pop("bulk_selected_template_id", None)
    if preselected_id:
        st.session_state["bulk_mode"] = "template"
        for t in all_templates:
            if str(t["id"]) == str(preselected_id):
                st.session_state["bulk_tpl_id"] = t["id"]
                st.session_state["bulk_subject"] = t.get("subject") or ""
                st.session_state["bulk_body_html"] = t.get("body_content") or t.get("body_html") or ""
                break

    if st.session_state["bulk_mode"] == "template":
        if not all_templates:
            st.warning("No saved templates found. Create one in the **Templates** tab or switch to 'Write your own'.")
            selected_subject = ""
            current_body = ""
        else:
            tpl_choices = {f"{t.get('template_name') or t.get('name') or 'Template #' + str(t['id'])} — {t.get('subject') or 'No Subject'}": t for t in all_templates}
            
            # Match current choice index if set
            current_sel_idx = 0
            if "bulk_tpl_id" in st.session_state:
                for idx, t in enumerate(tpl_choices.values()):
                    if t["id"] == st.session_state["bulk_tpl_id"]:
                        current_sel_idx = idx
                        break

            col_tpl_sel, col_tpl_subj = st.columns([1.5, 2.5])
            with col_tpl_sel:
                st.markdown("<span class='lbl'>Template</span>", unsafe_allow_html=True)
                sel_tpl_label = st.selectbox("Select template", list(tpl_choices.keys()), index=current_sel_idx, key="bulk_tpl_dropdown", label_visibility="collapsed")
                chosen_tpl = tpl_choices[sel_tpl_label]

                if st.session_state.get("bulk_tpl_id") != chosen_tpl["id"]:
                    st.session_state["bulk_tpl_id"] = chosen_tpl["id"]
                    st.session_state["bulk_subject"] = chosen_tpl.get("subject") or ""
                    st.session_state["bulk_body_html"] = chosen_tpl.get("body_content") or chosen_tpl.get("body_html") or ""

            with col_tpl_subj:
                st.markdown("<span class='lbl'>Subject (supports [Name], [Company] & Spintax)</span>", unsafe_allow_html=True)
                selected_subject = st.text_input("Subject", value=st.session_state.get("bulk_subject", chosen_tpl.get("subject") or ""), key="bulk_subj_input", label_visibility="collapsed")
                st.session_state["bulk_subject"] = selected_subject

            st.markdown("<span class='lbl'>Body (editable for this batch)</span>", unsafe_allow_html=True)
            current_body = render_dual_mode_editor(
                key_prefix="bulk_tpl_editor",
                initial_content=st.session_state.get("bulk_body_html", chosen_tpl.get("body_content") or chosen_tpl.get("body_html") or ""),
                height=150
            )
            st.session_state["bulk_body_html"] = current_body
    else:
        # Write your own
        st.markdown("<span class='lbl'>Subject</span>", unsafe_allow_html=True)
        selected_subject = st.text_input("Subject", value=st.session_state.get("bulk_subject", "Quick question for [Company]"), key="bulk_custom_subj", label_visibility="collapsed")
        st.session_state["bulk_subject"] = selected_subject

        st.markdown("<span class='lbl'>Body</span>", unsafe_allow_html=True)
        current_body = render_dual_mode_editor(
            key_prefix="bulk_custom_editor",
            initial_content=st.session_state.get("bulk_body_html", "<p>Hi [Name],</p><p>I noticed [Company] and wanted to connect.</p>"),
            height=150
        )
        st.session_state["bulk_body_html"] = current_body

    st.markdown("<div style='font-size:12px; color:#64748B; margin:6px 0 20px;'>Edit the loaded template, or compose from scratch — same editor, styling and source paste as Compose. Spam-checked before send.</div>", unsafe_allow_html=True)

    # Deliverability check on current bulk copy
    neg_keywords = get_config("negative_keywords", "")
    audit = audit_email_deliverability(body_html=f"{selected_subject} {current_body}", custom_negative_keywords=neg_keywords)
    triggers = audit.get("detected_spam_words", [])

    st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:16px 0;'>", unsafe_allow_html=True)

    # =========================================================================
    # 2-COLUMN LAYOUT: STEP 2 (Recipients) & STEP 3 (Mailboxes & Pacing)
    # =========================================================================
    col_recipients, col_pacing = st.columns([1.1, 0.9], gap="large")

    # -------------------------------------------------------------------------
    # STEP 2: RECIPIENTS — Filter or hand-pick
    # -------------------------------------------------------------------------
    with col_recipients:
        st.markdown("<span class='lbl' style='font-size:14px; font-weight:700; color:#083731;'>2 · Recipients — filter or hand-pick</span>", unsafe_allow_html=True)

        # Collect unique tags
        all_tags = set()
        for c in all_leads:
            raw_tags = c.get("tags") or ""
            for tag in raw_tags.split(","):
                clean = tag.strip().lower()
                if clean:
                    all_tags.add(clean)
        tag_list = sorted(list(all_tags))

        # Filter by Tag Pills
        if tag_list:
            st.markdown("<div style='font-size:12px; color:#64748B; margin-bottom:4px;'>Filter by tag:</div>", unsafe_allow_html=True)
            if "bulk_tag_filter" not in st.session_state:
                st.session_state["bulk_tag_filter"] = "all"

            tag_cols = st.columns(min(len(tag_list) + 1, 5))
            with tag_cols[0]:
                is_all_tag = st.session_state["bulk_tag_filter"] == "all"
                if st.button("All tags", key="tag_pill_all", type="primary" if is_all_tag else "secondary"):
                    st.session_state["bulk_tag_filter"] = "all"
                    st.rerun()

            for idx, tag in enumerate(tag_list[:4]):
                with tag_cols[idx + 1]:
                    is_this_tag = st.session_state["bulk_tag_filter"] == tag
                    if st.button(tag, key=f"tag_pill_{tag}", type="primary" if is_this_tag else "secondary"):
                        st.session_state["bulk_tag_filter"] = tag
                        st.rerun()

        # Filter by Status / Stage Pills
        st.markdown("<div style='font-size:12px; color:#64748B; margin:8px 0 4px;'>Filter by status / stage:</div>", unsafe_allow_html=True)
        if "bulk_status_filter" not in st.session_state:
            st.session_state["bulk_status_filter"] = "New"

        st_cols = st.columns(len(LEAD_STATUSES) + 1)
        with st_cols[0]:
            is_all_st = st.session_state["bulk_status_filter"] == "all"
            if st.button("All", key="st_pill_all", type="primary" if is_all_st else "secondary"):
                st.session_state["bulk_status_filter"] = "all"
                st.rerun()

        for idx, st_name in enumerate(LEAD_STATUSES):
            with st_cols[idx + 1]:
                is_this_st = st.session_state["bulk_status_filter"] == st_name
                if st.button(st_name, key=f"st_pill_{st_name}", type="primary" if is_this_st else "secondary"):
                    st.session_state["bulk_status_filter"] = st_name
                    st.rerun()

        # Filter candidate leads
        filtered_candidates = []
        for c in all_leads:
            # Tag check
            lead_tags = (c.get("tags") or "").lower()
            if st.session_state.get("bulk_tag_filter", "all") != "all":
                if st.session_state["bulk_tag_filter"] not in lead_tags:
                    continue

            # Status check
            lead_status = c.get("status") or "New"
            if st.session_state.get("bulk_status_filter", "all") != "all":
                if lead_status != st.session_state["bulk_status_filter"]:
                    continue

            filtered_candidates.append(c)

        # Checkbox Table for Hand-Picking
        st.markdown("<div style='font-size:12px; color:#64748B; margin:10px 0 4px;'>Then tick or untick individuals:</div>", unsafe_allow_html=True)

        if "bulk_selected_lead_ids" not in st.session_state:
            st.session_state["bulk_selected_lead_ids"] = {c["id"] for c in filtered_candidates}

        # Version counter: increment this to force Streamlit to re-create checkbox
        # widgets from scratch (bypasses cached key state).
        if "bulk_chk_ver" not in st.session_state:
            st.session_state["bulk_chk_ver"] = 0

        # Quick select / deselect — bump version so keys change → widget reinits
        q_c1, q_c2 = st.columns([1, 1])
        with q_c1:
            if st.button("☑️ Select All Filtered", key="bulk_btn_sel_all", use_container_width=True):
                st.session_state["bulk_selected_lead_ids"] = {c["id"] for c in filtered_candidates}
                st.session_state["bulk_chk_ver"] += 1
                st.rerun()
        with q_c2:
            if st.button("◻️ Clear Selection", key="bulk_btn_clear_sel", use_container_width=True):
                st.session_state["bulk_selected_lead_ids"] = set()
                st.session_state["bulk_chk_ver"] += 1
                st.rerun()

        ver = st.session_state["bulk_chk_ver"]
        selected_leads = []
        if not filtered_candidates:
            st.caption("No leads match the active filters.")
        else:
            with st.container(height=240):
                for c in filtered_candidates:
                    cid = c["id"]
                    cname = c.get("name") or "Unknown"
                    ccomp = c.get("company") or "Unknown Company"
                    cemail = c.get("email") or ""
                    ctags = c.get("tags") or ""

                    is_checked = cid in st.session_state["bulk_selected_lead_ids"]
                    # Key includes version so it re-initializes when selection is
                    # changed programmatically via Select All / Clear Selection.
                    check_val = st.checkbox(
                        f"**{ccomp}** — {cname} (`{cemail}`) {f'· {ctags}' if ctags else ''}",
                        value=is_checked,
                        key=f"lead_chk_{cid}_v{ver}"
                    )
                    if check_val:
                        selected_leads.append(c)
                        st.session_state["bulk_selected_lead_ids"].add(cid)
                    else:
                        st.session_state["bulk_selected_lead_ids"].discard(cid)

        # Optional Manual Address Not in CRM
        st.markdown("<div style='font-size:12px; color:#64748B; margin-top:10px;'>Or manually add an address not in the CRM:</div>", unsafe_allow_html=True)
        manual_recipient = st.text_input(
            "Manual address",
            placeholder="e.g. partner@example.com or Jane Doe <jane@example.com>",
            key="bulk_manual_address",
            label_visibility="collapsed"
        )
        if manual_recipient.strip():
            manual_stub = {
                "id": -999,
                "name": manual_recipient.split("<")[0].strip() if "<" in manual_recipient else manual_recipient.split("@")[0].capitalize(),
                "email": manual_recipient.split("<")[-1].replace(">", "").strip() if "<" in manual_recipient else manual_recipient.strip(),
                "company": "Prospective Partner",
                "country_or_timezone": "LOCAL"
            }
            selected_leads.append(manual_stub)

    # -------------------------------------------------------------------------
    # STEP 3: MAILBOXES & PACING + REVIEW + SCHEDULE BATCH
    # -------------------------------------------------------------------------
    with col_pacing:
        st.markdown("<span class='lbl' style='font-size:14px; font-weight:700; color:#083731;'>3 · Mailboxes &amp; pacing</span>", unsafe_allow_html=True)

        num_mailboxes = len(all_mailboxes)
        st.markdown(
            f"""<div class="field" style="margin-bottom:8px; font-weight:600; color:#083731;">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/></svg>
                {num_mailboxes} Hostinger mailbox{'es' if num_mailboxes != 1 else ''} · round-robin
            </div>""",
            unsafe_allow_html=True
        )

        # Start Date and Start Time Pickers (UTC+5 Engine Timeframe)
        c_date, c_time = st.columns(2)
        with c_date:
            st.markdown("<span class='lbl'>Start date</span>", unsafe_allow_html=True)
            chosen_date = st.date_input("Start date", value=get_engine_now().date(), key="bulk_start_date", label_visibility="collapsed")
        with c_time:
            st.markdown("<span class='lbl'>Start time (UTC+5)</span>", unsafe_allow_html=True)
            chosen_time = st.time_input("Start time", value=get_engine_now().time(), key="bulk_start_time", label_visibility="collapsed")

        spread_hours = st.slider(
            "Spread outreach over (Hours)",
            min_value=1,
            max_value=12,
            value=min(6, max(2, len(selected_leads) // 5 + 1)),
            step=1,
            help="Distributes outreach pacing evenly across the selected duration from start time."
        )

        st.caption("🕒 24/7 Delivery: Window restrictions cancelled. Emails dispatch evenly from your chosen start date & time while respecting daily limits.")

        # Calculate fleet capacity
        total_fleet_cap = 0
        for mb in all_mailboxes:
            eff_limit = get_effective_daily_limit(mb)
            sent_today = mb.get("sent_today", 0)
            total_fleet_cap += max(0, eff_limit - sent_today)

        recipients_count = len(selected_leads)
        per_mailbox_est = f"~{max(1, recipients_count // max(1, num_mailboxes))} each" if recipients_count > 0 else "0"

        # Estimated start and end time based on user-picked date and time
        base_start = datetime.combine(chosen_date, chosen_time)
        est_end = base_start + timedelta(hours=spread_hours)
        time_range_str = f"{base_start.strftime('%a %b %d, %H:%M')} → {est_end.strftime('%H:%M')}"

        st.markdown("<span class='lbl' style='margin-top:12px;'>Review</span>", unsafe_allow_html=True)

        spam_pill = '<span class="pill p-pass">clean</span>' if not triggers else f'<span class="pill p-fail">{len(triggers)} trigger(s)</span>'

        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #F1F5F9;">
                <span style="color:var(--muted); font-size:13px;">Recipients</span>
                <b style="color:#083731;">{recipients_count}</b>
            </div>
            <div style="display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #F1F5F9;">
                <span style="color:var(--muted); font-size:13px;">Spam check</span>
                {spam_pill}
            </div>
            <div style="display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #F1F5F9;">
                <span style="color:var(--muted); font-size:13px;">Pacing</span>
                <b style="color:#083731; font-size:12px;">{time_range_str}</b>
            </div>
            <div style="display:flex; justify-content:space-between; padding:5px 0;">
                <span style="color:var(--muted); font-size:13px;">Per mailbox</span>
                <b style="color:#083731;">{per_mailbox_est}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Overflow note if leads exceed today's remaining cap
        overflow_count = max(0, recipients_count - total_fleet_cap)
        if overflow_count > 0:
            st.markdown(
                f"""<div class="banner banner-warn">
                    <b>{overflow_count} lead{'s' if overflow_count != 1 else ''}</b> exceed today's fleet limit ({total_fleet_cap} remaining) — they will dispatch tomorrow.
                </div>""",
                unsafe_allow_html=True
            )

        # Schedule batch primary button
        if st.button("🚀 Schedule batch", type="primary", use_container_width=True, key="bulk_schedule_cta"):
            if not selected_leads:
                st.error("Please select at least one recipient lead.")
                return

            if not selected_subject.strip():
                st.error("Please specify a subject line.")
                return

            if not current_body.strip():
                st.error("Please provide email body content.")
                return

            # Check unfilled tokens
            unfilled_sample = []
            for lead in selected_leads[:5]:
                missing = _missing_tokens(f"{selected_subject} {current_body}", lead)
                if missing:
                    unfilled_sample.extend(missing)

            if unfilled_sample:
                st.error(f"Unfilled variables detected: {', '.join(set(unfilled_sample))}. Please fill or use variable fallbacks.")
                return

            with st.spinner("Scheduling batch outreach..."):
                queued_count = 0
                base_start = datetime.combine(chosen_date, chosen_time)
                step_seconds = max(45, int((spread_hours * 3600) / max(1, len(selected_leads))))

                for i, lead in enumerate(selected_leads):
                    lead_tz = lead.get("country_or_timezone") or "LOCAL"
                    if i < total_fleet_cap:
                        target_dt = base_start + timedelta(seconds=(i * step_seconds))
                    else:
                        overflow_offset = i - total_fleet_cap
                        target_dt = (base_start + timedelta(days=1)) + timedelta(seconds=(overflow_offset * step_seconds))

                    sched_time_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")

                    lead_subj = inject_variables(parse_spintax(selected_subject), lead)
                    lead_body = resolve_template(current_body, lead)
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

            trigger_toast(f"Successfully scheduled batch of {queued_count} emails!", icon="🚀")
            st.session_state["active_screen"] = "outbox"
            st.session_state["main_app_tabs"] = "📥 Outbox"
            st.rerun()
