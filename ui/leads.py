"""
ui/leads.py - Simple, fast Lead Management (CRM) for Sellomize Reach.
Section C4 of Complete Restructure Spec.

Fields: Name, Email, Company, Country/Timezone, Status (5 statuses only), Notes.
Supports quick search, status filtering, CSV import, CSV export, and Compose prefill.
"""

import streamlit as st
import pandas as pd
from typing import List, Dict, Any, Optional

from database import (
    get_contacts,
    get_contact_by_id,
    create_contact,
    update_contact,
    delete_contact,
    upsert_contact_by_email,
    LEAD_STATUSES,
    DB_FILE,
)
from contacts_handler import (
    generate_csv_template,
    export_contacts_to_csv,
    import_contacts_from_csv,
)
from ui.components import render_tab_header, trigger_toast


@st.dialog("➕ Add New Lead")
def render_add_lead_dialog():
    """Modal dialog to add a new lead with exactly the 6 required fields."""
    with st.form("form_add_lead", clear_on_submit=True):
        name = st.text_input("Lead Name *", placeholder="e.g. Elena Rostova")
        email = st.text_input("Email Address *", placeholder="e.g. elena@company.com")
        company = st.text_input("Company", placeholder="e.g. Skinfix")
        country_tz = st.text_input(
            "Country / Timezone",
            placeholder="e.g. US/Eastern, Europe/London, Canada, New York",
            help="Used to schedule sends inside this lead's local business window."
        )
        status = st.selectbox("Status", LEAD_STATUSES, index=0)
        notes = st.text_area("Notes", placeholder="Research notes, observation, or context...")

        submitted = st.form_submit_button("Save Lead", use_container_width=True, type="primary")
        if submitted:
            if not email.strip() or "@" not in email:
                st.error("Please enter a valid email address.")
                return
            new_id = create_contact(
                name=name.strip(),
                email=email.strip(),
                company=company.strip(),
                country_or_timezone=country_tz.strip(),
                status=status,
                notes=notes.strip()
            )
            trigger_toast(f"Lead '{name or email}' created successfully!", icon="✅")
            st.rerun()


@st.dialog("✏️ Edit Lead")
def render_edit_lead_dialog(lead: Dict[str, Any]):
    """Modal dialog to edit an existing lead."""
    with st.form("form_edit_lead"):
        name = st.text_input("Lead Name", value=lead.get("name") or "")
        email = st.text_input("Email Address", value=lead.get("email") or "", disabled=True)
        company = st.text_input("Company", value=lead.get("company") or "")
        country_tz = st.text_input(
            "Country / Timezone",
            value=lead.get("country_or_timezone") or "",
            help="Used to interpret the daily sending window in the lead's local time."
        )
        curr_status = lead.get("status") or "New"
        status_idx = LEAD_STATUSES.index(curr_status) if curr_status in LEAD_STATUSES else 0
        status = st.selectbox("Status", LEAD_STATUSES, index=status_idx)
        notes = st.text_area("Notes", value=lead.get("notes") or "")

        col_save, col_del = st.columns([3, 1])
        with col_save:
            submitted = st.form_submit_button("Update Lead", use_container_width=True, type="primary")
        with col_del:
            delete_btn = st.form_submit_button("🗑️ Delete", use_container_width=True)

        if submitted:
            update_contact(
                contact_id=lead["id"],
                name=name.strip(),
                company=company.strip(),
                country_or_timezone=country_tz.strip(),
                status=status,
                notes=notes.strip()
            )
            trigger_toast(f"Lead '{name or email}' updated successfully!", icon="✅")
            st.rerun()

        if delete_btn:
            delete_contact(lead["id"])
            trigger_toast("Lead deleted successfully.", icon="🗑️")
            st.rerun()


def render_leads_tab(all_contacts: Optional[List[Dict[str, Any]]] = None):
    """Render the simplified 5-status Lead Directory tab."""
    render_tab_header(
        "👥 Lead Directory",
        "Manage targeted prospects with 5 statuses, country/timezone awareness, and standard CSV import/export."
    )

    if all_contacts is None:
        all_contacts = get_contacts()

    # --- TOP ACTIONS: SEARCH, STATUS FILTER, CSV, ADD ---
    c_search, c_filter, c_add = st.columns([2.5, 1.8, 1.2])

    with c_search:
        search_query = st.text_input("🔍 Search Leads", placeholder="Search by name, email, or company...", label_visibility="collapsed")

    with c_filter:
        status_filter = st.selectbox(
            "Filter Status",
            ["All Statuses"] + LEAD_STATUSES,
            label_visibility="collapsed"
        )

    with c_add:
        if st.button("➕ Add Lead", type="primary", use_container_width=True):
            render_add_lead_dialog()

    # CSV Import / Export Toolbar
    with st.expander("📁 Import / Export CSV", expanded=False):
        c_exp1, c_exp2, c_exp3 = st.columns(3)
        with c_exp1:
            st.download_button(
                "📥 Download CSV Template",
                data=generate_csv_template(),
                file_name="sellomize_leads_template.csv",
                mime="text/csv",
                use_container_width=True
            )
        with c_exp2:
            st.download_button(
                "📤 Export All Leads (CSV)",
                data=export_contacts_to_csv(all_contacts),
                file_name="sellomize_leads_export.csv",
                mime="text/csv",
                use_container_width=True
            )
        with c_exp3:
            up_csv = st.file_uploader("Upload CSV", type=["csv"], label_visibility="collapsed", key="uploader_leads_csv")
            if up_csv is not None:
                if st.button("Start CSV Import", use_container_width=True):
                    with st.spinner("Importing leads..."):
                        content = up_csv.read()
                        res = import_contacts_from_csv(content)
                        st.success(f"Import complete: {res['inserted']} inserted, {res['updated']} updated.")
                        if res.get("errors"):
                            st.warning(f"Encountered {len(res['errors'])} row warnings.")
                    st.rerun()

    # --- STATUS PILL STATS ---
    status_counts = {s: 0 for s in LEAD_STATUSES}
    for c in all_contacts:
        st_val = c.get("status") or "New"
        if st_val in status_counts:
            status_counts[st_val] += 1
        else:
            status_counts["New"] += 1

    pill_cols = st.columns(5)
    for idx, st_name in enumerate(LEAD_STATUSES):
        with pill_cols[idx]:
            count = status_counts[st_name]
            st.markdown(
                f"""<div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.14); border-radius:8px; padding:8px 12px; text-align:center;">
                    <div style="font-size:0.75rem; color:#64748B; font-weight:700; text-transform:uppercase;">{st_name}</div>
                    <div style="font-size:1.25rem; font-weight:900; color:#083731;">{count}</div>
                </div>""",
                unsafe_allow_html=True
            )

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

    # --- FILTER DATA ---
    filtered = all_contacts
    if status_filter != "All Statuses":
        filtered = [c for c in filtered if (c.get("status") or "New") == status_filter]

    if search_query.strip():
        q = search_query.strip().lower()
        filtered = [
            c for c in filtered
            if q in (c.get("name") or "").lower()
            or q in (c.get("email") or "").lower()
            or q in (c.get("company") or "").lower()
            or q in (c.get("notes") or "").lower()
        ]

    # --- DISPLAY TABLE ---
    if not filtered:
        st.info("No leads found matching your criteria. Use **➕ Add Lead** or **📁 Import CSV** above to get started.")
        return

    table_rows = []
    for c in filtered:
        table_rows.append({
            "ID": c.get("id"),
            "Name": c.get("name") or "",
            "Email": c.get("email") or "",
            "Company": c.get("company") or "",
            "Country / Timezone": c.get("country_or_timezone") or "LOCAL",
            "Status": c.get("status") or "New",
            "Notes": c.get("notes") or ""
        })

    df = pd.DataFrame(table_rows)

    c_table, c_actions = st.columns([4, 1.2])
    with c_table:
        st.dataframe(
            df[["Name", "Email", "Company", "Country / Timezone", "Status", "Notes"]],
            use_container_width=True,
            hide_index=True
        )

    with c_actions:
        st.markdown("**Quick Actions:**")
        selected_lead_email = st.selectbox(
            "Select Lead Action",
            [f"{r['Name']} ({r['Email']})" for r in table_rows],
            key="leads_quick_action_select"
        )
        if selected_lead_email:
            matching_lead = next((c for c in filtered if f"{c.get('name') or ''} ({c.get('email')})" == selected_lead_email), None)
            if matching_lead:
                if st.button("✏️ Edit Lead", use_container_width=True):
                    render_edit_lead_dialog(matching_lead)
                if st.button("✍️ Compose to Lead", use_container_width=True, type="primary"):
                    st.session_state["prefill_compose_lead_id"] = matching_lead["id"]
                    st.session_state["main_app_tabs"] = "✍️ Compose"
                    st.rerun()
