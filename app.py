"""
app.py - Streamlit Frontend for the Modular Local Email Automation System.
Features:
- Contact Manager: Internal CRM to add, edit, and manage leads without external spreadsheets.
- Template Builder: Reusable templates with Spintax ({a|b}) and variable ([Name], [Company]) guides and live preview.
- Campaign Generator: Batch generation across selected contacts with Spintax resolution and negative keyword scanning.
- Review Queue & Flag Handling: Dual-mode editor, red alert banners for Flagged emails, and pre-flight compliance gate.
- Configuration & Outbox: Sending windows, negative keywords, Hostinger/Outlook dispatch, and corporate signatures.
"""

import os
import sys

# Ensure local project modules are always loaded directly from the current script directory (.py files)
# rather than any stale frozen bytecode embedded inside PyInstaller's PYZ.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
if current_script_dir not in sys.path:
    sys.path.insert(0, current_script_dir)

import streamlit as st
from database import (
    init_db,
    get_emails,
    get_contacts,
    get_templates,
    get_notifications
)
from tracker import start_tracking_server
from ui.theme import apply_theme
from ui.components import render_header
from ui.sidebar import render_sidebar
from ui.tabs.crm import render_crm_tab
from ui.tabs.studio import render_studio_tab
from ui.tabs.campaigns import render_campaigns_tab
from ui.tabs.review import render_review_tab
from ui.tabs.analytics import render_analytics_tab
from ui.tabs.settings import render_settings_tab

import threading
from scheduler import start_scheduler_loop

def ensure_scheduler_running():
    """Ensure the background email dispatch scheduler thread is active (e.g. when hosted on Streamlit Cloud)."""
    for th in threading.enumerate():
        if th.name == "SellomizeSchedulerThread" and th.is_alive():
            return
    sched_thread = threading.Thread(
        target=start_scheduler_loop,
        kwargs={"interval": 60},
        daemon=True,
        name="SellomizeSchedulerThread"
    )
    sched_thread.start()

# Ensure DB is initialized, tracking server is running, and scheduler daemon is active
init_db()
try:
    start_tracking_server(port=8502)
except Exception:
    pass
ensure_scheduler_running()

st.set_page_config(
    page_title="Sellomize Reach | Agency Email Automation",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling (Sellomize Reach Modern SaaS Theme)
apply_theme()

# Branded Sellomize Reach Top Header Bar
render_header()

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

# Data Collections
all_emails = get_emails()
all_contacts = get_contacts()
contacts_list = all_contacts
all_templates = get_templates()

# ==============================================================================
# ⚙️ PERSISTENT SIDEBAR: INFRASTRUCTURE & SETTINGS
# ==============================================================================
render_sidebar()

# ==============================================================================
# 6-SECTION PRIMARY APP NAVIGATION
# ==============================================================================
tab_leads, tab_studio, tab_campaigns, tab_review, tab_analytics, tab_settings = st.tabs([
    "👥 Leads & Contacts",
    "✍️ Studio & Templates",
    "⚡ Sequences & Campaigns",
    "🛡️ Review Queue & Triage",
    "📊 Analytics & Intelligence",
    "⚙️ Rules & Settings"
])

with tab_leads:
    render_crm_tab(all_contacts)

with tab_studio:
    render_studio_tab(all_templates, contacts_list)

with tab_campaigns:
    render_campaigns_tab(contacts_list, all_templates)

with tab_review:
    render_review_tab()

with tab_analytics:
    render_analytics_tab(all_contacts, all_emails)

with tab_settings:
    render_settings_tab()


