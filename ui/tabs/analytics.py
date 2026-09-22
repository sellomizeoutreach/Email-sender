"""
Analytics & Delivery Health Tab for Sellomize Reach.
Outreach telemetry, KPI metrics, Hostinger IMAP inbox scanner, replies, bounced contacts, and pixel tracking diagnostics.
"""

import pandas as pd
import streamlit as st
from database import (
    get_outreach_analytics,
    get_bounced_contacts,
    get_replied_contacts,
    update_contact,
    get_config,
    set_config,
    get_contacts,
    get_emails
)
from smtp_dispatcher import scan_all_hostinger_inbox
from ui.components import render_stat_banner, render_tab_header


def render_analytics_tab(all_contacts=None, all_emails=None):
    """Render Tab 5: Analytics & Intelligence."""
    if all_contacts is None:
        all_contacts = get_contacts()
    if all_emails is None:
        all_emails = get_emails()

    render_tab_header("📊 Outreach Analytics & Performance", "Real-time email performance telemetry, confirmed prospect replies, Hostinger IMAP bounce quarantine, and dispatch ledger.")

    analytics_live = get_outreach_analytics()
    bounced_leads = get_bounced_contacts()
    replied_leads = get_replied_contacts()

    # Consolidated live stat display on dedicated overview view
    pending_count = sum(1 for e in all_emails if e.get("status") == "Pending")
    flagged_count = sum(1 for e in all_emails if e.get("status") == "Flagged")
    render_stat_banner(analytics_live, all_contacts, pending_count, flagged_count)

    st.markdown("---")

    # Section 1: Hostinger IMAP Unified Scanner (Replies & Bounces)
    col_b_hdr, col_b_scan = st.columns([3, 1.4])
    with col_b_hdr:
        st.markdown("### 📬 Hostinger IMAP Inbox Scanner (Replies & Bounces)")
        st.caption("Single-pass high-performance scan of connected Hostinger mailboxes (`imap.hostinger.com:993` SSL) for prospect replies and NDR bounce reports. Flags replies, automatically cancels future scheduled follow-ups, and quarantines bad emails.")
    with col_b_scan:
        scan_now_btn = st.button("🔍 Scan Inboxes Now", type="primary", use_container_width=True, key="scan_inbox_btn")

    if scan_now_btn:
        with st.spinner("Connecting to Hostinger IMAP and checking inboxes for replies and bounces..."):
            scan_results = scan_all_hostinger_inbox()
            b_cnt = scan_results.get("total_bounces", 0)
            r_cnt = scan_results.get("total_replies", 0)
            acc_cnt = scan_results.get("accounts_scanned", 0)
            st.success(f"✅ Hostinger IMAP Scan Complete across {acc_cnt} mailbox(es)! Found **{r_cnt}** prospect reply/replies and **{b_cnt}** bounce(s).")
            st.rerun()

    # Section 2: Detected Prospect Replies
    st.markdown("#### 💬 Detected Prospect Replies (Follow-Ups Auto-Cancelled)")
    if not replied_leads:
        st.info("ℹ️ No prospect replies recorded yet. When a contact replies to your outreach, their status will automatically update to **'Replied'** and future scheduled follow-ups will be safely cancelled.")
    else:
        st.success(f"🎉 **{len(replied_leads)}** prospect(s) have replied! Future follow-ups for these contacts are automatically cancelled in the Outbox.")
        reply_rows = []
        for r in replied_leads:
            reply_rows.append({
                "ID": r["id"],
                "Contact Name": r["name"],
                "Company": r.get("company") or "",
                "Email Address": r["email"],
                "Status": r.get("status") or "Replied",
                "Last Reply Received": r.get("last_reply_at") or r.get("last_contact_date") or "",
                "Subject Snippet": r.get("reply_subject") or "(Direct reply)",
                "Notes": r.get("notes") or ""
            })
        st.dataframe(pd.DataFrame(reply_rows), use_container_width=True, hide_index=True)

    st.markdown("---")

    # Section 3: Bounced Leads Management
    st.markdown("#### 🛡️ Deliverability Quarantine & Bounced Contacts")
    if not bounced_leads:
        st.info("🎉 Zero bounced contacts detected! Your sending domain health and list quality are clean.")
    else:
        st.warning(f"⚠️ **{len(bounced_leads)}** contact(s) have been flagged as Bounced. These leads are automatically excluded from campaigns.")

        bounced_rows = []
        for b in bounced_leads:
            bounced_rows.append({
                "ID": b["id"],
                "Lead Name": b["name"],
                "Company": b.get("company") or "",
                "Email Address": b["email"],
                "Status": b.get("status") or "Bounced",
                "Bounce Reason": b.get("bounce_reason") or "Delivery failure (Hostinger NDR)",
                "Follow-ups Sent": b.get("follow_ups_sent", 0),
                "Last Contact Date": b.get("last_contact_date") or ""
            })
        st.dataframe(pd.DataFrame(bounced_rows), use_container_width=True, hide_index=True)

        col_b_act1, col_b_act2 = st.columns([3, 1.2])
        with col_b_act1:
            st.caption("ℹ️ Hard bounces (NDR 5xx / dead domain) are automatically moved to 'Do Not Contact' status upon detection.")
        with col_b_act2:
            if st.button("🔄 Clear Bounce Status (Retry)", key="btn_clear_bounces", use_container_width=True):
                for b in bounced_leads:
                    update_contact(b["id"], status="Not Contacted", is_bounced=0, bounce_reason=None)
                st.success(f"Reset {len(bounced_leads)} leads back to 'Not Contacted'.")
                st.rerun()

    st.markdown("---")

    # Section 4: Sent Outreach Ledger & Delivery Health
    st.markdown("#### 📬 Sent Outreach Ledger & Delivery Health")
    sent_emails = [e for e in all_emails if e.get("status") == "Sent"]
    if not sent_emails:
        st.info("No sent emails recorded yet. Dispatched outreach messages will appear here.")
    else:
        sent_rows = []
        for se in sent_emails:
            bounce_txt = f"⚠️ Bounced: {se.get('bounce_reason')}" if se.get("is_bounced") else "Healthy"
            recip = se.get("recipient", "")
            has_replied = any(r.get("email", "").lower() == recip.lower() for r in replied_leads)
            reply_status_txt = "✅ Confirmed Reply" if has_replied else "Pending Response"
            step_txt = f"Touch {se.get('sequence_step', 1)}"

            sent_rows.append({
                "ID": f"#{se['id']}",
                "Recipient": recip,
                "Sequence Step": step_txt,
                "Subject": se.get("subject"),
                "Dispatched Via": se.get("sent_via") or "Hostinger SMTP",
                "Sent Timestamp": se.get("sent_at") or se.get("scheduled_time") or "",
                "Prospect Response": reply_status_txt,
                "Delivery Health": bounce_txt
            })
        st.dataframe(pd.DataFrame(sent_rows), use_container_width=True, hide_index=True)

