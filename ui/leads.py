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
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from database import (
    get_contacts,
    get_contact_by_id,
    create_contact,
    update_contact,
    delete_contact,
    bulk_delete_contacts,
    bulk_update_contacts,
    bulk_modify_contact_tags,
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
PRIORITY_OPTS  = ["High", "Medium", "Low"]
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
    "First Contacted": "date_first_emailed",
    "Last Contacted":  "last_contact_date",
    "Next Follow-Up":  "next_follow_up",
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
            owner       = st.text_input("Owner", value="Jack Connor")
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

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
        st.markdown("**🏷️ Tags**")
        tag_c1, tag_c2 = st.columns(2)
        with tag_c1:
            add_tags_input = st.text_input(
                "➕ Add Tag(s)",
                placeholder="e.g. VIP, Amazon (comma-separated)",
                help="Appends these tags to selected leads without overwriting existing tags",
            )
        with tag_c2:
            remove_tags_input = st.text_input(
                "➖ Remove Tag(s)",
                placeholder="e.g. OldLead, Inactive (comma-separated)",
                help="Removes these tags from selected leads if present",
            )
        clear_all_tags = st.checkbox("🗑️ Clear all tags from selected leads", value=False)

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

        has_tag_change = bool(add_tags_input.strip() or remove_tags_input.strip() or clear_all_tags)
        if not updates and not has_tag_change:
            st.warning("No changes specified — please fill at least one field or tag action.")
        else:
            affected = 0
            if updates:
                affected = bulk_update_contacts(selected_ids, updates)
            if has_tag_change:
                add_list = [t.strip() for t in add_tags_input.split(",") if t.strip()]
                rem_list = [t.strip() for t in remove_tags_input.split(",") if t.strip()]
                tag_affected = bulk_modify_contact_tags(
                    selected_ids,
                    add_tags=add_list if add_list else None,
                    remove_tags=rem_list if rem_list else None,
                    clear_all=clear_all_tags,
                )
                affected = max(affected, tag_affected)

            st.session_state["crm_selected_ids"] = set()
            st.session_state.pop("crm_data_editor", None)
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
            st.session_state.pop("crm_data_editor", None)
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
    if active_filter == "Not Contacted":
        f = [c for c in f if (c.get("status") or "New") == "New"]
    elif active_filter == "Emailed":
        f = [c for c in f if (c.get("status") or "").lower() == "emailed"]
    elif active_filter == "Opened":
        f = [c for c in f if (c.get("status") or "").lower() == "opened"]
    elif active_filter == "Bounced":
        f = [c for c in f if (c.get("status") or "").lower() == "bounced"]
    elif active_filter == "High Priority":
        f = [c for c in f if (c.get("priority") or "").lower() in ["high", "hi"]]

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
    """Render the full CRM leads screen with instant inline click-to-edit, master selection, and fast rendering."""
    if all_contacts is None:
        all_contacts = get_contacts()

    # Session state defaults & synchronization
    if "crm_selected_ids" not in st.session_state:
        st.session_state["crm_selected_ids"] = set()
    # Prune any deleted or stale lead IDs so counts always match 100% accurately
    valid_id_set = {c["id"] for c in all_contacts if c.get("id")}
    st.session_state["crm_selected_ids"] = {cid for cid in st.session_state["crm_selected_ids"] if cid in valid_id_set}

    if "lead_filter_pill" not in st.session_state:
        st.session_state["lead_filter_pill"] = "All leads"

    # =========================================================================
    # TOP TOOLBAR
    # =========================================================================
    col_tools, col_search = st.columns([3.2, 1.8], vertical_alignment="center")

    with col_tools:
        c1, c2, c3 = st.columns(3, vertical_alignment="center")
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
                    up_file.seek(0)
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
            if st.button("➕ Add lead (Form)", type="primary", use_container_width=True, help="Open full lead creation form"):
                render_add_lead_dialog()

    with col_search:
        search_query = st.text_input(
            "Search",
            placeholder="Search name, company, email…",
            label_visibility="collapsed",
            key="crm_lead_search",
        )

    # =========================================================================
    # FUNNEL FILTER PILLS (Matching live database counts)
    # =========================================================================
    cnt_total = len(all_contacts)
    cnt_not_contacted = sum(1 for c in all_contacts if (c.get("status") or "New") == "New")
    cnt_emailed = sum(1 for c in all_contacts if (c.get("status") or "").lower() == "emailed")
    cnt_opened = sum(1 for c in all_contacts if (c.get("status") or "").lower() == "opened")
    cnt_bounced = sum(1 for c in all_contacts if (c.get("status") or "").lower() == "bounced")

    filter_tabs = [
        ("All leads", f"All leads ({cnt_total})"),
        ("Not Contacted", f"Not Contacted ({cnt_not_contacted})"),
        ("Emailed", f"Emailed ({cnt_emailed})"),
        ("Opened", f"Opened ({cnt_opened})"),
        ("Bounced", f"Bounced ({cnt_bounced})"),
    ]

    pill_cols = st.columns(len(filter_tabs), vertical_alignment="center")
    for idx, (f_key, f_label) in enumerate(filter_tabs):
        with pill_cols[idx]:
            is_active = st.session_state["lead_filter_pill"] == f_key
            if st.button(
                f_label, key=f"crm_pill_{idx}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state["lead_filter_pill"] = f_key
                st.rerun()

    # =========================================================================
    # FILTER + GUARD
    # =========================================================================
    filtered = _apply_filters(all_contacts, st.session_state["lead_filter_pill"], search_query)

    if not filtered:
        st.info("No leads match your criteria. Use **➕ Add lead** or **📥 Import CSV** above.")
        return

    # Visible contact IDs
    id_list: List[int] = [c.get("id") or 0 for c in filtered]
    visible_set = set(id_list)
    selected_set = st.session_state["crm_selected_ids"]
    selected_visible = visible_set.intersection(selected_set)
    all_visible_selected = bool(visible_set and len(selected_visible) == len(visible_set))

    # =========================================================================
    # =========================================================================
    # UNIFIED SELECTION & ACTION BAR
    # =========================================================================
    vis_sel_count = len(selected_visible)

    if vis_sel_count > 0:
        with st.container(border=True):
            u_c1, u_c2, u_c3, u_c4, u_c5 = st.columns([3.2, 1.9, 1.6, 1.6, 1.4], vertical_alignment="center")
            with u_c1:
                master_label = f"Deselect all visible ({len(id_list)})" if all_visible_selected else f"Select all visible ({len(id_list)})"
                master_toggled = st.checkbox(
                    f"**{master_label}** · ⚡ **{vis_sel_count} selected**",
                    value=all_visible_selected,
                    key="crm_master_select_all",
                    help="Check to select all visible leads. Uncheck to deselect visible leads.",
                )
                if master_toggled != all_visible_selected:
                    if master_toggled:
                        st.session_state["crm_selected_ids"].update(visible_set)
                    else:
                        st.session_state["crm_selected_ids"].difference_update(visible_set)
                    st.session_state.pop("crm_data_editor", None)
                    st.rerun()
            with u_c2:
                if st.button("✍️ Compose Batch", type="primary", use_container_width=True, key="bar_btn_compose_batch"):
                    st.session_state["bulk_target_leads"] = list(selected_visible if selected_visible else selected_set)
                    st.session_state["active_screen"] = "bulk"
                    st.rerun()
            with u_c3:
                if st.button("✏️ Bulk Edit", use_container_width=True, key="bar_btn_bulk_edit"):
                    target_ids = list(selected_visible if selected_visible else selected_set)
                    render_bulk_edit_dialog(target_ids, len(target_ids))
            with u_c4:
                if st.button("🗑️ Bulk Delete", use_container_width=True, key="bar_btn_bulk_del"):
                    target_ids = list(selected_visible if selected_visible else selected_set)
                    render_bulk_delete_dialog(target_ids, len(target_ids))
            with u_c5:
                if st.button("✖ Clear", use_container_width=True, key="bar_btn_clear"):
                    st.session_state["crm_selected_ids"] = set()
                    st.session_state.pop("crm_data_editor", None)
                    st.rerun()
    else:
        # Borderless, aligned directly over the table's Select column
        st.markdown(
            """
            <style>
            div[data-testid="stCheckbox"]:has(input[aria-label*="Select all visible"]) {
                margin-bottom: -10px;
                padding-bottom: 0px;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        u_c1, u_c2 = st.columns([2.5, 7.5], vertical_alignment="center")
        with u_c1:
            master_toggled = st.checkbox(
                f"Select all visible ({len(id_list)})",
                value=False,
                key="crm_master_select_all",
                help="Check to select all visible leads in this view",
            )
            if master_toggled:
                st.session_state["crm_selected_ids"].update(visible_set)
                st.session_state.pop("crm_data_editor", None)
                st.rerun()
        with u_c2:
            st.caption("💡 **Click any cell to edit · Press Enter to save to database** · Click column headers to sort")

    # =========================================================================
    # BUILD DATAFRAME — Checkbox column first
    # =========================================================================
    rows: List[Dict[str, Any]] = []
    for c in filtered:
        lid = c.get("id") or 0
        rows.append({
            "☑":               lid in selected_set,
            "Lead ID":         f"#SLM-{lid:04d}",
            "Brand / Company": c.get("company") or "",
            "Contact Name":    c.get("name") or "",
            "Email":           c.get("email") or "",
            "MX":              "✓ ok" if _mx_ok(c.get("email") or "") else "✗ bad",
            "Lead Source":     c.get("lead_source") or "Amazon scrape",
            "Priority":        c.get("priority") or "Medium",
            "Contacted?":      c.get("contacted") or "No",
            "Status":          c.get("status") or "New",
            "First Contacted": c.get("date_first_emailed") or "",
            "Last Contacted":  c.get("last_contact_date") or "",
            "Next Follow-Up":  c.get("next_follow_up") or "",
            "Follow-Ups":      int(c.get("follow_ups_sent") or 0),
            "Owner":           c.get("owner") or "",
            "Notes":           c.get("notes") or "",
            "Tags":            c.get("tags") or "",
        })

    original_df = pd.DataFrame(rows)

    col_cfg = {
        "☑": st.column_config.CheckboxColumn(
            "Select",
            help="Check to select contact for batch outreach or bulk actions",
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
        "First Contacted": st.column_config.TextColumn("First Contacted", width="small", help="Date initial email was dispatched (YYYY-MM-DD)"),
        "Last Contacted":  st.column_config.TextColumn("Last Contacted",  width="small", help="Date of most recent outreach (YYYY-MM-DD)"),
        "Next Follow-Up":  st.column_config.TextColumn("Next Follow-Up",  width="small", help="Target date for next follow-up sequence (YYYY-MM-DD)"),
        "Follow-Ups":      st.column_config.NumberColumn("Follow-Ups",    disabled=True, width="small", format="%d"),
        "Owner":           st.column_config.TextColumn("Owner",           width="small"),
        "Notes":           st.column_config.TextColumn("Notes",           width="large"),
        "Tags":            st.column_config.TextColumn("Tags",            width="medium"),
    }

    # Only computed/system columns are read-only; all lead details are click-to-edit!
    disabled_cols = ["Lead ID", "MX", "Follow-Ups"]

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
    # 1. Sync row checkboxes -> crm_selected_ids
    # -------------------------------------------------------------------------
    new_visible_selected = set()
    for i, val in enumerate(edited_df["☑"]):
        if val:
            new_visible_selected.add(id_list[i])

    outside_visible_selected = st.session_state["crm_selected_ids"] - visible_set
    merged_selected = outside_visible_selected | new_visible_selected

    if merged_selected != st.session_state["crm_selected_ids"]:
        st.session_state["crm_selected_ids"] = merged_selected
        st.session_state.pop("crm_data_editor", None)
        st.rerun()

    # -------------------------------------------------------------------------
    # 2. Save inline cell edits (Click to edit, Enter to save directly to DB)
    # -------------------------------------------------------------------------
    edit_original = original_df.drop(columns=["☑", "MX"], errors="ignore")
    edit_new      = edited_df.drop(columns=["☑", "MX"], errors="ignore")
    saved_count   = _save_inline_edits(edit_new, edit_original, id_list)
    if saved_count > 0:
        st.session_state.pop("crm_data_editor", None)
        trigger_toast(f"Saved {saved_count} inline change{'s' if saved_count != 1 else ''} to database.", icon="💾")
        st.rerun()

    # =========================================================================
    # STATUS BAR
    # =========================================================================
    total_shown = len(filtered)
    total_selected = len(st.session_state.get("crm_selected_ids", set()))
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;margin-top:8px;padding:7px 14px;"
        f"background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;font-size:12px;color:#64748B;'>"
        f"Showing <strong style='color:#083731;'>{total_shown}</strong> lead{'s' if total_shown != 1 else ''} · "
        f"<strong style='color:#083731;'>{total_selected}</strong> selected · "
        f"Click any cell to edit · Press Enter to save to database · Click column header to sort"
        f"</div>",
        unsafe_allow_html=True,
    )
