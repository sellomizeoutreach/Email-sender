"""
ui/outbox.py - Reference Outbox, Sent Tracking & Dispatch History for Sellomize Reach.
Matches sellomize_reference.html:
- Filter pills: Scheduled | Sent | Paused / failed
- Sent Tab: Full open/unopened/replied/bounced/clicked tracking, KPI badges, filters, search, CSV export, and logical actions (View, Quick Follow-up, Mark Replied, DNC opt-out).
- Scheduled Tab: Live queue, Edit modal, Send now, Pause, Cancel.
- Paused / failed Tab: Resume, Retry, Edit, Delete, auto-pause reason.
"""

import streamlit as st
import html
import csv
import io
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from database import (
    get_emails,
    get_email_by_id,
    get_approved_due_emails,
    update_email,
    delete_email,
    get_smtp_accounts,
    get_contacts,
    mark_contact_do_not_contact,
    mark_contact_replied_manual,
)
from timezone_helper import get_engine_now, get_engine_now_str
from scheduler import dispatch_email_hostinger, run_scheduler_cycle
from ui.editor import render_dual_mode_editor
from ui.components import trigger_toast


# ---------------------------------------------------------------------------
# Dialog: View Sent Outreach Details & Tracking
# ---------------------------------------------------------------------------
@st.dialog("👁️ Sent Outreach Details & Tracking")
def render_view_sent_dialog(email_record: Dict[str, Any], matched_lead: Optional[Dict[str, Any]] = None):
    """Modal dialog displaying exact sent body HTML, delivery metadata, and real-time engagement telemetry."""
    eid = email_record["id"]
    recip = (email_record.get("recipient") or "").strip()
    subj = email_record.get("subject") or "No Subject"
    sent_time = email_record.get("updated_at") or email_record.get("created_at") or "Sent"
    mailbox = email_record.get("sent_via") or "Hostinger SMTP"
    open_count = int(email_record.get("open_count") or 0)
    opened_at = email_record.get("opened_at") or ""
    click_count = int(email_record.get("click_count") or 0)
    clicked_at = email_record.get("clicked_at") or ""
    last_clicked_url = email_record.get("last_clicked_url") or ""
    replied_at = email_record.get("replied_at") or (matched_lead.get("last_reply_at") if matched_lead else "")
    reply_subject = email_record.get("reply_subject") or (matched_lead.get("reply_subject") if matched_lead else "")
    is_bounced = int(email_record.get("is_bounced") or 0) == 1
    bounce_reason = email_record.get("bounce_reason") or ""
    body_html = email_record.get("email_html") or ""

    st.markdown(f"#### Outreach #OUT-{eid:04d}")
    st.markdown(f"**Subject:** *{html.escape(subj)}*")
    st.caption(f"**To:** {recip} · **Mailbox:** {html.escape(mailbox)} · **Delivered:** {sent_time}")

    # Engagement summary telemetry
    c_op, c_clk, c_rep = st.columns(3)
    with c_op:
        if open_count > 0 or opened_at:
            st.markdown(f"""
            <div style="background:#ECFDF5; border:1px solid #A7F3D0; border-radius:6px; padding:8px 10px;">
                <b style="color:#065F46; font-size:13px;">👁️ Opened ({open_count}x)</b>
                <div style="font-size:11px; color:#047857; margin-top:2px;">Last: {opened_at[:16]}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:8px 10px;">
                <b style="color:#64748B; font-size:13px;">⏳ Unopened</b>
                <div style="font-size:11px; color:#94A3B8; margin-top:2px;">No opens detected yet</div>
            </div>
            """, unsafe_allow_html=True)

    with c_clk:
        if click_count > 0:
            st.markdown(f"""
            <div style="background:#EFF6FF; border:1px solid #BFDBFE; border-radius:6px; padding:8px 10px;">
                <b style="color:#1D4ED8; font-size:13px;">🔗 Clicked ({click_count}x)</b>
                <div style="font-size:11px; color:#1E40AF; margin-top:2px;">Last: {clicked_at[:16]}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:8px 10px;">
                <b style="color:#64748B; font-size:13px;">🔗 0 Clicks</b>
                <div style="font-size:11px; color:#94A3B8; margin-top:2px;">No link clicks</div>
            </div>
            """, unsafe_allow_html=True)

    with c_rep:
        if replied_at:
            st.markdown(f"""
            <div style="background:#FEF3C7; border:1px solid #FDE68A; border-radius:6px; padding:8px 10px;">
                <b style="color:#B45309; font-size:13px;">💬 Replied</b>
                <div style="font-size:11px; color:#92400E; margin-top:2px;">Received: {replied_at[:16]}</div>
            </div>
            """, unsafe_allow_html=True)
        elif is_bounced:
            st.markdown(f"""
            <div style="background:#FEE2E2; border:1px solid #FECACA; border-radius:6px; padding:8px 10px;">
                <b style="color:#991B1B; font-size:13px;">⚠️ Bounced</b>
                <div style="font-size:11px; color:#7F1D1D; margin-top:2px;">{html.escape(bounce_reason[:28])}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:8px 10px;">
                <b style="color:#64748B; font-size:13px;">💬 No Reply</b>
                <div style="font-size:11px; color:#94A3B8; margin-top:2px;">Follow-up eligible</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:12px 0 8px;'>", unsafe_allow_html=True)
    st.markdown("<span style='font-size:12px; font-weight:700; color:#083731; text-transform:uppercase;'>Delivered Email Content</span>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='border:1px solid #E2E8F0; border-radius:8px; padding:12px 14px; background:#FFFFFF; max-height:220px; overflow-y:auto; font-size:13px; line-height:1.5; color:#1E293B;'>"
        f"{body_html}"
        f"</div>",
        unsafe_allow_html=True
    )

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("↩️ Quick Follow-Up", type="primary", use_container_width=True, key=f"dlg_fu_{eid}"):
            st.session_state["compose_recipient"] = recip
            clean_subj = subj if subj.lower().startswith("re:") else f"Re: {subj}"
            st.session_state["compose_subject"] = clean_subj
            st.session_state["active_screen"] = "compose"
            st.session_state["main_app_tabs"] = "✍️ Compose"
            st.rerun()

    with b2:
        if not replied_at:
            if st.button("💬 Mark as Replied", use_container_width=True, key=f"dlg_rep_{eid}"):
                mark_contact_replied_manual(recip, reply_subject="Operator noted reply from Outbox")
                trigger_toast(f"Marked {recip} as Replied!", icon="💬")
                st.rerun()
        else:
            st.caption("✅ Prospect reply verified")

    with b3:
        if st.button("🛑 Add to DNC", use_container_width=True, key=f"dlg_dnc_{eid}", help="Add contact to Do Not Contact suppression list"):
            mark_contact_do_not_contact(recip, reason="Operator marked DNC from Sent audit")
            trigger_toast(f"{recip} suppressed and added to DNC list.", icon="🛑")
            st.rerun()


# ---------------------------------------------------------------------------
# Dialog: Edit Scheduled Outreach
# ---------------------------------------------------------------------------
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

    dt_val = get_engine_now()
    if current_sched:
        try:
            dt_val = datetime.strptime(current_sched[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    from database import get_contact_by_email
    from template_engine import inject_variables, parse_spintax

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
    current_bcc = email_record.get("bcc_email") or ""
    new_bcc = st.text_input("BCC (optional, comma-separated)", value=current_bcc, key=f"edit_bcc_{eid}")

    c_date, c_time = st.columns(2)
    with c_date:
        new_date = st.date_input("Scheduled Date", value=dt_val.date(), key=f"edit_date_{eid}")
    with c_time:
        new_time = st.time_input("Scheduled Time (UTC+5)", value=dt_val.time(), key=f"edit_time_{eid}")

    st.markdown("<span class='lbl' style='margin-top:6px;'>Email Body Content</span>", unsafe_allow_html=True)
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
                smtp_account_id=chosen_mb_id,
                bcc_email=new_bcc.strip()
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
                smtp_account_id=chosen_mb_id,
                bcc_email=new_bcc.strip()
            )
            updated_rec = get_email_by_id(eid)
            with st.spinner("Dispatching via Hostinger..."):
                try:
                    ok = dispatch_email_hostinger(updated_rec)
                    if ok:
                        trigger_toast(f"Sent email to {new_recip.strip()}!", icon="🚀")
                        st.rerun()
                    else:
                        updated_e = get_email_by_id(eid)
                        err_reason = (updated_e.get("error_message") if updated_e else "") or "Check mailbox settings."
                        st.error(f"Dispatch failed: {err_reason}")
                except Exception as ex:
                    st.error(f"Dispatch exception: {ex}")


# ---------------------------------------------------------------------------
# Helper: Evaluate Sent Engagement Status
# ---------------------------------------------------------------------------
def _get_sent_engagement_status(email_rec: Dict[str, Any], lead: Optional[Dict[str, Any]]) -> str:
    """Classify sent email into Opened, Unopened, Replied, Clicked, or Bounced."""
    rep = bool(email_rec.get("replied_at")) or (lead and (lead.get("status") == "Replied" or bool(lead.get("last_reply_at"))))
    bounced = int(email_rec.get("is_bounced") or 0) == 1 or email_rec.get("status") == "Bounced"
    opened = int(email_rec.get("open_count") or 0) > 0 or bool(email_rec.get("opened_at"))
    clicked = int(email_rec.get("click_count") or 0) > 0 or bool(email_rec.get("clicked_at"))

    if rep:
        return "Replied"
    elif bounced:
        return "Bounced"
    elif clicked:
        return "Clicked"
    elif opened:
        return "Opened"
    else:
        return "Unopened"


# ---------------------------------------------------------------------------
# Main Outbox Render
# ---------------------------------------------------------------------------
def render_outbox_tab():
    """Render Outbox with Scheduled, Sent (with telemetry & filters), and Paused/Failed."""
    # ── Auto-dispatch overdue scheduled outreach immediately ──
    now_ts = get_engine_now_str()
    overdue_due = get_approved_due_emails(now_ts)
    if overdue_due:
        try:
            dispatched = run_scheduler_cycle(dry_run=False)
            if dispatched > 0:
                trigger_toast(f"⚡ Auto-dispatched {dispatched} overdue scheduled email(s) immediately!", icon="🚀")
        except Exception:
            pass

    all_emails = get_emails()
    all_leads = get_contacts()
    lead_map = {c.get("email", "").lower().strip(): c for c in all_leads if c.get("email")}

    # Main Filter Pills: Scheduled | Sent | Paused / failed
    if "outbox_filter" not in st.session_state:
        st.session_state["outbox_filter"] = "Scheduled"

    # Count items for main tabs
    scheduled_count = sum(1 for e in all_emails if e.get("status") in ["Approved", "Pending", "Scheduled"])
    sent_count = sum(1 for e in all_emails if e.get("status") == "Sent")
    paused_failed_count = sum(1 for e in all_emails if e.get("status") in ["Paused", "Failed", "Bounced", "Cancelled"])

    fp_col1, fp_col2, fp_col3, _ = st.columns([1.3, 1.2, 1.5, 3.5], vertical_alignment="center")
    with fp_col1:
        is_sched = st.session_state["outbox_filter"] == "Scheduled"
        if st.button(f"🕒 Scheduled ({scheduled_count})", key="outbox_pill_sched", type="primary" if is_sched else "secondary", use_container_width=True):
            st.session_state["outbox_filter"] = "Scheduled"
            st.rerun()

    with fp_col2:
        is_sent = st.session_state["outbox_filter"] == "Sent"
        if st.button(f"📬 Sent ({sent_count})", key="outbox_pill_sent", type="primary" if is_sent else "secondary", use_container_width=True):
            st.session_state["outbox_filter"] = "Sent"
            st.rerun()

    with fp_col3:
        is_pf = st.session_state["outbox_filter"] == "Paused / failed"
        if st.button(f"⏸️ Paused / failed ({paused_failed_count})", key="outbox_pill_pf", type="primary" if is_pf else "secondary", use_container_width=True):
            st.session_state["outbox_filter"] = "Paused / failed"
            st.rerun()

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    current_filter = st.session_state["outbox_filter"]

    # =========================================================================
    # TAB: SENT OUTREACH WITH REAL-TIME TELEMETRY & FILTERS
    # =========================================================================
    if current_filter == "Sent":
        sent_items = [e for e in all_emails if e.get("status") == "Sent"]

        if not sent_items:
            st.info("📬 No outreach emails have been sent yet. Use **Compose** or **Bulk Send** to launch dispatches.")
            return

        # ── 1. Calculate Sent KPI Metrics ──
        total_sent = len(sent_items)
        count_opened = sum(1 for e in sent_items if int(e.get("open_count") or 0) > 0 or bool(e.get("opened_at")))
        count_replied = sum(
            1 for e in sent_items
            if bool(e.get("replied_at")) or (lead_map.get((e.get("recipient") or "").lower().strip()) and lead_map[(e.get("recipient") or "").lower().strip()].get("status") == "Replied")
        )
        count_clicked = sum(1 for e in sent_items if int(e.get("click_count") or 0) > 0)
        count_bounced = sum(1 for e in sent_items if int(e.get("is_bounced") or 0) == 1 or e.get("status") == "Bounced")
        count_unopened = max(0, total_sent - count_opened)

        open_rate = (count_opened / total_sent * 100) if total_sent else 0
        reply_rate = (count_replied / total_sent * 100) if total_sent else 0

        # KPI Metrics Dashboard Banner
        m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
        with m_c1:
            st.markdown(f"""
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px 12px; text-align:center;">
                <div style="font-size:11px; font-weight:700; color:#64748B; text-transform:uppercase;">Total Sent</div>
                <div style="font-size:22px; font-weight:800; color:#083731; margin-top:2px;">{total_sent}</div>
                <div style="font-size:11px; color:#94A3B8;">100% dispatched</div>
            </div>
            """, unsafe_allow_html=True)
        with m_c2:
            st.markdown(f"""
            <div style="background:#ECFDF5; border:1px solid #A7F3D0; border-radius:8px; padding:10px 12px; text-align:center;">
                <div style="font-size:11px; font-weight:700; color:#065F46; text-transform:uppercase;">👁️ Opened</div>
                <div style="font-size:22px; font-weight:800; color:#047857; margin-top:2px;">{count_opened}</div>
                <div style="font-size:11px; color:#059669; font-weight:600;">{open_rate:.1f}% open rate</div>
            </div>
            """, unsafe_allow_html=True)
        with m_c3:
            st.markdown(f"""
            <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px 12px; text-align:center;">
                <div style="font-size:11px; font-weight:700; color:#64748B; text-transform:uppercase;">⏳ Unopened</div>
                <div style="font-size:22px; font-weight:800; color:#475569; margin-top:2px;">{count_unopened}</div>
                <div style="font-size:11px; color:#94A3B8;">{(count_unopened / total_sent * 100):.1f}% unopened</div>
            </div>
            """, unsafe_allow_html=True)
        with m_c4:
            st.markdown(f"""
            <div style="background:#FEF3C7; border:1px solid #FDE68A; border-radius:8px; padding:10px 12px; text-align:center;">
                <div style="font-size:11px; font-weight:700; color:#B45309; text-transform:uppercase;">💬 Replied</div>
                <div style="font-size:22px; font-weight:800; color:#92400E; margin-top:2px;">{count_replied}</div>
                <div style="font-size:11px; color:#D97706; font-weight:600;">{reply_rate:.1f}% reply rate</div>
            </div>
            """, unsafe_allow_html=True)
        with m_c5:
            st.markdown(f"""
            <div style="background:#EFF6FF; border:1px solid #BFDBFE; border-radius:8px; padding:10px 12px; text-align:center;">
                <div style="font-size:11px; font-weight:700; color:#1D4ED8; text-transform:uppercase;">🔗 Clicked / ⚠️ Bounced</div>
                <div style="font-size:22px; font-weight:800; color:#1E40AF; margin-top:2px;">{count_clicked} <span style="font-size:14px; font-weight:500; color:#991B1B;">/ {count_bounced}</span></div>
                <div style="font-size:11px; color:#3B82F6;">{count_clicked} clicked · {count_bounced} bounced</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:14px 0 10px;'>", unsafe_allow_html=True)

        # ── 2. Sent Status Filter Pills ──
        if "sent_status_filter" not in st.session_state:
            st.session_state["sent_status_filter"] = "All Sent"

        cur_stat_f = st.session_state["sent_status_filter"]
        p_all, p_op, p_unop, p_rep, p_clk, p_bnc = st.columns(6)
        with p_all:
            if st.button(f"All ({total_sent})", key="s_btn_all", type="primary" if cur_stat_f == "All Sent" else "secondary", use_container_width=True):
                st.session_state["sent_status_filter"] = "All Sent"
                st.rerun()
        with p_op:
            if st.button(f"👁️ Opened ({count_opened})", key="s_btn_op", type="primary" if cur_stat_f == "Opened" else "secondary", use_container_width=True):
                st.session_state["sent_status_filter"] = "Opened"
                st.rerun()
        with p_unop:
            if st.button(f"⏳ Unopened ({count_unopened})", key="s_btn_unop", type="primary" if cur_stat_f == "Unopened" else "secondary", use_container_width=True):
                st.session_state["sent_status_filter"] = "Unopened"
                st.rerun()
        with p_rep:
            if st.button(f"💬 Replied ({count_replied})", key="s_btn_rep", type="primary" if cur_stat_f == "Replied" else "secondary", use_container_width=True):
                st.session_state["sent_status_filter"] = "Replied"
                st.rerun()
        with p_clk:
            if st.button(f"🔗 Clicked ({count_clicked})", key="s_btn_clk", type="primary" if cur_stat_f == "Clicked" else "secondary", use_container_width=True):
                st.session_state["sent_status_filter"] = "Clicked"
                st.rerun()
        with p_bnc:
            if st.button(f"⚠️ Bounced ({count_bounced})", key="s_btn_bnc", type="primary" if cur_stat_f == "Bounced" else "secondary", use_container_width=True):
                st.session_state["sent_status_filter"] = "Bounced"
                st.rerun()

        # ── 3. Search and Secondary Filters Bar ──
        sf1, sf2, sf3, sf4, sf5 = st.columns([2.5, 1.4, 1.2, 1.3, 1.1], vertical_alignment="bottom")
        with sf1:
            search_query = st.text_input("Search Outreach", placeholder="Recipient, subject, lead, company...", key="sent_search_input", label_visibility="collapsed")
        with sf2:
            unique_mbs = sorted(list(set([e.get("sent_via") or "Hostinger" for e in sent_items if e.get("sent_via")])))
            mb_opts = ["All Mailboxes"] + unique_mbs
            sel_mb = st.selectbox("Mailbox", mb_opts, key="sent_sel_mb", label_visibility="collapsed")
        with sf3:
            time_opts = ["All Time", "Today", "Last 7 Days", "Last 30 Days"]
            sel_time = st.selectbox("Timeframe", time_opts, key="sent_sel_time", label_visibility="collapsed")
        with sf4:
            sort_opts = ["Newest Sent First", "Oldest Sent First", "Most Opens First", "Recipient (A-Z)"]
            sel_sort = st.selectbox("Sort", sort_opts, key="sent_sel_sort", label_visibility="collapsed")

        # ── 4. Apply Filters ──
        filtered_sent = []
        now_dt = get_engine_now()
        for e in sent_items:
            recip = (e.get("recipient") or "").lower().strip()
            lead = lead_map.get(recip)
            status_tag = _get_sent_engagement_status(e, lead)

            # Match status filter pill
            if cur_stat_f == "Opened" and status_tag != "Opened":
                continue
            if cur_stat_f == "Unopened" and status_tag != "Unopened":
                continue
            if cur_stat_f == "Replied" and status_tag != "Replied":
                continue
            if cur_stat_f == "Clicked" and status_tag != "Clicked":
                continue
            if cur_stat_f == "Bounced" and status_tag != "Bounced":
                continue

            # Match mailbox
            if sel_mb != "All Mailboxes" and (e.get("sent_via") or "Hostinger") != sel_mb:
                continue

            # Match timeframe
            sent_str = e.get("updated_at") or e.get("created_at") or ""
            if sel_time != "All Time" and sent_str:
                try:
                    s_dt = datetime.strptime(sent_str[:10], "%Y-%m-%d")
                    diff_days = (now_dt.date() - s_dt.date()).days
                    if sel_time == "Today" and diff_days > 0:
                        continue
                    if sel_time == "Last 7 Days" and diff_days > 7:
                        continue
                    if sel_time == "Last 30 Days" and diff_days > 30:
                        continue
                except Exception:
                    pass

            # Match search query
            if search_query.strip():
                sq = search_query.strip().lower()
                subj = (e.get("subject") or "").lower()
                lead_name = (lead.get("name") if lead else "").lower()
                company = (lead.get("company") if lead else "").lower()
                if sq not in recip and sq not in subj and sq not in lead_name and sq not in company:
                    continue

            filtered_sent.append((e, lead, status_tag))

        # Sort filtered list
        if sel_sort == "Newest Sent First":
            filtered_sent.sort(key=lambda x: x[0].get("updated_at") or x[0].get("created_at") or "", reverse=True)
        elif sel_sort == "Oldest Sent First":
            filtered_sent.sort(key=lambda x: x[0].get("updated_at") or x[0].get("created_at") or "")
        elif sel_sort == "Most Opens First":
            filtered_sent.sort(key=lambda x: int(x[0].get("open_count") or 0), reverse=True)
        elif sel_sort == "Recipient (A-Z)":
            filtered_sent.sort(key=lambda x: (x[0].get("recipient") or "").lower())

        # ── 5. CSV Export ──
        with sf5:
            csv_buf = io.StringIO()
            csv_writer = csv.writer(csv_buf)
            csv_writer.writerow([
                "Email ID", "Recipient", "Lead Name", "Company", "Subject", "Touch",
                "Mailbox", "Sent Date", "Status", "Opened", "Open Count", "Last Opened",
                "Clicked", "Click Count", "Replied", "Last Reply At"
            ])
            for e, lead, stag in filtered_sent:
                l_name = lead.get("name", "") if lead else ""
                l_comp = lead.get("company", "") if lead else ""
                op_bool = "Yes" if int(e.get("open_count") or 0) > 0 or e.get("opened_at") else "No"
                clk_bool = "Yes" if int(e.get("click_count") or 0) > 0 else "No"
                rep_bool = "Yes" if stag == "Replied" else "No"
                var_num = e.get("variation_num") or 1
                touch = "Initial" if var_num == 1 else f"Follow-up {var_num - 1}"
                csv_writer.writerow([
                    e["id"], e.get("recipient", ""), l_name, l_comp, e.get("subject", ""),
                    touch, e.get("sent_via", ""), e.get("updated_at", ""), stag,
                    op_bool, e.get("open_count", 0), e.get("opened_at", ""),
                    clk_bool, e.get("click_count", 0), rep_bool, e.get("replied_at", "")
                ])
            st.download_button(
                "📥 Export",
                data=csv_buf.getvalue(),
                file_name=f"sent_outreach_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True,
                key="btn_export_sent_csv"
            )

        st.markdown(f"<div style='font-size:12px; color:#64748B; margin:8px 0 6px;'>Showing <b>{len(filtered_sent)}</b> sent outreach message{'s' if len(filtered_sent) != 1 else ''}:</div>", unsafe_allow_html=True)

        if not filtered_sent:
            st.info("No sent outreach matched your current filter or search criteria.")
            return

        # ── 6. Render Sent Items Cards with Actions ──
        for e, matched_lead, status_tag in filtered_sent:
            eid = e["id"]
            recip = (e.get("recipient") or "No recipient").strip()
            lead_name = matched_lead.get("name") if (matched_lead and matched_lead.get("name")) else recip
            company_display = f" · <span style='color:#0F766E;'>{html.escape(matched_lead.get('company'))}</span>" if (matched_lead and matched_lead.get("company")) else ""

            var_num = e.get("variation_num") or 1
            touch_label = f"Follow-up {max(1, var_num - 1)}" if var_num > 1 else "Initial"

            sent_via = e.get("sent_via") or "Hostinger"
            mb_clean = sent_via.split("@")[0] + "@" if "@" in sent_via else sent_via
            when_display = (e.get("updated_at") or e.get("created_at") or "Sent")[:16]

            open_count = int(e.get("open_count") or 0)
            opened_at = e.get("opened_at") or ""
            replied_at = e.get("replied_at") or (matched_lead.get("last_reply_at") if matched_lead else "")

            # Render badge according to engagement status
            if status_tag == "Replied":
                pill_html = f'<span class="pill" style="background:#FEF3C7; color:#B45309; border:1px solid #FDE68A; font-weight:700;">💬 Replied</span>'
            elif status_tag == "Opened":
                pill_html = f'<span class="pill" style="background:#D1FAE5; color:#065F46; border:1px solid #A7F3D0; font-weight:700;">👁️ Opened ({open_count}x)</span>'
            elif status_tag == "Clicked":
                click_count = int(e.get("click_count") or 0)
                pill_html = f'<span class="pill" style="background:#EFF6FF; color:#1D4ED8; border:1px solid #BFDBFE; font-weight:700;">🔗 Clicked ({click_count}x)</span>'
            elif status_tag == "Bounced":
                pill_html = f'<span class="pill" style="background:#FEE2E2; color:#991B1B; border:1px solid #FECACA; font-weight:700;">⚠️ Bounced</span>'
            else:
                pill_html = f'<span class="pill" style="background:#F1F5F9; color:#64748B; border:1px solid #CBD5E1; font-weight:600;">⏳ Unopened</span>'

            with st.container():
                st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; padding:10px 14px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;">
                    <div style="flex:2.2; min-width:0;">
                        <strong style="color:#083731; font-size:14px;">{html.escape(lead_name)}</strong>
                        <span style="font-size:12px; color:#64748B; margin-left:6px;">{html.escape(recip)}</span>
                        {company_display}
                        <div style="font-size:12px; color:#334155; margin-top:2px;">{html.escape((e.get('subject') or 'No Subject')[:65])}</div>
                    </div>
                    <div style="flex:1.1; font-size:12px; color:#475569;">
                        <b>{touch_label}</b> · <span style="font-family:monospace;">{html.escape(mb_clean)}</span>
                    </div>
                    <div style="flex:1.2; font-size:12px; color:#64748B;">
                        {when_display}
                    </div>
                    <div style="flex:1.2; text-align:right;">
                        {pill_html}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Logical Actions Row for each Sent email
                r1, r2, r3, r4, _ = st.columns([1, 1.2, 1.2, 1, 2.5], vertical_alignment="center")
                with r1:
                    if st.button("👁️ View", key=f"s_view_{eid}", use_container_width=True, help="View delivered HTML body and telemetry"):
                        render_view_sent_dialog(e, matched_lead)
                with r2:
                    if st.button("↩️ Follow-Up", key=f"s_fu_{eid}", use_container_width=True, help="Open 1-click follow-up in Compose"):
                        st.session_state["compose_recipient"] = recip
                        s_subj = e.get("subject") or "Outreach"
                        st.session_state["compose_subject"] = s_subj if s_subj.lower().startswith("re:") else f"Re: {s_subj}"
                        st.session_state["active_screen"] = "compose"
                        st.session_state["main_app_tabs"] = "✍️ Compose"
                        st.rerun()
                with r3:
                    if status_tag != "Replied":
                        if st.button("💬 Replied", key=f"s_rep_{eid}", use_container_width=True, help="Flag as Replied (pauses further follow-up touches)"):
                            mark_contact_replied_manual(recip, reply_subject="Operator noted reply from Sent list")
                            trigger_toast(f"Marked {recip} as Replied!", icon="💬")
                            st.rerun()
                    else:
                        st.caption("✅ Replied")
                with r4:
                    if st.button("🛑 DNC", key=f"s_dnc_{eid}", use_container_width=True, help="Legal Opt-Out: Suppress contact & add to Do Not Contact list"):
                        mark_contact_do_not_contact(recip, reason="Operator marked DNC from Sent audit")
                        trigger_toast(f"{recip} suppressed in DNC list.", icon="🛑")
                        st.rerun()

        return

    # =========================================================================
    # TAB: SCHEDULED & PAUSED / FAILED QUEUES
    # =========================================================================
    if current_filter == "Scheduled":
        items = [e for e in all_emails if e.get("status") in ["Approved", "Pending", "Scheduled"]]
    else:
        items = [e for e in all_emails if e.get("status") in ["Paused", "Failed", "Bounced", "Cancelled"]]

    if not items:
        if current_filter == "Scheduled":
            st.info("No emails currently scheduled. Use **Compose** or **Bulk Send** to queue outreach.")
        else:
            st.success("✅ Clean queue: No paused follow-ups or failed deliveries.")
        return

    cur_now_ts = get_engine_now_str()
    due_items = [e for e in items if e.get("scheduled_time") and e.get("scheduled_time") <= cur_now_ts]
    if current_filter == "Scheduled" and due_items:
        d_c1, d_c2 = st.columns([3, 1], vertical_alignment="center")
        with d_c1:
            st.info(f"⚡ **{len(due_items)} email(s) are due/overdue for dispatch.**")
        with d_c2:
            if st.button("🚀 Auto-send All Due Now", type="primary", use_container_width=True, key="btn_outbox_flush_due"):
                with st.spinner("Dispatching due outreach..."):
                    dispatched = run_scheduler_cycle(dry_run=False)
                    trigger_toast(f"Dispatched {dispatched} email(s)!", icon="🚀")
                    st.rerun()

    st.markdown(f"<div style='font-size:12px; color:#64748B; margin-bottom:8px;'>Showing {len(items)} {current_filter.lower()} email(s):</div>", unsafe_allow_html=True)

    for e in items:
        eid = e["id"]
        recipient = (e.get("recipient") or "No recipient").strip()
        matched_lead = lead_map.get(recipient.lower())
        lead_display = matched_lead.get("name") if (matched_lead and matched_lead.get("name")) else recipient
        company_display = f" · <span style='color:#0F766E;'>{html.escape(matched_lead.get('company'))}</span>" if (matched_lead and matched_lead.get("company")) else ""

        var_num = e.get("variation_num") or 1
        is_reply = bool(e.get("in_reply_to"))
        touch_label = f"Follow-up {max(1, var_num - 1)}" if (is_reply or var_num > 1) else "Initial"

        sent_via = e.get("sent_via") or ""
        mailbox_display = sent_via.split("@")[0] + "@" if "@" in sent_via else (sent_via or "Hostinger")

        sched_time_str = e.get("scheduled_time") or ""
        target_tz = e.get("target_timezone") or "LOCAL"
        is_overdue = bool(sched_time_str and sched_time_str <= cur_now_ts)
        if sched_time_str:
            when_display = f"{sched_time_str[:16]} ({target_tz})" + (" · <b style='color:#B45309;'>⏰ Due Now</b>" if is_overdue else "")
        else:
            when_display = "Immediate"

        status_raw = e.get("status") or "Scheduled"
        if status_raw in ["Approved", "Pending", "Scheduled"]:
            if is_overdue:
                pill_html = '<span class="pill" style="background:#FEF3C7; color:#B45309; border:1px solid #FDE68A; font-weight:700;">⚡ Due to Send</span>'
            else:
                pill_html = '<span class="pill p-sent">Scheduled</span>'
        elif status_raw == "Paused":
            err_msg = e.get("error_message") or ""
            reason = "replied" if "replied" in err_msg.lower() or "reply" in err_msg.lower() else "manual"
            pill_html = f'<span class="pill p-warm">Paused · {reason}</span>'
        elif status_raw == "Bounced":
            pill_html = '<span class="pill p-bounce">Bounced</span>'
        elif status_raw == "Cancelled":
            pill_html = '<span class="pill" style="background:#FEE2E2; color:#991B1B; border:1px solid #FECACA;">Cancelled · DNC</span>'
        else:
            pill_html = f'<span class="pill p-fail">{html.escape(status_raw)}</span>'

        with st.container():
            st.markdown(f"""
            <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; padding:10px 14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                <div style="flex:2; min-width:0;">
                    <strong style="color:#083731; font-size:14px;">{html.escape(lead_display)}</strong>
                    <span style="font-size:12px; color:#64748B; margin-left:8px;">{html.escape(recipient)}</span>
                    {company_display}
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

            btn_col1, btn_col2, btn_col3, btn_col4, _ = st.columns([1, 1, 1, 1, 2], vertical_alignment="center")

            if current_filter == "Scheduled":
                with btn_col1:
                    if st.button("✏️ Edit", key=f"outbox_edit_{eid}", use_container_width=True):
                        render_edit_email_dialog(e)
                with btn_col2:
                    if st.button("🚀 Send now", key=f"outbox_send_{eid}", use_container_width=True, type="primary"):
                        with st.spinner("Dispatching via Hostinger..."):
                            try:
                                ok = dispatch_email_hostinger(e)
                                if ok:
                                    trigger_toast(f"Dispatched email to {recipient}!", icon="🚀")
                                    st.rerun()
                                else:
                                    updated_e = get_email_by_id(eid)
                                    err_reason = (updated_e.get("error_message") if updated_e else "") or "Unknown error"
                                    st.error(f"Dispatch failed: {err_reason}. Check mailbox connection in Settings.")
                            except Exception as ex:
                                st.error(f"Dispatch exception: {ex}")
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
                            try:
                                ok = dispatch_email_hostinger(e)
                                if ok:
                                    trigger_toast(f"Email #{eid} sent successfully!", icon="✅")
                                    st.rerun()
                                else:
                                    updated_e = get_email_by_id(eid)
                                    err_reason = (updated_e.get("error_message") if updated_e else "") or "Check mailbox credentials."
                                    st.error(f"Retry failed: {err_reason}")
                            except Exception as ex:
                                st.error(f"Retry exception: {ex}")
                with btn_col4:
                    if st.button("🗑️ Delete", key=f"outbox_pf_del_{eid}", use_container_width=True):
                        delete_email(eid)
                        trigger_toast(f"Email #{eid} deleted.", icon="🗑️")
                        st.rerun()

    st.markdown("<p class='sec' style='margin-top:16px;'>Scheduled follow-ups auto-pause here when a lead replies — cancel or edit before they fire.</p>", unsafe_allow_html=True)
