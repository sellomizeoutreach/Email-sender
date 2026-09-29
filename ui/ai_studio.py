"""
ui/ai_studio.py - AI Studio for Sellomize Reach.

Features:
- Dedicated, isolated multimodal outreach workspace powered by Groq.
- Bot training & customization: Persisted system prompts, agency offerings, tone guidelines, and dos/don'ts.
- Multimodal Vision: Analyze Amazon listing screenshots, product photos, audit graphics, or storefronts.
- Live API Quota & Rate Limit Progress Bar with exact request and token reset timers.
- 1-Click Direct Dispatch:
  - ✍️ Send to Compose (transports subject, body, attached image, and selected lead)
  - 🚀 Send to Bulk Send (loads as scratch template in bulk sender)
  - 📄 Save as Template (saves directly to templates library)
"""

import html
import json
import streamlit as st
from typing import List, Dict, Any, Optional

from database import (
    get_config,
    set_config,
    get_contacts,
    create_template,
)
from groq_client import (
    DEFAULT_GROQ_API_KEY,
    DEFAULT_VISION_MODEL,
    call_groq_completion,
    probe_groq_quota,
    parse_email_output,
    prepare_image_for_groq,
)
from ui.editor import html_to_visual_text
from ui.components import trigger_toast


# Default Bot Training Configuration
DEFAULT_SYSTEM_PROMPT = (
    "You are an elite B2B cold email copywriter for Sellomize Reach. "
    "Your objective is to craft high-converting, human, concise cold outreach emails. "
    "Rules:\n"
    "1. Keep emails under 90 words. Short paragraphs (1-2 sentences each).\n"
    "2. If an image is provided (such as an Amazon listing or product screenshot), "
    "specifically cite 1-2 real visual details you notice in the image (e.g. title flaws, missing badges, image angles, pricing, unavailable status).\n"
    "3. Never use generic corporate jargon like 'leverage', 'delve', 'synergy', 'game-changing', 'in today's fast-paced world'.\n"
    "4. Use variable tokens where appropriate: [Name], [Company].\n"
    "5. End with a low-friction call-to-action asking for thoughts or a quick 2-minute review.\n"
    "6. Format your response strictly as:\n"
    "SUBJECT: <concise 3-6 word subject line>\n"
    "BODY:\n"
    "<email body text>"
)

DEFAULT_AGENCY_SERVICES = (
    "Sellomize is an Amazon & E-commerce growth agency. We specialize in: "
    "Amazon listing optimization, fixing unavailable/suppressed ASINs, A+ Content & Storefront design, "
    "PPC optimization, and improving click-through and conversion rates."
)

DEFAULT_GUIDELINES = (
    "- Do: Reference specific observations from the attached screenshot.\n"
    "- Do: Sound like a friendly peer or specialist, not a pushy sales pitch.\n"
    "- Don't: Introduce yourself with 'My name is X and I work at Y'. Jump straight to the point.\n"
    "- Don't: Write walls of text or include multiple links."
)


def _get_active_api_key() -> str:
    """Retrieve stored Groq API key from system config, st.secrets, or environment."""
    saved = get_config("groq_api_key", None)
    if saved and saved.strip():
        return saved.strip()
    try:
        if "GROQ_API_KEY" in st.secrets:
            return st.secrets["GROQ_API_KEY"].strip()
    except Exception:
        pass
    import os
    return os.environ.get("GROQ_API_KEY", "")


def _render_quota_card(api_key: str):
    """Renders the live Groq API Quota and Rate Limit progress bar."""
    if "groq_rate_limits" not in st.session_state:
        # Load from system config or initialize
        saved_limits_json = get_config("groq_rate_limits_cache", None)
        if saved_limits_json:
            try:
                st.session_state["groq_rate_limits"] = json.loads(saved_limits_json)
            except Exception:
                st.session_state["groq_rate_limits"] = None
        else:
            st.session_state["groq_rate_limits"] = None

    limits = st.session_state.get("groq_rate_limits")

    with st.container(border=True):
        col_hdr, col_ref = st.columns([3.8, 1.2], vertical_alignment="center")
        with col_hdr:
            st.markdown(
                "<span style='font-size:14px; font-weight:700; color:#083731;'>"
                "⚡ Groq API Rate Limit &amp; Quota Monitor</span>",
                unsafe_allow_html=True
            )
        with col_ref:
            if st.button("🔄 Check Quota", key="btn_check_groq_quota", use_container_width=True):
                with st.spinner("Probing Groq quota..."):
                    fresh = probe_groq_quota(api_key)
                    if "error" not in fresh or fresh.get("status") != "unknown":
                        st.session_state["groq_rate_limits"] = fresh
                        set_config("groq_rate_limits_cache", json.dumps(fresh))
                        trigger_toast("Groq quota refreshed!", icon="⚡")
                        st.rerun()
                    else:
                        st.error(f"Quota probe error: {fresh.get('error')}")

        if limits and "limit_requests" in limits:
            rem_req = limits.get("remaining_requests", 1000)
            lim_req = limits.get("limit_requests", 1000)
            reset_req = limits.get("reset_requests", "0s")

            rem_tok = limits.get("remaining_tokens", 8000)
            lim_tok = limits.get("limit_tokens", 8000)
            reset_tok = limits.get("reset_tokens", "0s")

            req_pct = max(0.0, min(1.0, rem_req / lim_req)) if lim_req > 0 else 1.0
            tok_pct = max(0.0, min(1.0, rem_tok / lim_tok)) if lim_tok > 0 else 1.0

            q_col1, q_col2 = st.columns(2)

            with q_col1:
                status_color = "#0F6E56" if req_pct > 0.3 else ("#D97706" if req_pct > 0.1 else "#DC2626")
                st.markdown(
                    f"<div style='font-size:12px; font-weight:600; color:#475569; margin-bottom:4px; display:flex; justify-content:space-between;'>"
                    f"<span>Requests Remaining: <strong style='color:{status_color};'>{rem_req:,} / {lim_req:,}</strong></span>"
                    f"<span style='color:#64748B;'>Resets in: <strong>{reset_req}</strong></span>"
                    f"</div>",
                    unsafe_allow_html=True
                )
                st.progress(req_pct)

            with q_col2:
                tok_color = "#0F6E56" if tok_pct > 0.3 else ("#D97706" if tok_pct > 0.1 else "#DC2626")
                st.markdown(
                    f"<div style='font-size:12px; font-weight:600; color:#475569; margin-bottom:4px; display:flex; justify-content:space-between;'>"
                    f"<span>Tokens Remaining: <strong style='color:{tok_color};'>{rem_tok:,} / {lim_tok:,}</strong></span>"
                    f"<span style='color:#64748B;'>Token reset in: <strong>{reset_tok}</strong></span>"
                    f"</div>",
                    unsafe_allow_html=True
                )
                st.progress(tok_pct)
        else:
            st.caption("Quota information will update automatically on your first AI generation or by clicking **🔄 Check Quota**.")


def render_ai_studio_tab(all_contacts: Optional[List[Dict[str, Any]]] = None):
    """Render the full AI Studio screen."""
    if all_contacts is None:
        all_contacts = get_contacts()

    api_key = _get_active_api_key()

    # Quota Card at Top
    _render_quota_card(api_key)

    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    tab_writer, tab_training = st.tabs([
        "🎯 Outreach Pitch Generator (Multimodal Vision)",
        "🧠 Bot Training & Settings"
    ])

    # =========================================================================
    # TAB 1: OUTREACH PITCH GENERATOR
    # =========================================================================
    with tab_writer:
        col_input, col_output = st.columns([1.1, 0.9], gap="large")

        with col_input:
            st.markdown("<span class='lbl'>1. Target Prospect / Lead Context</span>", unsafe_allow_html=True)

            lead_source_choice = st.radio(
                "Lead Source",
                ["From CRM Leads", "Custom Prospect"],
                horizontal=True,
                label_visibility="collapsed",
                key="ai_lead_source_choice"
            )

            selected_lead_id = None
            target_company = ""
            target_name = ""
            target_notes = ""

            if lead_source_choice == "From CRM Leads":
                if all_contacts:
                    lead_map = {
                        f"#SLM-{c['id']:04d}: {c.get('name') or 'Lead'} ({c.get('company') or 'No Company'})": c
                        for c in all_contacts
                    }
                    chosen_lbl = st.selectbox(
                        "Select Lead",
                        list(lead_map.keys()),
                        label_visibility="collapsed",
                        key="ai_lead_select"
                    )
                    chosen_lead = lead_map[chosen_lbl]
                    selected_lead_id = chosen_lead["id"]
                    target_company = chosen_lead.get("company") or ""
                    target_name = chosen_lead.get("name") or ""
                    target_notes = chosen_lead.get("notes") or ""

                    st.caption(f"🏢 Company: **{target_company or 'N/A'}** · 👤 Contact: **{target_name or 'N/A'}**")
                else:
                    st.info("No leads found in CRM. Switch to 'Custom Prospect' below or add leads in the Leads tab.")
            else:
                c1, c2 = st.columns(2)
                with c1:
                    target_company = st.text_input("Brand / Company Name", placeholder="e.g. Facile Skincare", key="ai_custom_company")
                with c2:
                    target_name = st.text_input("Contact Name", placeholder="e.g. Danielle Nadick", key="ai_custom_name")

            st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
            st.markdown("<span class='lbl'>2. Multimodal Image Analysis (Amazon Listing / Audit Screenshot)</span>", unsafe_allow_html=True)
            st.caption("Upload a screenshot of their Amazon product listing, storefront, search results, or competitor audit.")

            uploaded_image = st.file_uploader(
                "Upload Screenshot / Image",
                type=["png", "jpg", "jpeg", "webp"],
                key="ai_image_upload",
                help="Groq Vision will inspect the image and identify real listing flaws to personalize the pitch."
            )

            image_bytes: Optional[bytes] = None
            image_data_uri: Optional[str] = None

            if uploaded_image is not None:
                image_bytes = uploaded_image.read()
                try:
                    image_data_uri, _ = prepare_image_for_groq(image_bytes)
                    img_col1, img_col2 = st.columns([1, 2])
                    with img_col1:
                        st.image(image_bytes, caption="Uploaded image for vision analysis", use_container_width=True)
                    with img_col2:
                        attach_inline = st.checkbox(
                            "📎 Attach this image inline in the email body (as [Image 1])",
                            value=True,
                            key="ai_attach_image_inline",
                            help="When sent to Compose, this image is embedded directly in the message body so the recipient sees it."
                        )
                except Exception as img_err:
                    st.error(f"Image processing error: {img_err}")

            st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
            st.markdown("<span class='lbl'>3. Pitch Objective &amp; Angles</span>", unsafe_allow_html=True)

            pitch_angles = [
                "Listing Audit Flaw (Identify 1-2 real flaws in title/images/A+ and offer free audit)",
                "Currently Unavailable / Suppressed ASIN (Point out lost sales on unavailable listings)",
                "Competitor Comparison (Point out where competitors are outranking or out-converting)",
                "Follow-Up / Bump (Referencing the audit observation with a fresh bump)",
                "Custom Directive (Describe your own prompt below)"
            ]

            selected_angle = st.selectbox(
                "Pitch Objective",
                pitch_angles,
                key="ai_pitch_angle"
            )

            custom_prompt_input = st.text_area(
                "Specific Instructions or Context (Optional)",
                placeholder="e.g. Focus on their missing A+ content and low review velocity. Offer a 3-minute video teardown.",
                key="ai_custom_directive",
                height=70
            )

            # Generate Button
            st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
            generate_clicked = st.button(
                "✨ Generate Outreach Pitch",
                type="primary",
                use_container_width=True,
                key="btn_run_ai_generation"
            )

            if generate_clicked:
                if not api_key:
                    st.error("Please configure your Groq API key in the 'Bot Training & Settings' tab.")
                else:
                    with st.spinner("⚡ Groq Vision analyzing context & drafting email..."):
                        # Build system prompt from training
                        sys_prompt = get_config("ai_bot_system_prompt", DEFAULT_SYSTEM_PROMPT)
                        services = get_config("ai_bot_services", DEFAULT_AGENCY_SERVICES)
                        guidelines = get_config("ai_bot_guidelines", DEFAULT_GUIDELINES)

                        full_sys_prompt = (
                            f"{sys_prompt}\n\n"
                            f"AGENCY SERVICES & OFFERINGS:\n{services}\n\n"
                            f"WRITING GUIDELINES:\n{guidelines}"
                        )

                        user_prompt = f"Target Company: {target_company or '[Company]'}\n"
                        if target_name:
                            user_prompt += f"Target Contact: {target_name}\n"
                        if target_notes:
                            user_prompt += f"CRM Notes on Lead: {target_notes}\n"
                        user_prompt += f"Pitch Angle: {selected_angle}\n"
                        if custom_prompt_input.strip():
                            user_prompt += f"Additional Directives: {custom_prompt_input.strip()}\n"

                        if image_bytes:
                            user_prompt += (
                                "\nPlease analyze the attached image closely. Specifically cite at least 1-2 real, "
                                "concrete visual details from the image in your email body to prove we actually looked at their brand."
                            )

                        try:
                            raw_output, limits = call_groq_completion(
                                api_key=api_key,
                                system_prompt=full_sys_prompt,
                                user_prompt=user_prompt,
                                image_bytes=image_bytes,
                                model=DEFAULT_VISION_MODEL,
                                max_tokens=750
                            )

                            # Save rate limits
                            st.session_state["groq_rate_limits"] = limits
                            set_config("groq_rate_limits_cache", json.dumps(limits))

                            subj, body = parse_email_output(raw_output)
                            st.session_state["ai_generated_subject"] = subj
                            st.session_state["ai_generated_body"] = body
                            st.session_state["ai_cached_image_bytes"] = image_bytes
                            st.session_state["ai_cached_lead_id"] = selected_lead_id
                            trigger_toast("Outreach pitch generated successfully!", icon="✨")
                            st.rerun()

                        except Exception as gen_err:
                            st.error(f"Generation failed: {gen_err}")

        # =====================================================================
        # OUTPUT & DISPATCH COLUMN
        # =====================================================================
        with col_output:
            st.markdown("<span class='lbl'>AI Generated Pitch &amp; Dispatch</span>", unsafe_allow_html=True)

            gen_subject = st.session_state.get("ai_generated_subject", "")
            gen_body = st.session_state.get("ai_generated_body", "")

            if not gen_subject and not gen_body:
                st.info(
                    "👈 Select a lead or enter custom details, optionally upload an image, and click "
                    "**✨ Generate Outreach Pitch** to view and edit your tailored email here."
                )
            else:
                st.markdown("<span class='lbl' style='font-size:12px;'>Subject Line</span>", unsafe_allow_html=True)
                subj_input = st.text_input(
                    "Subject",
                    value=gen_subject,
                    label_visibility="collapsed",
                    key="ai_out_subject"
                )

                st.markdown("<span class='lbl' style='font-size:12px;'>Email Body (Visual / Text)</span>", unsafe_allow_html=True)
                body_input = st.text_area(
                    "Body",
                    value=gen_body,
                    label_visibility="collapsed",
                    height=220,
                    key="ai_out_body"
                )

                # Live preview box
                st.markdown("<span class='lbl' style='font-size:12px;'>Live Email Preview</span>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f"<div style='font-size:13px; font-weight:700; color:#083731; margin-bottom:6px;'>Subject: {html.escape(subj_input)}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size:13px; color:#1E293B; line-height:1.6; white-space:pre-wrap;'>{html.escape(body_input)}</div>", unsafe_allow_html=True)

                    has_image = bool(st.session_state.get("ai_cached_image_bytes")) and st.session_state.get("ai_attach_image_inline", True)
                    if has_image:
                        st.markdown(
                            "<div style='margin-top:8px; padding:6px 10px; background:#F1F5F9; border-radius:6px; font-size:11px; color:#475569;'>"
                            "🖼️ <strong>Attached Screenshot:</strong> Will be embedded inline as [Image 1] when dispatched to Compose."
                            "</div>",
                            unsafe_allow_html=True
                        )

                # =============================================================
                # 3 DIRECT DISPATCH ACTIONS
                # =============================================================
                st.markdown("<span class='lbl' style='font-size:12px; margin-top:10px;'>Dispatch by Choice</span>", unsafe_allow_html=True)
                c_act1, c_act2, c_act3 = st.columns(3)

                with c_act1:
                    if st.button("✍️ Send to Compose", type="primary", use_container_width=True, key="ai_btn_to_compose"):
                        # Build final body HTML
                        final_body_html = body_input.replace("\n", "<br>")
                        if has_image and image_data_uri:
                            final_body_html += f'<br><br><img src="{image_data_uri}" style="max-width:100%; border-radius:6px; margin:8px 0;" />'

                        st.session_state["compose_subject"] = subj_input
                        st.session_state["comp_subj_in"] = subj_input
                        st.session_state["compose_body_html"] = final_body_html
                        st.session_state["compose_editor_body_html"] = final_body_html
                        clean_v, _ = html_to_visual_text(final_body_html)
                        st.session_state["compose_editor_visual_textarea"] = clean_v
                        st.session_state["compose_editor_last_synced_html"] = final_body_html

                        cached_lead_id = st.session_state.get("ai_cached_lead_id")
                        if cached_lead_id:
                            st.session_state["compose_selected_lead_id"] = cached_lead_id

                        trigger_toast("Transferred to Compose!", icon="✍️")
                        st.session_state["active_screen"] = "compose"
                        st.rerun()

                with c_act2:
                    if st.button("🚀 Send to Bulk", use_container_width=True, key="ai_btn_to_bulk"):
                        final_bulk_html = body_input.replace("\n", "<br>")
                        st.session_state["bulk_mode"] = "scratch"
                        st.session_state["bulk_subject"] = subj_input
                        st.session_state["bulk_subj_input"] = subj_input
                        st.session_state["bulk_body_html"] = final_bulk_html
                        st.session_state["bulk_tpl_editor_body_html"] = final_bulk_html
                        clean_v, _ = html_to_visual_text(final_bulk_html)
                        st.session_state["bulk_tpl_editor_visual_textarea"] = clean_v
                        st.session_state["bulk_tpl_editor_last_synced_html"] = final_bulk_html

                        trigger_toast("Transferred to Bulk Send!", icon="🚀")
                        st.session_state["active_screen"] = "bulk"
                        st.rerun()

                with c_act3:
                    if st.button("📄 Save as Template", use_container_width=True, key="ai_btn_save_tpl"):
                        tpl_name = f"AI: {subj_input[:30]}"
                        final_tpl_html = body_input.replace("\n", "<br>")
                        new_id = create_template(
                            name=tpl_name,
                            subject=subj_input,
                            body_html=final_tpl_html
                        )
                        trigger_toast(f"Saved template #{new_id} ({tpl_name})!", icon="📄")

    # =========================================================================
    # TAB 2: BOT TRAINING & SETTINGS
    # =========================================================================
    with tab_training:
        st.markdown(
            "<div style='font-size:14px; font-weight:700; color:#083731; margin-bottom:2px;'>"
            "🧠 Train Your Outreach AI Bot</div>"
            "<div style='font-size:12px; color:#64748B; margin-bottom:14px;'>"
            "Customize the knowledge base, agency identity, and writing rules so all generated pitches "
            "match your exact business voice. All settings persist permanently in your database.</div>",
            unsafe_allow_html=True
        )

        with st.form("form_ai_bot_training"):
            # 1. API Key
            curr_key = _get_active_api_key()
            key_in = st.text_input(
                "Groq API Key",
                value=curr_key,
                type="password",
                help="Your Groq API key from console.groq.com."
            )

            # 2. System Prompt / Personality
            curr_sys = get_config("ai_bot_system_prompt", DEFAULT_SYSTEM_PROMPT)
            sys_in = st.text_area(
                "System Persona & Core Instructions",
                value=curr_sys,
                height=150,
                help="Governs the overall personality, constraints, and length of every email."
            )

            # 3. Agency Services & Offerings
            curr_serv = get_config("ai_bot_services", DEFAULT_AGENCY_SERVICES)
            serv_in = st.text_area(
                "Agency Services & Value Proposition",
                value=curr_serv,
                height=90,
                help="What services, audits, or benefits does Sellomize pitch to prospects?"
            )

            # 4. Dos and Don'ts / Guidelines
            curr_guide = get_config("ai_bot_guidelines", DEFAULT_GUIDELINES)
            guide_in = st.text_area(
                "Copywriting Dos and Don'ts",
                value=curr_guide,
                height=90,
                help="Specific positive and negative constraints to ensure high reply rates."
            )

            c_save, c_reset = st.columns([3, 1])
            with c_save:
                save_training_clicked = st.form_submit_button("💾 Save Bot Training & Settings", type="primary", use_container_width=True)
            with c_reset:
                reset_training_clicked = st.form_submit_button("↺ Restore Defaults", use_container_width=True)

        if save_training_clicked:
            set_config("groq_api_key", key_in.strip())
            set_config("ai_bot_system_prompt", sys_in.strip())
            set_config("ai_bot_services", serv_in.strip())
            set_config("ai_bot_guidelines", guide_in.strip())
            trigger_toast("Bot training & settings saved permanently!", icon="✅")
            st.rerun()

        if reset_training_clicked:
            set_config("ai_bot_system_prompt", DEFAULT_SYSTEM_PROMPT)
            set_config("ai_bot_services", DEFAULT_AGENCY_SERVICES)
            set_config("ai_bot_guidelines", DEFAULT_GUIDELINES)
            trigger_toast("Restored default training prompt & guidelines.", icon="↺")
            st.rerun()
