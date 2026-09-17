"""
app.py - Streamlit Frontend for the Modular Local Email Automation System.
Features:
- Contact Manager: Internal CRM to add, edit, and manage leads without external spreadsheets.
- Template Builder: Reusable templates with Spintax ({a|b}) and variable ([Name], [Company]) guides and live preview.
- Campaign Generator: Batch generation across selected contacts with Spintax resolution and negative keyword scanning.
- Review Queue & Flag Handling: Dual-mode editor, red alert banners for Flagged emails, and an instant Auto-Rewrite button.
- Configuration & Outbox: API keys (Gemini, GCP Project, OpenAI, Anthropic), negative keywords, Outlook bindings, and signatures.
"""

import os
import sys
import re
import json
import base64
from datetime import datetime, timedelta
import streamlit as st

# Check for rich visual editor component
try:
    from streamlit_quill import st_quill
    QUILL_AVAILABLE = True
except ImportError:
    QUILL_AVAILABLE = False

import importlib
import database
try:
    importlib.reload(database)
except Exception:
    pass

from database import (
    init_db,
    get_config,
    set_config,
    get_all_configs,
    save_all_configs,
    create_contact,
    get_contacts,
    get_contact_by_id,
    update_contact,
    upsert_contact_by_email,
    get_all_distinct_tags,
    get_predefined_tags,
    get_contacts_by_tag,
    delete_contact,
    bulk_delete_contacts,
    bulk_add_tags_to_contacts,
    bulk_remove_tags_from_contacts,
    bulk_set_tags_for_contacts,
    bulk_update_contacts_details,
    get_predefined_variable_keys,
    get_all_distinct_custom_variable_keys,
    parse_variables_from_text,
    format_variables_as_lines,
    create_template,
    get_templates,
    get_template_by_id,
    update_template,
    delete_template,
    create_email,
    get_emails,
    get_email_by_id,
    update_email,
    approve_email,
    flag_email,
    get_approved_due_emails,
    add_smtp_account,
    get_smtp_accounts,
    delete_smtp_account,
    update_smtp_account,
    delete_email
)
from smtp_dispatcher import test_smtp_connection
from contacts_handler import (
    generate_csv_template,
    export_contacts_to_csv,
    import_contacts_from_csv
)
from llm_engine import (
    inject_variables,
    parse_spintax,
    scan_negative_keywords,
    polish_campaign_email,
    rewrite_email,
    auto_rewrite_negative_keyword,
    generate_variations
)

# Ensure DB is initialized
init_db()

def get_logo_base64() -> str:
    """Read local brand logo and encode as base64 data URI."""
    logo_path = os.path.join(os.path.dirname(__file__), "assets", "logo.jpg")
    if os.path.exists(logo_path):
        try:
            with open(logo_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return ""
    return ""

st.set_page_config(
    page_title="Sellomize Reach | Agency Email Automation",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling (Sellomize Reach Ultra-Modern Dark SaaS Theme)
st.markdown("""
<style>
    /* Global Canvas & Ambient Lighting */
    .stApp {
        background: radial-gradient(1200px 600px at 50% -120px, rgba(238, 83, 36, 0.14) 0%, transparent 70%),
                    radial-gradient(900px 500px at 100% 5%, rgba(16, 185, 129, 0.09) 0%, transparent 60%),
                    radial-gradient(800px 600px at 0% 90%, rgba(238, 83, 36, 0.08) 0%, transparent 65%),
                    #061512 !important;
        background-attachment: fixed !important;
        color: #F8FAFC !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    }

    /* Custom Sleek Scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    ::-webkit-scrollbar-track {
        background: #061512;
    }
    ::-webkit-scrollbar-thumb {
        background: rgba(238, 83, 36, 0.35);
        border-radius: 8px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #EE5324;
    }

    /* Global Typography */
    h1, h2, h3, h4, h5, h6 {
        color: #FFFFFF !important;
        letter-spacing: -0.4px !important;
        font-weight: 700 !important;
    }
    p, span, label {
        color: #E2E8F0;
    }
    hr {
        border: none !important;
        height: 1px !important;
        background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.12), transparent) !important;
        margin: 1.75rem 0 !important;
    }

    /* Branded Floating Glass Header */
    .sellomize-header-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, rgba(14, 46, 39, 0.8) 0%, rgba(8, 28, 24, 0.92) 100%);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 16px;
        padding: 1.15rem 1.8rem;
        margin-bottom: 1.6rem;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.12);
        position: relative;
        overflow: hidden;
    }
    .sellomize-header-container::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: linear-gradient(90deg, transparent, #EE5324 30%, #10B981 70%, transparent);
    }
    .sellomize-header-left {
        display: flex;
        align-items: center;
        gap: 1.25rem;
    }
    .sellomize-logo-img {
        height: 56px;
        width: 56px;
        object-fit: cover;
        border-radius: 12px;
        border: 1.5px solid rgba(238, 83, 36, 0.8);
        box-shadow: 0 4px 16px rgba(238, 83, 36, 0.35);
    }
    .sellomize-logo-fallback {
        font-size: 2.2rem;
        background: #0B2621;
        border-radius: 12px;
        padding: 6px 12px;
        border: 1.5px solid #EE5324;
        box-shadow: 0 4px 16px rgba(238, 83, 36, 0.35);
    }
    .sellomize-brand-title {
        font-size: 1.95rem;
        font-weight: 900;
        letter-spacing: 1.4px;
        color: #FFFFFF;
        line-height: 1.1;
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .sellomize-highlight {
        background: linear-gradient(135deg, #FF7B4D 0%, #EE5324 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        filter: drop-shadow(0 0 12px rgba(238, 83, 36, 0.4));
    }
    .sellomize-brand-subtitle {
        font-size: 0.88rem;
        font-weight: 500;
        color: #94A3B8;
        margin-top: 4px;
        letter-spacing: 0.4px;
    }
    .sellomize-header-right {
        display: flex;
        align-items: center;
        gap: 0.85rem;
    }
    .system-status-pill {
        display: flex;
        align-items: center;
        gap: 8px;
        background: rgba(16, 185, 129, 0.12);
        border: 1px solid rgba(16, 185, 129, 0.35);
        color: #34D399;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }
    .status-indicator-dot {
        width: 8px;
        height: 8px;
        background-color: #10B981;
        border-radius: 50%;
        box-shadow: 0 0 10px #10B981;
        animation: pulse-dot 2s infinite ease-in-out;
    }
    @keyframes pulse-dot {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.35; transform: scale(0.8); }
    }
    .sellomize-header-badge {
        background: rgba(238, 83, 36, 0.12);
        color: #FF7B4D;
        border: 1px solid rgba(238, 83, 36, 0.35);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }

    /* Executive Glass KPI Stat Cards */
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 1.1rem;
        margin-bottom: 1.75rem;
    }
    @media (max-width: 1100px) {
        .stats-grid {
            grid-template-columns: repeat(3, 1fr);
        }
    }
    @media (max-width: 768px) {
        .stats-grid {
            grid-template-columns: 1fr;
        }
    }
    .stat-card {
        background: linear-gradient(145deg, rgba(14, 46, 39, 0.65) 0%, rgba(8, 28, 24, 0.8) 100%);
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 1.15rem 1.35rem;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.08);
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    .stat-card:hover {
        transform: translateY(-3px);
        border-color: rgba(238, 83, 36, 0.35);
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4), 0 0 18px rgba(238, 83, 36, 0.15);
    }
    .stat-card-highlight {
        border-color: rgba(238, 83, 36, 0.35);
        background: linear-gradient(145deg, rgba(238, 83, 36, 0.12) 0%, rgba(8, 28, 24, 0.85) 100%);
    }
    .stat-card-alert {
        border-color: rgba(239, 68, 68, 0.45);
        background: linear-gradient(145deg, rgba(239, 68, 68, 0.15) 0%, rgba(8, 28, 24, 0.85) 100%);
    }
    .stat-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.6rem;
    }
    .stat-label {
        font-size: 0.82rem;
        font-weight: 600;
        color: #94A3B8;
        text-transform: uppercase;
        letter-spacing: 0.75px;
    }
    .stat-icon-badge {
        font-size: 1.15rem;
        background: rgba(255, 255, 255, 0.06);
        padding: 3px 8px;
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .stat-value {
        font-size: 2.15rem;
        font-weight: 800;
        color: #FFFFFF;
        line-height: 1.1;
        letter-spacing: -0.5px;
        margin-bottom: 0.35rem;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .stat-val-glow {
        color: #FF7B4D;
        text-shadow: 0 0 16px rgba(238, 83, 36, 0.4);
    }
    .stat-val-alert {
        color: #F87171;
        text-shadow: 0 0 16px rgba(239, 68, 68, 0.4);
    }
    .stat-sub {
        font-size: 0.78rem;
        color: #64748B;
        font-weight: 500;
    }
    .stat-sub-alert {
        color: #F87171;
        font-weight: 600;
    }
    .stat-sub-clean {
        color: #34D399;
        font-weight: 600;
    }

    /* Modern Segmented Floating Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px !important;
        background: rgba(7, 24, 20, 0.75) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        padding: 6px !important;
        border-radius: 12px !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25) !important;
        margin-bottom: 1.75rem !important;
    }
    .stTabs [data-baseweb="tab"] {
        height: 44px !important;
        border-radius: 8px !important;
        color: #94A3B8 !important;
        font-weight: 600 !important;
        font-size: 0.92rem !important;
        padding: 0 1.25rem !important;
        border: 1px solid transparent !important;
        background: transparent !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #FFFFFF !important;
        background: rgba(255, 255, 255, 0.05) !important;
        border-color: rgba(255, 255, 255, 0.08) !important;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, rgba(238, 83, 36, 0.25) 0%, rgba(238, 83, 36, 0.08) 100%) !important;
        border: 1px solid #EE5324 !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        box-shadow: 0 4px 16px rgba(238, 83, 36, 0.28) !important;
        border-radius: 8px !important;
    }
    .stTabs [data-baseweb="tab-highlight"] {
        display: none !important;
    }
    .stTabs [data-baseweb="tab-border"] {
        display: none !important;
    }

    /* Primary High-Impact CTA Buttons */
    button[kind="primary"],
    [data-testid="baseButton-primary"],
    div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"] {
        background: linear-gradient(135deg, #FF6838 0%, #EE5324 55%, #D43E12 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        font-size: 0.92rem !important;
        border: 1px solid rgba(255, 255, 255, 0.2) !important;
        border-radius: 8px !important;
        padding: 0.55rem 1.4rem !important;
        box-shadow: 0 4px 16px rgba(238, 83, 36, 0.38), inset 0 1px 0 rgba(255, 255, 255, 0.25) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        letter-spacing: 0.3px !important;
    }
    button[kind="primary"]:hover,
    [data-testid="baseButton-primary"]:hover,
    div.stButton > button[kind="primary"]:hover,
    div.stFormSubmitButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #FF7B4D 0%, #FF5A26 55%, #E04516 100%) !important;
        box-shadow: 0 6px 24px rgba(238, 83, 36, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.35) !important;
        transform: translateY(-2px) !important;
    }
    button[kind="primary"]:active,
    [data-testid="baseButton-primary"]:active {
        transform: translateY(0px) !important;
    }

    /* Secondary Glass Action Buttons */
    button[kind="secondary"],
    [data-testid="baseButton-secondary"],
    .stButton > button:not([kind="primary"]),
    .stDownloadButton > button {
        background: rgba(14, 44, 38, 0.65) !important;
        backdrop-filter: blur(10px) !important;
        -webkit-backdrop-filter: blur(10px) !important;
        color: #E2E8F0 !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
        padding: 0.5rem 1.2rem !important;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2) !important;
        transition: all 0.2s ease-in-out !important;
    }
    button[kind="secondary"]:hover,
    [data-testid="baseButton-secondary"]:hover,
    .stButton > button:not([kind="primary"]):hover,
    .stDownloadButton > button:hover {
        background: rgba(20, 60, 52, 0.85) !important;
        border-color: #EE5324 !important;
        color: #FFFFFF !important;
        box-shadow: 0 4px 16px rgba(238, 83, 36, 0.25) !important;
        transform: translateY(-1px) !important;
    }

    /* Glass Form Containers & Expanders */
    div[data-testid="stForm"] {
        background: linear-gradient(180deg, rgba(13, 42, 36, 0.65) 0%, rgba(7, 24, 20, 0.8) 100%) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 14px !important;
        padding: 1.5rem 1.75rem !important;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3) !important;
    }
    div[data-testid="stExpander"] {
        background: linear-gradient(180deg, rgba(12, 38, 33, 0.5) 0%, rgba(7, 23, 20, 0.65) 100%) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25) !important;
        overflow: hidden !important;
        margin-bottom: 12px !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stExpander"]:hover {
        border-color: rgba(238, 83, 36, 0.3) !important;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 600 !important;
        color: #FFFFFF !important;
        font-size: 0.95rem !important;
    }

    /* Inputs, Textareas, and Dropdowns */
    div[data-baseweb="input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="select"] > div {
        background-color: rgba(5, 17, 14, 0.75) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        color: #FFFFFF !important;
        box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.3) !important;
        transition: all 0.2s ease !important;
    }
    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="textarea"]:focus-within,
    div[data-baseweb="select"]:focus-within > div {
        border-color: #EE5324 !important;
        box-shadow: 0 0 0 3px rgba(238, 83, 36, 0.22), inset 0 2px 4px rgba(0, 0, 0, 0.3) !important;
        background-color: rgba(6, 21, 18, 0.95) !important;
    }
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        color: #FFFFFF !important;
        background-color: transparent !important;
        font-size: 0.92rem !important;
    }
    div[data-baseweb="input"] input::placeholder,
    div[data-baseweb="textarea"] textarea::placeholder {
        color: #64748B !important;
    }

    /* Progress Bars */
    div[data-testid="stProgress"] > div > div > div > div {
        background: linear-gradient(90deg, #EE5324, #10B981) !important;
        border-radius: 10px !important;
    }
    div[data-testid="stProgress"] > div > div {
        background-color: rgba(4, 16, 13, 0.75) !important;
        border-radius: 10px !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
    }

    /* Email Preview Box */
    .email-preview-box {
        background: rgba(6, 20, 16, 0.8) !important;
        border: 1px solid rgba(255, 255, 255, 0.09) !important;
        border-left: 3.5px solid #EE5324 !important;
        border-radius: 10px !important;
        padding: 1.25rem 1.5rem !important;
        margin-top: 0.6rem !important;
        margin-bottom: 0.8rem !important;
        color: #E2E8F0 !important;
        box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.35) !important;
    }
    .email-preview-box p,
    .email-preview-box div,
    .email-preview-box span {
        color: #E2E8F0 !important;
    }

    /* Flagged Draft Card */
    .flagged-card {
        background: linear-gradient(135deg, rgba(38, 14, 16, 0.75) 0%, rgba(22, 7, 9, 0.9) 100%) !important;
        border: 1.5px solid rgba(239, 68, 68, 0.5) !important;
        border-radius: 12px !important;
        padding: 1.35rem !important;
        margin-bottom: 1.2rem !important;
        color: #FFFFFF !important;
        box-shadow: 0 8px 28px rgba(239, 68, 68, 0.18) !important;
    }
    .flagged-banner {
        background: linear-gradient(135deg, #EF4444 0%, #DC2626 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        padding: 6px 14px !important;
        border-radius: 6px !important;
        margin-bottom: 10px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 6px !important;
        letter-spacing: 0.5px !important;
        box-shadow: 0 2px 10px rgba(239, 68, 68, 0.35) !important;
    }

    /* Micro Status Badges */
    .badge-flagged {
        background: rgba(239, 68, 68, 0.14) !important;
        color: #F87171 !important;
        border: 1px solid rgba(239, 68, 68, 0.35) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }
    .badge-pending {
        background: rgba(245, 158, 11, 0.14) !important;
        color: #FBBF24 !important;
        border: 1px solid rgba(245, 158, 11, 0.35) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }
    .badge-approved {
        background: rgba(59, 130, 246, 0.14) !important;
        color: #60A5FA !important;
        border: 1px solid rgba(59, 130, 246, 0.35) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }
    .badge-sent {
        background: rgba(16, 185, 129, 0.14) !important;
        color: #34D399 !important;
        border: 1px solid rgba(16, 185, 129, 0.35) !important;
        padding: 3px 10px !important;
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 0.8rem !important;
        letter-spacing: 0.3px !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
    }

    /* Syntax Guide Box */
    .syntax-help {
        background: linear-gradient(135deg, rgba(14, 46, 39, 0.6) 0%, rgba(9, 29, 25, 0.75) 100%) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-left: 3.5px solid #EE5324 !important;
        padding: 1rem 1.25rem !important;
        border-radius: 10px !important;
        margin-bottom: 1rem !important;
        color: #E2E8F0 !important;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25) !important;
    }
    .syntax-help code {
        background: rgba(4, 16, 13, 0.8) !important;
        color: #FF7B4D !important;
        padding: 2px 7px !important;
        border-radius: 5px !important;
        border: 1px solid rgba(238, 83, 36, 0.2) !important;
        font-weight: 600 !important;
    }
</style>
""", unsafe_allow_html=True)

# Branded Sellomize Reach Top Header Bar
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
            <div class="sellomize-brand-subtitle">AI Outbound Engine & Multi-Account Outreach Suite</div>
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

# Live Statistics Banner
all_emails = get_emails()
all_contacts = get_contacts()
contacts_list = all_contacts
all_templates = get_templates()

pending_count = sum(1 for e in all_emails if e["status"] == "Pending")
flagged_count = sum(1 for e in all_emails if e["status"] == "Flagged")
approved_count = sum(1 for e in all_emails if e["status"] == "Approved")
sent_count = sum(1 for e in all_emails if e["status"] == "Sent")

flagged_card_class = "stat-card-alert" if flagged_count > 0 else ""
flagged_val_class = "stat-val-alert" if flagged_count > 0 else ""
flagged_sub_class = "stat-sub-alert" if flagged_count > 0 else "stat-sub-clean"
flagged_sub_text = f"🚨 {flagged_count} Action Required" if flagged_count > 0 else "✓ All Drafts Clean"
flagged_icon = "🚨" if flagged_count > 0 else "🛡️"

st.markdown(f"""
<div class="stats-grid">
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Saved Leads</span>
            <span class="stat-icon-badge">👥</span>
        </div>
        <div class="stat-value">{len(all_contacts)}</div>
        <div class="stat-sub">Direct CRM Contacts</div>
    </div>
    <div class="stat-card">
        <div class="stat-header">
            <span class="stat-label">Templates</span>
            <span class="stat-icon-badge">📝</span>
        </div>
        <div class="stat-value">{len(all_templates)}</div>
        <div class="stat-sub">Spintax Ready</div>
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
        <div class="stat-value stat-val-glow">{sent_count}</div>
        <div class="stat-sub">Hostinger & Outlook</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.divider()

# Primary App Navigation Tabs
tab_crm, tab_templates, tab_campaign, tab_review, tab_settings = st.tabs([
    "👥 Contact Manager",
    "📝 Template Builder",
    "🚀 Campaign Generator",
    "📥 Review Queue & Flags",
    "⚙️ Configuration & Outbox"
])

# ==============================================================================
# TAB 1: CONTACT MANAGER (INTERNAL CRM)
# ==============================================================================
with tab_crm:
    st.subheader("👥 Contact Manager (Internal CRM)")
    st.caption("Manage your outreach leads directly in SQLite without relying on external CSV or Excel files.")

    with st.expander("➕ Add New Contact", expanded=len(all_contacts) == 0):
        with st.form("add_contact_form", clear_on_submit=True):
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                c_name = st.text_input("Full Name *", placeholder="e.g. Alex Morgan")
            with fc2:
                c_email = st.text_input("Email Address *", placeholder="alex@company.com")
            with fc3:
                c_company = st.text_input("Company Name", placeholder="Acme Brands")

            # Tag Management
            st.markdown("##### 🏷️ Contact Tags & Outreach Classification")
            all_tags_list = get_all_distinct_tags(include_predefined=True)
            col_t1, col_t2 = st.columns([1.8, 1.2])
            with col_t1:
                selected_tags = st.multiselect(
                    "Select Tags (Predefined & Custom)",
                    options=all_tags_list,
                    help="Choose one or more tags (e.g. Amazon Brand, Shopify DTC, High Priority, Cold Outreach)."
                )
            with col_t2:
                new_tags_raw = st.text_input("Or Add New Custom Tag(s)", placeholder="e.g. Beauty Brands, Q4 Leads", help="Comma-separated list of new custom tags.")

            st.markdown("##### 🧩 Lead Variables & Outreach Attributes (No JSON needed)")
            st.caption("Add variables you want to inject into email templates (e.g. `[Role]`, `[Website]`, `[ASIN]`). They will be substituted automatically.")

            col_cv1, col_cv2, col_cv3 = st.columns(3)
            with col_cv1:
                cv_role = st.text_input("Role / Job Title", placeholder="e.g. Founder & CEO", help="Accessible via [Role] in email templates")
            with col_cv2:
                cv_website = st.text_input("Website / Store URL", placeholder="e.g. https://brand.com", help="Accessible via [Website] in email templates")
            with col_cv3:
                cv_asin = st.text_input("Amazon ASIN / Product ID", placeholder="e.g. B08N5WRWNW", help="Accessible via [ASIN] in email templates")

            with st.expander("➕ Add More Variables (Simple Key: Value or JSON)", expanded=False):
                st.caption("Type one variable per line (e.g. `Category: Skincare` or `Location: Austin, TX`). No JSON quotes or brackets needed!")
                more_vars_raw = st.text_area(
                    "Additional Variables",
                    placeholder="Category: E-Commerce\nLocation: New York, USA\nProduct: Organic Coffee",
                    height=75
                )

            add_contact_btn = st.form_submit_button("Save Contact", type="primary")

            if add_contact_btn:
                if not c_name.strip() or not c_email.strip():
                    st.error("Name and Email Address are required.")
                else:
                    cv_parsed = parse_variables_from_text(more_vars_raw)
                    if cv_role.strip():
                        cv_parsed["Role"] = cv_role.strip()
                    if cv_website.strip():
                        cv_parsed["Website"] = cv_website.strip()
                    if cv_asin.strip():
                        cv_parsed["ASIN"] = cv_asin.strip()

                    # Combine multiselect tags and new text tags
                    extra_tags = [t.strip() for t in new_tags_raw.split(",") if t.strip()]
                    combined_tags = list(set(selected_tags + extra_tags))

                    cid, is_new = upsert_contact_by_email(
                        name=c_name.strip(),
                        email=c_email.strip(),
                        company=c_company.strip(),
                        tags=combined_tags,
                        custom_variables=cv_parsed
                    )
                    action_msg = "added" if is_new else "updated (merged tags)"
                    st.success(f"✅ Contact '{c_name}' successfully {action_msg} (ID #{cid})!")
                    st.rerun()

    # CSV Import / Export Toolbar
    st.markdown("---")
    st.markdown("#### 📁 CSV Import & Export Tools")
    csv_col1, csv_col2 = st.columns(2)

    with csv_col1:
        template_csv = generate_csv_template()
        st.download_button(
            label="📥 Download CSV Template",
            data=template_csv,
            file_name="contacts_template.csv",
            mime="text/csv",
            help="Download a formatted CSV template with columns: Name, Email, Company, Tags, Custom_Variables."
        )

    with csv_col2:
        with st.expander("⬆️ Bulk Import Contacts from CSV"):
            uploaded_csv = st.file_uploader("Upload CSV File", type=["csv"], key="contact_csv_uploader")
            if uploaded_csv is not None:
                if st.button("Process & Import CSV", type="primary"):
                    with st.spinner("Processing CSV and executing deduplicated upserts..."):
                        import_stats = import_contacts_from_csv(uploaded_csv.getvalue())
                        if import_stats["errors"]:
                            for err in import_stats["errors"][:5]:
                                st.error(err)
                        st.success(
                            f"🎉 Successfully imported {import_stats['total']} contact(s): "
                            f"{import_stats['inserted']} new contact(s) added, "
                            f"{import_stats['updated']} existing contact(s) updated with merged tags & variables!"
                        )
                        st.rerun()

    st.markdown("---")

    # Advanced Filtering (Search + Filter by Tag)
    st.markdown("#### 🔍 Filter & Search Leads")
    distinct_tags = get_all_distinct_tags(include_predefined=True)
    filter_col1, filter_col2 = st.columns([2, 2])
    with filter_col1:
        search_query = st.text_input("Search Leads", placeholder="Search by Name, Email, Company, or Tags...", key="crm_search_query")
    with filter_col2:
        tag_filter = st.multiselect("Filter by Tag", options=distinct_tags, placeholder="Select one or more tags...", key="crm_tag_filter")

    # Data Table of Filtered Contacts
    filtered_contacts = get_contacts(tags_filter=tag_filter, search_query=search_query)

    # Initialize CRM selection and editing session state
    if "crm_selected_ids" not in st.session_state:
        st.session_state["crm_selected_ids"] = set()
    if "crm_editing_id" not in st.session_state:
        st.session_state["crm_editing_id"] = None

    filtered_ids = [c["id"] for c in filtered_contacts]
    # Prune any stale IDs
    st.session_state["crm_selected_ids"] = st.session_state["crm_selected_ids"].intersection(set(filtered_ids))
    selected_ids = st.session_state["crm_selected_ids"]
    s_count = len(selected_ids)

    # Selection Toolbar
    if filtered_contacts:
        col_sel1, col_sel2, col_sel3, col_sel4 = st.columns([1.5, 1.5, 2.5, 2.5])
        with col_sel1:
            if st.button(f"☑️ Select All ({len(filtered_contacts)})", key="btn_sel_all_crm", use_container_width=True):
                st.session_state["crm_selected_ids"] = set(filtered_ids)
                st.rerun()
        with col_sel2:
            if st.button("⬜ Clear Selection", key="btn_clear_sel_crm", use_container_width=True):
                st.session_state["crm_selected_ids"] = set()
                st.rerun()
        with col_sel3:
            if s_count > 0:
                st.markdown(f"<div style='padding:7px 12px; background:rgba(238,83,36,0.18); border:1px solid #EE5324; border-radius:8px; color:#FF7B4D; font-weight:700; text-align:center;'>📌 {s_count} lead(s) selected</div>", unsafe_allow_html=True)
            else:
                st.caption(f"Showing **{len(filtered_contacts)}** contact(s). Select leads below for bulk actions.")
        with col_sel4:
            export_csv_data = export_contacts_to_csv(filtered_contacts)
            st.download_button(
                label=f"📤 Export ({len(filtered_contacts)}) to CSV",
                data=export_csv_data,
                file_name="contacts_export.csv",
                mime="text/csv",
                use_container_width=True
            )

    # ⚡ BULK ACTIONS COMMAND CENTER (Appears when 1+ contacts selected)
    if s_count > 0:
        with st.container():
            st.markdown(f"""
            <div style="background: linear-gradient(135deg, rgba(14, 46, 39, 0.9) 0%, rgba(8, 28, 24, 0.98) 100%); border: 1.5px solid #EE5324; border-radius: 14px; padding: 16px 20px; margin: 12px 0 20px; box-shadow: 0 8px 30px rgba(0,0,0,0.45);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="font-size:1.1rem; font-weight:800; color:#FFFFFF; letter-spacing:0.5px;">⚡ BULK ACTIONS <span style="color:#FF7B4D;">({s_count} Leads Selected)</span></div>
                    <div style="font-size:0.82rem; color:#94A3B8;">Apply tag changes, update details, or delete selected contacts in 1 click</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            bulk_tab_tag, bulk_tab_details, bulk_tab_delete = st.tabs([
                "🏷️ Bulk Tag Management",
                "✏️ Bulk Edit Details & Variables",
                "🗑️ Bulk Delete"
            ])

            with bulk_tab_tag:
                col_bt1, col_bt2 = st.columns([1.3, 2.7])
                with col_bt1:
                    bulk_tag_mode = st.radio(
                        "Tag Action",
                        ["➕ Add Tags (Keep Existing)", "🔄 Replace All Tags", "➖ Remove Specific Tags"],
                        key="bulk_tag_mode"
                    )
                with col_bt2:
                    all_avail_tags = get_all_distinct_tags(include_predefined=True)
                    bulk_chosen_tags = st.multiselect("Select Predefined / Existing Tags", options=all_avail_tags, key="bulk_tags_multisel")
                    bulk_custom_tag = st.text_input("Or Type New Tag to Apply", placeholder="e.g. Q4 Audit Target", key="bulk_custom_tag")

                full_bulk_tags = list(set(bulk_chosen_tags + ([bulk_custom_tag.strip()] if bulk_custom_tag.strip() else [])))
                if st.button(f"🚀 Apply Tags to {s_count} Selected Leads", type="primary", key="btn_apply_bulk_tags"):
                    if not full_bulk_tags and "Replace" not in bulk_tag_mode:
                        st.warning("Please select or enter at least one tag.")
                    else:
                        s_list = list(selected_ids)
                        if "Add Tags" in bulk_tag_mode:
                            bulk_add_tags_to_contacts(s_list, full_bulk_tags)
                            st.success(f"✅ Added tags {full_bulk_tags} to {len(s_list)} contact(s)!")
                        elif "Replace" in bulk_tag_mode:
                            bulk_set_tags_for_contacts(s_list, full_bulk_tags)
                            st.success(f"✅ Replaced tags with {full_bulk_tags} on {len(s_list)} contact(s)!")
                        else:
                            bulk_remove_tags_from_contacts(s_list, full_bulk_tags)
                            st.success(f"✅ Removed tags {full_bulk_tags} from {len(s_list)} contact(s)!")
                        st.rerun()

            with bulk_tab_details:
                st.caption(f"Update company or inject custom variables across all {s_count} selected leads:")
                col_bd1, col_bd2 = st.columns(2)
                with col_bd1:
                    bulk_company = st.text_input("Set Company Name (leave blank to keep unchanged)", placeholder="e.g. Acme Brands", key="bulk_company_inp")
                with col_bd2:
                    st.caption("Add/Update Custom Variables (JSON):")
                    bulk_vars_raw = st.text_area("Variables JSON to Merge", value="{\n  \"Role\": \"Founder\"\n}", height=75, key="bulk_vars_json")

                if st.button(f"💾 Update Details on {s_count} Leads", key="btn_bulk_update_details"):
                    try:
                        parsed_vars = json.loads(bulk_vars_raw) if bulk_vars_raw.strip() else {}
                    except Exception as b_err:
                        st.warning(f"Invalid JSON: {b_err}")
                        parsed_vars = {}

                    bulk_update_contacts_details(
                        contact_ids=list(selected_ids),
                        company=bulk_company if bulk_company.strip() else None,
                        custom_vars_to_merge=parsed_vars if parsed_vars else None
                    )
                    st.success(f"✅ Updated details on {s_count} contact(s)!")
                    st.rerun()

            with bulk_tab_delete:
                st.error(f"⚠️ Caution: This will permanently delete {s_count} selected contact(s) from your database.")
                confirm_del = st.checkbox(f"Yes, permanently delete these {s_count} contact(s)", key="confirm_bulk_del")
                if confirm_del:
                    if st.button(f"🗑️ Confirm Delete {s_count} Contacts", type="primary", key="btn_confirm_bulk_del"):
                        del_num = bulk_delete_contacts(list(selected_ids))
                        st.session_state["crm_selected_ids"] = set()
                        st.success(f"Deleted {del_num} contact(s).")
                        st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

    if not filtered_contacts:
        st.info("No contacts match the selected search/tag filter.")
    else:
        for contact in filtered_contacts:
            c_id = contact["id"]
            is_editing = (st.session_state.get("crm_editing_id") == c_id)
            is_selected = (c_id in selected_ids)

            with st.container():
                col_chk, col_c1, col_c2, col_c3, col_c4 = st.columns([0.45, 2.5, 2.5, 3.1, 1.45])
                with col_chk:
                    checked = st.checkbox("", key=f"sel_c_{c_id}", value=is_selected)
                    if checked != is_selected:
                        if checked:
                            st.session_state["crm_selected_ids"].add(c_id)
                        else:
                            st.session_state["crm_selected_ids"].discard(c_id)
                        st.rerun()

                with col_c1:
                    st.markdown(f"**{contact['name']}**")
                    st.caption(f"ID #{c_id} | Added: {contact['created_at']}")
                with col_c2:
                    st.markdown(f"📧 `{contact['email']}`")
                    st.markdown(f"🏢 {contact.get('company') or 'No Company'}")
                with col_c3:
                    # Tag Badges with distinct intelligent colors
                    tags_list = contact.get("tags_list") or []
                    if tags_list:
                        tags_html = ""
                        for t in tags_list:
                            bg = "rgba(16, 185, 129, 0.14)"
                            color = "#34D399"
                            border = "rgba(16, 185, 129, 0.35)"
                            if any(w in t.lower() for w in ["priority", "warm", "urgent"]):
                                bg = "rgba(238, 83, 36, 0.16)"
                                color = "#FF7B4D"
                                border = "rgba(238, 83, 36, 0.45)"
                            elif any(w in t.lower() for w in ["amazon", "shopify", "ecommerce", "brand"]):
                                bg = "rgba(59, 130, 246, 0.14)"
                                color = "#60A5FA"
                                border = "rgba(59, 130, 246, 0.35)"
                            elif any(w in t.lower() for w in ["do not", "stop", "unsub"]):
                                bg = "rgba(239, 68, 68, 0.14)"
                                color = "#F87171"
                                border = "rgba(239, 68, 68, 0.35)"
                            tags_html += f"<span style='background:{bg}; color:{color}; border:1px solid {border}; font-size:0.78rem; font-weight:700; padding:2px 8px; border-radius:6px; margin-right:4px; display:inline-block;'>🏷️ {t}</span> "
                        st.markdown(tags_html, unsafe_allow_html=True)

                    vars_dict = contact.get("custom_variables_dict") or {}
                    if vars_dict:
                        var_badges = []
                        for k, v in vars_dict.items():
                            k_lower = k.lower()
                            if any(w in k_lower for w in ["role", "title", "position"]):
                                icon = "💼"
                            elif any(w in k_lower for w in ["web", "url", "domain", "store", "shop"]):
                                icon = "🌐"
                            elif any(w in k_lower for w in ["asin", "sku", "product"]):
                                icon = "📦"
                            elif any(w in k_lower for w in ["phone", "mobile", "tel"]):
                                icon = "📞"
                            elif any(w in k_lower for w in ["loc", "city", "country", "state"]):
                                icon = "📍"
                            elif any(w in k_lower for w in ["rev", "arr", "mrr", "$"]):
                                icon = "💰"
                            else:
                                icon = "🧩"
                            var_badges.append(f"<span style='background:rgba(255,255,255,0.06); color:#CBD5E1; border:1px solid rgba(255,255,255,0.12); font-size:0.75rem; padding:2px 7px; border-radius:5px; margin-right:4px; display:inline-block;'>{icon} <b>[{k}]</b>: {v}</span> ")
                        st.markdown("".join(var_badges), unsafe_allow_html=True)
                    elif not tags_list:
                        st.caption("No tags or variables")

                with col_c4:
                    col_btn_e, col_btn_d = st.columns(2)
                    with col_btn_e:
                        if st.button("✏️", key=f"edit_btn_{c_id}", help="Edit Contact Details & Tags"):
                            if st.session_state.get("crm_editing_id") == c_id:
                                st.session_state["crm_editing_id"] = None
                            else:
                                st.session_state["crm_editing_id"] = c_id
                            st.rerun()
                    with col_btn_d:
                        if st.button("🗑️", key=f"del_contact_{c_id}", help="Delete Contact"):
                            delete_contact(c_id)
                            st.session_state["crm_selected_ids"].discard(c_id)
                            st.warning(f"Contact #{c_id} deleted.")
                            st.rerun()

                # INLINE EDIT EXPANDER/FORM
                if is_editing:
                    with st.container():
                        st.markdown(f"""
                        <div style="background: rgba(14, 46, 39, 0.65); border: 1px solid #10B981; border-radius: 10px; padding: 14px 18px; margin: 8px 0 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.3);">
                            <strong style="color: #34D399;">✏️ Edit Lead #{c_id}: {contact['name']}</strong>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.form(f"edit_contact_form_{c_id}"):
                            ec1, ec2, ec3 = st.columns(3)
                            with ec1:
                                edit_name = st.text_input("Full Name *", value=contact["name"])
                            with ec2:
                                edit_email = st.text_input("Email Address *", value=contact["email"])
                            with ec3:
                                edit_company = st.text_input("Company", value=contact.get("company") or "")

                            all_avail_tags = get_all_distinct_tags(include_predefined=True)
                            curr_tags = contact.get("tags_list") or []
                            col_et1, col_et2 = st.columns([2, 1])
                            with col_et1:
                                edit_selected_tags = st.multiselect(
                                    "Tags (Select Predefined / Existing)",
                                    options=all_avail_tags,
                                    default=[t for t in curr_tags if t in all_avail_tags]
                                )
                            with col_et2:
                                edit_new_tags = st.text_input("Or Add New Tag(s)", placeholder="e.g. Q4 Audit", help="Comma separated")

                            st.markdown("##### 🧩 Lead Variables (No JSON needed)")
                            c_cv = contact.get("custom_variables_dict") or {}
                            col_ecv1, col_ecv2, col_ecv3 = st.columns(3)
                            with col_ecv1:
                                edit_role = st.text_input("Role / Job Title", value=str(c_cv.get("Role", "")), key=f"edit_role_{c_id}")
                            with col_ecv2:
                                edit_web = st.text_input("Website / Store URL", value=str(c_cv.get("Website", "")), key=f"edit_web_{c_id}")
                            with col_ecv3:
                                edit_asin = st.text_input("Amazon ASIN / Product", value=str(c_cv.get("ASIN", "")), key=f"edit_asin_{c_id}")

                            other_vars = {k: v for k, v in c_cv.items() if k not in ["Role", "Website", "ASIN"]}
                            with st.expander("➕ Other Variables (Key: Value or JSON)", expanded=bool(other_vars)):
                                edit_other_vars_text = st.text_area(
                                    "Additional Custom Variables",
                                    value=format_variables_as_lines(other_vars),
                                    key=f"edit_other_vars_{c_id}",
                                    height=65,
                                    help="One variable per line (e.g. Category: Skincare or Location: NYC). No JSON syntax required!"
                                )

                            col_esave, col_ecancel = st.columns([1.5, 4])
                            with col_esave:
                                save_edit_btn = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)
                            with col_ecancel:
                                cancel_edit_btn = st.form_submit_button("Cancel", use_container_width=True)

                            if save_edit_btn:
                                if not edit_name.strip() or not edit_email.strip():
                                    st.error("Name and Email are required.")
                                else:
                                    parsed_cv = parse_variables_from_text(edit_other_vars_text)
                                    if edit_role.strip():
                                        parsed_cv["Role"] = edit_role.strip()
                                    if edit_web.strip():
                                        parsed_cv["Website"] = edit_web.strip()
                                    if edit_asin.strip():
                                        parsed_cv["ASIN"] = edit_asin.strip()

                                    extra_t = [t.strip() for t in edit_new_tags.split(",") if t.strip()]
                                    final_t = list(set(edit_selected_tags + extra_t))

                                    update_contact(
                                        contact_id=c_id,
                                        name=edit_name.strip(),
                                        email=edit_email.strip(),
                                        company=edit_company.strip(),
                                        tags=final_t,
                                        custom_variables=parsed_cv
                                    )
                                    st.session_state["crm_editing_id"] = None
                                    st.success(f"✅ Saved changes for contact #{c_id}!")
                                    st.rerun()

                            if cancel_edit_btn:
                                st.session_state["crm_editing_id"] = None
                                st.rerun()

                st.markdown("<hr style='margin: 0.4rem 0; opacity: 0.15;'>", unsafe_allow_html=True)

# ==============================================================================
# TAB 2: TEMPLATE BUILDER (SPINTAX & VARIABLES)
# ==============================================================================
with tab_templates:
    st.subheader("📝 Template Builder")
    st.caption("Create reusable cold outreach templates with dynamic variable insertion and Spintax variation.")

    # Dynamic Variable Badges from CRM
    detected_var_keys = get_all_distinct_custom_variable_keys(include_predefined=True)
    var_chips = ["Name", "Company", "Email"] + [k for k in detected_var_keys if k not in ["Name", "Company", "Email"]]
    var_chips_html = "".join([f"<code style='background:rgba(56, 189, 248, 0.12); color:#38BDF8; border: 1px solid rgba(56, 189, 248, 0.3); font-weight:700; padding:2px 6px; border-radius:4px; margin-right:4px; display:inline-block;'>[{k}]</code> " for k in var_chips])

    # Syntax Guide Box
    st.markdown(f"""
    <div class="syntax-help">
        <strong>💡 Template Formatting Guide:</strong><br>
        • <strong>Variables:</strong> Use brackets like <code>[Name]</code> or <code>[Company]</code>. They are automatically injected with prospect data.<br>
        • <strong>Available Lead Variables:</strong> {var_chips_html}<br>
        • <strong>Spintax:</strong> Use <code>{{variation1|variation2|variation3}}</code> syntax. The engine randomly selects an option per lead to ensure unique copy.
    </div>
    """, unsafe_allow_html=True)

    with st.expander("➕ Create New Template", expanded=True):
        with st.form("new_template_form"):
            t_name = st.text_input("Template Name *", placeholder="e.g. E-Commerce Product Page Teardown")
            default_tpl_body = (
                "{Hi|Hello|Hey} [Name],<br><br>"
                "I was reviewing [Company]'s listings and noticed {a couple of missed opportunities|some quick areas for improvement} on your mobile bullet points.<br><br>"
                "We recently helped another brand in your category improve mobile conversions by 21% using a quick infographic overhaul.<br><br>"
                "Would you be open to {a 3-minute video breakdown|a quick Loom teardown} showing how this applies to [Company]?"
            )
            t_body = st.text_area("Template Body (HTML / Spintax / Variables) *", value=default_tpl_body, height=220)

            save_tpl_btn = st.form_submit_button("Save Template", type="primary")
            if save_tpl_btn:
                if not t_name.strip() or not t_body.strip():
                    st.error("Both Template Name and Body are required.")
                else:
                    new_tid = create_template(template_name=t_name, body_content=t_body)
                    st.success(f"✅ Template '{t_name}' saved (ID #{new_tid})!")
                    st.rerun()

    # Saved Templates List & Spintax Test Preview
    templates_list = get_templates()
    if not templates_list:
        st.info("No templates found. Create one above.")
    else:
        st.markdown(f"### Saved Templates ({len(templates_list)})")
        for tpl in templates_list:
            with st.expander(f"📄 {tpl['template_name']} (Created: {tpl['created_at']})", expanded=False):
                st.markdown("**Template Source:**")
                st.code(tpl["body_content"], language="html")

                col_tp1, col_tp2, col_tp3 = st.columns([2, 1, 1])
                with col_tp1:
                    test_btn = st.button("🧪 Test Spintax & Variable Resolution", key=f"test_tpl_{tpl['id']}")
                with col_tp3:
                    if st.button("🗑️ Delete Template", key=f"del_tpl_{tpl['id']}"):
                        delete_template(tpl["id"])
                        st.warning(f"Template '{tpl['template_name']}' deleted.")
                        st.rerun()

                if test_btn:
                    # Pick sample or first contact for preview
                    sample_contact = contacts_list[0] if contacts_list else {
                        "name": "Sarah Jenkins",
                        "company": "Apex Outdoors",
                        "email": "sarah@apex.com",
                        "custom_variables_dict": {"Role": "Founder"}
                    }
                    injected = inject_variables(tpl["body_content"], sample_contact)
                    resolved = parse_spintax(injected)

                    st.markdown(f"**Randomized Resolution with contact '{sample_contact['name']}' at '{sample_contact['company']}':**")
                    st.markdown(f'<div class="email-preview-box">{resolved}</div>', unsafe_allow_html=True)

# ==============================================================================
# TAB 3: CAMPAIGN GENERATOR (REPLACES OLD GENERATOR)
# ==============================================================================
with tab_campaign:
    st.subheader("🚀 Campaign Generator")
    st.caption("Select leads from your CRM, choose a template, and generate contextual personalized emails with automated negative keyword scanning.")

    contacts_list = get_contacts()
    templates_list = get_templates()

    if not contacts_list:
        st.warning("⚠️ You have no contacts saved. Please add contacts in the 'Contact Manager' tab first.")
    elif not templates_list:
        st.warning("⚠️ You have no templates saved. Please create a template in the 'Template Builder' tab first.")
    else:
        all_distinct_tags = get_all_distinct_tags()
        tag_selector_options = ["-- All Contacts --"] + all_distinct_tags

        col_tag_sel, col_tpl_sel = st.columns([1, 1])
        with col_tag_sel:
            selected_tag_filter = st.selectbox(
                "🏷️ Select by Tag (Target Audience)",
                options=tag_selector_options,
                help="Choose a tag to automatically queue all matching brand leads."
            )

        with col_tpl_sel:
            template_options = {t["id"]: t["template_name"] for t in templates_list}
            selected_template_id = st.selectbox(
                "Select Outreach Template *",
                options=list(template_options.keys()),
                format_func=lambda tid: template_options[tid]
            )

        # Determine queued contacts based on tag selection
        if selected_tag_filter != "-- All Contacts --":
            matching_contacts = get_contacts(tags_filter=[selected_tag_filter])
            default_selection = [c["id"] for c in matching_contacts]
            st.info(f"🎯 Auto-queued **{len(matching_contacts)}** contact(s) with tag **'{selected_tag_filter}'**.")
        else:
            matching_contacts = contacts_list
            select_all = st.checkbox("Select All Contacts", value=True)
            default_selection = [c["id"] for c in matching_contacts] if select_all else ([matching_contacts[0]["id"]] if matching_contacts else [])

        contact_options = {c["id"]: f"{c['name']} ({c.get('company') or 'No Company'} - {c['email']}) [Tags: {c.get('tags') or 'None'}]" for c in contacts_list}

        selected_contact_ids = st.multiselect(
            "Target Contacts Queued for Generation *",
            options=list(contact_options.keys()),
            default=default_selection,
            format_func=lambda cid: contact_options.get(cid, str(cid)),
            key=f"camp_contacts_select_{selected_tag_filter}"
        )

        custom_prompt_notes = st.text_area(
            "Special AI Instructions / Playbook Polish Notes (Optional)",
            value="Ensure the opening hook is friendly and natural. Keep formatting strictly in clean HTML paragraphs.",
            height=80,
            placeholder="Add any additional constraints for LiteLLM..."
        )

        st.markdown("<br>", unsafe_allow_html=True)
        generate_campaign_btn = st.button("🚀 Generate Campaign", type="primary", use_container_width=True)

        if generate_campaign_btn:
            if not selected_contact_ids:
                st.error("Please select at least one contact.")
            else:
                chosen_template = get_template_by_id(selected_template_id)
                neg_keywords_setting = get_config("negative_keywords", "")

                st.info(f"Generating personalized emails for {len(selected_contact_ids)} contact(s) using template '{chosen_template['template_name']}'...")
                progress_bar = st.progress(0)

                created_pending = 0
                created_flagged = 0

                for idx, cid in enumerate(selected_contact_ids):
                    contact = get_contact_by_id(cid)

                    # 1. Variable Injection
                    injected_text = inject_variables(chosen_template["body_content"], contact)

                    # 2. Spintax Resolution
                    spintax_resolved = parse_spintax(injected_text)

                    # 3. AI Polish / Formatting via LiteLLM
                    try:
                        polished = polish_campaign_email(
                            resolved_body=spintax_resolved,
                            contact_name=contact["name"],
                            company_name=contact.get("company") or "your brand",
                            custom_instructions=custom_prompt_notes
                        )
                        final_subject = polished.get("subject") or f"Quick observation for {contact.get('company') or contact['name']}"
                        final_html = polished.get("body_html") or f"<p>{spintax_resolved}</p>"
                    except Exception as llm_err:
                        # Fallback if API fails
                        final_subject = f"Quick inquiry for {contact.get('company') or contact['name']}"
                        final_html = f"<p>{spintax_resolved}</p>"

                    # 4. Negative Keyword Scanner
                    combined_text = f"{final_subject} {final_html}"
                    detected_trigger = scan_negative_keywords(combined_text, neg_keywords_setting)

                    if detected_trigger:
                        status = "Flagged"
                        notes = f"Flagged for negative keyword: '{detected_trigger}'"
                        created_flagged += 1
                    else:
                        status = "Pending"
                        notes = None
                        created_pending += 1

                    # 5. Save to database
                    create_email(
                        email_html=final_html,
                        subject=final_subject,
                        recipient=contact["email"],
                        status=status,
                        revision_notes=notes
                    )

                    progress_bar.progress((idx + 1) / len(selected_contact_ids))

                st.success(f"🎉 Campaign Generation Complete! Created **{created_pending}** Pending draft(s) and **{created_flagged}** Flagged draft(s).")
                if created_flagged > 0:
                    st.warning(f"⚠️ {created_flagged} draft(s) were flagged for containing restricted negative keywords. Check the Review Queue to Auto-Rewrite them.")

# ==============================================================================
# TAB 4: REVIEW QUEUE & FLAG HANDLING
# ==============================================================================
with tab_review:
    st.subheader("📥 Review Queue & Flag Handling")
    st.caption("Review drafts, resolve flagged negative keywords via Auto-Rewrite, edit copy in Visual/HTML modes, and schedule for automated dispatch.")

    review_filter = st.radio(
        "Queue Filter",
        ["All Actionable", "Flagged Only (Action Required)", "Pending Only"],
        horizontal=True
    )

    if review_filter == "Flagged Only (Action Required)":
        drafts_to_show = get_emails(status="Flagged")
    elif review_filter == "Pending Only":
        drafts_to_show = get_emails(status="Pending")
    else:
        # All Actionable = Pending + Flagged
        drafts_to_show = [e for e in get_emails() if e["status"] in ["Pending", "Flagged"]]

    if not drafts_to_show:
        st.info("No actionable emails in the review queue. Generate a campaign in the 'Campaign Generator' tab.")
    else:
        st.write(f"Displaying **{len(drafts_to_show)}** email(s):")

        for draft in drafts_to_show:
            draft_id = draft["id"]
            is_flagged = draft["status"] == "Flagged"
            subject_key = f"subject_{draft_id}"
            recipient_key = f"recipient_{draft_id}"
            sched_date_key = f"sched_date_{draft_id}"
            sched_time_key = f"sched_time_{draft_id}"
            editor_mode_key = f"editor_mode_{draft_id}"
            shared_body_key = f"draft_body_{draft_id}"
            reject_toggle_key = f"reject_toggle_{draft_id}"
            revision_inst_key = f"revision_inst_{draft_id}"

            if shared_body_key not in st.session_state:
                st.session_state[shared_body_key] = draft["email_html"]

            # Visual styling wrapper for Flagged vs Pending
            container_title = f"{'🚨 FLAGGED' if is_flagged else '📄 Draft'} #{draft_id} - {draft['subject']} -> {draft.get('recipient')}"

            with st.expander(container_title, expanded=is_flagged):
                # PROMINENT RED WARNING BOX FOR FLAGGED EMAILS
                if is_flagged:
                    st.markdown(f"""
                    <div class="flagged-card">
                        <div class="flagged-banner">⚠️ FLAGGED NEGATIVE KEYWORD DETECTED</div><br>
                        <strong>Reason:</strong> {draft.get('revision_notes') or 'Trigger word found'}.<br>
                        This email cannot be approved until the restricted word is removed. Use <strong>Auto-Rewrite</strong> below to automatically remove it with AI.
                    </div>
                    """, unsafe_allow_html=True)

                    # Dedicated Auto-Rewrite Button
                    ar_col1, ar_col2 = st.columns([1.5, 3])
                    with ar_col1:
                        auto_rw_btn = st.button("⚡ Auto-Rewrite with AI", key=f"autorw_{draft_id}", type="primary")

                    if auto_rw_btn:
                        # Extract trigger word from revision notes
                        rev_notes = draft.get("revision_notes") or ""
                        match = re.search(r"'(.*?)'", rev_notes)
                        trigger_word = match.group(1) if match else "banned term"

                        with st.spinner(f"Calling LiteLLM to rewrite and remove '{trigger_word}'..."):
                            try:
                                rw_result = auto_rewrite_negative_keyword(
                                    email_html=st.session_state[shared_body_key],
                                    trigger_word=trigger_word
                                )

                                # Re-scan rewritten content
                                neg_list = get_config("negative_keywords", "")
                                test_text = f"{rw_result['subject']} {rw_result['body_html']}"
                                still_flagged = scan_negative_keywords(test_text, neg_list)

                                if still_flagged:
                                    update_email(
                                        email_id=draft_id,
                                        email_html=rw_result["body_html"],
                                        subject=rw_result["subject"],
                                        status="Flagged",
                                        revision_notes=f"Flagged for negative keyword: '{still_flagged}'"
                                    )
                                    st.session_state[shared_body_key] = rw_result["body_html"]
                                    st.warning(f"Rewritten, but detected another negative word: '{still_flagged}'.")
                                else:
                                    update_email(
                                        email_id=draft_id,
                                        email_html=rw_result["body_html"],
                                        subject=rw_result["subject"],
                                        status="Pending",
                                        revision_notes="Cleaned via Auto-Rewrite"
                                    )
                                    st.session_state[shared_body_key] = rw_result["body_html"]
                                    st.success(f"🎉 Successfully cleaned! Draft #{draft_id} updated to 'Pending'.")

                                st.rerun()
                            except Exception as ar_err:
                                st.error(f"Auto-rewrite failed: {ar_err}")

                # Metadata row: Subject, Recipient, Local Schedule Time
                col_meta1, col_meta2, col_meta3 = st.columns([2, 1, 1])
                with col_meta1:
                    updated_subject = st.text_input("Subject Line", value=draft["subject"], key=subject_key)
                with col_meta2:
                    updated_recipient = st.text_input("Target Recipient Email", value=draft.get("recipient") or "", placeholder="client@target.com", key=recipient_key)
                with col_meta3:
                    # Scheduled Time strictly in Local System Time
                    local_now = datetime.now().astimezone()
                    default_date = local_now.date()
                    default_time = (local_now + timedelta(minutes=5)).time()

                    if draft.get("scheduled_time"):
                        try:
                            parsed_dt = datetime.strptime(draft["scheduled_time"], "%Y-%m-%d %H:%M:%S")
                            default_date = parsed_dt.date()
                            default_time = parsed_dt.time()
                        except Exception:
                            pass

                    c_date, c_time = st.columns(2)
                    with c_date:
                        sched_date = st.date_input("Date (Local)", value=default_date, key=sched_date_key)
                    with c_time:
                        sched_time = st.time_input("Time (Local)", value=default_time, key=sched_time_key)

                    scheduled_datetime_str = f"{sched_date.strftime('%Y-%m-%d')} {sched_time.strftime('%H:%M:%S')}"

                st.markdown("---")

                # Dual-Mode Editor tied to single shared state key
                st.markdown("##### 📝 Email Body (Dual-Mode Editor)")
                editor_mode = st.radio(
                    "View Mode",
                    ["Visual", "HTML Source"],
                    horizontal=True,
                    key=editor_mode_key
                )

                if editor_mode == "Visual":
                    if QUILL_AVAILABLE:
                        st.caption("Visual WYSIWYG Editor: Format text directly. Tied to single shared state.")
                        quill_content = st_quill(
                            value=st.session_state[shared_body_key],
                            html=True,
                            key=f"quill_email_{draft_id}"
                        )
                        if quill_content is not None:
                            st.session_state[shared_body_key] = quill_content
                    else:
                        st.caption("Visual Preview & Editor")
                        edited_txt = st.text_area(
                            "Visual / Plain Text Content",
                            value=st.session_state[shared_body_key],
                            height=200,
                            key=f"text_email_{draft_id}"
                        )
                        st.session_state[shared_body_key] = edited_txt
                else:
                    st.caption("HTML Source Mode: Directly edit HTML tags, attributes, and inline styling.")
                    source_content = st.text_area(
                        "Raw HTML Code",
                        value=st.session_state[shared_body_key],
                        height=200,
                        key=f"source_email_{draft_id}"
                    )
                    st.session_state[shared_body_key] = source_content

                # Live Email Preview Box with Signature
                saved_sig = get_config("signature_html", "")
                st.markdown("**Combined Email Preview (with Signature):**")
                preview_html = f"{st.session_state[shared_body_key]}<br><br>{saved_sig}"
                st.markdown(f'<div class="email-preview-box">{preview_html}</div>', unsafe_allow_html=True)

                st.markdown("---")

                # Action Controls
                act_col1, act_col2, act_col3 = st.columns([1.5, 2, 1])

                with act_col1:
                    # Disable Approve if flagged
                    approve_btn = st.button(
                        "✅ Approve & Schedule",
                        key=f"approve_{draft_id}",
                        type="primary",
                        disabled=is_flagged,
                        help="Flagged emails must have negative keywords removed before approval." if is_flagged else "Approve and schedule for automated dispatch."
                    )
                    if approve_btn:
                        if not updated_recipient.strip():
                            st.error("Please enter a Target Recipient Email before approving.")
                        else:
                            approve_email(
                                email_id=draft_id,
                                recipient=updated_recipient.strip(),
                                scheduled_time=scheduled_datetime_str,
                                email_html=st.session_state[shared_body_key],
                                subject=updated_subject.strip()
                            )
                            st.success(f"Draft #{draft_id} marked as 'Approved' for dispatch at {scheduled_datetime_str} (Local Time)!")
                            st.rerun()

                with act_col2:
                    reject_toggle = st.checkbox("🔄 Custom Reject & Rewrite", key=reject_toggle_key)

                with act_col3:
                    del_btn = st.button("🗑️ Delete Draft", key=f"del_{draft_id}", use_container_width=True)
                    if del_btn:
                        delete_email(draft_id)
                        st.warning(f"Draft #{draft_id} deleted.")
                        st.rerun()

                # Custom Revision Instructions Panel
                if reject_toggle:
                    st.markdown("#### 🤖 Custom Revision Instructions")
                    rev_instructions = st.text_input(
                        "Revision Instructions (What should the AI fix?)",
                        placeholder="e.g. Make the opening hook punchier, shorten the second paragraph...",
                        key=revision_inst_key
                    )
                    submit_rev_btn = st.button("🚀 Submit Revision", key=f"submit_rev_{draft_id}")

                    if submit_rev_btn:
                        if not rev_instructions.strip():
                            st.error("Please provide revision instructions for the AI.")
                        else:
                            with st.spinner("Calling LiteLLM to revise email..."):
                                try:
                                    revised = rewrite_email(
                                        rejected_draft_html=st.session_state[shared_body_key],
                                        revision_instructions=rev_instructions.strip()
                                    )
                                    # Scan again for negative keywords
                                    neg_list = get_config("negative_keywords", "")
                                    detected = scan_negative_keywords(f"{revised['subject']} {revised['body_html']}", neg_list)

                                    new_status = "Flagged" if detected else "Pending"
                                    new_notes = f"Flagged for negative keyword: '{detected}'" if detected else rev_instructions.strip()

                                    update_email(
                                        email_id=draft_id,
                                        email_html=revised["body_html"],
                                        subject=revised["subject"],
                                        status=new_status,
                                        revision_notes=new_notes
                                    )
                                    st.session_state[shared_body_key] = revised["body_html"]
                                    st.success(f"Draft #{draft_id} has been revised and refreshed as {new_status}!")
                                    st.rerun()
                                except Exception as rev_err:
                                    st.error(f"Revision failed: {rev_err}")

# ==============================================================================
# TAB 5: CONFIGURATION & OUTBOX
# ==============================================================================
with tab_settings:
    st.subheader("⚙️ System Configuration & Outbox")
    st.caption("Manage Hostinger SMTP mailboxes, AI provider credentials, negative keywords, Outlook sender bindings, and monitor dispatch history.")

    current_configs = get_all_configs()

    # ==============================================================================
    # HOSTINGER DIRECT SMTP MAILBOXES (MULTI-ACCOUNT ROTATION)
    # ==============================================================================
    st.markdown("### ⚡ Hostinger Mailbox Accounts (Multi-Account Rotation)")
    st.caption("Connect and scale multiple Hostinger agency email accounts. The outbound engine automatically load-balances and rotates mailboxes to prevent daily quota exhaustion and bypass spam filters.")

    smtp_accounts = get_smtp_accounts(active_only=False)
    active_accounts = [acc for acc in smtp_accounts if acc.get("is_active")]
    total_capacity = sum(acc.get("daily_limit", 80) for acc in active_accounts)
    total_sent_today = sum(acc.get("sent_today", 0) for acc in active_accounts)

    col_h1, col_h2, col_h3, col_h4 = st.columns(4)
    col_h1.metric("Connected Mailboxes", len(smtp_accounts))
    col_h2.metric("Active in Rotation", len(active_accounts))
    col_h3.metric("Daily Sending Quota", f"{total_capacity} emails/day")
    col_h4.metric("Sent Today", f"{total_sent_today} / {total_capacity}")

    with st.expander("➕ Connect New Hostinger Mailbox", expanded=len(smtp_accounts) == 0):
        with st.form("add_smtp_account_form", clear_on_submit=True):
            st.markdown("##### Mailbox Credentials & Limits")
            hc1, hc2 = st.columns(2)
            with hc1:
                new_acc_name = st.text_input("Sender Display Name *", placeholder="e.g. Alex Morgan | Sellomize")
                new_acc_email = st.text_input("Hostinger Email Address *", placeholder="alex@sellomize.com")
                new_acc_pass = st.text_input("Hostinger Webmail / App Password *", type="password", help="The email password configured in Hostinger hPanel.")
            with hc2:
                new_acc_host = st.text_input("SMTP Host", value="smtp.hostinger.com", help="Default: smtp.hostinger.com")
                new_acc_port = st.number_input("SMTP Port (SSL: 465 / STARTTLS: 587)", min_value=1, max_value=65535, value=465, step=1)
                new_acc_limit = st.number_input("Daily Send Limit (per mailbox)", min_value=1, max_value=500, value=80, help="Hostinger allows ~100/hr or up to 500/day. Recommended cold outreach limit: 50-80 per mailbox/day.")

            st.caption("🔒 Credentials are stored locally in your SQLite database.")
            add_acc_submit = st.form_submit_button("Verify & Connect Hostinger Mailbox", type="primary")

            if add_acc_submit:
                if not new_acc_name.strip() or not new_acc_email.strip() or not new_acc_pass.strip():
                    st.error("Display Name, Email Address, and Password are all required.")
                else:
                    with st.spinner(f"Verifying SMTP connection to {new_acc_host}:{new_acc_port}..."):
                        success, test_msg = test_smtp_connection(
                            smtp_host=new_acc_host.strip(),
                            smtp_port=int(new_acc_port),
                            email=new_acc_email.strip(),
                            password=new_acc_pass.strip()
                        )
                    if success:
                        try:
                            acc_id = add_smtp_account(
                                sender_name=new_acc_name.strip(),
                                email=new_acc_email.strip(),
                                password=new_acc_pass.strip(),
                                smtp_host=new_acc_host.strip(),
                                smtp_port=int(new_acc_port),
                                daily_limit=int(new_acc_limit)
                            )
                            st.success(f"✅ Connection verified! Mailbox '{new_acc_email}' successfully connected (ID #{acc_id})!")
                            st.rerun()
                        except Exception as add_err:
                            st.error(f"Failed to save mailbox: {add_err}")
                    else:
                        st.error(f"❌ SMTP Connection Failed: {test_msg}. Please check your Hostinger credentials or port settings.")

    if smtp_accounts:
        st.markdown("##### Configured Mailbox Fleet")
        for acc in smtp_accounts:
            acc_id = acc["id"]
            status_color = "#10B981" if acc["is_active"] else "#6B7280"
            status_text = "ACTIVE" if acc["is_active"] else "INACTIVE"
            sent_today = acc.get("sent_today", 0)
            d_limit = acc.get("daily_limit", 80)
            pct = min(1.0, float(sent_today) / max(1.0, float(d_limit)))

            with st.container():
                st.markdown(f"""
                <div style="background: linear-gradient(135deg, rgba(14, 46, 39, 0.65) 0%, rgba(8, 28, 24, 0.8) 100%); border: 1px solid rgba(255, 255, 255, 0.09); border-radius: 12px; padding: 14px 20px; margin-bottom: 10px; margin-top: 6px; box-shadow: 0 4px 18px rgba(0, 0, 0, 0.25);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span style="background: {'rgba(16, 185, 129, 0.15)' if acc['is_active'] else 'rgba(107, 114, 128, 0.15)'}; color: {status_color}; border: 1px solid {status_color}; font-size: 0.75rem; font-weight: 700; padding: 3px 10px; border-radius: 20px; letter-spacing: 0.5px;">● {status_text}</span>
                            <strong style="color: #FFFFFF; font-size: 1.05rem; letter-spacing: 0.3px;">{acc['email']}</strong>
                            <span style="color: #94A3B8; font-size: 0.88rem;">({acc['sender_name']})</span>
                        </div>
                        <div style="color: #94A3B8; font-size: 0.85rem; background: rgba(0,0,0,0.25); padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.06);">
                            Host: <code style="color: #FF7B4D;">{acc['smtp_host']}:{acc['smtp_port']}</code>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                col_bar, col_act1, col_act2, col_act3 = st.columns([3.5, 1.2, 1.2, 1.2])
                with col_bar:
                    st.progress(pct, text=f"Today: {sent_today} / {d_limit} sent ({d_limit - sent_today} remaining)")
                with col_act1:
                    if st.button("🧪 Test", key=f"test_acc_{acc_id}", help="Verify connection with current credentials"):
                        with st.spinner(f"Testing {acc['email']}..."):
                            ok, msg = test_smtp_connection(acc["smtp_host"], acc["smtp_port"], acc["email"], acc["password"])
                        if ok:
                            st.success(f"Verified: {msg}")
                        else:
                            st.error(f"Failed: {msg}")
                with col_act2:
                    if acc["is_active"]:
                        if st.button("⏸️ Pause", key=f"pause_acc_{acc_id}", help="Temporarily exclude from rotation"):
                            update_smtp_account(acc_id, is_active=False)
                            st.rerun()
                    else:
                        if st.button("▶️ Activate", key=f"act_acc_{acc_id}", help="Include in rotation"):
                            update_smtp_account(acc_id, is_active=True)
                            st.rerun()
                with col_act3:
                    if st.button("🗑️ Delete", key=f"del_acc_{acc_id}", help="Remove mailbox from system"):
                        delete_smtp_account(acc_id)
                        st.success(f"Deleted mailbox #{acc_id}")
                        st.rerun()

    st.markdown("---")

    # ==============================================================================
    # SYSTEM CONFIGURATION FORM (AI, DISPATCH ENGINE, DELAYS, SIGNATURE)
    # ==============================================================================
    st.markdown("### ⚙️ System Settings & AI Providers")
    with st.form("config_form"):
        col_api1, col_api2 = st.columns(2)

        with col_api1:
            st.markdown("#### 🤖 AI Engine & Provider Keys")
            secrets_dict = {}
            try:
                if hasattr(st, "secrets"):
                    for k in ["gemini_api_key", "gcp_project_id", "openai_api_key", "anthropic_api_key"]:
                        try:
                            val = st.secrets.get(k)
                            if val:
                                secrets_dict[k] = val
                        except Exception:
                            pass
            except Exception:
                secrets_dict = {}

            gemini_key = st.text_input(
                "Google Gemini API Key",
                value=current_configs.get("gemini_api_key") or secrets_dict.get("gemini_api_key", "AQ.Ab8RN6JyptGhhfk8w83PSpKVcFpmNJOA7aoEJtiB2BCEEiuwVw"),
                type="password",
                help="Gemini / Vertex API key for gemini models."
            )
            gcp_project = st.text_input(
                "Google Cloud / Vertex Project ID",
                value=current_configs.get("gcp_project_id") or secrets_dict.get("gcp_project_id", "606768026327"),
                help="GCP project ID (projects/606768026327)."
            )
            openai_key = st.text_input(
                "OpenAI API Key",
                value=current_configs.get("openai_api_key") or secrets_dict.get("openai_api_key", ""),
                type="password",
                help="OpenAI API key for gpt-4o, gpt-4o-mini."
            )
            anthropic_key = st.text_input(
                "Anthropic API Key",
                value=current_configs.get("anthropic_api_key") or secrets_dict.get("anthropic_api_key", ""),
                type="password",
                help="Anthropic API key for claude-3-haiku, claude-3-5-sonnet."
            )

            primary_model = st.text_input(
                "Primary Model String",
                value=current_configs.get("primary_model", "gemini/gemini-1.5-flash"),
                help="Format: gemini/gemini-1.5-flash, gpt-4o, claude-3-haiku-20240307, etc."
            )
            fallback_model = st.text_input(
                "Fallback Model String",
                value=current_configs.get("fallback_model", "gpt-4o-mini"),
                help="Fallback model if primary model encounters rate limits or errors."
            )

        with col_api2:
            st.markdown("#### 🚀 Outbound Dispatch Engine")
            current_engine = current_configs.get("dispatch_method", "hostinger_smtp")
            dispatch_engine_choice = st.radio(
                "Primary Outbound Dispatch Engine",
                ["⚡ Hostinger Direct SMTP (Multi-Account Rotation)", "📧 Desktop Microsoft Outlook"],
                index=0 if current_engine == "hostinger_smtp" else 1,
                help="Hostinger Direct SMTP sends without needing Outlook running. Outlook uses local Windows Outlook app."
            )

            st.markdown("#### ⏱️ Anti-Spam Human Delay Throttling")
            st.caption("Randomized delay between consecutive emails to mimic human sending and prevent domain flagging.")
            col_del1, col_del2 = st.columns(2)
            with col_del1:
                min_delay_val = st.number_input(
                    "Min Delay (Seconds)",
                    min_value=5,
                    max_value=300,
                    value=int(current_configs.get("min_delay_seconds", "20")),
                    help="Minimum seconds to wait between dispatches."
                )
            with col_del2:
                max_delay_val = st.number_input(
                    "Max Delay (Seconds)",
                    min_value=10,
                    max_value=600,
                    value=int(current_configs.get("max_delay_seconds", "45")),
                    help="Maximum seconds to wait between dispatches."
                )

            st.markdown("#### 📧 Outlook Fallback Settings")
            sender_email = st.text_input(
                "Designated Outlook Sender Email",
                value=current_configs.get("sender_email", ""),
                placeholder="sales@yourdomain.com",
                help="Only used if dispatch engine is set to Desktop Microsoft Outlook."
            )
            bcc_email = st.text_input(
                "Verification BCC Address",
                value=current_configs.get("bcc_email", ""),
                placeholder="archive@yourdomain.com",
                help="Pre-configured BCC address attached to every outgoing email for verification."
            )

            st.markdown("#### 🛡️ Negative Keywords & Spam Lists")
            negative_keywords_val = st.text_area(
                "Restricted Negative Keywords (comma separated)",
                value=current_configs.get("negative_keywords", "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash"),
                height=65,
                help="Drafts containing any of these words will be automatically tagged as 'Flagged' and require Auto-Rewrite."
            )
            spam_blocklist_val = st.text_area(
                "Restricted Spam Words (comma separated)",
                value=current_configs.get("spam_blocklist", "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast"),
                height=65,
                help="LiteLLM will inject a strict forbidding instruction during prompt execution."
            )

        submit_config = st.form_submit_button("💾 Save System & AI Settings", type="primary", use_container_width=True)

        if submit_config:
            engine_key = "hostinger_smtp" if "Hostinger" in dispatch_engine_choice else "outlook"
            new_configs = {
                "gemini_api_key": gemini_key,
                "gcp_project_id": gcp_project,
                "openai_api_key": openai_key,
                "anthropic_api_key": anthropic_key,
                "primary_model": primary_model,
                "fallback_model": fallback_model,
                "dispatch_method": engine_key,
                "min_delay_seconds": str(min_delay_val),
                "max_delay_seconds": str(max_delay_val),
                "sender_email": sender_email,
                "bcc_email": bcc_email,
                "negative_keywords": negative_keywords_val,
                "spam_blocklist": spam_blocklist_val
            }
            save_all_configs(new_configs)
            st.success("✅ System settings successfully saved!")
            st.rerun()

    # DEDICATED SIGNATURE MANAGEMENT SECTION (Fully interactive outside st.form)
    # ==============================================================================
    st.markdown("---")
    st.markdown("### ✍️ Professional HTML Email Signature")
    st.caption("Paste or customize your HTML email signature. Supports table layouts, inline CSS, hosted logo images, and clickable contact links.")

    shared_sig_key = "sig_shared_content"
    if shared_sig_key not in st.session_state:
        st.session_state[shared_sig_key] = current_configs.get("signature_html", "")

    col_sig_mode, col_sig_template = st.columns([2.2, 1.2])
    with col_sig_mode:
        sig_mode = st.radio(
            "Signature Editor Mode",
            ["HTML Source Code (Recommended for Tables, Logos & Links)", "Visual WYSIWYG Editor"],
            index=0,
            horizontal=True,
            key="sig_editor_mode_selector"
        )
    with col_sig_template:
        st.write("") # vertical spacing
        if st.button("📋 Load Sellomize Signature Template", help="Insert the branded Sellomize signature with logo, orange accent bar, and contact details"):
            sample_sig = """<div>
<table cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif; max-width:650px; color:#073d35;">
  <tbody>
    <tr>
      <td style="padding-right:20px; vertical-align:top;">
        <img src="https://sellomize.com/wp-content/uploads/2026/05/cropped-amazon-aligators.png" width="110" style="display:block;" alt="Sellomize Logo">
        <br>
      </td>
      <td style="padding:0 20px; border-left:2px solid #ff5a1f; vertical-align:top;">
        <div style="font-size:20px; font-weight:bold; color:#073d35;">Jack Connor</div>
        <div style="font-size:14px; color:#ff5a1f; margin:4px 0 8px;">Business Development Officer</div>
        <div style="font-size:14px; line-height:1.7;">
          <div><b>Sellomize</b></div>
          <div>Amazon Brand Management</div>
        </div>
        <div style="margin-top:10px; font-size:14px; line-height:1.7;">
          <div>✉️ <a href="mailto:info@sellomize.com" style="color:#073d35; text-decoration:none;">info@sellomize.com</a></div>
          <div>📞 +1 646-351-0812</div>
          <div>🌐 <a href="https://sellomize.com" target="_blank" style="color:#073d35; text-decoration:none;">sellomize.com</a></div>
        </div>
      </td>
    </tr>
  </tbody>
</table>
</div>"""
            st.session_state[shared_sig_key] = sample_sig
            set_config("signature_html", sample_sig)
            st.success("Loaded Sellomize signature template!")
            st.rerun()

    if sig_mode.startswith("HTML Source Code"):
        st.caption("💻 **Monospace HTML Source Mode**: Paste your raw `<table>`, `<tr>`, `<td>`, `<img src='...'>`, CSS, and links directly below.")
        new_sig = st.text_area(
            "HTML Signature Code",
            value=st.session_state[shared_sig_key],
            height=280,
            help="Paste complete raw HTML table layout, inline styles, images, and links here.",
            key="sig_editor_source"
        )
        st.session_state[shared_sig_key] = new_sig
    else:
        st.caption("✍️ **Visual WYSIWYG Mode**: Format standard rich text.")
        if QUILL_AVAILABLE:
            quill_res = st_quill(
                value=st.session_state[shared_sig_key],
                html=True,
                key="sig_editor_quill"
            )
            if quill_res is not None:
                st.session_state[shared_sig_key] = quill_res
        else:
            new_sig = st.text_area(
                "Visual Rich Text",
                value=st.session_state[shared_sig_key],
                height=220,
                key="sig_editor_fallback"
            )
            st.session_state[shared_sig_key] = new_sig

    col_sig_save, _ = st.columns([1.5, 3.5])
    with col_sig_save:
        if st.button("💾 Save Signature", type="primary", use_container_width=True, key="save_signature_btn"):
            set_config("signature_html", st.session_state[shared_sig_key])
            st.success("✅ Signature successfully saved!")
            st.rerun()

    st.markdown("##### 👁️ Live Rendered Signature Preview")
    st.caption("This is exactly how your signature will appear to prospective clients at the bottom of outgoing emails:")

    current_sig = st.session_state.get(shared_sig_key, "").strip()
    if current_sig:
        st.markdown(
            f"""<div style="background:#FFFFFF; color:#1E293B; padding:22px; border-radius:12px; border:1px solid rgba(255,255,255,0.2); box-shadow:0 6px 24px rgba(0,0,0,0.35); overflow-x:auto;">
                {current_sig}
            </div>""",
            unsafe_allow_html=True
        )
    else:
        st.info("No signature configured yet. Paste your HTML code above or click 'Load Sellomize Signature Template'.")

    st.markdown("---")
    col_outbox_hdr, col_dry_run = st.columns([3, 1])
    with col_outbox_hdr:
        st.markdown("### 📊 Outbox & Dispatch History")
    with col_dry_run:
        dry_run_btn = st.button("🧪 Dry Run (Check Due)", key="dry_run_outbox", help="Check approved emails due for dispatch without sending.")
        if dry_run_btn:
            curr_local_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            due = get_approved_due_emails(curr_local_time)
            if due:
                st.info(f"🔎 Dry Run: Found {len(due)} approved email(s) currently due for dispatch at {curr_local_time} (Local Time).")
            else:
                st.info(f"🔎 Dry Run: 0 approved emails currently due for dispatch at {curr_local_time} (Local Time).")

    outbox_filter = st.selectbox(
        "Filter Outbox by Status",
        ["All", "Approved", "Sent", "Flagged", "Account Mismatch", "Error", "Pending"],
        index=0
    )

    if outbox_filter == "All":
        filtered_outbox = get_emails()
    else:
        filtered_outbox = get_emails(status=outbox_filter)

    if not filtered_outbox:
        st.info("No emails match the selected outbox filter.")
    else:
        for item in filtered_outbox:
            st_class = "badge-pending"
            if item["status"] == "Approved":
                st_class = "badge-approved"
            elif item["status"] == "Sent":
                st_class = "badge-sent"
            elif item["status"] == "Flagged":
                st_class = "badge-flagged"
            elif item["status"] in ["Account Mismatch", "Error"]:
                st_class = "badge-flagged"

            with st.expander(f"#{item['id']} | [{item['status'].upper()}] {item['subject']} -> {item.get('recipient') or 'No Recipient'}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**Status:** <span class='{st_class}'>{item['status']}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Recipient:** `{item.get('recipient')}`")
                    if item.get("sent_via"):
                        st.markdown(f"**Dispatched Via:** `{item['sent_via']}`")
                    st.markdown(f"**Scheduled Send Time:** `{item.get('scheduled_time')}`")
                    st.markdown(f"**Created:** `{item.get('created_at')}`")
                with c2:
                    if item.get("revision_notes"):
                        st.info(f"**Notes / Trigger:** {item['revision_notes']}")
                    if item.get("error_message"):
                        st.error(f"**Error Details:** {item['error_message']}")

                st.markdown("**Email Content Preview:**")
                st.markdown(f'<div class="email-preview-box">{item["email_html"]}</div>', unsafe_allow_html=True)

                if item["status"] in ["Account Mismatch", "Error", "Approved", "Flagged"]:
                    if st.button(f"↩️ Reset #{item['id']} to Pending", key=f"reset_{item['id']}"):
                        update_email(email_id=item["id"], status="Pending", error_message=None)
                        st.success(f"Email #{item['id']} reset to Pending.")
                        st.rerun()
