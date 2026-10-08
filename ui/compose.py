"""
ui/compose.py - Compose Screen for Sellomize Reach.

Features:
- Mailbox selection & recipient selection with top-level custom-address toggle.
- Tab-based sequence editor: Initial Email and Follow-ups in the same horizontal row,
  each editable completely independently.
- Live preview on the right side including Subject line preview, resolved variables,
  and corporate signature.
- Atomic sending & scheduling: Send now or Schedule queues both initial outreach
  and any configured follow-up steps.
"""

import streamlit as st
import html
import re
import os
import json
import base64
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

from database import (
    get_contacts,
    get_templates,
    get_smtp_accounts,
    get_config,
    create_email,
    get_emails,
    get_contact_by_id,
    get_contact_by_email,
    upsert_contact_by_email,
    create_template,
    update_template,
    update_contact,
    mark_email_error,
    get_lead_images,
    delete_lead_image,
    update_image_filename,
    DB_FILE,
)
from template_engine import (
    resolve_template,
    inject_variables,
    parse_spintax,
    audit_email_deliverability,
    _missing_tokens,
    format_email_html,
    deduplicate_email_signature,
    has_signature_marker,
)
from scheduler import dispatch_email_hostinger
from timezone_helper import get_engine_now, get_engine_now_str
from ui.editor import render_dual_mode_editor, visual_text_to_html
from ui.rich_editor import render_rich_editor, is_rich_editor_enabled, process_and_store_image
from ui.components import trigger_toast

_TOKEN_RE = re.compile(r'\[([A-Za-z0-9_]+)\]|\{([A-Za-z0-9_]+)\}')

# Backward compatibility copies for legacy tests
DEFAULT_COPIES = {
    1: {
        "subj": "Quick observation for [Company]",
        "body": "Hi {first_name},\n\nI noticed [Company] and wanted to reach out regarding your growth.\n\nBest regards,",
    },
    2: {
        "subj": "Re: Quick observation for [Company]",
        "body": "Hi {first_name},\n\nJust following up on my previous note.\n\nBest,",
    },
    3: {
        "subj": "Final note for [Company]",
        "body": "Hi {first_name},\n\nI haven't heard back, so I'll assume the timing isn't right. Wishing you success!",
    },
}


def _apply_variable_fallback(text: str, fallback: str) -> str:
    if not fallback:
        return _TOKEN_RE.sub("", text)
    return _TOKEN_RE.sub(fallback, text)


def get_lead_identifier(lead: Optional[Dict[str, Any]]) -> str:
    """Extract or generate a clean, stable lead code like L-0147 or email identifier."""
    if not lead:
        return "general"
    vars_dict = lead.get("custom_variables_dict")
    if not vars_dict and isinstance(lead.get("custom_variables"), str):
        try:
            vars_dict = json.loads(lead["custom_variables"])
        except Exception:
            vars_dict = {}
    if isinstance(vars_dict, dict):
        for k in ("lead_code", "Lead Code", "lead_id", "Lead ID", "code", "LeadCode"):
            if vars_dict.get(k):
                return str(vars_dict[k]).strip()
    if lead.get("id"):
        return f"L-{lead['id']:04d}"
    if lead.get("email"):
        return re.sub(r'[^a-zA-Z0-9_\-]', '_', str(lead.get("email")).strip().lower())
    return "general"


def insert_lead_image_into_editor(
    image_rec: Dict[str, Any],
    editor_key: str = "compose_rich_editor",
    append_mode: bool = False
):
    """Inserts a lead library image directly into the editor body."""
    textarea_key = f"{editor_key}_visual_textarea"
    cursor_tracker_key = f"{editor_key}_cursor_pos"
    cursor_input_key = f"{editor_key}_cursor_input"
    img_map_key = f"{editor_key}_img_map"
    state_key = f"{editor_key}_html_content"
    last_synced_html = f"{editor_key}_last_synced_html"

    live_visual = st.session_state.get(textarea_key, "")
    current_pos = st.session_state.get(cursor_tracker_key, 0)
    inp_val = st.session_state.get(cursor_input_key, "")
    if inp_val and str(inp_val).strip().isdigit():
        current_pos = int(str(inp_val).strip())

    if append_mode or (current_pos <= 0 and live_visual.strip()):
        current_pos = len(live_visual)
    elif current_pos < 0:
        current_pos = 0
    elif current_pos > len(live_visual):
        current_pos = len(live_visual)

    if img_map_key not in st.session_state:
        st.session_state[img_map_key] = {}
    img_map = st.session_state[img_map_key]

    existing_nums = [
        int(m.group(1)) for m in re.finditer(r'\[Image\s+(\d+)\]', live_visual, re.IGNORECASE)
    ]
    next_num = (max(existing_nums) + 1) if existing_nums else (len(img_map) + 1)
    placeholder = f"[Image {next_num}]"

    s_key = image_rec.get("storage_key", "")
    full_fpath = os.path.join("assets/uploads", s_key) if not os.path.isabs(s_key) else s_key
    if os.path.exists(full_fpath):
        with open(full_fpath, "rb") as fp:
            b64_raw = base64.b64encode(fp.read()).decode("utf-8")
        data_uri = f"data:{image_rec.get('mime_type', 'image/jpeg')};base64,{b64_raw}"
    else:
        data_uri = ""

    f_name = image_rec.get("filename") or "Screenshot"
    w = image_rec.get("width") or 600
    tag = (f'<img src="{data_uri}" alt="{html.escape(f_name)}" '
           f'style="max-width:100%; width:{w}px; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')

    img_map[placeholder] = tag

    before = live_visual[:current_pos].rstrip()
    after = live_visual[current_pos:].lstrip()
    prefix = "\n\n" if before else ""
    suffix = "\n\n" if after else ""
    new_vis = f"{before}{prefix}{placeholder}{suffix}{after}"
    new_pos = len(before + prefix + placeholder)

    new_html = visual_text_to_html(new_vis, img_map)
    st.session_state[state_key] = new_html
    st.session_state[textarea_key] = new_vis
    st.session_state[last_synced_html] = new_html
    st.session_state[cursor_tracker_key] = new_pos
    if cursor_input_key in st.session_state:
        st.session_state[cursor_input_key] = str(new_pos)
    st.session_state["compose_body_html"] = new_html


def _stub_contact(email: str) -> dict:
    return {
        "id": None,
        "name": "",
        "email": email,
        "company": "",
        "country_or_timezone": "LOCAL",
        "custom_variables_dict": {},
        "custom_variables": "{}",
    }


def _resolve_all_recipients(crm_ids, manual_emails, contact_id_map,
                             num_touches=1, save_manual=False):
    recipients = []
    seen_emails = set()

    for cid in crm_ids:
        c = contact_id_map.get(cid) or get_contact_by_id(cid)
        if c:
            email = (c.get("email") or "").strip().lower()
            if email and email not in seen_emails:
                seen_emails.add(email)
                recipients.append({"contact": c, "source": "crm"})

    for em in manual_emails:
        em_clean = em.strip().lower()
        if em_clean in seen_emails:
            continue
        seen_emails.add(em_clean)
        existing = get_contact_by_email(em_clean)
        if existing:
            recipients.append({"contact": existing, "source": "crm_found"})
        elif save_manual or num_touches > 1:
            new_id, _ = upsert_contact_by_email(name="", email=em_clean, company="")
            new_c = get_contact_by_id(new_id) or _stub_contact(em_clean)
            new_c["id"] = new_id
            recipients.append({"contact": new_c, "source": "manual_saved"})
        else:
            recipients.append({"contact": _stub_contact(em_clean), "source": "manual_stub"})

    return recipients


def _get_default_copy(touch_step, sample_contact, is_single_recipient):
    if not is_single_recipient:
        return DEFAULT_COPIES.get(touch_step, DEFAULT_COPIES[1])

    c_name = (sample_contact.get("name") or "").strip()
    c_comp = (sample_contact.get("company") or "").strip()
    greeting  = f"Hi {c_name}," if c_name else "Hi,"
    subj_comp = f" for {c_comp}" if c_comp else ""

    if touch_step == 1:
        return {
            "subj": f"Quick question{subj_comp}",
            "body": f"{greeting}\n\nI wanted to reach out regarding your work at {c_comp or 'your company'}.\n\nBest regards,",
        }
    elif touch_step == 2:
        return {
            "subj": f"Re: Quick question{subj_comp}",
            "body": f"{greeting}\n\nFollowing up to see if you had a chance to review my previous note.\n\nBest,",
        }
    else:
        return {
            "subj": f"Final note{subj_comp}",
            "body": f"{greeting}\n\nI haven't heard back, so I'll assume the timing isn't right. Wishing you continued success!",
        }



def _detect_body_language(text: str) -> str:
    """Heuristic language detection from email body text."""
    t_lower = text.lower()
    spanish_indicators = ["hola", "gracias", "saludos", "estimado", "escribo", "nuestro", "atentamente", "cordial"]
    french_indicators = ["bonjour", "merci", "cordialement", "suite à", "notre", "salutations"]
    german_indicators = ["hallo", "guten tag", "vielen dank", "mit freundlichen", "bezugnehmend"]
    if any(w in t_lower for w in spanish_indicators):
        return "Spanish"
    if any(w in t_lower for w in french_indicators):
        return "French"
    if any(w in t_lower for w in german_indicators):
        return "German"
    return "English"


def _generate_rule_based_followup(
    initial_subject: str,
    initial_body: str,
    step_num: int,
    angle_type: str,
    lead_name: str = "{first_name}",
    company_name: str = "[Company]"
) -> Tuple[str, str]:
    """Generates natural, rule-based follow-up copy tailored to Touch 1."""
    lang = _detect_body_language(initial_body)
    c_subj = initial_subject.strip()
    if not c_subj:
        c_subj = f"{company_name} inquiry"
    re_subj = c_subj if re.match(r'^(re|fwd):', c_subj, re.IGNORECASE) else f"Re: {c_subj}"

    greeting = f"Hi {lead_name}," if lead_name and lead_name != "{first_name}" else "Hi {first_name},"

    if lang == "Spanish":
        greeting_es = f"Hola {lead_name}," if lead_name and lead_name != "{first_name}" else "Hola {first_name},"
        if "nudge" in angle_type.lower():
            body = (
                f"{greeting_es}\n\n"
                f"Te escribo brevemente para dar seguimiento a mi nota anterior sobre {company_name}. "
                f"¿Tuviste oportunidad de revisarla?\n\n"
                f"Avísame si tienes unos minutos esta semana."
            )
        elif "value" in angle_type.lower():
            body = (
                f"{greeting_es}\n\n"
                f"Estuve revisando la situación de {company_name} y encontré una oportunidad interesante que podría aportarles valor inmediato.\n\n"
                f"¿Te interesaría que te comparta un resumen rápido de 2 minutos?"
            )
        elif "alternative" in angle_type.lower():
            body = (
                f"{greeting_es}\n\n"
                f"Dando seguimiento a mi correo anterior. Si no eres la persona indicada en {company_name} para este tema, ¿me podrías orientar con quién debería comunicarme?\n\n"
                f"Agradezco mucho tu ayuda."
            )
        else:
            body = (
                f"{greeting_es}\n\n"
                f"Como no he tenido respuesta, asumo que el momento no es el adecuado para {company_name}.\n\n"
                f"Cierro este contacto por ahora. ¡Mucho éxito en sus proyectos!"
            )
        return re_subj, body

    # English default
    if "nudge" in angle_type.lower():
        body = (
            f"{greeting}\n\n"
            f"Just bumping my previous note regarding {company_name} to the top of your inbox. "
            f"Did you have a quick moment to look it over?\n\n"
            f"Would you be open to a 5-minute chat this week?"
        )
    elif "value" in angle_type.lower():
        body = (
            f"{greeting}\n\n"
            f"Following up on my previous note. I put together a quick observation on how similar brands to {company_name} "
            f"are improving their performance right now.\n\n"
            f"Happy to send over a brief 2-minute overview if this is on your radar."
        )
    elif "alternative" in angle_type.lower():
        body = (
            f"{greeting}\n\n"
            f"Touching base on my earlier message. If you're not the best person at {company_name} to speak with regarding this, "
            f"could you point me toward whoever leads this on your team?\n\n"
            f"Really appreciate your help!"
        )
    else:
        body = (
            f"{greeting}\n\n"
            f"I haven't heard back, so I'll assume timing isn't right for {company_name} right now.\n\n"
            f"I'll close the loop for now—wishing you and your team continued success!"
        )

    return re_subj, body


def _generate_api_followup(
    api_key: str,
    initial_subject: str,
    initial_body: str,
    step_num: int,
    angle_type: str,
    custom_guidance: str = "",
    lead_name: str = "{first_name}",
    company_name: str = "[Company]"
) -> Tuple[str, str]:
    """Generates context-aware follow-up via Groq API or falls back to rule-based copy."""
    from groq_client import call_groq_completion, DEFAULT_TEXT_MODEL
    lang = _detect_body_language(initial_body)

    system_prompt = (
        "You are an elite B2B outreach copywriter for Sellomize. "
        f"Write follow-up touch #{step_num} responding to Touch 1.\n"
        "STRICT RULES:\n"
        f"1. Language: Must match the exact language of the initial email ({lang}).\n"
        "2. Length: Maximum 65 words. Zero fluff.\n"
        "3. Tone: Professional, human, conversational, polite. Never robotic or pushy.\n"
        "4. Do NOT repeat the initial pitch from Touch 1. Reference it as 'my previous note' or 'our note earlier'.\n"
        "5. Include a low-friction call to action.\n"
        "6. Return ONLY a valid JSON object in this format: {\"subject\": \"...\", \"body\": \"...\"}\n"
    )

    user_prompt = (
        f"Initial Subject: {initial_subject}\n"
        f"Initial Body Context:\n{initial_body[:500]}\n\n"
        f"Recipient: {lead_name}\n"
        f"Company: {company_name}\n"
        f"Follow-up Strategy: {angle_type}\n"
        f"Custom Instructions: {custom_guidance.strip() if custom_guidance else 'None'}\n"
    )

    try:
        raw_output, _ = call_groq_completion(
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=DEFAULT_TEXT_MODEL,
            temperature=0.6,
            max_tokens=300
        )
        m = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            s = data.get("subject", "").strip()
            b = data.get("body", "").strip()
            if s and b:
                return s, b
        lines = [ln.strip() for ln in raw_output.strip().split("\n") if ln.strip()]
        if lines:
            if lines[0].lower().startswith("subject:"):
                subj_line = lines[0].split(":", 1)[1].strip()
                body_lines = lines[1:]
                return subj_line, "\n\n".join(body_lines)
            return f"Re: {initial_subject}", raw_output.strip()
    except Exception as ex:
        logger.warning(f"Groq follow-up generation failed: {ex}")

    return _generate_rule_based_followup(initial_subject, initial_body, step_num, angle_type, lead_name, company_name)


# ---------------------------------------------------------------------------
# Dialog: Add Follow-up Sequence Step
# ---------------------------------------------------------------------------
@st.dialog("➕ Add Follow-up Sequence Step", width="large")
def render_add_followup_dialog(
    initial_subject: str,
    initial_body: str,
    current_followups_count: int,
    sample_lead: Optional[Dict[str, Any]] = None
):
    step_num = current_followups_count + 1
    default_delay = step_num * 3
    lead_name = (sample_lead.get("name") if sample_lead else "") or "{first_name}"
    company_name = (sample_lead.get("company") if sample_lead else "") or "[Company]"

    st.markdown(
        f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-size:13px;'>"
        f"<b>Adding Follow-up #{step_num}</b> &nbsp;·&nbsp; "
        f"Recipient: <b>{html.escape(lead_name)}</b> ({html.escape(company_name)}) &nbsp;·&nbsp; "
        f"Initial Subject: <i>{html.escape(initial_subject or 'No Subject')}</i>"
        f"</div>",
        unsafe_allow_html=True
    )

    tab_ai, tab_custom, tab_tpl = st.tabs([
        "🤖 Generate via AI / Bot (API)",
        "✏️ Write Custom Follow-up",
        "📋 Load from Saved Template"
    ])

    with tab_ai:
        st.markdown(
            "<div style='font-size:12px; color:#475569; margin-bottom:8px;'>"
            "Generates context-aware follow-up copy tailored to your initial message, matching the language, tone, and offer."
            "</div>",
            unsafe_allow_html=True
        )

        col_ang, col_del = st.columns([2.5, 1.5], vertical_alignment="center")
        with col_ang:
            chosen_angle = st.selectbox(
                "Follow-up Strategy / Angle",
                [
                    "📌 Quick Polite Nudge (Check in on previous note without pressure)",
                    "💡 Value-Add & Insight (Offer a relevant tip or case study for company)",
                    "🤝 Alternative Contact (Ask who on the team handles this)",
                    "🚪 Soft Breakup / Final Note (Politely close the loop)",
                    "✏️ Custom Prompt Guidance"
                ],
                key=f"dlg_ai_angle_{step_num}"
            )
        with col_del:
            ai_delay = st.slider(
                "Delay (Days after touch 1):",
                min_value=1,
                max_value=30,
                value=default_delay,
                key=f"dlg_ai_delay_{step_num}"
            )

        custom_prompt = ""
        if "Custom" in chosen_angle:
            custom_prompt = st.text_input(
                "Custom AI Instructions",
                placeholder="e.g. Mention that our onboarding closes on Friday",
                key=f"dlg_ai_custom_{step_num}"
            )

        configured_key = get_config("groq_api_key", "")
        if not configured_key and "GROQ_API_KEY" in st.secrets:
            configured_key = st.secrets["GROQ_API_KEY"]
        if not configured_key:
            import os
            configured_key = os.environ.get("GROQ_API_KEY", "")

        c_gen_btn, c_gen_info = st.columns([1.5, 2.5], vertical_alignment="center")
        with c_gen_btn:
            gen_clicked = st.button("⚡ Generate Follow-up", type="secondary", use_container_width=True, key=f"dlg_btn_trigger_gen_{step_num}")
        with c_gen_info:
            if configured_key:
                st.caption("⚡ Powered by Groq API (High-speed multimodal AI)")
            else:
                st.caption("🤖 Powered by Built-in Outreach Bot (Deterministic smart rules)")

        subj_state_key = f"dlg_gen_subj_{step_num}"
        body_state_key = f"dlg_gen_body_{step_num}"

        if gen_clicked:
            with st.spinner("Generating follow-up copy..."):
                if configured_key:
                    g_subj, g_body = _generate_api_followup(
                        api_key=configured_key,
                        initial_subject=initial_subject,
                        initial_body=initial_body,
                        step_num=step_num,
                        angle_type=chosen_angle,
                        custom_guidance=custom_prompt,
                        lead_name=lead_name,
                        company_name=company_name
                    )
                else:
                    g_subj, g_body = _generate_rule_based_followup(
                        initial_subject=initial_subject,
                        initial_body=initial_body,
                        step_num=step_num,
                        angle_type=chosen_angle,
                        lead_name=lead_name,
                        company_name=company_name
                    )
                st.session_state[subj_state_key] = g_subj
                st.session_state[body_state_key] = g_body
                trigger_toast(f"Follow-up #{step_num} generated!", icon="⚡")

        if subj_state_key not in st.session_state:
            def_s, def_b = _generate_rule_based_followup(initial_subject, initial_body, step_num, chosen_angle, lead_name, company_name)
            st.session_state[subj_state_key] = def_s
            st.session_state[body_state_key] = def_b

        st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)
        edit_subj = st.text_input("Follow-up Subject Line", value=st.session_state[subj_state_key], key=f"dlg_ai_res_subj_{step_num}")
        edit_body = st.text_area("Follow-up Body Copy", value=st.session_state[body_state_key], height=130, key=f"dlg_ai_res_body_{step_num}")
        ai_inc_sig = st.checkbox(f"🖋️ Include signature in follow-up #{step_num}", value=False, key=f"dlg_ai_sig_{step_num}")

        if st.button(f"➕ Add Follow-up #{step_num} to Sequence", type="primary", use_container_width=True, key=f"dlg_ai_add_step_{step_num}"):
            st.session_state["compose_followups"].append({
                "delay_days": ai_delay,
                "subject": edit_subj.strip(),
                "body": edit_body.strip(),
                "include_signature": ai_inc_sig
            })
            st.session_state.pop(subj_state_key, None)
            st.session_state.pop(body_state_key, None)
            trigger_toast(f"Follow-up #{step_num} added (+{ai_delay}d)!", icon="↩️")
            st.rerun()

    with tab_custom:
        st.markdown(
            "<div style='font-size:12px; color:#475569; margin-bottom:8px;'>"
            "Write custom follow-up copy from scratch and choose your delay timing."
            "</div>",
            unsafe_allow_html=True
        )
        c_delay = st.slider(
            "Delay (Days after touch 1):",
            min_value=1,
            max_value=30,
            value=default_delay,
            key=f"dlg_c_delay_{step_num}"
        )
        def_c_subj = f"Re: {initial_subject}" if initial_subject and not initial_subject.lower().startswith("re:") else (initial_subject or "Re: Quick question")
        c_subj = st.text_input("Follow-up Subject Line", value=def_c_subj, key=f"dlg_c_subj_{step_num}")
        def_c_body = f"Hi {lead_name},\n\nJust following up on my previous note to see if you had a chance to review it."
        c_body = st.text_area("Follow-up Body", value=def_c_body, height=130, key=f"dlg_c_body_{step_num}")
        c_inc_sig = st.checkbox(f"🖋️ Include signature in follow-up #{step_num}", value=False, key=f"dlg_c_sig_{step_num}")

        if st.button(f"➕ Add Custom Follow-up #{step_num}", type="primary", use_container_width=True, key=f"dlg_c_add_step_{step_num}"):
            st.session_state["compose_followups"].append({
                "delay_days": c_delay,
                "subject": c_subj.strip(),
                "body": c_body.strip(),
                "include_signature": c_inc_sig
            })
            trigger_toast(f"Custom follow-up #{step_num} added (+{c_delay}d)!", icon="↩️")
            st.rerun()

    with tab_tpl:
        st.markdown(
            "<div style='font-size:12px; color:#475569; margin-bottom:8px;'>"
            "Load any saved outreach template to use as this follow-up step."
            "</div>",
            unsafe_allow_html=True
        )
        all_tpls = get_templates(active_only=True)
        if not all_tpls:
            st.info("No saved templates found. Create templates in the Templates screen.")
        else:
            tpl_map = {f"{t.get('name') or t.get('template_name', 'Template')} (ID #{t['id']})": t for t in all_tpls}
            chosen_tpl_lbl = st.selectbox("Select Template", list(tpl_map.keys()), key=f"dlg_tpl_pick_{step_num}")
            chosen_tpl = tpl_map[chosen_tpl_lbl]

            t_delay = st.slider(
                "Delay (Days after touch 1):",
                min_value=1,
                max_value=30,
                value=default_delay,
                key=f"dlg_t_delay_{step_num}"
            )
            tpl_subj_init = chosen_tpl.get("subject") or f"Re: {initial_subject}"
            t_subj = st.text_input("Follow-up Subject Line", value=tpl_subj_init, key=f"dlg_tpl_subj_{step_num}")
            from ui.editor import html_to_visual_text
            clean_tpl_body, _ = html_to_visual_text(chosen_tpl.get("body_html") or chosen_tpl.get("body_content") or "")
            t_body = st.text_area("Template Body", value=clean_tpl_body, height=130, key=f"dlg_tpl_body_{step_num}")
            t_inc_sig = st.checkbox(f"🖋️ Include signature in follow-up #{step_num}", value=False, key=f"dlg_tpl_sig_{step_num}")

            if st.button(f"➕ Add Template as Follow-up #{step_num}", type="primary", use_container_width=True, key=f"dlg_tpl_add_step_{step_num}"):
                st.session_state["compose_followups"].append({
                    "delay_days": t_delay,
                    "subject": t_subj.strip(),
                    "body": t_body.strip(),
                    "include_signature": t_inc_sig
                })
                trigger_toast(f"Template added as follow-up #{step_num} (+{t_delay}d)!", icon="↩️")
                st.rerun()


# ---------------------------------------------------------------------------
# Dialog: Confirm Outreach & Schedule Sequence (UTC+5)
# ---------------------------------------------------------------------------

@st.dialog("🚀 Confirm Outreach & Schedule Sequence (UTC+5)", width="large")
def render_compose_schedule_dialog(

    mode: str,
    current_lead: Dict[str, Any],
    selected_mb: Dict[str, Any],
    final_subj: str,
    final_body: str,
    followup_steps: List[Dict[str, Any]],
    bcc_email: str = "",
    include_signature: bool = True,
):
    """Modal popup allowing exact custom date and time setting for initial email and each follow-up separately."""
    bcc_email = (bcc_email or "").strip()

    recipient_clean = (current_lead.get("email") or "").strip()
    recipient_name  = current_lead.get("name") or recipient_clean
    lead_tz         = current_lead.get("country_or_timezone") or "LOCAL"
    now_engine      = get_engine_now()

    recip_parts = [e.strip() for e in re.split(r'[,;]+', recipient_clean) if e.strip()]
    if len(recip_parts) > 1:
        recip_display_html = f"<b>Recipients ({len(recip_parts)}):</b> <span style='font-family:monospace; color:#083731;'>{html.escape(recipient_clean)}</span>"
    else:
        recip_display_html = f"<b>Recipient:</b> {html.escape(recipient_name)} &lt;{html.escape(recipient_clean)}&gt;"

    bcc_badge_html = f"<br><b>BCC:</b> <span style='font-family:monospace; color:#083731;'>{html.escape(bcc_email)}</span>" if bcc_email.strip() else ""
    st.markdown(
        f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px 12px; margin-bottom:12px; font-size:13px;'>"
        f"{recip_display_html}<br>"
        f"<b>Sending Mailbox:</b> {html.escape(selected_mb.get('email', ''))}"
        f"{bcc_badge_html}"
        f"</div>",
        unsafe_allow_html=True
    )

    # ── Section 1: Initial Email ──
    st.markdown("##### 📧 Initial Email")
    st.markdown(f"**Subject:** *{html.escape(final_subj)}*")

    if mode == "send_now":
        init_choice = st.radio(
            "Initial Email Dispatch",
            ["🚀 Send immediately right now", "🕒 Set custom date & time (UTC+5)"],
            horizontal=True,
            key="comp_dlg_init_choice"
        )
        if init_choice.startswith("🕒"):
            c1, c2 = st.columns(2)
            with c1:
                init_date = st.date_input("Initial Date (UTC+5)", value=now_engine.date(), min_value=now_engine.date(), key="comp_dlg_init_d")
            with c2:
                init_time = st.time_input("Initial Time (UTC+5)", value=(now_engine + timedelta(minutes=15)).time(), step=60, key="comp_dlg_init_t")
            init_dt = datetime.combine(init_date, init_time)
        else:
            init_dt = None
    else:
        st.markdown("<span class='lbl'>Scheduled Date &amp; Time (UTC+5)</span>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            init_date = st.date_input("Scheduled Date", value=now_engine.date(), min_value=now_engine.date(), key="comp_dlg_sched_d", label_visibility="collapsed")
        with c2:
            init_time = st.time_input("Scheduled Time", value=(now_engine + timedelta(hours=1)).time(), step=60, key="comp_dlg_sched_t", label_visibility="collapsed")
        init_dt = datetime.combine(init_date, init_time)

    # ── Section 2: Follow-up Emails (Set each date and time separately) ──
    fu_dts = []
    if followup_steps:
        st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:14px 0 10px;'>", unsafe_allow_html=True)
        st.markdown(f"##### ↩️ Follow-Up Sequence ({len(followup_steps)} step{'s' if len(followup_steps) != 1 else ''})")
        st.caption("Set the exact date and time for each follow-up email separately:")

        ref_base = init_dt if init_dt else now_engine

        for idx, fu in enumerate(followup_steps):
            with st.container(border=True):
                st.markdown(f"**Follow-Up #{idx + 1}:** *{html.escape(fu['subject'])}*")
                default_fu_dt = ref_base + timedelta(days=fu.get("delay_days", (idx + 1) * 3))
                f_c1, f_c2 = st.columns(2)
                with f_c1:
                    f_date = st.date_input(
                        f"Date for Follow-up #{idx + 1}",
                        value=default_fu_dt.date(),
                        min_value=now_engine.date(),
                        key=f"comp_dlg_fu_d_{idx}"
                    )
                with f_c2:
                    f_time = st.time_input(
                        f"Time (UTC+5) for Follow-up #{idx + 1}",
                        value=default_fu_dt.time(),
                        step=60,
                        key=f"comp_dlg_fu_t_{idx}"
                    )
                target_fu_dt = datetime.combine(f_date, f_time)
                fu_dts.append(target_fu_dt)
                st.caption(f"📅 Scheduled to dispatch: **{target_fu_dt.strftime('%a %b %d, %Y at %H:%M')} (UTC+5)**")

    # ── Confirmation & Cancel/Draft Actions ──
    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)
    if mode == "send_now" and init_dt is None:
        confirm_label = "🚀 Send Now" if not followup_steps else f"🚀 Send Now (+{len(followup_steps)} FU)"
    else:
        confirm_label = "🕒 Confirm Scheduled Sequence"

    def _trigger_compose_submit():
        st.session_state["_comp_dlg_do_submit"] = True

    def _trigger_compose_draft():
        st.session_state["_comp_dlg_do_draft"] = True

    col_act_main, col_act_draft, col_act_disc = st.columns([2.5, 1.4, 1.1], vertical_alignment="center")
    with col_act_main:
        submit_clicked = st.button(
            confirm_label,
            type="primary",
            use_container_width=True,
            key="comp_dlg_confirm_cta",
            on_click=_trigger_compose_submit
        )
    with col_act_draft:
        save_draft_clicked = st.button(
            "💾 Save Draft",
            use_container_width=True,
            key="comp_dlg_save_draft_cta",
            help="Save this message to Outbox as Pending draft",
            on_click=_trigger_compose_draft
        )
    with col_act_disc:
        discard_clicked = st.button("🗑️ Discard", use_container_width=True, key="comp_dlg_discard_cta", help="Discard outreach and close popup")

    if discard_clicked:
        trigger_toast("Outreach dismissed without sending.", icon="ℹ️")
        st.rerun()

    sig_html = get_config("signature_html", "") or "Jack Connor · Sellomize · sales@sellomize.com"
    full_initial_body = deduplicate_email_signature(
        final_body,
        signature_html=sig_html,
        include_signature=include_signature
    )
    initial_email_html = format_email_html(full_initial_body)

    do_save_draft = save_draft_clicked or st.session_state.pop("_comp_dlg_do_draft", False)
    do_submit = submit_clicked or st.session_state.pop("_comp_dlg_do_submit", False)

    if do_save_draft:
        create_email(
            email_html=initial_email_html,
            subject=final_subj,
            recipient=recipient_clean,
            status="Pending",
            scheduled_time=get_engine_now_str(),
            target_timezone=lead_tz,
            bcc_email=bcc_email
        )
        trigger_toast("Saved as draft in Outbox.", icon="💾")
        st.session_state["active_screen"] = "outbox"
        st.session_state["main_app_tabs"] = "📥 Outbox"
        st.rerun()

    if do_submit:
        c_status = (current_lead.get("status") or "")
        if not c_status and recipient_clean:
            c_db = get_contact_by_email(recipient_clean)
            if c_db:
                c_status = c_db.get("status") or ""
        if c_status.lower() in ["do not contact", "unsubscribed"]:
            st.error("🚫 Legal Compliance Block: This recipient is marked as 'Do Not Contact' (Opt-Out). Cannot dispatch outreach to suppressed contacts.")
            return

        with st.spinner("Processing outreach..."):
            # 1. Initial Email
            if init_dt is None:
                email_id = create_email(
                    email_html=initial_email_html,
                    subject=final_subj,
                    recipient=recipient_clean,
                    status="Approved",
                    scheduled_time=get_engine_now_str(),
                    target_timezone=lead_tz,
                    bcc_email=bcc_email
                )
                try:
                    ok = dispatch_email_hostinger({
                        "id": email_id,
                        "recipient": recipient_clean,
                        "subject": final_subj,
                        "email_html": initial_email_html,
                        "smtp_account_id": selected_mb["id"],
                        "target_timezone": lead_tz,
                        "bcc_email": bcc_email
                    })
                except Exception as ex:
                    logger.error(f"Dispatch error in compose dialog: {ex}")
                    mark_email_error(email_id, status="Error", error_message=str(ex))
                    ok = False
            else:
                sched_str = init_dt.strftime("%Y-%m-%d %H:%M:%S")
                create_email(
                    email_html=initial_email_html,
                    subject=final_subj,
                    recipient=recipient_clean,
                    status="Approved",
                    scheduled_time=sched_str,
                    target_timezone=lead_tz,
                    bcc_email=bcc_email
                )
                ok = True

            # 2. Follow-ups with individually customized timing and signature option
            for idx, fu in enumerate(followup_steps):
                fu_target_dt = fu_dts[idx]
                fu_sched_str = fu_target_dt.strftime("%Y-%m-%d %H:%M:%S")
                fu_subj_res  = inject_variables(parse_spintax(fu["subject"]), current_lead)
                fu_body_raw  = fu["body"].replace("\n\n", "</p><p>").replace("\n", "<br>")
                fu_body_res  = resolve_template(f"<p>{fu_body_raw}</p>", current_lead)

                # Attach or strip signature based on user setting for this follow-up step
                fu_body_res = deduplicate_email_signature(
                    fu_body_res,
                    signature_html=sig_html,
                    include_signature=fu.get("include_signature", False)
                )

                create_email(
                    email_html=format_email_html(fu_body_res),
                    subject=fu_subj_res,
                    recipient=recipient_clean,
                    status="Approved",
                    scheduled_time=fu_sched_str,
                    target_timezone=lead_tz,
                    bcc_email=bcc_email,
                    sequence_step=idx + 2,
                    variation_num=idx + 2
                )

            # Update lead outreach dates in CRM database
            c_match = get_contact_by_email(recipient_clean)
            if c_match:
                c_updates = {}
                first_date = init_dt.strftime("%Y-%m-%d") if init_dt else get_engine_now().strftime("%Y-%m-%d")
                if not c_match.get("date_first_emailed"):
                    c_updates["date_first_emailed"] = first_date
                if init_dt is None:
                    c_updates["last_contact_date"] = get_engine_now().strftime("%Y-%m-%d")
                    c_updates["contacted"] = "Yes"
                    if (c_match.get("status") or "").lower() not in ["opened", "replied", "bounced", "do not contact"]:
                        c_updates["status"] = "Emailed"
                if fu_dts:
                    c_updates["next_follow_up"] = fu_dts[0].strftime("%Y-%m-%d")
                if c_updates:
                    update_contact(c_match["id"], **c_updates)

            # Auto-clean Compose fields, recipient, and uploaded media after sending
            st.session_state["compose_followups"] = []
            st.session_state["compose_subject"] = ""
            st.session_state["compose_body_html"] = ""
            st.session_state["compose_visual_textarea"] = ""
            st.session_state["compose_last_synced_html"] = ""
            st.session_state.pop("compose_img_up", None)
            st.session_state.pop("comp_subj_in", None)
            st.session_state.pop("compose_custom_email_input", None)
            st.session_state.pop("compose_selected_lead_id", None)
            st.session_state.pop("comp_lead_pick", None)
            st.session_state.pop("comp_custom_email_in", None)
            st.session_state.pop("comp_recipient_email", None)
            st.session_state.pop("compose_to_text", None)

            if init_dt is None:
                if ok:
                    msg = f"Sent initial email to {recipient_clean}!"
                    if followup_steps:
                        msg += f" Scheduled {len(followup_steps)} follow-up(s) with custom timing."
                    trigger_toast(msg, icon="🚀")
                else:
                    trigger_toast(f"Email #{email_id} queued to Outbox with send error. Check mailbox connection in Settings.", icon="⚠️")
            else:
                msg = f"Scheduled initial email for {init_dt.strftime('%b %d at %H:%M')}!"
                if followup_steps:
                    msg += f" Along with {len(followup_steps)} follow-up(s)."
                trigger_toast(msg, icon="🚀")
            st.session_state["active_screen"] = "outbox"
            st.session_state["main_app_tabs"] = "📥 Outbox"
            st.rerun()


# ---------------------------------------------------------------------------
# Main compose tab
# ---------------------------------------------------------------------------

def render_compose_tab(contacts=None, templates=None):
    """Render the Compose screen matching sellomize_reference.html."""
    if contacts is None:
        contacts = get_contacts()
    if templates is None:
        templates = get_templates()

    smtp_accounts = get_smtp_accounts(active_only=True)

    # Ensure fresh compose starts with an EMPTY recipient unless an explicit prefill was passed
    if "compose_active_session" not in st.session_state:
        st.session_state["compose_active_session"] = True
        st.session_state.pop("compose_selected_lead_id", None)
        st.session_state.pop("comp_lead_pick", None)
        st.session_state["comp_recipient_email"] = ""

    # Prefill from Leads tab, Outbox, or prior screen
    prefill_lead_id = st.session_state.pop("prefill_compose_lead_id", None)
    if prefill_lead_id:
        st.session_state["compose_selected_lead_id"] = prefill_lead_id
        for c in (contacts or []):
            if c.get("id") == prefill_lead_id:
                st.session_state["comp_recipient_email"] = c.get("email", "")
                break

    # Handle incoming compose_recipient (e.g. from Outbox follow-up or takeover)
    incoming_recipient = st.session_state.pop("compose_recipient", None)
    if incoming_recipient:
        inc_clean = str(incoming_recipient).strip()
        matched_lead_for_inc = get_contact_by_email(inc_clean)
        if matched_lead_for_inc:
            st.session_state["compose_selected_lead_id"] = matched_lead_for_inc["id"]
        st.session_state["comp_recipient_email"] = inc_clean

    # Default body / subject
    if "compose_body_html" not in st.session_state:
        st.session_state["compose_body_html"] = (
            "Hi {first_name},\n\n"
            "We haven't been properly introduced, but I was looking through [Company] on Amazon and noticed a number of listings showing currently unavailable.\n\n"
            "When a customer searches and finds it unavailable, the sale simply stops there. I'd be glad to take a look together."
        )
    if "compose_subject" not in st.session_state:
        st.session_state["compose_subject"] = "[Company] + Amazon"

    # Follow-up sequence state in Compose (same row tabs)
    if "compose_followups" not in st.session_state:
        st.session_state["compose_followups"] = []

    col_editor, col_preview = st.columns([1.1, 0.9], gap="large")

    with col_editor:
        # =====================================================================
        # ROW 1 — Mailbox + Recipient + BCC toggle
        # =====================================================================
        c_mb, c_rcpt = st.columns(2)

        with c_mb:
            st.markdown('<span class="lbl">Sending mailbox</span>', unsafe_allow_html=True)
            if not smtp_accounts:
                st.warning("No active Hostinger mailboxes. Configure in Settings.")
                selected_mb = None
            else:
                mb_choices = {
                    f"{a.get('sender_name') or 'Mailbox'} <{a['email']}>": a
                    for a in smtp_accounts
                }
                sel_mb_label = st.selectbox(
                    "Mailbox", list(mb_choices.keys()),
                    label_visibility="collapsed", key="comp_mb_pick"
                )
                selected_mb = mb_choices[sel_mb_label]

        with c_rcpt:
            st.markdown('<span class="lbl">To (Lead or direct address)</span>', unsafe_allow_html=True)
            global_bcc = get_config("bcc_email", "") or ""
            if "compose_show_bcc" not in st.session_state:
                st.session_state["compose_show_bcc"] = False
            if "compose_bcc_email" not in st.session_state:
                st.session_state["compose_bcc_email"] = global_bcc

            show_bcc = st.session_state["compose_show_bcc"]
            active_bcc = st.session_state.get("compose_bcc_email", "").strip()
            bcc_btn_label = "📬 BCC (Active)" if (show_bcc and active_bcc) else ("− Hide BCC" if show_bcc else "+ Add BCC")

            col_rcpt_b1, col_rcpt_b2 = st.columns([1.1, 0.9])
            with col_rcpt_b1:
                if st.button("✖ Clear To", key="comp_clear_to_btn", use_container_width=True, help="Reset recipient field"):
                    st.session_state.pop("compose_selected_lead_id", None)
                    st.session_state.pop("comp_lead_pick", None)
                    st.session_state["comp_recipient_email"] = ""
                    st.rerun()
            with col_rcpt_b2:
                if st.button(bcc_btn_label, key="comp_bcc_toggle", use_container_width=True):
                    st.session_state["compose_show_bcc"] = not show_bcc
                    st.rerun()

        # Build lead list for CRM selector
        EMPTY_LEAD_PLACEHOLDER = "— Pick from CRM leads (or type below) —"
        lead_choices: Dict[str, Any] = {EMPTY_LEAD_PLACEHOLDER: None}
        for c in (contacts or []):
            label = (
                f"{c.get('name') or 'Lead'} <{c.get('email')}>"
                + (f" — {c.get('company')}" if c.get("company") else "")
            )
            lead_choices[label] = c

        def _on_crm_select():
            picked_lbl = st.session_state.get("comp_lead_pick")
            matched_obj = lead_choices.get(picked_lbl)
            if matched_obj:
                st.session_state["compose_selected_lead_id"] = matched_obj.get("id")
                st.session_state["comp_recipient_email"] = matched_obj.get("email", "")
            elif picked_lbl == EMPTY_LEAD_PLACEHOLDER:
                st.session_state.pop("compose_selected_lead_id", None)

        preselected_idx = 0
        target_prefill_id = st.session_state.get("compose_selected_lead_id")
        lead_choice_keys = list(lead_choices.keys())
        if target_prefill_id:
            for idx, (lbl, l_obj) in enumerate(lead_choices.items()):
                if l_obj and l_obj.get("id") == target_prefill_id:
                    preselected_idx = idx
                    break

        col_to_crm, col_to_in = st.columns([1.2, 1.8], gap="small")
        with col_to_crm:
            st.selectbox(
                "CRM Leads",
                lead_choice_keys,
                index=preselected_idx if preselected_idx < len(lead_choice_keys) else 0,
                label_visibility="collapsed",
                key="comp_lead_pick",
                on_change=_on_crm_select
            )
        with col_to_in:
            if "comp_recipient_email" not in st.session_state:
                st.session_state["comp_recipient_email"] = ""
            recipient_email_val = st.text_input(
                "To Email",
                placeholder="e.g. partner@brand.com (or select from CRM on left)",
                label_visibility="collapsed",
                key="comp_recipient_email"
            )

        if st.session_state.get("compose_show_bcc"):
            st.markdown(
                '<div style="display:flex; justify-content:space-between; align-items:center; margin-top:6px; margin-bottom:2px;">'
                '<span class="lbl" style="font-size:12px; font-weight:600; color:#083731;">📬 BCC (comma-separated — 2 or more addresses supported)</span>'
                '</div>',
                unsafe_allow_html=True
            )
            comp_bcc_val = st.text_input(
                "BCC recipients",
                value=st.session_state.get("compose_bcc_email", ""),
                placeholder="e.g. audit@sellomize.com, crm-sync@hubspot.com",
                label_visibility="collapsed",
                key="comp_bcc_input_field"
            )
            st.session_state["compose_bcc_email"] = comp_bcc_val

        # Resolve active recipient
        custom_email = (st.session_state.get("comp_recipient_email") or "").strip()
        chosen_lead_obj = None
        target_lead_id = st.session_state.get("compose_selected_lead_id")
        if target_lead_id and contacts:
            for c in contacts:
                if c.get("id") == target_lead_id:
                    chosen_lead_obj = c
                    break

        # Resolve active recipient
        if custom_email.strip():
            # Check for multiple comma-separated addresses
            email_parts = [e.strip() for e in re.split(r'[,;]+', custom_email.strip()) if e.strip()]
            first_addr = email_parts[0] if email_parts else custom_email.strip()
            first_clean = first_addr
            if "<" in first_addr and ">" in first_addr:
                m_a = re.search(r'<([^>]+)>', first_addr)
                if m_a:
                    first_clean = m_a.group(1).strip()

            matched = get_contact_by_email(first_clean)
            if matched and len(email_parts) == 1:
                current_lead = matched
            elif matched:
                # Retain matched CRM context but preserve all recipient addresses
                current_lead = dict(matched)
                current_lead["email"] = custom_email.strip()
            else:
                current_lead = {
                    "id":                   None,
                    "name":                 first_clean.split("@")[0].capitalize(),
                    "email":                custom_email.strip(),
                    "company":              "",
                    "country_or_timezone":  "LOCAL",
                }
        elif chosen_lead_obj:
            current_lead = chosen_lead_obj
        else:
            current_lead = {
                "name":    "",
                "email":   "",
                "company": "",
                "country_or_timezone": "LOCAL",
            }

        # =====================================================================
        # SEQUENCE TABS: Initial Email + Follow-ups in the SAME ROW
        # =====================================================================
        st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:14px 0 10px;'>", unsafe_allow_html=True)
        lead_id_str = get_lead_identifier(current_lead)
        lead_display_name = current_lead.get("name") or current_lead.get("company") or "Lead"
        try:
            saved_lead_imgs = get_lead_images(lead_id_str)
        except Exception:
            saved_lead_imgs = []

        num_fu = len(st.session_state["compose_followups"])
        can_add_fu = num_fu < 3

        if num_fu > 0:
            seq_hdr1, seq_hdr_img, seq_hdr2, seq_hdr3 = st.columns([1.6, 1.4, 1.4, 0.8], vertical_alignment="center")
        else:
            seq_hdr1, seq_hdr_img, seq_hdr2 = st.columns([1.8, 1.6, 1.4], vertical_alignment="center")

        with seq_hdr1:
            st.markdown("<span class='lbl' style='font-size:13px; font-weight:700;'>Message &amp; Sequence</span>", unsafe_allow_html=True)

        with seq_hdr_img:
            img_badge = f" ({len(saved_lead_imgs)})" if saved_lead_imgs else ""
            with st.popover(f"🖼️ Lead Library{img_badge}", help=f"Lead Image Library for {lead_display_name} ({lead_id_str})", use_container_width=True):
                st.markdown(f"<div style='font-size:13px; font-weight:700; color:#083731; margin-bottom:2px;'>🖼️ Lead Image Library</div>", unsafe_allow_html=True)
                st.caption(f"Lead ID: **{lead_id_str}** · Saved screenshots for this lead.")

                up_lead_file = st.file_uploader(
                    "Upload to Lead Library",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=f"comp_lead_img_up_{lead_id_str}",
                    label_visibility="collapsed"
                )
                if up_lead_file:
                    up_lead_file.seek(0)
                    lead_raw = up_lead_file.read()
                    if lead_raw:
                        lead_file_sig = f"{up_lead_file.name}_{len(lead_raw)}"
                        lead_last_sig_key = f"comp_last_lead_sig_{lead_id_str}"
                        if st.session_state.get(lead_last_sig_key) != lead_file_sig:
                            st.session_state[lead_last_sig_key] = lead_file_sig
                            proc_img = process_and_store_image(
                                lead_raw,
                                mime_type=up_lead_file.type or "image/png",
                                lead_id=lead_id_str,
                                original_filename=up_lead_file.name
                            )
                            # Save to Lead Library only - do NOT auto-insert at pos 0
                            trigger_toast(f"Saved '{up_lead_file.name}' to Lead Library! Click 'Append' or 'Insert' below.", icon="🖼️")
                            st.rerun()

                st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:8px 0;'>", unsafe_allow_html=True)

                if not saved_lead_imgs:
                    st.info("No saved images for this lead yet. Paste (Ctrl+V) directly in the editor or upload above!")
                else:
                    for img_rec in saved_lead_imgs:
                        with st.container(border=True):
                            g_c1, g_c2, g_c3 = st.columns([1.2, 2.2, 1.4], vertical_alignment="center")
                            s_key = img_rec.get("storage_key", "")
                            full_fpath = os.path.join("assets/uploads", s_key) if not os.path.isabs(s_key) else s_key
                            with g_c1:
                                if os.path.exists(full_fpath):
                                    try:
                                        with open(full_fpath, "rb") as fp_prev:
                                            b64_p = base64.b64encode(fp_prev.read()).decode("utf-8")
                                        st.markdown(f'<img src="data:{img_rec.get("mime_type","image/jpeg")};base64,{b64_p}" style="max-height:44px; max-width:75px; object-fit:cover; border-radius:4px; border:1px solid #CBD5E1;" />', unsafe_allow_html=True)
                                    except Exception:
                                        st.caption("🖼️")
                                else:
                                    st.caption("🖼️")
                            with g_c2:
                                f_name = img_rec.get("filename") or f"image_{img_rec['id'][:6]}"
                                st.markdown(f"<div style='font-size:11px; font-weight:600; line-height:1.2; word-break:break-all;'>{html.escape(f_name)}</div>", unsafe_allow_html=True)
                                st.caption(f"{img_rec.get('width', 0)}x{img_rec.get('height', 0)}px · {round(img_rec.get('bytes', 0)/1024, 1)} KB")
                            with g_c3:
                                if st.button("➕ Append", key=f"comp_app_btn_{img_rec['id']}", use_container_width=True, type="primary", help="Append safely at end of email body"):
                                    insert_lead_image_into_editor(img_rec, editor_key="compose_rich_editor", append_mode=True)
                                    trigger_toast(f"Appended image to email body!", icon="🖼️")
                                    st.rerun()
                                if st.button("➕ Insert", key=f"comp_ins_btn_{img_rec['id']}", use_container_width=True, help="Insert at cursor in email body"):
                                    insert_lead_image_into_editor(img_rec, editor_key="compose_rich_editor")
                                    trigger_toast(f"Inserted image at cursor!", icon="🖼️")
                                    st.rerun()
                                if st.button("🗑️", key=f"comp_del_img_{img_rec['id']}", use_container_width=True, help="Delete from lead library"):
                                    delete_lead_image(img_rec['id'])
                                    if os.path.exists(full_fpath):
                                        try:
                                            os.remove(full_fpath)
                                        except Exception:
                                            pass
                                    trigger_toast("Image deleted from library.", icon="🗑️")
                                    st.rerun()

        with seq_hdr2:
            fu_btn_text = f"➕ Add follow-up ({num_fu}/3)" if num_fu > 0 else "➕ Add follow-up"
            if st.button(
                fu_btn_text,
                key="comp_add_fu_btn_main",
                use_container_width=True,
                disabled=not can_add_fu,
                help="Add another follow-up step via AI, custom text, or template"
            ):
                render_add_followup_dialog(
                    initial_subject=st.session_state.get("compose_subject", ""),
                    initial_body=st.session_state.get("compose_body_html", ""),
                    current_followups_count=num_fu,
                    sample_lead=chosen_lead_obj
                )

        if num_fu > 0:
            with seq_hdr3:
                if st.button("↩️ Undo", key="comp_undo_fu_btn", use_container_width=True, help="Undo / remove last added follow-up"):
                    st.session_state["compose_followups"].pop()
                    st.rerun()

        # Build tabs
        tab_titles = ["📧 Initial Email"] + [
            f"↩️ Follow-up #{i+1} (+{fu['delay_days']}d)"
            for i, fu in enumerate(st.session_state["compose_followups"])
        ]
        tabs = st.tabs(tab_titles)

        # --- Tab 0: Initial Email ---
        with tabs[0]:
            st.markdown('<span class="lbl">Subject</span>', unsafe_allow_html=True)
            subj_val = st.text_input(
                "Subject",
                value=st.session_state.get("compose_subject", ""),
                label_visibility="collapsed",
                key="comp_subj_in"
            )
            st.session_state["compose_subject"] = subj_val

            st.markdown('<span class="lbl">Body</span>', unsafe_allow_html=True)
            if is_rich_editor_enabled():
                current_body = render_rich_editor(
                    initial_html=st.session_state.get("compose_body_html", ""),
                    key="compose_rich_editor",
                    height=200,
                    owner_type="compose",
                    lead_id=lead_id_str
                )
            else:
                current_body = render_dual_mode_editor(
                    key_prefix="compose",
                    initial_content=st.session_state.get("compose_body_html", ""),
                    height=180
                )
            st.session_state["compose_body_html"] = current_body

            if "compose_include_sig" not in st.session_state:
                st.session_state["compose_include_sig"] = (get_config("signature_enabled_default", "true") == "true")
            st.session_state["compose_include_sig"] = st.checkbox(
                "🖋️ Include bottom signature in this email",
                value=st.session_state["compose_include_sig"],
                key="comp_init_sig_chk",
                help="Attach your saved corporate signature at the bottom of the email"
            )

        # --- Tab 1+: Follow-up steps ---
        for idx, fu in enumerate(st.session_state["compose_followups"]):
            with tabs[idx + 1]:
                fu_top1, fu_top2 = st.columns([3.0, 2.0], vertical_alignment="center")
                with fu_top1:
                    fu["delay_days"] = st.slider(
                        f"Send follow-up #{idx+1} days after initial email:",
                        min_value=1,
                        max_value=30,
                        value=fu["delay_days"],
                        key=f"comp_fu_{idx}_delay_slider"
                    )
                with fu_top2:
                    if st.button("🗑️ Remove follow-up", key=f"comp_fu_del_{idx}", use_container_width=True):
                        st.session_state["compose_followups"].pop(idx)
                        st.rerun()

                st.markdown('<span class="lbl">Follow-up Subject</span>', unsafe_allow_html=True)
                fu["subject"] = st.text_input(
                    "Follow-up Subject",
                    value=fu["subject"],
                    key=f"comp_fu_{idx}_subj_in",
                    label_visibility="collapsed"
                )

                st.markdown('<span class="lbl">Follow-up Body</span>', unsafe_allow_html=True)
                if is_rich_editor_enabled():
                    fu["body"] = render_rich_editor(
                        initial_html=fu.get("body", ""),
                        key=f"comp_fu_{idx}_rich_editor",
                        height=160,
                        owner_type="compose_followup",
                        lead_id=lead_id_str
                    )
                else:
                    fu["body"] = st.text_area(
                        "Follow-up Body",
                        value=fu["body"],
                        height=140,
                        key=f"comp_fu_{idx}_body_in",
                        label_visibility="collapsed"
                    )

                if "include_signature" not in fu:
                    fu["include_signature"] = False
                fu["include_signature"] = st.checkbox(
                    f"🖋️ Include bottom signature in follow-up #{idx+1}",
                    value=fu.get("include_signature", False),
                    key=f"comp_fu_{idx}_include_sig_chk",
                    help="Attach your saved corporate signature at the bottom of this follow-up email"
                )

                # Quick token insertion buttons for follow-up
                c_tok1, c_tok2, c_tok3, _ = st.columns([1, 1.2, 1.4, 3], vertical_alignment="center")
                with c_tok1:
                    if st.button("👤 {first_name}", key=f"comp_fu_tok_name_{idx}", use_container_width=True):
                        fu["body"] = fu["body"] + " {first_name}"
                        st.session_state[f"comp_fu_{idx}_body_in"] = fu["body"]
                        st.rerun()
                with c_tok2:
                    if st.button("🏢 [Company]", key=f"comp_fu_tok_comp_{idx}", use_container_width=True):
                        fu["body"] = fu["body"] + " [Company]"
                        st.session_state[f"comp_fu_{idx}_body_in"] = fu["body"]
                        st.rerun()
                with c_tok3:
                    if st.button("🖋️ Signature", key=f"comp_fu_tok_sig_{idx}", use_container_width=True):
                        sig = get_config("signature_html", "") or "Best regards,\nOutreach Team"
                        fu["body"] = fu["body"] + f"\n\n{sig}"
                        st.session_state[f"comp_fu_{idx}_body_in"] = fu["body"]
                        st.rerun()

                st.caption(f"📅 Follow-up #{idx+1} will queue to send **{fu['delay_days']} days** after the initial email.")

        # =====================================================================
        # Safety & Deliverability check (on initial email)
        # =====================================================================
        final_subj = inject_variables(parse_spintax(subj_val), current_lead)
        final_body = resolve_template(current_body, current_lead)

        missing_tokens = sorted(list(set(_missing_tokens(final_subj) + _missing_tokens(final_body))))
        neg_keywords   = get_config("negative_keywords", "")
        audit          = audit_email_deliverability(body_html=final_body, subject=final_subj, custom_negative_keywords=neg_keywords)
        triggers       = audit.get("detected_spam_words", [])

        spam_policy = (get_config("negative_keywords_action", "") or get_config("spam_policy", "warn") or "warn").strip().lower()
        is_warn_only = (spam_policy in ["warn", "warn_only"])

        if triggers:
            trig_names = ", ".join(f'"{t.get("word")}"' for t in triggers)
            sugg_list = []
            for t in triggers[:2]:
                if t.get("suggestions"):
                    sugg_list.append(f"'{t['word']}' → {', '.join(t['suggestions'][:2])}")
            sugg_txt = f" (Suggested fixes: {'; '.join(sugg_list)})" if sugg_list else ""

            if is_warn_only:
                st.markdown(
                    f'<div class="spam" style="background:#FFFBEB; border:1px solid #F59E0B; color:#92400E; padding:8px 12px; border-radius:6px; margin:8px 0; font-size:12px;">'
                    f'<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#D97706" stroke-width="2" style="vertical-align:middle; margin-right:4px;">'
                    f'<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
                    f' <b>Deliverability Advisory:</b> {len(triggers)} spam trigger word(s): <b>{trig_names}</b>{sugg_txt} · Warn-only mode (sending permitted)</div>',
                    unsafe_allow_html=True
                )
                can_send = bool((current_lead.get("email") or "").strip())
            else:
                st.markdown(
                    f'<div class="spam"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">'
                    f'<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
                    f' {len(triggers)} spam trigger: <b>&nbsp;{trig_names}</b>{sugg_txt} &nbsp;·&nbsp; edit to enable send</div>',
                    unsafe_allow_html=True
                )
                can_send = False
        else:
            can_send = bool((current_lead.get("email") or "").strip())

        recipient_is_specified = bool((current_lead.get("email") or "").strip())
        if not recipient_is_specified:
            st.markdown(
                '<div class="banner banner-warn" style="margin-top:8px; font-size:12px; color:#B45309; background:#FEF3C7; border:1px solid #FDE68A; padding:6px 12px; border-radius:6px;">'
                '⚠️ <b>No recipient selected:</b> Please choose a lead from the <b>To</b> dropdown or enter a custom address above before sending or scheduling.'
                '</div>',
                unsafe_allow_html=True
            )
            can_send = False

        if missing_tokens:
            toks_str = ", ".join(missing_tokens)
            st.markdown(
                f'<div class="spam" style="color:#DC2626;"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">'
                f'<path d="M18 6 6 18M6 6l12 12"/></svg>'
                f' Unfilled tokens: <b>{toks_str}</b> — fill before send</div>',
                unsafe_allow_html=True
            )
            can_send = False
        elif recipient_is_specified:
            st.markdown(
                '<div class="guard"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">'
                '<path d="M20 6 9 17l-5-5"/></svg>'
                ' No unfilled tokens — send guard clear</div>',
                unsafe_allow_html=True
            )

        # =====================================================================
        # ACTION BUTTONS (Send now, Schedule, Save draft, Save template)
        # =====================================================================
        c_act1, c_act2, c_act3, c_act4, c_act5 = st.columns([1.4, 1.3, 1.1, 1.2, 1.1], vertical_alignment="center")

        with c_act1:
            send_btn_label = "🚀 Send now"
            if st.session_state["compose_followups"]:
                send_btn_label += f" (+{len(st.session_state['compose_followups'])} FU)"

            if st.button(send_btn_label, type="primary", use_container_width=True, disabled=not can_send, key="comp_btn_send_now_cta"):
                if not selected_mb:
                    st.error("No active mailbox configured to send.")
                else:
                    render_compose_schedule_dialog(
                        mode="send_now",
                        current_lead=current_lead,
                        selected_mb=selected_mb,
                        final_subj=final_subj,
                        final_body=final_body,
                        followup_steps=st.session_state.get("compose_followups", []),
                        bcc_email=st.session_state.get("compose_bcc_email", "").strip(),
                        include_signature=st.session_state.get("compose_include_sig", True)
                    )

        with c_act2:
            sched_label = "🕒 Schedule"
            if st.session_state["compose_followups"]:
                sched_label += f" (+{len(st.session_state['compose_followups'])} FU)"

            if st.button(sched_label, use_container_width=True, disabled=not can_send, key="comp_btn_sched_cta"):
                if not selected_mb:
                    st.error("No active mailbox configured.")
                else:
                    render_compose_schedule_dialog(
                        mode="schedule",
                        current_lead=current_lead,
                        selected_mb=selected_mb,
                        final_subj=final_subj,
                        final_body=final_body,
                        followup_steps=st.session_state.get("compose_followups", []),
                        bcc_email=st.session_state.get("compose_bcc_email", "").strip(),
                        include_signature=st.session_state.get("compose_include_sig", True)
                    )

        with c_act3:
            if st.button("💾 Save draft", use_container_width=True, key="comp_btn_save_draft_cta"):
                inc_sig = st.session_state.get("compose_include_sig", True)
                sig_html = get_config("signature_html", "") or "Jack Connor · Sellomize · sales@sellomize.com"
                draft_body = f"{final_body}<br><br>{sig_html}" if (inc_sig and sig_html and sig_html not in final_body) else final_body
                recip_email = (current_lead.get("email") or "").strip() or (st.session_state.get("comp_recipient_email") or "").strip()
                create_email(
                    email_html=format_email_html(draft_body),
                    subject=final_subj,
                    recipient=recip_email,
                    status="Draft",
                    scheduled_time=get_engine_now_str(),
                    target_timezone="LOCAL",
                    bcc_email=st.session_state.get("compose_bcc_email", "").strip()
                )
                trigger_toast("Draft saved to Outbox.", icon="💾")
                st.session_state["active_screen"] = "outbox"
                st.session_state["main_app_tabs"] = "📥 Outbox"
                st.session_state["outbox_filter"] = "Drafts"
                st.rerun()

        with c_act4:
            if st.button("📋 Save template", use_container_width=True, key="comp_btn_save_tpl_cta"):
                new_t_name = f"Template: {subj_val[:28]}" if subj_val else "Saved Template"
                create_template(
                    template_name=new_t_name,
                    subject=subj_val,
                    body_content=current_body,
                    body_html=current_body
                )
                trigger_toast("Saved as template!", icon="📋")

        with c_act5:
            if st.button("🗑️ Discard", use_container_width=True, key="comp_btn_discard_cta", help="Clear email body, subject, recipient, and uploaded media"):
                st.session_state["compose_subject"] = ""
                st.session_state["compose_body_html"] = ""
                st.session_state["compose_visual_textarea"] = ""
                st.session_state["compose_last_synced_html"] = ""
                st.session_state["compose_followups"] = []
                st.session_state.pop("compose_selected_lead_id", None)
                st.session_state.pop("comp_lead_pick", None)
                st.session_state.pop("comp_custom_email_in", None)
                st.session_state["comp_recipient_email"] = ""
                st.session_state.pop("compose_img_up", None)
                st.session_state.pop("comp_subj_in", None)
                trigger_toast("Compose editor cleared.", icon="🗑️")
                st.rerun()

    # =========================================================================
    # RIGHT COLUMN — Live preview (Subject + Body preview)
    # =========================================================================
    with col_preview:
        lead_name = current_lead.get("name") or "the recipient"
        st.markdown(
            f'<span class="lbl">Live preview — what {html.escape(lead_name)} receives</span>',
            unsafe_allow_html=True
        )

        signature_html = get_config("signature_html", "") or "Jack Connor · Sellomize · sales@sellomize.com"

        # If follow-ups exist, allow selecting which email to preview
        show_sig_in_preview = False
        if st.session_state["compose_followups"]:
            prev_choice = st.radio(
                "Preview selection",
                ["📧 Initial Email"] + [f"↩️ Follow-up #{i+1}" for i in range(len(st.session_state["compose_followups"]))],
                horizontal=True,
                label_visibility="collapsed",
                key="comp_prev_toggle"
            )
            if prev_choice.startswith("📧"):
                preview_subj = final_subj
                preview_body = format_email_html(final_body)
                show_sig_in_preview = st.session_state.get("compose_include_sig", True)
            else:
                fu_idx = int(prev_choice.split("#")[-1]) - 1
                fu_obj = st.session_state["compose_followups"][fu_idx]
                preview_subj = inject_variables(parse_spintax(fu_obj["subject"]), current_lead)
                fu_resolved = resolve_template(fu_obj["body"], current_lead)
                preview_body = format_email_html(fu_resolved)
                show_sig_in_preview = fu_obj.get("include_signature", False)
        else:
            preview_subj = final_subj
            preview_body = format_email_html(final_body)
            show_sig_in_preview = st.session_state.get("compose_include_sig", True)

        bcc_active_str = st.session_state.get("compose_bcc_email", "").strip() if st.session_state.get("compose_show_bcc") else ""
        bcc_preview_html = (
            f'<div style="font-size:11px; color:#475569; margin-bottom:8px; padding-bottom:4px; border-bottom:1px dashed #CBD5E1;">'
            f'📬 <b>BCC:</b> <span style="font-family:monospace; color:#083731;">{html.escape(bcc_active_str)}</span>'
            f'</div>'
        ) if bcc_active_str else ''

        # Avoid duplicate preview signature if preview_body already contains the signature HTML or branding
        already_has_sig = (signature_html and signature_html in preview_body) or ("Sellomize Logo" in preview_body) or ("Jack Connor" in preview_body)
        sig_box_html = f'<div class="sig">{signature_html}</div>' if (show_sig_in_preview and signature_html and not already_has_sig) else ''

        preview_box_html = (
            '<div class="preview">'
            f'<div style="font-weight:700; color:#083731; margin-bottom:8px; font-size:13px; border-bottom:1px solid #E2E8F0; padding-bottom:6px;">'
            f'Subject: {html.escape(preview_subj)}'
            f'</div>'
            f'{bcc_preview_html}'
            f'{preview_body}'
            f'{sig_box_html}'
            '</div>'
            f'<div class="banner banner-info" style="margin-top:12px;">'
            f'Variables resolved for this recipient. This exact HTML is what gets sent.'
            f'</div>'
        )
        if hasattr(st, "html"):
            st.html(preview_box_html)
        else:
            st.markdown(preview_box_html, unsafe_allow_html=True)
