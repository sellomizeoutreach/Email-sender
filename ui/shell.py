"""
ui/shell.py - Application Shell, Navigation & Worker Status for Sellomize Reach.
Section C0 of Complete Restructure Spec.

Responsibilities:
1. Unified top/sidebar shell with 6 clean destinations:
   Compose · Templates · Leads · Bulk Send · Outbox · Settings
2. Live Worker Status Indicator (Sender running vs Sender stopped).
3. Design token enforcement (Pine #083731, White, Orange #FD4D1B for primary CTAs only).
"""

import streamlit as st
from datetime import datetime
from typing import Tuple

from database import get_config, DB_FILE
from ui.theme import apply_theme
from ui.components import render_header, render_notification_bell


def get_worker_status(db_path: str = DB_FILE) -> Tuple[bool, str, str]:
    """
    Evaluate background worker daemon health based on worker_heartbeat in settings.
    Returns (is_active, status_label, caption_message).
    """
    heartbeat_str = (get_config("worker_heartbeat", "", db_path=db_path) or "").strip()
    if not heartbeat_str:
        return False, "Sender Stopped", "Scheduled mail won't send until worker is started."

    try:
        hb_dt = datetime.strptime(heartbeat_str[:19], "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.now()
        diff_seconds = abs((now_dt - hb_dt).total_seconds())
        # If heartbeat within last 45 seconds, considered actively running
        if diff_seconds < 45:
            return True, "Sender Running", f"Worker active (heartbeat: {int(diff_seconds)}s ago)"
        else:
            return False, "Sender Stale", f"Last heartbeat was {int(diff_seconds // 60)}m ago. Scheduled mail paused."
    except Exception:
        return False, "Sender Unknown", "Could not parse worker heartbeat timestamp."


def render_worker_status_badge():
    """Renders a small, clean worker status badge in the UI."""
    is_active, label, msg = get_worker_status()
    if is_active:
        st.markdown(
            f"""<div style="display:inline-flex; align-items:center; gap:6px; background:rgba(8,55,49,0.06);
                border:1px solid rgba(8,55,49,0.2); border-radius:20px; padding:3px 10px; font-size:0.75rem; font-weight:700; color:#083731;"
                title="{msg}">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#10B981;"></span>
                {label}
            </div>""",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f"""<div style="display:inline-flex; align-items:center; gap:6px; background:#F1F5F9;
                border:1px solid #CBD5E1; border-radius:20px; padding:3px 10px; font-size:0.75rem; font-weight:600; color:#64748B;"
                title="{msg}">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#94A3B8;"></span>
                {label}
            </div>""",
            unsafe_allow_html=True
        )


def render_app_shell():
    """Renders top header, notification toaster, worker badge, and sets up design tokens."""
    apply_theme()

    # Top Header Bar: Title on left, Worker status + Notification Bell on right
    col_hdr_main, col_hdr_status, col_hdr_bell = st.columns([3.5, 1.2, 0.5], vertical_alignment="center")
    with col_hdr_main:
        render_header()
    with col_hdr_status:
        render_worker_status_badge()
    with col_hdr_bell:
        render_notification_bell()
