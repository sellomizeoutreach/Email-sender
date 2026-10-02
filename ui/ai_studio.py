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


# Default Bot Training Configuration (Trained on Sellomize 15 Core Templates & Rules)
DEFAULT_SYSTEM_PROMPT = (
    "You are an elite B2B cold email copywriter for Sellomize, an Amazon & E-commerce growth agency.\n"
    "Your objective is to craft high-converting, deeply human, concise outreach emails that get opened and replied to.\n\n"
    "CRITICAL COPYWRITING RULES:\n"
    "1. Structure & Flow:\n"
    "   - Keep emails phone-friendly (60–100 words max).\n"
    "   - 1-2 sentences per paragraph with clean line breaks.\n"
    "   - Opening: If cold initial email, start with: 'Hi [Name],\\n\\nWe haven’t been properly introduced, but I’m Jack with Sellomize.'\n"
    "     If prospect name is missing or unknown, fall back to: 'Hi,\\n\\n'\n"
    "   - Subject Line: Use verified patterns: '[Company] + Sellomize' or '[Company] + [Location] + Sellomize' (ONLY use location if verified, otherwise omit cleanly).\n"
    "   - Call-to-Action: Always use the low-friction CTA:\n"
    "     'Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite.'\n"
    "   - Do NOT include automated signature blocks unless requested.\n\n"
    "2. Strict Fact-Safety (NEVER Hallucinate):\n"
    "   - NEVER invent contact names, locations, revenue figures, ROAS, reviews, or unverified claims.\n"
    "   - Any client outcome or case study numbers (e.g. 161 shipments, $8,145.91 reimbursement, 6.99 ROAS) must strictly be labeled as SELLOMIZE CLIENT RESULTS, never prospect data.\n\n"
    "3. Follow-Ups & Friendly Reminders (Template 16 Rules):\n"
    "   - When crafting a follow-up or friendly reminder, use the Template 16 structure:\n"
    "     SUBJECT: Re: [Company] + Sellomize\n"
    "     BODY:\n"
    "     Hi [Name],\n\n"
    "     Just popping this back up before it gets lost in the inbox shuffle. 😄\n\n"
    "     Wanted to see if you had a chance to look at my note about [AmazonIssue] for [Company].\n\n"
    "     I still think it’s worth a quick look.\n\n"
    "     Would you be open to taking a look together? Let me know what works and I’ll send a calendar invite.\n"
    "   - Keep follow-ups much shorter than the original email (under 50 words).\n"
    "   - Never repeat the original pitch, client case study, or all Amazon research.\n"
    "   - Mention only the topic of the previous email with friendly, human tone.\n"
    "   - Tasteful light jokes allowed (e.g. 'bringing this back before your inbox buries it deeper than page 5 of Amazon search 🔍', or 'Assuming you didn't get eaten by the Amazon algorithm this week... 😅').\n"
    "   - Do not introduce new claims or unverified facts.\n"
    "   - Use [AmazonIssue] ONLY when verified; if unavailable, remove cleanly (e.g. 'my note for [Company]') rather than inventing one.\n"
    "   - Maintain the same subject thread using 'Re:'.\n"
    "   - Stop sequence immediately if prospect replies.\n"
    "   - Do not add the Jack Conner / Sellomize signature automatically.\n\n"
    "4. FORBIDDEN CORPORATE JARGON (Strictly Banned):\n"
    "   - NEVER use: leverage, utilize, robust, seamless, delve, streamline, unlock, elevate, game-changer, unparalleled, comprehensive, facilitate, additionally, numerous, significant, ensure, optimal, holistic, cutting-edge, innovative, dynamic, foster, underscore, testament, landscape, realm, tapestry, endeavor, ascertain, commence, procure.\n"
    "   - Banned openings: 'I hope this email finds you well', 'I wanted to reach out', 'I noticed you may be', 'Great opportunity'.\n\n"
    "5. Format strictly as:\n"
    "SUBJECT: <subject line>\n"
    "BODY:\n"
    "<email body text>"
)

DEFAULT_AGENCY_SERVICES = (
    "Sellomize is an Amazon & E-commerce growth partner. Core expertise:\n"
    "- Full-Service Amazon Growth: listing content, PPC, SEO, and catalog positioning.\n"
    "- Listing Conversion: SEO, titles, bullets, product images, infographics, video, and A+ Content.\n"
    "- PPC Optimization: trimming organic/sponsored overlap, keyword rank expansion, scaling budget to high-converting ASINs.\n"
    "- FBA Inventory & Reconciliation: auditing lost shipments and Cubiscan measurement errors for cash reimbursements.\n"
    "- Technical Account Health: Buy Box suppression, stranded inventory, variation cleanup, and competitor conquesting."
)

DEFAULT_GUIDELINES = (
    "- Do: Write like a specialist sending a quick, thoughtful note from an iPhone.\n"
    "- Do: Reference concrete observations from the attached screenshot or Amazon research.\n"
    "- Do: Add light, witty humor to follow-ups.\n"
    "- Don't: Sound like generic AI or corporate marketing copy.\n"
    "- Don't: Invent unverified statistics or make false promises."
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

            selected_lead_data = None
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
                    selected_lead_data = chosen_lead
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

            # -----------------------------------------------------------------
            # Template Recommendation & Selection
            # -----------------------------------------------------------------
            from sellomize_templates import (
                CORE_15_TEMPLATES,
                APPROVED_CLIENT_STORIES,
                recommend_template_for_lead
            )

            rec_tpl = None
            rec_reason = ""
            if selected_lead_data:
                rec_tpl, rec_reason = recommend_template_for_lead(selected_lead_data)

            st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
            st.markdown("<span class='lbl'>2. Sellomize Core Template / Framework</span>", unsafe_allow_html=True)

            tpl_choices = ["AI Custom Framework (Write from scratch)"] + [
                f"{t['id']}. {t['category']}" for t in CORE_15_TEMPLATES
            ]

            def_idx = 0
            if rec_tpl:
                match_label = f"{rec_tpl['id']}. {rec_tpl['category']}"
                if match_label in tpl_choices:
                    def_idx = tpl_choices.index(match_label)
                    st.success(f"🎯 **Auto-Recommended Template:** {rec_tpl['category']}\n*{rec_reason}*")

            chosen_tpl_option = st.selectbox(
                "Framework / Template Base",
                tpl_choices,
                index=def_idx,
                key="ai_tpl_framework_select"
            )

            # Optional 3-Part Client Proof selection
            selected_story_key = None
            if "PPC" in chosen_tpl_option or "Reconciliation" in chosen_tpl_option or "Product" in chosen_tpl_option:
                st.caption("Include verified 3-part Sellomize Client Proof:")
                story_opts = ["None", "PPC Scaling ($100k sales / 6.99 ROAS)", "FBA Reconciliation ($9,602 recovered)", "Overlooked SKU Repositioning"]
                chosen_story_opt = st.selectbox("Client Proof", story_opts, key="ai_story_opt")
                if "PPC" in chosen_story_opt:
                    selected_story_key = "ppc_scaling"
                elif "FBA" in chosen_story_opt:
                    selected_story_key = "reconciliation_fba"
                elif "Overlooked" in chosen_story_opt:
                    selected_story_key = "overlooked_sku"

            st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
            st.markdown("<span class='lbl'>3. Multimodal Image Analysis (Amazon Listing / Audit Screenshot)</span>", unsafe_allow_html=True)
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
                uploaded_image.seek(0)
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
            st.markdown("<span class='lbl'>4. Pitch Objective &amp; Angles</span>", unsafe_allow_html=True)

            pitch_angles = [
                "Initial Outreach (Cold Introduction with Specific Observation)",
                "Follow-Up / Bump (Short, human note with light humor)",
                "Listing Audit Flaw (Identify 1-2 real flaws in title/images/A+)",
                "Currently Unavailable / Suppressed ASIN (Point out lost sales)",
                "Competitor Comparison (Point out where competitors are outranking)",
                "Custom Directive (Describe your own prompt below)"
            ]

            selected_angle = st.selectbox(
                "Pitch Objective",
                pitch_angles,
                key="ai_pitch_angle"
            )

            custom_prompt_input = st.text_area(
                "Specific Instructions or Context (Optional)",
                placeholder="e.g. Reference their low review velocity and offer a quick 5-minute Loom breakdown.",
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
                            user_prompt += f"CRM Notes / Research: {target_notes}\n"
                        if chosen_tpl_option and "AI Custom Framework" not in chosen_tpl_option:
                            user_prompt += f"Base Sellomize Category / Template: {chosen_tpl_option}\n"
                        if selected_story_key and selected_story_key in APPROVED_CLIENT_STORIES:
                            st_item = APPROVED_CLIENT_STORIES[selected_story_key]
                            user_prompt += (
                                f"Client Proof Story (Label as agency outcome, never prospect data):\n"
                                f"- Found: {st_item['found']}\n"
                                f"- Solved: {st_item['solved']}\n"
                                f"- Rewarded: {st_item['rewarded']}\n"
                            )
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
