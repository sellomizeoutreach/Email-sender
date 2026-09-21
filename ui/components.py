"""
UI reusable components for Sellomize Reach.
Includes branded header, live metric banners, and sandboxed HTML preview renderer.
"""

import base64
import html
import os
import streamlit as st
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
    """Render branded top header bar."""
    logo_b64 = get_logo_base64()
    if logo_b64:
        logo_html = f'<img src="data:image/jpeg;base64,{logo_b64}" class="sellomize-logo-img" alt="Sellomize Reach Logo" />'
    else:
        logo_html = '<div class="sellomize-logo-fallback">🚀</div>'

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


def render_stat_banner(analytics: dict, all_contacts: list, pending_count: int, flagged_count: int):
    """Render live statistics banner and outreach metrics grid."""
    flagged_card_class = "stat-card-alert" if flagged_count > 0 else ""
    flagged_val_class = "stat-val-alert" if flagged_count > 0 else ""
    flagged_sub_class = "stat-sub-alert" if flagged_count > 0 else "stat-sub-clean"
    flagged_sub_text = f"🚨 {flagged_count} Action Required" if flagged_count > 0 else "✓ All Drafts Clean"
    flagged_icon = "🚨" if flagged_count > 0 else "🛡️"

    bounced_card_class = "stat-card-alert" if analytics.get("total_bounced", 0) > 0 else ""
    bounced_val_class = "stat-val-alert" if analytics.get("total_bounced", 0) > 0 else ""

    st.markdown(f"""
<div class="stats-grid">
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Saved Leads</span>
            <span class="stat-icon-badge">👥</span>
        </div>
        <div class="stat-value">{len(all_contacts)}</div>
        <div class="stat-sub">{analytics.get("contacted_count", 0)} Contacted</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Pending Review</span>
            <span class="stat-icon-badge">⏳</span>
        </div>
        <div class="stat-value">{pending_count}</div>
        <div class="stat-sub">Awaiting Approval</div>
    </div>
    <div class="stat-card {flagged_card_class}">
        <div class="stat-header">
            <span class="stat-label">Flagged Drafts</span>
            <span class="stat-icon-badge">{flagged_icon}</span>
        </div>
        <div class="stat-value {flagged_val_class}">{flagged_count}</div>
        <div class="stat-sub {flagged_sub_class}">{flagged_sub_text}</div>
    </div>
    <div class="stat-card stat-card-highlight">
        <div class="stat-header">
            <span class="stat-label">Total Sent</span>
            <span class="stat-icon-badge">🚀</span>
        </div>
        <div class="stat-value stat-val-glow">{analytics.get("total_sent", 0)}</div>
        <div class="stat-sub">Hostinger & Outlook</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Open Rate</span>
            <span class="stat-icon-badge">👁️</span>
        </div>
        <div class="stat-value" style="color: #34D399;">{analytics.get("open_rate", 0.0)}%</div>
        <div class="stat-sub">{analytics.get("total_opened", 0)} Opened Pixel</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Replies</span>
            <span class="stat-icon-badge">💬</span>
        </div>
        <div class="stat-value" style="color: #A78BFA;">{analytics.get("total_replied", 0)}</div>
        <div class="stat-sub">{analytics.get("reply_rate", 0.0)}% Reply Rate</div>
    </div>
    <div class="stat-card {bounced_card_class}">
        <div class="stat-header">
            <span class="stat-label">Bounces</span>
            <span class="stat-icon-badge">⚠️</span>
        </div>
        <div class="stat-value {bounced_val_class}">{analytics.get("total_bounced", 0)}</div>
        <div class="stat-sub">{analytics.get("bounce_rate", 0.0)}% Bounce Rate</div>
    </div>
</div>
""", unsafe_allow_html=True)
