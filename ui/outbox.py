"""
ui/outbox.py - Reference Outbox & Dispatch History for Sellomize Reach.
Matches sellomize_reference.html:
- Filter pills: Scheduled, Sent, Paused / failed
- Clean table with columns: Lead, Touch, Mailbox, When (local), Status, Actions (Cancel, Review, Retry, Send now, Edit)
- Edit Scheduled Outreach modal dialog with dual-mode editor, recipient, subject, scheduled date/time, and mailbox reassignment.
- Scheduled follow-ups auto-pause note.
"""

import streamlit as st
import html
from datetime import datetime
from typing import List, Dict, Any, Optional

from database import (
    get_emails,
    get_email_by_id,
    update_email,
    delete_email,
    get_smtp_accounts,
    get_contacts,
)
from timezone_helper import get_engine_now
from scheduler import dispatch_email_hostinger
from ui.editor import render_dual_mode_editor
from ui.components import trigger_toast


@st.dialog("✏️ Edit Scheduled Outreach")
def render_edit_email_dialog(email_record: Dict[str, Any]):
    """Modal dialog to edit subject, recipient, scheduled date/time, mailbox, and email body."""
    eid = email_record["id"]
    current_recip = email_record.get("recipient") or ""
    current_subj = email_record.get("subject") or ""
    current_sched = email_record.get("scheduled_time") or ""
    current_html = email_record.get("email_html") or ""
    current_mb_id = email_record.get("smtp_account_id")

    all_mailboxes = get_smtp_accounts(active_only=True)

    # Parse scheduled date and time
    dt_val = get_engine_now()
    if current_sched:
        try:
            dt_val = datetime.strptime(current_sched[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    from database import get_contact_by_email
    from template_engine import inject_variables, parse_spintax

    # If recipient exists in CRM, ensure variables are fully resolved to their final values
    lead_match = get_contact_by_email(current_recip) if current_recip else None
    if lead_match:
        current_subj = inject_variables(parse_spintax(current_subj), lead_match)
        current_html = inject_variables(current_html, lead_match)

    st.markdown(f"#### Edit Outreach #OUT-{eid:04d}")

    c1, c2 = st.columns(2)
    with c1:
        new_recip = st.text_input("Recipient Email", value=current_recip, key=f"edit_recip_{eid}")
    with c2:
        if all_mailboxes:
            mb_options = {f"{mb.get('sender_name') or mb.get('email')} <{mb.get('email')}>": mb["id"] for mb in all_mailboxes}
            curr_idx = 0
            for idx, mb_id in enumerate(mb_options.values()):
                if mb_id == current_mb_id:
                    curr_idx = idx
                    break
            chosen_mb_label = st.selectbox("Sending Mailbox", list(mb_options.keys()), index=curr_idx, key=f"edit_mb_{eid}")
            chosen_mb_id = mb_options[chosen_mb_label]
        else:
            st.caption("No mailboxes configured.")
            chosen_mb_id = current_mb_id

    new_subj = st.text_input("Subject Line", value=current_subj, key=f"edit_subj_{eid}")

    c_date, c_time = st.columns(2)
    with c_date:
        new_date = st.date_input("Scheduled Date", value=dt_val.date(), key=f"edit_date_{eid}")
    with c_time:
        new_time = st.time_input("Scheduled Time (UTC+5)", value=dt_val.time(), key=f"edit_time_{eid}")

    st.markdown("<span class='lbl' style='margin-top:6px;'>Email Body Content (Final text with variables — edits take priority)</span>", unsafe_allow_html=True)
    new_body = render_dual_mode_editor(
        key_prefix=f"edit_outbox_{eid}",
        initial_content=current_html,
        height=180
    )

    btn_save, btn_send_now = st.columns(2)
    with btn_save:
        if st.button("💾 Save Changes", type="primary", use_container_width=True, key=f"save_edit_{eid}"):
            combined_dt = datetime.combine(new_date, new_time)
            sched_str = combined_dt.strftime("%Y-%m-%d %H:%M:%S")
            update_email(
                email_id=eid,
                recipient=new_recip.strip(),
                subject=new_subj.strip(),
                email_html=new_body,
                scheduled_time=sched_str,
                smtp_account_id=chosen_mb_id
            )
            trigger_toast(f"Email #OUT-{eid:04d} updated!", icon="💾")
            st.rerun()

    with btn_send_now:
        if st.button("🚀 Send Immediately", use_container_width=True, key=f"send_edit_{eid}"):
            combined_dt = datetime.combine(new_date, new_time)
            sched_str = combined_dt.strftime("%Y-%m-%d %H:%M:%S")
            update_email(
                email_id=eid,
                recipient=new_recip.strip(),
                subject=new_subj.strip(),
                email_html=new_body,
                scheduled_time=sched_str,
                smtp_account_id=chosen_mb_id
            )
            updated_rec = get_email_by_id(eid)
            with st.spinner("Dispatching via Hostinger..."):
                ok = dispatch_email_hostinger(updated_rec)
                if ok:
                    trigger_toast(f"Sent email to {new_recip.strip()}!", icon="🚀")
                else:
                    st.error("Dispatch failed. Check mailbox settings.")
            st.rerun()


def render_outbox_tab():
    """Render Outbox matching sellomize_reference.html."""
    all_emails = get_emails()
    all_leads = get_contacts()
    lead_map = {c.get("email", "").lower().strip(): c for c in all_leads if c.get("email")}

    # Filter pills across the top: Scheduled | Sent | Paused / failed
    if "outbox_filter" not in st.session_state:
        st.session_state["outbox_filter"] = "Scheduled"

    fp_col1, fp_col2, fp_col3, _ = st.columns([1.2, 1, 1.4, 4])
    with fp_col1:
        is_sched = st.session_state["outbox_filter"] == "Scheduled"
        if st.button("🕒 Scheduled", key="outbox_pill_sched", type="primary" if is_sched else "secondary", use_container_width=True):
            st.session_state["outbox_filter"] = "Scheduled"
            st.rerun()

    with fp_col2:
        is_sent = st.session_state["outbox_filter"] == "Sent"
        if st.button("📬 Sent", key="outbox_pill_sent", type="primary" if is_sent else "secondary", use_container_width=True):
            st.session_state["outbox_filter"] = "Sent"
            st.rerun()

    with fp_col3:
        is_pf = st.session_state["outbox_filter"] == "Paused / failed"
        if st.button("⏸️ Paused / failed", key="outbox_pill_pf", type="primary" if is_pf else "secondary", use_container_width=True):
            st.session_state["outbox_filter"] = "Paused / failed"
            st.rerun()

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    # Filter items according to active pill
    current_filter = st.session_state["outbox_filter"]
    if current_filter == "Scheduled":
        items = [e for e in all_emails if e.get("status") in ["Approved", "Pending", "Scheduled"]]
    elif current_filter == "Sent":
        items = [e for e in all_emails if e.get("status") == "Sent"]
    else:
        items = [e for e in all_emails if e.get("status") in ["Paused", "Failed", "Bounced", "Cancelled"]]

    if not items:
        if current_filter == "Scheduled":
            st.info("No emails currently scheduled. Use **Compose** or **Bulk Send** to queue outreach.")
        elif current_filter == "Sent":
            st.caption("No emails have been sent yet.")
        else:
            st.success("✅ Clean queue: No paused follow-ups or failed deliveries.")
        return

    # Render items in table-like card rows
    st.markdown(f"<div style='font-size:12px; color:#64748B; margin-bottom:8px;'>Showing {len(items)} {current_filter.lower()} email(s):</div>", unsafe_allow_html=True)

    for e in items:
        eid = e["id"]
        recipient = (e.get("recipient") or "No recipient").strip()
        matched_lead = lead_map.get(recipient.lower())
        lead_display = matched_lead.get("name") if (matched_lead and matched_lead.get("name")) else recipient

        var_num = e.get("variation_num") or 1
        is_reply = bool(e.get("in_reply_to"))
        if is_reply or var_num > 1:
            touch_label = f"Follow-up {max(1, var_num - 1)}"
        else:
            touch_label = "Initial"

        sent_via = e.get("sent_via") or ""
        mailbox_display = sent_via.split("@")[0] + "@" if "@" in sent_via else (sent_via or "Hostinger")

        sched_time_str = e.get("scheduled_time") or ""
        target_tz = e.get("target_timezone") or "LOCAL"
        if current_filter == "Sent":
            when_display = (e.get("updated_at") or e.get("created_at") or "Sent")[:16]
        else:
            if sched_time_str:
                when_display = f"{sched_time_str[:16]} ({target_tz})"
            else:
                when_display = "Immediate"

        status_raw = e.get("status") or "Scheduled"
        if status_raw in ["Approved", "Pending", "Scheduled"]:
            pill_html = '<span class="pill p-sent">Scheduled</span>'
        elif status_raw == "Sent":
            pill_html = '<span class="pill p-pass">Sent</span>'
        elif status_raw == "Paused":
            err_msg = e.get("error_message") or ""
            reason = "replied" if "replied" in err_msg.lower() or "reply" in err_msg.lower() else "manual"
            pill_html = f'<span class="pill p-warm">Paused · {reason}</span>'
        elif status_raw == "Bounced":
            pill_html = '<span class="pill p-bounce">Bounced</span>'
        else:
            pill_html = f'<span class="pill p-fail">{html.escape(status_raw)}</span>'

        with st.container():
            st.markdown(f"""
            <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; padding:10px 14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                <div style="flex:2; min-width:0;">
                    <strong style="color:#083731; font-size:14px;">{html.escape(lead_display)}</strong>
                    <span style="font-size:12px; color:#64748B; margin-left:8px;">{html.escape(recipient)}</span>
                    <div style="font-size:12px; color:#334155; margin-top:2px;">{html.escape((e.get('subject') or 'No Subject')[:60])}</div>
                </div>
                <div style="flex:1; font-size:12px; color:#475569;">
                    <b>{touch_label}</b> · <span style="font-family:monospace;">{html.escape(mailbox_display)}</span>
                </div>
                <div style="flex:1.5; font-size:12px; color:#64748B;">
                    {when_display}
                </div>
                <div style="flex:1; text-align:center;">
                    {pill_html}
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Action buttons row
            btn_col1, btn_col2, btn_col3, btn_col4, _ = st.columns([1, 1, 1, 1, 2])

            if current_filter == "Scheduled":
                with btn_col1:
                    if st.button("✏️ Edit", key=f"outbox_edit_{eid}", use_container_width=True):
                        render_edit_email_dialog(e)
                with btn_col2:
                    if st.button("🚀 Send now", key=f"outbox_send_{eid}", use_container_width=True, type="primary"):
                        with st.spinner("Dispatching via Hostinger..."):
                            ok = dispatch_email_hostinger(e)
                            if ok:
                                trigger_toast(f"Dispatched email to {recipient}!", icon="🚀")
                            else:
                                st.error("Dispatch failed. Check mailbox connection in Settings.")
                        st.rerun()
                with btn_col3:
                    if st.button("⏸️ Pause", key=f"outbox_pause_{eid}", use_container_width=True):
                        update_email(email_id=eid, status="Paused")
                        trigger_toast(f"Email #{eid} paused.", icon="⏸️")
                        st.rerun()
                with btn_col4:
                    if st.button("❌ Cancel", key=f"outbox_del_{eid}", use_container_width=True):
                        delete_email(eid)
                        trigger_toast(f"Email #{eid} cancelled and removed.", icon="🗑️")
                        st.rerun()

            elif current_filter == "Paused / failed":
                with btn_col1:
                    if st.button("✏️ Edit", key=f"outbox_pf_edit_{eid}", use_container_width=True):
                        render_edit_email_dialog(e)
                with btn_col2:
                    if st.button("▶️ Resume", key=f"outbox_res_{eid}", use_container_width=True, type="primary"):
                        update_email(email_id=eid, status="Approved")
                        trigger_toast(f"Email #{eid} unpaused & queued.", icon="▶️")
                        st.rerun()
                with btn_col3:
                    if st.button("🔄 Retry", key=f"outbox_retry_{eid}", use_container_width=True):
                        with st.spinner("Retrying dispatch..."):
                            ok = dispatch_email_hostinger(e)
                            if ok:
                                trigger_toast(f"Email #{eid} sent successfully!", icon="✅")
                            else:
                                st.error("Retry failed. Check mailbox credentials.")
                        st.rerun()
                with btn_col4:
                    if st.button("🗑️ Delete", key=f"outbox_pf_del_{eid}", use_container_width=True):
                        delete_email(eid)
                        trigger_toast(f"Email #{eid} deleted.", icon="🗑️")
                        st.rerun()

    st.markdown("<p class='sec' style='margin-top:16px;'>Scheduled follow-ups auto-pause here when a lead replies — cancel or edit before they fire.</p>", unsafe_allow_html=True)
