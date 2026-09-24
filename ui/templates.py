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
from ui.components import trigger_toast


def render_templates_tab(all_templates: Optional[List[Dict[str, Any]]] = None):
    """Render the Templates screen matching sellomize_reference.html."""
    if all_templates is None:
        all_templates = get_templates()

    all_leads = get_contacts()

    # Top Toolbar: "+ New template" & Search field
    top_col1, top_col2, _ = st.columns([1.5, 2.5, 3], vertical_alignment="center")
    with top_col1:
        if st.button("➕ New template", type="primary", use_container_width=True, key="tpl_btn_new"):
            st.session_state["editing_template_id"] = "new"
            st.session_state["tpl_name"] = "New Outreach Template"
            st.session_state["tpl_subject"] = "Quick question for [Company]"
            st.session_state["tpl_body_html"] = "<p>Hi [Name],</p><p>I noticed [Company] and wanted to connect.</p>"
            st.rerun()

    with top_col2:
        search_query = st.text_input(
            "Search templates",
            placeholder="Search templates...",
            label_visibility="collapsed",
            key="tpl_search_input"
        )

    # Filter templates
    filtered_templates = all_templates
    if search_query.strip():
        q = search_query.strip().lower()
        filtered_templates = [
            t for t in all_templates
            if q in (t.get("template_name") or t.get("name") or "").lower()
            or q in (t.get("subject") or "").lower()
            or q in (t.get("body_content") or t.get("body_html") or "").lower()
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

        col_name, col_subj = st.columns([1.5, 2.5])
        with col_name:
            st.markdown("<span class='lbl'>Template name</span>", unsafe_allow_html=True)
            tpl_name_val = st.text_input("Template name", value=st.session_state.get("tpl_name", ""), label_visibility="collapsed", key="in_tpl_name")
        with col_subj:
            st.markdown("<span class='lbl'>Subject (supports [Name], [Company] & Spintax)</span>", unsafe_allow_html=True)
            tpl_subj_val = st.text_input("Subject", value=st.session_state.get("tpl_subject", ""), label_visibility="collapsed", key="in_tpl_subj")

        st.markdown("<span class='lbl' style='margin-top:10px;'>Body</span>", unsafe_allow_html=True)
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

        if triggers:
            trigger_list = ", ".join(f'"{t.get("word")}"' for t in triggers)
            st.markdown(
                f"""<div class="spam"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg> {len(triggers)} spam trigger(s): <b>{html.escape(trigger_list)}</b> &nbsp;·&nbsp; edit to optimize deliverability</div>""",
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                """<div class="guard"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg> 0 spam triggers — clean copy</div>""",
                unsafe_allow_html=True
            )

        # Action buttons
        btn_save, btn_comp, btn_bulk, btn_cancel = st.columns([1.5, 1.3, 1.3, 1])
        with btn_save:
            if st.button("💾 Save Template", type="primary", use_container_width=True, key="btn_save_tpl"):
                if not tpl_name_val.strip():
                    st.error("Please enter a template name.")
                else:
                    if is_new:
                        new_id = create_template(
                            template_name=tpl_name_val.strip(),
                            body_content=current_body.strip()
                        )
                        update_template(
                            template_id=new_id,
                            name=tpl_name_val.strip(),
                            subject=tpl_subj_val.strip(),
                            body_html=current_body.strip()
                        )
                        st.session_state["editing_template_id"] = new_id
                        trigger_toast("Template created successfully!", icon="✅")
                    else:
                        update_template(
                            template_id=int(active_id),
                            name=tpl_name_val.strip(),
                            subject=tpl_subj_val.strip(),
                            body_html=current_body.strip()
                        )
                        trigger_toast("Template updated successfully!", icon="✅")
                    st.rerun()

        with btn_comp:
            if st.button("✍️ Load in Compose", use_container_width=True, key="btn_load_comp"):
                st.session_state["compose_subject"] = tpl_subj_val.strip()
                st.session_state["compose_body_html"] = current_body.strip()
                st.session_state["active_screen"] = "compose"
                st.session_state["main_app_tabs"] = "✍️ Compose"
                st.rerun()

        with btn_bulk:
            if st.button("🚀 Use in Bulk", use_container_width=True, key="btn_use_bulk"):
                st.session_state["bulk_selected_template_id"] = active_id
                st.session_state["active_screen"] = "bulk"
                st.session_state["main_app_tabs"] = "🚀 Bulk Send"
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
                    tid = t["id"]
                    tname = t.get("template_name") or t.get("name") or f"Template #{tid}"
                    tsubj = t.get("subject") or "No Subject"
                    tbody = t.get("body_content") or t.get("body_html") or ""

                    st.markdown(f"""
                    <div style="font-weight:700; font-size:14px; color:#083731; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{html.escape(tname)}</div>
                    <div style="font-size:12px; color:#64748B; margin:3px 0 10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Subject: {html.escape(tsubj)}</div>
                    """, unsafe_allow_html=True)

                    c1, c2, c3, c4 = st.columns([1.1, 1.2, 1.1, 0.6])
                    with c1:
                        if st.button("📥 Load", key=f"tpl_load_{tid}", use_container_width=True):
                            st.session_state["compose_subject"] = tsubj
                            st.session_state["compose_body_html"] = tbody
                            st.session_state["active_screen"] = "compose"
                            st.session_state["main_app_tabs"] = "✍️ Compose"
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
                            st.session_state["tpl_name"] = tname
                            st.session_state["tpl_subject"] = tsubj
                            st.session_state["tpl_body_html"] = tbody
                            st.rerun()

                    with c4:
                        if st.button("🗑️", key=f"tpl_grid_del_{tid}", use_container_width=True):
                            delete_template(tid)
                            if str(st.session_state.get("editing_template_id")) == str(tid):
                                st.session_state.pop("editing_template_id", None)
                            trigger_toast(f"Template '{tname}' deleted.", icon="🗑️")
                            st.rerun()
