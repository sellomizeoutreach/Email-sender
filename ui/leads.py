"""
ui/leads.py - Leads Directory for Sellomize Reach.
Matching sellomize_reference.html:
- Exact CRM columns: Checkbox, Lead ID, Brand / Company, Contact Name,
  Email & MX, Lead Source, Priority, Contacted?, Status, Follow-Ups, Owner, Notes, Tags.
- Filter pills: All leads, Cold outreach, Follow-up #1, Follow-up #2+, Opened, High intent, Due today.
- 5 normalized statuses: New, Emailed, Replied, Bounced, Do Not Contact.
- CSV template & export headers match table headers exactly.
- Sortable column headers (click to sort asc/desc).
- Functional master checkbox (select / deselect all visible rows).
- Bulk actions: ✏️ Bulk Edit & 🗑️ Bulk Delete via @st.dialog popups.
"""

import streamlit as st
import html as html_mod
from typing import List, Dict, Any, Optional

from database import (
    get_contacts,
    get_contact_by_id,
    create_contact,
    update_contact,
    delete_contact,
    bulk_delete_contacts,
    bulk_update_contacts,
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


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

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
            save_clicked = st.form_submit_button("💾 Update Lead", type="primary", use_container_width=True)
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


@st.dialog("✏️ Bulk Edit Selected Leads")
def render_bulk_edit_dialog(selected_ids: List[int], count: int):
    """Dialog to bulk-edit common fields across all selected contacts."""
    st.markdown(f"Editing **{count} selected lead{'s' if count != 1 else ''}**. Leave a field blank to keep existing values.")

    with st.form("bulk_edit_form"):
        c1, c2 = st.columns(2)
        with c1:
            src_opts_bulk = ["", "Amazon scrape", "LinkedIn", "Referral", "Website", "Other"]
            new_source = st.selectbox("Lead Source", src_opts_bulk, index=0, help="Leave blank to keep current")
            prio_opts_bulk = ["", "High", "Med", "Low"]
            new_priority = st.selectbox("Priority", prio_opts_bulk, index=0, help="Leave blank to keep current")
            contacted_opts = ["", "Yes", "No"]
            new_contacted = st.selectbox("Contacted?", contacted_opts, index=0, help="Leave blank to keep current")
        with c2:
            status_opts_bulk = [""] + LEAD_STATUSES
            new_status = st.selectbox("Status", status_opts_bulk, index=0, help="Leave blank to keep current")
            new_owner = st.text_input("Owner", placeholder="Leave blank to keep current")
            new_company = st.text_input("Brand / Company", placeholder="Leave blank to keep current")
        new_notes = st.text_area("Notes", placeholder="Leave blank to keep current")

        c_apply, c_cancel = st.columns([2, 1])
        with c_apply:
            apply = st.form_submit_button("✅ Apply Changes", type="primary", use_container_width=True)
        with c_cancel:
            cancel = st.form_submit_button("✖️ Cancel", use_container_width=True)

    if cancel:
        st.rerun()

    if apply:
        updates: Dict[str, Any] = {}
        if new_source:
            updates["lead_source"] = new_source
        if new_priority:
            updates["priority"] = new_priority
        if new_contacted:
            updates["contacted"] = new_contacted
        if new_status:
            updates["status"] = new_status
        if new_owner.strip():
            updates["owner"] = new_owner.strip()
        if new_company.strip():
            updates["company"] = new_company.strip()
        if new_notes.strip():
            updates["notes"] = new_notes.strip()

        if not updates:
            st.warning("No changes specified. Please update at least one field.")
        else:
            affected = bulk_update_contacts(selected_ids, updates)
            # Clear selection
            st.session_state["crm_selected_ids"] = set()
            st.session_state["crm_master_check"] = False
            trigger_toast(f"Updated {affected} lead{'s' if affected != 1 else ''} successfully!", icon="✅")
            st.rerun()


@st.dialog("🗑️ Bulk Delete Selected Leads")
def render_bulk_delete_dialog(selected_ids: List[int], count: int):
    """Confirmation dialog before bulk-deleting selected leads."""
    st.error(f"⚠️ You are about to **permanently delete {count} lead{'s' if count != 1 else ''}**. This cannot be undone.")
    st.markdown(f"**{count} lead{'s' if count != 1 else ''} will be deleted from the CRM.**")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🗑️ Yes, Delete All", type="primary", use_container_width=True):
            deleted = bulk_delete_contacts(selected_ids)
            st.session_state["crm_selected_ids"] = set()
            st.session_state["crm_master_check"] = False
            trigger_toast(f"Deleted {deleted} lead{'s' if deleted != 1 else ''}.", icon="🗑️")
            st.rerun()
    with c2:
        if st.button("✖️ Cancel", use_container_width=True):
            st.rerun()


# ---------------------------------------------------------------------------
# Sort helper
# ---------------------------------------------------------------------------

_SORT_KEY_MAP = {
    "lead_id":      lambda c: c.get("id") or 0,
    "company":      lambda c: (c.get("company") or "").lower(),
    "name":         lambda c: (c.get("name") or "").lower(),
    "email":        lambda c: (c.get("email") or "").lower(),
    "lead_source":  lambda c: (c.get("lead_source") or "").lower(),
    "priority":     lambda c: {"High": 0, "Med": 1, "Low": 2}.get(c.get("priority") or "Med", 1),
    "contacted":    lambda c: (c.get("contacted") or "No").lower(),
    "status":       lambda c: (c.get("status") or "New").lower(),
    "follow_ups":   lambda c: int(c.get("follow_ups_sent") or 0),
    "owner":        lambda c: (c.get("owner") or "").lower(),
}

def _sort_arrow(col_key: str) -> str:
    """Return ▲ ▼ or ⇅ indicator for a column header."""
    if st.session_state.get("crm_sort_col") == col_key:
        return " ▲" if not st.session_state.get("crm_sort_desc", False) else " ▼"
    return " ⇅"


# ---------------------------------------------------------------------------
# Priority / Contacted pills
# ---------------------------------------------------------------------------

def _priority_pill(prio: str) -> str:
    p = (prio or "Med").strip()
    if p == "High":
        return '<span class="pill p-new">High</span>'
    if p == "Low":
        return '<span class="pill p-sent">Low</span>'
    return f'<span class="pill p-warm">{html_mod.escape(p)}</span>'


def _contacted_pill(val: str) -> str:
    v = (val or "No").strip()
    if v.lower() == "yes":
        return '<span class="pill p-pass">Yes</span>'
    return '<span class="pill p-sent">No</span>'


# ---------------------------------------------------------------------------
# Main leads tab
# ---------------------------------------------------------------------------

def render_leads_tab(all_contacts: Optional[List[Dict[str, Any]]] = None):
    """Render the full CRM leads screen matching reference structure."""
    if all_contacts is None:
        all_contacts = get_contacts()

    # --- SESSION STATE INIT ---
    if "crm_selected_ids" not in st.session_state:
        st.session_state["crm_selected_ids"] = set()
    if "crm_sort_col" not in st.session_state:
        st.session_state["crm_sort_col"] = "lead_id"
    if "crm_sort_desc" not in st.session_state:
        st.session_state["crm_sort_desc"] = False
    if "crm_master_check" not in st.session_state:
        st.session_state["crm_master_check"] = False

    # --- TOP TOOLBAR ---
    col_tools, col_search = st.columns([3.2, 1.8], vertical_alignment="center")

    with col_tools:
        c1, c2, c3, c4 = st.columns(4, vertical_alignment="center")
        with c1:
            with st.popover("📥 Import CSV", use_container_width=True):
                st.markdown("**Import Leads from CSV**")
                st.caption("Columns: Lead ID · Brand / Company · Contact Name · Email · Lead Source · Priority · Contacted? · Status · Follow-Ups · Owner · Notes · Tags")
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
            sel_count = len(st.session_state["crm_selected_ids"])
            lbl = f"⚡ Bulk actions{f' ({sel_count})' if sel_count else ''}"
            with st.popover(lbl, use_container_width=True):
                st.markdown("**Bulk Operations**")
                if sel_count == 0:
                    st.caption("☑️ Select rows using the checkboxes below, then choose an action.")
                else:
                    st.markdown(f"**{sel_count} lead{'s' if sel_count != 1 else ''} selected**")
                    if st.button("✏️ Bulk Edit Selected", use_container_width=True, key="btn_bulk_edit"):
                        render_bulk_edit_dialog(list(st.session_state["crm_selected_ids"]), sel_count)
                    if st.button("🗑️ Bulk Delete Selected", use_container_width=True, key="btn_bulk_delete"):
                        render_bulk_delete_dialog(list(st.session_state["crm_selected_ids"]), sel_count)
                st.divider()
                st.markdown("**Quick Operations on All Filtered**")
                if st.button("☑️ Select All Filtered", use_container_width=True):
                    st.rerun()  # handled after filtering below
                if st.button("◻️ Clear Selection", use_container_width=True):
                    st.session_state["crm_selected_ids"] = set()
                    st.session_state["crm_master_check"] = False
                    st.rerun()

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

    filtered_ids = {c["id"] for c in filtered if c.get("id")}

    # --- SORTING ---
    sort_col = st.session_state.get("crm_sort_col", "lead_id")
    sort_desc = st.session_state.get("crm_sort_desc", False)
    if sort_col in _SORT_KEY_MAP:
        filtered = sorted(filtered, key=_SORT_KEY_MAP[sort_col], reverse=sort_desc)

    # --- SELECT ALL BUTTON (post-filter) ---
    # Handles "Select All Filtered" from Bulk actions popover
    if st.session_state.get("_do_select_all_filtered"):
        st.session_state["crm_selected_ids"] = filtered_ids.copy()
        st.session_state["crm_master_check"] = True
        st.session_state["_do_select_all_filtered"] = False

    # --- SORT HEADER BUTTONS (above table) ---
    # 12 columns: ☐  LEAD ID  BRAND/CO  NAME  EMAIL&MX  SOURCE  PRIORITY  CONTACTED  STATUS  FOLLOW-UPS  OWNER  ACTIONS
    sort_cols = st.columns([0.35, 0.7, 1.3, 1.3, 1.5, 0.9, 0.7, 0.75, 0.75, 0.7, 0.8, 0.8], vertical_alignment="center")

    # Master checkbox
    with sort_cols[0]:
        master = st.checkbox(
            "☑",
            value=st.session_state.get("crm_master_check", False),
            key="crm_checkbox_master",
            label_visibility="collapsed",
            help="Select / deselect all visible rows"
        )
        if master != st.session_state.get("crm_master_check", False):
            st.session_state["crm_master_check"] = master
            if master:
                st.session_state["crm_selected_ids"] = filtered_ids.copy()
            else:
                st.session_state["crm_selected_ids"] = set()
            st.rerun()

    # Sort buttons — inline compact style
    def sort_btn(col_key: str, label: str, col_obj):
        arrow = _sort_arrow(col_key)
        with col_obj:
            if st.button(
                f"{label}{arrow}",
                key=f"sort_btn_{col_key}",
                use_container_width=True,
                help=f"Sort by {label}"
            ):
                if st.session_state["crm_sort_col"] == col_key:
                    st.session_state["crm_sort_desc"] = not st.session_state["crm_sort_desc"]
                else:
                    st.session_state["crm_sort_col"] = col_key
                    st.session_state["crm_sort_desc"] = False
                st.rerun()

    sort_btn("lead_id",     "Lead ID",       sort_cols[1])
    sort_btn("company",     "Brand / Co",    sort_cols[2])
    sort_btn("name",        "Contact Name",  sort_cols[3])
    sort_btn("email",       "Email & MX",    sort_cols[4])
    sort_btn("lead_source", "Source",        sort_cols[5])
    sort_btn("priority",    "Priority",      sort_cols[6])
    sort_btn("contacted",   "Contacted?",    sort_cols[7])
    sort_btn("status",      "Status",        sort_cols[8])
    sort_btn("follow_ups",  "Follow-Ups",    sort_cols[9])
    sort_btn("owner",       "Owner",         sort_cols[10])
    with sort_cols[11]:
        st.markdown("<div style='font-size:11px; color:#64748B; text-align:center; font-weight:600; letter-spacing:.04em; text-transform:uppercase;'>ACTIONS</div>", unsafe_allow_html=True)

    # --- CRM TABLE ROWS ---
    if not filtered:
        st.info("No leads match your criteria. Use **➕ Add lead** or **📥 Import CSV** above.")
        return

    for c in filtered:
        lid = c.get("id") or 0
        lead_code = f"#SLM-{lid:04d}"
        company = c.get("company") or "—"
        name = c.get("name") or "—"
        email_val = c.get("email") or ""

        # MX Check (cached per email)
        mx_cache_key = f"mx_{email_val}"
        if mx_cache_key not in st.session_state:
            st.session_state[mx_cache_key] = verify_email_domain_mx(email_val) if "@" in email_val else (True, "OK", [])
        is_mx_valid, _, _ = st.session_state[mx_cache_key]

        src = c.get("lead_source") or "Amazon scrape"
        prio = c.get("priority") or "Med"
        contacted = c.get("contacted") or "No"
        st_val = c.get("status") or "New"
        follow_ups = int(c.get("follow_ups_sent") or 0)
        owner = c.get("owner") or "—"
        notes = c.get("notes") or "—"
        tags = c.get("tags") or ""

        is_selected = lid in st.session_state["crm_selected_ids"]

        # Row background for selected
        bg = "background:#F0F7F5;" if is_selected else ""

        with st.container():
            row_cols = st.columns([0.35, 0.7, 1.3, 1.3, 1.5, 0.9, 0.7, 0.75, 0.75, 0.7, 0.8, 0.8], vertical_alignment="center")

            # Checkbox
            with row_cols[0]:
                checked = st.checkbox(
                    f"sel_{lid}",
                    value=is_selected,
                    key=f"crm_row_chk_{lid}",
                    label_visibility="collapsed"
                )
                if checked != is_selected:
                    if checked:
                        st.session_state["crm_selected_ids"].add(lid)
                    else:
                        st.session_state["crm_selected_ids"].discard(lid)
                        st.session_state["crm_master_check"] = False
                    st.rerun()

            # Lead ID
            with row_cols[1]:
                st.markdown(f"<span style='font-family:monospace; font-size:12px; color:#083731; font-weight:600;'>{lead_code}</span>", unsafe_allow_html=True)

            # Brand / Company
            with row_cols[2]:
                st.markdown(f"<strong style='font-size:13px;'>{html_mod.escape(company)}</strong>", unsafe_allow_html=True)

            # Contact Name
            with row_cols[3]:
                st.markdown(f"<span style='font-size:13px;'>{html_mod.escape(name)}</span>", unsafe_allow_html=True)

            # Email & MX
            with row_cols[4]:
                mx_pill = '<span style="background:#E1F5EE; color:#0F6E56; font-size:10px; padding:1px 7px; border-radius:999px; font-weight:600; margin-left:5px;">MX ok</span>' if is_mx_valid else '<span style="background:#FEE2E2; color:#DC2626; font-size:10px; padding:1px 7px; border-radius:999px; font-weight:600; margin-left:5px;">bad MX</span>'
                st.markdown(f"<span style='font-family:monospace; font-size:12px; color:#083731;'>{html_mod.escape(email_val)}</span>{mx_pill}", unsafe_allow_html=True)

            # Source
            with row_cols[5]:
                st.markdown(f"<span style='font-size:12px; color:#475569;'>{html_mod.escape(src)}</span>", unsafe_allow_html=True)

            # Priority
            with row_cols[6]:
                if prio == "High":
                    pill = '<span style="background:#FFF1EC; color:#FD4D1B; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">High</span>'
                elif prio == "Low":
                    pill = '<span style="background:#F1F5F9; color:#64748B; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">Low</span>'
                else:
                    pill = f'<span style="background:#FEF3C7; color:#D97706; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">{html_mod.escape(prio)}</span>'
                st.markdown(pill, unsafe_allow_html=True)

            # Contacted?
            with row_cols[7]:
                if contacted.lower() == "yes":
                    c_pill = '<span style="background:#E1F5EE; color:#0F6E56; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">Yes</span>'
                else:
                    c_pill = '<span style="background:#F1F5F9; color:#64748B; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">No</span>'
                st.markdown(c_pill, unsafe_allow_html=True)

            # Status
            with row_cols[8]:
                if st_val == "New":
                    s_pill = '<span style="background:#FFF1EC; color:#FD4D1B; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">New</span>'
                elif st_val == "Emailed":
                    s_pill = '<span style="background:#F1F5F9; color:#64748B; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">Emailed</span>'
                elif st_val == "Replied":
                    s_pill = '<span style="background:#E1F5EE; color:#0F6E56; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">Replied</span>'
                elif st_val == "Bounced":
                    s_pill = '<span style="background:#FEE2E2; color:#DC2626; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">Bounced</span>'
                else:
                    s_pill = f'<span style="background:#FEF3C7; color:#D97706; font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600;">{html_mod.escape(st_val)}</span>'
                st.markdown(s_pill, unsafe_allow_html=True)

            # Follow-Ups
            with row_cols[9]:
                fu_color = "#0F6E56" if follow_ups > 0 else "#94A3B8"
                st.markdown(f"<span style='font-size:13px; color:{fu_color}; font-weight:600;'>{follow_ups}</span>", unsafe_allow_html=True)

            # Owner
            with row_cols[10]:
                st.markdown(f"<span style='font-size:12px; color:#475569;'>{html_mod.escape(owner)}</span>", unsafe_allow_html=True)

            # Actions
            with row_cols[11]:
                a1, a2 = st.columns(2)
                with a1:
                    if st.button("✏️", key=f"edit_lead_{lid}", help=f"Edit {name}", use_container_width=True):
                        render_edit_lead_dialog(c)
                with a2:
                    if st.button("✍️", key=f"compose_lead_{lid}", help=f"Compose to {name}", use_container_width=True):
                        st.session_state["compose_selected_lead_id"] = lid
                        st.session_state["active_screen"] = "compose"
                        st.rerun()

        # Thin separator line
        st.markdown("<div style='border-bottom:1px solid #E2E8F0; margin:0 0 2px 0;'></div>", unsafe_allow_html=True)

    # --- SELECTION SUMMARY BAR ---
    sel_count = len(st.session_state["crm_selected_ids"])
    total_shown = len(filtered)

    st.markdown(
        f"<div style='display:flex; align-items:center; gap:12px; margin-top:10px; padding:8px 14px; "
        f"background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; font-size:12px; color:#64748B;'>"
        f"<span>Showing <strong style='color:#083731;'>{total_shown}</strong> lead{'s' if total_shown != 1 else ''} · "
        f"<strong style='color:#083731;'>{sel_count}</strong> selected</span>"
        f"</div>",
        unsafe_allow_html=True
    )

    st.markdown("<p class='sec' style='margin-top:10px;'>CRM Columns: Lead ID · Brand / Company · Contact Name · Email &amp; MX · Lead Source · Priority · Contacted? · Status · Follow-Ups · Owner · Notes · Tags. Statuses: New · Emailed · Replied · Bounced · Do Not Contact.</p>", unsafe_allow_html=True)
