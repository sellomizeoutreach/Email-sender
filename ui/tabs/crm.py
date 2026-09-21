"""
Leads & Contacts CRM Tab for Sellomize Reach.
Includes contact creation, unified CSV center (import/export/template),
intelligent search/filtering, bulk actions, Excel grid editor, and interactive card view with unified modal editing.
"""

import html
import json
import pandas as pd
import streamlit as st
from database import (
    get_contacts,
    get_all_distinct_tags,
    upsert_contact_by_email,
    update_contact,
    delete_contact,
    bulk_add_tags_to_contacts,
    bulk_set_tags_for_contacts,
    bulk_remove_tags_from_contacts,
    bulk_update_contacts_details,
    bulk_delete_contacts,
    bulk_update_contact_grid,
    parse_variables_from_text,
    format_variables_as_lines
)
from contacts_handler import (
    generate_csv_template,
    export_contacts_to_csv,
    import_contacts_from_csv
)
from mx_checker import batch_verify_contacts_mx


@st.dialog("✏️ Edit & Manage Lead")
def render_edit_contact_dialog(contact: dict):
    """
    Unified contact management modal dialog.
    Puts Update, Edit details, and Delete functions together in the exact same place.
    """
    c_id = contact["id"]
    c_name_val = contact.get("name") or ""
    c_email_val = contact.get("email") or ""
    c_company_val = contact.get("company") or ""
    c_added = contact.get("created_at") or ""

    st.markdown(f"""
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; border-bottom:1px solid rgba(8,55,49,0.15); padding-bottom:8px;">
        <span style="font-weight:800; font-size:1.1rem; color:#083731;">Lead #L-{c_id:04d}</span>
        <span class="timestamp-right" style="margin:0;">Added {c_added[:10]}</span>
    </div>
    """, unsafe_allow_html=True)

    tab_det, tab_vars = st.tabs(["👤 Details & Pipeline", "🏷️ Tags & Variables"])

    with tab_det:
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            e_name = st.text_input("Full Name *", value=c_name_val, key=f"dlg_name_{c_id}")
        with ec2:
            e_email = st.text_input("Email Address *", value=c_email_val, key=f"dlg_email_{c_id}")
        with ec3:
            e_company = st.text_input("Company", value=c_company_val, key=f"dlg_comp_{c_id}")

        ec_r1, ec_r2, ec_r3, ec_r4 = st.columns(4)
        with ec_r1:
            source_opts = ["Website", "Referral", "Cold Outreach", "LinkedIn", "Inbound", "Amazon Store", "Shopify Store", "Other"]
            curr_src = contact.get("lead_source") or "Other"
            src_idx = source_opts.index(curr_src) if curr_src in source_opts else 7
            e_source = st.selectbox("Lead Source", source_opts, index=src_idx, key=f"dlg_src_{c_id}")
        with ec_r2:
            prio_opts = ["High", "Medium", "Low"]
            curr_prio = contact.get("priority") or "Medium"
            prio_idx = prio_opts.index(curr_prio) if curr_prio in prio_opts else 1
            e_priority = st.selectbox("Priority", prio_opts, index=prio_idx, key=f"dlg_prio_{c_id}")
        with ec_r3:
            e_owner = st.text_input("Lead Owner", value=contact.get("owner") or "", key=f"dlg_own_{c_id}")
        with ec_r4:
            stat_opts = ["Not Contacted", "Contacted", "Follow-Up Sent", "Opened / Interested", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"]
            curr_st = contact.get("status") or "Not Contacted"
            st_idx = stat_opts.index(curr_st) if curr_st in stat_opts else 0
            e_status = st.selectbox("Pipeline Status", stat_opts, index=st_idx, key=f"dlg_stat_{c_id}")

        e_notes = st.text_input("Internal Notes", value=contact.get("notes") or "", key=f"dlg_notes_{c_id}")

    with tab_vars:
        all_avail_tags = get_all_distinct_tags(include_predefined=True)
        curr_tags = contact.get("tags_list") or []
        col_et1, col_et2 = st.columns([2, 1])
        with col_et1:
            e_tags = st.multiselect(
                "Tags (Select Predefined / Existing)",
                options=all_avail_tags,
                default=[t for t in curr_tags if t in all_avail_tags],
                key=f"dlg_tags_{c_id}"
            )
        with col_et2:
            e_new_tag = st.text_input("Add Custom Tag(s)", placeholder="e.g. Q4 Audit, Tier 1", key=f"dlg_newtag_{c_id}")

        st.markdown("##### 🧩 Outreach Variables")
        c_cv = contact.get("custom_variables_dict") or {}
        col_ecv1, col_ecv2, col_ecv3 = st.columns(3)
        with col_ecv1:
            e_role = st.text_input("Role / Job Title", value=str(c_cv.get("Role", "")), key=f"dlg_role_{c_id}", help="Accessible via [Role] in email templates")
        with col_ecv2:
            e_web = st.text_input("Website / Store URL", value=str(c_cv.get("Website", "")), key=f"dlg_web_{c_id}", help="Accessible via [Website] in email templates")
        with col_ecv3:
            e_asin = st.text_input("Amazon ASIN / Product ID", value=str(c_cv.get("ASIN", "")), key=f"dlg_asin_{c_id}", help="Accessible via [ASIN] in email templates")

        other_vars = {k: v for k, v in c_cv.items() if k not in ["Role", "Website", "ASIN"]}
        with st.expander("➕ Additional Variables (Key: Value)", expanded=bool(other_vars)):
            e_other_vars_txt = st.text_area(
                "Additional Custom Variables",
                value=format_variables_as_lines(other_vars),
                key=f"dlg_othervars_{c_id}",
                height=65,
                help="One variable per line (e.g. Category: Skincare or Location: NYC). No JSON syntax required!"
            )

    st.markdown("<hr style='margin: 14px 0 16px; opacity: 0.2;'>", unsafe_allow_html=True)

    # Unified Action Toolbar inside Dialog: Update, Delete, and Cancel together
    col_act_save, col_act_del, col_act_close = st.columns([2.2, 1.8, 1])
    with col_act_save:
        if st.button("💾 Save & Update Lead", type="primary", use_container_width=True, key=f"dlg_btn_save_{c_id}"):
            if not e_name.strip() or not e_email.strip():
                st.error("Name and Email are required.")
            else:
                parsed_cv = parse_variables_from_text(e_other_vars_txt)
                if e_role.strip():
                    parsed_cv["Role"] = e_role.strip()
                if e_web.strip():
                    parsed_cv["Website"] = e_web.strip()
                if e_asin.strip():
                    parsed_cv["ASIN"] = e_asin.strip()

                extra_t = [t.strip() for t in e_new_tag.split(",") if t.strip()]
                final_t = list(set(e_tags + extra_t))

                update_contact(
                    contact_id=c_id,
                    name=e_name.strip(),
                    email=e_email.strip(),
                    company=e_company.strip(),
                    tags=final_t,
                    custom_variables=parsed_cv,
                    lead_source=e_source,
                    priority=e_priority,
                    owner=e_owner.strip() if e_owner else None,
                    status=e_status,
                    notes=e_notes.strip() if e_notes else None
                )
                st.session_state["crm_editing_id"] = None
                st.success("Lead updated successfully!")
                st.rerun()

    with col_act_del:
        if st.session_state.get(f"dlg_confirm_del_{c_id}"):
            if st.button("⚠️ Confirm Delete?", key=f"dlg_btn_del_conf_{c_id}", use_container_width=True):
                delete_contact(c_id)
                st.session_state["crm_selected_ids"].discard(c_id)
                st.session_state["crm_editing_id"] = None
                st.session_state[f"dlg_confirm_del_{c_id}"] = False
                st.rerun()
        else:
            if st.button("🗑️ Delete Lead", key=f"dlg_btn_del_{c_id}", use_container_width=True):
                st.session_state[f"dlg_confirm_del_{c_id}"] = True
                st.rerun()

    with col_act_close:
        if st.button("Close", key=f"dlg_btn_close_{c_id}", use_container_width=True):
            st.session_state["crm_editing_id"] = None
            st.session_state[f"dlg_confirm_del_{c_id}"] = False
            st.rerun()


def render_crm_tab(all_contacts=None):
    """Render Tab 1: Leads & Contacts CRM."""
    if all_contacts is None:
        all_contacts = get_contacts()

    st.subheader("👥 Leads & Contacts")
    st.caption("Contact command center: 15-column spreadsheet grid, custom variables dossier, CSV data center, and unified lead editing.")

    # Initialize CRM selection and editing session state
    if "crm_selected_ids" not in st.session_state:
        st.session_state["crm_selected_ids"] = set()
    if "crm_editing_id" not in st.session_state:
        st.session_state["crm_editing_id"] = None

    # Trigger unified edit dialog if a lead is currently selected for editing
    editing_id = st.session_state.get("crm_editing_id")
    if editing_id:
        matching_contact = next((c for c in all_contacts if c["id"] == editing_id), None)
        if matching_contact:
            render_edit_contact_dialog(matching_contact)
        else:
            st.session_state["crm_editing_id"] = None

    # ==============================================================================
    # 🎯 SECTION 1: TOP ACTION HUBS (Add Lead & CSV Data Center in One Place)
    # ==============================================================================
    top_col1, top_col2 = st.columns(2)

    with top_col1:
        with st.expander("➕ Add Single Contact", expanded=len(all_contacts) == 0):
            with st.form("add_contact_form", clear_on_submit=True):
                fc1, fc2, fc3 = st.columns(3)
                with fc1:
                    c_name = st.text_input("Full Name *", placeholder="e.g. Alex Morgan")
                with fc2:
                    c_email = st.text_input("Email Address *", placeholder="alex@company.com")
                with fc3:
                    c_company = st.text_input("Company Name", placeholder="Acme Brands")

                # CRM Classification Fields
                st.markdown("##### 📊 Lead Classification")
                col_crm1, col_crm2, col_crm3, col_crm4 = st.columns(4)
                with col_crm1:
                    c_source = st.selectbox(
                        "Lead Source",
                        ["Website", "Referral", "Cold Outreach", "LinkedIn", "Inbound", "Amazon Store", "Shopify Store", "Other"],
                        index=2
                    )
                with col_crm2:
                    c_priority = st.selectbox(
                        "Priority",
                        ["High", "Medium", "Low"],
                        index=1
                    )
                with col_crm3:
                    c_owner = st.text_input("Lead Owner", placeholder="e.g. Alex M")
                with col_crm4:
                    c_status = st.selectbox(
                        "Pipeline Status",
                        ["Not Contacted", "Contacted", "Follow-Up Sent", "Opened / Interested", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"],
                        index=0
                    )
                c_notes = st.text_input("Internal Notes", placeholder="e.g. Needs Amazon brand listing audit")

                # Tag Management
                st.markdown("##### 🏷️ Tags & Variables")
                all_tags_list = get_all_distinct_tags(include_predefined=True)
                col_t1, col_t2 = st.columns([1.8, 1.2])
                with col_t1:
                    selected_tags = st.multiselect(
                        "Select Tags",
                        options=all_tags_list,
                        help="Choose tags (e.g. Amazon Brand, Shopify DTC, High Priority)."
                    )
                with col_t2:
                    new_tags_raw = st.text_input("Or Add New Tag(s)", placeholder="e.g. Beauty Brands, Q4 Leads")

                col_cv1, col_cv2, col_cv3 = st.columns(3)
                with col_cv1:
                    cv_role = st.text_input("Role / Title", placeholder="e.g. Founder & CEO", help="Accessible via [Role]")
                with col_cv2:
                    cv_website = st.text_input("Website URL", placeholder="e.g. https://brand.com", help="Accessible via [Website]")
                with col_cv3:
                    cv_asin = st.text_input("Product ID / ASIN", placeholder="e.g. B08N5WRWNW", help="Accessible via [ASIN]")

                with st.expander("➕ Additional Variables (Key: Value)", expanded=False):
                    more_vars_raw = st.text_area(
                        "Additional Custom Variables",
                        placeholder="Category: Skincare\nLocation: Austin, TX",
                        height=65
                    )

                if st.form_submit_button("Save New Contact", type="primary"):
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

                        extra_tags = [t.strip() for t in new_tags_raw.split(",") if t.strip()]
                        combined_tags = list(set(selected_tags + extra_tags))

                        cid, is_new = upsert_contact_by_email(
                            name=c_name.strip(),
                            email=c_email.strip(),
                            company=c_company.strip(),
                            tags=combined_tags,
                            custom_variables=cv_parsed,
                            lead_source=c_source,
                            priority=c_priority,
                            owner=c_owner.strip() if c_owner else None,
                            status=c_status,
                            notes=c_notes.strip() if c_notes else None
                        )
                        action_msg = "added" if is_new else "updated (merged tags & details)"
                        st.success(f"✅ Contact '{c_name}' successfully {action_msg} (ID #{cid})!")
                        st.rerun()

    with top_col2:
        with st.expander("📁 CSV Center (Import, Export & Template)", expanded=False):
            tab_imp, tab_exp, tab_tmpl = st.tabs(["⬆️ Import CSV", "📤 Export CSV", "📥 Download Template"])

            with tab_imp:
                st.caption("Upload a spreadsheet to add new contacts or merge tags and variables into existing leads:")
                uploaded_csv = st.file_uploader("Upload Leads CSV File", type=["csv"], key="contact_csv_uploader")
                verify_mx_import = st.checkbox(
                    "🛡️ Perform Pre-Flight MX & Domain Verification",
                    value=True,
                    help="Validates domain mail exchangers (MX) during import to detect dead domains before dispatch."
                )
                if uploaded_csv is not None:
                    if st.button("Process & Import CSV", type="primary", use_container_width=True):
                        with st.spinner("Processing CSV and validating domains..."):
                            import_stats = import_contacts_from_csv(uploaded_csv.getvalue(), verify_mx=verify_mx_import)
                            if import_stats["errors"]:
                                for err in import_stats["errors"][:5]:
                                    st.error(err)
                            st.success(
                                f"🎉 Successfully imported {import_stats['total']} contact(s): "
                                f"{import_stats['inserted']} new, {import_stats['updated']} updated!"
                            )
                            if import_stats.get("invalid_mx", 0) > 0:
                                st.warning(f"⚠️ {import_stats['invalid_mx']} lead(s) failed MX check and were tagged 'Invalid MX'.")
                            st.rerun()

            with tab_exp:
                st.caption("Export your current contact database with all tags, statuses, and custom variables:")
                export_data = export_contacts_to_csv(all_contacts)
                st.download_button(
                    label=f"📤 Download All ({len(all_contacts)}) Leads as CSV",
                    data=export_data,
                    file_name="sellomize_contacts_export.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="btn_csv_export_center"
                )

            with tab_tmpl:
                st.caption("Download our pre-formatted blank template with standard columns (`Name`, `Email`, `Company`, `Tags`, `Custom_Variables`):")
                template_csv = generate_csv_template()
                st.download_button(
                    label="📥 Download Starter CSV Template",
                    data=template_csv,
                    file_name="contacts_template.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="btn_csv_tmpl_center"
                )

    st.markdown("---")

    # ==============================================================================
    # 🔍 SECTION 2: SEARCH, FILTERS & VIEW MODE CONTROLS (Compact Single Bar)
    # ==============================================================================
    distinct_tags = get_all_distinct_tags(include_predefined=True)
    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2.2, 1.4, 1.4, 1.5])
    with filter_col1:
        search_query = st.text_input("🔍 Search Leads", placeholder="Search by Name, Email, Company, Owner, Notes...", key="crm_search_query")
    with filter_col2:
        tag_filter = st.multiselect("Filter by Tag", options=distinct_tags, placeholder="All tags...", key="crm_tag_filter")
    with filter_col3:
        status_filter_choice = st.selectbox(
            "Filter by Pipeline",
            ["-- All Statuses --", "Not Contacted", "Contacted", "Follow-Up Sent", "Opened / Interested", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"],
            key="crm_status_filter_choice"
        )
    with filter_col4:
        crm_layout_mode = st.radio(
            "Display Mode",
            ["📊 Spreadsheet Grid", "🗂️ Detailed Cards"],
            horizontal=True,
            key="crm_display_layout_mode"
        )

    active_status_filter = None if status_filter_choice == "-- All Statuses --" else status_filter_choice
    filtered_contacts = get_contacts(tags_filter=tag_filter, search_query=search_query, status_filter=active_status_filter)

    filtered_ids = [c["id"] for c in filtered_contacts]
    st.session_state["crm_selected_ids"] = st.session_state["crm_selected_ids"].intersection(set(filtered_ids))
    selected_ids = st.session_state["crm_selected_ids"]
    s_count = len(selected_ids)

    # Selection Toolbar & Counter Bar
    if filtered_contacts:
        sel_c1, sel_c2, sel_c3 = st.columns([1.2, 1.2, 3.6])
        with sel_c1:
            if st.button(f"☑️ Select All ({len(filtered_contacts)})", key="btn_sel_all_crm", use_container_width=True):
                st.session_state["crm_selected_ids"] = set(filtered_ids)
                st.rerun()
        with sel_c2:
            if st.button("⬜ Clear Selection", key="btn_clear_sel_crm", use_container_width=True):
                st.session_state["crm_selected_ids"] = set()
                st.rerun()
        with sel_c3:
            if s_count > 0:
                st.markdown(f"<div style='padding:7px 14px; background:rgba(8,55,49,0.08); border:1.5px solid #083731; border-radius:8px; color:#083731; font-weight:700;'>📌 {s_count} of {len(filtered_contacts)} lead(s) selected</div>", unsafe_allow_html=True)
            else:
                st.caption(f"Displaying **{len(filtered_contacts)}** of **{len(all_contacts)}** total contacts.")

    # ==============================================================================
    # ⚡ SECTION 3: BULK ACTIONS COMMAND CENTER (Appears when 1+ contacts selected)
    # ==============================================================================
    if s_count > 0:
        with st.container():
            st.markdown(f"""
            <div style="background: #FFFFFF; border: 2px solid #083731; border-radius: 14px; padding: 14px 18px; margin: 10px 0 16px; box-shadow: 0 4px 16px rgba(8, 55, 49, 0.08);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="font-size:1.05rem; font-weight:800; color:#083731; letter-spacing:0.4px;">⚡ BULK ACTIONS <span style="color:#083731; font-weight:700;">({s_count} Leads Selected)</span></div>
                    <div style="font-size:0.82rem; color:#64748B;">Apply tag updates, edit details, audit domains, or delete selected contacts in 1 click</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            bulk_tab_tag, bulk_tab_details, bulk_tab_mx, bulk_tab_delete = st.tabs([
                "🏷️ Bulk Tag Management",
                "✏️ Bulk Edit Details & Variables",
                "🛡️ Pre-Flight MX Verification",
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
                    bulk_chosen_tags = st.multiselect("Select Existing Tags", options=all_avail_tags, key="bulk_tags_multisel")
                    bulk_custom_tag = st.text_input("Or Type New Tag", placeholder="e.g. Q4 Audit Target", key="bulk_custom_tag")

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

            with bulk_tab_mx:
                st.markdown("##### 🛡️ Verify Domain Mail Exchangers (MX) for Selected Leads")
                st.caption("Validates domain DNS records for selected contacts to catch dead domains or typos before dispatch.")
                if st.button(f"🔍 Run Pre-Flight MX Audit on {s_count} Selected Leads", type="primary", key="btn_bulk_audit_mx"):
                    with st.spinner("Auditing domain mail exchangers..."):
                        selected_contacts_list = [c for c in filtered_contacts if c["id"] in selected_ids]
                        mx_res = batch_verify_contacts_mx(selected_contacts_list, update_db=True)
                        if mx_res["invalid_count"] > 0:
                            st.warning(f"⚠️ {mx_res['invalid_count']} lead(s) failed MX verification and were tagged 'Invalid MX' in SQLite.")
                            for inv in mx_res["invalid_contacts"][:8]:
                                st.write(f"- 🔴 `{inv['email']}`: {inv['reason']}")
                        else:
                            st.success(f"🎉 All {mx_res['valid_count']} selected lead(s) have active MX mail exchangers!")
                        st.rerun()

            with bulk_tab_delete:
                st.error(f"⚠️ Caution: This will permanently delete {s_count} selected contact(s) from your database.")
                confirm_del = st.checkbox(f"Yes, permanently delete these {s_count} contact(s)", key="confirm_bulk_del")
                if confirm_del:
                    if st.button(f"🗑️ Confirm Delete {s_count} Contacts", key="btn_confirm_bulk_del"):
                        del_num = bulk_delete_contacts(list(selected_ids))
                        st.session_state["crm_selected_ids"] = set()
                        st.success(f"Deleted {del_num} contact(s).")
                        st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

    # ==============================================================================
    # 🗃️ SECTION 4: MAIN WORKSPACE (Spreadsheet Grid vs Card View)
    # ==============================================================================
    if not all_contacts:
        st.info("No contacts in database yet. Add a single lead or import a CSV using the panels above to get started.")
    elif not filtered_contacts:
        st.info("No contacts match the current search or filter criteria. Adjust or reset your filters above.")
    elif crm_layout_mode.startswith("📊 Spreadsheet"):
        # ==============================================================================
        # 📊 SPREADSHEET GRID VIEW (st.data_editor with dropdowns)
        # ==============================================================================
        grid_rows = []
        for c in filtered_contacts:
            grid_rows.append({
                "id": c["id"],
                "Lead ID": f"L-{c['id']:04d}",
                "Company": c.get("company") or "",
                "Contact Name": c.get("name") or "",
                "Email Address": c.get("email") or "",
                "Lead Source": c.get("lead_source") or "Other",
                "Priority": c.get("priority") or "Medium",
                "Contacted?": c.get("contacted") or "No",
                "Date First Emailed": c.get("date_first_emailed") or "",
                "Status": c.get("status") or "Not Contacted",
                "Follow-Ups Sent": int(c.get("follow_ups_sent") if c.get("follow_ups_sent") is not None else 0),
                "Last Contact Date": c.get("last_contact_date") or "",
                "Next Follow-Up": c.get("next_follow_up") or "",
                "Owner": c.get("owner") or "",
                "Notes": c.get("notes") or "",
                "Tags": c.get("tags") or ", ".join(c.get("tags_list", []))
            })

        df_grid = pd.DataFrame(grid_rows)

        # Column Visibility Controls
        all_grid_cols = [
            "Lead ID", "Company", "Contact Name", "Email Address", "Lead Source",
            "Priority", "Contacted?", "Date First Emailed", "Status", "Follow-Ups Sent",
            "Last Contact Date", "Next Follow-Up", "Owner", "Notes", "Tags"
        ]
        default_outreach_cols = [
            "Lead ID", "Company", "Contact Name", "Email Address",
            "Priority", "Status", "Follow-Ups Sent", "Next Follow-Up"
        ]

        col_v1, col_v2 = st.columns([1.6, 2.4])
        with col_v1:
            col_preset = st.radio(
                "Grid Column View",
                ["Focused Outreach View (8 cols)", "All Columns (15 cols)", "Custom Columns"],
                horizontal=True,
                key="crm_col_preset"
            )
        with col_v2:
            if col_preset.startswith("Custom"):
                visible_cols = st.multiselect(
                    "Visible Columns",
                    options=all_grid_cols,
                    default=default_outreach_cols,
                    key="crm_custom_cols"
                )
            elif col_preset.startswith("Focused"):
                visible_cols = default_outreach_cols
                st.caption("Showing 8 essential outreach columns. Select 'All Columns' or 'Custom' to expand.")
            else:
                visible_cols = all_grid_cols
                st.caption("Showing all 15 lead columns. Scroll horizontally to browse fields on the right.")

        st.markdown(f"""
        <div class="crm-scroll-caption">
            <span>↔️ Scroll horizontally to browse columns</span>
            <span>Displaying <b>{len(visible_cols)}</b> of 15 columns</span>
        </div>
        """, unsafe_allow_html=True)

        column_config = {
            "id": None,
            "Lead ID": st.column_config.TextColumn("Lead ID", disabled=True, width="small") if "Lead ID" in visible_cols else None,
            "Company": st.column_config.TextColumn("Company", width="medium") if "Company" in visible_cols else None,
            "Contact Name": st.column_config.TextColumn("Contact Name", width="medium", required=True) if "Contact Name" in visible_cols else None,
            "Email Address": st.column_config.TextColumn("Email Address", width="medium", required=True) if "Email Address" in visible_cols else None,
            "Lead Source": st.column_config.SelectboxColumn(
                "Lead Source",
                options=["Website", "Referral", "Cold Outreach", "LinkedIn", "Inbound", "Amazon Store", "Shopify Store", "Other"],
                width="medium"
            ) if "Lead Source" in visible_cols else None,
            "Priority": st.column_config.SelectboxColumn(
                "Priority",
                options=["High", "Medium", "Low"],
                width="small"
            ) if "Priority" in visible_cols else None,
            "Contacted?": st.column_config.SelectboxColumn(
                "Contacted?",
                options=["Yes", "No"],
                width="small"
            ) if "Contacted?" in visible_cols else None,
            "Date First Emailed": st.column_config.TextColumn("Date First Emailed", width="small") if "Date First Emailed" in visible_cols else None,
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=["Not Contacted", "Contacted", "Follow-Up Sent", "Opened / Interested", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"],
                width="medium"
            ) if "Status" in visible_cols else None,
            "Follow-Ups Sent": st.column_config.NumberColumn("Follow-Ups Sent", min_value=0, max_value=20, step=1, width="small") if "Follow-Ups Sent" in visible_cols else None,
            "Last Contact Date": st.column_config.TextColumn("Last Contact Date", width="small") if "Last Contact Date" in visible_cols else None,
            "Next Follow-Up": st.column_config.TextColumn("Next Follow-Up", width="small") if "Next Follow-Up" in visible_cols else None,
            "Owner": st.column_config.TextColumn("Owner", width="small") if "Owner" in visible_cols else None,
            "Notes": st.column_config.TextColumn("Notes", width="large") if "Notes" in visible_cols else None,
            "Tags": st.column_config.TextColumn("Tags", width="medium") if "Tags" in visible_cols else None,
        }

        edited_grid = st.data_editor(
            df_grid,
            column_config=column_config,
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            key="crm_spreadsheet_editor"
        )

        has_unsaved_edits = False
        editor_state = st.session_state.get("crm_spreadsheet_editor")
        if editor_state and isinstance(editor_state, dict):
            if editor_state.get("edited_rows"):
                has_unsaved_edits = True

        col_save_grid, col_reset_grid, _ = st.columns([2, 2, 3])
        with col_save_grid:
            if st.button("💾 Save Spreadsheet Changes", type="primary", use_container_width=True, key="btn_save_crm_spreadsheet"):
                records_to_save = edited_grid.to_dict(orient="records")
                saved_count = bulk_update_contact_grid(records_to_save)
                st.success(f"✅ Successfully saved changes to {saved_count} contact(s)!")
                st.rerun()
        with col_reset_grid:
            if has_unsaved_edits:
                if st.button("🔄 Discard Unsaved Changes", use_container_width=True, key="btn_reset_crm_spreadsheet"):
                    if "crm_spreadsheet_editor" in st.session_state:
                        del st.session_state["crm_spreadsheet_editor"]
                    st.rerun()

    else:
        # ==============================================================================
        # 🗂️ DETAILED CARD VIEW (Clean, Unified Edit & Delete in the same place)
        # ==============================================================================
        for contact in filtered_contacts:
            c_id = contact["id"]
            is_selected = (c_id in selected_ids)

            with st.container():
                col_chk, col_c1, col_c2, col_c3, col_c4 = st.columns([0.45, 2.3, 2.3, 3.1, 1.85])
                with col_chk:
                    checked = st.checkbox(f"Select contact #{c_id}", key=f"sel_c_{c_id}", value=is_selected, label_visibility="collapsed")
                    if checked != is_selected:
                        if checked:
                            st.session_state["crm_selected_ids"].add(c_id)
                        else:
                            st.session_state["crm_selected_ids"].discard(c_id)
                        st.rerun()

                with col_c1:
                    st.markdown(f"**{contact['name']}**")
                    st.caption(f"Lead ID: `L-{c_id:04d}`")
                    if contact.get("owner"):
                        st.caption(f"👤 Owner: {contact['owner']}")

                with col_c2:
                    st.markdown(f"📧 `{contact['email']}`")
                    st.markdown(f"🏢 {contact.get('company') or 'No Company'}")
                    p_status = contact.get("status") or "Not Contacted"
                    status_color = "#A78BFA" if p_status == "Replied" else ("#34D399" if p_status == "Contacted" else ("#60A5FA" if "Opened" in p_status else ("#F87171" if p_status == "Bounced" else "#94A3B8")))
                    st.markdown(f"<span style='font-size:0.8rem; font-weight:700; color:{status_color};'>● {p_status}</span> (Sent: {contact.get('follow_ups_sent', 0)})", unsafe_allow_html=True)

                with col_c3:
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
                            elif "replied" in t.lower():
                                bg = "rgba(168, 85, 247, 0.16)"
                                color = "#C084FC"
                                border = "rgba(168, 85, 247, 0.45)"
                            elif any(w in t.lower() for w in ["amazon", "shopify", "ecommerce", "brand"]):
                                bg = "rgba(59, 130, 246, 0.14)"
                                color = "#60A5FA"
                                border = "rgba(59, 130, 246, 0.35)"
                            elif any(w in t.lower() for w in ["bounced", "do not", "stop", "unsub"]):
                                bg = "rgba(239, 68, 68, 0.14)"
                                color = "#F87171"
                                border = "rgba(239, 68, 68, 0.35)"
                            tags_html += f"<span style='background:{bg}; color:{color}; border:1px solid {border}; font-size:0.78rem; font-weight:700; padding:2px 8px; border-radius:6px; margin-right:4px; display:inline-block;'>🏷️ {html.escape(str(t))}</span> "
                        st.markdown(tags_html, unsafe_allow_html=True)

                    vars_dict = contact.get("custom_variables_dict") or {}
                    if vars_dict:
                        var_badges = []
                        for k, v in vars_dict.items():
                            k_lower = str(k).lower()
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
                            var_badges.append(f"<span style='background:rgba(255,255,255,0.06); color:#CBD5E1; border:1px solid rgba(255,255,255,0.12); font-size:0.75rem; padding:2px 7px; border-radius:5px; margin-right:4px; display:inline-block;'>{icon} <b>[{html.escape(str(k))}]</b>: {html.escape(str(v))}</span> ")
                        st.markdown("".join(var_badges), unsafe_allow_html=True)

                    if contact.get("notes"):
                        st.caption(f"📝 {contact['notes']}")

                # Far Right Column: Low-opacity timestamp + Unified Edit and Delete buttons in the same place
                with col_c4:
                    c_added = contact.get("created_at") or ""
                    if c_added:
                        st.markdown(
                            f"<div class='timestamp-right' style='margin-bottom:6px;' title='Added: {c_added}'>Added {c_added[:10]}</div>",
                            unsafe_allow_html=True
                        )

                    col_btn_edit, col_btn_del = st.columns([2.5, 1])
                    with col_btn_edit:
                        if st.button("✏️ Edit & Manage", key=f"edit_btn_{c_id}", use_container_width=True):
                            st.session_state["crm_editing_id"] = c_id
                            st.rerun()

                    with col_btn_del:
                        if st.session_state.get(f"confirm_del_c_{c_id}"):
                            if st.button("Confirm", key=f"del_conf_{c_id}", use_container_width=True):
                                delete_contact(c_id)
                                st.session_state["crm_selected_ids"].discard(c_id)
                                st.session_state[f"confirm_del_c_{c_id}"] = False
                                st.warning(f"Contact #{c_id} deleted.")
                                st.rerun()
                        else:
                            if st.button("🗑️", key=f"del_contact_{c_id}", use_container_width=True, help="Delete this contact"):
                                st.session_state[f"confirm_del_c_{c_id}"] = True
                                st.rerun()

                st.markdown("<hr style='margin: 0.4rem 0; opacity: 0.15;'>", unsafe_allow_html=True)
