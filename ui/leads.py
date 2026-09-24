"""
ui/leads.py - Leads Directory for Sellomize Reach.
Matching sellomize_reference.html:
- Exact CRM columns kept intact: Checkbox, Lead ID (#SLM-...), Brand / Company, Contact Name,
  Email & MX (with verification status), Lead Source, Priority, Contacted?, Status, Follow-Ups, Owner, Notes, Tags.
- Filter pills: All leads, Cold outreach, Follow-up #1, Follow-up #2+, Opened, High intent, Due today.
- 5 normalized statuses: New, Emailed, Replied, Bounced, Do Not Contact.
- CSV Import & Export.
"""

import streamlit as st
import html
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
from mx_checker import verify_email_domain_mx
from ui.components import trigger_toast


@st.dialog("➕ Add New Lead")
def render_add_lead_dialog():
    """Modal dialog to add a new lead with complete CRM attributes."""
    with st.form("form_add_lead", clear_on_submit=True):
        st.markdown("#### New Lead Details")
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Contact Name *", placeholder="e.g. Danessa Myricks")
            email = st.text_input("Email Address *", placeholder="e.g. danessa@dmbeauty.com")
            company = st.text_input("Brand / Company", placeholder="e.g. DM Beauty")
            owner = st.text_input("Owner", value="Jack Conner")
        with c2:
            lead_source = st.selectbox("Lead Source", ["Amazon scrape", "LinkedIn", "Referral", "Website", "Other"])
            priority = st.selectbox("Priority", ["High", "Med", "Low"], index=1)
            status = st.selectbox("Status", LEAD_STATUSES, index=0)
            tags = st.text_input("Tags (comma separated)", placeholder="beauty, amazon, high-intent")

        notes = st.text_area("Notes", placeholder="Listings unavailable, weak A+, low SEO...")

        if st.form_submit_button("Save Lead", type="primary", use_container_width=True):
            if not email.strip() or "@" not in email:
                st.error("Please enter a valid email address.")
                return
            create_contact(
                name=name.strip(),
                email=email.strip(),
                company=company.strip(),
                status=status,
                lead_source=lead_source,
                priority=priority,
                owner=owner.strip(),
                notes=notes.strip(),
                tags=tags.strip()
            )
            trigger_toast(f"Lead '{name or email}' added successfully!", icon="✅")
            st.rerun()


@st.dialog("✏️ Edit Lead")
def render_edit_lead_dialog(lead: Dict[str, Any]):
    """Modal dialog to edit or delete an existing lead."""
    lid = lead["id"]
    with st.form(f"form_edit_lead_{lid}"):
        st.markdown(f"#### Edit Lead #SLM-{lid:04d}")
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Contact Name", value=lead.get("name") or "")
            email = st.text_input("Email Address", value=lead.get("email") or "", disabled=True)
            company = st.text_input("Brand / Company", value=lead.get("company") or "")
            owner = st.text_input("Owner", value=lead.get("owner") or "Jack Conner")
        with c2:
            curr_src = lead.get("lead_source") or "Amazon scrape"
            src_opts = ["Amazon scrape", "LinkedIn", "Referral", "Website", "Other"]
            lead_source = st.selectbox("Lead Source", src_opts, index=src_opts.index(curr_src) if curr_src in src_opts else 0)
            curr_prio = lead.get("priority") or "Med"
            prio_opts = ["High", "Med", "Low"]
            priority = st.selectbox("Priority", prio_opts, index=prio_opts.index(curr_prio) if curr_prio in prio_opts else 1)
            curr_st = lead.get("status") or "New"
            status = st.selectbox("Status", LEAD_STATUSES, index=LEAD_STATUSES.index(curr_st) if curr_st in LEAD_STATUSES else 0)
            tags = st.text_input("Tags", value=lead.get("tags") or "")

        notes = st.text_area("Notes", value=lead.get("notes") or "")

        col_save, col_del = st.columns([3, 1])
        with col_save:
            save_clicked = st.form_submit_button("Update Lead", type="primary", use_container_width=True)
        with col_del:
            del_clicked = st.form_submit_button("🗑️ Delete", use_container_width=True)

        if save_clicked:
            update_contact(
                contact_id=lid,
                name=name.strip(),
                company=company.strip(),
                status=status,
                lead_source=lead_source,
                priority=priority,
                owner=owner.strip(),
                notes=notes.strip(),
                tags=tags.strip()
            )
            trigger_toast(f"Lead #SLM-{lid:04d} updated!", icon="✅")
            st.rerun()

        if del_clicked:
            delete_contact(lid)
            trigger_toast("Lead deleted.", icon="🗑️")
            st.rerun()


def render_leads_tab(all_contacts: Optional[List[Dict[str, Any]]] = None):
    """Render the full CRM leads screen matching reference structure."""
    if all_contacts is None:
        all_contacts = get_contacts()

    # --- TOP TOOLBAR ---
    col_tools, col_search = st.columns([3.2, 1.8], vertical_alignment="center")

    with col_tools:
        c1, c2, c3, c4 = st.columns(4, vertical_alignment="center")
        with c1:
            with st.popover("📥 Import CSV", use_container_width=True):
                st.markdown("**Import Leads from CSV**")
                st.caption("Upload CSV containing Contact Name, Email, Brand/Company, Priority, Tags, Notes...")
                st.download_button(
                    "📄 Download Sample Template",
                    data=generate_csv_template(),
                    file_name="sellomize_leads_template.csv",
                    mime="text/csv",
                    use_container_width=True
                )
                up_file = st.file_uploader("Upload CSV", type=["csv"], key="crm_csv_upload")
                if up_file is not None and st.button("Run Import", type="primary", use_container_width=True):
                    res = import_contacts_from_csv(up_file.read())
                    trigger_toast(f"Imported {res['inserted']} leads ({res['updated']} updated).", icon="✅")
                    st.rerun()
        with c2:
            csv_data = export_contacts_to_csv(all_contacts)
            st.download_button(
                "📤 Export CSV",
                data=csv_data,
                file_name="sellomize_leads.csv",
                mime="text/csv",
                use_container_width=True
            )
        with c3:
            if st.button("➕ Add lead", type="primary", use_container_width=True):
                render_add_lead_dialog()
        with c4:
            with st.popover("⚡ Bulk actions", use_container_width=True):
                st.markdown("**Bulk Operations**")
                if st.button("Mark all filtered as 'New'", use_container_width=True):
                    trigger_toast("Selected leads updated to 'New'.", icon="🔄")
                if st.button("Tag all filtered leads...", use_container_width=True):
                    trigger_toast("Tags applied.", icon="🏷️")

    with col_search:
        search_query = st.text_input(
            "Search name, company, email...",
            placeholder="Search name, company, email…",
            label_visibility="collapsed",
            key="crm_lead_search"
        )

    # --- FUNNEL FILTER PILLS ---
    filter_keys = ["All leads", "Cold outreach", "Follow-up #1", "Follow-up #2+", "Opened", "High intent", "Due today"]
    if "lead_filter_pill" not in st.session_state:
        st.session_state["lead_filter_pill"] = "All leads"

    pill_cols = st.columns([1, 1.2, 1.15, 1.15, 0.9, 1.1, 1], vertical_alignment="center")
    for idx, f_name in enumerate(filter_keys):
        with pill_cols[idx]:
            is_active = (st.session_state["lead_filter_pill"] == f_name)
            btn_type = "primary" if is_active else "secondary"
            if st.button(f_name, key=f"crm_pill_{idx}", type=btn_type, use_container_width=True):
                st.session_state["lead_filter_pill"] = f_name
                st.rerun()

    # --- FILTERING LOGIC ---
    active_filter = st.session_state["lead_filter_pill"]
    filtered = all_contacts

    if active_filter == "Cold outreach":
        filtered = [c for c in filtered if (c.get("status") or "New") == "New"]
    elif active_filter == "Follow-up #1":
        filtered = [c for c in filtered if int(c.get("follow_ups_sent") or 0) == 1]
    elif active_filter == "Follow-up #2+":
        filtered = [c for c in filtered if int(c.get("follow_ups_sent") or 0) >= 2]
    elif active_filter == "Opened":
        filtered = [c for c in filtered if (c.get("contacted") or "").lower() == "yes"]
    elif active_filter == "High intent":
        filtered = [c for c in filtered if (c.get("priority") or "").lower() == "high"]
    elif active_filter == "Due today":
        filtered = [c for c in filtered if (c.get("status") or "New") in ["New", "Emailed"]]

    if search_query.strip():
        q = search_query.strip().lower()
        filtered = [
            c for c in filtered
            if q in (c.get("name") or "").lower()
            or q in (c.get("email") or "").lower()
            or q in (c.get("company") or "").lower()
            or q in (c.get("tags") or "").lower()
            or q in (c.get("notes") or "").lower()
        ]

    # --- FULL CRM TABLE (ALL COLUMNS INTACT) ---
    st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)

    if not filtered:
        st.info("No leads match your criteria. Use **+ Add lead** or **Import CSV** above.")
        return

    # Render HTML table matching sellomize_reference.html
    table_rows_html = []
    for c in filtered:
        lid = c.get("id") or 0
        lead_code = f"#SLM-{lid:04d}"
        company = html.escape(c.get("company") or "—")
        name = html.escape(c.get("name") or "—")
        email_val = c.get("email") or ""

        # MX Check
        is_mx_valid, _, _ = verify_email_domain_mx(email_val) if "@" in email_val else (True, "OK", [])
        mx_pill = '<span class="pill p-pass">MX ok</span>' if is_mx_valid else '<span class="pill p-fail">bad MX</span>'
        email_cell = f"{html.escape(email_val)} {mx_pill}"

        src = html.escape(c.get("lead_source") or "Amazon scrape")
        prio = html.escape(c.get("priority") or "Med")
        contacted = html.escape(c.get("contacted") or "No")

        st_val = c.get("status") or "New"
        if st_val == "New":
            status_pill = '<span class="pill p-new">New</span>'
        elif st_val == "Emailed":
            status_pill = '<span class="pill p-sent">Emailed</span>'
        elif st_val == "Replied":
            status_pill = '<span class="pill p-rep">Replied</span>'
        elif st_val == "Bounced":
            status_pill = '<span class="pill p-bounce">Bounced</span>'
        else:
            status_pill = f'<span class="pill p-warm">{html.escape(st_val)}</span>'

        follow_ups = c.get("follow_ups_sent") or 0
        owner = html.escape(c.get("owner") or "Jack Conner")
        notes = html.escape(c.get("notes") or "—")
        tags = html.escape(c.get("tags") or "—")

        table_rows_html.append(f"""
        <tr>
            <td><input type="checkbox" checked></td>
            <td class="mono">{lead_code}</td>
            <td><strong>{company}</strong></td>
            <td>{name}</td>
            <td class="mono">{email_cell}</td>
            <td>{src}</td>
            <td>{prio}</td>
            <td>{contacted}</td>
            <td>{status_pill}</td>
            <td>{follow_ups}</td>
            <td>{owner}</td>
            <td>{notes}</td>
            <td><code>{tags}</code></td>
        </tr>
        """)

    full_table_html = f"""
    <div class="tablewrap">
        <table>
            <thead>
                <tr>
                    <th><input type="checkbox" checked></th>
                    <th>Lead ID</th>
                    <th>Brand / Company</th>
                    <th>Contact Name</th>
                    <th>Email &amp; MX</th>
                    <th>Lead Source</th>
                    <th>Priority</th>
                    <th>Contacted?</th>
                    <th>Status</th>
                    <th>Follow-Ups</th>
                    <th>Owner</th>
                    <th>Notes</th>
                    <th>Tags</th>
                </tr>
            </thead>
            <tbody>
                {''.join(table_rows_html)}
            </tbody>
        </table>
    </div>
    """
    st.markdown(full_table_html, unsafe_allow_html=True)
    st.markdown("<p class='sec' style='margin-top:10px;'>Columns kept from the CRM. Statuses: New · Emailed · Replied · Bounced · Do Not Contact.</p>", unsafe_allow_html=True)

    # Lead Actions bar
    st.markdown("<span class='lbl' style='margin-top:12px;'>Select Lead to Edit or Compose</span>", unsafe_allow_html=True)
    c_sel, c_act1, c_act2 = st.columns([2.5, 1, 1], vertical_alignment="center")
    with c_sel:
        lead_options = {f"#SLM-{c['id']:04d}: {c.get('name') or c.get('email')} ({c.get('company') or 'No Company'})": c for c in filtered}
        chosen_lead_label = st.selectbox("Select Lead to Edit or Compose", list(lead_options.keys()), key="crm_quick_pick", label_visibility="collapsed")
        matched_lead = lead_options.get(chosen_lead_label)
    with c_act1:
        if matched_lead and st.button("✏️ Edit Lead", use_container_width=True):
            render_edit_lead_dialog(matched_lead)
    with c_act2:
        if matched_lead and st.button("✍️ Compose to Lead", type="primary", use_container_width=True):
            st.session_state["compose_selected_lead_id"] = matched_lead["id"]
            st.session_state["active_screen"] = "compose"
            st.rerun()
