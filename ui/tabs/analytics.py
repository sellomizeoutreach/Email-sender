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
    set_config,
    get_contacts,
    get_emails
)
from smtp_dispatcher import scan_all_hostinger_inbox
from tracker import is_port_in_use, start_tracking_server, get_tracking_base_url
from ui.components import render_stat_banner


def render_analytics_tab(all_contacts=None, all_emails=None):
    """Render Tab 5: Analytics & Intelligence."""
    if all_contacts is None:
        all_contacts = get_contacts()
    if all_emails is None:
        all_emails = get_emails()

    st.subheader("Outreach Analytics, Open Tracking & Bounce Report")
    st.caption("Real-time email performance telemetry, 1x1 transparent pixel open tracking, and Hostinger IMAP bounce detection.")

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

    # Section 4: Open Tracking Diagnostics & Server Status
    st.markdown("### 👁️ 1x1 Pixel Open Tracking Telemetry")
    st.caption("Sellomize Reach embeds an invisible transparent 1x1 PNG tracking pixel into HTML emails. When opened by the recipient, it logs the open event, increments open count, and updates lead status to 'Opened / Interested'.")

    server_running = is_port_in_use(8502)
    if server_running:
        st.markdown("""
        <div style="display:inline-flex; align-items:center; gap:8px; background:rgba(16,185,129,0.12); border:1px solid #10B981; border-radius:20px; padding:6px 16px; margin-bottom:12px;">
            <span style="color:#10B981; font-weight:700; font-size:0.9rem;">🟢 Tracking Active</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        col_trk_off1, col_trk_off2 = st.columns([2, 1.5])
        with col_trk_off1:
            st.markdown("""
            <div style="display:inline-flex; align-items:center; gap:8px; background:rgba(239,68,68,0.12); border:1px solid #EF4444; border-radius:20px; padding:6px 16px; margin-bottom:12px;">
                <span style="color:#EF4444; font-weight:700; font-size:0.9rem;">🔴 Tracking Offline</span>
            </div>
            """, unsafe_allow_html=True)
        with col_trk_off2:
            if st.button("Start Tracking Server", key="start_trk_srv"):
                start_tracking_server(port=8502)
                st.rerun()

    with st.expander("⚙️ Advanced Tracking Settings", expanded=False):
        st.caption("Internal telemetry service listens on local port `8502`.")
        curr_base_url = get_tracking_base_url()
        new_base_url = st.text_input(
            "Tracking Server Public / Base URL",
            value=curr_base_url,
            help="For external recipients to report opens, enter your public domain, static IP, or ngrok tunnel URL (e.g. https://track.yourdomain.com or https://abc.ngrok.app)."
        )
        if new_base_url.strip() and new_base_url.strip() != curr_base_url:
            if st.button("💾 Update Tracking Base URL", key="btn_save_trk_url"):
                set_config("tracking_base_url", new_base_url.strip())
                st.success("Tracking base URL updated!")
                st.rerun()

    # Section 5: Sent Emails with Open Tracking Status
    st.markdown("#### 📬 Sent Messages Delivery & Read Receipts")
    sent_emails = [e for e in all_emails if e.get("status") == "Sent"]
    if not sent_emails:
        st.info("No sent emails recorded yet. Approved emails will show here with their live open receipts.")
    else:
        sent_rows = []
        for se in sent_emails:
            opened_txt = f"✅ Opened ({se.get('open_count', 0)}x at {se.get('opened_at')})" if se.get("opened_at") else "⏳ Unopened"
            clicked_txt = f"🔗 Clicked ({se.get('click_count', 0)}x)" if se.get("click_count", 0) > 0 else "—"
            bounce_txt = f"⚠️ Bounced: {se.get('bounce_reason')}" if se.get("is_bounced") else "Healthy"
            sent_rows.append({
                "ID": se["id"],
                "Recipient": se.get("recipient"),
                "Subject": se.get("subject"),
                "Dispatched Via": se.get("sent_via") or "Hostinger SMTP",
                "Sent At": se.get("sent_at") or se.get("scheduled_time") or "",
                "Open Status": opened_txt,
                "Click Status": clicked_txt,
                "Delivery Health": bounce_txt
            })
        st.dataframe(pd.DataFrame(sent_rows), use_container_width=True, hide_index=True)
