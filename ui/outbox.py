"""
ui/outbox.py - Unified Dispatch Log & Outbox Monitor for Sellomize Reach.
Section C6 of Complete Restructure Spec.

Three focused sub-views:
1. 🕒 Scheduled: Pending dispatches with time, recipient, timezone, actions (Pause, Resume, Cancel, Send Now).
2. 📬 Sent: Delivery history log with recipient, subject, sent timestamp, open/click counts.
3. ⏸️ Paused / Failed: Auto-paused follow-ups from prospect replies, bounces, errors with retry/delete.
"""

import streamlit as st
import pandas as pd
from typing import List, Dict, Any, Optional

from database import (
    get_emails,
    get_email_by_id,
    update_email,
    mark_email_sent,
    delete_email,
    get_smtp_accounts,
    DB_FILE,
)
from scheduler import dispatch_email_hostinger
from ui.components import render_tab_header, trigger_toast


def render_outbox_tab():
    """Render the unified Outbox with Scheduled, Sent, and Paused/Failed sub-views."""
    render_tab_header(
        "📥 Outbox & Dispatch History",
        "Monitor active queue dispatches, delivered campaigns, and auto-paused follow-up sequences."
    )

    all_emails = get_emails()

    sub_tab_scheduled, sub_tab_sent, sub_tab_paused = st.tabs([
        "🕒 Scheduled Dispatches",
        "📬 Sent History",
        "⏸️ Paused & Failed"
    ])

    # ==========================================================================
    # SUB-TAB 1: SCHEDULED DISPATCHES
    # ==========================================================================
    with sub_tab_scheduled:
        scheduled_emails = [
            e for e in all_emails
            if e.get("status") in ["Approved", "Pending", "Scheduled"]
        ]
        st.markdown(f"**Pending Messages in Queue ({len(scheduled_emails)})**")

        if not scheduled_emails:
            st.info("No emails currently scheduled. Use **✍️ Compose** or **🚀 Bulk Send** to queue outreach.")
        else:
            sched_rows = []
            for e in scheduled_emails:
                sched_rows.append({
                    "ID": e["id"],
                    "Recipient": e.get("recipient") or "",
                    "Subject": e.get("subject") or "No Subject",
                    "Scheduled Time": e.get("scheduled_time") or "Immediate",
                    "Timezone": e.get("target_timezone") or "LOCAL",
                    "Status": e.get("status") or "Approved"
                })
            df_sched = pd.DataFrame(sched_rows)

            c_stbl, c_sact = st.columns([3.8, 1.2])
            with c_stbl:
                st.dataframe(df_sched, use_container_width=True, hide_index=True)

            with c_sact:
                st.markdown("**Queue Actions:**")
                sel_sched_id = st.selectbox(
                    "Select Message",
                    [f"#{r['ID']} — {r['Recipient']}" for r in sched_rows],
                    key="sel_sched_act"
                )
                if sel_sched_id:
                    mid = int(sel_sched_id.split(" — ")[0].replace("#", ""))
                    matched_email = next((e for e in scheduled_emails if e["id"] == mid), None)

                    if matched_email:
                        col_a1, col_a2 = st.columns(2)
                        with col_a1:
                            if st.button("🚀 Send Now", key=f"s_now_{mid}", use_container_width=True):
                                with st.spinner("Dispatching via Hostinger..."):
                                    ok = dispatch_email_hostinger(matched_email)
                                    if ok:
                                        trigger_toast(f"Sent email #{mid} immediately!", icon="🚀")
                                    else:
                                        st.error(f"Dispatch failed for email #{mid}.")
                                st.rerun()

                        with col_a2:
                            if st.button("⏸️ Pause", key=f"s_pause_{mid}", use_container_width=True):
                                update_email(email_id=mid, status="Paused")
                                trigger_toast(f"Email #{mid} paused.", icon="⏸️")
                                st.rerun()

                        if st.button("❌ Cancel / Delete", key=f"s_del_{mid}", use_container_width=True):
                            delete_email(mid)
                            trigger_toast(f"Email #{mid} cancelled and deleted.", icon="🗑️")
                            st.rerun()

    # ==========================================================================
    # SUB-TAB 2: SENT HISTORY
    # ==========================================================================
    with sub_tab_sent:
        sent_emails = [e for e in all_emails if e.get("status") == "Sent"]
        st.markdown(f"**Successfully Dispatched Outreach ({len(sent_emails)})**")

        if not sent_emails:
            st.caption("No emails have been sent yet.")
        else:
            sent_rows = []
            for e in sent_emails:
                sent_rows.append({
                    "ID": e["id"],
                    "Recipient": e.get("recipient") or "",
                    "Subject": e.get("subject") or "No Subject",
                    "Dispatched At": e.get("updated_at") or e.get("created_at") or "",
                    "Opens": e.get("open_count", 0),
                    "Clicks": e.get("click_count", 0),
                    "Status": "Sent"
                })
            df_sent = pd.DataFrame(sent_rows)
            st.dataframe(df_sent, use_container_width=True, hide_index=True)

    # ==========================================================================
    # SUB-TAB 3: PAUSED & FAILED
    # ==========================================================================
    with sub_tab_paused:
        paused_failed = [
            e for e in all_emails
            if e.get("status") in ["Paused", "Failed", "Bounced", "Cancelled"]
        ]
        st.markdown(f"**Paused Follow-Ups, Bounces & Errors ({len(paused_failed)})**")
        st.caption("Emails auto-paused due to prospect replies, delivery errors, or hard bounces.")

        if not paused_failed:
            st.success("✅ Clean queue: No paused follow-ups or failed deliveries.")
        else:
            pf_rows = []
            for e in paused_failed:
                pf_rows.append({
                    "ID": e["id"],
                    "Recipient": e.get("recipient") or "",
                    "Subject": e.get("subject") or "No Subject",
                    "Reason / Error": e.get("error_message") or e.get("bounce_reason") or "Paused by prospect reply",
                    "Status": e.get("status") or "Paused"
                })
            df_pf = pd.DataFrame(pf_rows)

            c_ptbl, c_pact = st.columns([3.8, 1.2])
            with c_ptbl:
                st.dataframe(df_pf, use_container_width=True, hide_index=True)

            with c_pact:
                st.markdown("**Resolution Actions:**")
                sel_pf = st.selectbox(
                    "Select Item",
                    [f"#{r['ID']} — {r['Recipient']}" for r in pf_rows],
                    key="sel_pf_act"
                )
                if sel_pf:
                    mid = int(sel_pf.split(" — ")[0].replace("#", ""))
                    matched_pf = next((e for e in paused_failed if e["id"] == mid), None)

                    if matched_pf:
                        if st.button("▶️ Resume / Unpause", key=f"pf_resume_{mid}", use_container_width=True, type="primary"):
                            update_email(email_id=mid, status="Approved")
                            trigger_toast(f"Email #{mid} resumed and queued for dispatch.", icon="▶️")
                            st.rerun()

                        if st.button("🔄 Retry Send", key=f"pf_retry_{mid}", use_container_width=True):
                            with st.spinner("Retrying dispatch..."):
                                ok = dispatch_email_hostinger(matched_pf)
                                if ok:
                                    trigger_toast(f"Email #{mid} sent successfully!", icon="✅")
                                else:
                                    st.error("Retry failed. Check credentials/server error.")
                            st.rerun()

                        if st.button("🗑️ Delete", key=f"pf_del_{mid}", use_container_width=True):
                            delete_email(mid)
                            trigger_toast(f"Email #{mid} deleted.", icon="🗑️")
                            st.rerun()
