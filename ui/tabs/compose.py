"""
compose.py - Unified Compose & Send tab for Sellomize Reach.

Single progressive-disclosure flow covering both single sends and multi-touch campaigns:
  Step 1: Recipients  (CRM search + typed emails, dedup, compact chips)
  Step 2: Sequence    (Once / Twice / 3 times)
  Step 3: Write       (per-touch blocks with inline spam highlighting)
  Step 4: Schedule    (market, window, pacing - advanced in expander)
  Step 5: Send        (context-labelled primary button)

Backend engines (template_engine, scheduler, smtp_dispatcher) are reused unchanged.
"""

from datetime import datetime, timedelta
import re
import uuid
import streamlit as st

from database import (
    get_contacts,
    get_templates,
    get_config,
    set_config,
    get_template_by_id,
    get_contact_by_id,
    get_contact_by_email,
    upsert_contact_by_email,
    create_contact,
    create_email,
    create_sequence_rule,
    create_template,
    get_smtp_accounts,
    get_system_excluded_emails,
    create_notification,
    DB_FILE,
)
from scheduler import (
    calculate_staggered_schedule,
    analyze_schedule_overflow,
    WEEKDAY_NAMES,
)
from timezone_helper import (
    TARGET_MARKETS,
    get_market_info,
    get_market_current_time,
    get_time_difference_summary,
    calculate_market_aware_schedule,
    is_within_market_hours,
)
from template_engine import (
    resolve_template,
    format_email_html,
    inject_variables,
    parse_spintax,
    scan_all_negative_keywords,
    audit_email_deliverability,
    highlight_spam_triggers,
    COMMON_SPAM_TRIGGERS,
)
from ui.components import render_tab_header, render_html_preview, trigger_toast


# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_TOKEN_RE = re.compile(r'\[([a-zA-Z0-9_\s\-]+)\]')

SEQ_LABELS = {
    1: "Once (1 touch)",
    2: "Twice (2 touches — initial + 1 follow-up)",
    3: "3 times (3 touches — initial + 2 follow-ups)",
}

DEFAULT_COPIES = {
    1: {
        "subj": "Quick observation for [Company]",
        "body": (
            "Hi [Name],\n\n"
            "I noticed [Company] and was really impressed by your recent growth.\n\n"
            "We help companies like yours scale their outbound reach and generate consistent leads without landing in spam.\n\n"
            "Do you have 10 minutes this week for a brief call?\n\nBest regards,"
        ),
    },
    2: {
        "subj": "Re: Quick observation for [Company]",
        "body": (
            "Hi [Name],\n\n"
            "Just following up to see if you had a chance to review my previous note.\n\n"
            "Would you be open to a quick 5-minute chat next week?\n\nBest,"
        ),
    },
    3: {
        "subj": "Final quick note for [Company]",
        "body": (
            "Hi [Name],\n\n"
            "I haven't heard back, so I'll assume the timing isn't right for [Company].\n\n"
            "No worries — feel free to reach out any time. Wishing you continued success!"
        ),
    },
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _is_valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s.strip()))


def _apply_variable_fallback(text: str, fallback: str) -> str:
    """Replace remaining [Token] placeholders with the configured fallback value."""
    if not fallback:
        return _TOKEN_RE.sub("", text)
    return _TOKEN_RE.sub(fallback, text)


def _missing_tokens(text: str) -> list:
    """Return list of unfilled [Token] names remaining in text."""
    return list({m.group(1).strip() for m in _TOKEN_RE.finditer(text)})


def _stub_contact(email: str) -> dict:
    """Minimal contact dict for a manually-typed email with no CRM record."""
    return {
        "id": None,
        "name": "",
        "email": email,
        "company": "",
        "custom_variables_dict": {},
        "custom_variables": "{}",
    }


def _sync_sending_window_to_db(preset: str, days: list, start: str, end: str, db_path: str = DB_FILE):
    """Persist active schedule to SQLite so scheduler daemon stays in sync."""
    if preset.startswith("24/7"):
        set_config("enforce_sending_window", "false", db_path=db_path)
        set_config("sending_days", ", ".join(WEEKDAY_NAMES), db_path=db_path)
        set_config("sending_start_time", "00:00", db_path=db_path)
        set_config("sending_end_time", "23:59", db_path=db_path)
    elif preset.startswith("Business"):
        set_config("enforce_sending_window", "true", db_path=db_path)
        set_config("sending_days", "Monday, Tuesday, Wednesday, Thursday, Friday", db_path=db_path)
        set_config("sending_start_time", "09:00", db_path=db_path)
        set_config("sending_end_time", "18:00", db_path=db_path)
    else:
        set_config("enforce_sending_window", "true", db_path=db_path)
        clean_days = [d for d in days if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        set_config("sending_days", ", ".join(clean_days), db_path=db_path)
        set_config("sending_start_time", (start or "09:00").strip(), db_path=db_path)
        set_config("sending_end_time", (end or "18:00").strip(), db_path=db_path)


# ---------------------------------------------------------------------------
# SESSION STATE — BACKWARD COMPAT WITH CRM NAVIGATION
# ---------------------------------------------------------------------------

def _bootstrap_from_crm_nav():
    """
    Translate legacy camp_* session-state keys set by CRM nav buttons into the
    new cs_* keys used by compose.py. Called once at the top of render_compose_tab.
    """
    incoming_mode = st.session_state.pop("camp_audience_mode", "")
    single_cid = st.session_state.pop("camp_single_contact_picker", None)
    cherry_ids = st.session_state.pop("camp_cherry_pick_multisel", None)
    seq_touches = st.session_state.pop("camp_seq_touches", None)

    if single_cid is not None:
        st.session_state["cs_crm_selected_ids"] = [single_cid]
    elif cherry_ids is not None:
        st.session_state["cs_crm_selected_ids"] = list(cherry_ids)

    if seq_touches:
        if "Twice" in seq_touches:
            st.session_state["cs_num_touches"] = 2
        elif "Thrice" in seq_touches or "3" in seq_touches:
            st.session_state["cs_num_touches"] = 3
        else:
            st.session_state["cs_num_touches"] = 1


# ---------------------------------------------------------------------------
# STEP 1 — RECIPIENT CONTROL
# ---------------------------------------------------------------------------

def _add_manual_email():
    """Callback: validate and add a typed email; dedup against CRM."""
    raw = st.session_state.get("cs_email_input_field", "").strip().lower()
    st.session_state["cs_email_input_field"] = ""

    if not raw:
        return
    if not _is_valid_email(raw):
        st.session_state["cs_recipient_error"] = f"'{raw}' is not a valid email address."
        return

    system_excluded = get_system_excluded_emails()
    if raw in system_excluded:
        st.session_state["cs_recipient_error"] = f"'{raw}' is a protected internal address and cannot be a recipient."
        return

    crm_ids = list(st.session_state.get("cs_crm_selected_ids", []))
    manual = list(st.session_state.get("cs_manual_emails", []))

    # Dedup: check CRM
    existing = get_contact_by_email(raw)
    if existing:
        if existing["id"] not in crm_ids:
            crm_ids.append(existing["id"])
            st.session_state["cs_crm_selected_ids"] = crm_ids
            st.session_state["cs_recipient_error"] = ""
            st.session_state["cs_dedup_notice"] = f"'{raw}' matched existing CRM contact — added to CRM selection."
        else:
            st.session_state["cs_recipient_error"] = f"'{raw}' is already selected (CRM contact)."
        return

    if raw in manual:
        st.session_state["cs_recipient_error"] = f"'{raw}' is already in the list."
        return

    manual.append(raw)
    st.session_state["cs_manual_emails"] = manual
    st.session_state["cs_recipient_error"] = ""
    st.session_state["cs_dedup_notice"] = ""


def _remove_manual_email(email: str):
    manual = list(st.session_state.get("cs_manual_emails", []))
    if email in manual:
        manual.remove(email)
    st.session_state["cs_manual_emails"] = manual


def _render_recipient_step(active_candidates: list, contact_id_map: dict, system_excluded: set):
    """Render Step 1: Recipients — CRM multiselect + manual typed emails."""
    with st.container(border=True):
        st.markdown("#### Step 1 — Recipients")

        # ── CRM multiselect ────────────────────────────────────────────────
        contact_id_keys = list(contact_id_map.keys())
        if "cs_crm_selected_ids" not in st.session_state:
            st.session_state["cs_crm_selected_ids"] = []
        else:
            # Prune IDs that are no longer active
            valid = [i for i in st.session_state["cs_crm_selected_ids"] if i in contact_id_map]
            if len(valid) != len(st.session_state["cs_crm_selected_ids"]):
                st.session_state["cs_crm_selected_ids"] = valid

        show_name = st.session_state.get("cs_show_name_email", False)

        def _fmt_contact(cid):
            c = contact_id_map.get(cid, {})
            if show_name:
                return f"{c.get('name', '')} <{c.get('email', '')}> — {c.get('company') or 'No Company'}"
            return c.get("email", str(cid))

        col_ms, col_toggle = st.columns([4, 1], vertical_alignment="bottom")
        with col_ms:
            st.multiselect(
                "Search CRM contacts",
                options=contact_id_keys,
                format_func=_fmt_contact,
                key="cs_crm_selected_ids",
                placeholder="Type a name, email, or company to search…",
                help="Select one or more contacts from your CRM. Start typing to filter.",
            )
        with col_toggle:
            st.checkbox("Show name", value=show_name, key="cs_show_name_email")

        # ── Manual email entry ─────────────────────────────────────────────
        st.caption("Or add email addresses manually (press Add or Enter):")
        col_inp, col_add = st.columns([4, 1], vertical_alignment="bottom")
        with col_inp:
            st.text_input(
                "Type an email address",
                key="cs_email_input_field",
                label_visibility="collapsed",
                placeholder="someone@example.com",
                on_change=_add_manual_email,
            )
        with col_add:
            st.button("Add", on_click=_add_manual_email, use_container_width=True)

        # Error / dedup notices
        if st.session_state.get("cs_recipient_error"):
            st.error(st.session_state["cs_recipient_error"], icon="⚠️")
        if st.session_state.get("cs_dedup_notice"):
            st.info(st.session_state["cs_dedup_notice"], icon="🔗")
            st.session_state["cs_dedup_notice"] = ""

        # ── Manual email chips ─────────────────────────────────────────────
        manual_emails = st.session_state.get("cs_manual_emails", [])
        if manual_emails:
            st.markdown("**Manually added:**")
            for em in list(manual_emails):
                col_chip, col_rm = st.columns([6, 1], vertical_alignment="center")
                with col_chip:
                    is_exc = em in system_excluded
                    badge = " 🛡️ *Internal*" if is_exc else ""
                    st.markdown(
                        f"<span style='background:#EFF6FF; border:1px solid #BFDBFE; "
                        f"border-radius:20px; padding:3px 10px; font-size:0.85rem; "
                        f"color:#1E40AF;'>📧 {em}{badge}</span>",
                        unsafe_allow_html=True,
                    )
                with col_rm:
                    st.button("✕", key=f"rm_manual_{em}", on_click=_remove_manual_email, args=(em,), help=f"Remove {em}")

        # ── Save option ────────────────────────────────────────────────────
        if manual_emails:
            st.checkbox(
                "Save manually-added emails as new contacts",
                value=st.session_state.get("cs_save_manual_as_contacts", False),
                key="cs_save_manual_as_contacts",
                help="Creates a minimal CRM record for each typed email. Multi-touch sequences always require a CRM entry.",
            )

        # ── Live count summary ─────────────────────────────────────────────
        crm_sel = st.session_state.get("cs_crm_selected_ids", [])
        manual_clean = [e for e in manual_emails if e not in system_excluded]
        total = len(crm_sel) + len(manual_clean)
        if total:
            st.markdown(
                f"<div style='background:#F0FDF4; border:1px solid #BBF7D0; border-radius:8px; "
                f"padding:8px 14px; margin-top:8px;'>"
                f"<span style='color:#166534; font-weight:700; font-size:0.9rem;'>🎯 "
                f"{total} recipient{'s' if total != 1 else ''} "
                f"({len(crm_sel)} from CRM, {len(manual_clean)} manual)</span></div>",
                unsafe_allow_html=True,
            )

        # ── Expand details (on demand) ─────────────────────────────────────
        if crm_sel:
            with st.expander(f"▼ View details for {len(crm_sel)} CRM contact(s)", expanded=False):
                for cid in crm_sel:
                    c = contact_id_map.get(cid)
                    if c:
                        st.caption(
                            f"**{c.get('name') or '—'}** | {c.get('email')} | "
                            f"{c.get('company') or 'No Company'} | Status: {c.get('status') or '—'}"
                        )

    return {
        "crm_ids": list(st.session_state.get("cs_crm_selected_ids", [])),
        "manual_emails": [e for e in st.session_state.get("cs_manual_emails", []) if e not in system_excluded],
    }


# ---------------------------------------------------------------------------
# STEP 3 — ENHANCED TOUCH BLOCK
# ---------------------------------------------------------------------------

def _render_touch_block(
    touch_step: int,
    touch_label: str,
    template_options: dict,
    sample_contact: dict,
    neg_keywords_setting: str,
    default_subj: str,
    default_body: str,
    include_delay: bool = False,
    default_delay_val: int = 3,
    default_delay_unit: str = "Days",
) -> dict:
    """
    Authoring card for one outreach touch.
    Enhanced over campaigns.py: inline spam-word highlighting, explicit trigger list with suggestions.
    """
    tpl_keys = list(template_options.keys())
    saved = st.session_state.setdefault("cs_touch_state", {})

    k_mode = f"cs_mode_{touch_step}"
    k_tpl = f"cs_tpl_{touch_step}"
    k_subj = f"cs_subj_{touch_step}"
    k_body = f"cs_body_{touch_step}"
    k_dval = f"cs_dval_{touch_step}"
    k_dunit = f"cs_dunit_{touch_step}"
    k_save = f"cs_save_tpl_{touch_step}"
    k_name = f"cs_tpl_name_{touch_step}"

    for k in [k_mode, k_tpl, k_subj, k_body, k_dval, k_dunit, k_save, k_name]:
        if k in saved and k not in st.session_state:
            st.session_state[k] = saved[k]

    # Delay selector (for Touch 2 and 3)
    d_val, d_unit = default_delay_val, default_delay_unit
    if include_delay:
        col_dv, col_du, col_di = st.columns([1, 1, 3])
        with col_dv:
            d_val = st.number_input(
                f"Wait after Touch {touch_step - 1}",
                min_value=1, max_value=720,
                value=int(saved.get(k_dval, default_delay_val)),
                key=k_dval,
            )
            saved[k_dval] = int(d_val)
        with col_du:
            d_unit = st.selectbox("Unit", ["Days", "Hours"], index=0 if saved.get(k_dunit, "Days") == "Days" else 1, key=k_dunit)
            saved[k_dunit] = d_unit
        with col_di:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            st.caption(f"Generates +{d_val} {d_unit.lower()} after Touch {touch_step - 1} if no reply received.")

    # Authoring mode
    mode_opts = ["Use saved template", "Write your own"]
    saved_mode = saved.get(k_mode, mode_opts[0])
    mode_idx = 0 if str(saved_mode).startswith("Use") else 1
    author_mode = st.radio(
        "Compose method",
        mode_opts,
        index=mode_idx,
        horizontal=True,
        key=k_mode,
        label_visibility="collapsed",
    )
    saved[k_mode] = author_mode

    if author_mode == "Use saved template":
        col_tpl, col_subj = st.columns([1.2, 1.8])
        with col_tpl:
            if not tpl_keys:
                st.warning("No templates saved yet. Write your own or add templates in the Templates tab.")
                return {"step": touch_step, "label": touch_label, "source_type": "custom",
                        "template_id": None, "subject": default_subj, "custom_body": default_body,
                        "delay_value": int(d_val), "delay_unit": d_unit.lower(),
                        "save_as_template": False, "new_template_name": ""}
            saved_tpl = saved.get(k_tpl)
            def_idx = tpl_keys.index(saved_tpl) if saved_tpl in tpl_keys else min(touch_step - 1, len(tpl_keys) - 1)
            sel_tpl_id = st.selectbox("Template", tpl_keys, index=def_idx,
                                      format_func=lambda tid: template_options[tid], key=k_tpl)
            saved[k_tpl] = sel_tpl_id
        with col_subj:
            subj_val = st.text_input("Subject line", value=saved.get(k_subj, default_subj), key=k_subj,
                                     help="Supports [Name], [Company], {A|B} spintax.")
            saved[k_subj] = subj_val

        chosen_tpl = get_template_by_id(sel_tpl_id)
        raw_body = chosen_tpl["body_content"] if chosen_tpl else ""
        raw_subj = subj_val.strip() or (chosen_tpl["template_name"] if chosen_tpl else "Outreach")

        preview_subj = parse_spintax(inject_variables(raw_subj, sample_contact))
        preview_body = resolve_template(raw_body, sample_contact)
        final_html = format_email_html(preview_body)
        triggers = scan_all_negative_keywords(f"{preview_subj} {preview_body}", neg_keywords_setting)

        _render_spam_preview(preview_subj, final_html, triggers, touch_label, sample_contact, expanded=False)

        return {"step": touch_step, "label": touch_label, "source_type": "premade",
                "template_id": sel_tpl_id, "subject": subj_val, "custom_body": "",
                "delay_value": int(d_val) if include_delay else 0,
                "delay_unit": d_unit.lower() if include_delay else "days",
                "save_as_template": False, "new_template_name": ""}

    else:
        # Write your own
        st.markdown(
            "<div style='font-size:0.8rem; color:#475569; margin-bottom:4px;'>"
            "<strong>Variables:</strong> <code>[Name]</code> <code>[Company]</code> <code>[Email]</code> "
            "• <strong>Spintax:</strong> <code>{Hi|Hello|Hey}</code></div>",
            unsafe_allow_html=True,
        )
        subj_val = st.text_input("Subject line", value=saved.get(k_subj, default_subj), key=k_subj,
                                  help="Supports [Name], [Company], {A|B} spintax.")
        saved[k_subj] = subj_val

        body_val = st.text_area(f"Email body ({touch_label})", value=saved.get(k_body, default_body),
                                 height=160, key=k_body)
        saved[k_body] = body_val

        col_sv, col_sn = st.columns([1.4, 2.6])
        with col_sv:
            save_tpl = st.checkbox("Save as template", value=bool(saved.get(k_save, False)), key=k_save)
            saved[k_save] = save_tpl
        with col_sn:
            new_tpl_name = ""
            if save_tpl:
                def_name = f"{touch_label} — {datetime.now().strftime('%b %d')}"
                new_tpl_name = st.text_input("Template name", value=saved.get(k_name, def_name), key=k_name)
                saved[k_name] = new_tpl_name

        preview_subj = parse_spintax(inject_variables(subj_val, sample_contact))
        preview_body = resolve_template(body_val, sample_contact)
        final_html = format_email_html(preview_body)
        triggers = scan_all_negative_keywords(f"{preview_subj} {preview_body}", neg_keywords_setting)

        _render_spam_preview(preview_subj, final_html, triggers, touch_label, sample_contact, expanded=True)

        return {"step": touch_step, "label": touch_label, "source_type": "custom",
                "template_id": None, "subject": subj_val, "custom_body": body_val,
                "delay_value": int(d_val) if include_delay else 0,
                "delay_unit": d_unit.lower() if include_delay else "days",
                "save_as_template": bool(save_tpl),
                "new_template_name": new_tpl_name.strip() if save_tpl else ""}


def _render_spam_preview(preview_subj: str, final_html: str, triggers: list, touch_label: str, sample_contact: dict, expanded: bool):
    """Preview pane with inline spam-word highlighting and explicit trigger list."""
    highlighted_html = highlight_spam_triggers(final_html, triggers)
    sample_name = sample_contact.get("name") or "sample contact"

    with st.expander(f"Preview — {sample_name} ({touch_label})", expanded=expanded):
        st.markdown(f"**Subject:** `{preview_subj}`")
        render_html_preview(highlighted_html, height=180)

    if triggers:
        # Show exact words found + suggestions
        lines = []
        for t in triggers:
            alts = COMMON_SPAM_TRIGGERS.get(t.lower(), [])
            alt_str = " / ".join(f"*{a}*" for a in alts[:2]) if alts else "rephrase"
            lines.append(f"• **`{t}`** → try: {alt_str}")
        st.warning(
            "**Spam triggers found — highlighted in preview above:**\n" + "\n".join(lines)
            + "\n\nDrafts containing these words will be **Flagged** and cannot be approved until resolved.",
            icon="⚠️",
        )
    else:
        st.markdown(
            "<div style='background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.22); "
            "border-radius:8px; padding:6px 12px; margin:4px 0 10px;'>"
            "<span style='color:#059669; font-weight:700; font-size:0.82rem;'>✅ Deliverability check passed</span>"
            "<span style='color:#475569; font-size:0.8rem;'> — no spam triggers detected.</span></div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# STEP 4 — SCHEDULE BLOCK
# ---------------------------------------------------------------------------

def _render_schedule_step(n_recipients: int, num_touches: int) -> dict:
    """Render Step 4: Schedule. Returns dict with all scheduling parameters."""
    with st.container(border=True):
        st.markdown("#### Step 4 — Schedule")

        # Market / timezone
        db_default_market = get_config("default_market", "CA_EAST")
        market_keys = list(TARGET_MARKETS.keys())
        def_m_idx = market_keys.index(db_default_market) if db_default_market in market_keys else 0

        col_tm1, col_tm2 = st.columns([1.8, 1.2])
        with col_tm1:
            selected_market_key = st.selectbox(
                "Recipient market / timezone",
                options=market_keys,
                index=def_m_idx,
                format_func=lambda k: TARGET_MARKETS[k]["label"],
                key="cs_target_market",
                help="Send times auto-adapt to recipient business hours.",
            )
        with col_tm2:
            m_info = TARGET_MARKETS[selected_market_key]
            m_now = get_market_current_time(selected_market_key)
            is_open, _ = is_within_market_hours(selected_market_key)
            badge_html = (
                "<span style='background:#16A34A;color:white;font-size:0.72rem;font-weight:800;"
                "padding:2px 7px;border-radius:10px;'>OPEN</span>"
                if is_open else
                "<span style='background:#CA8A04;color:white;font-size:0.72rem;font-weight:800;"
                "padding:2px 7px;border-radius:10px;'>CLOSED</span>"
            )
            diff_str = get_time_difference_summary(selected_market_key)
            st.markdown(
                f"<div style='background:#FFFFFF;border:1px solid rgba(8,55,49,0.18);"
                f"border-radius:8px;padding:9px 12px;margin-top:5px;'>"
                f"<div style='font-size:0.75rem;color:#64748B;font-weight:700;'>LOCAL TIME {badge_html}</div>"
                f"<div style='font-weight:800;color:#083731;font-size:1rem;'>{m_now.strftime('%I:%M %p')}"
                f" <span style='font-size:0.75rem;color:#64748B;'>{m_now.strftime('%Z')}</span></div>"
                f"<div style='font-size:0.78rem;color:#475569;'>{diff_str}</div></div>",
                unsafe_allow_html=True,
            )

        # BCC notice
        bcc_conf = get_config("bcc_email", "")
        if bcc_conf:
            st.caption(f"📬 Compliance BCC active: `{bcc_conf}` (Settings → Advanced)")

        # Sending window preset
        db_enforce = (get_config("enforce_sending_window", "true") or "true").strip().lower() in ["true", "1", "yes"]
        db_days_raw = get_config("sending_days", "Monday, Tuesday, Wednesday, Thursday, Friday") or "Monday, Tuesday, Wednesday, Thursday, Friday"
        db_days_list = [d.strip() for d in db_days_raw.split(",") if d.strip()]
        db_start = (get_config("sending_start_time", "09:00") or "09:00").strip()
        db_end = (get_config("sending_end_time", "18:00") or "18:00").strip()

        if not db_enforce or (db_start == "00:00" and db_end in ["23:59", "24:00"] and len(db_days_list) >= 7):
            default_preset_idx = 1
        elif set(db_days_list) == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"} and db_start == "09:00" and db_end == "18:00":
            default_preset_idx = 0
        else:
            default_preset_idx = 2

        preset_choice = st.radio(
            "Sending schedule",
            ["Business Days (Mon–Fri, 9 AM–6 PM)", "24/7 Continuous", "Custom"],
            index=default_preset_idx,
            horizontal=True,
            key="cs_sched_preset",
        )

        if preset_choice.startswith("Business"):
            camp_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            camp_start = m_info.get("default_start", "09:00")
            camp_end = m_info.get("default_end", "17:00")
        elif preset_choice.startswith("24/7"):
            camp_days = list(WEEKDAY_NAMES)
            camp_start = "00:00"
            camp_end = "23:59"
        else:
            col_sd1, col_sd2, col_sd3 = st.columns([2, 1, 1])
            with col_sd1:
                camp_days = st.multiselect("Allowed days", WEEKDAY_NAMES,
                                           default=[d for d in db_days_list if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                                           key="cs_custom_days")
            with col_sd2:
                camp_start = st.text_input("Start time", value=db_start or "09:00", key="cs_custom_start")
            with col_sd3:
                camp_end = st.text_input("End time", value=db_end or "18:00", key="cs_custom_end")

        # Pacing
        pacing_opts = ["Send as fast as possible", "Spread evenly across active hours"]
        pacing = st.selectbox("Batch pacing", pacing_opts, key="cs_pacing")

        with st.expander("Advanced throttle tuning", expanded=False):
            col_j1, col_j2 = st.columns(2)
            with col_j1:
                use_jitter = st.checkbox("Add natural timing jitter (±20–90s)", value=True, key="cs_use_jitter",
                                         help="Prevents robotic fixed-second send patterns.")
            with col_j2:
                adv_gap = st.number_input("Custom minutes between sends", min_value=0.0, max_value=120.0,
                                          value=0.0 if "fast" in pacing.lower() else 5.0,
                                          step=1.0, key="cs_adv_gap",
                                          help="0 = immediate succession.")

        # Stagger params
        if "fast" in pacing.lower():
            span_hours = 0.0
            spacing_minutes = float(adv_gap) if adv_gap > 0 else 0.0
            stagger_mode_arg = "none" if adv_gap == 0 else "fixed_interval"
        else:
            span_hours = 4.0
            spacing_minutes = float(adv_gap) if adv_gap > 0 else 5.0
            stagger_mode_arg = "daily_window"

        # Live schedule preview card
        total_drafts = n_recipients * num_touches
        first_slot_str = "—"
        est_completion_str = "—"

        if n_recipients > 0:
            try:
                if selected_market_key != "LOCAL":
                    pairs = calculate_market_aware_schedule(
                        total_contacts=n_recipients, market_key_or_tz=selected_market_key,
                        stagger_mode=stagger_mode_arg, span_hours=float(span_hours),
                        spacing_minutes=float(spacing_minutes), days=camp_days,
                        start_time=camp_start, end_time=camp_end, use_jitter=False,
                    )
                    first_slot_str = pairs[0][0].strftime("%a %b %d, %I:%M %p")
                    est_completion_str = pairs[-1][0].strftime("%a %b %d, %I:%M %p")
                else:
                    slots = calculate_staggered_schedule(
                        total_contacts=n_recipients, stagger_mode=stagger_mode_arg,
                        base_dt=datetime.now(), span_hours=float(span_hours),
                        spacing_minutes=float(spacing_minutes), sending_days=camp_days,
                        start_time_str=camp_start, end_time_str=camp_end, use_jitter=False,
                    )
                    first_slot_str = slots[0].strftime("%a %b %d, %I:%M %p")
                    est_completion_str = slots[-1].strftime("%a %b %d, %I:%M %p")
            except Exception:
                pass

        smtp_accs = get_smtp_accounts(active_only=True)
        sending_mailbox = (smtp_accs[0].get("from_email") or smtp_accs[0].get("username") if smtp_accs
                           else get_config("sender_email") or "Default Mailbox")

        st.markdown(
            f"<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;"
            f"padding:14px;margin:12px 0;'>"
            f"<div style='font-weight:700;color:#0F172A;font-size:0.9rem;margin-bottom:10px;'>Schedule preview</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;'>"
            f"<div style='background:#fff;border:1px solid #E2E8F0;border-radius:8px;padding:10px;'>"
            f"<div style='font-size:0.72rem;color:#64748B;font-weight:700;text-transform:uppercase;'>Drafts to queue</div>"
            f"<div style='font-size:1.1rem;font-weight:800;color:#083731;'>{total_drafts}"
            f" <span style='font-size:0.78rem;font-weight:400;color:#64748B;'>({n_recipients} × {num_touches})</span></div></div>"
            f"<div style='background:#fff;border:1px solid #E2E8F0;border-radius:8px;padding:10px;'>"
            f"<div style='font-size:0.72rem;color:#64748B;font-weight:700;text-transform:uppercase;'>First dispatch slot</div>"
            f"<div style='font-size:0.9rem;font-weight:800;color:#083731;'>{first_slot_str}</div></div>"
            f"<div style='background:#fff;border:1px solid #E2E8F0;border-radius:8px;padding:10px;'>"
            f"<div style='font-size:0.72rem;color:#64748B;font-weight:700;text-transform:uppercase;'>Est. completion</div>"
            f"<div style='font-size:0.9rem;font-weight:800;color:#083731;'>{est_completion_str}</div></div>"
            f"<div style='background:#fff;border:1px solid #E2E8F0;border-radius:8px;padding:10px;'>"
            f"<div style='font-size:0.72rem;color:#64748B;font-weight:700;text-transform:uppercase;'>Sending mailbox</div>"
            f"<div style='font-size:0.88rem;font-weight:800;color:#083731;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
            f"{sending_mailbox}</div></div></div></div>",
            unsafe_allow_html=True,
        )

    return {
        "market_key": selected_market_key,
        "m_info": m_info,
        "camp_days": camp_days,
        "camp_start": camp_start,
        "camp_end": camp_end,
        "preset_choice": preset_choice,
        "stagger_mode_arg": stagger_mode_arg,
        "span_hours": span_hours,
        "spacing_minutes": spacing_minutes,
        "use_jitter": use_jitter,
    }


# ---------------------------------------------------------------------------
# GENERATION LOOP
# ---------------------------------------------------------------------------

def _resolve_all_recipients(crm_ids: list, manual_emails: list, contact_id_map: dict,
                             num_touches: int, save_manual: bool) -> list:
    """
    Build a unified recipient list of contact dicts.
    For manual emails: auto-upsert when multi-touch (sequence needs contact_id);
    for single touch only create if save_manual is checked.
    Returns list of dicts with 'contact' and 'source' keys.
    """
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
            continue  # dedup
        seen_emails.add(em_clean)

        existing = get_contact_by_email(em_clean)
        if existing:
            recipients.append({"contact": existing, "source": "crm_found"})
        elif save_manual or num_touches > 1:
            # Multi-touch ALWAYS needs a CRM record for sequence tracking
            new_id, _ = upsert_contact_by_email(name="", email=em_clean, company="")
            new_c = get_contact_by_id(new_id) or _stub_contact(em_clean)
            new_c["id"] = new_id
            recipients.append({"contact": new_c, "source": "manual_saved"})
        else:
            # Single touch, no save: use a stub (no CRM record)
            recipients.append({"contact": _stub_contact(em_clean), "source": "manual_stub"})

    return recipients


def _generate_drafts(recipients, touch_configs, sched_params, neg_keywords_setting, variable_fallback):
    """Core generation loop. Returns (created_pending, created_flagged, flagged_details)."""
    num_touches = len(touch_configs)
    n = len(recipients)
    selected_market_key = sched_params["market_key"]
    m_info = sched_params["m_info"]
    camp_days = sched_params["camp_days"]
    camp_start = sched_params["camp_start"]
    camp_end = sched_params["camp_end"]
    stagger_mode_arg = sched_params["stagger_mode_arg"]
    span_hours = sched_params["span_hours"]
    spacing_minutes = sched_params["spacing_minutes"]
    use_jitter = sched_params["use_jitter"]
    m_tz = m_info.get("timezone", "LOCAL")
    m_country = m_info.get("country", "")

    batch_seq_id = f"seq_{uuid.uuid4().hex[:8]}" if num_touches >= 2 else ""

    # Compute Touch 1 schedule slots
    if selected_market_key != "LOCAL":
        pairs = calculate_market_aware_schedule(
            total_contacts=n, market_key_or_tz=selected_market_key,
            stagger_mode=stagger_mode_arg, span_hours=float(span_hours),
            spacing_minutes=float(spacing_minutes), days=camp_days,
            start_time=camp_start, end_time=camp_end, use_jitter=use_jitter,
        )
        scheduled_dts = [p[1] for p in pairs]
    else:
        scheduled_dts = calculate_staggered_schedule(
            total_contacts=n, stagger_mode=stagger_mode_arg,
            base_dt=datetime.now(), span_hours=float(span_hours),
            spacing_minutes=float(spacing_minutes), sending_days=camp_days,
            start_time_str=camp_start, end_time_str=camp_end, use_jitter=use_jitter,
        )

    system_excluded = get_system_excluded_emails()
    created_pending = 0
    created_flagged = 0
    flagged_details = []
    registered_rules = 0

    # Save any "write your own" copies as templates if requested
    for cfg in touch_configs:
        if cfg.get("save_as_template") and cfg.get("custom_body") and cfg.get("new_template_name"):
            tpl_name = cfg["new_template_name"].strip()
            if tpl_name:
                new_tpl_id = create_template(tpl_name, cfg["custom_body"])
                cfg["template_id"] = new_tpl_id

    progress_bar = st.progress(0)

    for idx, rdata in enumerate(recipients):
        contact = rdata["contact"]
        email_clean = (contact.get("email") or "").strip().lower()

        if not email_clean or email_clean in system_excluded:
            progress_bar.progress((idx + 1) / n)
            continue

        t1_cfg = touch_configs[0]
        if t1_cfg.get("source_type") == "custom" or t1_cfg.get("custom_body"):
            raw_subj = t1_cfg["subject"].strip() or "Outreach"
            raw_body = t1_cfg["custom_body"]
        else:
            step1_tpl = get_template_by_id(t1_cfg["template_id"])
            raw_subj = t1_cfg["subject"].strip() or (step1_tpl["template_name"] if step1_tpl else "Outreach")
            raw_body = step1_tpl["body_content"] if step1_tpl else ""

        resolved_body = resolve_template(raw_body, contact)
        resolved_subj = parse_spintax(inject_variables(raw_subj, contact))

        # Variable fallback: replace remaining [Token] placeholders
        if _missing_tokens(resolved_subj) or _missing_tokens(resolved_body):
            resolved_subj = _apply_variable_fallback(resolved_subj, variable_fallback)
            resolved_body = _apply_variable_fallback(resolved_body, variable_fallback)

        final_html = format_email_html(resolved_body)
        combined_text = f"{resolved_subj} {final_html}"
        triggers = scan_all_negative_keywords(combined_text, neg_keywords_setting)

        if triggers:
            status = "Flagged"
            trig_str = ", ".join(f"'{t}'" for t in triggers)
            notes = f"Touch 1/{num_touches}: Flagged for trigger keyword(s): {trig_str}" if num_touches > 1 else f"Flagged for trigger keyword(s): {trig_str}"
            created_flagged += 1
            flagged_details.append({"recipient": email_clean, "touch": "Touch 1", "triggers": triggers})
        else:
            status = "Pending"
            notes = f"Sequence Touch 1/{num_touches}" if num_touches > 1 else (
                "One-Time Outreach" if n == 1 else "Batch Outreach"
            )
            created_pending += 1

        t1_dt = scheduled_dts[idx]
        t1_email_id = create_email(
            email_html=final_html,
            subject=resolved_subj,
            recipient=email_clean,
            status=status,
            revision_notes=notes,
            scheduled_time=t1_dt.strftime("%Y-%m-%d %H:%M:%S"),
            sequence_step=1,
            sequence_id=batch_seq_id,
            target_timezone=m_tz,
            target_country=m_country,
            market_key=selected_market_key,
        )

        contact_id = contact.get("id")

        if num_touches >= 2 and contact_id:
            t2_cfg = touch_configs[1]
            create_sequence_rule(
                sequence_id=batch_seq_id, contact_id=contact_id, contact_email=email_clean,
                step_number=2, delay_unit=t2_cfg["delay_unit"], delay_value=t2_cfg["delay_value"],
                template_id=t2_cfg.get("template_id"), custom_subject=t2_cfg["subject"],
                custom_body=t2_cfg.get("custom_body", ""), trigger_email_id=t1_email_id,
                target_timezone=m_tz, target_country=m_country, market_key=selected_market_key,
            )
            registered_rules += 1

        if num_touches == 3 and contact_id:
            t3_cfg = touch_configs[2]
            create_sequence_rule(
                sequence_id=batch_seq_id, contact_id=contact_id, contact_email=email_clean,
                step_number=3, delay_unit=t3_cfg["delay_unit"], delay_value=t3_cfg["delay_value"],
                template_id=t3_cfg.get("template_id"), custom_subject=t3_cfg["subject"],
                custom_body=t3_cfg.get("custom_body", ""), trigger_email_id=None,
                target_timezone=m_tz, target_country=m_country, market_key=selected_market_key,
            )
            registered_rules += 1

        progress_bar.progress((idx + 1) / n)

    return created_pending, created_flagged, flagged_details, registered_rules, batch_seq_id, touch_configs


# ---------------------------------------------------------------------------
# MAIN TAB RENDERER
# ---------------------------------------------------------------------------

def render_compose_tab(contacts_list=None, templates_list=None):
    """Render the unified Compose & Send tab."""
    if contacts_list is None:
        contacts_list = get_contacts()
    if templates_list is None:
        templates_list = get_templates()

    render_tab_header("✉️ Compose & Send", "Write, schedule, and launch outreach — single sends and multi-touch campaigns in one place.")

    # Translate any CRM nav session-state keys into compose keys
    _bootstrap_from_crm_nav()

    # Build active candidate pool
    system_excluded = get_system_excluded_emails()
    active_candidates = [
        c for c in contacts_list
        if c.get("status") not in ["Bounced", "Do Not Contact", "Closed Lost"]
        and not c.get("is_bounced")
        and (c.get("email") or "").strip().lower() not in system_excluded
    ]
    internal_excluded = [
        c for c in contacts_list
        if (c.get("email") or "").strip().lower() in system_excluded
    ]
    contact_id_map = {c["id"]: c for c in active_candidates}

    if internal_excluded:
        ex_samples = ", ".join(f"`{c.get('email')}`" for c in internal_excluded[:3])
        extra = f" (+{len(internal_excluded) - 3} more)" if len(internal_excluded) > 3 else ""
        st.markdown(
            f"<div style='background:rgba(8,55,49,0.04);border:1px solid rgba(8,55,49,0.16);"
            f"border-radius:8px;padding:7px 12px;margin-bottom:12px;font-size:0.81rem;color:#083731;'>"
            f"🛡️ <b>BCC / internal protection:</b> {len(internal_excluded)} address(es) "
            f"({ex_samples}{extra}) excluded from all campaigns.</div>",
            unsafe_allow_html=True,
        )

    # Template side-panel (collapsed by default)
    if templates_list:
        with st.expander(f"My Templates ({len(templates_list)})", expanded=False):
            for tpl in templates_list:
                col_n, col_p = st.columns([3, 1])
                with col_n:
                    st.markdown(f"**{tpl['template_name']}**")
                with col_p:
                    if st.button("Load →", key=f"load_tpl_side_{tpl['id']}", help="Load into Touch 1"):
                        st.session_state["cs_mode_1"] = "Use saved template"
                        st.session_state.setdefault("cs_touch_state", {})["cs_tpl_1"] = tpl["id"]
                        st.session_state["cs_touch_state"]["cs_mode_1"] = "Use saved template"
                        st.rerun()
    else:
        with st.expander("My Templates (0)", expanded=False):
            st.info("No templates yet. Create one in the Templates tab, or write your email directly below.")

    # ── Step 1: Recipients ─────────────────────────────────────────────────
    recip_result = _render_recipient_step(active_candidates, contact_id_map, system_excluded)
    crm_ids = recip_result["crm_ids"]
    manual_emails = recip_result["manual_emails"]
    total_recipients = len(crm_ids) + len(manual_emails)

    # ── Step 2: Sequence ───────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("#### Step 2 — Sequence")
        num_touches = st.radio(
            "How many touches?",
            options=[1, 2, 3],
            format_func=lambda n: SEQ_LABELS[n],
            index=st.session_state.get("cs_num_touches", 1) - 1,
            horizontal=True,
            key="cs_num_touches",
        )
        st.markdown(
            "<div style='font-size:0.8rem;color:#475569;margin-top:4px;padding:6px 10px;"
            "background:rgba(8,55,49,0.04);border-radius:6px;display:inline-block;'>"
            "ℹ️ Follow-ups automatically stop if the person replies.</div>",
            unsafe_allow_html=True,
        )

    # ── Step 3: Write each touch ───────────────────────────────────────────
    neg_keywords_setting = get_config("negative_keywords", "")
    template_options = {t["id"]: t["template_name"] for t in templates_list}

    # Pick a sample contact for live preview
    sample_contact = (
        (contact_id_map.get(crm_ids[0]) or get_contact_by_id(crm_ids[0]))
        if crm_ids
        else {"name": "Alex", "company": "Acme Corp", "email": "prospect@acme.com", "custom_variables_dict": {}}
    )

    touch_configs = []

    if num_touches == 1:
        with st.container(border=True):
            st.markdown("#### Step 3 — Compose")
            cfg1 = _render_touch_block(1, "Touch 1", template_options, sample_contact,
                                       neg_keywords_setting, DEFAULT_COPIES[1]["subj"], DEFAULT_COPIES[1]["body"])
            touch_configs.append(cfg1)
    else:
        touch_tab_titles = ["Touch 1 — Initial Pitch", "Touch 2 — Follow-Up"]
        if num_touches == 3:
            touch_tab_titles.append("Touch 3 — Final Note")

        with st.container(border=True):
            st.markdown("#### Step 3 — Compose")
            touch_ui_tabs = st.tabs(touch_tab_titles)

        with touch_ui_tabs[0]:
            cfg1 = _render_touch_block(1, "Touch 1", template_options, sample_contact,
                                       neg_keywords_setting, DEFAULT_COPIES[1]["subj"], DEFAULT_COPIES[1]["body"])
            touch_configs.append(cfg1)

        with touch_ui_tabs[1]:
            st.caption("Sent automatically if no reply received to Touch 1.")
            cfg2 = _render_touch_block(2, "Touch 2", template_options, sample_contact,
                                       neg_keywords_setting, DEFAULT_COPIES[2]["subj"], DEFAULT_COPIES[2]["body"],
                                       include_delay=True, default_delay_val=3)
            touch_configs.append(cfg2)

        if num_touches == 3:
            with touch_ui_tabs[2]:
                st.caption("Sent automatically if no reply received to Touch 2.")
                cfg3 = _render_touch_block(3, "Touch 3", template_options, sample_contact,
                                           neg_keywords_setting, DEFAULT_COPIES[3]["subj"], DEFAULT_COPIES[3]["body"],
                                           include_delay=True, default_delay_val=4)
                touch_configs.append(cfg3)

    # ── Step 4: Schedule ───────────────────────────────────────────────────
    sched_params = _render_schedule_step(total_recipients, num_touches)

    # ── Step 5: Send ───────────────────────────────────────────────────────
    variable_fallback = get_config("variable_fallback", "there") or "there"
    save_manual = st.session_state.get("cs_save_manual_as_contacts", False)

    # Check for any manual emails that need saving for multi-touch
    manual_no_crm_multi = (
        [e for e in manual_emails if not get_contact_by_email(e)]
        if num_touches > 1 else []
    )
    if manual_no_crm_multi:
        st.info(
            f"ℹ️ **Multi-touch note:** {len(manual_no_crm_multi)} manual email(s) will be added to your CRM "
            f"as new contacts so the sequence engine can track replies and cancel follow-ups automatically. "
            f"You can edit or remove them in the Contacts tab.",
            icon="📋",
        )

    has_recipients = total_recipients > 0

    if total_recipients == 1 and num_touches == 1:
        btn_label = "Send now (1 recipient, 1 touch)"
    elif total_recipients == 1:
        btn_label = f"Generate & Schedule ({num_touches}-touch sequence for 1 recipient)"
    elif has_recipients:
        btn_label = f"Generate & Schedule ({total_recipients * num_touches} drafts for {total_recipients} recipients)"
    else:
        btn_label = "Select recipients above to continue"

    generate_btn = st.button(
        btn_label,
        type="primary",
        use_container_width=True,
        disabled=not has_recipients,
        key="btn_compose_generate",
    )

    if not has_recipients:
        st.caption("Add at least one recipient in Step 1 to continue.")

    if generate_btn and has_recipients:
        with st.spinner(f"Generating outreach for {total_recipients} recipient(s)…"):
            # Sync schedule to DB
            _sync_sending_window_to_db(
                sched_params["preset_choice"], sched_params["camp_days"],
                sched_params["camp_start"], sched_params["camp_end"]
            )
            if num_touches >= 2:
                set_config("followup_delay_days", str(int(touch_configs[1]["delay_value"])))

            neg_keywords_setting = get_config("negative_keywords", "")

            # Resolve all recipients into contact dicts
            all_recipients = _resolve_all_recipients(
                crm_ids, manual_emails, contact_id_map, num_touches, save_manual
            )

            if not all_recipients:
                st.error("No valid recipients found after filtering.")
                return

            (created_pending, created_flagged, flagged_details,
             registered_rules, batch_seq_id, touch_configs) = _generate_drafts(
                all_recipients, touch_configs, sched_params, neg_keywords_setting, variable_fallback
            )

        trigger_toast("Drafts generated!", icon="🚀")
        st.session_state["scroll_to_review"] = True

        if num_touches > 1:
            create_notification(type="campaign", title="Campaign Sequence Generated",
                                message=f"Created {created_pending} Touch 1 draft(s) and {registered_rules} follow-up rule(s).")
            st.success(
                f"Done: {created_pending} pending draft(s) + {registered_rules} automated follow-up rule(s) registered. "
                f"{'(' + str(created_flagged) + ' flagged for review)' if created_flagged else ''}"
            )
            st.markdown(
                "<div style='background:#EFF6FF;border:1.5px solid #3B82F6;border-radius:10px;"
                "padding:12px 16px;margin:12px 0;'>"
                "<div style='font-weight:800;color:#1D4ED8;'>Automated follow-up sequence active</div>"
                f"<div style='color:#1E3A8A;font-size:0.85rem;margin-top:4px;'>"
                f"Touch 1 drafts are in the Review & Outbox tab. Once dispatched, the engine waits "
                f"{touch_configs[1]['delay_value']} {touch_configs[1]['delay_unit']} and auto-generates the follow-up "
                f"— only if the prospect hasn't replied.</div></div>",
                unsafe_allow_html=True,
            )
        else:
            create_notification(type="campaign", title="Drafts Generated",
                                message=f"Queued {created_pending} draft(s).")
            st.success(f"Done: {created_pending} pending draft(s) queued for review. "
                       f"{'(' + str(created_flagged) + ' flagged)' if created_flagged else ''}")
            st.info("Review and approve your drafts in the **Review & Outbox** tab.", icon="📥")

        if created_flagged > 0:
            st.warning(f"{created_flagged} draft(s) were flagged by the spam keyword shield:")
            for fd in flagged_details[:8]:
                trig_badges = ", ".join(f"`{t}`" for t in fd["triggers"])
                st.markdown(f"- **{fd['recipient']}** ({fd['touch']}): {trig_badges}")
