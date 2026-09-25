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
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

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
    DB_FILE,
)
from template_engine import (
    resolve_template,
    inject_variables,
    parse_spintax,
    audit_email_deliverability,
    _missing_tokens,
    format_email_html,
)
from scheduler import dispatch_email_hostinger
from timezone_helper import get_engine_now, get_engine_now_str
from ui.editor import render_dual_mode_editor
from ui.components import trigger_toast

_TOKEN_RE = re.compile(r'\[([A-Za-z0-9_]+)\]|\{([A-Za-z0-9_]+)\}')

# Backward compatibility copies for legacy tests
DEFAULT_COPIES = {
    1: {
        "subj": "Quick observation for [Company]",
        "body": "Hi [Name],\n\nI noticed [Company] and wanted to reach out regarding your growth.\n\nBest regards,",
    },
    2: {
        "subj": "Re: Quick observation for [Company]",
        "body": "Hi [Name],\n\nJust following up on my previous note.\n\nBest,",
    },
    3: {
        "subj": "Final note for [Company]",
        "body": "Hi [Name],\n\nI haven't heard back, so I'll assume the timing isn't right. Wishing you success!",
    },
}


def _apply_variable_fallback(text: str, fallback: str) -> str:
    if not fallback:
        return _TOKEN_RE.sub("", text)
    return _TOKEN_RE.sub(fallback, text)


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


# ---------------------------------------------------------------------------
# Dialog: Confirm Outreach & Schedule Sequence (UTC+5)
# ---------------------------------------------------------------------------

@st.dialog("🚀 Confirm Outreach & Schedule Sequence (UTC+5)")
def render_compose_schedule_dialog(
    mode: str,
    current_lead: Dict[str, Any],
    selected_mb: Dict[str, Any],
    final_subj: str,
    final_body: str,
    followup_steps: List[Dict[str, Any]],
):
    """Modal popup allowing exact custom date and time setting for initial email and each follow-up separately."""
    recipient_clean = (current_lead.get("email") or "").strip()
    recipient_name  = current_lead.get("name") or recipient_clean
    lead_tz         = current_lead.get("country_or_timezone") or "LOCAL"
    now_engine      = get_engine_now()

    st.markdown(
        f"<div style='background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px 12px; margin-bottom:12px; font-size:13px;'>"
        f"<b>Recipient:</b> {html.escape(recipient_name)} &lt;{html.escape(recipient_clean)}&gt;<br>"
        f"<b>Sending Mailbox:</b> {html.escape(selected_mb.get('email', ''))}"
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
                init_time = st.time_input("Initial Time (UTC+5)", value=(now_engine + timedelta(minutes=15)).time(), key="comp_dlg_init_t")
            init_dt = datetime.combine(init_date, init_time)
        else:
            init_dt = None
    else:
        st.markdown("<span class='lbl'>Scheduled Date &amp; Time (UTC+5)</span>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            init_date = st.date_input("Scheduled Date", value=now_engine.date(), min_value=now_engine.date(), key="comp_dlg_sched_d", label_visibility="collapsed")
        with c2:
            init_time = st.time_input("Scheduled Time", value=(now_engine + timedelta(hours=1)).time(), key="comp_dlg_sched_t", label_visibility="collapsed")
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
                        key=f"comp_dlg_fu_t_{idx}"
                    )
                target_fu_dt = datetime.combine(f_date, f_time)
                fu_dts.append(target_fu_dt)
                st.caption(f"📅 Scheduled to dispatch: **{target_fu_dt.strftime('%a %b %d, %Y at %H:%M')} (UTC+5)**")

    # ── Confirmation Action ──
    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)
    confirm_label = "🚀 Launch Outreach Sequence" if (mode == "send_now" and init_dt is None) else "🕒 Confirm Scheduled Sequence"
    if st.button(confirm_label, type="primary", use_container_width=True, key="comp_dlg_confirm_cta"):
        with st.spinner("Processing outreach..."):
            email_html = format_email_html(final_body)

            # 1. Initial Email
            if init_dt is None:
                email_id = create_email(
                    email_html=email_html,
                    subject=final_subj,
                    recipient=recipient_clean,
                    status="Approved",
                    scheduled_time=get_engine_now_str(),
                    target_timezone=lead_tz
                )
                ok = dispatch_email_hostinger({
                    "id": email_id,
                    "recipient": recipient_clean,
                    "subject": final_subj,
                    "email_html": email_html,
                    "smtp_account_id": selected_mb["id"],
                    "target_timezone": lead_tz
                })
            else:
                sched_str = init_dt.strftime("%Y-%m-%d %H:%M:%S")
                create_email(
                    email_html=email_html,
                    subject=final_subj,
                    recipient=recipient_clean,
                    status="Approved",
                    scheduled_time=sched_str,
                    target_timezone=lead_tz
                )
                ok = True

            # 2. Follow-ups with individually customized timing
            for idx, fu in enumerate(followup_steps):
                fu_target_dt = fu_dts[idx]
                fu_sched_str = fu_target_dt.strftime("%Y-%m-%d %H:%M:%S")
                fu_subj_res  = inject_variables(parse_spintax(fu["subject"]), current_lead)
                fu_body_raw  = fu["body"].replace("\n\n", "</p><p>").replace("\n", "<br>")
                fu_body_res  = resolve_template(f"<p>{fu_body_raw}</p>", current_lead)

                create_email(
                    email_html=format_email_html(fu_body_res),
                    subject=fu_subj_res,
                    recipient=recipient_clean,
                    status="Approved",
                    scheduled_time=fu_sched_str,
                    target_timezone=lead_tz
                )

            st.session_state["compose_followups"] = []
            if init_dt is None:
                msg = f"Sent initial email to {recipient_clean}!"
                if followup_steps:
                    msg += f" Scheduled {len(followup_steps)} follow-up(s) with custom timing."
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

    # Prefill from Leads tab
    prefill_lead_id = st.session_state.pop("prefill_compose_lead_id", None)
    if prefill_lead_id:
        st.session_state["compose_selected_lead_id"] = prefill_lead_id

    # Default body / subject
    if "compose_body_html" not in st.session_state:
        st.session_state["compose_body_html"] = (
            "Hi [Name],\n\n"
            "We haven't been properly introduced, but I was looking through [Company] on Amazon and noticed a number of listings showing currently unavailable.\n\n"
            "When a customer searches and finds it unavailable, the sale simply stops there. I'd be glad to take a look together."
        )
    if "compose_subject" not in st.session_state:
        st.session_state["compose_subject"] = "[Company] + Amazon"

    # Custom-address mode flag
    if "compose_custom_mode" not in st.session_state:
        st.session_state["compose_custom_mode"] = False

    # Follow-up sequence state in Compose (same row tabs)
    if "compose_followups" not in st.session_state:
        st.session_state["compose_followups"] = []

    col_editor, col_preview = st.columns([1.1, 0.9], gap="large")

    with col_editor:
        # =====================================================================
        # ROW 1 — Mailbox + Recipient + Custom-address toggle
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
            st.markdown('<span class="lbl">To (lead or type an address)</span>', unsafe_allow_html=True)
            custom_mode = st.session_state["compose_custom_mode"]
            btn_label   = "📋 Pick from CRM" if custom_mode else "✏️ Custom address"
            if st.button(btn_label, key="comp_custom_toggle", use_container_width=True):
                st.session_state["compose_custom_mode"] = not custom_mode
                st.rerun()

        # Build lead list + either dropdown or custom input
        lead_choices: Dict[str, Any] = {}
        for c in contacts:
            label = (
                f"{c.get('name') or 'Lead'} <{c.get('email')}>"
                + (f" — {c.get('company')}" if c.get("company") else "")
            )
            lead_choices[label] = c

        if st.session_state["compose_custom_mode"]:
            custom_email = st.text_input(
                "Custom recipient email",
                placeholder="e.g. partner@example.com or Jane Doe <jane@example.com>",
                label_visibility="collapsed",
                key="comp_custom_email_in"
            )
            chosen_lead_obj = None
        else:
            preselected_idx = 0
            target_prefill_id = st.session_state.get("compose_selected_lead_id")
            if target_prefill_id:
                for idx, (lbl, l_obj) in enumerate(lead_choices.items()):
                    if l_obj and l_obj.get("id") == target_prefill_id:
                        preselected_idx = idx
                        break

            sel_lead_label = st.selectbox(
                "To", list(lead_choices.keys()),
                index=preselected_idx,
                label_visibility="collapsed",
                key="comp_lead_pick"
            )
            chosen_lead_obj = lead_choices.get(sel_lead_label)
            custom_email    = chosen_lead_obj.get("email", "") if chosen_lead_obj else ""

        # Resolve active recipient
        if custom_email.strip():
            matched = get_contact_by_email(custom_email.strip())
            if matched:
                current_lead = matched
            else:
                current_lead = {
                    "id":                   None,
                    "name":                 custom_email.split("@")[0].capitalize(),
                    "email":                custom_email.strip(),
                    "company":              "",
                    "country_or_timezone":  "LOCAL",
                }
        elif chosen_lead_obj:
            current_lead = chosen_lead_obj
        else:
            current_lead = {
                "name":    "Danessa Myricks",
                "email":   "danessa@dmbeauty.com",
                "company": "DM Beauty",
                "country_or_timezone": "LOCAL",
            }

        # =====================================================================
        # SEQUENCE TABS: Initial Email + Follow-ups in the SAME ROW
        # =====================================================================
        st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:14px 0 10px;'>", unsafe_allow_html=True)
        num_fu = len(st.session_state["compose_followups"])
        can_add_fu = num_fu < 3
        if num_fu > 0:
            seq_hdr1, seq_hdr2, seq_hdr3 = st.columns([2.0, 1.8, 1.2], vertical_alignment="center")
            with seq_hdr1:
                st.markdown("<span class='lbl' style='font-size:13px; font-weight:700;'>Message &amp; Sequence</span>", unsafe_allow_html=True)
            with seq_hdr2:
                if st.button(
                    f"➕ Add follow-up ({num_fu}/3)",
                    key="comp_add_fu_btn",
                    use_container_width=True,
                    disabled=not can_add_fu,
                    help="Adds another follow-up tab in this sequence"
                ):
                    fu_step_num = num_fu + 1
                    default_delay = fu_step_num * 3
                    curr_subj = st.session_state.get("compose_subject", "[Company] + Amazon")
                    re_subj = f"Re: {curr_subj}" if not curr_subj.startswith("Re:") else curr_subj
                    st.session_state["compose_followups"].append({
                        "delay_days": default_delay,
                        "subject": re_subj,
                        "body": "Hi [Name],\n\nJust following up on my previous note to see if you had a chance to look it over.\n\nBest regards,"
                    })
                    st.rerun()
            with seq_hdr3:
                if st.button("↩️ Undo", key="comp_undo_fu_btn", use_container_width=True, help="Undo / remove last added follow-up"):
                    st.session_state["compose_followups"].pop()
                    st.rerun()
        else:
            seq_hdr1, seq_hdr2 = st.columns([3.2, 1.8], vertical_alignment="center")
            with seq_hdr1:
                st.markdown("<span class='lbl' style='font-size:13px; font-weight:700;'>Message &amp; Sequence</span>", unsafe_allow_html=True)
            with seq_hdr2:
                if st.button(
                    "➕ Add follow-up",
                    key="comp_add_fu_btn",
                    use_container_width=True,
                    help="Adds a follow-up tab in this row — each editable separately"
                ):
                    default_delay = 3
                    curr_subj = st.session_state.get("compose_subject", "[Company] + Amazon")
                    re_subj = f"Re: {curr_subj}" if not curr_subj.startswith("Re:") else curr_subj
                    st.session_state["compose_followups"].append({
                        "delay_days": default_delay,
                        "subject": re_subj,
                        "body": "Hi [Name],\n\nJust following up on my previous note to see if you had a chance to look it over.\n\nBest regards,"
                    })
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
            current_body = render_dual_mode_editor(
                key_prefix="compose",
                initial_content=st.session_state.get("compose_body_html", ""),
                height=180
            )
            st.session_state["compose_body_html"] = current_body

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
                fu["body"] = st.text_area(
                    "Follow-up Body",
                    value=fu["body"],
                    height=140,
                    key=f"comp_fu_{idx}_body_in",
                    label_visibility="collapsed"
                )

                # Quick token insertion buttons for follow-up
                c_tok1, c_tok2, c_tok3, _ = st.columns([1, 1.2, 1.4, 3])
                with c_tok1:
                    if st.button("👤 [Name]", key=f"comp_fu_tok_name_{idx}", use_container_width=True):
                        fu["body"] = fu["body"] + " [Name]"
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

        if triggers:
            trig_names = ", ".join(f'"{t.get("word")}"' for t in triggers[:2])
            st.markdown(
                f'<div class="spam"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">'
                f'<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
                f' {len(triggers)} spam trigger: <b>&nbsp;{trig_names}</b> &nbsp;·&nbsp; edit to enable send</div>',
                unsafe_allow_html=True
            )
            can_send = False
        else:
            can_send = bool((current_lead.get("email") or "").strip())

        if missing_tokens:
            toks_str = ", ".join(missing_tokens)
            st.markdown(
                f'<div class="spam" style="color:#DC2626;"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">'
                f'<path d="M18 6 6 18M6 6l12 12"/></svg>'
                f' Unfilled tokens: <b>{toks_str}</b> — fill before send</div>',
                unsafe_allow_html=True
            )
            can_send = False
        else:
            st.markdown(
                '<div class="guard"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">'
                '<path d="M20 6 9 17l-5-5"/></svg>'
                ' No unfilled tokens — send guard clear</div>',
                unsafe_allow_html=True
            )

        # =====================================================================
        # ACTION BUTTONS (Send now, Schedule, Save draft, Save template)
        # =====================================================================
        c_act1, c_act2, c_act3, c_act4 = st.columns([1.4, 1.3, 1.2, 1.3])

        with c_act1:
            send_btn_label = "🚀 Send now"
            if st.session_state["compose_followups"]:
                send_btn_label += f" (+{len(st.session_state['compose_followups'])} FU)"

            if st.button(send_btn_label, type="primary", use_container_width=True, disabled=not can_send):
                if not selected_mb:
                    st.error("No active mailbox configured to send.")
                else:
                    render_compose_schedule_dialog(
                        mode="send_now",
                        current_lead=current_lead,
                        selected_mb=selected_mb,
                        final_subj=final_subj,
                        final_body=final_body,
                        followup_steps=st.session_state.get("compose_followups", [])
                    )

        with c_act2:
            sched_label = "🕒 Schedule"
            if st.session_state["compose_followups"]:
                sched_label += f" (+{len(st.session_state['compose_followups'])} FU)"

            if st.button(sched_label, use_container_width=True, disabled=not can_send):
                if not selected_mb:
                    st.error("No active mailbox configured.")
                else:
                    render_compose_schedule_dialog(
                        mode="schedule",
                        current_lead=current_lead,
                        selected_mb=selected_mb,
                        final_subj=final_subj,
                        final_body=final_body,
                        followup_steps=st.session_state.get("compose_followups", [])
                    )

        with c_act3:
            if st.button("💾 Save draft", use_container_width=True):
                create_email(
                    email_html=format_email_html(final_body),
                    subject=final_subj,
                    recipient=current_lead.get("email", "").strip(),
                    status="Pending",
                    scheduled_time=get_engine_now_str(),
                    target_timezone="LOCAL"
                )
                trigger_toast("Draft saved to Outbox.", icon="💾")

        with c_act4:
            if st.button("📋 Save template", use_container_width=True):
                new_t_name = f"Template: {subj_val[:28]}" if subj_val else "Saved Template"
                create_template(
                    template_name=new_t_name,
                    subject=subj_val,
                    body_content=current_body,
                    body_html=current_body
                )
                trigger_toast("Saved as template!", icon="📋")

    # =========================================================================
    # RIGHT COLUMN — Live preview (Subject + Body preview)
    # =========================================================================
    with col_preview:
        lead_name = current_lead.get("name") or "the recipient"
        st.markdown(
            f'<span class="lbl">Live preview — what {html.escape(lead_name)} receives</span>',
            unsafe_allow_html=True
        )

        signature_html = get_config("signature_html", "") or "Jack Conner · Sellomize · jack@sellomize.com"

        # If follow-ups exist, allow selecting which email to preview
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
                preview_body = final_body
            else:
                fu_idx = int(prev_choice.split("#")[-1]) - 1
                fu_obj = st.session_state["compose_followups"][fu_idx]
                preview_subj = inject_variables(parse_spintax(fu_obj["subject"]), current_lead)
                fu_raw = fu_obj["body"].replace("\n\n", "</p><p>").replace("\n", "<br>")
                preview_body = resolve_template(f"<p>{fu_raw}</p>", current_lead)
        else:
            preview_subj = final_subj
            preview_body = final_body

        preview_box_html = (
            '<div class="preview">'
            f'<div style="font-weight:700; color:#083731; margin-bottom:8px; font-size:13px; border-bottom:1px solid #E2E8F0; padding-bottom:6px;">'
            f'Subject: {html.escape(preview_subj)}'
            f'</div>'
            f'{preview_body}'
            f'<div class="sig">{signature_html}</div>'
            '</div>'
            f'<div class="banner banner-info" style="margin-top:12px;">'
            f'Variables resolved for this recipient. This exact HTML is what gets sent.'
            f'</div>'
        )
        if hasattr(st, "html"):
            st.html(preview_box_html)
        else:
            st.markdown(preview_box_html, unsafe_allow_html=True)
