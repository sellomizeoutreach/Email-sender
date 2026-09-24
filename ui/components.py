"""
UI reusable components for Sellomize Reach.
Includes branded header, live metric banners, and sandboxed HTML preview renderer.
"""

import base64
import html
import os
import streamlit as st
from database import (
    get_unread_notifications_count,
    get_notifications,
    mark_all_notifications_as_read,
    get_bounced_contacts,
    get_replied_contacts,
    get_emails
)
from template_engine import sanitize_email_html
from timezone_helper import get_engine_now


def trigger_toast(msg: str, icon: str = "✅"):
    """
    Queue or display an animated non-blocking toast notification.
    Slides in smoothly at bottom-right on next rerun or current run.
    """
    st.session_state["pending_toast"] = {"msg": msg, "icon": icon}


def render_notification_bell():
    """
    Renders the global notification bell popover in the top right header.
    Icon & Badge: 🔔 Notifications (X) where X is the count of unread alerts.
    Feed: Scrollable list of recent system events with [Clear All] button.
    """
    unread_count = get_unread_notifications_count()
    bounced_leads = get_bounced_contacts()
    flagged_emails = [e for e in get_emails() if e.get("status") in ["Flagged", "Account Mismatch", "Error"]]
    replied_leads = get_replied_contacts()

    # Dynamic alert tally
    alert_count = unread_count
    if not st.session_state.get("notifications_cleared"):
        alert_count += (1 if bounced_leads else 0) + (1 if flagged_emails else 0)

    bell_label = f"🔔 {alert_count}" if alert_count > 0 else "🔔 Alerts"

    with st.popover(bell_label, help="System notifications & alerts", use_container_width=True):
        c_head, c_clear = st.columns([1.8, 1.2], vertical_alignment="center")
        with c_head:
            st.markdown("<strong style='font-size:0.95rem; color:#083731;'>System Alerts</strong>", unsafe_allow_html=True)
        with c_clear:
            if st.button("Clear All", key="btn_clear_pop_notifs", use_container_width=True):
                mark_all_notifications_as_read()
                st.session_state["notifications_cleared"] = True
                trigger_toast("All notifications cleared.", icon="🧹")
                st.rerun()

        st.markdown("<div style='height: 1px; background:#E2E8F0; margin: 6px 0 10px;'></div>", unsafe_allow_html=True)

        with st.container(height=300):
            events = []

            # Dynamic system events if not cleared
            if not st.session_state.get("notifications_cleared"):
                if bounced_leads:
                    events.append({
                        "icon": "🔴",
                        "title": f"{len(bounced_leads)} Hard Bounce(s) Detected",
                        "desc": "Quarantined in Deliverability tab to protect sender reputation.",
                        "time": "Active"
                    })
                if flagged_emails:
                    events.append({
                        "icon": "⚠️",
                        "title": f"{len(flagged_emails)} Draft(s) Flagged by Spam Shield",
                        "desc": "Action required in Review Queue before scheduled dispatch.",
                        "time": "Active"
                    })
                if replied_leads:
                    events.append({
                        "icon": "💬",
                        "title": f"{len(replied_leads)} Prospect Reply/Replies Received",
                        "desc": "Automated sequence follow-ups safely paused.",
                        "time": "Recent"
                    })

            # Database notification feed
            db_notifs = get_notifications(limit=20)
            for n in db_notifs:
                n_type = n.get("type", "system")
                icon = "💬" if n_type == "reply" else ("✅" if n_type == "campaign" else ("🔴" if n_type == "bounce" else "🔔"))
                events.append({
                    "icon": icon,
                    "title": n.get("title", "System Notification"),
                    "desc": n.get("message", ""),
                    "time": (n.get("created_at") or "")[:16]
                })

            if not events:
                st.caption("No new notifications. Everything is up to date!")
            else:
                for ev in events:
                    st.markdown(f"""
                    <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:8px 12px; margin-bottom:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-weight:700; font-size:0.84rem; color:#0F172A;">{ev['icon']} {ev['title']}</span>
                            <span style="font-size:0.72rem; color:#94A3B8;">{ev['time']}</span>
                        </div>
                        <div style="font-size:0.78rem; color:#475569; margin-top:2px; line-height:1.35;">{ev['desc']}</div>
                    </div>
                    """, unsafe_allow_html=True)


def get_logo_base64() -> str:
    """Read local brand logo and encode as base64 data URI."""
    logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo.jpg")
    if os.path.exists(logo_path):
        try:
            with open(logo_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return ""
    return ""


def render_html_preview(html_content: str, height: int = None):
    """
    Renders rich HTML email and signature previews safely without Markdown corruption or XSS vulnerabilities.
    Sanitizes with nh3 allowlist and renders in a sandboxed iframe via st.components.v1.html.
    """
    if not html_content or not str(html_content).strip():
        st.caption("No preview content available.")
        return
    content = str(html_content).strip()
    has_html_tags = any(tag in content.lower() for tag in ["<p", "<div", "<table", "<br", "<h1", "<h2", "<h3", "<h4", "<ul", "<ol", "<span"])
    if not has_html_tags:
        content = "".join(f"<p style='margin: 0 0 1em 0;'>{html.escape(p.strip())}</p>" for p in content.split("\n\n") if p.strip())

    sanitized = sanitize_email_html(content)
    h = height or 280

    iframe_document = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        margin: 0;
        padding: 12px 16px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        font-size: 14px;
        line-height: 1.55;
        color: #0F172A;
        background-color: #FFFFFF;
        box-sizing: border-box;
    }}
    a {{ color: #2563EB; text-decoration: underline; }}
    img {{ max-width: 100%; height: auto; }}
    table {{ border-collapse: collapse; width: 100%; }}
    p {{ margin: 0 0 1em 0; }}
</style>
</head>
<body>
{sanitized}
</body>
</html>"""
    st.components.v1.html(iframe_document, height=h, scrolling=True)


def render_header():
    """Render branded top header bar with live local PC time and reply notifications."""
    from datetime import datetime
    logo_b64 = get_logo_base64()
    if logo_b64:
        logo_html = f'<img src="data:image/jpeg;base64,{logo_b64}" class="sellomize-logo-img" alt="Sellomize Reach Logo" />'
    else:
        logo_html = '<div class="sellomize-logo-fallback">🚀</div>'

    unread_notifs = get_unread_notifications_count()
    if unread_notifs > 0:
        badge_lbl = f"{unread_notifs} NEW REPLY" if unread_notifs == 1 else f"{unread_notifs} NEW REPLIES"
        notif_badge_html = f'<div style="background:#FD4D1B; color:#FFFFFF; border:1.5px solid #FF8059; padding:4px 12px; border-radius:20px; font-size:0.75rem; font-weight:800; letter-spacing:0.8px; text-transform:uppercase; box-shadow:0 0 10px rgba(253,77,27,0.4); display:inline-flex; align-items:center; gap:6px;"><span>🔔</span><span>{badge_lbl}</span></div>'
    else:
        notif_badge_html = ""

    local_time_str = get_engine_now().strftime("%I:%M %p")

    header_html = (
        f'<div class="sellomize-header-container">'
        f'<div class="sellomize-header-left">'
        f'{logo_html}'
        f'<div>'
        f'<div class="sellomize-brand-title">SELLOMIZE <span class="sellomize-highlight">REACH</span></div>'
        f'<div class="sellomize-brand-subtitle">Hostinger Email Outreach Engine</div>'
        f'</div>'
        f'</div>'
        f'<div class="sellomize-header-right">'
        f'{notif_badge_html}'
        f'<div style="display:inline-flex; align-items:center; gap:6px; background:#F8FAFC; border:1px solid #CBD5E1; border-radius:20px; padding:4px 12px; font-size:0.8rem; font-weight:700; color:#083731;" title="Engine timeframe locked to UTC+5 (Asia/Karachi)">'
        f'<span>🕒</span><span>{local_time_str} UTC+5</span>'
        f'</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(header_html, unsafe_allow_html=True)


def render_tab_header(title: str, subtitle: str = ""):
    """Render a clean branded tab title bar with the Sellomize logo icon."""
    logo_b64 = get_logo_base64()
    if logo_b64:
        logo_tag = f'<img src="data:image/jpeg;base64,{logo_b64}" style="height:32px; width:32px; object-fit:cover; border-radius:8px; border:1.5px solid #FD4D1B; box-shadow:0 2px 8px rgba(253,77,27,0.25); flex-shrink:0;" alt="Sellomize" />'
    else:
        logo_tag = '<span style="font-size:1.4rem;">🚀</span>'

    subtitle_html = f"<div style='font-size:0.83rem; color:#475569; font-weight:500; margin-top:2px;'>{subtitle}</div>" if subtitle else ""

    tab_header_html = (
        f'<div style="display:flex; align-items:center; gap:12px; margin-bottom:4px; margin-top:2px;">'
        f'{logo_tag}'
        f'<div>'
        f'<div style="font-size:1.35rem; font-weight:800; color:#083731; letter-spacing:-0.3px;">{title}</div>'
        f'{subtitle_html}'
        f'</div>'
        f'</div>'
        f'<div style="height:1px; background:linear-gradient(90deg, rgba(8,55,49,0.12), transparent); margin:8px 0 16px;"></div>'
    )
    st.markdown(tab_header_html, unsafe_allow_html=True)


def render_stat_banner(analytics: dict, all_contacts: list, pending_count: int, flagged_count: int):
    """Render live statistics banner with real verifiable outreach metrics (no untrackable pixels)."""
    flagged_card_class = "stat-card-alert" if flagged_count > 0 else ""
    flagged_val_class = "stat-val-alert" if flagged_count > 0 else ""
    flagged_sub_class = "stat-sub-alert" if flagged_count > 0 else "stat-sub-clean"
    flagged_sub_text = f"{flagged_count} Action Required" if flagged_count > 0 else "All Drafts Clean"

    total_sent = int(analytics.get("total_sent", 0))
    total_bounced = int(analytics.get("total_bounced", 0))
    total_replied = int(analytics.get("total_replied", 0))
    reply_rate = float(analytics.get("reply_rate", 0.0))
    bounce_rate = float(analytics.get("bounce_rate", 0.0))

    if total_sent < 10:
        reply_val = f"{total_replied}"
        reply_sub = f"{total_replied} / {total_sent} sent" if total_sent > 0 else "0 sent"
        reply_style = "color: #083731;"

        bounced_card_class = ""
        bounced_val_class = ""
        bounced_val = f"{total_bounced}"
        bounced_sub = f"{total_bounced} / {total_sent} sent" if total_sent > 0 else "0 sent"
        delivery_health_label = "100% Clean" if total_bounced == 0 else f"{total_bounced} Bounced"
    else:
        reply_val = f"{total_replied}"
        reply_sub = f"{reply_rate:.1f}% Reply Rate"
        reply_style = "color: #059669;" if reply_rate >= 10.0 else "color: #083731;"

        is_bounce_crisis = (bounce_rate >= 5.0 and total_bounced >= 2)
        bounced_card_class = "stat-card-alert" if is_bounce_crisis else ""
        bounced_val_class = "stat-val-alert" if is_bounce_crisis else ""
        bounced_val = f"{total_bounced}"
        bounced_sub = f"{bounce_rate:.1f}% Bounce Rate"
        clean_deliv_rate = max(0.0, 100.0 - bounce_rate)
        delivery_health_label = f"{clean_deliv_rate:.1f}% Delivery"

    contacted_count = analytics.get("contacted_count", 0)

    banner_html = (
        f'<div class="stats-grid">'
        f'<div class="stat-card">'
        f'<div class="stat-header"><span class="stat-label">Saved Leads</span></div>'
        f'<div class="stat-value">{len(all_contacts)}</div>'
        f'<div class="stat-sub">{contacted_count} Contacted</div>'
        f'</div>'
        f'<div class="stat-card">'
        f'<div class="stat-header"><span class="stat-label">Pending Review</span></div>'
        f'<div class="stat-value">{pending_count}</div>'
        f'<div class="stat-sub">Awaiting Approval</div>'
        f'</div>'
        f'<div class="stat-card {flagged_card_class}">'
        f'<div class="stat-header"><span class="stat-label">Flagged Drafts</span></div>'
        f'<div class="stat-value {flagged_val_class}">{flagged_count}</div>'
        f'<div class="stat-sub {flagged_sub_class}">{flagged_sub_text}</div>'
        f'</div>'
        f'<div class="stat-card stat-card-highlight">'
        f'<div class="stat-header"><span class="stat-label">Total Sent</span></div>'
        f'<div class="stat-value stat-val-glow">{total_sent}</div>'
        f'<div class="stat-sub">Hostinger Outbound</div>'
        f'</div>'
        f'<div class="stat-card">'
        f'<div class="stat-header"><span class="stat-label">Prospect Replies</span></div>'
        f'<div class="stat-value" style="{reply_style}">{reply_val}</div>'
        f'<div class="stat-sub">{reply_sub}</div>'
        f'</div>'
        f'<div class="stat-card {bounced_card_class}">'
        f'<div class="stat-header"><span class="stat-label">Deliverability Health</span></div>'
        f'<div class="stat-value {bounced_val_class}">{delivery_health_label}</div>'
        f'<div class="stat-sub">{bounced_sub}</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(banner_html, unsafe_allow_html=True)
