"""
app.py - Streamlit Frontend for Sellomize Reach.
Section B1, B2 & Part C of Complete Restructure Spec.

Exactly 6 tabs/destinations:
1. Compose
2. Templates
3. Leads
4. Bulk Send
5. Outbox
6. Settings
"""

import os
import sys

# Ensure local project root is on sys.path
current_script_dir = os.path.dirname(os.path.abspath(__file__))
if current_script_dir not in sys.path:
    sys.path.insert(0, current_script_dir)

import streamlit as st

from database import (
    init_db,
    get_contacts,
    get_templates,
    get_notifications,
)
from tracker import start_tracking_server
from ui.shell import render_app_shell
from ui.compose import render_compose_tab
from ui.templates import render_templates_tab
from ui.leads import render_leads_tab
from ui.bulk import render_bulk_tab
from ui.outbox import render_outbox_tab
from ui.settings import render_settings_tab

# Ensure database is initialized on startup
init_db()

# Lightweight local click/open tracking server
try:
    start_tracking_server(port=8502)
except Exception:
    pass

st.set_page_config(
    page_title="Sellomize Reach | Agency Email Automation",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Render unified App Shell (Pine Theme tokens, header, worker status badge, notification bell)
render_app_shell()

# Global Non-Blocking Toast Listener
if "pending_toast" in st.session_state and st.session_state["pending_toast"]:
    toast_info = st.session_state.pop("pending_toast")
    st.toast(toast_info["msg"], icon=toast_info.get("icon", "✅"))

# Real-time Reply Notification Toaster
if "seen_notification_ids" not in st.session_state:
    st.session_state["seen_notification_ids"] = set()

try:
    unread_reply_notifs = get_notifications(unread_only=True, limit=5)
    for n in unread_reply_notifs:
        nid = n["id"]
        if nid not in st.session_state["seen_notification_ids"]:
            st.session_state["seen_notification_ids"].add(nid)
            st.toast(f"{n['title']}: {n['message']}", icon="💬")
except Exception:
    pass

# Retrieve cached or fresh data
all_contacts = get_contacts()
all_templates = get_templates()

# ==============================================================================
# PRIMARY APP NAVIGATION (Exactly 6 destinations)
# ==============================================================================
TAB_NAMES = [
    "✍️ Compose",
    "📄 Templates",
    "👥 Leads",
    "🚀 Bulk Send",
    "📥 Outbox",
    "⚙️ Settings",
]

tab_compose, tab_templates, tab_leads, tab_bulk, tab_outbox, tab_settings = st.tabs(
    TAB_NAMES,
    key="main_app_tabs",
    on_change="rerun"
)

with tab_compose:
    render_compose_tab(all_contacts, all_templates)

with tab_templates:
    render_templates_tab(all_templates)

with tab_leads:
    render_leads_tab(all_contacts)

with tab_bulk:
    render_bulk_tab()

with tab_outbox:
    render_outbox_tab()

with tab_settings:
    render_settings_tab()
