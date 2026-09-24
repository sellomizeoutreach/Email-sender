"""
ui/settings.py - Clean Settings Hub for Sellomize Reach.
Section C6 & Part B of Complete Restructure Spec.

Sub-tabs:
1. 📧 Hostinger Mailboxes (Hostinger SMTP accounts, Fernet encryption, 5s connection test, warmup settings)
2. 🕒 Sending Window (Start/End time, active days, default timezone, dispatch jitter, send-now policy)
3. ✒️ Signatures (Corporate HTML signature editor, live preview)
4. 🛡️ Deliverability & Spam Rules (Custom negative keywords, action on match: warn/block, score threshold)
5. ⚡ Dispatch Worker Status (Heartbeat, running/stopped badge, poll interval, worker instructions)
"""

import streamlit as st
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from database import (
    get_all_configs,
    get_config,
    set_config,
    save_all_configs,
    get_smtp_accounts,
    add_smtp_account,
    update_smtp_account,
    delete_smtp_account,
    get_warmup_info,
    get_effective_daily_limit,
    WEEKDAY_NAMES,
    DB_FILE,
)
from smtp_dispatcher import test_smtp_connection
from timezone_helper import TARGET_MARKETS
from ui.components import render_tab_header, trigger_toast
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
        name = st.text_input("Sender Display Name *", placeholder="e.g. Elena Rostova | Sellomize")
        email = st.text_input("Hostinger Email Address *", placeholder="elena@yourdomain.com")
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
    """Render the definitive Settings Hub."""
    render_tab_header(
        "⚙️ System Settings",
        "Configure Hostinger mailboxes, sending windows, corporate signatures, and deliverability spam guard rules."
    )

    settings_sub_tabs = st.tabs([
        "📧 Hostinger Mailboxes",
        "🕒 Sending Window",
        "✒️ Signatures",
        "🛡️ Negative Keywords & Spam Rules",
        "⚡ Worker Status"
    ])

    # ==========================================================================
    # SUB-TAB 1: 📧 MAILBOXES
    # ==========================================================================
    with settings_sub_tabs[0]:
        st.markdown("### Hostinger Mailbox Accounts")
        st.caption("Add and manage your Hostinger email accounts. Passwords are encrypted at rest using AES-Fernet.")

        smtp_accounts = get_smtp_accounts(active_only=False)

        top_mb_c1, top_mb_c2 = st.columns([3, 1])
        with top_mb_c1:
            st.markdown(f"**Connected Mailboxes ({len(smtp_accounts)}):**")
        with top_mb_c2:
            if st.button("➕ Connect Mailbox", type="primary", use_container_width=True):
                render_add_mailbox_dialog()

        if not smtp_accounts:
            st.info("No mailboxes connected yet. Click '➕ Connect Mailbox' to add your Hostinger account.")
        else:
            for acc in smtp_accounts:
                aid = acc["id"]
                aname = acc.get("sender_name") or acc.get("email")
                aemail = acc.get("email")
                ahost = acc.get("smtp_host") or DEFAULT_SMTP_HOST
                aport = acc.get("smtp_port") or DEFAULT_SMTP_PORT
                sent_today = acc.get("sent_today", 0)

                w_info = get_warmup_info(acc)
                eff_limit = w_info["effective_limit"]
                warmup_status = f"🔥 Warmup Day {w_info['day_num']} (Limit: {eff_limit}/day)" if w_info["is_warmup"] else f"Standard Limit: {eff_limit}/day"

                with st.container():
                    st.markdown(f"""
                    <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.14); border-radius:8px; padding:14px 16px; margin-bottom:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong style="color:#083731; font-size:1.05rem;">{aname}</strong>
                            <span style="font-size:0.8rem; color:#475569;">{ahost}:{aport}</span>
                        </div>
                        <div style="color:#1E293B; font-size:0.9rem;">📧 {aemail}</div>
                        <div style="font-size:0.85rem; color:#0369A1; font-weight:600; margin-top:4px;">
                            {warmup_status} — {sent_today}/{eff_limit} sent today
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    c_act1, c_act2, c_act3 = st.columns([1.2, 1, 4])
                    with c_act1:
                        if st.button("🧪 Test Connection", key=f"test_conn_{aid}", use_container_width=True):
                            with st.spinner("Testing SMTP connection to Hostinger (5s timeout)..."):
                                is_ok, err_msg = test_smtp_connection(acc, timeout=5)
                                if is_ok:
                                    st.success("✅ Connection Successful! Hostinger SMTP credentials verified.")
                                else:
                                    st.error(f"❌ Connection Failed: {err_msg}")
                    with c_act2:
                        if st.button("✏️ Edit", key=f"edit_mb_{aid}", use_container_width=True):
                            render_edit_mailbox_dialog(acc)

    # ==========================================================================
    # SUB-TAB 2: 🕒 SENDING WINDOW
    # ==========================================================================
    with settings_sub_tabs[1]:
        st.markdown("### Outreach Sending Window")
        local_now = datetime.now()
        pc_time_formatted = local_now.strftime("%I:%M %p")
        tz_name = local_now.astimezone().tzname() or "Local Time"
        st.info(f"💻 **Detected Host PC Local Time:** **{pc_time_formatted}** ({tz_name}) — Scheduled outreach and sending windows evaluate against your computer's local clock.")

        curr_start = get_config("sending_start_time", DEFAULT_START_TIME) or DEFAULT_START_TIME
        curr_end = get_config("sending_end_time", DEFAULT_END_TIME) or DEFAULT_END_TIME
        curr_days_str = get_config("sending_days", ",".join(DEFAULT_DAYS)) or ",".join(DEFAULT_DAYS)
        curr_days = [d.strip() for d in curr_days_str.split(",") if d.strip()]
        curr_tz = get_config("default_timezone", "LOCAL") or "LOCAL"
        curr_min_j = int(get_config("jitter_min_seconds", str(DEFAULT_MIN_JITTER)) or DEFAULT_MIN_JITTER)
        curr_max_j = int(get_config("jitter_max_seconds", str(DEFAULT_MAX_JITTER)) or DEFAULT_MAX_JITTER)
        curr_send_now_policy = get_config("send_now_policy", "immediate") or "immediate"

        with st.form("form_sending_window"):
            w_c1, w_c2 = st.columns(2)
            with w_c1:
                start_time_val = st.text_input("Daily Start Time (HH:MM)", value=curr_start, help="Outreach dispatches will not begin before this time.")
            with w_c2:
                end_time_val = st.text_input("Daily End Time (HH:MM)", value=curr_end, help="Outreach dispatches will strictly stop at this cutoff time (e.g. 18:00).")

            st.markdown("**Active Sending Days:**")
            selected_days = []
            day_cols = st.columns(7)
            for i, day in enumerate(WEEKDAY_NAMES):
                with day_cols[i]:
                    if st.checkbox(day[:3], value=(day in curr_days), key=f"win_day_{day}"):
                        selected_days.append(day)

            st.markdown("---")
            st.markdown("**Default Timezone & Pacing Jitter:**")
            tz_c1, tz_c2, tz_c3 = st.columns(3)
            with tz_c1:
                tz_opts = ["LOCAL", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Dubai", "Asia/Singapore", "Asia/Kolkata", "UTC"]
                tz_idx = tz_opts.index(curr_tz) if curr_tz in tz_opts else 0
                default_tz_val = st.selectbox("Default Timezone", tz_opts, index=tz_idx, format_func=lambda x: "LOCAL (Host PC Time)" if x == "LOCAL" else x)
            with tz_c2:
                min_jitter_val = st.number_input("Min Dispatch Delay (seconds)", min_value=5, max_value=600, value=curr_min_j)
            with tz_c3:
                max_jitter_val = st.number_input("Max Dispatch Delay (seconds)", min_value=5, max_value=600, value=curr_max_j)

            st.markdown("---")
            st.markdown("**Send-Now Policy:**")
            send_now_options = ["immediate", "window"]
            sn_idx = 0 if curr_send_now_policy == "immediate" else 1
            send_now_val = st.radio(
                "When operator clicks 'Send Now' outside active hours:",
                send_now_options,
                index=sn_idx,
                format_func=lambda x: "Send immediately via direct SMTP (override window)" if x == "immediate" else "Queue for next valid window opening"
            )

            if st.form_submit_button("Save Sending Window Settings", type="primary", use_container_width=True):
                set_config("sending_start_time", start_time_val.strip())
                set_config("sending_end_time", end_time_val.strip())
                set_config("sending_days", ",".join(selected_days))
                set_config("default_timezone", default_tz_val.strip())
                set_config("jitter_min_seconds", str(min_jitter_val))
                set_config("jitter_max_seconds", str(max_jitter_val))
                set_config("send_now_policy", send_now_val)
                trigger_toast("Sending window settings saved!", icon="💾")
                st.rerun()

    # ==========================================================================
    # SUB-TAB 3: ✒️ SIGNATURES
    # ==========================================================================
    with settings_sub_tabs[2]:
        st.markdown("### Corporate Email Signature")
        st.caption("Clean HTML signature appended to cold outreach messages.")

        saved_sig = get_config("signature_html", "") or (
            "<p style='font-size:0.9rem; color:#475569;'>Best regards,<br>"
            "<strong>Jack Connor</strong><br>"
            "Outreach Director | Sellomize<br>"
            "<a href='https://sellomize.com' style='color:#083731; text-decoration:none;'>sellomize.com</a></p>"
        )

        with st.form("form_signature"):
            new_sig = st.text_area("HTML Signature Code", value=saved_sig, height=180)
            st.markdown("**Live Signature Preview:**")
            st.markdown(
                f"""<div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.15); border-radius:8px; padding:14px; margin-bottom:10px;">
                    {new_sig}
                </div>""",
                unsafe_allow_html=True
            )
            if st.form_submit_button("Save Signature", type="primary", use_container_width=True):
                set_config("signature_html", new_sig.strip())
                trigger_toast("Signature updated successfully!", icon="✒️")
                st.rerun()

    # ==========================================================================
    # SUB-TAB 4: 🛡️ NEGATIVE KEYWORDS & SPAM RULES
    # ==========================================================================
    with settings_sub_tabs[3]:
        st.markdown("### Negative Keywords & Spam Rules")
        st.caption("Custom keywords that flag or block dispatches before they leave your outbox.")

        saved_neg = get_config("negative_keywords", "guarantee, 100% free, act now, urgent, winner, make money, cash") or ""
        saved_action = get_config("negative_keywords_action", DEFAULT_NEGATIVE_KEYWORD_ACTION) or DEFAULT_NEGATIVE_KEYWORD_ACTION
        saved_threshold = int(get_config("spam_score_threshold", str(DEFAULT_SPAM_SCORE_THRESHOLD)) or DEFAULT_SPAM_SCORE_THRESHOLD)

        with st.form("form_spam_rules"):
            neg_kw_input = st.text_area(
                "Custom Negative Keywords (comma-separated)",
                value=saved_neg,
                height=120,
                help="Appends to built-in spam triggers (guarantee, 100% free, act now, urgent, winner, etc.)."
            )

            c_act, c_thresh = st.columns(2)
            with c_act:
                action_options = ["warn", "block"]
                act_idx = 0 if saved_action == "warn" else 1
                action_val = st.selectbox(
                    "Action on Match",
                    action_options,
                    index=act_idx,
                    format_func=lambda x: "Warn only (highlight in editor)" if x == "warn" else "Block queueing (hard stop)"
                )
            with c_thresh:
                thresh_val = st.slider("Deliverability Spam Score Threshold (0–100)", min_value=50, max_value=95, value=saved_threshold)

            if st.form_submit_button("Save Spam Rules", type="primary", use_container_width=True):
                set_config("negative_keywords", neg_kw_input.strip())
                set_config("negative_keywords_action", action_val)
                set_config("spam_score_threshold", str(thresh_val))
                trigger_toast("Spam rules saved!", icon="🛡️")
                st.rerun()

    # ==========================================================================
    # SUB-TAB 5: ⚡ WORKER STATUS
    # ==========================================================================
    with settings_sub_tabs[4]:
        st.markdown("### Background Dispatch Worker Status")
        st.caption("Sellomize Reach uses an independent scheduler process to dispatch queued emails outside the UI render thread.")

        heartbeat_str = get_config("worker_heartbeat", "")
        worker_active = False
        last_hb_display = "Never started"

        if heartbeat_str:
            try:
                hb_dt = datetime.fromisoformat(heartbeat_str)
                now_dt = datetime.now(timezone.utc) if hb_dt.tzinfo else datetime.now()
                sec_diff = (now_dt - hb_dt).total_seconds()
                if sec_diff < 45:
                    worker_active = True
                last_hb_display = f"{int(sec_diff)}s ago ({heartbeat_str})"
            except Exception:
                last_hb_display = heartbeat_str

        w_col1, w_col2, w_col3 = st.columns(3)
        with w_col1:
            if worker_active:
                st.metric("Worker Status", "🟢 Running")
            else:
                st.metric("Worker Status", "⚪ Stopped")
        with w_col2:
            st.metric("Last Heartbeat", last_hb_display)
        with w_col3:
            st.metric("Poll Interval", f"{WORKER_POLL_INTERVAL_SECONDS} seconds")

        st.markdown("---")
        st.markdown("#### Starting the Background Worker")
        st.markdown(
            "To launch the background sender daemon on Windows, double-click or run:\n"
            "```bat\n"
            "run_scheduler.bat\n"
            "```\n"
            "Or directly in terminal:\n"
            "```bash\n"
            "python scheduler.py\n"
            "```"
        )
