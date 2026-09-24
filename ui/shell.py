"""
ui/shell.py - Reference Left Sidebar Shell & Navigation for Sellomize Reach.
Matches sellomize_reference.html:
- Left Pine Sidebar (#083731) with real Sellomize logo, brand title, OUTREACH section, and pinned worker indicator.
- Topbar with screen title, subtitle, live detected local PC clock, and notifications.
"""

import os
import base64
import streamlit as st
from datetime import datetime
from typing import Tuple

from database import get_config, DB_FILE
from ui.theme import apply_theme
from ui.components import render_notification_bell


def get_logo_data_uri() -> str:
    """Read local brand logo and encode as base64 data URI."""
    for candidate in ["sellomize-logo.png", "logo.jpg"]:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", candidate)
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    mime = "image/png" if candidate.endswith(".png") else "image/jpeg"
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                    return f"data:{mime};base64,{b64}"
            except Exception:
                pass
    return ""


def get_worker_status(db_path: str = DB_FILE) -> Tuple[bool, str, str]:
    """Evaluate background worker daemon health based on worker_heartbeat in settings."""
    heartbeat_str = (get_config("worker_heartbeat", "", db_path=db_path) or "").strip()
    if not heartbeat_str:
        return False, "Sender Stopped", "Scheduled mail won't send until worker is started."

    try:
        hb_dt = datetime.strptime(heartbeat_str[:19], "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.now()
        diff_seconds = abs((now_dt - hb_dt).total_seconds())
        if diff_seconds < 45:
            return True, "Sender Running", f"Worker active (heartbeat: {int(diff_seconds)}s ago)"
        else:
            return False, "Sender Stale", f"Last heartbeat was {int(diff_seconds // 60)}m ago."
    except Exception:
        return False, "Sender Stopped", "Could not parse worker heartbeat timestamp."


def render_sidebar_nav() -> str:
    """Renders the Left Pine Sidebar (#083731) matching the reference HTML."""
    if "active_screen" not in st.session_state:
        st.session_state["active_screen"] = "compose"

    with st.sidebar:
        logo_uri = get_logo_data_uri()
        if logo_uri:
            logo_html = f'<img src="{logo_uri}" alt="Sellomize Reach">'
        else:
            logo_html = '<svg viewBox="0 0 24 24" fill="none"><path d="M22 2 11 13" stroke="#FD4D1B" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M22 2 15 22l-4-9-9-4 20-7Z" stroke="#083731" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'

        st.markdown(f"""
        <div class="brand">
            <span class="logo-mark">{logo_html}</span>
            <span class="logo-text">
                <span class="a">SELLOMIZE</span> <span class="b">REACH</span>
                <span class="v">V2.4</span>
            </span>
        </div>
        <div class="navlabel">OUTREACH</div>
        """, unsafe_allow_html=True)

        screens = [
            ("compose", "✍️  Compose"),
            ("templates", "📄  Templates"),
            ("leads", "👥  Leads"),
            ("bulk", "🚀  Bulk Send"),
            ("outbox", "📥  Outbox"),
            ("settings", "⚙️  Settings"),
        ]

        current = st.session_state["active_screen"]

        for key, label in screens:
            is_active = (current == key)
            btn_type = "primary" if is_active else "secondary"
            if st.button(label, key=f"nav_btn_{key}", type=btn_type, use_container_width=True):
                st.session_state["active_screen"] = key
                st.rerun()

        # Pinned bottom worker indicator
        is_worker_on, worker_label, worker_tooltip = get_worker_status()
        dot_class = "dot" if is_worker_on else "dot-stale"
        status_text = "Sender running · :8502" if is_worker_on else "Sender stopped"

        st.markdown(f"""
        <div class="worker" title="{worker_tooltip}">
            <span class="{dot_class}"></span> {status_text}
        </div>
        """, unsafe_allow_html=True)

    return st.session_state["active_screen"]


def render_topbar(screen_key: str):
    """Render the topbar matching sellomize_reference.html with screen title and detected PC time."""
    screen_meta = {
        "compose": ("Compose", "Write, style and send one email — Hostinger direct."),
        "templates": ("Templates", "Reusable emails. Load into Compose, or use in bulk."),
        "leads": ("Leads", "Your cherry-picked prospects. Import CSV, full CRM columns."),
        "bulk": ("Bulk Send", "One template to a group, spread over time per timezone."),
        "outbox": ("Outbox", "Scheduled, sent and failed — cancel a follow-up before it fires."),
        "settings": ("Settings", "Mailboxes, warmup, signature, keywords and window.")
    }

    title, subtitle = screen_meta.get(screen_key, ("Sellomize Reach", "Hostinger Outreach Engine"))
    local_time_str = datetime.now().strftime("%I:%M %p")

    col_title, col_time, col_bell = st.columns([3.4, 1.6, 0.6], vertical_alignment="center")

    with col_title:
        st.markdown(f"""
        <div style="margin: 0; padding: 2px 0;">
            <h1 style="margin:0; font-size:22px; font-weight:800; color:#083731; line-height:1.2;">{title}</h1>
            <div style="font-size:13px; color:#64748B; margin-top:2px;">{subtitle}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_time:
        st.markdown(f"""
        <div style="display:inline-flex; align-items:center; gap:6px; background:#F8FAFC; border:1px solid #CBD5E1; border-radius:20px; padding:6px 14px; font-size:0.82rem; font-weight:700; color:#083731; white-space:nowrap;" title="Current local system time on your computer">
            <span>💻</span><span>{local_time_str} Local</span>
        </div>
        """, unsafe_allow_html=True)

    with col_bell:
        render_notification_bell()

    st.markdown("<div style='height:1px; background:#E2E8F0; margin-bottom:18px;'></div>", unsafe_allow_html=True)


def render_app_shell():
    """Renders theme, left sidebar nav, and topbar."""
    apply_theme()
    screen_key = render_sidebar_nav()
    render_topbar(screen_key)
    return screen_key
