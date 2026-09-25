"""
app.py - Streamlit Frontend for Sellomize Reach.
Section B1, B2 & Part C of Complete Restructure Spec.
Matches sellomize_reference.html:
- 240px Left Pine Sidebar (#083731) with real Sellomize logo, brand title, and pinned worker indicator.
- 6 clean destinations: Compose, Templates, Leads, Bulk Send, Outbox, Settings.
- Live detected local PC clock in the topbar.
- Zero horizontal tab redundancy.
"""

import os
import sys

# Ensure local project root is on sys.path
current_script_dir = os.path.dirname(os.path.abspath(__file__))
if current_script_dir not in sys.path:
    sys.path.insert(0, current_script_dir)

import streamlit as st

import threading

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

# Ensure background scheduler thread is running on Streamlit Cloud & local server
def ensure_background_scheduler():
    for t in threading.enumerate():
        if t.name == "SellomizeSchedulerThread" and t.is_alive():
            return
    try:
        from scheduler import start_scheduler_loop
        t = threading.Thread(
            target=start_scheduler_loop,
            kwargs={"interval": 15},
            name="SellomizeSchedulerThread",
            daemon=True
        )
        t.start()
    except Exception:
        pass

ensure_background_scheduler()

st.set_page_config(
    page_title="Sellomize Reach | Agency Email Automation",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Synchronize backwards compatibility for session tab switches
TAB_TO_SCREEN = {
    "✍️ Compose": "compose",
    "📄 Templates": "templates",
    "👥 Leads": "leads",
    "🚀 Bulk Send": "bulk",
    "📥 Outbox": "outbox",
    "⚙️ Settings": "settings",
}
if "main_app_tabs" in st.session_state and st.session_state["main_app_tabs"] in TAB_TO_SCREEN:
    st.session_state["active_screen"] = TAB_TO_SCREEN[st.session_state["main_app_tabs"]]
    st.session_state.pop("main_app_tabs", None)

# Render Left Pine Sidebar Navigation & Topbar
screen_key = render_app_shell()

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

# Retrieve fresh leads and templates
all_contacts = get_contacts()
all_templates = get_templates()

# ==============================================================================
# RENDER ACTIVE SCREEN (Exactly 6 destinations)
# ==============================================================================
if screen_key == "compose":
    render_compose_tab(all_contacts, all_templates)
elif screen_key == "templates":
    render_templates_tab(all_templates)
elif screen_key == "leads":
    render_leads_tab(all_contacts)
elif screen_key == "bulk":
    render_bulk_tab()
elif screen_key == "outbox":
    render_outbox_tab()
elif screen_key == "settings":
    render_settings_tab()
