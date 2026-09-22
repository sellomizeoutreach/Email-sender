"""
Settings & Rules Studio for Sellomize Reach.
Unified hub for sending schedules, anti-spam pacing, negative keyword shields,
mailbox fleet configuration, corporate signature studio, and system diagnostics.
"""

import os
import socket
import streamlit as st
from datetime import datetime
from database import (
    get_all_configs,
    get_config,
    set_config,
    get_smtp_accounts,
    add_smtp_account,
    update_smtp_account,
    delete_smtp_account,
    get_effective_daily_limit,
    get_warmup_info,
    is_within_sending_window,
    cleanup_duplicate_notifications,
    WEEKDAY_NAMES,
    DB_FILE,
    get_log_file_path
)
from smtp_dispatcher import test_smtp_connection, scan_all_hostinger_inbox, scan_all_hostinger_bounces
from ui.components import render_tab_header, render_html_preview
from ui.tabs.signature import DEFAULT_SIGNATURE_TEMPLATE


def get_local_ip() -> str:
    """Retrieve the primary local IP address of this machine on the LAN."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def render_settings_tab():
    """Render Tab 6: ⚙️ Rules & Settings unified studio."""
    render_tab_header(
        "⚙️ Rules & Settings Hub",
        "Centralized management for dispatch schedules, anti-spam rules, mailbox fleet, signatures, and system controls."
    )

    current_configs = get_all_configs()

    settings_tabs = st.tabs([
        "⏰ Sending Schedule",
        "⏱️ Anti-Spam & Engine",
        "🛡️ Negative Keyword Shield",
        "📬 Mailbox Fleet & Warmup",
        "✒️ Corporate Signature",
        "🛠️ Diagnostics & Multi-PC"
    ])

    # ==========================================================================
    # TAB 1: SENDING SCHEDULE & WINDOW
    # ==========================================================================
    with settings_tabs[0]:
        st.markdown("### ⏰ Sending Window & Delivery Schedule")
        st.caption("Control the exact days and hours during which the automated dispatcher is authorized to send emails.")

        db_enforce = (current_configs.get("enforce_sending_window", "true") or "true").strip().lower() in ["true", "1", "yes"]
        db_days_raw = current_configs.get("sending_days", "Monday, Tuesday, Wednesday, Thursday, Friday") or "Monday, Tuesday, Wednesday, Thursday, Friday"
        db_days_list = [d.strip() for d in db_days_raw.split(",") if d.strip()]
        db_start = (current_configs.get("sending_start_time", "09:00") or "09:00").strip()
        db_end = (current_configs.get("sending_end_time", "18:00") or "18:00").strip()

        if not db_enforce or (db_start == "00:00" and db_end in ["23:59", "24:00"] and len(db_days_list) >= 7):
            preset_idx = 1
        elif set(db_days_list) == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"} and db_start == "09:00" and db_end == "18:00":
            preset_idx = 0
        else:
            preset_idx = 2

        sched_preset = st.radio(
            "Schedule Mode",
            [
                "Business Days (Mon - Fri, 09:00 - 18:00)",
                "24/7 Continuous (All 7 Days, Around the Clock)",
                "Custom Schedule"
            ],
            index=preset_idx,
            horizontal=True,
            key="set_sched_preset"
        )

        if sched_preset.startswith("Business Days"):
            sel_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            sel_start = "09:00"
            sel_end = "18:00"
            sel_enforce = True
            st.info("Emails will only be sent Monday through Friday between 09:00 AM and 06:00 PM local time.")
        elif sched_preset.startswith("24/7 Continuous"):
            sel_days = list(WEEKDAY_NAMES)
            sel_start = "00:00"
            sel_end = "23:59"
            sel_enforce = False
            st.info("Continuous delivery: Emails will be dispatched at any time, 24 hours a day, 7 days a week.")
        else:
            sel_enforce = True
            col_cs1, col_cs2, col_cs3 = st.columns([2, 1, 1])
            with col_cs1:
                sel_days = st.multiselect(
                    "Allowed Sending Days",
                    WEEKDAY_NAMES,
                    default=[d for d in db_days_list if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                    key="set_custom_days"
                )
            with col_cs2:
                sel_start = st.text_input("Daily Start Time", value=db_start, placeholder="09:00", key="set_custom_start")
            with col_cs3:
                sel_end = st.text_input("Daily Cutoff Time", value=db_end, placeholder="18:00", key="set_custom_end")

        is_open, window_msg = is_within_sending_window()
        status_color = "#059669" if is_open else "#DC2626"
        status_label = "WINDOW OPEN (ACTIVE DISPATCH)" if is_open else "WINDOW PAUSED (OUTSIDE SENDING HOURS)"

        st.markdown(f"""
        <div style="background:#FFFFFF; border:1.5px solid {status_color}; border-radius:8px; padding:12px 16px; margin:14px 0 18px;">
            <div style="font-size:0.75rem; color:#64748B; font-weight:700; text-transform:uppercase;">Live Dispatcher Status</div>
            <div style="color:{status_color}; font-weight:800; font-size:0.95rem; margin:2px 0;">{status_label}</div>
            <div style="font-size:0.82rem; color:#475569;">{window_msg}</div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("💾 Save Sending Schedule", type="primary", key="btn_save_settings_sched"):
            clean_days = [d for d in sel_days if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            set_config("enforce_sending_window", "true" if sel_enforce else "false")
            set_config("sending_days", ", ".join(clean_days))
            set_config("sending_start_time", sel_start.strip())
            set_config("sending_end_time", sel_end.strip())
            st.success("Sending schedule successfully updated and synchronized to background dispatcher.")
            st.rerun()

    # ==========================================================================
    # TAB 2: ANTI-SPAM & DISPATCH ENGINE
    # ==========================================================================
    with settings_tabs[1]:
        st.markdown("### ⏱️ Outbound Engine & Anti-Spam Throttling")
        st.caption("Tune delivery protocols, natural human pacing intervals, and pre-flight validation shields.")

        curr_engine = current_configs.get("dispatch_method", "hostinger_smtp")
        engine_pick = st.radio(
            "Primary Outbound Dispatch Engine",
            [
                "Hostinger Direct SMTP (Autonomous Cloud & Background Dispatch)",
                "Desktop Microsoft Outlook (Local MAPI Application)"
            ],
            index=0 if curr_engine == "hostinger_smtp" else 1,
            key="set_engine_pick",
            help="Hostinger Direct SMTP sends autonomously in background via your configured mailbox pool. Outlook uses the local Windows Outlook client."
        )

        st.markdown("##### Human Pacing Intervals (Anti-Spam Delay)")
        st.caption("Introduce randomized delays between consecutive prospect dispatches to mimic genuine human sending behavior and protect sender reputation.")

        col_del1, col_del2, col_del3 = st.columns(3)
        with col_del1:
            min_del = st.number_input(
                "Minimum Delay (seconds)",
                min_value=5,
                max_value=300,
                value=int(current_configs.get("min_delay_seconds", "20")),
                key="set_min_del",
                help="Minimum gap between consecutive dispatches from the same or pooled mailboxes."
            )
        with col_del2:
            max_del = st.number_input(
                "Maximum Delay (seconds)",
                min_value=10,
                max_value=600,
                value=int(current_configs.get("max_delay_seconds", "45")),
                key="set_max_del",
                help="Maximum gap between consecutive dispatches."
            )
        with col_del3:
            default_cadence_days = st.number_input(
                "Default Follow-Up Delay (days)",
                min_value=1,
                max_value=30,
                value=int(current_configs.get("followup_delay_days", "4")),
                key="set_def_cadence_days",
                help="Default cadence interval between sequence steps if not individually configured in campaigns."
            )

        st.markdown("##### Pre-Flight Deliverability Sanity Checks")
        col_mx, col_blank = st.columns([1.5, 1.5])
        with col_mx:
            enforce_mx = st.checkbox(
                "Verify Domain MX Records before Sending",
                value=(current_configs.get("enforce_mx_check", "true").lower() in ["true", "1", "yes"]),
                key="set_enforce_mx",
                help="Checks that prospect's email domain has active Mail Exchange (MX) records. Prevents hard bounces before attempting dispatch."
            )

        if st.button("💾 Save Anti-Spam & Engine Settings", type="primary", key="btn_save_engine_settings"):
            chosen_method = "hostinger_smtp" if "Hostinger" in engine_pick else "outlook"
            set_config("dispatch_method", chosen_method)
            set_config("min_delay_seconds", str(min_del))
            set_config("max_delay_seconds", str(max_del))
            set_config("followup_delay_days", str(default_cadence_days))
            set_config("enforce_mx_check", "true" if enforce_mx else "false")
            st.success("Anti-spam and engine settings updated successfully.")
            st.rerun()

    # ==========================================================================
    # TAB 3: NEGATIVE KEYWORD SHIELD
    # ==========================================================================
    with settings_tabs[2]:
        st.markdown("### 🛡️ Negative Keyword & Deliverability Shield")
        st.caption("The system inspects every generated draft against restricted trigger words. If any trigger is detected, the draft is flagged for review before sending.")

        neg_words_val = current_configs.get("negative_keywords", "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash")
        spam_words_val = current_configs.get("spam_blocklist", "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast")

        set_neg_words = st.text_area(
            "Negative Keywords (Flagged for Approval)",
            value=neg_words_val,
            height=120,
            key="set_neg_words_area",
            help="Comma-separated trigger words or phrases that flag drafts in Review Queue."
        )

        set_spam_words = st.text_area(
            "Spam Words Blocklist",
            value=spam_words_val,
            height=100,
            key="set_spam_words_area",
            help="Aggressive promotional triggers to flag during pre-flight template scanning."
        )

        col_save_kw, col_reset_kw = st.columns([1.5, 1.5])
        with col_save_kw:
            if st.button("💾 Save Keyword Shield Rules", type="primary", use_container_width=True, key="btn_save_kw_shield"):
                set_config("negative_keywords", set_neg_words.strip())
                set_config("spam_blocklist", set_spam_words.strip())
                st.success("Negative keyword shield rules saved successfully.")
                st.rerun()
        with col_reset_kw:
            if st.button("🔄 Reset to Recommended Defaults", use_container_width=True, key="btn_reset_kw_shield"):
                default_neg = "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash"
                default_spam = "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast"
                set_config("negative_keywords", default_neg)
                set_config("spam_blocklist", default_spam)
                st.info("Reset shield rules to recommended defaults.")
                st.rerun()

    # ==========================================================================
    # TAB 4: MAILBOX FLEET & WARMUP
    # ==========================================================================
    with settings_tabs[3]:
        st.markdown("### 📬 Hostinger Mailbox Fleet & Warmup Ramp-Up")
        st.caption("Manage your pool of Hostinger SMTP and IMAP accounts for rotating cold outreach dispatches.")

        smtp_accounts = get_smtp_accounts(active_only=False)
        active_accounts = [acc for acc in smtp_accounts if acc.get("is_active")]
        total_capacity = sum(get_effective_daily_limit(acc) for acc in active_accounts)
        total_sent_today = sum(acc.get("sent_today", 0) for acc in active_accounts)

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Active Mailboxes", f"{len(active_accounts)} / {len(smtp_accounts)}")
        col_m2.metric("Today's Capacity", f"{total_sent_today} / {total_capacity} sent")
        col_m3.metric("Fleet Status", "Healthy" if active_accounts else "No Active Accounts")

        with st.expander("➕ Connect New Hostinger Mailbox", expanded=len(smtp_accounts) == 0):
            with st.form("set_add_smtp_form", clear_on_submit=True):
                col_af1, col_af2 = st.columns(2)
                with col_af1:
                    new_acc_name = st.text_input("Sender Display Name *", placeholder="e.g. Jack Connor | Sellomize")
                    new_acc_email = st.text_input("Email Address *", placeholder="jack@sellomize.com")
                    new_acc_pass = st.text_input("Mailbox Password *", type="password")
                with col_af2:
                    new_acc_host = st.text_input("SMTP Host", value="smtp.hostinger.com")
                    new_acc_port = st.number_input("SMTP Port", min_value=1, max_value=65535, value=465, step=1)
                    new_acc_limit = st.number_input("Target Daily Limit", min_value=1, max_value=500, value=80)

                new_warmup_enabled = st.checkbox("Enable Automated Warmup Ramp-Up", value=True)
                if new_warmup_enabled:
                    col_nw1, col_nw2 = st.columns(2)
                    with col_nw1:
                        new_warmup_start = st.number_input("Warmup Starting Cap (Day 1)", min_value=1, max_value=100, value=10)
                    with col_nw2:
                        new_warmup_inc = st.number_input("Daily Increment", min_value=1, max_value=50, value=5)
                else:
                    new_warmup_start = 10
                    new_warmup_inc = 5

                add_submit = st.form_submit_button("Verify Connection & Add Mailbox", type="primary", use_container_width=True)
                if add_submit:
                    if not new_acc_name.strip() or not new_acc_email.strip() or not new_acc_pass.strip():
                        st.error("Display Name, Email, and Password are required.")
                    else:
                        with st.spinner(f"Verifying SMTP connection to {new_acc_host}:{new_acc_port}..."):
                            ok, test_msg = test_smtp_connection(new_acc_host.strip(), int(new_acc_port), new_acc_email.strip(), new_acc_pass.strip())
                        if ok:
                            add_smtp_account(
                                sender_name=new_acc_name.strip(),
                                email=new_acc_email.strip(),
                                password=new_acc_pass.strip(),
                                smtp_host=new_acc_host.strip(),
                                smtp_port=int(new_acc_port),
                                daily_limit=int(new_acc_limit),
                                warmup_enabled=new_warmup_enabled,
                                warmup_starting_limit=int(new_warmup_start),
                                warmup_daily_increment=int(new_warmup_inc),
                                warmup_target_limit=int(new_acc_limit)
                            )
                            st.success(f"Mailbox '{new_acc_email}' connected successfully!")
                            st.rerun()
                        else:
                            st.error(f"Connection verification failed: {test_msg}")

        if smtp_accounts:
            st.markdown("##### Active Mailbox Fleet")
            for acc in smtp_accounts:
                acc_id = acc["id"]
                sent_today = acc.get("sent_today", 0)
                w_info = get_warmup_info(acc)
                eff_limit = w_info["effective_limit"]
                target_limit = w_info["target_limit"]
                is_warmup = w_info["is_warmup"]
                day_num = w_info["day_num"]
                pct = min(1.0, float(sent_today) / max(1.0, float(eff_limit)))

                badge = f'<span style="background:rgba(217,119,6,0.12); color:#D97706; border:1px solid #D97706; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">WARMUP DAY {day_num}</span>' if is_warmup else '<span style="background:rgba(8,55,49,0.1); color:#083731; border:1px solid #083731; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">STANDARD</span>'
                status_icon = "🟢" if acc.get("is_active") else "⏸️"

                st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:10px; padding:12px 16px; margin:10px 0 6px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <strong style="color:#083731; font-size:1rem;">{status_icon} {acc['email']}</strong>
                            <div style="font-size:0.82rem; color:#64748B;">{acc['sender_name']} • Host: {acc['smtp_host']}:{acc['smtp_port']}</div>
                        </div>
                        <div>{badge}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                st.progress(pct, text=f"{sent_today} of {eff_limit} sent today (Target: {target_limit}/day)")

                c_act1, c_act2, c_act3 = st.columns([1, 1, 1])
                with c_act1:
                    if st.button("🔌 Test SMTP", key=f"set_test_{acc_id}", use_container_width=True):
                        ok, msg = test_smtp_connection(acc["smtp_host"], acc["smtp_port"], acc["email"], acc["password"])
                        if ok:
                            st.success("Verified!")
                        else:
                            st.error(f"Failed: {msg}")
                with c_act2:
                    if acc["is_active"]:
                        if st.button("⏸️ Pause", key=f"set_pause_{acc_id}", use_container_width=True):
                            update_smtp_account(acc_id, is_active=False)
                            st.rerun()
                    else:
                        if st.button("▶️ Activate", key=f"set_act_{acc_id}", use_container_width=True):
                            update_smtp_account(acc_id, is_active=True)
                            st.rerun()
                with c_act3:
                    if st.session_state.get(f"confirm_del_{acc_id}"):
                        if st.button("Confirm Delete", key=f"set_conf_del_{acc_id}", use_container_width=True):
                            delete_smtp_account(acc_id)
                            st.session_state[f"confirm_del_{acc_id}"] = False
                            st.rerun()
                    else:
                        if st.button("🗑️ Delete", key=f"set_del_{acc_id}", use_container_width=True):
                            st.session_state[f"confirm_del_{acc_id}"] = True
                            st.rerun()

                with st.expander(f"⚙️ Edit Settings for {acc['email']}", expanded=False):
                    with st.form(f"set_edit_acc_{acc_id}"):
                        e_name = st.text_input("Sender Display Name", value=acc["sender_name"])
                        e_pass = st.text_input("New Password (leave blank to keep current)", type="password")
                        e_daily_limit = st.number_input("Target Daily Limit", min_value=1, max_value=500, value=int(acc.get("daily_limit", 80)))
                        e_warmup_on = st.checkbox("Warmup Enabled", value=bool(acc.get("warmup_enabled")), key=f"set_wo_{acc_id}")
                        e_w_start = st.number_input("Starting Cap", min_value=1, max_value=100, value=int(acc.get("warmup_starting_limit") or 10), key=f"set_ws_{acc_id}")
                        e_w_inc = st.number_input("Daily Increment", min_value=1, max_value=50, value=int(acc.get("warmup_daily_increment") or 5), key=f"set_wi_{acc_id}")
                        e_w_target = st.number_input("Target Cap", min_value=5, max_value=300, value=int(acc.get("warmup_target_limit") or 50), key=f"set_wt_{acc_id}")
                        if st.form_submit_button("Update Mailbox", type="primary", use_container_width=True):
                            update_smtp_account(
                                acc_id,
                                sender_name=e_name.strip(),
                                password=e_pass.strip() if e_pass.strip() else None,
                                daily_limit=int(e_daily_limit),
                                warmup_enabled=e_warmup_on,
                                warmup_starting_limit=int(e_w_start),
                                warmup_daily_increment=int(e_w_inc),
                                warmup_target_limit=int(e_w_target)
                            )
                            st.success("Mailbox updated successfully!")
                            st.rerun()

    # ==========================================================================
    # TAB 5: CORPORATE SIGNATURE STUDIO
    # ==========================================================================
    with settings_tabs[4]:
        st.markdown("### ✒️ Corporate HTML Signature Studio")
        st.caption("Design, edit, and preview the HTML signature automatically appended to outgoing campaign and sequence emails.")

        sig_key = "set_shared_sig_content"
        if sig_key not in st.session_state:
            st.session_state[sig_key] = get_config("signature_html", "")

        col_sig_ed, col_sig_prv = st.columns([1.1, 1.1])
        with col_sig_ed:
            st.markdown("##### Signature Code Editor")
            st.caption("Inline CSS is recommended for universal rendering across Outlook, Gmail, Apple Mail, and mobile clients.")

            c_act1, c_act2 = st.columns([1.4, 1])
            with c_act1:
                if st.button("Load Sellomize Template", key="set_load_sig_btn", use_container_width=True):
                    st.session_state[sig_key] = DEFAULT_SIGNATURE_TEMPLATE
                    set_config("signature_html", DEFAULT_SIGNATURE_TEMPLATE)
                    st.success("Loaded default Sellomize signature template.")
                    st.rerun()
            with c_act2:
                confirm_clr = st.checkbox("Confirm clear", key="set_conf_clear_sig")
                if st.button("Clear Signature", key="set_clear_sig_btn", use_container_width=True, disabled=not confirm_clr):
                    st.session_state[sig_key] = ""
                    set_config("signature_html", "")
                    st.info("Signature cleared.")
                    st.rerun()

            sig_code = st.text_area(
                "HTML Signature Code",
                value=st.session_state[sig_key],
                height=280,
                key="set_sig_textarea"
            )
            st.session_state[sig_key] = sig_code

            if st.button("💾 Save Signature", type="primary", use_container_width=True, key="set_save_sig_btn"):
                set_config("signature_html", sig_code.strip())
                st.success("Corporate HTML signature saved successfully.")
                st.rerun()

        with col_sig_prv:
            st.markdown("##### Live Visual Preview")
            st.caption("Real-time preview rendered as prospect mail clients see it.")
            active_sig = st.session_state[sig_key].strip() or get_config("signature_html", "").strip()
            if active_sig:
                render_html_preview(active_sig, height=340)
            else:
                st.info("No active signature configured. Click 'Load Sellomize Template' on the left or paste your HTML code.")

    # ==========================================================================
    # TAB 6: DIAGNOSTICS, IMAP SYNC & MULTI-PC
    # ==========================================================================
    with settings_tabs[5]:
        st.markdown("### 🛠️ Diagnostics, IMAP Sync & Network Access")
        st.caption("System health maintenance, manual inbox synchronization, notification deduplication, and LAN access.")

        local_ip = get_local_ip()
        st.markdown(f"""
        <div style="background:#F8FAFC; border:1.5px solid #CBD5E1; border-radius:10px; padding:16px; margin-bottom:18px;">
            <div style="font-weight:800; font-size:0.95rem; color:#083731;">🌐 Multi-PC &amp; Team LAN Access</div>
            <div style="font-size:0.83rem; color:#475569; margin-top:4px; line-height:1.4;">
                This Host PC acts as the central single source of truth. Any other computer or laptop on your local Wi-Fi or office network can connect directly:
            </div>
            <code style="display:block; background:#EEF2F6; padding:8px 12px; border-radius:6px; font-size:0.95rem; color:#083731; font-weight:700; margin:8px 0;">
                http://{local_ip}:8501
            </code>
            <div style="font-size:0.78rem; color:#64748B;">
                Tip: In Chrome or Edge, click 'Install App' in the URL bar to launch Sellomize Reach as a desktop app on any workstation.
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("##### Database Maintenance & IMAP Tools")
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            if st.button("🧹 Prune Duplicate Notifications", use_container_width=True, key="btn_prune_dups"):
                pruned = cleanup_duplicate_notifications()
                st.success(f"Deduplication complete: Cleaned {pruned} redundant notification(s).")
                st.rerun()
        with col_t2:
            if st.button("📬 Run Instant Hostinger Inbox Scan", use_container_width=True, key="btn_sync_imap_now"):
                with st.spinner("Scanning Hostinger inbox via IMAP for replies and bounces..."):
                    try:
                        replies_found = scan_all_hostinger_inbox()
                        bounces_found = scan_all_hostinger_bounces()
                        st.success(f"Scan complete: Processed {replies_found} new prospect replie(s) and {bounces_found} bounce(s).")
                    except Exception as e:
                        st.error(f"IMAP scan failed: {e}")
                st.rerun()

        st.markdown("##### System Logs")
        log_path = get_log_file_path()
        if os.path.exists(log_path):
            with st.expander("📄 View Recent System Logs (sellomize.log)", expanded=False):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    last_lines = "".join(lines[-60:]) if lines else "Log file is currently empty."
                    st.code(last_lines, language="log")
                except Exception as e:
                    st.caption(f"Error reading log file: {e}")
        else:
            st.caption("No log file found on disk.")
