"""
ui/templates.py - Unified Template Manager for Sellomize Reach.
Section C3 of Complete Restructure Spec.

Features:
- Dual-mode editor: Visual toolbar view + Source raw HTML view.
- Live preview with Spintax resolution and variable injection using any lead from CRM.
- Template CRUD: Create, Edit, Clone, Delete.
- Load into Compose & Use in Bulk shortcuts.
- Rule-based spam check on template copy.
"""

import streamlit as st
import html
import re
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
    _missing_tokens,
)
from ui.editor import render_dual_mode_editor
from ui.components import render_tab_header, trigger_toast


def render_templates_tab(all_templates: Optional[List[Dict[str, Any]]] = None):
    """Render the unified Template Manager tab with dual-mode editor and live resolved test preview."""
    render_tab_header(
        "✍️ Outreach Templates",
        "Create, edit, and audit reusable cold outreach templates with Spintax, variable injection, and spam auditing."
    )

    if all_templates is None:
        all_templates = get_templates()

    all_leads = get_contacts()

    # --- TOP ACTIONS: SEARCH & NEW TEMPLATE ---
    top_c1, top_c2 = st.columns([3, 1])
    with top_c1:
        search_query = st.text_input("🔍 Search Templates", placeholder="Search by template name or subject...", label_visibility="collapsed")
    with top_c2:
        if st.button("➕ New Template", type="primary", use_container_width=True):
            st.session_state["editing_template_id"] = "new"
            st.session_state["tpl_name"] = "New Outreach Template"
            st.session_state["tpl_subject"] = "Quick question for [Company]"
            st.session_state["tpl_body_html"] = "<p>Hi [Name],</p><p>I noticed [Company] and wanted to connect.</p>"
            st.rerun()

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

    # --- MAIN VIEW: LIST ON LEFT, DUAL-MODE EDITOR ON RIGHT ---
    col_list, col_editor = st.columns([1.2, 2.8])

    # Left Column: Template Cards
    with col_list:
        st.markdown(f"**Saved Templates ({len(filtered_templates)})**")
        if not filtered_templates:
            st.caption("No templates found. Click '➕ New Template' to create one.")
        else:
            for t in filtered_templates:
                tid = t["id"]
                tname = t.get("template_name") or t.get("name") or f"Template #{tid}"
                tsubj = t.get("subject") or "No Subject"

                is_selected = str(st.session_state.get("editing_template_id")) == str(tid)
                card_bg = "#FFFFFF" if not is_selected else "rgba(8,55,49,0.06)"
                border_color = "#FD4D1B" if is_selected else "rgba(8,55,49,0.15)"

                st.markdown(
                    f"""<div style="background:{card_bg}; border:1.5px solid {border_color}; border-radius:8px; padding:10px; margin-bottom:8px;">
                        <div style="font-weight:800; font-size:0.88rem; color:#083731;">{html.escape(tname)}</div>
                        <div style="font-size:0.75rem; color:#64748B; margin-top:2px;">{html.escape(tsubj[:45])}...</div>
                    </div>""",
                    unsafe_allow_html=True
                )
                btn_cols = st.columns(3)
                with btn_cols[0]:
                    if st.button("✏️ Edit", key=f"tpl_edit_{tid}", use_container_width=True):
                        st.session_state["editing_template_id"] = tid
                        st.session_state["tpl_name"] = tname
                        st.session_state["tpl_subject"] = t.get("subject") or ""
                        st.session_state["tpl_body_html"] = t.get("body_content") or t.get("body_html") or ""
                        st.rerun()
                with btn_cols[1]:
                    if st.button("📋 Clone", key=f"tpl_dup_{tid}", use_container_width=True):
                        new_tid = create_template(
                            template_name=f"{tname} (Copy)",
                            body_content=t.get("body_content") or t.get("body_html") or ""
                        )
                        update_template(
                            template_id=new_tid,
                            name=f"{tname} (Copy)",
                            subject=tsubj,
                            body_html=t.get("body_content") or t.get("body_html") or ""
                        )
                        trigger_toast(f"Cloned as '{tname} (Copy)'", icon="📋")
                        st.session_state["editing_template_id"] = new_tid
                        st.rerun()
                with btn_cols[2]:
                    if st.button("🗑️", key=f"tpl_del_{tid}", use_container_width=True):
                        delete_template(tid)
                        if str(st.session_state.get("editing_template_id")) == str(tid):
                            st.session_state.pop("editing_template_id", None)
                        trigger_toast("Template deleted.", icon="🗑️")
                        st.rerun()

    # Right Column: Editor & Live Lead Preview
    with col_editor:
        active_id = st.session_state.get("editing_template_id")
        if not active_id:
            st.info("👈 Select a template from the list on the left to edit, or click '➕ New Template'.")
            return

        is_new = (active_id == "new")
        hdr_title = "➕ Create New Template" if is_new else f"✏️ Edit Template #{active_id}"
        st.markdown(f"#### {hdr_title}")

        tpl_name_val = st.text_input("Template Name *", value=st.session_state.get("tpl_name", ""), key="input_tpl_name")
        tpl_subj_val = st.text_input("Subject Line (Supports [Variables] & Spintax)", value=st.session_state.get("tpl_subject", ""), key="input_tpl_subj")

        # Dual-Mode Editor Component
        st.markdown("**Template Body (Dual-Mode Editor):**")
        current_body = render_dual_mode_editor(
            key_prefix="tpl",
            initial_content=st.session_state.get("tpl_body_html", ""),
            height=200
        )
        st.session_state["tpl_body_html"] = current_body

        # Save and Action Buttons
        save_col1, save_col2, save_col3 = st.columns([1.5, 1.2, 1.2])
        with save_col1:
            if st.button("💾 Save Template", type="primary", use_container_width=True):
                if not tpl_name_val.strip():
                    st.error("Please provide a template name.")
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

        with save_col2:
            if st.button("✍️ Load in Compose", use_container_width=True):
                st.session_state["compose_subject"] = tpl_subj_val.strip()
                st.session_state["compose_body_html"] = current_body.strip()
                st.session_state["main_app_tabs"] = "✍️ Compose"
                st.rerun()

        with save_col3:
            if st.button("🚀 Use in Bulk", use_container_width=True):
                st.session_state["bulk_selected_template_id"] = active_id
                st.session_state["main_app_tabs"] = "🚀 Bulk Send"
                st.rerun()

        # Deliverability & Live CRM Preview
        st.markdown("---")
        st.markdown("##### 🛡️ Deliverability Check & Test Preview")
        neg_keywords = get_config("negative_keywords", "")
        score, triggers = audit_email_deliverability(f"{tpl_subj_val} {current_body}", neg_keywords)

        c_aud1, c_aud2 = st.columns([1, 2])
        with c_aud1:
            if triggers:
                st.error(f"⚠️ {len(triggers)} Negative Keyword(s) Detected:")
                for trig in triggers:
                    st.markdown(f"- `{trig}`")
            else:
                st.success("✅ Clean: 0 negative keywords detected.")

        with c_aud2:
            st.markdown("**Test Variable Injection Preview:**")
            lead_choices = {f"{c.get('name') or 'Lead'} ({c.get('email')}) — {c.get('company') or 'No Company'}": c for c in all_leads}
            if lead_choices:
                sel_lead_key = st.selectbox("Select Lead for Preview", list(lead_choices.keys()), key="tpl_preview_lead_sel", label_visibility="collapsed")
                preview_lead = lead_choices[sel_lead_key]
            else:
                preview_lead = {"name": "Alex Mercer", "company": "Mercer Retail", "email": "alex@mercerretail.com"}

            resolved_subj = inject_variables(parse_spintax(tpl_subj_val), preview_lead)
            resolved_body = resolve_template(current_body, preview_lead)

            st.markdown(
                f"""<div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.18); border-radius:8px; padding:12px; font-size:0.85rem;">
                    <div style="border-bottom:1px solid #F1F5F9; padding-bottom:6px; margin-bottom:8px;">
                        <strong>Subject:</strong> {html.escape(resolved_subj)}
                    </div>
                    <div>{resolved_body}</div>
                </div>""",
                unsafe_allow_html=True
            )
