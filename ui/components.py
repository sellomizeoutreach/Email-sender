"""
UI reusable components for Sellomize Reach.
Includes branded header, live metric banners, and sandboxed HTML preview renderer.
"""

import base64
import html
import os
import streamlit as st
from database import get_unread_notifications_count
from template_engine import sanitize_email_html


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
    """Render branded top header bar with live reply notifications."""
    logo_b64 = get_logo_base64()
    if logo_b64:
        logo_html = f'<img src="data:image/jpeg;base64,{logo_b64}" class="sellomize-logo-img" alt="Sellomize Reach Logo" />'
    else:
        logo_html = '<div class="sellomize-logo-fallback">🚀</div>'

    unread_notifs = get_unread_notifications_count()
    if unread_notifs > 0:
        badge_lbl = f"{unread_notifs} NEW REPLY" if unread_notifs == 1 else f"{unread_notifs} NEW REPLIES"
        notif_badge_html = f"""
        <div style="background:#FD4D1B; color:#FFFFFF; border:1.5px solid #FF8059; padding:5px 13px; border-radius:20px; font-size:0.75rem; font-weight:800; letter-spacing:0.8px; text-transform:uppercase; box-shadow:0 0 12px rgba(253,77,27,0.5); display:inline-flex; align-items:center; gap:6px;">
            <span>🔔</span>
            <span>{badge_lbl}</span>
        </div>
        """
    else:
        notif_badge_html = ""

    st.markdown(f"""
<div class="sellomize-header-container">
    <div class="sellomize-header-left">
        {logo_html}
        <div>
            <div class="sellomize-brand-title">SELLOMIZE <span class="sellomize-highlight">REACH</span></div>
            <div class="sellomize-brand-subtitle">Desktop Outbound Engine & Multi-Account Outreach Suite</div>
        </div>
    </div>
    <div class="sellomize-header-right">
        {notif_badge_html}
        <div class="system-status-pill">
            <span class="status-indicator-dot"></span>
            <span>ENGINE ONLINE</span>
        </div>
        <div class="sellomize-header-badge">
            <span>Agency Edition</span>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


def render_tab_header(title: str, subtitle: str = ""):
    """Render a clean branded tab title bar with the Sellomize logo icon."""
    logo_b64 = get_logo_base64()
    if logo_b64:
        logo_tag = f'<img src="data:image/jpeg;base64,{logo_b64}" style="height:32px; width:32px; object-fit:cover; border-radius:8px; border:1.5px solid #FD4D1B; box-shadow:0 2px 8px rgba(253,77,27,0.25); flex-shrink:0;" alt="Sellomize" />'
    else:
        logo_tag = '<span style="font-size:1.4rem;">🚀</span>'

    subtitle_html = f"<div style='font-size:0.83rem; color:#64748B; font-weight:500; margin-top:2px;'>{subtitle}</div>" if subtitle else ""

    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:4px; margin-top:2px;">
        {logo_tag}
        <div>
            <div style="font-size:1.32rem; font-weight:800; color:#083731; letter-spacing:-0.3px; line-height:1.2;">{title}</div>
            {subtitle_html}
        </div>
    </div>
    <div style="height:1px; background:linear-gradient(90deg, rgba(8,55,49,0.12), transparent); margin:8px 0 16px;"></div>
    """, unsafe_allow_html=True)


def render_stat_banner(analytics: dict, all_contacts: list, pending_count: int, flagged_count: int):
    """Render live statistics banner and outreach metrics grid with small-sample discipline."""
    flagged_card_class = "stat-card-alert" if flagged_count > 0 else ""
    flagged_val_class = "stat-val-alert" if flagged_count > 0 else ""
    flagged_sub_class = "stat-sub-alert" if flagged_count > 0 else "stat-sub-clean"
    flagged_sub_text = f"{flagged_count} Action Required" if flagged_count > 0 else "All Drafts Clean"

    total_sent = int(analytics.get("total_sent", 0))
    total_bounced = int(analytics.get("total_bounced", 0))
    total_opened = int(analytics.get("total_opened", 0))
    total_replied = int(analytics.get("total_replied", 0))
    open_rate = float(analytics.get("open_rate", 0.0))
    reply_rate = float(analytics.get("reply_rate", 0.0))
    bounce_rate = float(analytics.get("bounce_rate", 0.0))

    # Small-sample statistical discipline (< 20 sends is too small for meaningful percentages)
    if total_sent < 20:
        open_val = f"{total_opened} / {total_sent}" if total_sent > 0 else "0"
        open_sub = "Need 20+ sends for rate" if total_sent > 0 else "No sends yet"
        open_style = "color: #64748B;"

        reply_val = f"{total_replied}"
        reply_sub = f"{total_replied} / {total_sent} sent" if total_sent > 0 else "0 sent"
        reply_style = "color: #083731;"

        bounced_card_class = ""
        bounced_val_class = ""
        bounced_val = f"{total_bounced}"
        bounced_sub = f"{total_bounced} / {total_sent} sent" if total_sent > 0 else "0 sent"
    else:
        open_val = f"{open_rate:.1f}%"
        open_sub = f"{total_opened} Opened Pixel"
        if open_rate == 0.0:
            open_style = "color: #64748B;"
        elif open_rate >= 15.0:
            open_style = "color: #059669;"
        else:
            open_style = "color: #083731;"

        reply_val = f"{total_replied}"
        reply_sub = f"{reply_rate:.1f}% Reply Rate"
        reply_style = "color: #083731;"

        is_bounce_crisis = (bounce_rate >= 5.0 and total_bounced >= 2)
        bounced_card_class = "stat-card-alert" if is_bounce_crisis else ""
        bounced_val_class = "stat-val-alert" if is_bounce_crisis else ""
        bounced_val = f"{total_bounced}"
        bounced_sub = f"{bounce_rate:.1f}% Bounce Rate"

    st.markdown(f"""
<div class="stats-grid">
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Saved Leads</span>
        </div>
        <div class="stat-value">{len(all_contacts)}</div>
        <div class="stat-sub">{analytics.get("contacted_count", 0)} Contacted</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Pending Review</span>
        </div>
        <div class="stat-value">{pending_count}</div>
        <div class="stat-sub">Awaiting Approval</div>
    </div>
    <div class="stat-card {flagged_card_class}">
        <div class="stat-header">
            <span class="stat-label">Flagged Drafts</span>
        </div>
        <div class="stat-value {flagged_val_class}">{flagged_count}</div>
        <div class="stat-sub {flagged_sub_class}">{flagged_sub_text}</div>
    </div>
    <div class="stat-card stat-card-highlight">
        <div class="stat-header">
            <span class="stat-label">Total Sent</span>
        </div>
        <div class="stat-value stat-val-glow">{total_sent}</div>
        <div class="stat-sub">Hostinger & Outlook</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Open Rate</span>
        </div>
        <div class="stat-value" style="{open_style}">{open_val}</div>
        <div class="stat-sub">{open_sub}</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Replies</span>
        </div>
        <div class="stat-value" style="{reply_style}">{reply_val}</div>
        <div class="stat-sub">{reply_sub}</div>
    </div>
    <div class="stat-card {bounced_card_class}">
        <div class="stat-header">
            <span class="stat-label">Bounces</span>
        </div>
        <div class="stat-value {bounced_val_class}">{bounced_val}</div>
        <div class="stat-sub">{bounced_sub}</div>
    </div>
</div>
""", unsafe_allow_html=True)

