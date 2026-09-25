"""
ui/bulk.py - Reference 3-Section Bulk Outreach Dispatcher for Sellomize Reach.

Matches sellomize_reference.html:
1. Message & Sequence (with live preview side-by-side matching Compose):
   - Initial Email and Follow-ups sit in the same row using st.tabs.
   - Each step is independently editable (subject, body, delay slider, variables).
   - Side-by-side Live Preview showing Subject preview, resolved variables, and signature.
2. Recipients:
   - Filter by tags, status/stage, hand-pick with robust version-keyed checkboxes.
   - Safe set operations completely immune to type errors.
3. Mailboxes & Pacing:
   - Round-robin fleet, review card with follow-up count, overflow calculation.
   - Schedule batch CTA queues both initial emails and all follow-up emails in one click.
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
from ui.editor import render_dual_mode_editor, html_to_visual_text
from ui.components import trigger_toast


# ---------------------------------------------------------------------------
# Dialog: Confirm Batch Schedule & Sequence Timing (UTC+5)
# ---------------------------------------------------------------------------

@st.dialog("🚀 Confirm Batch Outreach & Timing (UTC+5)")
def render_bulk_schedule_dialog(
    mode: str,
    selected_leads: List[Dict[str, Any]],
    selected_subject: str,
    current_body: str,
    all_mailboxes: List[Dict[str, Any]],
    default_date,
    default_time,
    spread_hours: int,
    total_fleet_cap: int,
    followup_steps: List[Dict[str, Any]],
):
    """Modal dialog allowing exact date and time customization for initial batch and each follow-up step separately."""
    recipients_count = len(selected_leads)
    num_mailboxes    = len(all_mailboxes)
    now_engine       = get_engine_now()

    st.markdown(
        f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px 12px; margin-bottom:12px; font-size:13px;'>"
        f"<b>Recipients:</b> {recipients_count} leads &nbsp;·&nbsp; "
        f"<b>Fleet:</b> {num_mailboxes} active Hostinger mailbox{'es' if num_mailboxes != 1 else ''} &nbsp;·&nbsp; "
        f"<b>Pacing:</b> {spread_hours}h spread"
        f"</div>",
        unsafe_allow_html=True
    )

    # ── Section 1: Initial Batch Timing ──
    st.markdown("##### 📧 Initial Outreach Batch")
    st.markdown(f"**Subject:** *{html.escape(selected_subject)}*")

    if mode == "send_now":
        init_choice = st.radio(
            "Initial Batch Dispatch",
            ["🚀 Start dispatch immediately right now", "🕒 Set custom start date & time (UTC+5)"],
            horizontal=True,
            key="bulk_dlg_init_choice"
        )
        if init_choice.startswith("🕒"):
            c1, c2 = st.columns(2)
            with c1:
                batch_date = st.date_input("Start Date (UTC+5)", value=default_date, min_value=now_engine.date(), key="bulk_dlg_init_d")
            with c2:
                batch_time = st.time_input("Start Time (UTC+5)", value=default_time, key="bulk_dlg_init_t")
            init_start_dt = datetime.combine(batch_date, batch_time)
        else:
            init_start_dt = now_engine
    else:
        st.markdown("<span class='lbl'>Scheduled Start Date &amp; Time (UTC+5)</span>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            batch_date = st.date_input("Start Date (UTC+5)", value=default_date, min_value=now_engine.date(), key="bulk_dlg_init_d", label_visibility="collapsed")
        with c2:
            batch_time = st.time_input("Start Time (UTC+5)", value=default_time, key="bulk_dlg_init_t", label_visibility="collapsed")
        init_start_dt = datetime.combine(batch_date, batch_time)

    st.caption(f"📅 Initial emails will pace evenly from **{init_start_dt.strftime('%a %b %d, %H:%M')}** across **{spread_hours} hours**.")

    # ── Section 2: Follow-up Sequences (Set each date and time separately) ──
    fu_start_dts = []
    if followup_steps:
        st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:14px 0 10px;'>", unsafe_allow_html=True)
        st.markdown(f"##### ↩️ Follow-Up Sequence Schedule ({len(followup_steps)} step{'s' if len(followup_steps) != 1 else ''})")
        st.caption("Set the exact start date and time for each follow-up step separately:")

        for idx, step in enumerate(followup_steps):
            with st.container(border=True):
                st.markdown(f"**Follow-Up #{idx + 1}:** *{html.escape(step['subject'])}*")
                default_fu_dt = init_start_dt + timedelta(days=step.get("delay_days", (idx + 1) * 3))
                fc1, fc2 = st.columns(2)
                with fc1:
                    fu_d = st.date_input(
                        f"Start Date for Follow-up #{idx + 1}",
                        value=default_fu_dt.date(),
                        min_value=now_engine.date(),
                        key=f"bulk_dlg_fu_d_{idx}"
                    )
                with fc2:
                    fu_t = st.time_input(
                        f"Start Time (UTC+5) for Follow-up #{idx + 1}",
                        value=default_fu_dt.time(),
                        key=f"bulk_dlg_fu_t_{idx}"
                    )
                step_start_dt = datetime.combine(fu_d, fu_t)
                fu_start_dts.append(step_start_dt)
                st.caption(f"📅 Follow-up #{idx + 1} batch will pace evenly from **{step_start_dt.strftime('%a %b %d, %Y at %H:%M')} (UTC+5)**.")

    # ── Confirmation Action ──
    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)
    if st.button("🚀 Confirm & Launch Campaign Batch", type="primary", use_container_width=True, key="bulk_dlg_confirm_cta"):
        with st.spinner("Scheduling batch outreach and sequences..."):
            queued_count = 0
            fu_queued    = 0
            step_seconds = max(45, int((spread_hours * 3600) / max(1, len(selected_leads))))

            # 1. Initial emails
            for i, lead in enumerate(selected_leads):
                lead_tz = lead.get("country_or_timezone") or "LOCAL"
                if i < total_fleet_cap:
                    target_dt = init_start_dt + timedelta(seconds=(i * step_seconds))
                else:
                    overflow_offset = i - total_fleet_cap
                    target_dt = (init_start_dt + timedelta(days=1)) + timedelta(seconds=(overflow_offset * step_seconds))

                lead_subj      = inject_variables(parse_spintax(selected_subject), lead)
                lead_body      = resolve_template(current_body, lead)
                formatted_body = format_email_html(lead_body)

                create_email(
                    email_html=formatted_body,
                    subject=lead_subj,
                    recipient=lead["email"].strip(),
                    status="Approved",
                    scheduled_time=target_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    target_timezone=lead_tz
                )
                queued_count += 1

            # 2. Follow-up emails for each step with individually chosen start date and time
            for idx, step in enumerate(followup_steps):
                step_base_dt = fu_start_dts[idx]
                fu_body_raw  = step["body"]
                fu_body_html = "<p>" + fu_body_raw.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"

                for i, lead in enumerate(selected_leads):
                    lead_tz   = lead.get("country_or_timezone") or "LOCAL"
                    fu_target = step_base_dt + timedelta(seconds=(i * step_seconds))

                    fu_subj_resolved = inject_variables(parse_spintax(step["subject"]), lead)
                    fu_body_resolved = resolve_template(fu_body_html, lead)

                    create_email(
                        email_html=format_email_html(fu_body_resolved),
                        subject=fu_subj_resolved,
                        recipient=lead["email"].strip(),
                        status="Approved",
                        scheduled_time=fu_target.strftime("%Y-%m-%d %H:%M:%S"),
                        target_timezone=lead_tz
                    )
                    fu_queued += 1

        if fu_queued:
            msg = f"Scheduled {queued_count} initial + {fu_queued} follow-up email(s)!"
        else:
            msg = f"Successfully scheduled batch of {queued_count} emails!"

        st.session_state["bulk_followup_steps"] = []
        trigger_toast(msg, icon="🚀")
        st.session_state["active_screen"]  = "outbox"
        st.session_state["main_app_tabs"]  = "📥 Outbox"
        st.rerun()


def render_bulk_tab():
    """Render the 3-section Bulk Send screen with follow-up sequences and live preview."""
    all_templates = get_templates()
    all_leads     = get_contacts()
    all_mailboxes = get_smtp_accounts(active_only=True)

    if not all_mailboxes:
        st.error("⚠️ No active Hostinger mailboxes configured. Connect a mailbox in **Settings**.")
        return

    # ── Follow-up steps state ─────────────────────────────────────────────
    if "bulk_followup_steps" not in st.session_state:
        st.session_state["bulk_followup_steps"] = []   # list of {delay_days, subject, body}

    # =========================================================================
    # STEP 1 · MESSAGE & SEQUENCE + LIVE PREVIEW (Side-by-side like Compose)
    # =========================================================================
    col_msg, col_prev = st.columns([1.1, 0.9], gap="large")

    with col_msg:
        st.markdown("<span class='lbl' style='font-size:14px; font-weight:700; color:#083731;'>1 · Message &amp; Sequence</span>", unsafe_allow_html=True)

        if "bulk_mode" not in st.session_state:
            st.session_state["bulk_mode"] = "template" if all_templates else "custom"

        fu_count = len(st.session_state["bulk_followup_steps"])
        can_add  = fu_count < 3

        if fu_count > 0:
            c1, c2, c3, c4 = st.columns([1.5, 1.5, 2.0, 1.0], vertical_alignment="center")
            with c1:
                if st.button(
                    "📄 Load template", key="bulk_mode_tpl",
                    type="primary" if st.session_state["bulk_mode"] == "template" else "secondary",
                    use_container_width=True
                ):
                    st.session_state["bulk_mode"] = "template"
                    st.rerun()
            with c2:
                if st.button(
                    "✍️ Write custom", key="bulk_mode_custom",
                    type="primary" if st.session_state["bulk_mode"] == "custom" else "secondary",
                    use_container_width=True
                ):
                    st.session_state["bulk_mode"] = "custom"
                    st.rerun()
            with c3:
                if st.button(
                    f"➕ Add follow-up ({fu_count}/3)",
                    key="bulk_add_fu",
                    use_container_width=True,
                    disabled=not can_add,
                    help="Adds a follow-up tab in this row — each editable separately"
                ):
                    delay = (fu_count + 1) * 3
                    init_subj = st.session_state.get("bulk_subject", "[Company] + Amazon")
                    re_prefix = "Re: " if not init_subj.startswith("Re:") else ""
                    st.session_state["bulk_followup_steps"].append({
                        "delay_days": delay,
                        "subject":    f"{re_prefix}{init_subj}",
                        "body":       (
                            "Hi [Name],\n\n"
                            "Just following up on my previous note — wanted to make sure it didn't get buried.\n\n"
                            "Would love to connect if the timing works.\n\n"
                            "Best regards,"
                        ),
                    })
                    st.rerun()
            with c4:
                if st.button("↩️ Undo", key="bulk_undo_fu", use_container_width=True,
                             help="Undo / remove last added follow-up"):
                    st.session_state["bulk_followup_steps"].pop()
                    st.rerun()
        else:
            c1, c2, c3 = st.columns([1.5, 1.5, 2.2], vertical_alignment="center")
            with c1:
                if st.button(
                    "📄 Load template", key="bulk_mode_tpl",
                    type="primary" if st.session_state["bulk_mode"] == "template" else "secondary",
                    use_container_width=True
                ):
                    st.session_state["bulk_mode"] = "template"
                    st.rerun()
            with c2:
                if st.button(
                    "✍️ Write custom", key="bulk_mode_custom",
                    type="primary" if st.session_state["bulk_mode"] == "custom" else "secondary",
                    use_container_width=True
                ):
                    st.session_state["bulk_mode"] = "custom"
                    st.rerun()
            with c3:
                if st.button(
                    "➕ Add follow-up",
                    key="bulk_add_fu",
                    use_container_width=True,
                    help="Adds a follow-up tab in this row — each editable separately"
                ):
                    delay = 3
                    init_subj = st.session_state.get("bulk_subject", "[Company] + Amazon")
                    re_prefix = "Re: " if not init_subj.startswith("Re:") else ""
                    st.session_state["bulk_followup_steps"].append({
                        "delay_days": delay,
                        "subject":    f"{re_prefix}{init_subj}",
                        "body":       (
                            "Hi [Name],\n\n"
                            "Just following up on my previous note — wanted to make sure it didn't get buried.\n\n"
                            "Would love to connect if the timing works.\n\n"
                            "Best regards,"
                        ),
                    })
                    st.rerun()

        # Build tabs in the same horizontal row
        tab_labels = ["📧 Initial Email"] + [
            f"↩️ Follow-up #{i + 1} (+{s['delay_days']}d)"
            for i, s in enumerate(st.session_state["bulk_followup_steps"])
        ]
        tabs = st.tabs(tab_labels)

        # --- Tab 0: Initial Email ---
        with tabs[0]:
            preselected_id = st.session_state.pop("bulk_selected_template_id", None)
            if preselected_id:
                st.session_state["bulk_mode"] = "template"
                for t in all_templates:
                    if str(t["id"]) == str(preselected_id):
                        st.session_state["bulk_tpl_id"]   = t["id"]
                        t_subj = t.get("subject") or ""
                        t_body = t.get("body_content") or t.get("body_html") or ""
                        st.session_state["bulk_subject"]   = t_subj
                        st.session_state["bulk_subj_input"] = t_subj
                        st.session_state["bulk_body_html"] = t_body
                        st.session_state["bulk_tpl_editor_body_html"] = t_body
                        clean_v, _ = html_to_visual_text(t_body)
                        st.session_state["bulk_tpl_editor_visual_textarea"] = clean_v
                        st.session_state["bulk_tpl_editor_last_synced_html"] = t_body
                        break

            if st.session_state["bulk_mode"] == "template":
                if not all_templates:
                    st.warning("No saved templates found. Create one in **Templates** or switch to 'Write custom'.")
                    selected_subject = ""
                    current_body     = ""
                else:
                    tpl_choices = {
                        f"{t.get('template_name') or t.get('name') or 'Template #' + str(t['id'])} — {t.get('subject') or 'No Subject'}": t
                        for t in all_templates
                    }
                    current_sel_idx = 0
                    if "bulk_tpl_id" in st.session_state:
                        for idx, t in enumerate(tpl_choices.values()):
                            if t["id"] == st.session_state["bulk_tpl_id"]:
                                current_sel_idx = idx
                                break
                    else:
                        first_tpl = list(tpl_choices.values())[0]
                        st.session_state["bulk_tpl_id"] = first_tpl["id"]
                        first_subj = first_tpl.get("subject") or ""
                        first_body = first_tpl.get("body_content") or first_tpl.get("body_html") or ""
                        st.session_state["bulk_subject"] = first_subj
                        st.session_state["bulk_subj_input"] = first_subj
                        st.session_state["bulk_body_html"] = first_body
                        st.session_state["bulk_tpl_editor_body_html"] = first_body
                        clean_v, _ = html_to_visual_text(first_body)
                        st.session_state["bulk_tpl_editor_visual_textarea"] = clean_v
                        st.session_state["bulk_tpl_editor_last_synced_html"] = first_body

                    col_tpl_sel, col_tpl_subj = st.columns([1.4, 2.6])
                    with col_tpl_sel:
                        st.markdown("<span class='lbl'>Template</span>", unsafe_allow_html=True)
                        sel_tpl_label = st.selectbox(
                            "Select template", list(tpl_choices.keys()),
                            index=current_sel_idx, key="bulk_tpl_dropdown",
                            label_visibility="collapsed"
                        )
                        chosen_tpl = tpl_choices[sel_tpl_label]
                        if st.session_state.get("bulk_tpl_id") != chosen_tpl["id"]:
                            st.session_state["bulk_tpl_id"]   = chosen_tpl["id"]
                            new_subj = chosen_tpl.get("subject") or ""
                            new_body = chosen_tpl.get("body_content") or chosen_tpl.get("body_html") or ""
                            st.session_state["bulk_subject"]   = new_subj
                            st.session_state["bulk_subj_input"] = new_subj
                            st.session_state["bulk_body_html"] = new_body
                            st.session_state["bulk_tpl_editor_body_html"] = new_body
                            clean_v, _ = html_to_visual_text(new_body)
                            st.session_state["bulk_tpl_editor_visual_textarea"] = clean_v
                            st.session_state["bulk_tpl_editor_last_synced_html"] = new_body
                            st.rerun()

                    with col_tpl_subj:
                        st.markdown("<span class='lbl'>Subject (supports [Name], [Company])</span>", unsafe_allow_html=True)
                        selected_subject = st.text_input(
                            "Subject",
                            value=st.session_state.get("bulk_subject", chosen_tpl.get("subject") or ""),
                            key="bulk_subj_input", label_visibility="collapsed"
                        )
                        st.session_state["bulk_subject"] = selected_subject

                    st.markdown("<span class='lbl'>Body</span>", unsafe_allow_html=True)
                    current_body = render_dual_mode_editor(
                        key_prefix="bulk_tpl_editor",
                        initial_content=st.session_state.get(
                            "bulk_body_html",
                            chosen_tpl.get("body_content") or chosen_tpl.get("body_html") or ""
                        ),
                        height=160
                    )
                    st.session_state["bulk_body_html"] = current_body
            else:
                # Custom compose
                st.markdown("<span class='lbl'>Subject</span>", unsafe_allow_html=True)
                selected_subject = st.text_input(
                    "Subject",
                    value=st.session_state.get("bulk_subject", "Quick question for [Company]"),
                    key="bulk_custom_subj", label_visibility="collapsed"
                )
                st.session_state["bulk_subject"] = selected_subject

                st.markdown("<span class='lbl'>Body</span>", unsafe_allow_html=True)
                current_body = render_dual_mode_editor(
                    key_prefix="bulk_custom_editor",
                    initial_content=st.session_state.get(
                        "bulk_body_html",
                        "Hi [Name],\n\nI noticed [Company] and wanted to connect."
                    ),
                    height=160
                )
                st.session_state["bulk_body_html"] = current_body

        # --- Tab 1+: Follow-up steps ---
        for i, step in enumerate(st.session_state["bulk_followup_steps"]):
            with tabs[i + 1]:
                fu_top_c1, fu_top_c2 = st.columns([3.8, 1.2], vertical_alignment="center")
                with fu_top_c1:
                    step["delay_days"] = st.slider(
                        f"Send follow-up #{i + 1} how many days after initial?",
                        min_value=1, max_value=30,
                        value=step["delay_days"],
                        key=f"bulk_fu_{i}_delay",
                        help="Days after the initial batch start time"
                    )
                with fu_top_c2:
                    if st.button("🗑️ Remove follow-up", key=f"bulk_fu_{i}_rm", use_container_width=True):
                        st.session_state["bulk_followup_steps"].pop(i)
                        st.rerun()

                st.markdown("<span class='lbl'>Follow-up Subject</span>", unsafe_allow_html=True)
                step["subject"] = st.text_input(
                    "Follow-up subject",
                    value=step["subject"],
                    key=f"bulk_fu_{i}_subj",
                    label_visibility="collapsed"
                )

                st.markdown("<span class='lbl'>Follow-up Body</span>", unsafe_allow_html=True)
                step["body"] = st.text_area(
                    "Follow-up body",
                    value=step["body"],
                    height=130,
                    key=f"bulk_fu_{i}_body",
                    label_visibility="collapsed"
                )

                chip_c = st.columns([1, 1.2, 1.3, 3])
                with chip_c[0]:
                    if st.button("👤 [Name]", key=f"bulk_fu_{i}_chip_name", use_container_width=True):
                        step["body"] = step["body"] + " [Name]"
                        st.session_state[f"bulk_fu_{i}_body"] = step["body"]
                        st.rerun()
                with chip_c[1]:
                    if st.button("🏢 [Company]", key=f"bulk_fu_{i}_chip_comp", use_container_width=True):
                        step["body"] = step["body"] + " [Company]"
                        st.session_state[f"bulk_fu_{i}_body"] = step["body"]
                        st.rerun()
                with chip_c[2]:
                    if st.button("🖋️ Signature", key=f"bulk_fu_{i}_chip_sig", use_container_width=True):
                        sig = get_config("signature_html", "") or "Best regards,\nOutreach Team"
                        step["body"] = step["body"] + f"\n\n{sig}"
                        st.session_state[f"bulk_fu_{i}_body"] = step["body"]
                        st.rerun()

                st.caption(
                    f"📅 Follow-up #{i + 1} will dispatch **{step['delay_days']} day(s)** after the initial batch."
                )

    # ── Right Column: Live Preview (Exact match to Compose) ───────────────
    with col_prev:
        # Determine sample lead for preview resolution
        sample_lead = all_leads[0] if all_leads else {
            "name": "Danessa Myricks",
            "email": "danessa@dmbeauty.com",
            "company": "DM Beauty",
            "country_or_timezone": "LOCAL"
        }
        lead_display_name = sample_lead.get("name") or "the recipient"
        st.markdown(
            f'<span class="lbl">Live preview — what {html.escape(lead_display_name)} receives</span>',
            unsafe_allow_html=True
        )

        signature_html = get_config("signature_html", "") or "Jack Conner · Sellomize · jack@sellomize.com"

        # Toggle preview between initial email and any follow-up steps
        if st.session_state["bulk_followup_steps"]:
            prev_sel = st.radio(
                "Preview Step",
                ["📧 Initial Email"] + [f"↩️ Follow-up #{i+1}" for i in range(len(st.session_state["bulk_followup_steps"]))],
                horizontal=True,
                label_visibility="collapsed",
                key="bulk_prev_radio"
            )
            if prev_sel.startswith("📧"):
                preview_subj = inject_variables(parse_spintax(selected_subject), sample_lead)
                preview_body = resolve_template(current_body, sample_lead)
            else:
                fu_idx = int(prev_sel.split("#")[-1]) - 1
                fu_step = st.session_state["bulk_followup_steps"][fu_idx]
                preview_subj = inject_variables(parse_spintax(fu_step["subject"]), sample_lead)
                fu_html = "<p>" + fu_step["body"].replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
                preview_body = resolve_template(fu_html, sample_lead)
        else:
            preview_subj = inject_variables(parse_spintax(selected_subject), sample_lead)
            preview_body = resolve_template(current_body, sample_lead)

        preview_box_html = (
            '<div class="preview">'
            f'<div style="font-weight:700; color:#083731; margin-bottom:8px; font-size:13px; border-bottom:1px solid #E2E8F0; padding-bottom:6px;">'
            f'Subject: {html.escape(preview_subj)}'
            f'</div>'
            f'{preview_body}'
            f'<div class="sig">{signature_html}</div>'
            '</div>'
            f'<div class="banner banner-info" style="margin-top:12px;">'
            f'Variables resolved for <b>{html.escape(lead_display_name)}</b>. This exact HTML is what gets sent.'
            f'</div>'
        )
        if hasattr(st, "html"):
            st.html(preview_box_html)
        else:
            st.markdown(preview_box_html, unsafe_allow_html=True)

    # Deliverability check on initial email copy
    neg_keywords = get_config("negative_keywords", "")
    audit = audit_email_deliverability(
        body_html=f"{selected_subject} {current_body}",
        custom_negative_keywords=neg_keywords
    )
    triggers = audit.get("detected_spam_words", [])

    st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:18px 0;'>", unsafe_allow_html=True)

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
            for tag in (c.get("tags") or "").split(","):
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
                if st.button("All tags", key="tag_pill_all",
                             type="primary" if st.session_state["bulk_tag_filter"] == "all" else "secondary"):
                    st.session_state["bulk_tag_filter"] = "all"
                    st.rerun()
            for idx, tag in enumerate(tag_list[:4]):
                with tag_cols[idx + 1]:
                    if st.button(tag, key=f"tag_pill_{tag}",
                                 type="primary" if st.session_state["bulk_tag_filter"] == tag else "secondary"):
                        st.session_state["bulk_tag_filter"] = tag
                        st.rerun()

        # Filter by Status Pills
        st.markdown("<div style='font-size:12px; color:#64748B; margin:8px 0 4px;'>Filter by status / stage:</div>", unsafe_allow_html=True)
        if "bulk_status_filter" not in st.session_state:
            st.session_state["bulk_status_filter"] = "New"

        st_cols = st.columns(len(LEAD_STATUSES) + 1)
        with st_cols[0]:
            if st.button("All", key="st_pill_all",
                         type="primary" if st.session_state["bulk_status_filter"] == "all" else "secondary"):
                st.session_state["bulk_status_filter"] = "all"
                st.rerun()
        for idx, st_name in enumerate(LEAD_STATUSES):
            with st_cols[idx + 1]:
                if st.button(st_name, key=f"st_pill_{st_name}",
                             type="primary" if st.session_state["bulk_status_filter"] == st_name else "secondary"):
                    st.session_state["bulk_status_filter"] = st_name
                    st.rerun()

        # Filter candidates
        filtered_candidates = []
        for c in all_leads:
            lead_tags   = (c.get("tags") or "").lower()
            lead_status = c.get("status") or "New"
            if st.session_state.get("bulk_tag_filter", "all") != "all":
                if st.session_state["bulk_tag_filter"] not in lead_tags:
                    continue
            if st.session_state.get("bulk_status_filter", "all") != "all":
                if lead_status != st.session_state["bulk_status_filter"]:
                    continue
            filtered_candidates.append(c)

        # Robust session state initialization for selected lead IDs
        valid_candidate_ids = {c["id"] for c in filtered_candidates if c.get("id") is not None}
        if (
            "bulk_selected_lead_ids" not in st.session_state
            or not isinstance(st.session_state["bulk_selected_lead_ids"], set)
        ):
            st.session_state["bulk_selected_lead_ids"] = set(valid_candidate_ids)

        if "bulk_chk_ver" not in st.session_state:
            st.session_state["bulk_chk_ver"] = 0

        # Checkbox selection bar
        st.markdown("<div style='font-size:12px; color:#64748B; margin:10px 0 4px;'>Then tick or untick individuals:</div>", unsafe_allow_html=True)
        q_c1, q_c2 = st.columns([1, 1])
        with q_c1:
            if st.button("☑️ Select All Filtered", key="bulk_btn_sel_all", use_container_width=True):
                st.session_state["bulk_selected_lead_ids"] = set(valid_candidate_ids)
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
                    cid   = c.get("id")
                    cname = c.get("name") or "Unknown"
                    ccomp = c.get("company") or "Unknown Company"
                    cemail = c.get("email") or ""
                    ctags  = c.get("tags") or ""

                    # Bulletproof membership check — never raises TypeError
                    is_checked = False
                    try:
                        sel_set = st.session_state.get("bulk_selected_lead_ids")
                        if isinstance(sel_set, set) and cid is not None:
                            is_checked = cid in sel_set
                    except Exception:
                        is_checked = True

                    check_val = st.checkbox(
                        f"**{ccomp}** — {cname} (`{cemail}`) {f'· {ctags}' if ctags else ''}",
                        value=is_checked,
                        key=f"lead_chk_{cid}_v{ver}"
                    )
                    if check_val:
                        selected_leads.append(c)
                        if isinstance(st.session_state.get("bulk_selected_lead_ids"), set) and cid is not None:
                            st.session_state["bulk_selected_lead_ids"].add(cid)
                    else:
                        if isinstance(st.session_state.get("bulk_selected_lead_ids"), set) and cid is not None:
                            st.session_state["bulk_selected_lead_ids"].discard(cid)

        # Manual address
        st.markdown("<div style='font-size:12px; color:#64748B; margin-top:10px;'>Or manually add an address not in the CRM:</div>", unsafe_allow_html=True)
        manual_recipient = st.text_input(
            "Manual address",
            placeholder="e.g. partner@example.com or Jane Doe <jane@example.com>",
            key="bulk_manual_address",
            label_visibility="collapsed"
        )
        if manual_recipient.strip():
            manual_stub = {
                "id":      -999,
                "name":    manual_recipient.split("<")[0].strip() if "<" in manual_recipient else manual_recipient.split("@")[0].capitalize(),
                "email":   manual_recipient.split("<")[-1].replace(">", "").strip() if "<" in manual_recipient else manual_recipient.strip(),
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

        c_date, c_time = st.columns(2)
        with c_date:
            st.markdown("<span class='lbl'>Start date</span>", unsafe_allow_html=True)
            chosen_date = st.date_input("Start date", value=get_engine_now().date(), key="bulk_start_date", label_visibility="collapsed")
        with c_time:
            st.markdown("<span class='lbl'>Start time (UTC+5)</span>", unsafe_allow_html=True)
            chosen_time = st.time_input("Start time", value=get_engine_now().time(), key="bulk_start_time", label_visibility="collapsed")

        spread_hours = st.slider(
            "Spread outreach over (Hours)",
            min_value=1, max_value=12,
            value=min(6, max(2, len(selected_leads) // 5 + 1)),
            step=1,
            help="Distributes pacing evenly across the selected duration."
        )

        st.caption("🕒 24/7 Delivery: Window restrictions cancelled. Emails dispatch evenly from your chosen start date & time while respecting daily limits.")

        # Fleet capacity
        total_fleet_cap = sum(
            max(0, get_effective_daily_limit(mb) - mb.get("sent_today", 0))
            for mb in all_mailboxes
        )

        recipients_count  = len(selected_leads)
        fu_step_count     = len(st.session_state["bulk_followup_steps"])
        total_emails_est  = recipients_count * (1 + fu_step_count)
        per_mailbox_est   = f"~{max(1, recipients_count // max(1, num_mailboxes))} each" if recipients_count > 0 else "0"

        base_start      = datetime.combine(chosen_date, chosen_time)
        est_end         = base_start + timedelta(hours=spread_hours)
        time_range_str  = f"{base_start.strftime('%a %b %d, %H:%M')} → {est_end.strftime('%H:%M')}"

        st.markdown("<span class='lbl' style='margin-top:12px;'>Review</span>", unsafe_allow_html=True)

        spam_pill = '<span class="pill p-pass">clean</span>' if not triggers else f'<span class="pill p-fail">{len(triggers)} trigger(s)</span>'
        fu_badge  = (
            f'<span style="background:#E1F5EE; color:#0F6E56; font-size:11px; padding:1px 8px; border-radius:999px; font-weight:600;">'
            f'+{fu_step_count} follow-up{"s" if fu_step_count != 1 else ""}</span>'
            if fu_step_count > 0 else "—"
        )

        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #F1F5F9;">
                <span style="color:var(--muted); font-size:13px;">Recipients</span>
                <b style="color:#083731;">{recipients_count}</b>
            </div>
            <div style="display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #F1F5F9;">
                <span style="color:var(--muted); font-size:13px;">Follow-ups</span>
                {fu_badge}
            </div>
            <div style="display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #F1F5F9;">
                <span style="color:var(--muted); font-size:13px;">Total emails</span>
                <b style="color:#083731;">{total_emails_est}</b>
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

        overflow_count = max(0, recipients_count - total_fleet_cap)
        if overflow_count > 0:
            st.markdown(
                f"""<div class="banner banner-warn">
                    <b>{overflow_count} lead{'s' if overflow_count != 1 else ''}</b> exceed today's fleet limit
                    ({total_fleet_cap} remaining) — they will dispatch tomorrow.
                </div>""",
                unsafe_allow_html=True
            )

        # ── Send Now & Schedule Batch Action Buttons ──────────────────────
        col_act1, col_act2 = st.columns(2)
        with col_act1:
            send_now_label = "🚀 Send batch now"
            if st.session_state["bulk_followup_steps"]:
                send_now_label += f" (+{len(st.session_state['bulk_followup_steps'])} FU)"
            if st.button(send_now_label, type="primary", use_container_width=True, key="bulk_send_now_cta"):
                if not selected_leads:
                    st.error("Please select at least one recipient lead.")
                    return
                if not selected_subject.strip():
                    st.error("Please specify a subject line.")
                    return
                if not current_body.strip():
                    st.error("Please provide email body content.")
                    return

                # Token check on initial email
                unfilled_sample = []
                for lead in selected_leads[:5]:
                    resolved_text = (
                        f"{inject_variables(parse_spintax(selected_subject), lead)} "
                        f"{resolve_template(current_body, lead)}"
                    )
                    unfilled_sample.extend(_missing_tokens(resolved_text))
                if unfilled_sample:
                    st.error(f"Unfilled variables: {', '.join(set(unfilled_sample))}. Fill or use fallbacks.")
                    return

                render_bulk_schedule_dialog(
                    mode="send_now",
                    selected_leads=selected_leads,
                    selected_subject=selected_subject,
                    current_body=current_body,
                    all_mailboxes=all_mailboxes,
                    default_date=chosen_date,
                    default_time=chosen_time,
                    spread_hours=spread_hours,
                    total_fleet_cap=total_fleet_cap,
                    followup_steps=st.session_state.get("bulk_followup_steps", [])
                )

        with col_act2:
            sched_label = "🕒 Schedule batch"
            if st.session_state["bulk_followup_steps"]:
                sched_label += f" (+{len(st.session_state['bulk_followup_steps'])} FU)"
            if st.button(sched_label, use_container_width=True, key="bulk_schedule_cta"):
                if not selected_leads:
                    st.error("Please select at least one recipient lead.")
                    return
                if not selected_subject.strip():
                    st.error("Please specify a subject line.")
                    return
                if not current_body.strip():
                    st.error("Please provide email body content.")
                    return

                # Token check on initial email
                unfilled_sample = []
                for lead in selected_leads[:5]:
                    resolved_text = (
                        f"{inject_variables(parse_spintax(selected_subject), lead)} "
                        f"{resolve_template(current_body, lead)}"
                    )
                    unfilled_sample.extend(_missing_tokens(resolved_text))
                if unfilled_sample:
                    st.error(f"Unfilled variables: {', '.join(set(unfilled_sample))}. Fill or use fallbacks.")
                    return

                render_bulk_schedule_dialog(
                    mode="schedule",
                    selected_leads=selected_leads,
                    selected_subject=selected_subject,
                    current_body=current_body,
                    all_mailboxes=all_mailboxes,
                    default_date=chosen_date,
                    default_time=chosen_time,
                    spread_hours=spread_hours,
                    total_fleet_cap=total_fleet_cap,
                    followup_steps=st.session_state.get("bulk_followup_steps", [])
                )
