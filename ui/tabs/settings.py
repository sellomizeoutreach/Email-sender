"""
Settings & Rules Studio for Sellomize Reach.
Unified hub for sending schedules, anti-spam pacing, negative keyword shields,
mailbox fleet configuration, corporate signature studio, and system diagnostics.
Compartmentalized into 4 clean sub-tabs:
1. 📧 Mailbox Fleet (Mailbox pool, warmup ramps, connection verification)
2. ✒️ Signature (Corporate HTML signature editor and live preview)
3. 🛡️ Keyword Shield (Negative keyword shield and spam blocklist)
4. ⚙️ Advanced Telemetry (Sending window, anti-spam pacing, LAN access & diagnostics)
"""

import os
import socket
import streamlit as st
from datetime import datetime
import database as db

# Resilient dynamic bindings prevent hot-reload ImportErrors on Streamlit Cloud & remote hosts
get_all_configs = lambda *a, **kw: getattr(db, "get_all_configs", lambda: {})(*a, **kw)
get_config = lambda *a, **kw: getattr(db, "get_config", lambda k, d=None: d)(*a, **kw)
set_config = lambda *a, **kw: getattr(db, "set_config", lambda k, v: None)(*a, **kw)
get_smtp_accounts = lambda *a, **kw: getattr(db, "get_smtp_accounts", lambda: [])(*a, **kw)
add_smtp_account = lambda *a, **kw: getattr(db, "add_smtp_account", lambda **k: None)(*a, **kw)
update_smtp_account = lambda *a, **kw: getattr(db, "update_smtp_account", lambda *x, **k: None)(*a, **kw)
delete_smtp_account = lambda *a, **kw: getattr(db, "delete_smtp_account", lambda aid: None)(*a, **kw)
get_effective_daily_limit = lambda *a, **kw: getattr(db, "get_effective_daily_limit", lambda acc, t=None: int(acc.get("daily_limit", 50)))(*a, **kw)
get_warmup_info = lambda *a, **kw: getattr(db, "get_warmup_info", lambda acc, t=None: {"is_warmup": False, "effective_limit": int(acc.get("daily_limit", 50))})(*a, **kw)
is_within_sending_window = lambda *a, **kw: getattr(db, "is_within_sending_window", lambda dt=None: (True, "OK"))(*a, **kw)
cleanup_duplicate_notifications = lambda *a, **kw: getattr(db, "cleanup_duplicate_notifications", lambda: 0)(*a, **kw)
WEEKDAY_NAMES = getattr(db, "WEEKDAY_NAMES", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
DB_FILE = getattr(db, "DB_FILE", "email_system.db")
get_log_file_path = lambda *a, **kw: getattr(db, "get_log_file_path", lambda: "sellomize.log")(*a, **kw)
from smtp_dispatcher import test_smtp_connection, scan_all_hostinger_inbox, scan_all_hostinger_bounces
from timezone_helper import (
    TARGET_MARKETS,
    get_market_info,
    get_market_current_time,
    get_time_difference_summary,
    is_within_market_hours
)
from ui.components import render_tab_header, render_html_preview, trigger_toast
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
    """Render ⚙️ Rules & Settings Hub compartmentalized into 4 secondary sub-tabs."""
    render_tab_header(
        "⚙️ Rules & Settings Hub",
        "Centralized management for mailbox fleet, signatures, keyword shield, and advanced dispatch telemetry."
    )

    current_configs = get_all_configs()

    settings_tabs = st.tabs([
        "📧 Mailbox Fleet",
        "✒️ Signature",
        "🛡️ Keyword Shield",
        "⚙️ Advanced Telemetry"
    ])

    # ==========================================================================
    # SUB-TAB 1: 📧 MAILBOX FLEET & WARMUP
    # ==========================================================================
    with settings_tabs[0]:
        st.markdown("### 📬 Hostinger Mailbox Fleet & Warmup Ramp-Up")
        st.caption("Manage your pool of Hostinger SMTP and IMAP accounts for rotating cold outreach dispatches.")

        smtp_accounts = get_smtp_accounts(active_only=False)
        active_accounts = [acc for acc in smtp_accounts if acc.get("is_active")]
        total_capacity = sum(get_effective_daily_limit(acc) for acc in active_accounts)
        total_sent_today = sum(acc.get("sent_today", 0) for acc in active_accounts)

        is_open, window_msg = is_within_sending_window()
        engine_method = current_configs.get("dispatch_method", "hostinger_smtp")
        engine_label = "Hostinger SMTP" if engine_method == "hostinger_smtp" else "Outlook Local"
        status_badge = '<span class="sellomize-badge badge-success">WINDOW OPEN</span>' if is_open else '<span class="sellomize-badge badge-alert">WINDOW PAUSED</span>'

        st.markdown(f"""
        <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:10px; padding:10px 14px; margin:8px 0 12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <div>
                <strong style="color:#083731; font-size:0.92rem;">Dispatch Engine: {engine_label}</strong>
                <span style="color:#475569; font-size:0.8rem; margin-left:8px;">{window_msg}</span>
            </div>
            <div>{status_badge}</div>
        </div>
        """, unsafe_allow_html=True)

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
                            trigger_toast(f"Mailbox '{new_acc_email}' connected successfully!", icon="📬")
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
                            trigger_toast("Mailbox connection verified!", icon="✅")
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
                            trigger_toast("Mailbox deleted.", icon="🗑️")
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
                            trigger_toast("Mailbox updated successfully!", icon="📬")
                            st.rerun()

    # ==========================================================================
    # SUB-TAB 2: ✒️ CORPORATE SIGNATURE STUDIO
    # ==========================================================================
    with settings_tabs[1]:
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
                    trigger_toast("Loaded default signature template.", icon="📄")
                    st.rerun()
            with c_act2:
                confirm_clr = st.checkbox("Confirm clear", key="set_conf_clear_sig")
                if st.button("Clear Signature", key="set_clear_sig_btn", use_container_width=True, disabled=not confirm_clr):
                    st.session_state[sig_key] = ""
                    set_config("signature_html", "")
                    trigger_toast("Signature cleared.", icon="🧹")
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
                trigger_toast("Corporate HTML signature saved successfully.", icon="✍️")
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
    # SUB-TAB 3: 🛡️ NEGATIVE KEYWORD SHIELD
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
                trigger_toast("Negative keyword shield rules saved successfully.", icon="🛡️")
                st.rerun()
        with col_reset_kw:
            if st.button("🔄 Reset to Recommended Defaults", use_container_width=True, key="btn_reset_kw_shield"):
                default_neg = "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash"
                default_spam = "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast"
                set_config("negative_keywords", default_neg)
                set_config("spam_blocklist", default_spam)
                trigger_toast("Reset shield rules to recommended defaults.", icon="🔄")
                st.rerun()

    # ==========================================================================
    # SUB-TAB 4: ⚙️ ADVANCED TELEMETRY & INFRASTRUCTURE
    # ==========================================================================
    with settings_tabs[3]:
        st.markdown("### ⚙️ Advanced Telemetry & Infrastructure")
        st.caption("Sending schedules, multi-country destination timezones, anti-spam delay intervals, system health diagnostics, and LAN access.")

        # --- SECTION A: SENDING SCHEDULE & WINDOW ---
        with st.expander("⏰ Sending Schedule & Destination Timezones", expanded=True):
            st.caption("Control the working hours, destination country schedules, and allowed delivery days for automated background dispatch.")

            db_schedule_mode = (current_configs.get("schedule_mode", "adaptive_multi_country") or "adaptive_multi_country").strip()
            db_default_market = (current_configs.get("default_market", "CA_EAST") or "CA_EAST").strip()
            db_enforce = (current_configs.get("enforce_sending_window", "true") or "true").strip().lower() in ["true", "1", "yes"]
            db_days_raw = current_configs.get("sending_days", "Monday, Tuesday, Wednesday, Thursday, Friday") or "Monday, Tuesday, Wednesday, Thursday, Friday"
            db_days_list = [d.strip() for d in db_days_raw.split(",") if d.strip()]
            db_start = (current_configs.get("sending_start_time", "09:00") or "09:00").strip()
            db_end = (current_configs.get("sending_end_time", "18:00") or "18:00").strip()

            preset_options = [
                "🌍 Adaptive Multi-Country Dispatch (Timezone-Aware)",
                "🏢 Single Office Hours Window (Mon - Fri, Host PC Hours)",
                "⚡ 24/7 Continuous (All 7 Days, Around the Clock)",
                "🛠️ Custom Schedule"
            ]

            if db_schedule_mode == "adaptive_multi_country":
                preset_idx = 0
            elif db_schedule_mode == "continuous" or not db_enforce or (db_start == "00:00" and db_end in ["23:59", "24:00"] and len(db_days_list) >= 7):
                preset_idx = 2
            elif set(db_days_list) == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"} and db_start == "09:00" and db_end == "18:00":
                preset_idx = 1
            else:
                preset_idx = 3

            sched_preset = st.radio(
                "Schedule Mode",
                preset_options,
                index=preset_idx,
                key="set_sched_preset"
            )

            market_keys = list(TARGET_MARKETS.keys())
            def_m_idx = market_keys.index(db_default_market) if db_default_market in market_keys else 0

            if sched_preset.startswith("🌍 Adaptive Multi-Country"):
                sel_mode = "adaptive_multi_country"
                sel_enforce = True
                sel_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                sel_start = "09:00"
                sel_end = "18:00"

                st.markdown("""
                <div style="background:#F0FDF4; border:1.5px solid #16A34A; border-radius:8px; padding:12px 16px; margin:10px 0 14px;">
                    <div style="font-weight:800; color:#15803D; font-size:0.92rem;">🌍 Autonomous Country-Wise Pacing Enabled</div>
                    <div style="color:#166534; font-size:0.83rem; margin-top:4px; line-height:1.45;">
                        The background dispatch engine operates 24/7, continuously matching each email's delivery to its specific destination country's working hours (09:00 - 17:00).
                        Emails destined for <strong>Canada</strong> send during Canadian business hours; <strong>Australia</strong> sends during Australian hours, without blocking each other or stopping when your Host PC is off hours.
                    </div>
                </div>
                """, unsafe_allow_html=True)

                col_m1, col_m2 = st.columns([1.8, 1.2])
                with col_m1:
                    sel_default_market = st.selectbox(
                        "Default Target Country & Market Timezone",
                        options=market_keys,
                        index=def_m_idx,
                        format_func=lambda k: TARGET_MARKETS[k]["label"],
                        key="set_def_market_select",
                        help="Default market applied to new campaigns if not individually customized."
                    )
                with col_m2:
                    m_info = TARGET_MARKETS[sel_default_market]
                    m_now = get_market_current_time(sel_default_market)
                    diff_summary = get_time_difference_summary(sel_default_market)
                    is_m_open, m_open_reason = is_within_market_hours(sel_default_market)
                    m_badge_color = "#16A34A" if is_m_open else "#CA8A04"
                    m_badge_status = "OPEN" if is_m_open else "CLOSED"

                    st.markdown(f"""
                    <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.18); border-radius:8px; padding:10px 14px; margin-top:6px;">
                        <div style="font-size:0.75rem; color:#64748B; font-weight:700;">LIVE TARGET CLOCK</div>
                        <div style="font-weight:800; color:#083731; font-size:1.05rem;">{m_now.strftime('%I:%M %p')} <span style="font-size:0.8rem; color:#64748B;">{m_now.strftime('%Z')}</span></div>
                        <div style="font-size:0.8rem; color:#475569; margin-top:2px;">{diff_summary}</div>
                        <div style="display:inline-block; font-size:0.72rem; font-weight:800; color:#FFFFFF; background:{m_badge_color}; padding:2px 8px; border-radius:10px; margin-top:4px;">{m_badge_status}</div>
                    </div>
                    """, unsafe_allow_html=True)

            elif sched_preset.startswith("🏢 Single Office Hours"):
                sel_mode = "office_hours"
                sel_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                sel_start = "09:00"
                sel_end = "18:00"
                sel_enforce = True
                sel_default_market = db_default_market
                st.info("Emails will only be sent Monday through Friday between 09:00 AM and 06:00 PM Host PC local time.")
            elif sched_preset.startswith("⚡ 24/7 Continuous"):
                sel_mode = "continuous"
                sel_days = list(WEEKDAY_NAMES)
                sel_start = "00:00"
                sel_end = "23:59"
                sel_enforce = False
                sel_default_market = db_default_market
                st.info("Continuous delivery: Emails will be dispatched at any time, 24 hours a day, 7 days a week.")
            else:
                sel_mode = "office_hours"
                sel_enforce = True
                sel_default_market = db_default_market
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
            status_label = "DISPATCH ENGINE ONLINE & ACTIVE" if is_open else "WINDOW PAUSED (OUTSIDE SENDING HOURS)"

            st.markdown(f"""
            <div style="background:#FFFFFF; border:1.5px solid {status_color}; border-radius:8px; padding:12px 16px; margin:14px 0 18px;">
                <div style="font-size:0.75rem; color:#64748B; font-weight:700; text-transform:uppercase;">Live Dispatcher Status</div>
                <div style="color:{status_color}; font-weight:800; font-size:0.95rem; margin:2px 0;">{status_label}</div>
                <div style="font-size:0.82rem; color:#475569;">{window_msg}</div>
            </div>
            """, unsafe_allow_html=True)

            if st.button("💾 Save Sending Schedule", type="primary", key="btn_save_settings_sched"):
                clean_days = [d for d in sel_days if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                set_config("schedule_mode", sel_mode)
                set_config("default_market", sel_default_market)
                set_config("enforce_sending_window", "true" if sel_enforce else "false")
                set_config("sending_days", ", ".join(clean_days))
                set_config("sending_start_time", sel_start.strip())
                set_config("sending_end_time", sel_end.strip())
                trigger_toast("Sending schedule successfully updated!", icon="🕒")
                st.rerun()

        # --- SECTION B: ANTI-SPAM & OUTBOUND ENGINE ---
        with st.expander("⏱️ Anti-Spam Delays & Outbound Compliance", expanded=True):
            st.caption("Tune delivery protocols, natural human pacing intervals, and pre-flight validation shields.")

            curr_engine = current_configs.get("dispatch_method", "hostinger_smtp")
            engine_pick = st.radio(
                "Outbound Delivery Engine",
                [
                    "Hostinger SMTP (Recommended & Active) — Dedicated Direct IP Relay",
                    "Local Outlook Desktop Engine — Native Desktop Automation via MAPI"
                ],
                index=0 if "hostinger" in curr_engine.lower() else 1,
                key="set_engine_radio"
            )

            col_del1, col_del2, col_cad = st.columns(3)
            with col_del1:
                min_del = st.number_input(
                    "Min Delay Between Sends (sec)",
                    min_value=5,
                    max_value=600,
                    value=int(current_configs.get("min_delay_seconds", 30)),
                    key="set_min_del"
                )
            with col_del2:
                max_del = st.number_input(
                    "Max Delay Between Sends (sec)",
                    min_value=10,
                    max_value=900,
                    value=int(current_configs.get("max_delay_seconds", 90)),
                    key="set_max_del"
                )
            with col_cad:
                default_cadence_days = st.number_input(
                    "Default Follow-Up Delay (Days)",
                    min_value=1,
                    max_value=60,
                    value=int(current_configs.get("followup_delay_days", 3)),
                    key="set_cad_days"
                )

            col_mx, col_bcc = st.columns(2)
            with col_mx:
                enforce_mx = st.checkbox(
                    "Enforce DNS MX Pre-Flight Check",
                    value=(current_configs.get("enforce_mx_check", "true").lower() == "true"),
                    key="set_enforce_mx",
                    help="Automatically queries domain DNS MX records before queuing. Prevents sending to dead domains."
                )
            with col_bcc:
                bcc_input = st.text_input(
                    "CRM Archival BCC Address (Optional)",
                    value=current_configs.get("bcc_email", ""),
                    placeholder="crm-inbox@yourdomain.com",
                    help="Automatically adds a hidden BCC to every outgoing email for CRM tracking."
                )

            if bcc_input.strip():
                st.markdown(f"""
                <div style="background:rgba(8,55,49,0.06); border:1px solid rgba(8,55,49,0.18); border-radius:6px; padding:6px 12px; margin:6px 0;">
                    <span style="font-weight:700; color:#083731; font-size:0.84rem;">📬 Active BCC Archive:</span>
                    <span style="color:#083731; font-size:0.84rem;"> All outreach emails will silently copy <code>{bcc_input.strip()}</code>.</span>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.caption("ℹ️ No BCC address currently configured. Outbound emails will only be sent directly to the prospect.")

            if st.button("💾 Save Anti-Spam & Engine Settings", type="primary", key="btn_save_engine_settings"):
                chosen_method = "hostinger_smtp" if "Hostinger" in engine_pick else "outlook"
                set_config("dispatch_method", chosen_method)
                set_config("min_delay_seconds", str(min_del))
                set_config("max_delay_seconds", str(max_del))
                set_config("followup_delay_days", str(default_cadence_days))
                set_config("enforce_mx_check", "true" if enforce_mx else "false")
                set_config("bcc_email", bcc_input.strip())
                trigger_toast("Anti-spam & engine settings updated successfully.", icon="🛡️")
                st.rerun()

        # --- SECTION C: DIAGNOSTICS & MULTI-PC ---
        with st.expander("🛠️ System Diagnostics, IMAP Sync & Multi-PC LAN", expanded=False):
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
            col_t1, col_t2, col_t3 = st.columns(3)
            with col_t1:
                if st.button("🧹 Prune Duplicate Notifications", use_container_width=True, key="btn_prune_dups"):
                    pruned = cleanup_duplicate_notifications()
                    trigger_toast(f"Cleaned {pruned} redundant notification(s).", icon="🧹")
                    st.rerun()
            with col_t2:
                if st.button("📬 Instant Inbox Scan", use_container_width=True, key="btn_sync_imap_now"):
                    with st.spinner("Scanning Hostinger inbox via IMAP for replies and bounces..."):
                        try:
                            replies_found = scan_all_hostinger_inbox()
                            bounces_found = scan_all_hostinger_bounces()
                            trigger_toast(f"Scan complete: {replies_found} new reply(ies), {bounces_found} bounce(s).", icon="📬")
                        except Exception as e:
                            st.error(f"IMAP scan failed: {e}")
                    st.rerun()
            with col_t3:
                clear_fn = getattr(db, "clear_outbox_emails", lambda **k: 0)
                if st.session_state.get("confirm_settings_purge_outbox"):
                    if st.button("Confirm Purge Sent", type="primary", use_container_width=True, key="btn_conf_purge_outbox_set"):
                        purged = clear_fn(status="Sent")
                        st.session_state["confirm_settings_purge_outbox"] = False
                        trigger_toast(f"Purged {purged} historical sent email(s).", icon="🗑️")
                        st.rerun()
                else:
                    if st.button("🗑️ Purge Sent Outbox", use_container_width=True, key="btn_purge_outbox_set", help="Clear historical sent emails so they do not pile up in the database."):
                        st.session_state["confirm_settings_purge_outbox"] = True
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
