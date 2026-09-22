"""
Studio & Templates Tab for Sellomize Reach.
Provides template building with Spintax, variable chips, external AI prompt guide, and live preview.
"""

import streamlit as st

from database import (
    get_all_distinct_custom_variable_keys,
    get_templates,
    create_template,
    update_template,
    delete_template,
    get_contacts,
    get_config
)
from template_engine import audit_email_deliverability, resolve_template, scan_negative_keywords, scan_all_negative_keywords
from ui.components import render_html_preview, render_tab_header, trigger_toast


def render_studio_tab(templates=None, contacts_list=None):
    """Render Tab 2: Studio & Templates."""
    if templates is None:
        templates = get_templates()
    if contacts_list is None:
        contacts_list = get_contacts()

    render_tab_header("✍️ Studio & Outreach Templates", "Create reusable cold outreach templates with dynamic variable insertion and Spintax variation.")

    # Dynamic Variable Badges & Syntax Guide inside Collapsible Expander
    with st.expander("💡 How to format templates (Spintax & Variables)", expanded=False):
        detected_var_keys = get_all_distinct_custom_variable_keys(include_predefined=True)
        var_chips = ["Name", "Company", "Email"] + [k for k in detected_var_keys if k not in ["Name", "Company", "Email"]]
        var_chips_html = "".join([f"<code style='background:rgba(56, 189, 248, 0.12); color:#38BDF8; border: 1px solid rgba(56, 189, 248, 0.3); font-weight:700; padding:2px 6px; border-radius:4px; margin-right:4px; display:inline-block;'>[{k}]</code> " for k in var_chips])

        st.markdown(f"""
        <div class="syntax-help">
            <strong>💡 Template Formatting Guide:</strong><br>
            • <strong>Variables:</strong> Use brackets like <code>[Name]</code> or <code>[Company]</code>. They are automatically injected with prospect data.<br>
            • <strong>Available Lead Variables:</strong> {var_chips_html}<br>
            • <strong>Spintax:</strong> Use <code>{{variation1|variation2|variation3}}</code> syntax. The engine randomly selects an option per lead to ensure unique copy.
        </div>
        """, unsafe_allow_html=True)

    with st.expander("Turn a written email into a template (External AI Workflow)", expanded=False):
        st.markdown(
            "Convert any written email into a reusable template for Sellomize Reach using an external AI (ChatGPT, Claude, Gemini).\n\n"
            "**Workflow:**\n"
            "1. Copy the master prompt below.\n"
            "2. Paste it into your AI model and replace `[PASTE YOUR EMAIL HERE]` with your real email draft.\n"
            "3. Paste the generated subject and body into the template editor below."
        )
        master_prompt_text = (
            "You are an expert cold-outreach copywriter. Convert the email I give you into a REUSABLE OUTREACH TEMPLATE for the Sellomize Reach platform. Follow these rules exactly.\n\n"
            "PLACEHOLDER SYNTAX (square brackets):\n"
            "- Recipient's first name  -> [Name]\n"
            "- Recipient's company/brand -> [Company]\n"
            "- Any other detail that changes per recipient -> a clear custom variable in square brackets, e.g. [Role], [City], [ProductCategory], [ASIN]. Name them in TitleCase, no spaces (use underscores if needed).\n"
            "- DO NOT turn MY OWN details into variables. My name, my company, my signature, my links stay exactly as written.\n\n"
            "RULES:\n"
            "1. Only replace text that is genuinely specific to one recipient. Do not force a placeholder where the wording isn't actually person-specific.\n"
            "2. Every placeholder must read grammatically once filled in (mind a/an, capitalization, plurals).\n"
            "3. SPINTAX (optional, light): for the greeting and 1-3 short interchangeable phrases, offer variations using {option A|option B} syntax so each send differs slightly and lands in the inbox better. Every possible combination must read correctly. Never apply spintax to names, links, the core offer, or anything where a wrong combo would sound off.\n"
            "4. DELIVERABILITY: avoid spam-trigger words and patterns (e.g. \"100% free\", \"guarantee\", \"act now\", \"risk-free\", \"limited time\", ALL CAPS, and rows of exclamation marks). Keep the subject line short, lower-key, and human.\n"
            "5. Preserve my tone, intent, structure, and call-to-action. Do not invent claims, offers, statistics, or facts I did not write.\n"
            "6. Keep all links intact.\n\n"
            "OUTPUT — return ONLY this, nothing before or after:\n\n"
            "Subject: <templated subject line>\n\n"
            "Body:\n"
            "<templated email body with placeholders and light spintax>\n\n"
            "---\n"
            "EMAIL TO CONVERT:\n"
            "[PASTE YOUR EMAIL HERE]"
        )
        st.code(master_prompt_text, language="markdown")
        st.caption("Copy this prompt into your preferred AI model along with your real email draft.")

    with st.expander("Create New Template", expanded=False):
        new_tpl_name = st.text_input("Template Name", placeholder="e.g. Q4 Amazon Optimization Hook")

        new_tpl_body_key = "new_template_body_content"
        if new_tpl_body_key not in st.session_state:
            st.session_state[new_tpl_body_key] = (
                "<p>Hi [Name],</p>\n"
                "<p>I was reviewing [Company]'s listings and noticed {a couple of missed opportunities|some quick areas for improvement} on your mobile bullet points.</p>\n"
                "<p>Would you be open to a 3-minute video teardown?</p>\n"
                "<p>Best,<br>Jack</p>"
            )

        col_new_edit, col_new_prev = st.columns([1.1, 1.1])
        with col_new_edit:
            st.markdown("##### HTML & Spintax Source")
            
            # One-Click Variable Chips directly above editor
            st.caption("Click to insert variable into template:")
            chip_cols = st.columns(5)
            chips = [("[+ Name]", "[Name]"), ("[+ Company]", "[Company]"), ("[+ Website]", "[Website]"), ("[+ ASIN]", "[ASIN]"), ("[+ Custom]", "[Custom]")]
            for idx, (chip_lbl, chip_val) in enumerate(chips):
                with chip_cols[idx]:
                    if st.button(chip_lbl, key=f"new_chip_{idx}", use_container_width=True):
                        st.session_state[new_tpl_body_key] = (st.session_state.get(new_tpl_body_key, "") + f" {chip_val}").strip()
                        st.rerun()

            new_src_txt = st.text_area(
                "HTML / Spintax Source",
                value=st.session_state[new_tpl_body_key],
                height=240,
                key="src_new_template",
                label_visibility="collapsed"
            )
            st.session_state[new_tpl_body_key] = new_src_txt

            # Compact Deliverability Score Badge
            new_body_to_audit = new_src_txt.strip()
            neg_kw = get_config("negative_keywords", "")
            detected_kws = scan_all_negative_keywords(new_body_to_audit, neg_kw)
            audit_res = audit_email_deliverability(new_body_to_audit)
            score = audit_res.get("score", 100)

            if score >= 85 and not detected_kws:
                status_lbl = "Inbox Ready"
                score_bg = "rgba(16,185,129,0.08)"
                score_border = "rgba(16,185,129,0.28)"
                score_col = "#059669"
            elif score >= 65 and not detected_kws:
                status_lbl = "Moderate"
                score_bg = "rgba(245,158,11,0.08)"
                score_border = "rgba(245,158,11,0.28)"
                score_col = "#D97706"
            else:
                status_lbl = "Spam Risk"
                score_bg = "rgba(239,68,68,0.08)"
                score_border = "rgba(239,68,68,0.28)"
                score_col = "#DC2626"

            st.markdown(f"""
            <div style="background:{score_bg}; border:1px solid {score_border}; border-radius:8px; padding:7px 12px; margin:6px 0 8px; display:flex; align-items:center; justify-content:space-between;">
                <div style="font-weight:700; color:{score_col}; font-size:0.86rem;">
                    Deliverability Score: {score}/100 <span style="font-size:0.8rem; font-weight:600; opacity:0.9;">({status_lbl})</span>
                </div>
                <div style="font-size:0.75rem; color:#64748B;">Automated Spam Pattern Shield</div>
            </div>
            """, unsafe_allow_html=True)

            if detected_kws:
                with st.expander(f"⚠️ {len(detected_kws)} Restricted Trigger(s) Detected (Click to expand)", expanded=False):
                    trig_badges = ", ".join(f"`{k}`" for k in detected_kws)
                    st.warning(f"Contains restricted trigger keyword(s): {trig_badges}")
                    if audit_res.get("issues"):
                        for iss in audit_res["issues"]:
                            st.caption(f"• {iss}")

            if st.button("Save Template", type="primary", use_container_width=True, key="btn_save_new_template"):
                if not new_tpl_name.strip() or not new_body_to_audit:
                    st.error("Please provide both a Template Name and Body content.")
                else:
                    create_template(
                        template_name=new_tpl_name.strip(),
                        body_content=new_body_to_audit
                    )
                    trigger_toast(f"Template '{new_tpl_name}' saved successfully!", icon="💾")
                    st.rerun()

        with col_new_prev:
            col_ph, col_prb = st.columns([2.5, 1.5])
            with col_ph:
                st.markdown("##### Live Formatted Preview")
                st.caption("Resolved with sample lead data:")
            with col_prb:
                if st.button("Re-roll Spintax", key="btn_reroll_new_tpl", use_container_width=True):
                    st.rerun()

            if new_body_to_audit:
                sample_lead = {
                    "name": "Jack Connor",
                    "company": "Summit Brands",
                    "email": "jack@summitbrands.com",
                    "custom_variables": {"Role": "Founder", "Website": "https://summitbrands.com"}
                }
                resolved_preview = resolve_template(new_body_to_audit, sample_lead)
                render_html_preview(resolved_preview, height=260)
            else:
                st.caption("Enter template source on the left to see live preview.")

    st.markdown("---")

    # Saved Templates Library with Consolidated Action Bar
    st.markdown("### 📚 Saved Templates Library")
    templates = get_templates()

    if not templates:
        st.info("No outreach templates saved yet. Create your first template using the editor above!")
    else:
        tpl_id_list = [t["id"] for t in templates]
        active_tpl_id = st.session_state.get("studio_active_tpl_id")
        if active_tpl_id not in tpl_id_list:
            active_tpl_id = tpl_id_list[0]
            st.session_state["studio_active_tpl_id"] = active_tpl_id

        # Quick summary & selector row
        col_tpl_sel, col_tpl_sum = st.columns([2.8, 1.2], vertical_alignment="center")
        with col_tpl_sel:
            selected_tpl_id = st.selectbox(
                "Select Template to Inspect & Manage:",
                options=tpl_id_list,
                index=tpl_id_list.index(active_tpl_id),
                format_func=lambda tid: next(f"📑 #{t['id']} — {t['template_name']}" for t in templates if t["id"] == tid),
                key="studio_active_tpl_id"
            )
        with col_tpl_sum:
            st.caption(f"📚 **{len(templates)}** template(s) in library")

        active_tpl = next((t for t in templates if t["id"] == selected_tpl_id), templates[0])
        is_editing = (st.session_state.get("studio_editing_mode") == selected_tpl_id)

        # Single Contextual Action Bar for the Selected Template
        col_act1, col_act2, col_act3 = st.columns([1.2, 1.4, 1.4])
        with col_act1:
            if st.button("✏️ Edit Template" if not is_editing else "👁️ Preview Mode", key=f"btn_toggle_edit_{selected_tpl_id}", use_container_width=True):
                st.session_state["studio_editing_mode"] = None if is_editing else selected_tpl_id
                st.rerun()

        with col_act2:
            if st.button("🔄 Re-roll Spintax", key=f"btn_reroll_tpl_{selected_tpl_id}", use_container_width=True):
                st.rerun()

        with col_act3:
            if st.session_state.get(f"confirm_del_tpl_{selected_tpl_id}"):
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    if st.button("Confirm", key=f"del_yes_{selected_tpl_id}", use_container_width=True):
                        delete_template(selected_tpl_id)
                        st.session_state[f"confirm_del_tpl_{selected_tpl_id}"] = False
                        st.session_state["studio_editing_mode"] = None
                        trigger_toast(f"Template #{selected_tpl_id} deleted.", icon="🗑️")
                        st.rerun()
                with col_d2:
                    if st.button("Cancel", key=f"del_no_{selected_tpl_id}", use_container_width=True):
                        st.session_state[f"confirm_del_tpl_{selected_tpl_id}"] = False
                        st.rerun()
            else:
                if st.button("🗑️ Delete Template", key=f"btn_del_card_{selected_tpl_id}", use_container_width=True):
                    st.session_state[f"confirm_del_tpl_{selected_tpl_id}"] = True
                    st.rerun()

        # Workspace below action bar: Edit Drawer vs Live Preview
        if is_editing:
            st.markdown(f"""
            <div style="background: rgba(8,55,49,0.04); border: 1px solid #10B981; border-radius: 8px; padding: 10px 14px; margin: 10px 0 8px;">
                <strong style="color: #083731; font-size:0.9rem;">✏️ Editing Template #{selected_tpl_id}: {active_tpl['template_name']}</strong>
            </div>
            """, unsafe_allow_html=True)

            edit_t_name = st.text_input("Template Name", value=active_tpl["template_name"], key=f"edit_tname_{selected_tpl_id}")

            edit_tpl_key = f"edit_tpl_content_{selected_tpl_id}"
            if edit_tpl_key not in st.session_state:
                st.session_state[edit_tpl_key] = active_tpl["body_content"]

            chips_list = [("[+ Name]", "[Name]"), ("[+ Company]", "[Company]"), ("[+ Website]", "[Website]"), ("[+ ASIN]", "[ASIN]"), ("[+ Custom]", "[Custom]")]
            st.caption("Click to insert variable:")
            e_chip_cols = st.columns(5)
            for e_idx, (e_lbl, e_val) in enumerate(chips_list):
                with e_chip_cols[e_idx]:
                    if st.button(e_lbl, key=f"edit_chip_{selected_tpl_id}_{e_idx}", use_container_width=True):
                        st.session_state[edit_tpl_key] = (st.session_state.get(edit_tpl_key, "") + f" {e_val}").strip()
                        st.rerun()

            col_edit_src, col_edit_prev = st.columns([1.1, 1.1])
            with col_edit_src:
                edit_src_txt = st.text_area(
                    "HTML / Spintax Source",
                    value=st.session_state[edit_tpl_key],
                    height=240,
                    key=f"src_edit_tpl_{selected_tpl_id}",
                    label_visibility="collapsed"
                )
                st.session_state[edit_tpl_key] = edit_src_txt

                edit_body_to_audit = edit_src_txt.strip()
                neg_kw = get_config("negative_keywords", "")
                edit_detected_kws = scan_all_negative_keywords(edit_body_to_audit, neg_kw)
                edit_audit = audit_email_deliverability(edit_body_to_audit)
                edit_score = edit_audit.get("score", 100)

                st.markdown(f"""
                <div style="background:rgba(8,55,49,0.05); border:1px solid rgba(8,55,49,0.18); border-radius:6px; padding:6px 10px; margin:4px 0 8px;">
                    <span style="font-weight:700; font-size:0.82rem; color:#083731;">Deliverability Score: {edit_score}/100</span>
                </div>
                """, unsafe_allow_html=True)

                if edit_detected_kws:
                    trig_chips = ", ".join(f"`{k}`" for k in edit_detected_kws)
                    st.warning(f"Contains restricted trigger keyword(s): {trig_chips}")

                col_save_e, col_canc_e = st.columns([1.5, 3])
                with col_save_e:
                    if st.button("Save Changes", type="primary", use_container_width=True, key=f"save_edit_tpl_btn_{selected_tpl_id}"):
                        if not edit_t_name.strip() or not edit_body_to_audit:
                            st.error("Both Name and Body are required.")
                        else:
                            update_template(selected_tpl_id, edit_t_name.strip(), edit_body_to_audit)
                            st.session_state["studio_editing_mode"] = None
                            trigger_toast(f"Template '{edit_t_name}' successfully updated!", icon="💾")
                            st.rerun()
                with col_canc_e:
                    if st.button("Cancel", use_container_width=True, key=f"canc_edit_tpl_btn_{selected_tpl_id}"):
                        st.session_state["studio_editing_mode"] = None
                        st.rerun()

            with col_edit_prev:
                if edit_body_to_audit:
                    sample_lead = {
                        "name": "Jack Connor",
                        "company": "Summit Brands",
                        "email": "jack@summitbrands.com",
                        "custom_variables": {"Role": "Founder", "Website": "https://summitbrands.com"}
                    }
                    resolved_edit_preview = resolve_template(edit_body_to_audit, sample_lead)
                    render_html_preview(resolved_edit_preview, height=240)
                else:
                    st.caption("Enter template source on the left to see live preview.")

        else:
            # Default Clean Preview Mode
            col_prev_card, col_prev_meta = st.columns([2.6, 1.4])
            with col_prev_card:
                sample_contact = contacts_list[0] if contacts_list else {
                    "name": "Sarah Jenkins",
                    "company": "Apex Outdoors",
                    "email": "sarah@apex.com",
                    "custom_variables_dict": {"Role": "Founder"}
                }
                resolved = resolve_template(active_tpl["body_content"], sample_contact)
                st.caption(f"Live preview resolved with lead: **{sample_contact['name']}** ({sample_contact.get('company', '')}):")
                render_html_preview(resolved, height=260)

            with col_prev_meta:
                st.markdown(f"""
                <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:10px; padding:14px; margin-top:6px;">
                    <div style="font-weight:700; color:#0F172A; font-size:0.92rem; margin-bottom:10px;">📊 Template Info</div>
                    <div style="font-size:0.8rem; color:#475569; margin-bottom:6px;"><strong>ID:</strong> #{selected_tpl_id}</div>
                    <div style="font-size:0.8rem; color:#475569; margin-bottom:6px;"><strong>Name:</strong> {active_tpl['template_name']}</div>
                    <div style="font-size:0.8rem; color:#475569; margin-bottom:6px;"><strong>Created:</strong> {(active_tpl.get('created_at') or 'N/A')[:10]}</div>
                </div>
                """, unsafe_allow_html=True)

                tpl_audit = audit_email_deliverability(active_tpl["body_content"])
                tpl_score = tpl_audit.get("score", 100)
                neg_kw = get_config("negative_keywords", "")
                detected = scan_all_negative_keywords(active_tpl["body_content"], neg_kw)

                if tpl_score >= 85 and not detected:
                    st.success(f"Deliverability: {tpl_score}/100 (Inbox Ready)")
                elif detected:
                    st.warning(f"⚠️ {len(detected)} Restricted Trigger(s)")
                else:
                    st.info(f"Deliverability: {tpl_score}/100")

        # Clean Table View of All Templates
        with st.expander(f"📑 View All Templates in Table ({len(templates)} Total)", expanded=False):
            tpl_table_rows = [
                {
                    "ID": f"#{t['id']}",
                    "Template Name": t["template_name"],
                    "Created": (t.get("created_at") or "")[:10],
                    "Length": f"{len(t.get('body_content') or '')} chars"
                }
                for t in templates
            ]
            try:
                import pandas as pd
                st.dataframe(pd.DataFrame(tpl_table_rows), use_container_width=True, hide_index=True)
            except Exception:
                st.write(tpl_table_rows)
