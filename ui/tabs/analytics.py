"""
Analytics & Intelligence Tab for Sellomize Reach.
Reorganized into three dedicated sub-tabs:
1. 📈 Overview & Telemetry (Top KPI metric cards, funnel chart, conversion health, sent ledger)
2. 💬 Prospect Replies (Hostinger IMAP scan, sentiment badges, inline response preview)
3. 🚫 Deliverability & Bounces (Dedicated bounce table, MX verification, bulk unbounce & removal)
"""

import pandas as pd
import streamlit as st
from database import (
    get_outreach_analytics,
    get_bounced_contacts,
    get_replied_contacts,
    update_contact,
    bulk_delete_contacts,
    get_contacts,
    get_emails
)
from smtp_dispatcher import scan_all_hostinger_inbox
from mx_checker import verify_email_domain_mx, get_cached_domain_mx
from ui.components import render_tab_header, render_html_preview, trigger_toast


def classify_reply_sentiment(text: str) -> str:
    """Classifies prospect reply snippet into Positive, Negative, or Neutral sentiment."""
    t = (text or "").lower()
    positive_words = [
        "interested", "call", "zoom", "schedule", "demo", "yes", "sounds good",
        "pricing", "more info", "send over", "let's talk", "let's chat", "sure", "thanks", "forward"
    ]
    negative_words = [
        "unsubscribe", "remove", "stop", "not interested", "take me off",
        "do not email", "spam", "cease", "wrong person", "no thanks"
    ]

    if any(w in t for w in negative_words):
        return "🔴 Unsubscribe / Negative"
    elif any(w in t for w in positive_words):
        return "🟢 Positive / Interested"
    return "⚪ Neutral / Info"


@st.dialog("✏️ Fix Email & Un-Quarantine Lead")
def render_fix_bounced_email_dialog(contact: dict):
    """
    Actionable bounce recovery modal.
    Enables user to correct typo, verify MX domain records, and clear bounce quarantine.
    """
    c_id = contact["id"]
    c_name = contact.get("name") or "Lead"
    c_email = contact.get("email") or ""
    c_reason = contact.get("bounce_reason") or "Delivery failure (Hostinger NDR)"

    st.markdown(f"""
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; border-bottom:1px solid rgba(8,55,49,0.15); padding-bottom:8px;">
        <span style="font-weight:800; font-size:1.05rem; color:#083731;">Fix Lead #{c_id:04d} — {c_name}</span>
        <span style="background:rgba(220,38,38,0.1); color:#DC2626; border:1px solid #DC2626; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">QUARANTINED</span>
    </div>
    """, unsafe_allow_html=True)

    st.caption("Correct misspelled domain or address typos (e.g. `gmal.com` → `gmail.com`). Pre-flight MX verification will automatically check domain deliverability before un-quarantining.")

    st.markdown(f"""
    <div style="background:#FEF2F2; border:1px solid #FECACA; border-radius:8px; padding:8px 12px; margin-bottom:12px; font-size:0.83rem; color:#991B1B;">
        <strong>⚠️ Recorded Bounce Reason:</strong> {c_reason}
    </div>
    """, unsafe_allow_html=True)

    new_email = st.text_input("Corrected Email Address *", value=c_email, key=f"fix_email_inp_{c_id}")

    # Live Pre-Flight MX Check on the entered email
    if new_email.strip():
        if "@" in new_email and "." in new_email.split("@")[-1]:
            is_mx_valid, mx_note = verify_email_domain_mx(new_email.strip())
            if is_mx_valid:
                st.markdown(f"""
                <div style="background:#F0FDF4; border:1px solid #BBF7D0; border-radius:8px; padding:6px 12px; margin-bottom:12px; font-size:0.8rem; color:#166534;">
                    🟢 <strong>Domain MX Valid:</strong> {mx_note}
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background:#FEF2F2; border:1px solid #FECACA; border-radius:8px; padding:6px 12px; margin-bottom:12px; font-size:0.8rem; color:#991B1B;">
                    🔴 <strong>Dead Domain (MX Check Failed):</strong> {mx_note}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("Enter a complete email address to run pre-flight MX verification.")

    col_save, col_cancel = st.columns([2, 1])
    with col_save:
        if st.button("💾 Save & Un-Quarantine Lead", type="primary", use_container_width=True, key=f"btn_save_fixed_email_{c_id}"):
            clean_email = new_email.strip()
            if not clean_email or "@" not in clean_email or "." not in clean_email.split("@")[-1]:
                st.error("Please enter a valid email address.")
            else:
                update_contact(
                    c_id,
                    email=clean_email,
                    is_bounced=0,
                    bounce_reason=None,
                    status="Not Contacted"
                )
                st.session_state["fix_bounce_contact_id"] = None
                trigger_toast(f"Email corrected to '{clean_email}' and lead un-quarantined!", icon="✅")
                st.rerun()

    with col_cancel:
        if st.button("Cancel", use_container_width=True, key=f"btn_cancel_fixed_email_{c_id}"):
            st.session_state["fix_bounce_contact_id"] = None
            st.rerun()


def render_analytics_tab(all_contacts=None, all_emails=None):
    """Render Tab 5: Analytics & Inbox Intelligence with Three-Tab Sub-Layout."""
    if all_contacts is None:
        all_contacts = get_contacts()
    if all_emails is None:
        all_emails = get_emails()

    render_tab_header(
        "📊 Analytics & Inbox Intelligence",
        "Campaign performance telemetry, prospect reply tracking with sentiment badges, and deliverability bounce management."
    )

    analytics_live = get_outreach_analytics()
    bounced_leads = get_bounced_contacts()
    replied_leads = get_replied_contacts()

    if "fix_bounce_contact_id" not in st.session_state:
        st.session_state["fix_bounce_contact_id"] = None

    if st.session_state.get("fix_bounce_contact_id"):
        contact_to_fix = next((b for b in bounced_leads if b["id"] == st.session_state["fix_bounce_contact_id"]), None)
        if contact_to_fix:
            render_fix_bounced_email_dialog(contact_to_fix)
        else:
            st.session_state["fix_bounce_contact_id"] = None

    tab_overview, tab_replies, tab_bounces = st.tabs([
        "📈 Overview & Telemetry",
        f"💬 Prospect Replies ({len(replied_leads)})",
        f"🚫 Deliverability & Bounces ({len(bounced_leads)})"
    ])

    # ==============================================================================
    # SUB-TAB 1: 📈 OVERVIEW & TELEMETRY
    # ==============================================================================
    with tab_overview:
        total_dispatched = analytics_live.get("total_sent", 0)
        reply_rate = analytics_live.get("reply_rate", 0.0)
        bounce_rate = analytics_live.get("bounce_rate", 0.0)
        active_seq_leads = sum(
            1 for c in all_contacts
            if (c.get("follow_ups_sent", 0) > 0 or c.get("status") in ["Contacted", "Follow-Up Sent"])
            and not c.get("is_bounced")
            and c.get("status") != "Replied"
        )

        # Top KPI Metric Cards
        col_k1, col_k2, col_k3, col_k4 = st.columns(4)
        with col_k1:
            st.metric("Total Dispatched", f"{total_dispatched}")
        with col_k2:
            st.metric("Reply Rate", f"{reply_rate:.1f}%")
        with col_k3:
            st.metric("Bounce Rate", f"{bounce_rate:.1f}%")
        with col_k4:
            st.metric("Leads in Sequences", f"{active_seq_leads}")

        st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

        # Activity Chart & Conversion Health Funnel
        col_fun_chart, col_fun_bars = st.columns([1.6, 1.4])
        with col_fun_chart:
            st.markdown("##### 🚀 Outreach Funnel")
            funnel_df = pd.DataFrame({
                "Funnel Stage": ["Total CRM Leads", "Outreach Sent", "Delivered / Clean", "Replied Leads"],
                "Count": [
                    len(all_contacts),
                    total_dispatched,
                    max(0, total_dispatched - len(bounced_leads)),
                    len(replied_leads)
                ]
            })
            st.bar_chart(funnel_df.set_index("Funnel Stage"))

        with col_fun_bars:
            st.markdown("##### 📊 Health Telemetry")
            coverage_pct = (total_dispatched / max(len(all_contacts), 1)) * 100
            clean_delivery_pct = max(0.0, 100.0 - bounce_rate)

            st.markdown(f"""
            <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:10px; padding:16px;">
                <div style="margin-bottom:12px;">
                    <div style="display:flex; justify-content:space-between; font-size:0.82rem; font-weight:700; color:#334155;">
                        <span>CRM Outreach Coverage</span>
                        <span>{min(100.0, coverage_pct):.1f}%</span>
                    </div>
                    <div style="background:#F1F5F9; border-radius:4px; height:8px; overflow:hidden; margin-top:4px;">
                        <div style="background:#083731; width:{min(100.0, coverage_pct):.1f}%; height:100%;"></div>
                    </div>
                </div>
                <div style="margin-bottom:12px;">
                    <div style="display:flex; justify-content:space-between; font-size:0.82rem; font-weight:700; color:#334155;">
                        <span>Clean Delivery Rate</span>
                        <span>{clean_delivery_pct:.1f}%</span>
                    </div>
                    <div style="background:#F1F5F9; border-radius:4px; height:8px; overflow:hidden; margin-top:4px;">
                        <div style="background:#10B981; width:{clean_delivery_pct:.1f}%; height:100%;"></div>
                    </div>
                </div>
                <div>
                    <div style="display:flex; justify-content:space-between; font-size:0.82rem; font-weight:700; color:#334155;">
                        <span>Engagement (Confirmed Replies)</span>
                        <span>{reply_rate:.1f}%</span>
                    </div>
                    <div style="background:#F1F5F9; border-radius:4px; height:8px; overflow:hidden; margin-top:4px;">
                        <div style="background:#2563EB; width:{min(100.0, reply_rate):.1f}%; height:100%;"></div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

        # Sent Outreach Ledger
        with st.expander("📬 Sent Outreach Ledger & Dispatch History", expanded=False):
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

    # ==============================================================================
    # SUB-TAB 2: 💬 PROSPECT REPLIES
    # ==============================================================================
    with tab_replies:
        col_scan_hdr, col_scan_btn = st.columns([3, 1.4])
        with col_scan_hdr:
            st.markdown("##### 📬 Connected Mailbox Scanner")
            st.caption("Scan Hostinger inboxes (`imap.hostinger.com:993`) for new prospect responses. Detects sentiment and auto-cancels scheduled follow-ups.")
        with col_scan_btn:
            scan_now_btn = st.button("🔍 Scan Inboxes Now", type="primary", use_container_width=True, key="scan_inbox_btn")

        if scan_now_btn:
            with st.spinner("Scanning Hostinger IMAP inboxes for prospect replies and bounces..."):
                scan_results = scan_all_hostinger_inbox()
                b_cnt = scan_results.get("total_bounces", 0)
                r_cnt = scan_results.get("total_replies", 0)
                acc_cnt = scan_results.get("accounts_scanned", 0)
                st.success(f"✅ Hostinger scan complete ({acc_cnt} mailboxes): Found **{r_cnt}** new reply/replies and **{b_cnt}** bounce(s).")
                st.rerun()

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

        if not replied_leads:
            st.info("ℹ️ No prospect replies detected yet. When prospects reply, they are logged here with sentiment classifications and their automated sequence follow-ups are safely paused.")
        else:
            reply_table_rows = []
            for r in replied_leads:
                combined_txt = f"{r.get('reply_subject', '')} {r.get('notes', '')}"
                sentiment_label = classify_reply_sentiment(combined_txt)
                reply_table_rows.append({
                    "ID": r["id"],
                    "Prospect Name": r["name"],
                    "Company": r.get("company") or "—",
                    "Email": r["email"],
                    "Sentiment": sentiment_label,
                    "Last Reply Date": r.get("last_reply_at") or r.get("last_contact_date") or "—",
                    "Subject / Snippet": r.get("reply_subject") or "(Direct reply)"
                })

            st.dataframe(pd.DataFrame(reply_table_rows), use_container_width=True, hide_index=True)

            # Inline Response & Follow-Up Preview Drawer
            st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
            with st.expander("💬 Inspect Prospect Thread & Compose Follow-Up", expanded=False):
                replied_options = {r["id"]: f"{r['name']} ({r.get('company') or 'No Company'} — {r['email']})" for r in replied_leads}
                sel_replied_id = st.selectbox(
                    "Select Prospect Thread",
                    options=list(replied_options.keys()),
                    format_func=lambda rid: replied_options[rid],
                    key="sel_replied_thread"
                )
                selected_lead = next((r for r in replied_leads if r["id"] == sel_replied_id), None)
                if selected_lead:
                    sent_sentiment = classify_reply_sentiment(f"{selected_lead.get('reply_subject', '')} {selected_lead.get('notes', '')}")
                    col_rp1, col_rp2 = st.columns([2, 1])
                    with col_rp1:
                        st.markdown(f"**Lead:** `{selected_lead['name']}` ({selected_lead['email']})")
                        st.markdown(f"**Sentiment Classification:** `{sent_sentiment}`")
                        if selected_lead.get("notes"):
                            st.info(f"**Reply Notes:** {selected_lead['notes']}")
                    with col_rp2:
                        st.markdown(f"**CRM Status:** `{selected_lead.get('status', 'Replied')}`")
                        st.markdown(f"**Follow-Ups Sent:** `{selected_lead.get('follow_ups_sent', 0)}`")

                    # Previous sent email history to this lead
                    lead_sent_emails = [e for e in all_emails if e.get("recipient", "").lower() == selected_lead["email"].lower() and e.get("status") == "Sent"]
                    if lead_sent_emails:
                        st.markdown("###### Previous Sent Email:")
                        last_sent = lead_sent_emails[-1]
                        st.caption(f"**Subject:** {last_sent.get('subject')} | **Sent At:** {last_sent.get('sent_at', '')}")
                        render_html_preview(last_sent.get("email_html", ""), height=160)

    # ==============================================================================
    # SUB-TAB 3: 🚫 DELIVERABILITY & BOUNCES
    # ==============================================================================
    with tab_bounces:
        if not bounced_leads:
            st.success("🎉 Zero bounced contacts detected! Your sending domain health and outreach lists are completely clean.")
        else:
            st.warning(f"⚠️ **{len(bounced_leads)}** contact(s) flagged as Bounced. These leads are quarantined and automatically excluded from campaigns.")

            # Render Actionable Bounce Cards
            for b in bounced_leads:
                b_id = b["id"]
                b_email = b["email"]
                cached = get_cached_domain_mx(b_email)
                if cached is not None:
                    is_mx_val, mx_note = cached
                    mx_badge = '<span style="background:rgba(16,185,129,0.1); color:#059669; border:1px solid #059669; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">🟢 MX ACTIVE</span>' if is_mx_val else f'<span style="background:rgba(220,38,38,0.1); color:#DC2626; border:1px solid #DC2626; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">🔴 DEAD DOMAIN</span>'
                else:
                    mx_badge = '<span style="background:#F1F5F9; color:#64748B; border:1px solid #CBD5E1; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">⚪ UNCHECKED</span>'

                b_reason = b.get("bounce_reason") or "Delivery failure (Hostinger NDR)"

                st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid rgba(220,38,38,0.22); border-radius:10px; padding:12px 16px; margin:8px 0 6px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
                        <div>
                            <strong style="color:#083731; font-size:0.95rem;">{b['name']}</strong>
                            <span style="color:#64748B; font-size:0.82rem; margin-left:6px;">• {b.get('company') or 'No Company'} (ID #{b_id:04d})</span>
                            <div style="margin-top:2px;">
                                <code style="font-size:0.85rem; color:#991B1B; font-weight:700;">{b_email}</code>
                            </div>
                        </div>
                        <div>{mx_badge}</div>
                    </div>
                    <div style="font-size:0.78rem; color:#64748B; margin-top:4px;">
                        <strong>Reason:</strong> {b_reason}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                col_b_fix, col_b_unb, col_b_del = st.columns([1.2, 1.2, 1.2])
                with col_b_fix:
                    if st.button("✏️ Fix Email", key=f"btn_fix_bounce_{b_id}", use_container_width=True, help="Edit typo in email, run live MX verification, and clear quarantine"):
                        st.session_state["fix_bounce_contact_id"] = b_id
                        st.rerun()
                with col_b_unb:
                    if st.button("🔄 Un-Quarantine", key=f"btn_unq_bounce_{b_id}", use_container_width=True, help="Reset lead to Not Contacted without changing email address"):
                        update_contact(b_id, is_bounced=0, bounce_reason=None, status="Not Contacted")
                        trigger_toast(f"Lead '{b['name']}' un-quarantined!", icon="✅")
                        st.rerun()
                with col_b_del:
                    if st.session_state.get(f"confirm_del_bounce_{b_id}"):
                        if st.button("Confirm Delete", key=f"btn_conf_del_b_{b_id}", use_container_width=True):
                            bulk_delete_contacts([b_id])
                            st.session_state[f"confirm_del_bounce_{b_id}"] = False
                            trigger_toast("Lead deleted.", icon="🗑️")
                            st.rerun()
                    else:
                        if st.button("🗑️ Delete", key=f"btn_del_bounce_{b_id}", use_container_width=True):
                            st.session_state[f"confirm_del_bounce_{b_id}"] = True
                            st.rerun()

            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            with st.expander("📊 View All Bounced Leads as Data Grid", expanded=False):
                bounced_data = []
                for b in bounced_leads:
                    b_email = b["email"]
                    cached = get_cached_domain_mx(b_email)
                    mx_badge_txt = "🟢 MX Active" if (cached and cached[0]) else ("🔴 Dead Domain" if cached else "⚪ Unchecked")
                    bounced_data.append({
                        "ID": b["id"],
                        "Lead Name": b["name"],
                        "Company": b.get("company") or "—",
                        "Email Address": b["email"],
                        "Domain MX Status": mx_badge_txt,
                        "Bounce Reason": b.get("bounce_reason") or "Delivery failure (Hostinger NDR)",
                        "Last Contact": b.get("last_contact_date") or "—"
                    })
                st.dataframe(pd.DataFrame(bounced_data), use_container_width=True, hide_index=True)

            col_b_act1, col_b_act2, col_b_act3 = st.columns([1.5, 1.3, 1.2])
            with col_b_act1:
                st.caption(f"Quarantined count: **{len(bounced_leads)}** leads")
            with col_b_act2:
                if st.button("🔄 Unbounce All (Reset to New)", key="btn_unbounce_all", use_container_width=True, help="Clear bounce flags and reset leads to Not Contacted"):
                    for b in bounced_leads:
                        update_contact(b["id"], status="Not Contacted", is_bounced=0, bounce_reason=None)
                    trigger_toast(f"Reset {len(bounced_leads)} leads back to 'Not Contacted'.", icon="🔄")
                    st.rerun()
            with col_b_act3:
                if st.session_state.get("confirm_del_bounced"):
                    if st.button("Confirm Remove", type="primary", key="btn_conf_del_bounced", use_container_width=True):
                        b_ids = [b["id"] for b in bounced_leads]
                        deleted_cnt = bulk_delete_contacts(b_ids)
                        st.session_state["confirm_del_bounced"] = False
                        trigger_toast(f"Permanently removed {deleted_cnt} bounced lead(s) from CRM.", icon="🗑️")
                        st.rerun()
                else:
                    if st.button("🗑️ Delete Bounced Leads", key="btn_del_bounced", use_container_width=True, help="Permanently delete all bounced contacts from CRM"):
                        st.session_state["confirm_del_bounced"] = True
                        st.rerun()
