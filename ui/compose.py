"""
ui/compose.py - Compose Screen for Sellomize Reach.

Changes vs previous:
- "✏️ Custom address" button moved to the top row next to the To selector.
- "➕ Add follow-up" now opens a popover where you pick:
    • Follow-up subject, body (plain-text prefill)
    • Send date & time (UTC+5)
  and pressing "Queue follow-up" creates a real approved email row.
- Bottom action buttons laid out in a 2-row grid so labels never truncate:
    Row A: 🚀 Send now | 🕒 Schedule  | 💾 Save draft
    Row B: 📋 Save template | ➕ Add follow-up  (wider = full labels)
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
            "Hi [Name],<br><br>"
            "We haven't been properly introduced, but I was looking through [Company] on Amazon and noticed a number of listings showing currently unavailable.<br><br>"
            "When a customer searches and finds it unavailable, the sale simply stops there. I'd be glad to take a look together."
        )
    if "compose_subject" not in st.session_state:
        st.session_state["compose_subject"] = "[Company] + Amazon"

    # Custom-address mode flag
    if "compose_custom_mode" not in st.session_state:
        st.session_state["compose_custom_mode"] = False

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

            # ── Custom-address button moved HERE (top level, not buried in dropdown) ──
            custom_mode = st.session_state["compose_custom_mode"]
            btn_label   = "📋 Pick from CRM" if custom_mode else "✏️ Custom address"
            if st.button(btn_label, key="comp_custom_toggle", use_container_width=True):
                st.session_state["compose_custom_mode"] = not custom_mode
                st.rerun()

        # Build lead list + either dropdown or custom input below the row
        lead_choices: Dict[str, Any] = {}
        for c in contacts:
            label = (
                f"{c.get('name') or 'Lead'} <{c.get('email')}>"
                + (f" — {c.get('company')}" if c.get("company") else "")
            )
            lead_choices[label] = c

        if st.session_state["compose_custom_mode"]:
            # Free-type mode
            custom_email = st.text_input(
                "Custom recipient email",
                placeholder="e.g. partner@example.com or Jane Doe <jane@example.com>",
                label_visibility="collapsed",
                key="comp_custom_email_in"
            )
            chosen_lead_obj = None
        else:
            # CRM picker
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
        # ROW 2 — Subject
        # =====================================================================
        st.markdown('<span class="lbl">Subject</span>', unsafe_allow_html=True)
        subj_val = st.text_input(
            "Subject",
            value=st.session_state.get("compose_subject", ""),
            label_visibility="collapsed",
            key="comp_subj_in"
        )
        st.session_state["compose_subject"] = subj_val

        # =====================================================================
        # ROW 3 — Body editor
        # =====================================================================
        st.markdown('<span class="lbl">Body</span>', unsafe_allow_html=True)
        current_body = render_dual_mode_editor(
            key_prefix="compose",
            initial_content=st.session_state.get("compose_body_html", ""),
            height=180
        )
        st.session_state["compose_body_html"] = current_body

        # =====================================================================
        # Safety & Deliverability check
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
        # ACTION BUTTONS  — 2-row layout so labels never truncate
        #
        #  Row A: [🚀 Send now]  [🕒 Schedule ▼]  [💾 Save draft]
        #  Row B: [📋 Save as template]            [➕ Add follow-up ▼]
        # =====================================================================

        # ── Row A ────────────────────────────────────────────────────────────
        a1, a2, a3 = st.columns([1.3, 1.2, 1.2])

        with a1:
            if st.button("🚀 Send now", type="primary", use_container_width=True, disabled=not can_send):
                if not selected_mb:
                    st.error("No active mailbox configured to send.")
                else:
                    with st.spinner("Connecting to Hostinger and sending..."):
                        email_html = format_email_html(final_body)
                        email_id   = create_email(
                            email_html=email_html,
                            subject=final_subj,
                            recipient=current_lead["email"].strip(),
                            status="Approved",
                            scheduled_time=get_engine_now_str(),
                            target_timezone="Asia/Karachi"
                        )
                        ok = dispatch_email_hostinger({
                            "id":               email_id,
                            "recipient":        current_lead["email"].strip(),
                            "subject":          final_subj,
                            "email_html":       email_html,
                            "smtp_account_id":  selected_mb["id"],
                            "target_timezone":  "Asia/Karachi"
                        })
                        if ok:
                            trigger_toast(f"Email sent to {current_lead['email']}!", icon="🚀")
                            st.session_state["active_screen"] = "outbox"
                            st.rerun()
                        else:
                            st.error("Dispatch failed.")

        with a2:
            with st.popover("🕒 Schedule", use_container_width=True):
                st.markdown("**Schedule Outreach (UTC+5)**")
                s_date = st.date_input("Date", value=get_engine_now().date(), key="comp_sd")
                s_time = st.time_input("Time", value=(get_engine_now() + timedelta(hours=1)).time(), key="comp_st")
                if st.button("Confirm Schedule", type="primary", use_container_width=True, disabled=not can_send):
                    comb_dt = datetime.combine(s_date, s_time)
                    s_iso   = comb_dt.strftime("%Y-%m-%d %H:%M:%S")
                    create_email(
                        email_html=format_email_html(final_body),
                        subject=final_subj,
                        recipient=current_lead["email"].strip(),
                        status="Approved",
                        scheduled_time=s_iso,
                        target_timezone="Asia/Karachi"
                    )
                    trigger_toast(f"Scheduled for {s_iso} (UTC+5)!", icon="🕒")
                    st.session_state["active_screen"] = "outbox"
                    st.rerun()

        with a3:
            if st.button("💾 Save draft", use_container_width=True):
                create_email(
                    email_html=format_email_html(final_body),
                    subject=final_subj,
                    recipient=current_lead.get("email", "").strip(),
                    status="Pending",
                    scheduled_time=get_engine_now_str(),
                    target_timezone="Asia/Karachi"
                )
                trigger_toast("Draft saved to Outbox.", icon="💾")

        # ── Row B ────────────────────────────────────────────────────────────
        b1, b2 = st.columns(2)

        with b1:
            if st.button("📋 Save as template", use_container_width=True):
                new_t_name = f"Template: {subj_val[:30]}" if subj_val else "Saved Template"
                create_template(template_name=new_t_name, body_content=current_body)
                trigger_toast("Saved as template!", icon="📋")

        with b2:
            # ── Follow-up popover ─────────────────────────────────────────
            # Generates a REAL second scheduled email (not just a P.S. append).
            with st.popover("➕ Add follow-up", use_container_width=True):
                st.markdown("**Queue a Follow-up Email**")
                st.caption(
                    f"Recipient: **{current_lead.get('name') or current_lead.get('email')}** "
                    f"({current_lead.get('email', '')})"
                )

                # Subject pre-filled with "Re:" prefix
                fu_subj_default = f"Re: {subj_val}" if subj_val and not subj_val.startswith("Re:") else subj_val
                fu_subj = st.text_input(
                    "Follow-up subject",
                    value=fu_subj_default,
                    key="comp_fu_subj"
                )

                # Body pre-filled with a sensible follow-up message
                fu_body_default = (
                    f"Hi [Name],<br><br>"
                    f"Just wanted to follow up on my previous email. "
                    f"Happy to jump on a quick call if that's easier.<br><br>"
                    f"Best regards,"
                )
                fu_body = st.text_area(
                    "Follow-up body",
                    value="Hi [Name],\n\nJust wanted to follow up on my previous email. "
                          "Happy to jump on a quick call if that's easier.\n\nBest regards,",
                    height=120,
                    key="comp_fu_body"
                )

                # Date & time pickers (UTC+5)
                fu_now        = get_engine_now()
                fu_default_dt = fu_now + timedelta(days=3)   # default: 3 days from now
                st.markdown("**Send follow-up on (UTC+5)**")
                fu_c1, fu_c2 = st.columns(2)
                with fu_c1:
                    fu_date = st.date_input(
                        "Date",
                        value=fu_default_dt.date(),
                        min_value=fu_now.date(),
                        key="comp_fu_date"
                    )
                with fu_c2:
                    fu_time = st.time_input(
                        "Time (UTC+5)",
                        value=fu_default_dt.replace(hour=9, minute=0, second=0).time(),
                        key="comp_fu_time"
                    )

                fu_sched_dt  = datetime.combine(fu_date, fu_time)
                fu_sched_iso = fu_sched_dt.strftime("%Y-%m-%d %H:%M:%S")

                st.info(
                    f"📅 Follow-up will be queued for **{fu_sched_dt.strftime('%a %b %d, %Y at %H:%M')} UTC+5**"
                )

                if st.button(
                    "✅ Queue follow-up",
                    type="primary",
                    use_container_width=True,
                    disabled=not current_lead.get("email"),
                    key="comp_fu_confirm"
                ):
                    recipient_email = current_lead.get("email", "").strip()
                    if not recipient_email:
                        st.error("No recipient email set.")
                    else:
                        # Convert plain-text body to HTML paragraphs
                        fu_body_html = fu_body.replace("\n\n", "</p><p>").replace("\n", "<br>")
                        fu_body_html = f"<p>{fu_body_html}</p>"

                        # Resolve variables against the current lead
                        fu_final_subj = inject_variables(parse_spintax(fu_subj), current_lead)
                        fu_final_body = resolve_template(fu_body_html, current_lead)

                        create_email(
                            email_html=format_email_html(fu_final_body),
                            subject=fu_final_subj,
                            recipient=recipient_email,
                            status="Approved",
                            scheduled_time=fu_sched_iso,
                            target_timezone="Asia/Karachi"
                        )
                        trigger_toast(
                            f"Follow-up queued for {fu_sched_dt.strftime('%b %d, %H:%M')} UTC+5!",
                            icon="📅"
                        )
                        st.rerun()

    # =========================================================================
    # RIGHT COLUMN — Live preview
    # =========================================================================
    with col_preview:
        lead_name = current_lead.get("name") or "the recipient"
        st.markdown(
            f'<span class="lbl">Live preview — what {lead_name} receives</span>',
            unsafe_allow_html=True
        )

        signature_html = get_config("signature_html", "") or "Jack Conner · Sellomize · jack@sellomize.com"
        preview_box_html = (
            '<div class="preview">'
            f'{final_body}'
            f'<div class="sig">{signature_html}</div>'
            '</div>'
            '<div class="banner banner-info" style="margin-top:12px;">'
            'Variables resolved for this recipient. This exact HTML is what gets sent.'
            '</div>'
        )
        if hasattr(st, "html"):
            st.html(preview_box_html)
        else:
            st.markdown(preview_box_html, unsafe_allow_html=True)
