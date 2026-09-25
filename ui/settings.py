"""
ui/settings.py - Clean Settings Hub for Sellomize Reach.
Matches sellomize_reference.html:
- Mailboxes & warmup table/cards: Mailbox, Host, Daily limit, Warmup, Today's cap, Status.
- 4 configuration cards:
  1. Sending window (Mon-Fri 09:00-18:00, per-lead timezone, Host PC local time)
  2. Signature (Saved corporate HTML)
  3. Negative keywords (guarantee, 100% free, act now...)
  4. Send-now outside window policy (Hold to next window vs Send immediately)
- Independent worker status and credentials encrypted at rest.
"""

import streamlit as st
import html
import re
from datetime import datetime, timezone
import json
from typing import List, Dict, Any, Optional

from database import (
    get_all_configs,
    get_config,
    set_config,
    save_all_configs,
    get_smtp_accounts,
    reset_daily_smtp_limits,
    add_smtp_account,
    update_smtp_account,
    delete_smtp_account,
    get_warmup_info,
    get_effective_daily_limit,
    export_backup_data,
    import_backup_data,
    auto_save_backup,
    WEEKDAY_NAMES,
    DB_FILE,
)
from smtp_dispatcher import test_smtp_connection
from timezone_helper import TARGET_MARKETS, get_engine_now
from ui.components import trigger_toast
from config import (
    DEFAULT_SMTP_HOST,
    DEFAULT_SMTP_PORT,
    DEFAULT_DAILY_LIMIT,
    WORKER_POLL_INTERVAL_SECONDS,
    DEFAULT_START_TIME,
    DEFAULT_END_TIME,
    DEFAULT_DAYS,
    DEFAULT_MIN_JITTER,
    DEFAULT_MAX_JITTER,
    DEFAULT_TIMEZONE,
    DEFAULT_SPAM_SCORE_THRESHOLD,
    DEFAULT_NEGATIVE_KEYWORD_ACTION,
)

DEFAULT_SIGNATURE_TEMPLATE = """<div>
<table cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif; max-width:650px; color:#083731;">
  <tbody>
    <tr>
      <td style="padding-right:20px; vertical-align:top;">
        <img src="https://sellomize.com/wp-content/uploads/2026/05/cropped-amazon-aligators.png" width="110" style="display:block;" alt="Sellomize Logo">
        <br>
      </td>
      <td style="padding:0 20px; border-left:2px solid #FD4D1B; vertical-align:top;">
        <div style="font-size:20px; font-weight:bold; color:#083731;">Jack Connor</div>
        <div style="font-size:14px; color:#FD4D1B; margin:4px 0 8px;">Business Development Officer</div>
        <div style="font-size:14px; line-height:1.7;">
          <div><b>Sellomize</b></div>
          <div>Amazon Brand Management</div>
        </div>
        <div style="margin-top:10px; font-size:14px; line-height:1.7;">
          <div><a href="mailto:info@sellomize.com" style="color:#083731; text-decoration:none;">info@sellomize.com</a></div>
          <div>+1 646-351-0812</div>
          <div><a href="https://sellomize.com" target="_blank" style="color:#083731; text-decoration:none;">sellomize.com</a></div>
        </div>
      </td>
    </tr>
  </tbody>
</table>
</div>"""


def sync_sending_window_to_db(preset: str, days: list, start: str, end: str, db_path: str = DB_FILE):
    """Persist sending window configuration to SQLite system_config."""
    if preset.startswith("24/7 Continuous"):
        set_config("enforce_sending_window", "false", db_path=db_path)
        set_config("sending_days", ", ".join(WEEKDAY_NAMES), db_path=db_path)
        set_config("sending_start_time", "00:00", db_path=db_path)
        set_config("sending_end_time", "23:59", db_path=db_path)
    elif preset.startswith("Business Days"):
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


@st.dialog("➕ Connect Hostinger Mailbox")
def render_add_mailbox_dialog():
    """Modal dialog to add a new Hostinger SMTP mailbox."""
    with st.form("form_add_mailbox", clear_on_submit=True):
        st.markdown("#### Hostinger SMTP Details")
        name = st.text_input("Sender Display Name *", placeholder="e.g. Jack Connor | Sellomize")
        email = st.text_input("Hostinger Email Address *", placeholder="jack@sellomize.com")
        pw = st.text_input("Password *", type="password", help="Stored encrypted at rest using AES-Fernet.")

        c_host, c_port = st.columns([2, 1])
        with c_host:
            host = st.text_input("SMTP Host", value=DEFAULT_SMTP_HOST)
        with c_port:
            port = st.number_input("Port", value=DEFAULT_SMTP_PORT, step=1, help="465 for SSL, 587 for TLS")

        daily_limit = st.number_input("Target Daily Sending Limit", min_value=1, max_value=500, value=DEFAULT_DAILY_LIMIT)

        st.markdown("---")
        st.markdown("#### Automated Warmup Schedule")
        warmup_enabled = st.checkbox("Enable Automated Warmup for this Mailbox", value=False)
        w_c1, w_c2 = st.columns(2)
        with w_c1:
            w_start_lim = st.number_input("Starting Limit (Day 1)", min_value=1, max_value=100, value=5)
        with w_c2:
            w_inc = st.number_input("Daily Increment", min_value=1, max_value=50, value=3)

        if st.form_submit_button("Save & Connect Mailbox", type="primary", use_container_width=True):
            if not email.strip() or not pw.strip():
                st.error("Please provide both email address and password.")
                return
            today_str = datetime.now().strftime("%Y-%m-%d")
            add_smtp_account(
                name=name.strip() or email.strip(),
                email=email.strip(),
                password=pw.strip(),
                smtp_host=host.strip(),
                smtp_port=int(port),
                daily_limit=int(daily_limit),
                warmup_enabled=warmup_enabled,
                warmup_start_date=today_str,
                warmup_starting_limit=int(w_start_lim),
                warmup_daily_increment=int(w_inc),
                warmup_target_limit=int(daily_limit)
            )
            trigger_toast(f"Mailbox '{email}' added successfully!", icon="✅")
            st.rerun()


@st.dialog("✏️ Edit Mailbox")
def render_edit_mailbox_dialog(acc: Dict[str, Any]):
    """Modal dialog to edit or remove an existing mailbox."""
    aid = acc["id"]
    with st.form(f"form_edit_mb_{aid}"):
        st.markdown(f"#### Edit Mailbox #{aid}: {acc.get('email')}")
        name = st.text_input("Sender Display Name", value=acc.get("sender_name") or "")
        email = st.text_input("Email Address", value=acc.get("email") or "")
        new_pw = st.text_input("New Password (leave blank to keep unchanged)", type="password")

        c_host, c_port = st.columns([2, 1])
        with c_host:
            host = st.text_input("SMTP Host", value=acc.get("smtp_host") or DEFAULT_SMTP_HOST)
        with c_port:
            port = st.number_input("Port", value=int(acc.get("smtp_port") or DEFAULT_SMTP_PORT), step=1)

        daily_limit = st.number_input("Target Daily Limit", min_value=1, max_value=500, value=int(acc.get("daily_limit") or DEFAULT_DAILY_LIMIT))

        st.markdown("---")
        st.markdown("#### Automated Warmup Schedule")
        is_warmup = bool(acc.get("warmup_enabled"))
        warmup_enabled = st.checkbox("Enable Automated Warmup", value=is_warmup)
        w_c1, w_c2 = st.columns(2)
        with w_c1:
            w_start_lim = st.number_input("Starting Limit", min_value=1, max_value=100, value=int(acc.get("warmup_starting_limit") or 5))
        with w_c2:
            w_inc = st.number_input("Daily Increment", min_value=1, max_value=50, value=int(acc.get("warmup_daily_increment") or 3))

        c1, c2 = st.columns(2)
        with c1:
            save_clicked = st.form_submit_button("Update Mailbox", type="primary", use_container_width=True)
        with c2:
            del_clicked = st.form_submit_button("Delete Mailbox", use_container_width=True)

        if save_clicked:
            up_kwargs = {
                "account_id": aid,
                "sender_name": name.strip(),
                "email": email.strip(),
                "smtp_host": host.strip(),
                "smtp_port": int(port),
                "daily_limit": int(daily_limit),
                "warmup_enabled": warmup_enabled,
                "warmup_starting_limit": int(w_start_lim),
                "warmup_daily_increment": int(w_inc),
                "warmup_target_limit": int(daily_limit),
            }
            if new_pw.strip():
                up_kwargs["password"] = new_pw.strip()
            update_smtp_account(**up_kwargs)
            trigger_toast(f"Mailbox #{aid} updated.", icon="✅")
            st.rerun()

        if del_clicked:
            delete_smtp_account(aid)
            trigger_toast(f"Mailbox #{aid} removed.", icon="🗑️")
            st.rerun()


def render_settings_tab():
    """Render Settings matching sellomize_reference.html."""
    # Live Engine Clock Banner (Locked to UTC+5)
    local_now = get_engine_now()
    pc_time_formatted = local_now.strftime("%I:%M %p")
    st.info(f"🕒 **Engine Timeframe Locked to UTC+5:** **{pc_time_formatted} UTC+5** — Scheduled outreach, queue pacing, and sending windows strictly follow this UTC+5 timeframe.")

    # =========================================================================
    # SECTION 1: MAILBOXES & WARMUP
    # =========================================================================
    st.markdown("<h3 class='sec' style='font-size:17px; font-weight:700; color:#083731; margin-bottom:8px;'>Mailboxes &amp; warmup</h3>", unsafe_allow_html=True)

    smtp_accounts = get_smtp_accounts(active_only=False)

    top_mb_c1, top_mb_c2, top_mb_c3 = st.columns([2.2, 1.2, 1.1], vertical_alignment="center")
    with top_mb_c1:
        st.markdown(f"<span style='font-size:12px; color:#64748B;'>{len(smtp_accounts)} connected Hostinger mailbox{'es' if len(smtp_accounts) != 1 else ''}</span>", unsafe_allow_html=True)
    with top_mb_c2:
        if st.button("🔄 Reset Daily Limits", use_container_width=True, key="btn_reset_daily_limits", help="Instantly reset sent_today to 0 for all connected mailboxes."):
            reset_daily_smtp_limits(force=True)
            trigger_toast("Daily sending limits successfully reset to 0!", icon="🔄")
            st.rerun()
    with top_mb_c3:
        if st.button("➕ Connect Mailbox", type="primary", use_container_width=True, key="btn_open_add_mb"):
            render_add_mailbox_dialog()

    if not smtp_accounts:
        st.info("No mailboxes connected yet. Click '➕ Connect Mailbox' to add your Hostinger account.")
    else:
        for acc in smtp_accounts:
            aid = acc["id"]
            aname = acc.get("sender_name") or acc.get("email")
            aemail = acc.get("email") or ""
            ahost = acc.get("smtp_host") or DEFAULT_SMTP_HOST
            aport = acc.get("smtp_port") or DEFAULT_SMTP_PORT
            d_limit = acc.get("daily_limit") or DEFAULT_DAILY_LIMIT

            w_info = get_warmup_info(acc)
            eff_limit = w_info["effective_limit"]
            sent_today = acc.get("sent_today", 0)

            if w_info["is_warmup"]:
                warmup_html = f'<span class="pill p-warm">warmup {w_info["day_num"]}/14</span>'
                cap_display = f'<span class="pill p-warm">{eff_limit}</span>'
            else:
                warmup_html = "done"
                cap_display = f"<b>{eff_limit}</b>"

            status_pill = '<span class="pill p-pass">connected</span>'

            with st.container(border=True):
                c_mb_info, c_mb_lim, c_mb_warm, c_mb_cap, c_mb_stat, c_mb_test, c_mb_edit = st.columns(
                    [2.6, 0.9, 1.1, 0.9, 1.0, 1.3, 0.9],
                    vertical_alignment="center"
                )
                with c_mb_info:
                    st.markdown(f"""
                    <div>
                        <strong style="color:#083731; font-size:14px;">{html.escape(aemail)}</strong>
                        <div style="font-size:12px; color:#64748B; font-family:monospace;">{html.escape(ahost)}:{aport}</div>
                    </div>
                    """, unsafe_allow_html=True)
                with c_mb_lim:
                    st.markdown(f"<div style='font-size:13px; color:#475569;'>Limit: <b>{d_limit}</b></div>", unsafe_allow_html=True)
                with c_mb_warm:
                    st.markdown(f"<div style='font-size:13px;'>{warmup_html}</div>", unsafe_allow_html=True)
                with c_mb_cap:
                    st.markdown(f"<div style='font-size:13px;'>Cap: {cap_display}<div style='font-size:11px; color:#64748B; margin-top:2px;'>Sent: <b>{sent_today}</b>/{eff_limit}</div></div>", unsafe_allow_html=True)
                with c_mb_stat:
                    st.markdown(f"<div style='text-align:center;'>{status_pill}</div>", unsafe_allow_html=True)
                with c_mb_test:
                    if st.button("🧪 Test", key=f"test_conn_{aid}", use_container_width=True):
                        with st.spinner(f"Verifying Hostinger SMTP credentials for {aemail}..."):
                            is_ok, err_msg = test_smtp_connection(acc, timeout=5)
                            if is_ok:
                                trigger_toast(f"Verified {aemail}! Hostinger SMTP connected.", icon="✅")
                            else:
                                st.error(f"Connection failed: {err_msg}")
                with c_mb_edit:
                    if st.button("✏️ Edit", key=f"edit_mb_{aid}", use_container_width=True):
                        render_edit_mailbox_dialog(acc)

    st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:22px 0 16px;'>", unsafe_allow_html=True)

    # =========================================================================
    # SECTION 2: 4 CONFIGURATION CARDS (Matching Reference Grid)
    # =========================================================================
    st.markdown("<h3 class='sec' style='font-size:17px; font-weight:700; color:#083731; margin-bottom:12px;'>Configuration</h3>", unsafe_allow_html=True)

    curr_start = get_config("sending_start_time", DEFAULT_START_TIME) or DEFAULT_START_TIME
    curr_end = get_config("sending_end_time", DEFAULT_END_TIME) or DEFAULT_END_TIME
    curr_days_str = get_config("sending_days", ",".join(DEFAULT_DAYS)) or ",".join(DEFAULT_DAYS)
    curr_days = [d.strip() for d in curr_days_str.split(",") if d.strip()]
    curr_tz = get_config("default_timezone", "Asia/Karachi") or "Asia/Karachi"
    curr_min_j = int(get_config("jitter_min_seconds", str(DEFAULT_MIN_JITTER)) or DEFAULT_MIN_JITTER)
    curr_max_j = int(get_config("jitter_max_seconds", str(DEFAULT_MAX_JITTER)) or DEFAULT_MAX_JITTER)
    curr_send_now_policy = get_config("send_now_policy", "immediate") or "immediate"
    saved_sig = get_config("signature_html", "") or DEFAULT_SIGNATURE_TEMPLATE
    saved_neg = get_config("negative_keywords", "guarantee, 100% free, act now, urgent, winner, make money, cash") or ""
    saved_action = get_config("negative_keywords_action", DEFAULT_NEGATIVE_KEYWORD_ACTION) or DEFAULT_NEGATIVE_KEYWORD_ACTION
    saved_threshold = int(get_config("spam_score_threshold", str(DEFAULT_SPAM_SCORE_THRESHOLD)) or DEFAULT_SPAM_SCORE_THRESHOLD)

    enforce_win = (get_config("enforce_sending_window", "false") or "false").lower() in ["true", "1", "yes"]
    win_summary = "Anytime 24/7 (Window Cancelled) · Daily limit enforced" if not enforce_win else f"Restricted: {curr_start}–{curr_end} · {curr_tz}"
    policy_summary = "Send immediately (Window cancelled)" if curr_send_now_policy == "immediate" else "Hold to next window"
    neg_summary = (saved_neg[:35] + "…") if len(saved_neg) > 35 else (saved_neg or "None")

    card_c1, card_c2 = st.columns(2)
    with card_c1:
        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="font-weight:600; color:#083731;">🕒 Dispatch schedule</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">{win_summary}</div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("Configure Dispatch Window & Schedule Mode", expanded=False):
            with st.form("form_sending_win"):
                win_mode = st.radio(
                    "Schedule Mode",
                    ["24/7 Anytime (Window Cancelled — Recommended)", "Restricted Hours Window (e.g. 09:00 - 18:00)"],
                    index=0 if not enforce_win else 1,
                    help="When Window is cancelled, you can schedule and send outreach at any time within the day while strictly respecting mailbox daily limits."
                )

                w_c1, w_c2 = st.columns(2)
                with w_c1:
                    start_time_val = st.text_input("Daily Start Time (HH:MM)", value=curr_start)
                with w_c2:
                    end_time_val = st.text_input("Daily End Time (HH:MM)", value=curr_end)

                st.markdown("**Default Timezone:**")
                tz_opts = ["Asia/Karachi", "UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Dubai", "Asia/Singapore", "Asia/Kolkata", "LOCAL"]
                tz_idx = tz_opts.index(curr_tz) if curr_tz in tz_opts else 0
                default_tz_val = st.selectbox(
                    "Default Timezone",
                    tz_opts,
                    index=tz_idx,
                    format_func=lambda x: "Asia/Karachi (UTC+5 — Engine Standard)" if x == "Asia/Karachi" else ("LOCAL (Host System Time)" if x == "LOCAL" else x)
                )

                if st.form_submit_button("Save Dispatch Schedule", type="primary", use_container_width=True):
                    is_enforce = "Restricted" in win_mode
                    set_config("enforce_sending_window", "true" if is_enforce else "false")
                    set_config("schedule_mode", "adaptive_multi_country" if is_enforce else "continuous")
                    set_config("sending_start_time", start_time_val.strip())
                    set_config("sending_end_time", end_time_val.strip())
                    set_config("default_timezone", default_tz_val.strip())
                    trigger_toast("Dispatch schedule updated!", icon="💾")
                    st.rerun()

        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="font-weight:600; color:#083731;">🛡️ Negative keywords</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">{neg_summary}</div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("Configure Negative Keywords & Spam Rules", expanded=False):
            with st.form("form_spam_cfg"):
                neg_kw_input = st.text_area("Custom Negative Keywords (comma-separated)", value=saved_neg, height=100)
                action_options = ["warn", "block"]
                act_idx = 0 if saved_action == "warn" else 1
                action_val = st.selectbox("Action on Match", action_options, index=act_idx, format_func=lambda x: "Warn only (highlight in editor)" if x == "warn" else "Block queueing (hard stop)")

                if st.form_submit_button("Save Spam Rules", type="primary", use_container_width=True):
                    set_config("negative_keywords", neg_kw_input.strip())
                    set_config("negative_keywords_action", action_val)
                    trigger_toast("Spam rules updated!", icon="🛡️")
                    st.rerun()

    with card_c2:
        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="font-weight:600; color:#083731;">✒️ Signature</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">Saved corporate HTML</div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("Configure Corporate Signature", expanded=False):
            with st.form("form_sig_cfg"):
                new_sig = st.text_area("HTML Signature Code", value=saved_sig, height=160)
                st.markdown("**Live Preview:**")
                st.markdown(f"<div style='border:1px solid #E2E8F0; border-radius:6px; padding:10px;'>{new_sig}</div>", unsafe_allow_html=True)
                if st.form_submit_button("Save Signature", type="primary", use_container_width=True):
                    set_config("signature_html", new_sig.strip())
                    trigger_toast("Signature updated!", icon="✒️")
                    st.rerun()

        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="font-weight:600; color:#083731;">⚡ Send-now policy</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">{policy_summary}</div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("Configure Send-Now Policy", expanded=False):
            with st.form("form_sn_policy"):
                send_now_options = ["immediate", "window"]
                sn_idx = 0 if curr_send_now_policy == "immediate" else 1
                send_now_val = st.radio(
                    "When operator clicks 'Send Now':",
                    send_now_options,
                    index=sn_idx,
                    format_func=lambda x: "Send immediately via direct SMTP (Window cancelled)" if x == "immediate" else "Hold to next window slot"
                )
                if st.form_submit_button("Save Policy", type="primary", use_container_width=True):
                    set_config("send_now_policy", send_now_val)
                    trigger_toast("Send-now policy updated!", icon="💾")
                    st.rerun()

    # Row 3: Outbound BCC Compliance & Anti-Spam Human Pacing
    card_c3, card_c4 = st.columns(2)
    saved_bcc = get_config("bcc_email", "") or ""
    bcc_parts = [e.strip() for e in re.split(r'[,;]+', saved_bcc) if e.strip()]
    if bcc_parts:
        bcc_count_lbl = f"{len(bcc_parts)} address{'es' if len(bcc_parts) != 1 else ''}"
        bcc_summary = f"{', '.join(bcc_parts)} ({bcc_count_lbl})"
        if len(bcc_summary) > 42:
            bcc_summary = bcc_summary[:39] + "…"
    else:
        bcc_summary = "None configured (direct to lead only)"

    with card_c3:
        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="font-weight:600; color:#083731;">📬 Outbound BCC &amp; CRM tracking</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">{bcc_summary}</div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("Configure Global BCC Archival & CRM Tracking", expanded=False):
            with st.form("form_bcc_cfg"):
                st.caption(
                    "Every outgoing email dispatched via Hostinger SMTP or Desktop Outlook will automatically send a hidden BCC copy to these addresses. "
                    "Ideal for CRM logging (HubSpot, Salesforce, Pipedrive) or internal record keeping. **Supports 1, 2, or more comma-separated addresses**."
                )
                new_bcc = st.text_input(
                    "Global BCC Email Address(es)",
                    value=saved_bcc,
                    placeholder="e.g. audit@sellomize.com, crm-sync@hubspot.com",
                    help="Enter 1 or more email addresses separated by commas (e.g. email1@brand.com, email2@brand.com)"
                )
                parsed_bccs = [e.strip() for e in re.split(r'[,;]+', new_bcc) if e.strip()]
                if parsed_bccs:
                    st.markdown(f"""
                    <div style="background:#F0FDF4; border:1px solid #BBF7D0; border-radius:6px; padding:6px 12px; margin:6px 0 10px;">
                        <span style="font-size:0.82rem; color:#15803D; font-weight:700;">Active Outbound BCC ({len(parsed_bccs)} address{'es' if len(parsed_bccs) != 1 else ''}):</span>
                        <span style="font-size:0.82rem; color:#0F172A; font-family:monospace;"> {', '.join(parsed_bccs)}</span>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.caption("ℹ️ No BCC address configured. Outbound emails will only be sent directly to the lead.")

                if st.form_submit_button("Save BCC Settings", type="primary", use_container_width=True):
                    clean_bccs = ", ".join(parsed_bccs)
                    set_config("bcc_email", clean_bccs)
                    auto_save_backup()
                    trigger_toast("BCC configuration updated & saved!", icon="📬")
                    st.rerun()

    curr_min_j = int(get_config("min_delay_seconds", str(DEFAULT_MIN_JITTER)) or DEFAULT_MIN_JITTER)
    curr_max_j = int(get_config("max_delay_seconds", str(DEFAULT_MAX_JITTER)) or DEFAULT_MAX_JITTER)
    jitter_summary = f"{curr_min_j}s – {curr_max_j}s random human delay between dispatches"

    with card_c4:
        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="font-weight:600; color:#083731;">⏱️ Anti-spam pacing &amp; jitter</div>
            <div style="font-size:12px; color:var(--muted); margin-top:4px;">{jitter_summary}</div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander("Configure Anti-Spam Sending Pacing", expanded=False):
            with st.form("form_jitter_cfg"):
                st.caption("Adds randomized human-like delays between consecutive dispatches to prevent spam filter triggers.")
                j_c1, j_c2 = st.columns(2)
                with j_c1:
                    new_min_j = st.number_input("Min Delay (seconds)", min_value=5, max_value=300, value=curr_min_j)
                with j_c2:
                    new_max_j = st.number_input("Max Delay (seconds)", min_value=10, max_value=600, value=curr_max_j)
                if st.form_submit_button("Save Pacing", type="primary", use_container_width=True):
                    if new_min_j > new_max_j:
                        new_min_j, new_max_j = new_max_j, new_min_j
                    set_config("min_delay_seconds", str(int(new_min_j)))
                    set_config("max_delay_seconds", str(int(new_max_j)))
                    auto_save_backup()
                    trigger_toast("Anti-spam pacing updated & saved!", icon="⏱️")
                    st.rerun()

    # =========================================================================
    # SECTION 3: INDEPENDENT DISPATCH WORKER STATUS
    # =========================================================================
    st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:16px 0;'>", unsafe_allow_html=True)
    with st.expander("⚡ Independent Background Sender Daemon Status", expanded=False):
        heartbeat_str = get_config("worker_heartbeat", "")
        worker_active = False
        last_hb_display = "Never started"

        if heartbeat_str:
            try:
                hb_dt = datetime.strptime(heartbeat_str[:19], "%Y-%m-%d %H:%M:%S")
                sec_diff = (datetime.now() - hb_dt).total_seconds()
                if sec_diff < 45:
                    worker_active = True
                last_hb_display = f"{int(sec_diff)}s ago"
            except Exception:
                last_hb_display = heartbeat_str

        w_col1, w_col2, w_col3 = st.columns(3)
        with w_col1:
            st.metric("Worker Status", "🟢 Running" if worker_active else "⚪ Stopped")
        with w_col2:
            st.metric("Last Heartbeat", last_hb_display)
        with w_col3:
            st.metric("Poll Interval", f"{WORKER_POLL_INTERVAL_SECONDS} seconds")

        st.caption("To start the background daemon, run `run_scheduler.bat` or `python scheduler.py` in the terminal.")

    # =========================================================================
    # SECTION 4: DATA PERSISTENCE & CRM BACKUP / RESTORE
    # =========================================================================
    st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:16px 0;'>", unsafe_allow_html=True)
    with st.expander("💾 Data Persistence & CRM Backup / Restore", expanded=True):
        st.markdown("""
        <div style="font-size:13px; color:#475569; margin-bottom:12px;">
            <b>Permanent Data Protection:</b> All your connected mailboxes, custom signatures, email templates, and CRM contacts are automatically protected with snapshots so cloud container reboots never erase your outreach configuration.
        </div>
        """, unsafe_allow_html=True)

        backup_dict = export_backup_data()
        mb_cnt = len(backup_dict.get("smtp_accounts", []))
        tpl_cnt = len(backup_dict.get("templates", []))
        c_cnt = len(backup_dict.get("contacts", []))
        has_sig = bool((backup_dict.get("system_config", {}).get("signature_html") or "").strip())

        s_col1, s_col2, s_col3, s_col4 = st.columns(4)
        with s_col1:
            st.metric("Connected Mailboxes", f"{mb_cnt}")
        with s_col2:
            st.metric("Saved Templates", f"{tpl_cnt}")
        with s_col3:
            st.metric("CRM Contacts", f"{c_cnt}")
        with s_col4:
            st.metric("Signature", "✅ Configured" if has_sig else "⚠️ Default")

        b_c1, b_c2 = st.columns([1.2, 1.2])
        with b_c1:
            backup_json_str = json.dumps(backup_dict, indent=2)
            st.download_button(
                "📥 Download Backup File (JSON)",
                data=backup_json_str,
                file_name=f"sellomize_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                mime="application/json",
                use_container_width=True,
                help="Download a portable snapshot of all your settings, mailboxes, templates, and leads"
            )
        with b_c2:
            if st.button("🔄 Sync & Save Auto-Backup Now", key="btn_manual_sync_backup", use_container_width=True):
                auto_save_backup()
                trigger_toast("Auto-backup snapshot saved successfully!", icon="💾")

        st.markdown("<div style='font-size:12px; font-weight:600; color:#083731; margin-top:14px;'>Upload Backup to Restore:</div>", unsafe_allow_html=True)
        up_backup = st.file_uploader("Restore from JSON backup file", type=["json"], key="up_backup_json", label_visibility="collapsed")
        if up_backup:
            if st.button("🚀 Restore Data from File", key="btn_apply_restore_file", type="primary", use_container_width=True):
                try:
                    loaded_data = json.load(up_backup)
                    ok, msg = import_backup_data(loaded_data)
                    if ok:
                        auto_save_backup()
                        trigger_toast(f"Restore complete! {msg}", icon="✅")
                        st.rerun()
                    else:
                        st.error(f"Restore failed: {msg}")
                except Exception as e:
                    st.error(f"Invalid backup file format: {e}")
