"""
Sidebar infrastructure and configuration panel for Sellomize Reach.
Manages sending window, mailbox fleet, system diagnostics, signature studio, and negative keyword shield.
"""

import streamlit as st
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
    WEEKDAY_NAMES
)
from smtp_dispatcher import test_smtp_connection
from tracker import is_port_in_use, start_tracking_server, get_tracking_base_url
from ui.components import render_html_preview


def render_sidebar():
    """Render the full persistent sidebar in st.sidebar."""
    with st.sidebar:
        st.markdown("""
        <div style="display:flex; align-items:center; gap:12px; padding:10px 4px 14px; border-bottom:1.5px solid rgba(8,55,49,0.15); margin-bottom:16px;">
            <span style="font-size:1.75rem;">⚙️</span>
            <div>
                <div style="font-weight:900; font-size:1.15rem; color:#083731; letter-spacing:0.6px; line-height:1.1;">INFRASTRUCTURE</div>
                <div style="font-size:0.75rem; color:#64748B; font-weight:700; letter-spacing:0.4px;">SELLOMIZE REACH AGENCY HUB</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        current_configs = get_all_configs()

        # SECTION 1: OUTBOUND DISPATCH ENGINE & WINDOW
        with st.expander("⚡ Outbound Engine & Sending Window", expanded=False):
            current_engine = current_configs.get("dispatch_method", "hostinger_smtp")
            dispatch_engine_choice = st.radio(
                "Primary Outbound Engine",
                ["⚡ Hostinger Direct SMTP (Multi-Account)", "📧 Desktop Microsoft Outlook"],
                index=0 if current_engine == "hostinger_smtp" else 1,
                help="Hostinger Direct SMTP sends autonomously in background. Outlook uses local Windows Outlook."
            )

            st.caption("Human Delay Throttling (Anti-Spam)")
            col_sb_del1, col_sb_del2 = st.columns(2)
            with col_sb_del1:
                sb_min_delay = st.number_input(
                    "Min Delay (s)",
                    min_value=5,
                    max_value=300,
                    value=int(current_configs.get("min_delay_seconds", "20")),
                    key="sb_min_del"
                )
            with col_sb_del2:
                sb_max_delay = st.number_input(
                    "Max Delay (s)",
                    min_value=10,
                    max_value=600,
                    value=int(current_configs.get("max_delay_seconds", "45")),
                    key="sb_max_del"
                )

            st.caption("Active Sending Window & Days")
            raw_saved_days = current_configs.get("sending_days", "Monday,Tuesday,Wednesday,Thursday,Friday")
            saved_days_list = [d.strip() for d in raw_saved_days.split(",") if d.strip()]
            sb_sending_days = st.multiselect(
                "Allowed Days",
                WEEKDAY_NAMES,
                default=[d for d in saved_days_list if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                key="sb_send_days"
            )
            col_sw1, col_sw2 = st.columns(2)
            with col_sw1:
                sb_start_time = st.text_input("Start (HH:MM)", value=current_configs.get("sending_start_time", "09:00"), key="sb_st_time")
            with col_sw2:
                sb_end_time = st.text_input("Cutoff (HH:MM)", value=current_configs.get("sending_end_time", "18:00"), key="sb_end_time")

            sb_enforce_win = st.checkbox(
                "Enforce Sending Window (Pause off-hours)",
                value=(current_configs.get("enforce_sending_window", "true").lower() in ["true", "1", "yes"]),
                key="sb_enf_win"
            )
            sb_enforce_mx = st.checkbox(
                "Enforce Pre-Flight MX Sanity Check",
                value=(current_configs.get("enforce_mx_check", "true").lower() in ["true", "1", "yes"]),
                key="sb_enf_mx"
            )

            is_open, window_status_msg = is_within_sending_window()
            status_badge = '<span style="background:rgba(16,185,129,0.15); color:#059669; border:1px solid #10B981; padding:3px 10px; border-radius:12px; font-weight:700; font-size:0.8rem;">🟢 WINDOW OPEN</span>' if is_open else '<span style="background:rgba(239,68,68,0.15); color:#DC2626; border:1px solid #EF4444; padding:3px 10px; border-radius:12px; font-weight:700; font-size:0.8rem;">🔴 WINDOW PAUSED</span>'
            st.markdown(f"<div style='margin:6px 0 10px;'>{status_badge} <div style='color:#64748B; font-size:0.8rem; margin-top:3px;'>{window_status_msg}</div></div>", unsafe_allow_html=True)

            if st.button("💾 Save Engine & Window Settings", type="primary", use_container_width=True, key="save_engine_btn"):
                engine_key = "hostinger_smtp" if "Hostinger" in dispatch_engine_choice else "outlook"
                set_config("dispatch_method", engine_key)
                set_config("min_delay_seconds", str(sb_min_delay))
                set_config("max_delay_seconds", str(sb_max_delay))
                set_config("sending_days", ", ".join(sb_sending_days))
                set_config("sending_start_time", sb_start_time.strip())
                set_config("sending_end_time", sb_end_time.strip())
                set_config("enforce_sending_window", "true" if sb_enforce_win else "false")
                set_config("enforce_mx_check", "true" if sb_enforce_mx else "false")
                st.success("✅ Engine & window settings saved!")
                st.rerun()

        # SECTION 2: HOSTINGER MAILBOX FLEET & WARMUP RAMP-UP
        with st.expander("📬 Hostinger Mailbox Fleet & Warmup", expanded=False):
            smtp_accounts = get_smtp_accounts(active_only=False)
            active_accounts = [acc for acc in smtp_accounts if acc.get("is_active")]
            total_capacity = sum(get_effective_daily_limit(acc) for acc in active_accounts)
            total_sent_today = sum(acc.get("sent_today", 0) for acc in active_accounts)

            col_f1, col_f2 = st.columns(2)
            col_f1.metric("Mailboxes", f"{len(active_accounts)} / {len(smtp_accounts)} Active")
            col_f2.metric("Today's Capacity", f"{total_sent_today} / {total_capacity}")

            with st.expander("➕ Connect New Hostinger Mailbox", expanded=len(smtp_accounts) == 0):
                with st.form("sb_add_smtp_form", clear_on_submit=True):
                    new_acc_name = st.text_input("Display Name *", placeholder="e.g. Alex Morgan | Sellomize")
                    new_acc_email = st.text_input("Email Address *", placeholder="alex@sellomize.com")
                    new_acc_pass = st.text_input("Password *", type="password")
                    col_nb1, col_nb2 = st.columns(2)
                    with col_nb1:
                        new_acc_host = st.text_input("SMTP Host", value="smtp.hostinger.com")
                        new_acc_limit = st.number_input("Target Daily Limit", min_value=1, max_value=500, value=80)
                    with col_nb2:
                        new_acc_port = st.number_input("SMTP Port", min_value=1, max_value=65535, value=465, step=1)
                        new_warmup_enabled = st.checkbox("Enable Automated Warmup", value=True)

                    if new_warmup_enabled:
                        col_nw1, col_nw2 = st.columns(2)
                        with col_nw1:
                            new_warmup_start = st.number_input("Starting Cap", min_value=1, max_value=100, value=10)
                        with col_nw2:
                            new_warmup_inc = st.number_input("Daily Increment", min_value=1, max_value=50, value=5)
                    else:
                        new_warmup_start = 10
                        new_warmup_inc = 5

                    add_acc_submit = st.form_submit_button("Verify & Connect Mailbox", type="primary", use_container_width=True)
                    if add_acc_submit:
                        if not new_acc_name.strip() or not new_acc_email.strip() or not new_acc_pass.strip():
                            st.error("Display Name, Email, and Password are required.")
                        else:
                            with st.spinner(f"Testing SMTP {new_acc_host}:{new_acc_port}..."):
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
                                st.success(f"✅ Connected '{new_acc_email}'!")
                                st.rerun()
                            else:
                                st.error(f"❌ Connection Failed: {test_msg}")

            if smtp_accounts:
                st.markdown("##### Mailbox Fleet")
                for acc in smtp_accounts:
                    acc_id = acc["id"]
                    sent_today = acc.get("sent_today", 0)
                    w_info = get_warmup_info(acc)
                    eff_limit = w_info["effective_limit"]
                    target_limit = w_info["target_limit"]
                    is_warmup = w_info["is_warmup"]
                    day_num = w_info["day_num"]
                    pct = min(1.0, float(sent_today) / max(1.0, float(eff_limit)))

                    if is_warmup:
                        warmup_badge = f'<span style="background:rgba(217,119,6,0.12); color:#D97706; border:1px solid #D97706; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">DAY {day_num}</span>'
                    else:
                        warmup_badge = '<span style="background:rgba(8,55,49,0.1); color:#083731; border:1px solid #083731; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:12px;">STD</span>'

                    st.markdown(f"""
                    <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:10px; padding:10px 14px; margin:8px 0; box-shadow:0 2px 8px rgba(8,55,49,0.04);">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <strong style="color:#083731; font-size:0.95rem;">{acc['email']}</strong><br>
                                <span style="font-size:0.78rem; color:#64748B;">{acc['sender_name']}</span>
                            </div>
                            <div>{warmup_badge}</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    st.progress(pct, text=f"{sent_today}/{eff_limit} sent today (Target: {target_limit}/day)")

                    col_b1, col_b2, col_b3 = st.columns(3)
                    with col_b1:
                        if st.button("🧪 Test", key=f"sb_test_{acc_id}", use_container_width=True):
                            ok, msg = test_smtp_connection(acc["smtp_host"], acc["smtp_port"], acc["email"], acc["password"])
                            if ok:
                                st.success("Verified!")
                            else:
                                st.error("Failed!")
                    with col_b2:
                        if acc["is_active"]:
                            if st.button("⏸️ Pause", key=f"sb_p_{acc_id}", use_container_width=True):
                                update_smtp_account(acc_id, is_active=False)
                                st.rerun()
                        else:
                            if st.button("▶️ Active", key=f"sb_a_{acc_id}", use_container_width=True):
                                update_smtp_account(acc_id, is_active=True)
                                st.rerun()
                    with col_b3:
                        if st.session_state.get(f"confirm_del_acc_{acc_id}"):
                            if st.button("Confirm", key=f"sb_conf_d_{acc_id}", use_container_width=True):
                                delete_smtp_account(acc_id)
                                st.session_state[f"confirm_del_acc_{acc_id}"] = False
                                st.rerun()
                        else:
                            if st.button("🗑️ Del", key=f"sb_d_{acc_id}", use_container_width=True):
                                st.session_state[f"confirm_del_acc_{acc_id}"] = True
                                st.rerun()

                    if acc.get("password_undecryptable"):
                        st.error(f"⚠️ Credentials for {acc['email']} could not be decrypted with current keychain key. Please expand below to re-enter password.")

                    is_undecryptable = bool(acc.get("password_undecryptable"))
                    with st.expander(f"Edit {acc['email']}", expanded=is_undecryptable):
                        with st.form(f"sb_edit_acc_{acc_id}"):
                            e_name = st.text_input("Display Name", value=acc["sender_name"])
                            e_pass = st.text_input("New Password (leave blank to keep current)", type="password", key=f"sb_wp_{acc_id}")
                            e_daily_limit = st.number_input("Daily Limit", min_value=1, max_value=500, value=int(acc.get("daily_limit", 80)))
                            e_warmup_on = st.checkbox("Warmup Enabled", value=bool(acc.get("warmup_enabled")), key=f"sb_wo_{acc_id}")
                            e_w_start = st.number_input("Starting Cap", min_value=1, max_value=100, value=int(acc.get("warmup_starting_limit") or 10), key=f"sb_ws_{acc_id}")
                            e_w_inc = st.number_input("Daily Increment", min_value=1, max_value=50, value=int(acc.get("warmup_daily_increment") or 5), key=f"sb_wi_{acc_id}")
                            e_w_target = st.number_input("Target Cap", min_value=5, max_value=300, value=int(acc.get("warmup_target_limit") or 50), key=f"sb_wt_{acc_id}")
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
                                st.success("Updated!")
                                st.rerun()

        # SECTION 3: SYSTEM DIAGNOSTICS & TELEMETRY
        with st.expander("📡 Live System Diagnostics", expanded=False):
            trk_online = is_port_in_use(8502)
            if trk_online:
                st.markdown("<div style='margin-bottom:8px;'><span style='color:#059669; font-weight:700;'>🟢 Tracking Active</span></div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='margin-bottom:8px;'><span style='color:#DC2626; font-weight:700;'>🔴 Tracking Server Offline</span></div>", unsafe_allow_html=True)
                if st.button("Start Tracking Server", key="sb_start_trk", use_container_width=True):
                    start_tracking_server(port=8502)
                    st.rerun()

            with st.expander("⚙️ Advanced Telemetry Settings", expanded=False):
                st.caption("Telemetry daemon listens on local port 8502.")
                sb_curr_base = get_tracking_base_url()
                sb_new_base = st.text_input("Tracking Public Base URL", value=sb_curr_base, key="sb_trk_url")
                if sb_new_base.strip() and sb_new_base.strip() != sb_curr_base:
                    if st.button("Update Base URL", key="sb_btn_trk_url", use_container_width=True):
                        set_config("tracking_base_url", sb_new_base.strip())
                        st.success("Base URL updated!")
                        st.rerun()

        # SECTION 4: CORPORATE HTML SIGNATURE STUDIO
        with st.expander("✒️ Corporate HTML Signature", expanded=False):
            sb_sig_key = "sig_shared_content"
            if sb_sig_key not in st.session_state:
                st.session_state[sb_sig_key] = current_configs.get("signature_html", "")

            col_sig1, col_sig2 = st.columns([1.6, 1])
            with col_sig1:
                if st.button("📋 Load Sellomize Template", key="sb_load_sig_btn", use_container_width=True):
                    sample_sig = """<div>
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
          <div>✉️ <a href="mailto:info@sellomize.com" style="color:#083731; text-decoration:none;">info@sellomize.com</a></div>
          <div>📞 +1 646-351-0812</div>
          <div>🌐 <a href="https://sellomize.com" target="_blank" style="color:#083731; text-decoration:none;">sellomize.com</a></div>
        </div>
      </td>
    </tr>
  </tbody>
</table>
</div>"""
                    st.session_state[sb_sig_key] = sample_sig
                    set_config("signature_html", sample_sig)
                    st.success("Loaded & Saved!")
                    st.rerun()
            with col_sig2:
                confirm_clear_sig = st.checkbox("Confirm clear", key="chk_confirm_clear_sig")
                if st.button("🗑️ Clear", key="sb_clear_sig_btn", use_container_width=True, disabled=not confirm_clear_sig):
                    st.session_state[sb_sig_key] = ""
                    set_config("signature_html", "")
                    st.info("Cleared!")
                    st.rerun()

            sb_sig_txt = st.text_area("HTML Signature Code", value=st.session_state[sb_sig_key], height=160, key="sb_sig_textarea")
            st.session_state[sb_sig_key] = sb_sig_txt
            if st.button("💾 Save Signature", type="primary", use_container_width=True, key="sb_save_sig_btn"):
                set_config("signature_html", sb_sig_txt)
                st.success("✅ Signature saved!")
                st.rerun()

            # LIVE VISUAL PREVIEW OF HTML SIGNATURE
            st.markdown("<hr style='margin: 0.8rem 0; opacity: 0.2;'>", unsafe_allow_html=True)
            st.markdown("##### 👁️ Live Visual Signature Preview")
            active_sig = st.session_state[sb_sig_key].strip() or get_config("signature_html", "").strip()
            if active_sig:
                render_html_preview(active_sig, height=200)
            else:
                st.caption("No signature saved yet. Load the template above or paste custom HTML.")

        # SECTION 5: GLOBAL NEGATIVE KEYWORDS
        with st.expander("🛡️ Negative Keyword Shield", expanded=False):
            sb_neg_words = st.text_area(
                "Negative Keywords (comma separated)",
                value=current_configs.get("negative_keywords", "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash"),
                height=80,
                key="sb_neg_words_txt"
            )
            sb_spam_words = st.text_area(
                "Spam Words Blocklist",
                value=current_configs.get("spam_blocklist", "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast"),
                height=80,
                key="sb_spam_words_txt"
            )
            if st.button("💾 Save Keywords", type="primary", use_container_width=True, key="sb_save_kw_btn"):
                set_config("negative_keywords", sb_neg_words.strip())
                set_config("spam_blocklist", sb_spam_words.strip())
                st.success("Keywords updated!")
                st.rerun()
