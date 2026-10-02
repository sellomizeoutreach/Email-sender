"""
ui/lead_selector.py - Advanced Column-Based Lead Filtering and Selection
Provides unified filtering by column headers (Lead Source, Status, Priority, Tags, Contacted, Custom Variables, Search)
and interactive multi-column selection across Campaigns and Bulk Send.
"""

import html
import json
from typing import List, Dict, Any, Set, Tuple, Optional
import streamlit as st
from database import enroll_contacts_in_campaign
from ui.components import trigger_toast


def extract_lead_column_metadata(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract all unique column headers and their distinct values from the leads dataset.
    """
    sources: Set[str] = set()
    statuses: Set[str] = set()
    priorities: Set[str] = {"High", "Medium", "Low"}
    all_tags: Set[str] = set()
    contacted_opts: Set[str] = {"Yes", "No"}
    owners: Set[str] = set()
    custom_vars_map: Dict[str, Set[str]] = {}

    for c in leads:
        # Lead Source
        src = (c.get("lead_source") or "").strip()
        if src:
            sources.add(src)

        # Status
        st_val = (c.get("status") or "").strip()
        if st_val:
            statuses.add(st_val)

        # Priority
        pr = (c.get("priority") or "").strip()
        if pr:
            priorities.add(pr)

        # Contacted
        cnt = (c.get("contacted") or "").strip()
        if cnt:
            contacted_opts.add(cnt)

        # Owner
        ow = (c.get("owner") or "").strip()
        if ow:
            owners.add(ow)

        # Tags
        raw_tags = c.get("tags") or ""
        if isinstance(raw_tags, str):
            for t in raw_tags.split(","):
                ct = t.strip()
                if ct:
                    all_tags.add(ct)
        for t in c.get("tags_list", []):
            ct = str(t).strip()
            if ct:
                all_tags.add(ct)

        # Custom variables (e.g. amazon_issue, asin, product, category)
        cv_dict = c.get("custom_variables_dict")
        if not isinstance(cv_dict, dict) and isinstance(c.get("custom_variables"), str):
            try:
                cv_dict = json.loads(c.get("custom_variables"))
            except Exception:
                cv_dict = {}
        if isinstance(cv_dict, dict):
            for k, v in cv_dict.items():
                k_clean = str(k).strip()
                v_clean = str(v).strip()
                if k_clean and v_clean:
                    if k_clean not in custom_vars_map:
                        custom_vars_map[k_clean] = set()
                    custom_vars_map[k_clean].add(v_clean)

    # Standard status ordering
    known_statuses = ["New", "Emailed", "Opened", "Replied", "Bounced", "Do Not Contact"]
    ordered_statuses = [s for s in known_statuses if s in statuses]
    for s in sorted(statuses):
        if s not in ordered_statuses:
            ordered_statuses.append(s)

    # Priority ordering
    ordered_priorities = ["High", "Medium", "Low"]
    for p in sorted(priorities):
        if p not in ordered_priorities:
            ordered_priorities.append(p)

    return {
        "sources": sorted(list(sources)),
        "statuses": ordered_statuses,
        "priorities": ordered_priorities,
        "tags": sorted(list(all_tags)),
        "contacted": ["No", "Yes"],
        "owners": sorted(list(owners)),
        "custom_vars": {k: sorted(list(v)) for k, v in sorted(custom_vars_map.items())}
    }


def filter_leads_by_columns(
    leads: List[Dict[str, Any]],
    source_filter: str = "All",
    status_filter: str = "All",
    priority_filter: str = "All",
    tag_filter: str = "All",
    contacted_filter: str = "All",
    search_query: str = "",
    custom_key: str = "None",
    custom_val: str = "All",
    exclude_ids: Optional[Set[int]] = None,
    exclude_suppressed: bool = True
) -> List[Dict[str, Any]]:
    """
    Filter leads dynamically across all column header values.
    """
    exclude_ids = exclude_ids or set()
    clean_search = (search_query or "").strip().lower()

    filtered: List[Dict[str, Any]] = []

    for c in leads:
        cid = c.get("id")
        if cid in exclude_ids:
            continue

        c_status = (c.get("status") or "New").strip()
        if exclude_suppressed and c_status.lower() == "do not contact":
            continue

        # 1. Lead Source column filter
        if source_filter and source_filter != "All":
            c_source = (c.get("lead_source") or "Other").strip()
            if c_source != source_filter:
                continue

        # 2. Status column filter
        if status_filter and status_filter != "All":
            if c_status.lower() != status_filter.lower():
                continue

        # 3. Priority column filter
        if priority_filter and priority_filter != "All":
            c_priority = (c.get("priority") or "Medium").strip()
            if c_priority.lower() != priority_filter.lower():
                continue

        # 4. Tags column filter
        if tag_filter and tag_filter != "All":
            c_tags = [t.strip().lower() for t in (c.get("tags") or "").split(",") if t.strip()]
            for t in c.get("tags_list", []):
                c_tags.append(str(t).strip().lower())
            if tag_filter.strip().lower() not in c_tags:
                continue

        # 5. Contacted column filter
        if contacted_filter and contacted_filter != "All":
            c_contacted = (c.get("contacted") or "No").strip().lower()
            if c_contacted != contacted_filter.strip().lower():
                continue

        # 6. Custom Variable column filter (e.g. Amazon Issue, ASIN)
        if custom_key and custom_key != "None" and custom_val and custom_val != "All":
            cv_dict = c.get("custom_variables_dict")
            if not isinstance(cv_dict, dict) and isinstance(c.get("custom_variables"), str):
                try:
                    cv_dict = json.loads(c.get("custom_variables"))
                except Exception:
                    cv_dict = {}
            if not isinstance(cv_dict, dict) or cv_dict.get(custom_key) != custom_val:
                continue

        # 7. Search query across columns
        if clean_search:
            name_match = clean_search in (c.get("name") or "").lower()
            email_match = clean_search in (c.get("email") or "").lower()
            company_match = clean_search in (c.get("company") or "").lower()
            notes_match = clean_search in (c.get("notes") or "").lower()
            tag_match = clean_search in (c.get("tags") or "").lower()
            if not (name_match or email_match or company_match or notes_match or tag_match):
                continue

        filtered.append(c)

    return filtered


def render_lead_badge_html(
    company: str,
    name: str,
    email: str,
    source: str = "",
    status: str = "",
    priority: str = "",
    tags: str = "",
    custom_issue: str = ""
) -> str:
    """
    Format a clean HTML snippet showing a lead with its column badges.
    """
    # Priority badge color
    pr_bg, pr_col = "#F1F5F9", "#475569"
    if priority.lower() in ["high", "hi"]:
        pr_bg, pr_col = "#FEE2E2", "#DC2626"
    elif priority.lower() in ["medium", "med"]:
        pr_bg, pr_col = "#FEF3C7", "#D97706"
    elif priority.lower() in ["low"]:
        pr_bg, pr_col = "#F1F5F9", "#64748B"

    # Status badge color
    st_bg, st_col = "#E1F5EE", "#0F6E56"
    if status.lower() == "emailed":
        st_bg, st_col = "#EFF6FF", "#2563EB"
    elif status.lower() == "opened":
        st_bg, st_col = "#EEF2FF", "#4F46E5"
    elif status.lower() == "replied":
        st_bg, st_col = "#ECFDF5", "#059669"
    elif status.lower() == "bounced":
        st_bg, st_col = "#FEE2E2", "#DC2626"

    badges = []
    if source:
        badges.append(f"<span style='background:#F0FDF4; color:#083731; border:1px solid #BBF7D0; border-radius:4px; padding:1px 6px; font-size:11px; font-weight:600;'>Src: {html.escape(source)}</span>")
    if status:
        badges.append(f"<span style='background:{st_bg}; color:{st_col}; border-radius:4px; padding:1px 6px; font-size:11px; font-weight:600;'>{html.escape(status)}</span>")
    if priority:
        badges.append(f"<span style='background:{pr_bg}; color:{pr_col}; border-radius:4px; padding:1px 6px; font-size:11px; font-weight:600;'>{html.escape(priority)}</span>")
    if tags:
        for t in tags.split(",")[:3]:
            ct = t.strip()
            if ct:
                badges.append(f"<span style='background:#F8FAFC; color:#64748B; border:1px solid #E2E8F0; border-radius:4px; padding:1px 5px; font-size:10px;'>#{html.escape(ct)}</span>")
    if custom_issue:
        badges.append(f"<span style='background:#FFF1EC; color:#FD4D1B; border:1px solid #FED7AA; border-radius:4px; padding:1px 6px; font-size:11px; font-weight:600;'>{html.escape(custom_issue)}</span>")

    badge_str = " ".join(badges)

    return (
        f"<div style='display:flex; justify-content:space-between; align-items:center; width:100%; flex-wrap:wrap; gap:4px; margin-bottom:2px;'>"
        f"  <div>"
        f"    <strong style='color:#0F172A; font-size:13px;'>{html.escape(company or 'No Brand')}</strong> "
        f"    <span style='color:#64748B; font-size:12px;'>· {html.escape(name or 'Lead')} ({html.escape(email or 'no email')})</span>"
        f"  </div>"
        f"  <div style='display:flex; gap:4px; align-items:center; flex-wrap:wrap;'>"
        f"    {badge_str}"
        f"  </div>"
        f"</div>"
    )


def render_column_filter_controls(
    leads: List[Dict[str, Any]],
    key_prefix: str = "filt",
    exclude_ids: Optional[Set[int]] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Renders filter controls across different column headers and returns the filtered leads.
    """
    meta = extract_lead_column_metadata(leads)

    st.markdown("<div style='font-size:12px; font-weight:700; color:#083731; margin-bottom:4px;'>FILTER BY COLUMN HEADERS & VALUES:</div>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        source_opts = ["All"] + meta["sources"]
        sel_source = st.selectbox("Column: Lead Source", options=source_opts, key=f"{key_prefix}_src")
    with c2:
        status_opts = ["All"] + meta["statuses"]
        sel_status = st.selectbox("Column: Status", options=status_opts, key=f"{key_prefix}_st")

    c3, c4 = st.columns(2)
    with c3:
        prio_opts = ["All"] + meta["priorities"]
        sel_prio = st.selectbox("Column: Priority", options=prio_opts, key=f"{key_prefix}_prio")
    with c4:
        tag_opts = ["All"] + meta["tags"]
        sel_tag = st.selectbox("Column: Tags", options=tag_opts, key=f"{key_prefix}_tag")

    search_q = st.text_input(
        "Search Keyword (Company, Name, Email)",
        placeholder="Filter by keyword in any column...",
        key=f"{key_prefix}_search"
    )

    custom_key = "None"
    custom_val = "All"
    if meta["custom_vars"]:
        with st.expander("➕ Advanced Column Filters (Custom Fields & Contacted)", expanded=False):
            adv_c1, adv_c2 = st.columns(2)
            with adv_c1:
                sel_contacted = st.selectbox("Column: Contacted?", options=["All", "No", "Yes"], key=f"{key_prefix}_contacted")
            with adv_c2:
                var_keys = ["None"] + list(meta["custom_vars"].keys())
                custom_key = st.selectbox("Custom Field Column", options=var_keys, key=f"{key_prefix}_cust_k")

            if custom_key != "None":
                val_opts = ["All"] + meta["custom_vars"].get(custom_key, [])
                custom_val = st.selectbox(f"Value for '{custom_key}'", options=val_opts, key=f"{key_prefix}_cust_v")
    else:
        sel_contacted = "All"

    filtered = filter_leads_by_columns(
        leads=leads,
        source_filter=sel_source,
        status_filter=sel_status,
        priority_filter=sel_prio,
        tag_filter=sel_tag,
        contacted_filter=sel_contacted,
        search_query=search_q,
        custom_key=custom_key,
        custom_val=custom_val,
        exclude_ids=exclude_ids
    )

    filter_dict = {
        "source": sel_source,
        "status": sel_status,
        "priority": sel_prio,
        "tag": sel_tag,
        "contacted": sel_contacted,
        "search": search_q,
        "custom_key": custom_key,
        "custom_val": custom_val
    }

    return filtered, filter_dict


def render_campaign_enrollment_interface(
    campaign_id: int,
    all_contacts: List[Dict[str, Any]],
    camp_contacts: List[Dict[str, Any]],
    key_suffix: str = ""
):
    """
    Renders the enhanced campaign lead enrollment UI supporting multi-column filtering and selection.
    """
    st.markdown("##### 👥 Enroll Leads into Campaign")
    st.caption("Filter prospects using any column header (Lead Source, Status, Priority, Tags, Research) and enroll them.")

    enrolled_ids = {cc["contact_id"] for cc in camp_contacts if cc.get("contact_id") is not None}

    filtered_leads, active_filters = render_column_filter_controls(
        leads=all_contacts,
        key_prefix=f"camp_enroll_{campaign_id}_{key_suffix}",
        exclude_ids=enrolled_ids
    )

    active_count = len(filtered_leads)
    already_count = len(enrolled_ids)

    st.markdown(
        f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; padding:8px 12px; margin:8px 0; font-size:12px;'>"
        f"Found <strong style='color:#083731;'>{active_count} eligible leads</strong> matching active column filters. "
        f"({already_count} already enrolled excluded)"
        f"</div>",
        unsafe_allow_html=True
    )

    if not filtered_leads:
        st.info("No unenrolled leads match the current column filters. Try adjusting the dropdowns above.")
        return

    mode_key = f"camp_enr_mode_{campaign_id}_{key_suffix}"
    enroll_mode = st.radio(
        "Enrollment Mode",
        options=[f"Enroll all {active_count} matching leads", "Pick specific leads from filtered list"],
        key=mode_key,
        horizontal=True
    )

    leads_to_add: List[int] = []

    if enroll_mode.startswith("Enroll all"):
        leads_to_add = [c["id"] for c in filtered_leads if c.get("id") is not None]
    else:
        # Checkbox selection bar
        sel_state_key = f"camp_picked_ids_{campaign_id}_{key_suffix}"
        if sel_state_key not in st.session_state or not isinstance(st.session_state[sel_state_key], set):
            st.session_state[sel_state_key] = {c["id"] for c in filtered_leads if c.get("id") is not None}

        ver_key = f"camp_chk_ver_{campaign_id}_{key_suffix}"
        if ver_key not in st.session_state:
            st.session_state[ver_key] = 0

        q_c1, q_c2 = st.columns([1, 1])
        with q_c1:
            if st.button("☑️ Select All Filtered", key=f"btn_sel_all_{campaign_id}_{key_suffix}", use_container_width=True):
                st.session_state[sel_state_key] = {c["id"] for c in filtered_leads if c.get("id") is not None}
                st.session_state[ver_key] += 1
                st.rerun()
        with q_c2:
            if st.button("◻️ Clear Selection", key=f"btn_clr_all_{campaign_id}_{key_suffix}", use_container_width=True):
                st.session_state[sel_state_key] = set()
                st.session_state[ver_key] += 1
                st.rerun()

        ver = st.session_state[ver_key]
        curr_selected = st.session_state[sel_state_key]

        with st.container(height=260):
            for c in filtered_leads:
                cid = c.get("id")
                if cid is None:
                    continue
                cname = c.get("name") or "Unknown"
                ccomp = c.get("company") or "No Brand"
                cemail = c.get("email") or ""
                csrc = c.get("lead_source") or ""
                cstat = c.get("status") or "New"
                cprio = c.get("priority") or "Medium"
                ctags = c.get("tags") or ""

                # Extract amazon issue if present in custom variables
                cissue = ""
                cv_dict = c.get("custom_variables_dict")
                if isinstance(cv_dict, dict):
                    cissue = cv_dict.get("amazon_issue") or cv_dict.get("issue") or ""

                is_checked = cid in curr_selected

                # Render lead badge and checkbox
                chk = st.checkbox(
                    f"{ccomp} — {cname} ({cemail})",
                    value=is_checked,
                    key=f"camp_chk_{campaign_id}_{cid}_{key_suffix}_v{ver}",
                    help=f"Source: {csrc} | Status: {cstat} | Priority: {cprio} | Tags: {ctags}"
                )

                # Show column tags underneath checkbox
                badge_html = render_lead_badge_html(
                    company=ccomp,
                    name=cname,
                    email=cemail,
                    source=csrc,
                    status=cstat,
                    priority=cprio,
                    tags=ctags,
                    custom_issue=cissue
                )
                st.markdown(badge_html, unsafe_allow_html=True)
                st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

                if chk:
                    st.session_state[sel_state_key].add(cid)
                else:
                    st.session_state[sel_state_key].discard(cid)

        leads_to_add = list(st.session_state[sel_state_key])

    st.markdown("<hr style='margin:12px 0 8px;'>", unsafe_allow_html=True)
    if st.button(
        f"🚀 Enroll {len(leads_to_add)} Contacts into Campaign",
        type="primary",
        use_container_width=True,
        key=f"btn_execute_enroll_{campaign_id}_{key_suffix}"
    ):
        if not leads_to_add:
            st.warning("Please select at least one contact to enroll.")
        else:
            cnt_added = enroll_contacts_in_campaign(campaign_id, leads_to_add)
            trigger_toast(f"Successfully enrolled {cnt_added} leads into campaign!", icon="👥")
            st.rerun()
