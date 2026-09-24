"""
ui/compose.py - Streamlined Compose & Outreach for Sellomize Reach.
Fast, clean dual-mode email authoring with real-time deliverability audit,
safety gates, and Hostinger SMTP dispatch.
"""

import streamlit as st
import html
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from database import (
    get_contacts,
    get_templates,
    get_smtp_accounts,
    get_config,
    create_email,
    get_emails,
    get_contact_by_id,
    get_contact_by_email,
    upsert_contact_by_email,
    create_template,
    update_template,
    DB_FILE,
)
from template_engine import (
    resolve_template,
    inject_variables,
    parse_spintax,
    audit_email_deliverability,
    _missing_tokens,
    format_email_html,
)
from scheduler import dispatch_email_hostinger
from ui.editor import render_dual_mode_editor
from ui.components import render_tab_header, trigger_toast

_TOKEN_RE = re.compile(r'\[([A-Za-z0-9_]+)\]|\{([A-Za-z0-9_]+)\}')

# Backward compatibility copies for legacy tests
DEFAULT_COPIES = {
    1: {
        "subj": "Quick observation for [Company]",
        "body": "Hi [Name],\n\nI noticed [Company] and wanted to reach out regarding your growth.\n\nBest regards,",
    },
    2: {
        "subj": "Re: Quick observation for [Company]",
        "body": "Hi [Name],\n\nJust following up on my previous note.\n\nBest,",
    },
    3: {
        "subj": "Final note for [Company]",
        "body": "Hi [Name],\n\nI haven't heard back, so I'll assume the timing isn't right. Wishing you success!",
    },
}


def _apply_variable_fallback(text: str, fallback: str) -> str:
    """Replace remaining [Token] or {token} placeholders with the fallback value."""
    if not fallback:
        return _TOKEN_RE.sub("", text)
    return _TOKEN_RE.sub(fallback, text)


def _stub_contact(email: str) -> dict:
    """Minimal contact dict defaulting strictly to LOCAL timezone."""
    return {
        "id": None,
        "name": "",
        "email": email,
        "company": "",
        "country_or_timezone": "LOCAL",
        "custom_variables_dict": {},
        "custom_variables": "{}",
    }


def _resolve_all_recipients(crm_ids: list, manual_emails: list, contact_id_map: dict,
                             num_touches: int = 1, save_manual: bool = False) -> list:
    """Helper for recipient list resolution."""
    recipients = []
    seen_emails = set()

    for cid in crm_ids:
        c = contact_id_map.get(cid) or get_contact_by_id(cid)
        if c:
            email = (c.get("email") or "").strip().lower()
            if email and email not in seen_emails:
                seen_emails.add(email)
                recipients.append({"contact": c, "source": "crm"})

    for em in manual_emails:
        em_clean = em.strip().lower()
        if em_clean in seen_emails:
            continue
        seen_emails.add(em_clean)

        existing = get_contact_by_email(em_clean)
        if existing:
            recipients.append({"contact": existing, "source": "crm_found"})
        elif save_manual or num_touches > 1:
            new_id, _ = upsert_contact_by_email(name="", email=em_clean, company="")
            new_c = get_contact_by_id(new_id) or _stub_contact(em_clean)
            new_c["id"] = new_id
            recipients.append({"contact": new_c, "source": "manual_saved"})
        else:
            recipients.append({"contact": _stub_contact(em_clean), "source": "manual_stub"})

    return recipients


def _get_default_copy(touch_step: int, sample_contact: dict, is_single_recipient: bool) -> dict:
    """Return default copy for outreach touch."""
    if not is_single_recipient:
        return DEFAULT_COPIES.get(touch_step, DEFAULT_COPIES[1])

    c_name = (sample_contact.get("name") or "").strip()
    c_comp = (sample_contact.get("company") or "").strip()
    greeting = f"Hi {c_name}," if c_name else "Hi,"
    subj_comp = f" for {c_comp}" if c_comp else ""

    if touch_step == 1:
        return {
            "subj": f"Quick question{subj_comp}",
            "body": f"{greeting}\n\nI wanted to reach out regarding your work at {c_comp or 'your company'}.\n\nBest regards,",
        }
    elif touch_step == 2:
        return {
            "subj": f"Re: Quick question{subj_comp}",
            "body": f"{greeting}\n\nFollowing up to see if you had a chance to review my previous note.\n\nBest,",
        }
    else:
        return {
            "subj": f"Final note{subj_comp}",
            "body": f"{greeting}\n\nI haven't heard back, so I'll assume the timing isn't right. Wishing you continued success!",
        }


def render_compose_tab(contacts: Optional[List[Dict[str, Any]]] = None, templates: Optional[List[Dict[str, Any]]] = None):
    """Render the simple, focused Compose & Style tab."""
    render_tab_header(
        "✍️ Compose & Outreach",
        "Compose styled cold emails, test live variable previews, and dispatch safely through Hostinger."
    )

    if contacts is None:
        contacts = get_contacts()
    if templates is None:
        templates = get_templates()

    smtp_accounts = get_smtp_accounts(active_only=True)

    # Check for prefill from Leads tab
    prefill_lead_id = st.session_state.pop("prefill_compose_lead_id", None)
    if prefill_lead_id:
        st.session_state["compose_selected_lead_id"] = prefill_lead_id

    # Initialize default state
    if "compose_body_html" not in st.session_state:
        st.session_state["compose_body_html"] = (
            "<p>Hi [Name],</p>\n"
            "<p>I noticed [Company] and wanted to reach out regarding your brand growth.</p>\n"
            "<p>Do you have a few minutes this week for a brief call?</p>\n"
            "<p>Best regards,</p>"
        )
    if "compose_subject" not in st.session_state:
        st.session_state["compose_subject"] = "Quick observation for [Company]"

    # --- ROW 1: SENDER MAILBOX & TEMPLATE SELECTOR ---
    c_mb, c_tpl = st.columns([1.5, 1.5])
    with c_mb:
        if not smtp_accounts:
            st.warning("⚠️ No active Hostinger mailboxes. Connect one in **Settings**.")
            selected_mb = None
        else:
            mb_choices = {f"{a.get('sender_name') or 'Mailbox'} <{a['email']}>": a for a in smtp_accounts}
            sel_mb_label = st.selectbox("Sender Mailbox", list(mb_choices.keys()), key="compose_mb_sel")
            selected_mb = mb_choices[sel_mb_label]

    with c_tpl:
        if templates:
            tpl_choices = {"-- Select a Template to Load --": None}
            for t in templates:
                tpl_choices[t.get("template_name") or t.get("name") or "Template"] = t
            sel_tpl_name = st.selectbox("Load Template", list(tpl_choices.keys()), key="compose_tpl_sel")
            if sel_tpl_name and sel_tpl_name != "-- Select a Template to Load --":
                chosen_t = tpl_choices[sel_tpl_name]
                if chosen_t:
                    new_subj = chosen_t.get("subject") or ""
                    new_body = chosen_t.get("body_content") or chosen_t.get("body_html") or ""
                    if st.session_state.get("_last_loaded_tpl") != sel_tpl_name:
                        st.session_state["compose_subject"] = new_subj
                        st.session_state["compose_body_html"] = new_body
                        st.session_state["_last_loaded_tpl"] = sel_tpl_name
                        st.rerun()
        else:
            st.selectbox("Load Template", ["No templates saved yet"], disabled=True)

    # --- ROW 2: RECIPIENT ---
    lead_choices = {"-- Type or select recipient --": None}
    for c in contacts:
        label = f"{c.get('name') or 'Lead'} <{c.get('email')}>" + (f" ({c.get('company')})" if c.get('company') else "")
        lead_choices[label] = c

    recip_col1, recip_col2 = st.columns([2, 1])
    with recip_col1:
        # Preselected index if navigated from Leads tab
        preselected_idx = 0
        target_prefill_id = st.session_state.get("compose_selected_lead_id")
        if target_prefill_id:
            for idx, (lbl, l_obj) in enumerate(lead_choices.items()):
                if l_obj and l_obj.get("id") == target_prefill_id:
                    preselected_idx = idx
                    break

        sel_lead_label = st.selectbox(
            "Select Recipient from Leads",
            list(lead_choices.keys()),
            index=preselected_idx,
            key="compose_lead_picker"
        )
        selected_lead_obj = lead_choices.get(sel_lead_label)

    with recip_col2:
        custom_email_typed = st.text_input(
            "Or Type Email Directly",
            value=selected_lead_obj.get("email") if selected_lead_obj else "",
            placeholder="lead@company.com",
            key="compose_custom_email"
        )

    # Resolve active recipient
    if selected_lead_obj and not custom_email_typed.strip():
        current_lead = selected_lead_obj
    elif custom_email_typed.strip():
        matched = get_contact_by_email(custom_email_typed.strip())
        if matched:
            current_lead = matched
        else:
            current_lead = {
                "id": None,
                "name": selected_lead_obj.get("name", "") if selected_lead_obj else "",
                "email": custom_email_typed.strip(),
                "company": selected_lead_obj.get("company", "") if selected_lead_obj else "",
                "country_or_timezone": "LOCAL"
            }
    else:
        current_lead = {"name": "", "email": "", "company": "", "country_or_timezone": "LOCAL"}

    # --- ROW 3: SUBJECT LINE & THREADING ---
    subj_val = st.text_input(
        "Subject Line",
        value=st.session_state.get("compose_subject", ""),
        placeholder="e.g. Quick question for [Company]",
        key="input_compose_subject"
    )
    st.session_state["compose_subject"] = subj_val

    # Auto-threading check
    is_reply_on_thread = st.checkbox(
        "🧵 Thread as reply to previous email to this recipient (sets In-Reply-To & References)",
        value=False,
        key="compose_thread_toggle"
    )
    original_msg_id = ""
    if is_reply_on_thread and current_lead.get("email"):
        # Auto-lookup the most recent sent email to this recipient
        prev_emails = [e for e in get_emails() if (e.get("recipient") or "").lower() == current_lead["email"].lower() and e.get("status") == "Sent"]
        if prev_emails:
            latest_prev = prev_emails[-1]
            original_msg_id = latest_prev.get("message_id") or latest_prev.get("in_reply_to") or f"<msg-{latest_prev['id']}@sellomize.com>"
            st.caption(f"✓ Automatically threading onto previous Message-ID: `{original_msg_id}`")
        else:
            st.caption("ℹ️ No previous sent emails found for this lead; a clean outbound email will be sent.")

    # --- ROW 4: DUAL-MODE EDITOR ---
    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
    current_body = render_dual_mode_editor(
        key_prefix="compose",
        initial_content=st.session_state.get("compose_body_html", ""),
        height=240
    )
    st.session_state["compose_body_html"] = current_body

    # --- ROW 5: DELIVERABILITY & LIVE RESOLVED PREVIEW ---
    final_subj = inject_variables(parse_spintax(subj_val), current_lead)
    final_body = resolve_template(current_body, current_lead)
    display_subj = f"Re: {final_subj}" if (is_reply_on_thread and not final_subj.lower().startswith("re:")) else final_subj

    missing_tokens = sorted(list(set(_missing_tokens(final_subj) + _missing_tokens(final_body))))
    neg_keywords = get_config("negative_keywords", "")
    audit = audit_email_deliverability(body_html=final_body, subject=display_subj, custom_negative_keywords=neg_keywords)

    score = audit.get("score", 100)
    detected_spam = audit.get("detected_spam_words", [])

    c_audit, c_prev = st.columns([1.2, 2.8])

    with c_audit:
        st.markdown("**Deliverability & Safety**")
        if missing_tokens:
            st.error(f"🚫 **Unfilled Tokens:** {', '.join(missing_tokens)}")
            can_send = False
        else:
            can_send = bool((current_lead.get("email") or "").strip())

        if detected_spam:
            st.warning(f"⚠️ **Spam Triggers ({len(detected_spam)}):**")
            for w in detected_spam:
                st.markdown(f"- `{w.get('word')}`")
        else:
            st.success(f"✅ **Spam Score:** {score}/100 · Clean")

        if not (current_lead.get("email") or "").strip():
            st.caption("Enter a recipient email to enable sending.")

    with c_prev:
        lead_display_name = current_lead.get("name") or current_lead.get("email") or "Lead"
        st.markdown(f"**Live Preview for `{lead_display_name}`:**")
        st.markdown(
            f"""<div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.18); border-radius:8px; padding:12px 14px; font-size:0.88rem;">
                <div style="border-bottom:1px solid #E2E8F0; padding-bottom:6px; margin-bottom:8px; font-size:0.8rem; color:#475569;">
                    <div><strong>To:</strong> {html.escape(current_lead.get('email') or 'recipient@example.com')}</div>
                    <div><strong>Subject:</strong> {html.escape(display_subj or '(No Subject)')}</div>
                </div>
                <div style="line-height:1.55; color:#0F172A;">
                    {final_body}
                </div>
            </div>""",
            unsafe_allow_html=True
        )

    # --- ROW 6: ACTIONS ---
    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    act_c1, act_c2, act_c3 = st.columns([1.5, 1.5, 1.2])

    with act_c1:
        if st.button("🚀 Send Now", type="primary", use_container_width=True, disabled=not can_send):
            if not selected_mb:
                st.error("No active mailbox configured to send.")
            else:
                with st.spinner("Dispatching via Hostinger SMTP..."):
                    email_html = format_email_html(final_body)
                    email_id = create_email(
                        email_html=email_html,
                        subject=display_subj,
                        recipient=current_lead["email"].strip(),
                        status="Approved",
                        scheduled_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        in_reply_to=original_msg_id.strip(),
                        target_timezone="LOCAL"
                    )
                    email_record = {
                        "id": email_id,
                        "recipient": current_lead["email"].strip(),
                        "subject": display_subj,
                        "email_html": email_html,
                        "in_reply_to": original_msg_id.strip(),
                        "smtp_account_id": selected_mb["id"],
                        "target_timezone": "LOCAL"
                    }
                    ok = dispatch_email_hostinger(email_record)
                    if ok:
                        trigger_toast(f"Email sent successfully to {current_lead['email']}!", icon="🚀")
                        st.session_state["main_app_tabs"] = "📥 Outbox"
                        st.rerun()
                    else:
                        st.error("Dispatch failed. Check mailbox settings.")

    with act_c2:
        with st.popover("🕒 Schedule...", use_container_width=True):
            st.markdown("**Schedule Outreach Dispatch**")
            sched_date = st.date_input("Date", value=datetime.now().date(), key="compose_sched_d")
            sched_time = st.time_input("Time", value=(datetime.now() + timedelta(hours=1)).time(), key="compose_sched_t")
            if st.button("Confirm Schedule", type="primary", use_container_width=True, disabled=not can_send):
                combined_dt = datetime.combine(sched_date, sched_time)
                sched_iso = combined_dt.strftime("%Y-%m-%d %H:%M:%S")
                email_html = format_email_html(final_body)
                create_email(
                    email_html=email_html,
                    subject=display_subj,
                    recipient=current_lead["email"].strip(),
                    status="Approved",
                    scheduled_time=sched_iso,
                    in_reply_to=original_msg_id.strip(),
                    target_timezone="LOCAL"
                )
                trigger_toast(f"Email scheduled for {sched_iso}!", icon="🕒")
                st.session_state["main_app_tabs"] = "📥 Outbox"
                st.rerun()

    with act_c3:
        if st.button("💾 Save Draft", use_container_width=True):
            email_html = format_email_html(final_body)
            create_email(
                email_html=email_html,
                subject=display_subj,
                recipient=current_lead.get("email", "").strip(),
                status="Pending",
                scheduled_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                in_reply_to=original_msg_id.strip(),
                target_timezone="LOCAL"
            )
            trigger_toast("Draft saved to Outbox!", icon="💾")
