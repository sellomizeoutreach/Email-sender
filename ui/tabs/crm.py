"""
Leads & Contacts CRM Tab for Sellomize Reach.
Includes contact creation, unified CSV center (import/export/template),
intelligent search/filtering, bulk actions, Excel grid editor, and interactive card view with unified modal editing.
"""

from datetime import datetime
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
    get_system_excluded_emails,
    parse_variables_from_text,
    format_variables_as_lines
)
from contacts_handler import (
    generate_csv_template,
    export_contacts_to_csv,
    import_contacts_from_csv
)
from mx_checker import batch_verify_contacts_mx
from ui.components import render_tab_header, trigger_toast


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
            stat_opts = ["Not Contacted", "Contacted", "Follow-Up Sent", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"]
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

    col_out1, col_out2 = st.columns(2)
    with col_out1:
        if st.button("✉️ Send One-Time Email", key=f"dlg_btn_outreach_once_{c_id}", use_container_width=True, help="Compose and send a single one-off email directly to this contact"):
            st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
            st.session_state["camp_audience_mode"] = "👤 Single Contact (1-to-1 Sequence / Direct Outreach)"
            st.session_state["camp_single_contact_picker"] = c_id
            st.session_state["camp_seq_touches"] = "Once (1 Email - Single Touch)"
            st.session_state["crm_editing_id"] = None
            st.rerun()
    with col_out2:
        if st.button("⚡ Multi-Touch Sequence", key=f"dlg_btn_outreach_seq_{c_id}", use_container_width=True, help="Create an automated multi-step sequence with follow-ups for this contact"):
            st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
            st.session_state["camp_audience_mode"] = "👤 Single Contact (1-to-1 Sequence / Direct Outreach)"
            st.session_state["camp_single_contact_picker"] = c_id
            st.session_state["camp_seq_touches"] = "Twice (2 Emails - Initial Pitch + 1 Follow-Up)"
            st.session_state["crm_editing_id"] = None
            st.rerun()

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
                trigger_toast("Lead updated successfully!", icon="👤")
                st.rerun()

    with col_act_del:
        if st.session_state.get(f"dlg_confirm_del_{c_id}"):
            if st.button("⚠️ Confirm Delete?", key=f"dlg_btn_del_conf_{c_id}", use_container_width=True):
                delete_contact(c_id)
                st.session_state["crm_selected_ids"].discard(c_id)
                st.session_state["crm_editing_id"] = None
                st.session_state[f"dlg_confirm_del_{c_id}"] = False
                trigger_toast("Lead deleted successfully.", icon="🗑️")
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

    system_excluded_emails = get_system_excluded_emails()

    render_tab_header("👥 Leads & Contacts", "Contact command center: 15-column spreadsheet grid, custom variables dossier, CSV data center, and unified lead editing.")

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
    # 🌟 ZERO-DATA ONBOARDING EMPTY STATE
    # ==============================================================================
    if not all_contacts:
        st.markdown("""
        <div style="background:#F8FAFC; border:2px dashed #94A3B8; border-radius:12px; padding:36px 24px; text-align:center; margin: 16px 0 24px;">
            <div style="font-size:2.8rem; margin-bottom:10px;">👋</div>
            <div style="font-size:1.35rem; font-weight:800; color:#083731;">Your CRM is empty. Let's add some leads.</div>
            <div style="font-size:0.92rem; color:#475569; max-width:560px; margin:8px auto 20px; line-height:1.5;">
                Jumpstart your cold outreach pipeline by importing a CSV contact list with pre-flight domain MX verification, or add your first lead manually.
            </div>
        </div>
        """, unsafe_allow_html=True)

        tab_onboard_csv, tab_onboard_add = st.tabs(["📥 Import CSV", "➕ Add First Lead"])
        with tab_onboard_csv:
            st.caption("Upload a spreadsheet to import contacts into Sellomize Reach with tags, variables, and MX verification:")
            up_csv = st.file_uploader("Upload Leads CSV File", type=["csv"], key="onboard_csv_uploader")
            chk_mx = st.checkbox("🛡️ Perform Pre-Flight MX & Domain Verification", value=True, key="onboard_mx_chk")
            col_ob_act1, col_ob_act2 = st.columns([1.5, 1.5])
            with col_ob_act1:
                if up_csv is not None:
                    if st.button("Process & Import CSV", type="primary", use_container_width=True, key="btn_onboard_import_csv"):
                        with st.spinner("Processing CSV and validating domains..."):
                            stats = import_contacts_from_csv(up_csv.getvalue(), verify_mx=chk_mx)
                            if stats["errors"]:
                                for err in stats["errors"][:5]:
                                    st.error(err)
                            trigger_toast(f"Imported {stats['total']} contacts ({stats['inserted']} new)!", icon="🎉")
                            st.rerun()
            with col_ob_act2:
                tmpl_csv = generate_csv_template()
                st.download_button(
                    label="📥 Download Starter CSV Template",
                    data=tmpl_csv,
                    file_name="contacts_template.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="btn_onboard_download_tmpl"
                )

        with tab_onboard_add:
            with st.form("onboard_add_contact_form", clear_on_submit=True):
                fc1, fc2, fc3 = st.columns(3)
                with fc1:
                    c_name = st.text_input("Full Name *", placeholder="e.g. Alex Morgan", key="onboard_name")
                with fc2:
                    c_email = st.text_input("Email Address *", placeholder="alex@company.com", key="onboard_email")
                with fc3:
                    c_company = st.text_input("Company Name", placeholder="Acme Brands", key="onboard_company")

                st.markdown("##### 📊 Lead Classification")
                col_crm1, col_crm2, col_crm3, col_crm4 = st.columns(4)
                with col_crm1:
                    c_source = st.selectbox(
                        "Lead Source",
                        ["Website", "Referral", "Cold Outreach", "LinkedIn", "Inbound", "Amazon Store", "Shopify Store", "Other"],
                        index=2,
                        key="onboard_source"
                    )
                with col_crm2:
                    c_priority = st.selectbox(
                        "Priority",
                        ["High", "Medium", "Low"],
                        index=1,
                        key="onboard_prio"
                    )
                with col_crm3:
                    c_owner = st.text_input("Lead Owner", placeholder="e.g. Alex M", key="onboard_owner")
                with col_crm4:
                    c_status = st.selectbox(
                        "Pipeline Status",
                        ["Not Contacted", "Contacted", "Follow-Up Sent", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"],
                        index=0,
                        key="onboard_status"
                    )
                c_notes = st.text_input("Internal Notes", placeholder="e.g. Needs Amazon brand listing audit", key="onboard_notes")

                st.markdown("##### 🏷️ Tags & Variables")
                all_tags_list = get_all_distinct_tags(include_predefined=True)
                col_t1, col_t2 = st.columns([1.8, 1.2])
                with col_t1:
                    selected_tags = st.multiselect(
                        "Select Tags",
                        options=all_tags_list,
                        help="Choose tags (e.g. Amazon Brand, Shopify DTC, High Priority).",
                        key="onboard_tags"
                    )
                with col_t2:
                    new_tags_raw = st.text_input("Or Add New Tag(s)", placeholder="e.g. Beauty Brands, Q4 Leads", key="onboard_new_tags")

                col_cv1, col_cv2, col_cv3 = st.columns(3)
                with col_cv1:
                    cv_role = st.text_input("Role / Title", placeholder="e.g. Founder & CEO", help="Accessible via [Role]", key="onboard_cv_role")
                with col_cv2:
                    cv_website = st.text_input("Website URL", placeholder="e.g. https://brand.com", help="Accessible via [Website]", key="onboard_cv_web")
                with col_cv3:
                    cv_asin = st.text_input("Product ID / ASIN", placeholder="e.g. B08N5WRWNW", help="Accessible via [ASIN]", key="onboard_cv_asin")

                with st.expander("➕ Additional Variables (Key: Value)", expanded=False):
                    more_vars_raw = st.text_area(
                        "Additional Custom Variables",
                        placeholder="Category: Skincare\nLocation: Austin, TX",
                        height=65,
                        key="onboard_more_vars"
                    )

                if st.form_submit_button("Save First Contact", type="primary"):
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
                        trigger_toast(f"Contact '{c_name}' successfully added (ID #{cid})!", icon="✅")
                        st.rerun()
        return

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
                        ["Not Contacted", "Contacted", "Follow-Up Sent", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"],
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
                        trigger_toast(f"Contact '{c_name}' successfully {action_msg} (ID #{cid})!", icon="✅")
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
                            trigger_toast(
                                f"Imported {import_stats['total']} contacts: {import_stats['inserted']} new, {import_stats['updated']} updated!",
                                icon="🎉"
                            )
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
    # 🔍 SECTION 2: SMART SEGMENT BAR & CLEAN FILTER ROW
    # ==============================================================================
    total_cnt = len(all_contacts)
    ready_cnt = sum(1 for c in all_contacts if c.get("status") in ["Not Contacted", None, ""] and not c.get("is_bounced") and c.get("status") not in ["Bounced", "Do Not Contact", "Closed Lost"])
    today_str = datetime.now().strftime("%Y-%m-%d")
    due_cnt = sum(1 for c in all_contacts if c.get("status") in ["Contacted", "Follow-Up Sent"] or (c.get("next_follow_up") and str(c.get("next_follow_up"))[:10] <= today_str))
    replied_cnt = sum(1 for c in all_contacts if c.get("status") == "Replied" or c.get("last_reply_at"))
    bounced_cnt = sum(1 for c in all_contacts if c.get("status") in ["Bounced", "Do Not Contact", "Closed Lost"] or c.get("is_bounced"))

    segment_labels = [
        f"All Leads ({total_cnt})",
        f"Ready for Outreach ({ready_cnt})",
        f"Follow-Up Due ({due_cnt})",
        f"Replied ({replied_cnt})",
        f"Bounced / Inactive ({bounced_cnt})"
    ]

    selected_segment = st.pills(
        "Smart Lead Segments",
        segment_labels,
        default=segment_labels[0],
        key="crm_smart_segments",
        label_visibility="collapsed"
    )
    if not selected_segment:
        selected_segment = segment_labels[0]

    # Clean Filter Row: Search (col1), Tag Filter (col2), Lead Source (col3)
    distinct_tags = get_all_distinct_tags(include_predefined=True)
    filter_col1, filter_col2, filter_col3 = st.columns([2, 1, 1])
    with filter_col1:
        search_query = st.text_input("🔍 Search Leads", placeholder="Search by Name, Email, Company, Owner, Notes...", key="crm_search_query")
    with filter_col2:
        tag_filter = st.multiselect("Filter by Tag", options=distinct_tags, placeholder="All tags...", key="crm_tag_filter")
    with filter_col3:
        source_options = ["-- All Sources --", "Website", "Referral", "Cold Outreach", "LinkedIn", "Inbound", "Amazon Store", "Shopify Store", "Other"]
        selected_source = st.selectbox("Lead Source", options=source_options, key="crm_source_filter")

    # Fetch and filter
    base_filtered = get_contacts(tags_filter=tag_filter, search_query=search_query)
    if selected_source != "-- All Sources --":
        base_filtered = [c for c in base_filtered if (c.get("lead_source") or "Other") == selected_source]

    if "Ready for Outreach" in selected_segment:
        filtered_contacts = [c for c in base_filtered if c.get("status") in ["Not Contacted", None, ""] and not c.get("is_bounced") and c.get("status") not in ["Bounced", "Do Not Contact", "Closed Lost"]]
    elif "Follow-Up Due" in selected_segment:
        filtered_contacts = [c for c in base_filtered if c.get("status") in ["Contacted", "Follow-Up Sent"] or (c.get("next_follow_up") and str(c.get("next_follow_up"))[:10] <= today_str)]
    elif "Replied" in selected_segment:
        filtered_contacts = [c for c in base_filtered if c.get("status") == "Replied" or c.get("last_reply_at")]
    elif "Bounced / Inactive" in selected_segment:
        filtered_contacts = [c for c in base_filtered if c.get("status") in ["Bounced", "Do Not Contact", "Closed Lost"] or c.get("is_bounced")]
    else:
        filtered_contacts = base_filtered

    filtered_ids = [c["id"] for c in filtered_contacts]
    st.session_state["crm_selected_ids"] = st.session_state["crm_selected_ids"].intersection(set(filtered_ids))
    selected_ids = st.session_state["crm_selected_ids"]
    s_count = len(selected_ids)

    # Sub-bar: Selection Tools & Display Mode Switcher
    col_sel_left, col_sel_right = st.columns([2.5, 1.5], vertical_alignment="center")
    with col_sel_left:
        c_s1, c_s2, c_s3 = st.columns([1.1, 1.1, 2.8])
        with c_s1:
            if st.button(f"☑️ Select All ({len(filtered_contacts)})", key="btn_sel_all_crm", use_container_width=True, disabled=not filtered_contacts):
                st.session_state["crm_selected_ids"] = set(filtered_ids)
                st.rerun()
        with c_s2:
            if st.button("⬜ Clear", key="btn_clear_sel_crm", use_container_width=True, disabled=not selected_ids):
                st.session_state["crm_selected_ids"] = set()
                st.rerun()
        with c_s3:
            st.caption(f"Displaying **{len(filtered_contacts)}** of **{len(all_contacts)}** leads.")
    with col_sel_right:
        crm_layout_mode = st.radio(
            "Display Mode",
            ["📊 Spreadsheet Grid", "🗂️ Detailed Cards"],
            horizontal=True,
            key="crm_display_layout_mode",
            label_visibility="collapsed"
        )

    # ==============================================================================
    # ⚡ CONTEXTUAL BULK ACTIONS TOOLBAR (Appears ONLY when >= 1 contact is checked)
    # ==============================================================================
    if s_count > 0:
        st.markdown("""
        <div style="height: 1px; background:#E2E8F0; margin: 4px 0 10px;"></div>
        """, unsafe_allow_html=True)

        col_b_ind, col_b_act, col_b_ctx, col_b_btn = st.columns([1.4, 2.0, 3.2, 1.4], vertical_alignment="bottom")

        with col_b_ind:
            st.markdown(f"""
            <div style="background:rgba(8,55,49,0.08); border:1px solid rgba(8,55,49,0.25); border-radius:8px; padding:7px 12px; text-align:center;">
                <strong style="color:#083731; font-size:0.92rem;">📌 {s_count} selected</strong>
            </div>
            """, unsafe_allow_html=True)

        with col_b_act:
            bulk_act_options = [
                "Add Tag",
                "Remove Tag",
                "Update Pipeline Stage",
                "Verify Domains (MX)",
                "Export to CSV",
                "Delete Contacts"
            ]
            chosen_bulk_action = st.selectbox(
                "Action",
                bulk_act_options,
                key="crm_unified_bulk_action_choice"
            )

        with col_b_ctx:
            if chosen_bulk_action == "Add Tag":
                all_avail_tags = get_all_distinct_tags(include_predefined=True)
                tag_add_input = st.text_input(
                    "Tag Name(s)",
                    placeholder="Enter tag(s) separated by commas, e.g. VIP, Tier 1",
                    key="crm_bulk_tag_add_input",
                    help="Type one or more comma-separated tags to append to selected leads."
                )
            elif chosen_bulk_action == "Remove Tag":
                all_avail_tags = get_all_distinct_tags(include_predefined=True)
                tags_to_remove = st.multiselect(
                    "Select Tag(s) to Remove",
                    options=all_avail_tags,
                    key="crm_bulk_tag_rem_sel",
                    placeholder="Select tags..."
                )
            elif chosen_bulk_action == "Update Pipeline Stage":
                new_bulk_stage = st.selectbox(
                    "Select New Stage",
                    [
                        "Not Contacted",
                        "Contacted",
                        "Follow-Up Sent",
                        "Replied",
                        "Meeting Booked",
                        "Closed Won",
                        "Closed Lost",
                        "Bounced",
                        "Do Not Contact"
                    ],
                    key="crm_bulk_stage_target_sel"
                )
            elif chosen_bulk_action == "Verify Domains (MX)":
                st.caption(f"Verifies DNS MX mail exchangers across {s_count} selected lead(s) to prevent bounces.")
            elif chosen_bulk_action == "Export to CSV":
                st.caption(f"Export all columns, tags, and custom variables for {s_count} selected lead(s).")
            elif chosen_bulk_action == "Delete Contacts":
                del_confirmed = st.checkbox(f"⚠️ Confirm permanent deletion of {s_count} lead(s)", key="crm_bulk_del_confirm_chk")

        with col_b_btn:
            if chosen_bulk_action == "Export to CSV":
                selected_contacts_list = [c for c in filtered_contacts if c["id"] in selected_ids]
                export_csv_data = export_contacts_to_csv(selected_contacts_list)
                st.download_button(
                    "📥 Export CSV",
                    data=export_csv_data,
                    file_name="selected_leads_export.csv",
                    mime="text/csv",
                    type="primary",
                    use_container_width=True,
                    key="btn_bulk_export_unified"
                )
            else:
                if st.button("Execute Action", type="primary", use_container_width=True, key="btn_exec_unified_bulk"):
                    if chosen_bulk_action == "Add Tag":
                        parsed_tags = [t.strip() for t in tag_add_input.split(",") if t.strip()]
                        if parsed_tags:
                            bulk_add_tags_to_contacts(list(selected_ids), parsed_tags)
                            trigger_toast(f"Added {parsed_tags} to {s_count} lead(s)!", icon="🏷️")
                            st.rerun()
                        else:
                            st.warning("Please enter at least one tag.")

                    elif chosen_bulk_action == "Remove Tag":
                        if tags_to_remove:
                            bulk_remove_tags_from_contacts(list(selected_ids), tags_to_remove)
                            trigger_toast(f"Removed {tags_to_remove} from {s_count} lead(s)!", icon="🏷️")
                            st.rerun()
                        else:
                            st.warning("Please select at least one tag to remove.")

                    elif chosen_bulk_action == "Update Pipeline Stage":
                        for sid in selected_ids:
                            update_contact(contact_id=sid, status=new_bulk_stage)
                        trigger_toast(f"Updated {s_count} leads to '{new_bulk_stage}'!", icon="⚡")
                        st.rerun()

                    elif chosen_bulk_action == "Verify Domains (MX)":
                        with st.spinner("Auditing domain mail exchangers..."):
                            selected_contacts_list = [c for c in filtered_contacts if c["id"] in selected_ids]
                            mx_res = batch_verify_contacts_mx(selected_contacts_list, update_db=True)
                            if mx_res["invalid_count"] > 0:
                                trigger_toast(f"⚠️ {mx_res['invalid_count']} lead(s) failed MX verification.", icon="⚠️")
                            else:
                                trigger_toast(f"🎉 All {mx_res['valid_count']} leads have active MX records!", icon="🌐")
                            st.rerun()

                    elif chosen_bulk_action == "Delete Contacts":
                        if del_confirmed:
                            del_num = bulk_delete_contacts(list(selected_ids))
                            st.session_state["crm_selected_ids"] = set()
                            trigger_toast(f"Deleted {del_num} contact(s).", icon="🗑️")
                            st.rerun()
                        else:
                            st.warning("Please check the confirmation box before deleting.")

    # ==============================================================================
    # 🗃️ SECTION 4: MAIN WORKSPACE (Spreadsheet Grid vs Card View)
    # ==============================================================================
    if not all_contacts:
        st.info("No contacts in database yet. Add a single lead or import a CSV using the panels above to get started.")
    elif not filtered_contacts:
        st.info("No contacts match the current search or filter criteria. Adjust or reset your filters above.")
    elif crm_layout_mode.startswith("📊 Spreadsheet"):
        # ==============================================================================
        # 📊 SPREADSHEET GRID VIEW (st.data_editor with row selection & quick actions)
        # ==============================================================================
        id_map = {c["id"]: c for c in filtered_contacts}

        # 1. Dedicated Multi-Pick & Individual Contact Action Bar
        st.markdown("""
        <div style="background:#FFFFFF; border:1.5px solid rgba(8,55,49,0.18); border-radius:10px; padding:12px 16px; margin:6px 0 12px; box-shadow:0 2px 8px rgba(8,55,49,0.04);">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div style="font-weight:800; font-size:0.95rem; color:#083731;">📋 Spreadsheet Contact Selection & Quick Actions</div>
                <div style="font-size:0.8rem; color:#64748B;">Select individual leads to edit details or delete, or pick multiple leads for bulk operations</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        col_bar1, col_bar2 = st.columns([2.6, 2.4], vertical_alignment="bottom")

        with col_bar1:
            # Multi-Pick from list
            picked_ids = st.multiselect(
                "🎯 Multi-Pick Contacts to Select:",
                options=filtered_ids,
                default=[cid for cid in selected_ids if cid in filtered_ids],
                format_func=lambda cid: f"{id_map[cid]['name']} ({id_map[cid].get('company') or 'No Company'} — {id_map[cid]['email']})",
                key="crm_grid_multipick",
                help="Search and pick multiple contacts by name, company, or email to select them in the spreadsheet."
            )
            if set(picked_ids) != selected_ids:
                st.session_state["crm_selected_ids"] = set(picked_ids)
                st.rerun()

        with col_bar2:
            # Individual contact selector
            def_indiv_idx = 0
            indiv_options = [None] + filtered_ids
            if len(selected_ids) == 1:
                single_id = list(selected_ids)[0]
                if single_id in filtered_ids:
                    def_indiv_idx = indiv_options.index(single_id)

            chosen_indiv = st.selectbox(
                "👤 Select Single Contact to Edit / Delete:",
                options=indiv_options,
                index=def_indiv_idx,
                format_func=lambda cid: "-- Choose a contact to manage --" if cid is None else f"L-{cid:04d}: {id_map[cid]['name']} ({id_map[cid]['email']})",
                key="crm_grid_indiv_action_picker"
            )

        if chosen_indiv is not None:
            c_target = id_map[chosen_indiv]
            st.markdown(f"""
            <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.2); border-radius:8px; padding:10px 14px; margin:6px 0 10px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <div>
                    <strong style="color:#083731; font-size:0.92rem;">👤 {c_target['name']}</strong>
                    <span style="color:#475569; font-size:0.82rem; margin-left:6px;">({c_target.get('company') or 'No Company'} • <strong>{c_target['email']}</strong>)</span>
                    <span class="sellomize-badge badge-brand" style="margin-left:8px;">Pipeline: {c_target.get('status') or 'Not Contacted'}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            col_act1, col_act2, col_act3, col_act4 = st.columns([1.3, 1.1, 1.8, 1.8])
            with col_act1:
                if st.button("✏️ Edit Lead", use_container_width=True, key=f"btn_edit_indiv_grid_{chosen_indiv}", help="Open full lead editor modal with all variables, tags & notes"):
                    st.session_state["crm_editing_id"] = chosen_indiv
                    st.rerun()
            with col_act2:
                if st.session_state.get(f"confirm_grid_del_{chosen_indiv}"):
                    if st.button("Confirm Delete", type="primary", use_container_width=True, key=f"btn_conf_grid_del_{chosen_indiv}"):
                        delete_contact(chosen_indiv)
                        st.session_state["crm_selected_ids"].discard(chosen_indiv)
                        st.session_state[f"confirm_grid_del_{chosen_indiv}"] = False
                        trigger_toast(f"Deleted contact #{chosen_indiv}.", icon="🗑️")
                        st.rerun()
                else:
                    if st.button("🗑️ Delete", use_container_width=True, key=f"btn_del_indiv_grid_{chosen_indiv}", help="Delete this individual lead"):
                        st.session_state[f"confirm_grid_del_{chosen_indiv}"] = True
                        st.rerun()
            with col_act3:
                if st.button("✉️ Send One-Time Email", type="primary", use_container_width=True, key=f"btn_mail_once_indiv_grid_{chosen_indiv}", help="Jump directly to compose a single one-off email for this contact"):
                    st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
                    st.session_state["camp_audience_mode"] = "👤 Single Contact (1-to-1 Sequence / Direct Outreach)"
                    st.session_state["camp_single_contact_picker"] = chosen_indiv
                    st.session_state["camp_seq_touches"] = "Once (1 Email - Single Touch)"
                    st.rerun()
            with col_act4:
                if st.button("⚡ Multi-Touch Sequence", use_container_width=True, key=f"btn_mail_seq_indiv_grid_{chosen_indiv}", help="Jump directly to set up an automated multi-step sequence for this contact"):
                    st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
                    st.session_state["camp_audience_mode"] = "👤 Single Contact (1-to-1 Sequence / Direct Outreach)"
                    st.session_state["camp_single_contact_picker"] = chosen_indiv
                    st.session_state["camp_seq_touches"] = "Twice (2 Emails - Initial Pitch + 1 Follow-Up)"
                    st.rerun()

        elif s_count > 1:
            st.markdown(f"""
            <div style="background:rgba(8,55,49,0.06); border:1.5px solid #083731; border-radius:8px; padding:10px 14px; margin:6px 0 10px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <div>
                    <strong style="color:#083731; font-size:0.92rem;">📌 {s_count} Leads Selected in Spreadsheet</strong>
                    <span style="color:#475569; font-size:0.8rem; margin-left:6px;">Use Bulk Actions above or quick controls below</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            col_b1, col_b2, col_b3 = st.columns([1.6, 2.2, 2.2])
            with col_b1:
                if st.session_state.get("confirm_multi_del_grid"):
                    if st.button(f"Confirm Delete {s_count} Leads", type="primary", use_container_width=True, key="btn_conf_multi_del_grid"):
                        bulk_delete_contacts(list(selected_ids))
                        st.session_state["crm_selected_ids"] = set()
                        st.session_state["confirm_multi_del_grid"] = False
                        trigger_toast(f"Deleted {s_count} contact(s).", icon="🗑️")
                        st.rerun()
                else:
                    if st.button(f"🗑️ Delete {s_count} Selected", use_container_width=True, key="btn_multi_del_grid"):
                        st.session_state["confirm_multi_del_grid"] = True
                        st.rerun()
            with col_b2:
                if st.button(f"✉️ Send One-Time Batch ({s_count})", type="primary", use_container_width=True, key="btn_multi_outreach_once_grid", help="Jump directly to send a single one-off email to these selected leads"):
                    st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
                    st.session_state["camp_audience_mode"] = "🎯 Cherry-Pick Specific Contacts (Direct Multi-Select)"
                    st.session_state["camp_cherry_pick_multisel"] = list(selected_ids)
                    st.session_state["camp_seq_touches"] = "Once (1 Email - Single Touch)"
                    st.rerun()
            with col_b3:
                if st.button(f"⚡ Launch Campaign ({s_count})", use_container_width=True, key="btn_multi_outreach_seq_grid", help="Jump directly to build a multi-step sequence campaign for these selected leads"):
                    st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
                    st.session_state["camp_audience_mode"] = "🎯 Cherry-Pick Specific Contacts (Direct Multi-Select)"
                    st.session_state["camp_cherry_pick_multisel"] = list(selected_ids)
                    st.session_state["camp_seq_touches"] = "Twice (2 Emails - Initial Pitch + 1 Follow-Up)"
                    st.rerun()

        # 2. Build rows for spreadsheet data editor with 'Select' checkbox
        grid_rows = []
        for c in filtered_contacts:
            raw_email = (c.get("email") or "").strip()
            grid_rows.append({
                "Select": (c["id"] in selected_ids),
                "id": c["id"],
                "Lead ID": f"L-{c['id']:04d}",
                "Company": c.get("company") or "",
                "Contact Name": c.get("name") or "",
                "Email Address": f"mailto:{raw_email}" if raw_email else "",
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

        # Column Visibility Controls - Hide system columns (internal IDs, timestamps) by default
        all_grid_cols = [
            "Select", "Lead ID", "Company", "Contact Name", "Email Address", "Lead Source",
            "Priority", "Contacted?", "Date First Emailed", "Status", "Follow-Ups Sent",
            "Last Contact Date", "Next Follow-Up", "Owner", "Notes", "Tags"
        ]
        default_outreach_cols = [
            "Select", "Company", "Contact Name", "Email Address",
            "Priority", "Status", "Follow-Ups Sent", "Next Follow-Up"
        ]

        col_v1, col_v2 = st.columns([1.6, 2.4])
        with col_v1:
            col_preset = st.radio(
                "Grid Column View",
                ["Focused Outreach View (8 cols)", "All Columns (16 cols)", "Custom Columns"],
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
                st.caption("Showing essential outreach columns. Select 'All Columns' or 'Custom' to expand.")
            else:
                visible_cols = all_grid_cols
                st.caption("Showing all 16 lead columns. Scroll horizontally to browse fields on the right.")

        if "Select" not in visible_cols:
            visible_cols = ["Select"] + visible_cols

        st.markdown(f"""
        <div class="crm-scroll-caption">
            <span>↔️ Scroll horizontally to browse columns • Check 'Select' box to pick leads</span>
            <span>Displaying <b>{len(visible_cols)}</b> of 16 columns</span>
        </div>
        """, unsafe_allow_html=True)

        column_config = {
            "Select": st.column_config.CheckboxColumn("Select", help="Check to select contact for editing or bulk actions", width="small", default=False, pinned=True),
            "id": None,
            "created_at": None,
            "custom_variables_raw": None,
            "Lead ID": st.column_config.TextColumn("Lead ID", disabled=True, width="small") if "Lead ID" in visible_cols else None,
            "Company": st.column_config.TextColumn("Company", width="medium", pinned=True) if "Company" in visible_cols else None,
            "Contact Name": st.column_config.TextColumn("Contact Name", width="medium", required=True, pinned=True) if "Contact Name" in visible_cols else None,
            "Email Address": st.column_config.LinkColumn(
                "Email Address",
                display_text=r"mailto:(.*)",
                help="Click to open default email client",
                width="medium",
                required=True
            ) if "Email Address" in visible_cols else None,
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
                options=["Not Contacted", "Contacted", "Follow-Up Sent", "Replied", "Meeting Booked", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"],
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

        # Detect checkbox selection changes in the grid
        if "Select" in edited_grid.columns:
            grid_checked = set(edited_grid[edited_grid["Select"] == True]["id"].tolist())
            if grid_checked != selected_ids:
                st.session_state["crm_selected_ids"] = grid_checked
                st.rerun()

        has_unsaved_edits = False
        editor_state = st.session_state.get("crm_spreadsheet_editor")
        if editor_state and isinstance(editor_state, dict):
            if editor_state.get("edited_rows"):
                has_unsaved_edits = True

        col_save_grid, col_reset_grid, col_sub_acts = st.columns([2, 1.8, 3.2])
        with col_save_grid:
            if st.button("💾 Save Spreadsheet Changes", type="primary", use_container_width=True, key="btn_save_crm_spreadsheet"):
                records_to_save = edited_grid.to_dict(orient="records")
                for r in records_to_save:
                    if "Email Address" in r and isinstance(r["Email Address"], str):
                        r["Email Address"] = r["Email Address"].replace("mailto:", "").strip()
                saved_count = bulk_update_contact_grid(records_to_save)
                trigger_toast(f"Saved changes to {saved_count} contact(s)!", icon="💾")
                st.rerun()
        with col_reset_grid:
            if has_unsaved_edits:
                if st.button("🔄 Discard Unsaved Changes", use_container_width=True, key="btn_reset_crm_spreadsheet"):
                    if "crm_spreadsheet_editor" in st.session_state:
                        del st.session_state["crm_spreadsheet_editor"]
                    st.rerun()
        with col_sub_acts:
            if s_count == 1:
                single_id_sel = list(selected_ids)[0]
                if st.button("✏️ Edit Selected Lead (Full Editor)", use_container_width=True, key="btn_bot_edit_sel_grid"):
                    st.session_state["crm_editing_id"] = single_id_sel
                    st.rerun()
            elif s_count > 1:
                if st.button(f"🗑️ Delete {s_count} Selected Leads", use_container_width=True, key="btn_bot_del_sel_grid"):
                    bulk_delete_contacts(list(selected_ids))
                    st.session_state["crm_selected_ids"] = set()
                    trigger_toast(f"Deleted {s_count} contact(s).", icon="🗑️")
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
                    c_em = contact['email']
                    is_int = (c_em or "").strip().lower() in system_excluded_emails
                    if is_int:
                        st.markdown(f"📧 `{c_em}` <span style='background:rgba(8,55,49,0.08); color:#083731; border:1px solid rgba(8,55,49,0.22); font-size:0.7rem; font-weight:700; padding:1px 6px; border-radius:8px;'>🛡️ Internal / BCC</span>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"📧 `{c_em}`")
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

                    col_btn_mail, col_btn_edit, col_btn_del = st.columns([1, 2.2, 0.8])
                    with col_btn_mail:
                        if st.button("✉️", key=f"mail_btn_{c_id}", use_container_width=True, help=f"Draft 1-to-1 email or sequence for {contact['name']}"):
                            st.session_state["main_app_tabs"] = "🚀 Dispatch & Review"
                            st.session_state["camp_audience_mode"] = "👤 Single Contact (1-to-1 Sequence / Direct Outreach)"
                            st.session_state["camp_single_contact_picker"] = c_id
                            st.rerun()

                    with col_btn_edit:
                        if st.button("✏️ Edit", key=f"edit_btn_{c_id}", use_container_width=True):
                            st.session_state["crm_editing_id"] = c_id
                            st.rerun()

                    with col_btn_del:
                        if st.session_state.get(f"confirm_del_c_{c_id}"):
                            if st.button("Confirm", key=f"del_conf_{c_id}", use_container_width=True):
                                delete_contact(c_id)
                                st.session_state["crm_selected_ids"].discard(c_id)
                                st.session_state[f"confirm_del_c_{c_id}"] = False
                                trigger_toast(f"Contact #{c_id} deleted.", icon="🗑️")
                                st.rerun()
                        else:
                            if st.button("🗑️", key=f"del_contact_{c_id}", use_container_width=True, help="Delete this contact"):
                                st.session_state[f"confirm_del_c_{c_id}"] = True
                                st.rerun()

                st.markdown("<hr style='margin: 0.4rem 0; opacity: 0.15;'>", unsafe_allow_html=True)
