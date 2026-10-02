"""
ui/templates.py - Reference Template Manager for Sellomize Reach.
Matches sellomize_reference.html:
- Clean card grid with template title, subject line, and actions (Load, Use in bulk, Edit, Delete).
- Dual-mode editor (Visual toolbar + raw HTML source) with variable chips.
- Rule-based spam check & live test lead preview with variable resolution.
"""

import streamlit as st
import html
from typing import List, Dict, Any, Optional

from database import (
    get_templates,
    get_template_by_id,
    create_template,
    update_template,
    delete_template,
    get_contacts,
    get_config,
    DB_FILE,
)
from template_engine import (
    resolve_template,
    inject_variables,
    parse_spintax,
    audit_email_deliverability,
    highlight_spam_triggers,
)
from ui.editor import render_dual_mode_editor
from ui.rich_editor import render_rich_editor, is_rich_editor_enabled
from ui.components import trigger_toast


from sellomize_templates import (
    CORE_15_TEMPLATES,
    APPROVED_CLIENT_STORIES,
    BANNED_JARGON_WORDS
)


def render_templates_tab(all_templates: Optional[List[Dict[str, Any]]] = None):
    """Render the Templates screen matching sellomize_reference.html."""
    if all_templates is None:
        all_templates = get_templates()

    all_leads = get_contacts()

    # Categories list for filter
    all_categories = ["All Categories"] + [t["category"] for t in CORE_15_TEMPLATES]

    # Top Toolbar: "+ New template", Search field, and Category Filter
    top_col1, top_col2, top_col3 = st.columns([1.5, 2.0, 2.5], vertical_alignment="center")
    with top_col1:
        if st.button("➕ New template", type="primary", use_container_width=True, key="tpl_btn_new"):
            st.session_state["editing_template_id"] = "new"
            st.session_state["tpl_name"] = "New Outreach Template"
            st.session_state["tpl_subject"] = "[Company] + Sellomize"
            st.session_state["tpl_category"] = "Amazon Growth — General"
            st.session_state["tpl_body_html"] = "Hi [Name],\n\nWe haven’t been properly introduced, but I’m Jack with Sellomize.\n\nI spent some time looking through your Amazon presence and noticed there’s room to get more from the account."
            st.session_state["tpl_cs_allowed"] = False
            st.session_state["tpl_cs_found"] = ""
            st.session_state["tpl_cs_solved"] = ""
            st.session_state["tpl_cs_rewarded"] = ""
            st.rerun()

    with top_col2:
        search_query = st.text_input(
            "Search templates",
            placeholder="Search templates or subjects...",
            label_visibility="collapsed",
            key="tpl_search_input"
        )

    with top_col3:
        selected_category_filter = st.selectbox(
            "Filter Category",
            all_categories,
            label_visibility="collapsed",
            key="tpl_category_filter"
        )

    # Filter templates by query and category
    filtered_templates = all_templates
    if selected_category_filter and selected_category_filter != "All Categories":
        filtered_templates = [
            t for t in filtered_templates
            if (t.get("template_category") or t.get("category") or "") == selected_category_filter
        ]

    if search_query.strip():
        q = search_query.strip().lower()
        filtered_templates = [
            t for t in filtered_templates
            if q in (t.get("template_name") or t.get("name") or "").lower()
            or q in (t.get("subject") or "").lower()
            or q in (t.get("body_content") or t.get("body_html") or "").lower()
            or q in (t.get("template_category") or t.get("category") or "").lower()
        ]

    active_id = st.session_state.get("editing_template_id")

    # If currently editing or creating a template, display the editor modal/panel
    if active_id:
        is_new = (active_id == "new")
        panel_title = "➕ Create New Template" if is_new else f"✏️ Edit Template #{active_id}"

        st.markdown(f"""
        <div style="background:#FFFFFF; border:1px solid #E2E8F0; border-radius:12px; padding:18px 20px; margin:16px 0 24px;">
            <div style="font-weight:700; font-size:16px; color:#083731; margin-bottom:12px;">{panel_title}</div>
        </div>
        """, unsafe_allow_html=True)

        col_name, col_cat, col_subj = st.columns([1.5, 1.2, 2.0])
        with col_name:
            st.markdown("<span class='lbl'>Template name</span>", unsafe_allow_html=True)
            tpl_name_val = st.text_input("Template name", value=st.session_state.get("tpl_name", ""), label_visibility="collapsed", key="in_tpl_name")
        with col_cat:
            st.markdown("<span class='lbl'>Category</span>", unsafe_allow_html=True)
            core_cats = [t["category"] for t in CORE_15_TEMPLATES]
            current_cat = st.session_state.get("tpl_category") or "Amazon Growth — General"
            cat_idx = core_cats.index(current_cat) if current_cat in core_cats else 0
            tpl_cat_val = st.selectbox("Category", core_cats, index=cat_idx, label_visibility="collapsed", key="in_tpl_cat")
            st.session_state["tpl_category"] = tpl_cat_val
        with col_subj:
            st.markdown("<span class='lbl'>Subject (supports [Name], [Company], [Location])</span>", unsafe_allow_html=True)
            tpl_subj_val = st.text_input("Subject", value=st.session_state.get("tpl_subject", ""), label_visibility="collapsed", key="in_tpl_subj")

        # Optional 3-Part Client Proof Expander
        with st.expander("💼 Approved Client Proof (What We Found / How We Solved / What It Rewarded)", expanded=st.session_state.get("tpl_cs_allowed", False)):
            st.caption("3-part outcomes must strictly describe agency client achievements — never prospect facts or false claims.")
            cs_allowed = st.checkbox("Include 3-Part Client Proof in this template", value=st.session_state.get("tpl_cs_allowed", False), key="in_tpl_cs_allowed")
            cs_found = st.text_area("1. What We Found", value=st.session_state.get("tpl_cs_found", ""), height=70, key="in_tpl_cs_found", placeholder="e.g. Discovered 161 shipment discrepancies...")
            cs_solved = st.text_area("2. How We Solved It", value=st.session_state.get("tpl_cs_solved", ""), height=70, key="in_tpl_cs_solved", placeholder="e.g. 151 Amazon reimbursement cases were opened...")
            cs_rewarded = st.text_area("3. What It Rewarded", value=st.session_state.get("tpl_cs_rewarded", ""), height=70, key="in_tpl_cs_rewarded", placeholder="e.g. 147 cases resolved, bringing back $8,145.91...")

        st.markdown("<span class='lbl' style='margin-top:10px;'>Body</span>", unsafe_allow_html=True)
        if is_rich_editor_enabled():
            current_body = render_rich_editor(
                initial_html=st.session_state.get("tpl_body_html", ""),
                key="tpl_rich_editor",
                height=220,
                owner_type="template",
                owner_id=active_id if active_id != "new" else None
            )
        else:
            current_body = render_dual_mode_editor(
                key_prefix="tpl_editor",
                initial_content=st.session_state.get("tpl_body_html", ""),
                height=200
            )
        st.session_state["tpl_body_html"] = current_body

        # Deliverability check
        neg_keywords = get_config("negative_keywords", "")
        audit = audit_email_deliverability(body_html=f"{tpl_subj_val} {current_body}", custom_negative_keywords=neg_keywords)
        triggers = audit.get("detected_spam_words", [])

        # Check banned jargon from Sellomize rules
        jargon_found = [w for w in BANNED_JARGON_WORDS if re.search(rf'\b{re.escape(w)}\b', f"{tpl_subj_val} {current_body}", re.IGNORECASE)]

        if triggers:
            trigger_list = ", ".join(f'"{t.get("word")}"' for t in triggers)
            st.markdown(
                f"""<div class="spam"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg> {len(triggers)} spam trigger(s): <b>{html.escape(trigger_list)}</b> &nbsp;·&nbsp; edit to optimize deliverability</div>""",
                unsafe_allow_html=True
            )
        if jargon_found:
            st.markdown(
                f"""<div style="background:#FFFBEB; border:1px solid #FCD34D; border-radius:8px; padding:8px 12px; font-size:12px; color:#B45309; margin-top:6px;">
                ⚠️ <b>Banned Corporate Jargon detected:</b> {", ".join(jargon_found)}. Sellomize rules require direct, human language.
                </div>""",
                unsafe_allow_html=True
            )
        if not triggers and not jargon_found:
            st.markdown(
                """<div class="guard"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg> 0 spam triggers & zero corporate jargon — clean copy</div>""",
                unsafe_allow_html=True
            )

        # Action buttons
        btn_save, btn_comp, btn_bulk, btn_del, btn_cancel = st.columns([1.4, 1.2, 1.1, 1.1, 1.0], vertical_alignment="center")
        with btn_save:
            if st.button("💾 Save Template", type="primary", use_container_width=True, key="btn_save_tpl"):
                if not tpl_name_val.strip():
                    st.error("Please enter a template name.")
                else:
                    if is_new:
                        new_id = create_template(
                            template_name=tpl_name_val.strip(),
                            subject=tpl_subj_val.strip(),
                            body_content=current_body.strip(),
                            body_html=current_body.strip(),
                            template_category=tpl_cat_val,
                            client_story_allowed=cs_allowed,
                            client_story_found=cs_found,
                            client_story_solved=cs_solved,
                            client_story_rewarded=cs_rewarded
                        )
                        st.session_state["editing_template_id"] = new_id
                        trigger_toast("Template created successfully!", icon="✅")
                    else:
                        update_template(
                            template_id=int(active_id),
                            name=tpl_name_val.strip(),
                            subject=tpl_subj_val.strip(),
                            body_html=current_body.strip(),
                            template_category=tpl_cat_val,
                            client_story_allowed=cs_allowed,
                            client_story_found=cs_found,
                            client_story_solved=cs_solved,
                            client_story_rewarded=cs_rewarded
                        )
                        trigger_toast("Template updated successfully!", icon="✅")
                    st.rerun()

        with btn_comp:
            if st.button("✍️ Load in Compose", use_container_width=True, key="btn_load_comp"):
                st.session_state["compose_subject"] = tpl_subj_val.strip()
                st.session_state["compose_body_html"] = current_body.strip()
                st.session_state.pop("compose_visual_textarea", None)
                st.session_state["compose_last_synced_html"] = current_body.strip()
                st.session_state["active_screen"] = "compose"
                st.session_state["main_app_tabs"] = "✍️ Compose"
                trigger_toast("Loaded template into Compose!", icon="✍️")
                st.rerun()

        with btn_bulk:
            if st.button("🚀 Use in Bulk", use_container_width=True, key="btn_use_bulk"):
                st.session_state["bulk_selected_template_id"] = active_id
                st.session_state["active_screen"] = "bulk"
                st.session_state["main_app_tabs"] = "🚀 Bulk Send"
                st.rerun()

        with btn_del:
            if not is_new:
                if st.button("🗑️ Delete", use_container_width=True, key="btn_del_in_editor"):
                    delete_template(int(active_id))
                    st.session_state.pop("editing_template_id", None)
                    trigger_toast("Template deleted.", icon="🗑️")
                    st.rerun()

        with btn_cancel:
            if st.button("✖️ Close", use_container_width=True, key="btn_close_editor"):
                st.session_state.pop("editing_template_id", None)
                st.rerun()

        # Variable Injection Test Preview
        with st.expander("👁️ Test Variable Injection & Live Preview", expanded=False):
            lead_choices = {f"{c.get('name') or 'Lead'} ({c.get('email')}) — {c.get('company') or 'No Company'}": c for c in all_leads}
            if lead_choices:
                sel_lead_key = st.selectbox("Select Lead for Preview", list(lead_choices.keys()), key="tpl_prev_sel", label_visibility="collapsed")
                preview_lead = lead_choices[sel_lead_key]
            else:
                preview_lead = {"name": "Alex Mercer", "company": "Mercer Retail", "email": "alex@mercerretail.com"}

            resolved_subj = inject_variables(parse_spintax(tpl_subj_val), preview_lead)
            resolved_body = resolve_template(current_body, preview_lead)

            tpl_preview_html = (
                '<div class="preview" style="margin-top:8px;">'
                f'<div style="font-weight:700; margin-bottom:6px; color:#083731;">Subject: {html.escape(resolved_subj)}</div>'
                f'<div>{resolved_body}</div>'
                '</div>'
            )
            if hasattr(st, "html"):
                st.html(tpl_preview_html)
            else:
                st.markdown(tpl_preview_html, unsafe_allow_html=True)

        st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:20px 0;'>", unsafe_allow_html=True)

    # Grid of Saved Templates matching reference HTML
    st.markdown(f"<div style='font-size:12px; color:#64748B; margin-bottom:12px;'>All Saved Templates ({len(filtered_templates)})</div>", unsafe_allow_html=True)

    if not filtered_templates:
        st.info("No templates found. Click **➕ New template** to create one.")
        return

    # Render in responsive grid of columns (3 cards per row)
    cards_per_row = 3
    for i in range(0, len(filtered_templates), cards_per_row):
        row_templates = filtered_templates[i:i + cards_per_row]
        cols = st.columns(cards_per_row)
        for j, t in enumerate(row_templates):
            with cols[j]:
                with st.container(border=True):
                    tid   = t["id"]
                    tname = t.get("template_name") or t.get("name") or f"Template #{tid}"
                    tcat  = t.get("template_category") or t.get("category") or "General"
                    tsubj_raw = t.get("subject") or ""
                    tsubj_display = tsubj_raw if tsubj_raw else "No Subject"
                    tbody = t.get("body_content") or t.get("body_html") or ""
                    is_sys = bool(t.get("is_system_template"))

                    badge_bg = "#E6F4EA" if is_sys else "#EFF6FF"
                    badge_color = "#137333" if is_sys else "#1D4ED8"
                    badge_label = "Sellomize Core" if is_sys else "Custom"

                    st.markdown(f"""
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <span style="font-size:10px; font-weight:700; padding:2px 6px; border-radius:4px; background:{badge_bg}; color:{badge_color};">{badge_label}</span>
                        <span style="font-size:11px; color:#64748B; max-width:60%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{html.escape(tcat)}</span>
                    </div>
                    <div style="font-weight:700; font-size:14px; color:#083731; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{html.escape(tname)}</div>
                    <div style="font-size:12px; color:#64748B; margin:3px 0 10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Subject: {html.escape(tsubj_display)}</div>
                    """, unsafe_allow_html=True)

                    c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.1, 0.8], vertical_alignment="center")
                    with c1:
                        if st.button("📥 Load", key=f"tpl_load_{tid}", use_container_width=True):
                            st.session_state["compose_subject"]  = tsubj_raw
                            st.session_state["compose_body_html"] = tbody
                            st.session_state.pop("compose_visual_textarea", None)
                            st.session_state["active_screen"]    = "compose"
                            st.session_state["main_app_tabs"]    = "✍️ Compose"
                            trigger_toast(f"Loaded '{tname}' into Compose!", icon="✍️")
                            st.rerun()

                    with c2:
                        if st.button("🚀 Bulk", key=f"tpl_bulk_{tid}", use_container_width=True):
                            st.session_state["bulk_selected_template_id"] = tid
                            st.session_state["active_screen"] = "bulk"
                            st.session_state["main_app_tabs"] = "🚀 Bulk Send"
                            trigger_toast(f"Selected '{tname}' for Bulk Send!", icon="🚀")
                            st.rerun()

                    with c3:
                        if st.button("✏️ Edit", key=f"tpl_grid_edit_{tid}", use_container_width=True):
                            st.session_state["editing_template_id"] = tid
                            st.session_state["tpl_name"]            = tname
                            st.session_state["tpl_category"]        = tcat
                            st.session_state["tpl_subject"]         = tsubj_raw
                            st.session_state["tpl_body_html"]       = tbody
                            st.session_state["tpl_cs_allowed"]      = bool(t.get("client_story_allowed"))
                            st.session_state["tpl_cs_found"]        = t.get("client_story_found") or ""
                            st.session_state["tpl_cs_solved"]       = t.get("client_story_solved") or ""
                            st.session_state["tpl_cs_rewarded"]     = t.get("client_story_rewarded") or ""
                            st.session_state.pop("tpl_editor_body_html", None)
                            st.session_state.pop("tpl_editor_visual_textarea", None)
                            st.rerun()

                    with c4:
                        with st.popover("⚙️", use_container_width=True):
                            if st.button("📑 Duplicate", key=f"tpl_grid_dup_{tid}", use_container_width=True):
                                new_tid = create_template(
                                    template_name=f"{tname} (Copy)",
                                    subject=tsubj_raw,
                                    body_content=tbody,
                                    body_html=tbody
                                )
                                trigger_toast(f"Duplicated as '{tname} (Copy)'!", icon="📑")
                                st.rerun()

                            if st.button("🗑️ Delete", key=f"tpl_grid_del_{tid}", type="primary", use_container_width=True):
                                delete_template(tid)
                                if str(st.session_state.get("editing_template_id")) == str(tid):
                                    st.session_state.pop("editing_template_id", None)
                                trigger_toast(f"Template '{tname}' deleted.", icon="🗑️")
                                st.rerun()
