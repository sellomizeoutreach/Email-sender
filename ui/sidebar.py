"""
Sidebar infrastructure and operational monitor for Sellomize Reach.
Provides real-time notifications, dispatch status, quick inbox sync, and fleet capacity overview.
"""

import streamlit as st
from database import (
    get_all_configs,
    get_smtp_accounts,
    get_effective_daily_limit,
    is_within_sending_window,
    get_unread_notifications_count,
    get_notifications,
    mark_all_notifications_as_read,
    clear_all_notifications
)
from smtp_dispatcher import scan_all_hostinger_inbox, scan_all_hostinger_bounces


def render_sidebar():
    """Render the streamlined executive sidebar in st.sidebar."""
    with st.sidebar:
        st.markdown("""
        <div style="padding:10px 4px 14px; border-bottom:1.5px solid rgba(8,55,49,0.15); margin-bottom:14px;">
            <div style="font-weight:900; font-size:1.15rem; color:#083731; letter-spacing:0.6px; line-height:1.1;">SELLOMIZE REACH</div>
            <div style="font-size:0.75rem; color:#64748B; font-weight:700; letter-spacing:0.4px; margin-top:2px;">COLD OUTREACH AGENCY ENGINE</div>
        </div>
        """, unsafe_allow_html=True)

        current_configs = get_all_configs()

        # ======================================================================
        # 1. NOTIFICATIONS & INBOX ALERTS (DEDUPLICATED & CLEAN)
        # ======================================================================
        unread_count = get_unread_notifications_count()
        notif_header = f"🔔 Notifications ({unread_count} Unread)" if unread_count > 0 else "🔔 Notifications & Activity"
        with st.expander(notif_header, expanded=(unread_count > 0)):
            recent_notifs = get_notifications(limit=30)
            # Deduplicate by (type, contact_email, title)
            unique_notifs = []
            seen_keys = set()
            for n in recent_notifs:
                key = (n.get("type"), (n.get("contact_email") or "").lower().strip(), n.get("title"))
                if key not in seen_keys:
                    seen_keys.add(key)
                    unique_notifs.append(n)

            if not unique_notifs:
                st.caption("No notifications yet. Prospect replies and system alerts will appear here.")
            else:
                col_n1, col_n2 = st.columns(2)
                with col_n1:
                    if st.button("✓ Mark Read", key="sb_btn_mark_read", use_container_width=True):
                        mark_all_notifications_as_read()
                        st.rerun()
                with col_n2:
                    if st.button("🗑️ Clear All", key="sb_btn_clear_notifs", use_container_width=True):
                        clear_all_notifications()
                        st.rerun()

                for notif in unique_notifs[:10]:
                    is_unread = not notif.get("is_read")
                    dot = "🔴 " if is_unread else "⚪ "
                    border_color = "#FD4D1B" if is_unread else "rgba(8,55,49,0.14)"
                    bg_color = "rgba(253,77,27,0.06)" if is_unread else "rgba(8,55,49,0.02)"
                    st.markdown(f"""
                    <div style="background:{bg_color}; border:1px solid {border_color}; border-radius:6px; padding:7px 10px; margin-bottom:6px;">
                        <div style="font-weight:700; font-size:0.82rem; color:#083731;">{dot}{notif['title']}</div>
                        <div style="font-size:0.75rem; color:#475569; margin:2px 0;">{notif['message']}</div>
                        <div style="font-size:0.68rem; color:#94A3B8; text-align:right;">{notif['created_at'][:16]}</div>
                    </div>
                    """, unsafe_allow_html=True)

        # ======================================================================
        # 2. DISPATCH ENGINE & SCHEDULE STATUS
        # ======================================================================
        is_open, window_msg = is_within_sending_window()
        engine_method = current_configs.get("dispatch_method", "hostinger_smtp")
        engine_label = "Hostinger SMTP" if engine_method == "hostinger_smtp" else "Outlook Local"

        smtp_accounts = get_smtp_accounts(active_only=False)
        active_accounts = [acc for acc in smtp_accounts if acc.get("is_active")]
        total_capacity = sum(get_effective_daily_limit(acc) for acc in active_accounts)
        total_sent_today = sum(acc.get("sent_today", 0) for acc in active_accounts)

        status_badge = '<span style="background:rgba(16,185,129,0.15); color:#059669; border:1px solid #10B981; padding:3px 10px; border-radius:12px; font-weight:700; font-size:0.72rem;">WINDOW OPEN</span>' if is_open else '<span style="background:rgba(239,68,68,0.15); color:#DC2626; border:1px solid #EF4444; padding:3px 10px; border-radius:12px; font-weight:700; font-size:0.72rem;">WINDOW PAUSED</span>'

        st.markdown(f"""
        <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:10px; padding:12px 14px; margin:10px 0 14px; box-shadow:0 1px 4px rgba(8,55,49,0.04);">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-size:0.75rem; color:#64748B; font-weight:800; text-transform:uppercase; letter-spacing:0.4px;">Dispatch Engine</span>
                <div>{status_badge}</div>
            </div>
            <div style="font-size:0.85rem; color:#083731; font-weight:700;">{engine_label}</div>
            <div style="font-size:0.78rem; color:#64748B; margin:4px 0 8px;">{window_msg}</div>
            <div style="border-top:1px solid #F1F5F9; padding-top:6px; display:flex; justify-content:space-between; font-size:0.78rem;">
                <span style="color:#64748B;">Fleet Cap Today:</span>
                <strong style="color:#083731;">{total_sent_today} / {total_capacity} sent</strong>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.78rem; margin-top:2px;">
                <span style="color:#64748B;">Active Mailboxes:</span>
                <strong style="color:#083731;">{len(active_accounts)} of {len(smtp_accounts)}</strong>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ======================================================================
        # 3. QUICK ACTIONS & SYNC
        # ======================================================================
        st.markdown("##### Quick Actions")
        if st.button("📬 Scan Hostinger Inbox Now", use_container_width=True, key="sb_quick_scan_inbox"):
            with st.spinner("Checking Hostinger inbox via IMAP..."):
                try:
                    reps = scan_all_hostinger_inbox()
                    bncs = scan_all_hostinger_bounces()
                    st.success(f"Scanned: {reps} reply(ies), {bncs} bounce(s).")
                except Exception as e:
                    st.error(f"Scan error: {e}")
            st.rerun()

        st.markdown("""
        <div style="background:rgba(8,55,49,0.04); border:1px dashed rgba(8,55,49,0.18); border-radius:8px; padding:10px 12px; margin-top:14px;">
            <div style="font-weight:700; color:#083731; font-size:0.78rem;">⚙️ Rules &amp; Settings</div>
            <div style="font-size:0.74rem; color:#64748B; margin-top:3px; line-height:1.35;">
                Configure sending windows, anti-spam delays, mailbox fleet, signatures, and team network access in the <strong>⚙️ Rules &amp; Settings</strong> tab.
            </div>
        </div>
        """, unsafe_allow_html=True)
