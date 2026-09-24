"""
ui/leads.py - Leads Directory for Sellomize Reach.

Features:
- Excel-like editable cells via st.data_editor (click any cell to edit).
  The FIRST column is a native CheckboxColumn — clicking its header selects/
  deselects ALL visible rows (standard spreadsheet behaviour).
  Column headers are sortable by clicking — built-in to data_editor.
- Inline edits are saved to the DB immediately (manual edits have priority).
- Bulk Edit & Bulk Delete via @st.dialog popups.
- Add Lead, Edit Lead (full form dialog), Compose quick-launch per row.
- CSV template & export aligned to table column order exactly.
"""

import streamlit as st
import html as html_mod
import pandas as pd
from typing import List, Dict, Any, Optional

from database import (
    get_contacts,
    get_contact_by_id,
    create_contact,
    update_contact,
    delete_contact,
    bulk_delete_contacts,
    bulk_update_contacts,
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
# Constants
# ---------------------------------------------------------------------------
PRIORITY_OPTS  = ["High", "Med", "Low"]
CONTACTED_OPTS = ["Yes", "No"]
SOURCE_OPTS    = ["Amazon scrape", "LinkedIn", "Referral", "Website", "Other"]

# Editable column display → DB field name
_EDITABLE_COLS: Dict[str, str] = {
    "Brand / Company": "company",
    "Contact Name":    "name",
    "Email":           "email",
    "Lead Source":     "lead_source",
    "Priority":        "priority",
    "Contacted?":      "contacted",
    "Status":          "status",
    "Owner":           "owner",
    "Notes":           "notes",
    "Tags":            "tags",
}


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

@st.dialog("➕ Add New Lead")
def render_add_lead_dialog():
    with st.form("form_add_lead", clear_on_submit=True):
        st.markdown("#### New Lead Details")
        c1, c2 = st.columns(2)
        with c1:
            name        = st.text_input("Contact Name *", placeholder="e.g. Danessa Myricks")
            email       = st.text_input("Email Address *", placeholder="e.g. danessa@dmbeauty.com")
            company     = st.text_input("Brand / Company", placeholder="e.g. DM Beauty")
            owner       = st.text_input("Owner", value="Jack Conner")
        with c2:
            lead_source = st.selectbox("Lead Source", SOURCE_OPTS)
            priority    = st.selectbox("Priority", PRIORITY_OPTS, index=1)
            status      = st.selectbox("Status", LEAD_STATUSES, index=0)
            tags        = st.text_input("Tags (comma separated)", placeholder="beauty, amazon, high-intent")
        notes = st.text_area("Notes", placeholder="Listings unavailable, weak A+, low SEO...")

        if st.form_submit_button("Save Lead", type="primary", use_container_width=True):
            if not email.strip() or "@" not in email:
                st.error("Please enter a valid email address.")
                return
            create_contact(
                name=name.strip(), email=email.strip(), company=company.strip(),
                status=status, lead_source=lead_source, priority=priority,
                owner=owner.strip(), notes=notes.strip(), tags=tags.strip()
            )
            trigger_toast(f"Lead '{name or email}' added!", icon="✅")
            st.rerun()


@st.dialog("✏️ Edit Lead")
def render_edit_lead_dialog(lead: Dict[str, Any]):
    lid = lead["id"]
    with st.form(f"form_edit_{lid}"):
        st.markdown(f"#### Edit Lead #SLM-{lid:04d}")
        c1, c2 = st.columns(2)
        with c1:
            name    = st.text_input("Contact Name",    value=lead.get("name") or "")
            email   = st.text_input("Email Address",   value=lead.get("email") or "", disabled=True)
            company = st.text_input("Brand / Company", value=lead.get("company") or "")
            owner   = st.text_input("Owner",           value=lead.get("owner") or "Jack Conner")
        with c2:
            curr_src = lead.get("lead_source") or SOURCE_OPTS[0]
            lead_source = st.selectbox("Lead Source", SOURCE_OPTS,
                index=SOURCE_OPTS.index(curr_src) if curr_src in SOURCE_OPTS else 0)
            curr_p = lead.get("priority") or "Med"
            priority = st.selectbox("Priority", PRIORITY_OPTS,
                index=PRIORITY_OPTS.index(curr_p) if curr_p in PRIORITY_OPTS else 1)
            curr_s = lead.get("status") or "New"
            status = st.selectbox("Status", LEAD_STATUSES,
                index=LEAD_STATUSES.index(curr_s) if curr_s in LEAD_STATUSES else 0)
            tags = st.text_input("Tags", value=lead.get("tags") or "")
        notes = st.text_area("Notes", value=lead.get("notes") or "")

        col_save, col_del = st.columns([3, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Update Lead", type="primary", use_container_width=True)
        with col_del:
            del_clicked  = st.form_submit_button("🗑️ Delete", use_container_width=True)

    if save_clicked:
        update_contact(
            contact_id=lid, name=name.strip(), company=company.strip(),
            status=status, lead_source=lead_source, priority=priority,
            owner=owner.strip(), notes=notes.strip(), tags=tags.strip()
        )
        trigger_toast(f"Lead #SLM-{lid:04d} updated!", icon="✅")
        st.rerun()
    if del_clicked:
        delete_contact(lid)
        trigger_toast("Lead deleted.", icon="🗑️")
        st.rerun()


@st.dialog("✏️ Bulk Edit Selected Leads")
def render_bulk_edit_dialog(selected_ids: List[int], count: int):
    st.markdown(f"Editing **{count} lead{'s' if count != 1 else ''}**. Leave blank = keep existing value.")
    with st.form("bulk_edit_form"):
        c1, c2 = st.columns(2)
        with c1:
            new_source    = st.selectbox("Lead Source",  [""] + SOURCE_OPTS,    index=0)
            new_priority  = st.selectbox("Priority",     [""] + PRIORITY_OPTS,  index=0)
            new_contacted = st.selectbox("Contacted?",   [""] + CONTACTED_OPTS, index=0)
        with c2:
            new_status  = st.selectbox("Status",         [""] + LEAD_STATUSES,  index=0)
            new_owner   = st.text_input("Owner",         placeholder="Leave blank to keep current")
            new_company = st.text_input("Brand / Company", placeholder="Leave blank to keep current")
        new_notes = st.text_area("Notes", placeholder="Leave blank to keep current")

        ca, cc = st.columns([2, 1])
        with ca:
            apply  = st.form_submit_button("✅ Apply Changes", type="primary", use_container_width=True)
        with cc:
            cancel = st.form_submit_button("✖️ Cancel", use_container_width=True)

    if cancel:
        st.rerun()
    if apply:
        updates: Dict[str, Any] = {}
        if new_source:          updates["lead_source"] = new_source
        if new_priority:        updates["priority"]    = new_priority
        if new_contacted:       updates["contacted"]   = new_contacted
        if new_status:          updates["status"]      = new_status
        if new_owner.strip():   updates["owner"]       = new_owner.strip()
        if new_company.strip(): updates["company"]     = new_company.strip()
        if new_notes.strip():   updates["notes"]       = new_notes.strip()
        if not updates:
            st.warning("No changes specified — please fill at least one field.")
        else:
            affected = bulk_update_contacts(selected_ids, updates)
            st.session_state["crm_selected_ids"] = set()
            trigger_toast(f"Updated {affected} lead{'s' if affected != 1 else ''}!", icon="✅")
            st.rerun()


@st.dialog("🗑️ Bulk Delete Selected Leads")
def render_bulk_delete_dialog(selected_ids: List[int], count: int):
    st.error(f"⚠️ Permanently delete **{count} lead{'s' if count != 1 else ''}**? This cannot be undone.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🗑️ Yes, Delete All", type="primary", use_container_width=True):
            deleted = bulk_delete_contacts(selected_ids)
            st.session_state["crm_selected_ids"] = set()
            trigger_toast(f"Deleted {deleted} lead{'s' if deleted != 1 else ''}.", icon="🗑️")
            st.rerun()
    with c2:
        if st.button("✖️ Cancel", use_container_width=True):
            st.rerun()


# ---------------------------------------------------------------------------
# MX cache (DNS lookup once per email per session)
# ---------------------------------------------------------------------------

def _mx_ok(email_val: str) -> bool:
    if not email_val or "@" not in email_val:
        return True
    k = f"mx_{email_val}"
    if k not in st.session_state:
        ok, _, _ = verify_email_domain_mx(email_val)
        st.session_state[k] = ok
    return st.session_state[k]


# ---------------------------------------------------------------------------
# Inline-edit save: detect changed cells, write only those to DB
# ---------------------------------------------------------------------------

def _save_inline_edits(
    edited_df: pd.DataFrame,
    original_df: pd.DataFrame,
    id_list: List[int],
) -> int:
    """
    Compare edited_df vs original_df row-by-row for every editable column.
    Only changed cells are written to the database (manual edits have priority).
    Returns number of DB field updates made.
    """
    saved = 0
    for idx in range(len(edited_df)):
        row_id = id_list[idx]
        for display_col, db_col in _EDITABLE_COLS.items():
            if display_col not in edited_df.columns:
                continue
            new_val = edited_df.at[idx, display_col]
            old_val = original_df.at[idx, display_col]
            if str(new_val).strip() != str(old_val).strip():
                update_contact(contact_id=row_id, **{db_col: str(new_val).strip()})
                saved += 1
    return saved


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _apply_filters(
    contacts: List[Dict[str, Any]],
    active_filter: str,
    search_query: str,
) -> List[Dict[str, Any]]:
    f = list(contacts)
    if active_filter == "Cold outreach":
        f = [c for c in f if (c.get("status") or "New") == "New"]
    elif active_filter == "Follow-up #1":
        f = [c for c in f if int(c.get("follow_ups_sent") or 0) == 1]
    elif active_filter == "Follow-up #2+":
        f = [c for c in f if int(c.get("follow_ups_sent") or 0) >= 2]
    elif active_filter == "Opened":
        f = [c for c in f if (c.get("contacted") or "").lower() == "yes"]
    elif active_filter == "High intent":
        f = [c for c in f if (c.get("priority") or "").lower() == "high"]
    elif active_filter == "Due today":
        f = [c for c in f if (c.get("status") or "New") in ["New", "Emailed"]]

    if search_query.strip():
        q = search_query.strip().lower()
        f = [
            c for c in f
            if q in (c.get("name") or "").lower()
            or q in (c.get("email") or "").lower()
            or q in (c.get("company") or "").lower()
            or q in (c.get("tags") or "").lower()
            or q in (c.get("notes") or "").lower()
        ]
    return f


# ---------------------------------------------------------------------------
# Main leads tab
# ---------------------------------------------------------------------------

def render_leads_tab(all_contacts: Optional[List[Dict[str, Any]]] = None):
    """Render the full CRM leads screen with Excel-like inline editing."""
    if all_contacts is None:
        all_contacts = get_contacts()

    # Session state defaults
    for key, default in [
        ("crm_selected_ids", set()),
        ("crm_edit_mode",    True),
        ("lead_filter_pill", "All leads"),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # =========================================================================
    # TOP TOOLBAR
    # =========================================================================
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
                    use_container_width=True,
                )
                up_file = st.file_uploader("Upload CSV", type=["csv"], key="crm_csv_upload")
                if up_file is not None and st.button("Run Import", type="primary", use_container_width=True):
                    res = import_contacts_from_csv(up_file.read())
                    trigger_toast(f"Imported {res['inserted']} leads ({res['updated']} updated).", icon="✅")
                    st.rerun()
        with c2:
            st.download_button(
                "📤 Export CSV",
                data=export_contacts_to_csv(all_contacts),
                file_name="sellomize_leads.csv",
                mime="text/csv",
                use_container_width=True,
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
                    st.caption("Tick rows using the ☑ column, then choose an action.")
                else:
                    st.markdown(f"**{sel_count} lead{'s' if sel_count != 1 else ''} selected**")
                    if st.button("✏️ Bulk Edit Selected",   use_container_width=True, key="btn_bulk_edit"):
                        render_bulk_edit_dialog(list(st.session_state["crm_selected_ids"]), sel_count)
                    if st.button("🗑️ Bulk Delete Selected", use_container_width=True, key="btn_bulk_delete"):
                        render_bulk_delete_dialog(list(st.session_state["crm_selected_ids"]), sel_count)
                st.divider()
                if st.button("◻️ Clear All Selections", use_container_width=True):
                    st.session_state["crm_selected_ids"] = set()
                    st.rerun()

    with col_search:
        search_query = st.text_input(
            "Search",
            placeholder="Search name, company, email…",
            label_visibility="collapsed",
            key="crm_lead_search",
        )

    # Edit-mode toggle
    em_left, em_right, _ = st.columns([1.4, 1.4, 5], vertical_alignment="center")
    with em_left:
        is_edit = st.session_state["crm_edit_mode"]
        if st.button(
            "✏️ Edit Mode: ON" if is_edit else "👁️ View Mode",
            type="primary" if is_edit else "secondary",
            use_container_width=True,
            help="Toggle inline cell editing on/off",
        ):
            st.session_state["crm_edit_mode"] = not is_edit
            st.rerun()

    # =========================================================================
    # FUNNEL FILTER PILLS
    # =========================================================================
    filter_keys = ["All leads", "Cold outreach", "Follow-up #1", "Follow-up #2+", "Opened", "High intent", "Due today"]
    pill_cols = st.columns([1, 1.2, 1.15, 1.15, 0.9, 1.1, 1], vertical_alignment="center")
    for idx, f_name in enumerate(filter_keys):
        with pill_cols[idx]:
            is_active = st.session_state["lead_filter_pill"] == f_name
            if st.button(
                f_name, key=f"crm_pill_{idx}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state["lead_filter_pill"] = f_name
                st.rerun()

    # =========================================================================
    # FILTER + GUARD
    # =========================================================================
    filtered = _apply_filters(all_contacts, st.session_state["lead_filter_pill"], search_query)

    if not filtered:
        st.info("No leads match your criteria. Use **➕ Add lead** or **📥 Import CSV** above.")
        return

    # =========================================================================
    # BUILD DATAFRAME  —  first column is the native checkbox column
    # =========================================================================
    id_list: List[int] = []
    rows: List[Dict[str, Any]] = []

    for c in filtered:
        lid = c.get("id") or 0
        id_list.append(lid)
        rows.append({
            # ☑ is a CheckboxColumn — its header checkbox = select / deselect all
            "☑":               lid in st.session_state["crm_selected_ids"],
            "Lead ID":         f"#SLM-{lid:04d}",
            "Brand / Company": c.get("company") or "",
            "Contact Name":    c.get("name") or "",
            "Email":           c.get("email") or "",
            "MX":              "✓ ok" if _mx_ok(c.get("email") or "") else "✗ bad",
            "Lead Source":     c.get("lead_source") or "Amazon scrape",
            "Priority":        c.get("priority") or "Med",
            "Contacted?":      c.get("contacted") or "No",
            "Status":          c.get("status") or "New",
            "Follow-Ups":      int(c.get("follow_ups_sent") or 0),
            "Owner":           c.get("owner") or "",
            "Notes":           c.get("notes") or "",
            "Tags":            c.get("tags") or "",
        })

    original_df = pd.DataFrame(rows)

    # =========================================================================
    # COLUMN CONFIG
    # Checkbox first → native header "select all" behaviour built-in.
    # All other data columns are sortable by clicking the header (data_editor default).
    # =========================================================================
    col_cfg = {
        "☑": st.column_config.CheckboxColumn(
            "☑",
            help="Tick to select · Click header to select/deselect ALL",
            default=False,
            width="small",
        ),
        "Lead ID":         st.column_config.TextColumn("Lead ID",         disabled=True, width="small"),
        "Brand / Company": st.column_config.TextColumn("Brand / Company", width="medium"),
        "Contact Name":    st.column_config.TextColumn("Contact Name",    width="medium"),
        "Email":           st.column_config.TextColumn("Email",           width="medium"),
        "MX":              st.column_config.TextColumn("MX",              disabled=True, width="small"),
        "Lead Source":     st.column_config.SelectboxColumn("Lead Source", options=SOURCE_OPTS,    width="small"),
        "Priority":        st.column_config.SelectboxColumn("Priority",    options=PRIORITY_OPTS,  width="small"),
        "Contacted?":      st.column_config.SelectboxColumn("Contacted?",  options=CONTACTED_OPTS, width="small"),
        "Status":          st.column_config.SelectboxColumn("Status",      options=LEAD_STATUSES,  width="small"),
        "Follow-Ups":      st.column_config.NumberColumn("Follow-Ups",    disabled=True, width="small", format="%d"),
        "Owner":           st.column_config.TextColumn("Owner",           width="small"),
        "Notes":           st.column_config.TextColumn("Notes",           width="large"),
        "Tags":            st.column_config.TextColumn("Tags",            width="medium"),
    }

    # =========================================================================
    # RENDER DATA EDITOR
    # Click column header → sort (ascending / descending, built-in)
    # Click ☑ header   → select all / deselect all (built-in CheckboxColumn)
    # Click any cell   → edit inline (when Edit Mode is ON)
    # =========================================================================
    edit_mode = st.session_state["crm_edit_mode"]

    # Columns that must always remain read-only
    always_disabled = ["☑", "Lead ID", "MX", "Follow-Ups"]
    disabled_cols   = always_disabled if edit_mode else True   # True = all disabled

    edited_df = st.data_editor(
        original_df,
        column_config=col_cfg,
        use_container_width=True,
        hide_index=True,
        disabled=disabled_cols,
        num_rows="fixed",
        key="crm_data_editor",
    )

    # -------------------------------------------------------------------------
    # 1. Sync checkbox column → crm_selected_ids
    #    (The ☑ column is always editable regardless of edit_mode so selection works)
    # -------------------------------------------------------------------------
    new_selected: set = set()
    for i, val in enumerate(edited_df["☑"]):
        if val:
            new_selected.add(id_list[i])

    if new_selected != st.session_state["crm_selected_ids"]:
        st.session_state["crm_selected_ids"] = new_selected
        # Don't rerun here — let Streamlit's next natural rerun pick it up
        # so we don't fight the data_editor state

    # -------------------------------------------------------------------------
    # 2. Save inline cell edits (manual edits have priority — written first)
    # -------------------------------------------------------------------------
    if edit_mode:
        # Build a version of original_df excluding the ☑ and MX columns for comparison
        edit_original = original_df.drop(columns=["☑", "MX"], errors="ignore")
        edit_new      = edited_df.drop(columns=["☑", "MX"], errors="ignore")
        saved_count   = _save_inline_edits(edit_new, edit_original, id_list)
        if saved_count > 0:
            trigger_toast(f"Saved {saved_count} inline change{'s' if saved_count != 1 else ''}.", icon="💾")
            st.rerun()

    # =========================================================================
    # PER-ROW ACTION BUTTONS  (✏️ full edit dialog  |  ✍️ compose)
    # A slim row of buttons beneath the table aligned to each lead row.
    # =========================================================================
    st.markdown(
        "<div style='font-size:11px;font-weight:600;color:#64748B;letter-spacing:.05em;"
        "text-transform:uppercase;padding:6px 0 2px;border-top:1px solid #E2E8F0;'>"
        "Row Actions</div>",
        unsafe_allow_html=True,
    )

    for idx, c in enumerate(filtered):
        lid  = c.get("id") or 0
        name = c.get("name") or f"#SLM-{lid:04d}"

        act_c = st.columns([0.6, 8, 1.2], vertical_alignment="center")
        with act_c[0]:
            st.markdown(
                f"<span style='font-family:monospace;font-size:11px;color:#083731;"
                f"font-weight:600;'>#SLM-{lid:04d}</span>",
                unsafe_allow_html=True,
            )
        with act_c[1]:
            st.markdown(
                f"<span style='font-size:12px;color:#475569;'>{html_mod.escape(name)}</span>",
                unsafe_allow_html=True,
            )
        with act_c[2]:
            b1, b2 = st.columns(2)
            with b1:
                if st.button("✏️", key=f"edit_lead_{lid}",
                             help=f"Full edit — {name}", use_container_width=True):
                    render_edit_lead_dialog(c)
            with b2:
                if st.button("✍️", key=f"compose_lead_{lid}",
                             help=f"Compose email to {name}", use_container_width=True):
                    st.session_state["compose_selected_lead_id"] = lid
                    st.session_state["active_screen"] = "compose"
                    st.rerun()

        st.markdown(
            "<div style='border-bottom:1px solid #F1F5F9;margin:1px 0;'></div>",
            unsafe_allow_html=True,
        )

    # =========================================================================
    # STATUS BAR
    # =========================================================================
    sel_count   = len(st.session_state["crm_selected_ids"])
    total_shown = len(filtered)
    edit_hint   = "Click any cell to edit · Click ☑ header to select all." if edit_mode else "Switch to ✏️ Edit Mode to edit cells."

    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;margin-top:8px;padding:7px 14px;"
        f"background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;font-size:12px;color:#64748B;'>"
        f"Showing <strong style='color:#083731;'>{total_shown}</strong> lead{'s' if total_shown != 1 else ''} · "
        f"<strong style='color:#083731;'>{sel_count}</strong> selected · {edit_hint}"
        f"</div>",
        unsafe_allow_html=True,
    )
