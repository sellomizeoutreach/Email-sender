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
    get_contacts_by_tag,
    delete_contact,
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
    delete_email
)
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

# Custom Styling (Sellomize Reach Brand Theme)
st.markdown("""
<style>
    /* Global Typography & Palette Constants:
       Primary: #EE5324 (Vibrant Sellomize Orange)
       Background: #0B2621 (Deepest Pine Green)
       Secondary Background: #0F3832 (Forest Green)
       Border Accent: #164E43 (Subtle Slate/Green)
       Text: #FFFFFF (Crisp White)
    */

    /* Branded Header Banner */
    .sellomize-header-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, #0F3832 0%, #164E43 100%);
        border: 1px solid #1B5E52;
        border-radius: 12px;
        padding: 1.1rem 1.6rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
    }
    .sellomize-header-left {
        display: flex;
        align-items: center;
        gap: 1.25rem;
    }
    .sellomize-logo-img {
        height: 58px;
        width: 58px;
        object-fit: cover;
        border-radius: 10px;
        border: 2px solid #EE5324;
        box-shadow: 0 2px 8px rgba(238, 83, 36, 0.35);
    }
    .sellomize-logo-fallback {
        font-size: 2.2rem;
        background: #0B2621;
        border-radius: 10px;
        padding: 6px 12px;
        border: 2px solid #EE5324;
    }
    .sellomize-brand-title {
        font-size: 2.0rem;
        font-weight: 800;
        letter-spacing: 1.2px;
        color: #FFFFFF;
        line-height: 1.1;
        margin: 0;
        font-family: system-ui, -apple-system, sans-serif;
    }
    .sellomize-highlight {
        color: #EE5324;
    }
    .sellomize-brand-subtitle {
        font-size: 0.95rem;
        font-weight: 500;
        color: #A7D7C5;
        margin-top: 4px;
        letter-spacing: 0.4px;
    }
    .sellomize-header-badge {
        background-color: rgba(238, 83, 36, 0.15);
        color: #EE5324;
        border: 1px solid #EE5324;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }

    /* Primary Action Buttons (Generate Campaign, Approve & Schedule, Save All Configurations, etc.) */
    button[kind="primary"],
    [data-testid="baseButton-primary"],
    div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"] {
        background-color: #EE5324 !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 6px !important;
        padding: 0.5rem 1.25rem !important;
        box-shadow: 0 2px 8px rgba(238, 83, 36, 0.35) !important;
        transition: all 0.2s ease-in-out !important;
    }
    button[kind="primary"]:hover,
    [data-testid="baseButton-primary"]:hover,
    div.stButton > button[kind="primary"]:hover,
    div.stFormSubmitButton > button[kind="primary"]:hover {
        background-color: #D4441B !important;
        color: #FFFFFF !important;
        box-shadow: 0 4px 12px rgba(238, 83, 36, 0.5) !important;
        transform: translateY(-1px) !important;
    }

    /* Secondary Actions / Outlines (Custom Reject & Rewrite, Dry Run, Delete, Reset, etc.) */
    button[kind="secondary"],
    [data-testid="baseButton-secondary"],
    .stButton > button:not([kind="primary"]),
    .stDownloadButton > button {
        background-color: #0F3832 !important;
        color: #FFFFFF !important;
        border: 1px solid #164E43 !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease-in-out !important;
    }
    button[kind="secondary"]:hover,
    [data-testid="baseButton-secondary"]:hover,
    .stButton > button:not([kind="primary"]):hover,
    .stDownloadButton > button:hover {
        background-color: #164E43 !important;
        border-color: #EE5324 !important;
        color: #FFFFFF !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3) !important;
    }

    /* Form containers and expanders */
    div[data-testid="stForm"],
    div[data-testid="stExpander"] {
        background-color: #0F3832 !important;
        border: 1px solid #164E43 !important;
        border-radius: 8px !important;
    }

    /* Inputs, textareas, and selectboxes */
    div[data-baseweb="input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="select"] > div {
        background-color: #0B2621 !important;
        border-color: #164E43 !important;
        color: #FFFFFF !important;
    }
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        color: #FFFFFF !important;
        background-color: transparent !important;
    }
    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="textarea"]:focus-within,
    div[data-baseweb="select"]:focus-within > div {
        border-color: #EE5324 !important;
        box-shadow: 0 0 0 1px #EE5324 !important;
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background-color: #0F3832;
        border: 1px solid #164E43;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
    }
    div[data-testid="stMetricLabel"] {
        color: #A7D7C5 !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }

    /* Email Preview Box */
    .email-preview-box {
        background-color: #0F3832;
        border: 1px solid #164E43;
        border-radius: 8px;
        padding: 1.25rem;
        margin-top: 0.5rem;
        margin-bottom: 0.8rem;
        color: #FFFFFF;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
    }
    .email-preview-box p,
    .email-preview-box div,
    .email-preview-box span {
        color: #FFFFFF !important;
    }

    /* Flagged Draft Card */
    .flagged-card {
        background-color: #240C0E;
        border: 2px solid #EF4444;
        border-radius: 8px;
        padding: 1.25rem;
        margin-bottom: 1.2rem;
        color: #FFFFFF;
    }
    .flagged-banner {
        background-color: #EF4444;
        color: #FFFFFF;
        font-weight: 700;
        padding: 6px 12px;
        border-radius: 4px;
        margin-bottom: 10px;
        display: inline-block;
        letter-spacing: 0.5px;
    }

    /* Badges */
    .badge-flagged {
        background-color: #451A1A;
        color: #FCA5A5;
        border: 1px solid #EF4444;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-pending {
        background-color: #3D2B0F;
        color: #FDE68A;
        border: 1px solid #F59E0B;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-approved {
        background-color: #0F3347;
        color: #93C5FD;
        border: 1px solid #3B82F6;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-sent {
        background-color: #0F3D2E;
        color: #86EFAC;
        border: 1px solid #10B981;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }

    /* Syntax Guide Box */
    .syntax-help {
        background-color: #0D332D;
        border: 1px solid #164E43;
        border-left: 4px solid #EE5324;
        padding: 0.9rem 1.1rem;
        border-radius: 6px;
        margin-bottom: 1rem;
        color: #E2E8F0;
    }
    .syntax-help code {
        background-color: #0B2621;
        color: #EE5324;
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid #164E43;
    }

    /* Tabs Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 6px 6px 0 0;
        color: #A7D7C5;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        color: #EE5324 !important;
        border-bottom-color: #EE5324 !important;
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
            <div class="sellomize-brand-subtitle">A Full Service Amazon Agency — Outbound Engine</div>
        </div>
    </div>
    <div class="sellomize-header-badge">
        <span>Agency Outbound Engine</span>
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

col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
col_m1.metric("Saved Leads", len(all_contacts))
col_m2.metric("Templates", len(all_templates))
col_m3.metric("Pending Review", pending_count)
col_m4.metric("🚨 Flagged Drafts", flagged_count, delta="Action Required" if flagged_count > 0 else None, delta_color="inverse")
col_m5.metric("Sent via Outlook", sent_count)

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
            st.markdown("##### Contact Tags")
            existing_tags = get_all_distinct_tags()
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                selected_tags = st.multiselect("Select Existing Tags", options=existing_tags, help="Select one or more existing tags.")
            with col_t2:
                new_tags_raw = st.text_input("Or Add New Tag(s)", placeholder="e.g. Beauty Brands, Listing Audit, Q4 Leads", help="Comma-separated list of new tags.")

            st.markdown("##### Custom Variables (Optional)")
            st.caption("Add key-value pairs (e.g. `Role: CEO`, `Product: Coffee Maker`, `Niche: Home Decor`). These can be injected using `[Role]` or `[Niche]` in templates.")
            custom_vars_raw = st.text_area("Custom Variables JSON", value="{\n  \"Role\": \"Brand Director\",\n  \"Category\": \"E-commerce\"\n}", height=90)

            add_contact_btn = st.form_submit_button("Save Contact", type="primary")

            if add_contact_btn:
                if not c_name.strip() or not c_email.strip():
                    st.error("Name and Email Address are required.")
                else:
                    try:
                        cv_parsed = json.loads(custom_vars_raw) if custom_vars_raw.strip() else {}
                        if not isinstance(cv_parsed, dict):
                            cv_parsed = {}
                    except Exception as json_err:
                        st.warning(f"Invalid JSON format for custom variables; saving as empty. ({json_err})")
                        cv_parsed = {}

                    # Combine multiselect tags and new text tags
                    extra_tags = [t.strip() for t in new_tags_raw.split(",") if t.strip()]
                    combined_tags = list(set(selected_tags + extra_tags))

                    cid, is_new = upsert_contact_by_email(
                        name=c_name,
                        email=c_email,
                        company=c_company,
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
    st.markdown("#### 🔍 Filter Contacts")
    filter_col1, filter_col2 = st.columns([2, 2])
    with filter_col1:
        search_query = st.text_input("Search Leads", placeholder="Search by Name, Email, or Company...", key="crm_search_query")
    with filter_col2:
        distinct_tags = get_all_distinct_tags()
        tag_filter = st.multiselect("Filter by Tag", options=distinct_tags, placeholder="Select one or more tags...", key="crm_tag_filter")

    # Data Table of Filtered Contacts
    filtered_contacts = get_contacts(tags_filter=tag_filter, search_query=search_query)

    # Export Button for Current Filtered View
    if filtered_contacts:
        export_csv_data = export_contacts_to_csv(filtered_contacts)
        st.download_button(
            label=f"📤 Export Filtered Leads ({len(filtered_contacts)} contacts) to CSV",
            data=export_csv_data,
            file_name="contacts_filtered_export.csv",
            mime="text/csv"
        )

    if not filtered_contacts:
        st.info("No contacts match the selected search/tag filter.")
    else:
        st.write(f"Showing **{len(filtered_contacts)}** contact(s):")

        for contact in filtered_contacts:
            with st.container():
                col_c1, col_c2, col_c3, col_c4 = st.columns([2.5, 2.5, 3, 1])
                with col_c1:
                    st.markdown(f"**{contact['name']}**")
                    st.caption(f"ID #{contact['id']} | Added: {contact['created_at']}")
                with col_c2:
                    st.markdown(f"📧 `{contact['email']}`")
                    st.markdown(f"🏢 {contact.get('company') or 'No Company'}")
                with col_c3:
                    # Tag Badges
                    tags_list = contact.get("tags_list") or []
                    if tags_list:
                        tags_html = " ".join([
                            f"<span style='background-color:#164E43;color:#A7D7C5;border:1px solid #1B5E52;font-size:0.8rem;font-weight:600;padding:2px 8px;border-radius:10px;margin-right:4px;'>🏷️ {t}</span>"
                            for t in tags_list
                        ])
                        st.markdown(tags_html, unsafe_allow_html=True)

                    vars_dict = contact.get("custom_variables_dict") or {}
                    if vars_dict:
                        chips = " ".join([f"`[{k}]: {v}`" for k, v in vars_dict.items()])
                        st.markdown(f"**Vars:** {chips}")
                    elif not tags_list:
                        st.caption("No tags or variables")
                with col_c4:
                    if st.button("🗑️ Delete", key=f"del_contact_{contact['id']}"):
                        delete_contact(contact["id"])
                        st.warning(f"Contact #{contact['id']} deleted.")
                        st.rerun()
                st.markdown("<hr style='margin: 0.5rem 0;'>", unsafe_allow_html=True)

# ==============================================================================
# TAB 2: TEMPLATE BUILDER (SPINTAX & VARIABLES)
# ==============================================================================
with tab_templates:
    st.subheader("📝 Template Builder")
    st.caption("Create reusable cold outreach templates with dynamic variable insertion and Spintax variation.")

    # Syntax Guide Box
    st.markdown("""
    <div class="syntax-help">
        <strong>💡 Template Formatting Guide:</strong><br>
        • <strong>Variables:</strong> Use <code>[Name]</code>, <code>[Company]</code>, <code>[Email]</code>, or custom fields like <code>[Role]</code>. They will be automatically replaced with the lead's real CRM data.<br>
        • <strong>Spintax:</strong> Use <code>{variation1|variation2|variation3}</code> syntax. The engine will randomly select one option for every contact to guarantee distinct email copy. Nested Spintax like <code>{Hi|{Good morning|Hello}}</code> is fully supported!
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
    st.caption("Review drafts, resolve flagged negative keywords via Auto-Rewrite, edit copy in Visual/HTML modes, and schedule for Outlook dispatch.")

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
                        help="Flagged emails must have negative keywords removed before approval." if is_flagged else "Approve and schedule for Outlook dispatch."
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
    st.caption("Manage AI provider credentials, negative keywords, spam blocklists, Outlook sender bindings, and monitor dispatch history.")

    current_configs = get_all_configs()

    with st.form("config_form"):
        col_api1, col_api2 = st.columns(2)

        with col_api1:
            st.markdown("### 🤖 LLM Models & API Credentials")
            gemini_key = st.text_input(
                "Google Gemini API Key",
                value=current_configs.get("gemini_api_key", "AQ.Ab8RN6JyptGhhfk8w83PSpKVcFpmNJOA7aoEJtiB2BCEEiuwVw"),
                type="password",
                help="Gemini / Vertex API key for gemini models."
            )
            gcp_project = st.text_input(
                "Google Cloud / Vertex Project ID",
                value=current_configs.get("gcp_project_id", "606768026327"),
                help="GCP project ID (projects/606768026327)."
            )
            openai_key = st.text_input(
                "OpenAI API Key",
                value=current_configs.get("openai_api_key", ""),
                type="password",
                help="OpenAI API key for gpt-4o, gpt-4o-mini."
            )
            anthropic_key = st.text_input(
                "Anthropic API Key",
                value=current_configs.get("anthropic_api_key", ""),
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
            st.markdown("### 📧 Outlook Dispatch Settings")
            sender_email = st.text_input(
                "Designated Sender Email Address",
                value=current_configs.get("sender_email", ""),
                placeholder="sales@yourdomain.com",
                help="Must match an account.SmtpAddress in local desktop Microsoft Outlook."
            )
            bcc_email = st.text_input(
                "Verification BCC Address",
                value=current_configs.get("bcc_email", ""),
                placeholder="archive@yourdomain.com",
                help="Pre-configured BCC address attached to every outgoing email for verification."
            )

            st.markdown("### 🛡️ Negative Keywords & Spam Lists")
            negative_keywords_val = st.text_area(
                "Restricted Negative Keywords (comma separated)",
                value=current_configs.get("negative_keywords", "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash"),
                height=70,
                help="Drafts containing any of these words will be automatically tagged as 'Flagged' and require Auto-Rewrite."
            )
            spam_blocklist_val = st.text_area(
                "Restricted Spam Words (comma separated)",
                value=current_configs.get("spam_blocklist", "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast"),
                height=70,
                help="LiteLLM will inject a strict forbidding instruction during prompt execution."
            )

        st.markdown("---")
        st.markdown("### ✍️ Signature Management (Dual-Mode Editor)")
        st.caption("Customize your HTML signature. Images can be embedded using hosted image URLs (`<img src='...'>`).")

        # Single shared key in st.session_state
        shared_sig_key = "sig_shared_content"
        if shared_sig_key not in st.session_state:
            st.session_state[shared_sig_key] = current_configs.get("signature_html", "")

        sig_mode = st.radio(
            "Signature Editor Mode",
            ["Visual Editor", "HTML Source Code"],
            horizontal=True,
            key="sig_editor_mode_selector"
        )

        if sig_mode == "Visual Editor":
            if QUILL_AVAILABLE:
                st.info("Visual WYSIWYG Mode: Format text directly.")
                quill_res = st_quill(
                    value=st.session_state[shared_sig_key],
                    html=True,
                    key="sig_editor_quill"
                )
                if quill_res is not None:
                    st.session_state[shared_sig_key] = quill_res
            else:
                st.warning("`streamlit-quill` not detected. Fallback editor active.")
                new_sig = st.text_area(
                    "Visual Rich Text",
                    value=st.session_state[shared_sig_key],
                    height=160,
                    key="sig_editor_fallback"
                )
                st.session_state[shared_sig_key] = new_sig
        else:
            st.info("HTML Source Code Mode: Edit raw HTML tags, embedded CSS, or `<img src='...'>`.")
            new_sig = st.text_area(
                "Raw HTML Signature",
                value=st.session_state[shared_sig_key],
                height=160,
                key="sig_editor_source"
            )
            st.session_state[shared_sig_key] = new_sig

        st.markdown("**Signature Preview:**")
        st.markdown(f'<div class="email-preview-box">{st.session_state[shared_sig_key]}</div>', unsafe_allow_html=True)

        submit_config = st.form_submit_button("💾 Save All Configurations", type="primary", use_container_width=True)

        if submit_config:
            new_configs = {
                "gemini_api_key": gemini_key,
                "gcp_project_id": gcp_project,
                "openai_api_key": openai_key,
                "anthropic_api_key": anthropic_key,
                "primary_model": primary_model,
                "fallback_model": fallback_model,
                "sender_email": sender_email,
                "bcc_email": bcc_email,
                "negative_keywords": negative_keywords_val,
                "spam_blocklist": spam_blocklist_val,
                "signature_html": st.session_state[shared_sig_key]
            }
            save_all_configs(new_configs)
            st.success("✅ Configuration successfully saved to SQLite database!")
            st.rerun()

    st.markdown("---")
    col_outbox_hdr, col_dry_run = st.columns([3, 1])
    with col_outbox_hdr:
        st.markdown("### 📊 Outbox & Dispatch History")
    with col_dry_run:
        dry_run_btn = st.button("🧪 Dry Run (Check Due)", key="dry_run_outbox", help="Check approved emails due for dispatch without launching Outlook.")
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
