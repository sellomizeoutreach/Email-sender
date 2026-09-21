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
from ui.components import render_html_preview


def render_studio_tab(all_templates=None, contacts_list=None):
    """Render Tab 2: Template Builder (Studio & Templates)."""
    if contacts_list is None:
        contacts_list = get_contacts()

    st.subheader("Template Builder")
    st.caption("Create reusable cold outreach templates with dynamic variable insertion and Spintax variation.")

    # Dynamic Variable Badges from CRM
    detected_var_keys = get_all_distinct_custom_variable_keys(include_predefined=True)
    var_chips = ["Name", "Company", "Email"] + [k for k in detected_var_keys if k not in ["Name", "Company", "Email"]]
    var_chips_html = "".join([f"<code style='background:rgba(56, 189, 248, 0.12); color:#38BDF8; border: 1px solid rgba(56, 189, 248, 0.3); font-weight:700; padding:2px 6px; border-radius:4px; margin-right:4px; display:inline-block;'>[{k}]</code> " for k in var_chips])

    # Syntax Guide Box
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
            st.caption("Edit HTML tags and Spintax directly. Insert dynamic placeholders like `[Name]` or `[Company]`.")
            new_src_txt = st.text_area(
                "HTML / Spintax Source",
                value=st.session_state[new_tpl_body_key],
                height=260,
                key="src_new_template",
                label_visibility="collapsed"
            )
            st.session_state[new_tpl_body_key] = new_src_txt

            # Deliverability Audit Gate
            new_body_to_audit = new_src_txt.strip()
            neg_kw = get_config("negative_keywords", "")
            detected_kws = scan_all_negative_keywords(new_body_to_audit, neg_kw)
            if detected_kws:
                kws_badges = ", ".join(f"`{k}`" for k in detected_kws)
                st.warning(f"Contains restricted trigger keyword(s): {kws_badges}")

            if st.button("Save Template", type="primary", use_container_width=True, key="btn_save_new_template"):
                if not new_tpl_name.strip() or not new_body_to_audit:
                    st.error("Please provide both a Template Name and Body content.")
                else:
                    create_template(
                        template_name=new_tpl_name.strip(),
                        body_content=new_body_to_audit
                    )
                    st.success(f"Template '{new_tpl_name}' saved successfully!")
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

    # Saved Templates List & Spintax Test Preview
    st.subheader("📚 Saved Templates Library")
    templates = get_templates()

    if not templates:
        st.info("No outreach templates saved yet. Create your first template using the editor above!")
    else:
        if "editing_tpl_id" not in st.session_state:
            st.session_state["editing_tpl_id"] = None

        for tpl in templates:
            tpl_id = tpl["id"]
            is_tpl_editing = (st.session_state.get("editing_tpl_id") == tpl_id)

            with st.expander(f"📑 {tpl['template_name']}", expanded=is_tpl_editing):
                if tpl.get("created_at"):
                    st.markdown(f"<div class='timestamp-right'>Created: {tpl['created_at'][:10]}</div>", unsafe_allow_html=True)

                if is_tpl_editing:
                    st.markdown(f"""
                    <div style="background: rgba(14, 46, 39, 0.65); border: 1px solid #10B981; border-radius: 10px; padding: 14px 18px; margin: 8px 0 14px;">
                        <strong style="color: #34D399;">✏️ Editing Template #{tpl_id}: {tpl['template_name']}</strong>
                    </div>
                    """, unsafe_allow_html=True)

                    edit_t_name = st.text_input("Template Name", value=tpl["template_name"], key=f"edit_tname_{tpl_id}")

                    col_edit_src, col_edit_prev = st.columns([1.1, 1.1])
                    with col_edit_src:
                        st.markdown("##### HTML & Spintax Source")
                        st.caption("Edit HTML tags and Spintax directly. Insert dynamic placeholders like `[Name]` or `[Company]`.")
                        edit_tpl_key = f"edit_tpl_content_{tpl_id}"
                        if edit_tpl_key not in st.session_state:
                            st.session_state[edit_tpl_key] = tpl["body_content"]

                        edit_src_txt = st.text_area(
                            "HTML / Spintax Source",
                            value=st.session_state[edit_tpl_key],
                            height=260,
                            key=f"src_edit_tpl_{tpl_id}",
                            label_visibility="collapsed"
                        )
                        st.session_state[edit_tpl_key] = edit_src_txt

                        # Deliverability Audit Gate
                        edit_body_to_audit = edit_src_txt.strip()
                        neg_kw = get_config("negative_keywords", "")
                        edit_detected_kws = scan_all_negative_keywords(edit_body_to_audit, neg_kw)
                        if edit_detected_kws:
                            edit_kws_badges = ", ".join(f"`{k}`" for k in edit_detected_kws)
                            st.warning(f"Contains restricted trigger keyword(s): {edit_kws_badges}")

                        col_save_e, col_canc_e = st.columns([1.5, 3])
                        with col_save_e:
                            if st.button("Save Changes", type="primary", use_container_width=True, key=f"save_edit_tpl_btn_{tpl_id}"):
                                if not edit_t_name.strip() or not edit_body_to_audit:
                                    st.error("Both Name and Body are required.")
                                else:
                                    update_template(tpl_id, edit_t_name.strip(), edit_body_to_audit)
                                    st.session_state["editing_tpl_id"] = None
                                    st.success(f"Template '{edit_t_name}' successfully updated!")
                                    st.rerun()
                        with col_canc_e:
                            if st.button("Cancel", use_container_width=True, key=f"canc_edit_tpl_btn_{tpl_id}"):
                                st.session_state["editing_tpl_id"] = None
                                st.rerun()

                    with col_edit_prev:
                        col_eph, col_eprb = st.columns([2.5, 1.5])
                        with col_eph:
                            st.markdown("##### Live Formatted Preview")
                            st.caption("Resolved with sample lead data:")
                        with col_eprb:
                            if st.button("Re-roll Spintax", key=f"btn_reroll_edit_tpl_{tpl_id}", use_container_width=True):
                                st.rerun()

                        if edit_body_to_audit:
                            sample_lead = {
                                "name": "Jack Connor",
                                "company": "Summit Brands",
                                "email": "jack@summitbrands.com",
                                "custom_variables": {"Role": "Founder", "Website": "https://summitbrands.com"}
                            }
                            resolved_edit_preview = resolve_template(edit_body_to_audit, sample_lead)
                            render_html_preview(resolved_edit_preview, height=260)
                        else:
                            st.caption("Enter template source on the left to see live preview.")

                    st.markdown("<hr style='margin: 1rem 0; opacity: 0.2;'>", unsafe_allow_html=True)

                st.markdown("**Rendered Preview:**")
                render_html_preview(tpl["body_content"], height=200)

                col_tp1, col_tp2, col_tp3 = st.columns([1.5, 1.5, 1])
                with col_tp1:
                    edit_toggle_btn = st.button("✏️ Edit Template", key=f"edit_toggle_{tpl_id}")
                    if edit_toggle_btn:
                        st.session_state["editing_tpl_id"] = None if is_tpl_editing else tpl_id
                        st.rerun()
                with col_tp2:
                    test_btn = st.button("🧪 Test Spintax & Vars", key=f"test_tpl_{tpl_id}")
                with col_tp3:
                    if st.session_state.get(f"confirm_del_tpl_{tpl_id}"):
                        if st.button("Confirm", key=f"del_conf_tpl_{tpl_id}", use_container_width=True):
                            delete_template(tpl_id)
                            st.session_state[f"confirm_del_tpl_{tpl_id}"] = False
                            st.warning(f"Template '{tpl['template_name']}' deleted.")
                            st.rerun()
                    else:
                        if st.button("🗑️ Delete", key=f"del_tpl_{tpl_id}"):
                            st.session_state[f"confirm_del_tpl_{tpl_id}"] = True
                            st.rerun()

                if test_btn:
                    # Pick sample or first contact for preview
                    sample_contact = contacts_list[0] if contacts_list else {
                        "name": "Sarah Jenkins",
                        "company": "Apex Outdoors",
                        "email": "sarah@apex.com",
                        "custom_variables_dict": {"Role": "Founder"}
                    }
                    resolved = resolve_template(tpl["body_content"], sample_contact)

                    st.markdown(f"**Randomized Resolution with contact '{sample_contact['name']}' at '{sample_contact['company']}':**")
                    render_html_preview(resolved, height=200)
