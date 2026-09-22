"""
Campaign Generator Tab for Sellomize Reach.
Allows audience targeting, sequence stage filtering, schedule window setup, variable/spintax injection, and compliance scanning.
"""

from datetime import datetime, timedelta
import uuid
import streamlit as st
from database import (
    get_contacts,
    get_templates,
    get_all_distinct_tags,
    get_config,
    set_config,
    get_template_by_id,
    get_contact_by_id,
    create_email,
    DB_FILE
)
from scheduler import (
    get_next_valid_sending_datetime,
    calculate_staggered_schedule,
    analyze_schedule_overflow,
    WEEKDAY_NAMES
)
from template_engine import (
    resolve_template,
    format_email_html,
    inject_variables,
    parse_spintax,
    scan_negative_keywords,
    scan_all_negative_keywords
)
from ui.components import render_tab_header


def sync_sending_window_to_db(preset: str, days: list, start: str, end: str, db_path: str = DB_FILE):
    """
    Persist the active campaign schedule directly to the SQLite system_config table.
    Ensures the background scheduler daemon and the dashboard campaign generator
    are always 100% synchronized with zero configuration drift.
    """
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


def render_campaigns_tab(contacts_list=None, templates_list=None):
    """Render Tab 3: Sequences & Campaigns."""
    if contacts_list is None:
        contacts_list = get_contacts()
    if templates_list is None:
        templates_list = get_templates()

    render_tab_header("⚡ Sequences & Campaigns", "Configure sending windows, cadence strategy, and generate personalized outreach batches.")

    if not contacts_list:
        st.warning("You have no contacts saved. Please add contacts in the Contacts tab first.")
    elif not templates_list:
        st.warning("You have no templates saved. Please create a template in the Template Builder tab first.")
    else:
        # 1. AUDIENCE SELECTION & SEQUENCE TARGETING
        stage_options = {
            "all": "All Contacts",
            "new": "New Leads (Never emailed)",
            "followup1": "Follow-Up 1 (Emailed once)",
            "followup2": "Follow-Up 2+ (Emailed 2+ times)",
            "opened": "Opened Previous Email",
            "clicked": "Clicked a Link",
            "due": "Due for Follow-Up Today"
        }

        all_distinct_tags = get_all_distinct_tags()
        tag_selector_options = ["-- All Tags --"] + all_distinct_tags

        col_stage_sel, col_tag_sel = st.columns([1.5, 1])
        with col_stage_sel:
            selected_stage_key = st.selectbox(
                "Who to Send To",
                options=list(stage_options.keys()),
                format_func=lambda k: stage_options[k],
                help="Choose which contacts to email. Bounced and unsubscribed contacts are automatically excluded.",
                key="camp_stage_select"
            )

        with col_tag_sel:
            selected_tag_filter = st.selectbox(
                "Filter by Tag",
                options=tag_selector_options,
                key="camp_tag_select"
            )

        # Base filter: Exclude Bounced, Closed Lost, and Do Not Contact unless specifically requested
        active_candidates = [
            c for c in contacts_list
            if c.get("status") not in ["Bounced", "Do Not Contact", "Closed Lost"] and not c.get("is_bounced")
        ]
        bounced_excluded_count = len(contacts_list) - len(active_candidates)

        today_str = datetime.now().strftime("%Y-%m-%d")

        # Stage Filtering
        if selected_stage_key == "new":
            stage_filtered = [
                c for c in active_candidates
                if (c.get("status") in ["Not Contacted", None, ""] or c.get("contacted") in ["No", None, ""] or c.get("follow_ups_sent", 0) == 0)
            ]
        elif selected_stage_key == "followup1":
            stage_filtered = [
                c for c in active_candidates
                if (c.get("follow_ups_sent") == 1 or c.get("status") == "Contacted")
            ]
        elif selected_stage_key == "followup2":
            stage_filtered = [
                c for c in active_candidates
                if (c.get("follow_ups_sent", 0) >= 2 or c.get("status") == "Follow-Up Sent")
            ]
        elif selected_stage_key == "opened":
            stage_filtered = [
                c for c in active_candidates
                if ("Opened" in (c.get("status") or "") or "Interested" in (c.get("status") or ""))
            ]
        elif selected_stage_key == "clicked":
            stage_filtered = [
                c for c in active_candidates
                if ("Clicked" in (c.get("tags") or "") or "Clicked" in (c.get("status") or "") or "Clicked" in (c.get("notes") or ""))
            ]
        elif selected_stage_key == "due":
            stage_filtered = [
                c for c in active_candidates
                if (c.get("next_follow_up") and c.get("next_follow_up") <= today_str)
            ]
        else:
            stage_filtered = active_candidates

        # Tag Filtering
        if selected_tag_filter != "-- All Tags --":
            matching_contacts = [c for c in stage_filtered if selected_tag_filter in (c.get("tags_list") or [])]
        else:
            matching_contacts = stage_filtered

        # Sequence Milestone Interval (Timing System A: Future Sequence Step)
        col_seq_days, col_seq_info = st.columns([1.2, 2.8])
        with col_seq_days:
            followup_delay_days = st.number_input(
                "Days until next sequence step",
                min_value=1,
                max_value=30,
                value=int(get_config("followup_delay_days", "4") or 4),
                help="When this campaign email is dispatched, recipient Next Follow-Up dates in CRM advance by this interval. This does NOT affect when today's email sends.",
                key="camp_followup_days_input"
            )
        with col_seq_info:
            next_due_date = (datetime.now() + timedelta(days=int(followup_delay_days))).strftime("%B %d, %Y")
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            st.caption(f"Next follow-up due: **{next_due_date}** (+{followup_delay_days} days). {bounced_excluded_count} bounced/inactive contact(s) automatically protected.")

        # Recipient Selection List (Responsive session-state binding without recursive rerun loops)
        if not matching_contacts:
            st.warning("No active contacts match the selected group and tag filters. Adjust filter criteria above to queue contacts.")
            selected_contact_ids = []
        else:
            current_filter = (selected_stage_key, selected_tag_filter)
            prev_filter = st.session_state.get("camp_prev_filter")

            # When the user switches stage or tag filters, auto-select all matching contacts in the new view
            if prev_filter != current_filter:
                for c in matching_contacts:
                    st.session_state[f"camp_chk_{c['id']}"] = True
                st.session_state["camp_prev_filter"] = current_filter

            # Pre-populate any matching contacts not yet in session_state
            for c in matching_contacts:
                k = f"camp_chk_{c['id']}"
                if k not in st.session_state:
                    st.session_state[k] = True

            # Calculate currently selected count for summary banner
            current_selected = [c["id"] for c in matching_contacts if st.session_state.get(f"camp_chk_{c['id']}", True)]
            selected_count = len(current_selected)

            col_sel_sum, col_sel_acts = st.columns([2.6, 1.4])
            with col_sel_sum:
                st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:8px; padding:9px 14px; display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                    <div>
                        <strong style="color:#083731; font-size:0.92rem;">{selected_count} contacts selected</strong>
                        <span style="color:#64748B; font-size:0.82rem; margin-left:6px;">(out of {len(matching_contacts)} matching)</span>
                    </div>
                    <span style="font-size:0.78rem; color:#2563EB; font-weight:700;">Ready for batch</span>
                </div>
                """, unsafe_allow_html=True)
            with col_sel_acts:
                c_a1, c_a2 = st.columns(2)
                with c_a1:
                    if st.button("Select All", key="btn_camp_sel_all", use_container_width=True):
                        for c in matching_contacts:
                            st.session_state[f"camp_chk_{c['id']}"] = True
                        st.rerun()
                with c_a2:
                    if st.button("Deselect All", key="btn_camp_desel_all", use_container_width=True):
                        for c in matching_contacts:
                            st.session_state[f"camp_chk_{c['id']}"] = False
                        st.rerun()

            with st.expander(f"{selected_count} contacts selected — view/edit selection", expanded=False):
                st.caption("Scroll to review or deselect specific recipients for this campaign:")
                with st.container(height=240):
                    for c in matching_contacts:
                        cid = c["id"]
                        lbl = f"{c['name']} ({c.get('company') or 'No Company'} - {c['email']}) [Status: {c.get('status') or 'Not Contacted'} | Sent: {c.get('follow_ups_sent', 0)}]"
                        st.checkbox(lbl, key=f"camp_chk_{cid}")

            selected_contact_ids = [c["id"] for c in matching_contacts if st.session_state.get(f"camp_chk_{c['id']}", True)]

        # 2. SEQUENCE CADENCE & MESSAGE CONTENT (Once, Twice, Thrice)
        st.markdown("---")
        st.markdown("### 2. Outreach Sequence & Follow-Up Cadence")
        st.caption("Choose how many emails to send in this sequence. Configure custom templates, subject lines, and wait intervals for each touch.")

        sequence_touches = st.radio(
            "Outreach Frequency / Sequence Touches",
            [
                "Once (1 Email - Single Touch)",
                "Twice (2 Emails - Initial Pitch + 1 Follow-Up)",
                "Thrice (3 Emails - Initial Pitch + 2 Follow-Ups)"
            ],
            index=0,
            horizontal=True,
            key="camp_seq_touches"
        )

        num_touches = 1
        if sequence_touches.startswith("Twice"):
            num_touches = 2
        elif sequence_touches.startswith("Thrice"):
            num_touches = 3

        template_options = {t["id"]: t["template_name"] for t in templates_list}
        tpl_keys = list(template_options.keys())
        neg_keywords_setting = get_config("negative_keywords", "")

        sample_contact = (
            get_contact_by_id(selected_contact_ids[0])
            if selected_contact_ids
            else (matching_contacts[0] if matching_contacts else {"name": "Alex", "company": "Acme Corp", "email": "prospect@acme.com"})
        )

        touch_configs = []

        if num_touches == 1:
            st.markdown("##### Touch 1: Initial Pitch")
            c_t1_tpl, c_t1_subj = st.columns([1.2, 1.8])
            with c_t1_tpl:
                sel_tpl_1 = st.selectbox("Select Template *", options=tpl_keys, format_func=lambda tid: template_options[tid], key="camp_tpl_1")
            with c_t1_subj:
                subj_1 = st.text_input("Subject Line", value="Quick observation for [Company]", help="Supports [Name], [Company], and {A|B} Spintax.", key="camp_subj_1")

            chosen_tpl_1 = get_template_by_id(sel_tpl_1)
            if chosen_tpl_1:
                preview_subj_1 = parse_spintax(inject_variables(subj_1.strip() or chosen_tpl_1["template_name"], sample_contact))
                preview_body_1 = resolve_template(chosen_tpl_1["body_content"], sample_contact)
                triggers_1 = scan_all_negative_keywords(f"{preview_subj_1} {preview_body_1}", neg_keywords_setting)
                if triggers_1:
                    trig_chips = ", ".join([f"`{t}`" for t in triggers_1])
                    st.markdown(f"""
                    <div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); border-radius:8px; padding:7px 12px; margin:4px 0 10px;">
                        <span style="color:#DC2626; font-weight:700; font-size:0.83rem;">⚠️ Negative Keyword Shield Notice:</span>
                        <span style="color:#475569; font-size:0.8rem;"> Template contains trigger(s) {trig_chips}. Drafts will be flagged in Review Queue.</span>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown("""
                    <div style="background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.22); border-radius:8px; padding:6px 12px; margin:4px 0 10px;">
                        <span style="color:#059669; font-weight:700; font-size:0.82rem;">✅ Deliverability Shield Clean:</span>
                        <span style="color:#475569; font-size:0.8rem;"> No restricted spam triggers detected.</span>
                    </div>
                    """, unsafe_allow_html=True)

            touch_configs.append({
                "step": 1,
                "label": "Touch 1 (Initial Pitch)",
                "template_id": sel_tpl_1,
                "subject": subj_1,
                "delay_days": 0
            })
        else:
            tab_titles = ["Touch 1 (Initial Pitch)", "Touch 2 (Follow-Up 1)"]
            if num_touches == 3:
                tab_titles.append("Touch 3 (Final Follow-Up)")

            touch_ui_tabs = st.tabs(tab_titles)

            with touch_ui_tabs[0]:
                st.caption("Initial outreach message to introduce your agency or service.")
                c_t1_tpl, c_t1_subj = st.columns([1.2, 1.8])
                with c_t1_tpl:
                    sel_tpl_1 = st.selectbox("Select Template *", options=tpl_keys, format_func=lambda tid: template_options[tid], key="camp_tpl_1")
                with c_t1_subj:
                    subj_1 = st.text_input("Subject Line", value="Quick observation for [Company]", help="Supports [Name], [Company], and {A|B} Spintax.", key="camp_subj_1")

                chosen_tpl_1 = get_template_by_id(sel_tpl_1)
                if chosen_tpl_1:
                    preview_subj_1 = parse_spintax(inject_variables(subj_1.strip() or chosen_tpl_1["template_name"], sample_contact))
                    preview_body_1 = resolve_template(chosen_tpl_1["body_content"], sample_contact)
                    triggers_1 = scan_all_negative_keywords(f"{preview_subj_1} {preview_body_1}", neg_keywords_setting)
                    if triggers_1:
                        trig_chips = ", ".join([f"`{t}`" for t in triggers_1])
                        st.warning(f"Touch 1 contains negative keyword trigger(s): {trig_chips}")

                touch_configs.append({
                    "step": 1,
                    "label": "Touch 1 (Initial Pitch)",
                    "template_id": sel_tpl_1,
                    "subject": subj_1,
                    "delay_days": 0
                })

            with touch_ui_tabs[1]:
                st.caption("First follow-up email, sent if the prospect has not replied to Touch 1.")
                c_t2_del, c_t2_tpl, c_t2_subj = st.columns([1, 1.2, 1.8])
                with c_t2_del:
                    t2_delay = st.number_input("Days after Touch 1", min_value=1, max_value=30, value=3, key="camp_t2_delay", help="Wait interval before sending this follow-up.")
                with c_t2_tpl:
                    idx_2 = 1 if len(tpl_keys) > 1 else 0
                    sel_tpl_2 = st.selectbox("Select Template *", options=tpl_keys, index=idx_2, format_func=lambda tid: template_options[tid], key="camp_tpl_2")
                with c_t2_subj:
                    def_subj_2 = f"Re: {subj_1.strip()}" if subj_1.strip() else "Following up on [Company]"
                    subj_2 = st.text_input("Subject Line", value=def_subj_2, key="camp_subj_2")

                chosen_tpl_2 = get_template_by_id(sel_tpl_2)
                if chosen_tpl_2:
                    preview_subj_2 = parse_spintax(inject_variables(subj_2.strip() or chosen_tpl_2["template_name"], sample_contact))
                    preview_body_2 = resolve_template(chosen_tpl_2["body_content"], sample_contact)
                    triggers_2 = scan_all_negative_keywords(f"{preview_subj_2} {preview_body_2}", neg_keywords_setting)
                    if triggers_2:
                        trig_chips = ", ".join([f"`{t}`" for t in triggers_2])
                        st.warning(f"Touch 2 contains negative keyword trigger(s): {trig_chips}")

                touch_configs.append({
                    "step": 2,
                    "label": "Touch 2 (Follow-Up 1)",
                    "template_id": sel_tpl_2,
                    "subject": subj_2,
                    "delay_days": int(t2_delay)
                })

            if num_touches == 3:
                with touch_ui_tabs[2]:
                    st.caption("Final follow-up or polite breakup email, sent if no reply to Touch 1 or 2.")
                    c_t3_del, c_t3_tpl, c_t3_subj = st.columns([1, 1.2, 1.8])
                    with c_t3_del:
                        t3_delay = st.number_input("Days after Touch 2", min_value=1, max_value=30, value=4, key="camp_t3_delay", help="Wait interval after Touch 2 before sending this final follow-up.")
                    with c_t3_tpl:
                        idx_3 = 2 if len(tpl_keys) > 2 else (1 if len(tpl_keys) > 1 else 0)
                        sel_tpl_3 = st.selectbox("Select Template *", options=tpl_keys, index=idx_3, format_func=lambda tid: template_options[tid], key="camp_tpl_3")
                    with c_t3_subj:
                        def_subj_3 = "Final quick note for [Company]"
                        subj_3 = st.text_input("Subject Line", value=def_subj_3, key="camp_subj_3")

                    chosen_tpl_3 = get_template_by_id(sel_tpl_3)
                    if chosen_tpl_3:
                        preview_subj_3 = parse_spintax(inject_variables(subj_3.strip() or chosen_tpl_3["template_name"], sample_contact))
                        preview_body_3 = resolve_template(chosen_tpl_3["body_content"], sample_contact)
                        triggers_3 = scan_all_negative_keywords(f"{preview_subj_3} {preview_body_3}", neg_keywords_setting)
                        if triggers_3:
                            trig_chips = ", ".join([f"`{t}`" for t in triggers_3])
                            st.warning(f"Touch 3 contains negative keyword trigger(s): {trig_chips}")

                    touch_configs.append({
                        "step": 3,
                        "label": "Touch 3 (Final Follow-Up)",
                        "template_id": sel_tpl_3,
                        "subject": subj_3,
                        "delay_days": int(t3_delay)
                    })

        st.markdown("""
        <div style="background:rgba(8,55,49,0.04); border:1px solid rgba(8,55,49,0.12); border-radius:8px; padding:8px 12px; margin:8px 0 14px;">
            <span style="font-size:0.82rem; color:#083731; font-weight:700;">🛡️ Intelligent Reply Guard Active:</span>
            <span style="font-size:0.8rem; color:#475569;"> When a prospect replies, all subsequent scheduled sequence follow-ups for that contact are automatically cancelled.</span>
        </div>
        """, unsafe_allow_html=True)

        # 3. SENDING WINDOW & BATCH DISPATCH PACING (Single Visible Panel, Single Source of Truth)
        st.markdown("---")
        st.markdown("### Campaign Sending Window & Cadence Setup")
        st.caption("Define the allowed delivery hours and pacing interval. Settings configured here synchronize directly to the system dispatch engine.")

        # Read current SQLite DB settings for default radio index
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

        # Section 1: Sending Window
        st.markdown("#### 1. When can emails go out? (Sending Window)")
        preset_choice = st.radio(
            "Select Active Sending Schedule",
            ["Business Days (Mon - Fri, 09:00 - 18:00)", "24/7 Continuous (All 7 Days)", "Custom Schedule"],
            index=default_preset_idx,
            horizontal=True,
            key="camp_sched_preset"
        )

        if preset_choice.startswith("Business Days"):
            camp_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            camp_start = "09:00"
            camp_end = "18:00"
            st.caption("Sending restricted strictly to Monday through Friday from 09:00 to 18:00.")
        elif preset_choice.startswith("24/7 Continuous"):
            camp_days = list(WEEKDAY_NAMES)
            camp_start = "00:00"
            camp_end = "23:59"
            st.caption("Continuous dispatch active 24/7 across all 7 days without pauses.")
        else:
            col_sd1, col_sd2, col_sd3 = st.columns([2, 1, 1])
            with col_sd1:
                camp_days = st.multiselect("Allowed Sending Days", WEEKDAY_NAMES, default=[d for d in db_days_list if d in WEEKDAY_NAMES] or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], key="camp_custom_days")
            with col_sd2:
                camp_start = st.text_input("Daily Start Time", value=db_start or "09:00", placeholder="09:00", key="camp_custom_start")
            with col_sd3:
                camp_end = st.text_input("Daily Cutoff Time", value=db_end or "18:00", placeholder="18:00", key="camp_custom_end")

        col_save_sch, col_sch_info = st.columns([1.3, 2.7])
        with col_save_sch:
            if st.button("💾 Save Schedule as Default", key="btn_save_camp_sched", help="Update the background dispatch engine's allowed sending days and hours now."):
                sync_sending_window_to_db(preset_choice, camp_days, camp_start, camp_end)
                st.success("Dispatch schedule saved to system configuration.")
                st.rerun()
        with col_sch_info:
            st.caption("This schedule is also automatically synchronized to the background dispatch engine whenever you generate a campaign.")

        st.markdown("---")
        # Section 2: Batch Dispatch Pacing
        st.markdown("#### 2. How fast should this batch send? (Batch Dispatch Pacing)")
        stagger_strategy = st.radio(
            "Select Dispatch Pacing Strategy",
            [
                "Send all now (immediate batch)",
                "Fixed gap between each send",
                "Spread evenly across a time span",
                "Spread evenly across today's window"
            ],
            key="camp_stagger_strategy"
        )

        if stagger_strategy.startswith("Send all now"):
            span_hours = 0.0
            spacing_minutes = 0.0
            stagger_mode_arg = "none"
            st.caption("All selected drafts will be scheduled for the earliest valid delivery slot.")
        elif stagger_strategy.startswith("Fixed gap"):
            spacing_minutes = st.number_input(
                "Minutes Between Sends",
                min_value=1.0,
                max_value=180.0,
                value=5.0,
                step=1.0,
                key="camp_spacing_mins",
                help="Pause interval between consecutive prospect dispatches in this batch."
            )
            span_hours = 4.0
            stagger_mode_arg = "fixed_interval"
        elif stagger_strategy.startswith("Spread evenly across a time"):
            span_hours = st.number_input(
                "Span Duration (Hours from now)",
                min_value=0.5,
                max_value=168.0,
                value=4.0,
                step=0.5,
                key="camp_span_hours",
                help="Total hours over which all selected contacts in this batch will be evenly distributed."
            )
            spacing_minutes = 5.0
            stagger_mode_arg = "next_x_hours"
        else:
            span_hours = 4.0
            spacing_minutes = 5.0
            stagger_mode_arg = "daily_window"
            if preset_choice.startswith("24/7"):
                st.caption("Emails will be spaced evenly across today's 24-hour window (00:00 – 23:59).")
            else:
                st.caption(f"Emails will be spaced evenly across today's active window ({camp_start} – {camp_end}).")

        use_jitter = st.checkbox(
            "Add natural human jitter",
            value=True,
            help="Adds ±20 to 90 seconds of organic variation so delivery times avoid robotic, fixed-second patterns.",
            key="camp_use_jitter"
        )

        # Real-time Live Schedule Preview & Rollover Analysis
        st.markdown("---")
        st.markdown("##### Live Schedule Preview")
        st.caption("Batch send times are scheduled using the dispatch pacing below (not the future follow-up interval).")
        n_sel = len(selected_contact_ids)
        if n_sel > 0:
            preview_schedule = calculate_staggered_schedule(
                total_contacts=n_sel,
                stagger_mode=stagger_mode_arg,
                base_dt=datetime.now(),
                span_hours=float(span_hours),
                spacing_minutes=float(spacing_minutes),
                sending_days=camp_days,
                start_time_str=camp_start,
                end_time_str=camp_end,
                use_jitter=False
            )
            analysis = analyze_schedule_overflow(
                scheduled_dts=preview_schedule,
                end_time_str=camp_end,
                reference_dt=datetime.now()
            )

            col_prev1, col_prev2 = st.columns(2)
            with col_prev1:
                st.markdown(f"**Touch 1 First Send:** `{analysis['first_dt'].strftime('%a, %b %d at %H:%M')}`")
            with col_prev2:
                st.markdown(f"**Touch 1 Final Send:** `{analysis['last_dt'].strftime('%a, %b %d at %H:%M')}`")

            if num_touches >= 2:
                t2_est = get_next_valid_sending_datetime(
                    base_dt=analysis['first_dt'] + timedelta(days=touch_configs[1]["delay_days"]),
                    sending_days=camp_days,
                    start_time_str=camp_start,
                    end_time_str=camp_end
                )
                st.markdown(f"**Touch 2 Follow-Up 1 Starts:** `{t2_est.strftime('%a, %b %d at %H:%M')}` (+{touch_configs[1]['delay_days']} days)")

            if num_touches == 3:
                t3_est = get_next_valid_sending_datetime(
                    base_dt=t2_est + timedelta(days=touch_configs[2]["delay_days"]),
                    sending_days=camp_days,
                    start_time_str=camp_start,
                    end_time_str=camp_end
                )
                st.markdown(f"**Touch 3 Final Touch Starts:** `{t3_est.strftime('%a, %b %d at %H:%M')}` (+{touch_configs[2]['delay_days']} days after Touch 2)")

            total_drafts_preview = n_sel * num_touches
            st.info(f"⚡ Multi-Touch Cadence: {n_sel} contact(s) × {num_touches} touch(es) = **{total_drafts_preview} total sequence draft(s)** will be generated and queued.")

            if analysis["fits_today"]:
                st.success(analysis["summary_message"])
            else:
                st.warning(analysis["warning_message"])
        else:
            st.info("0 contacts selected. Select contacts from the audience list above to preview scheduled delivery times.")

        # 4. PRIMARY ACTION (Generate Campaign)
        st.markdown("<br>", unsafe_allow_html=True)
        has_recipients = len(selected_contact_ids) > 0
        btn_help = f"Create {num_touches}-touch sequence drafts for selected contacts." if has_recipients else "Select at least one contact above to generate campaign drafts."
        generate_campaign_btn = st.button(
            f"Generate Campaign ({num_touches}-Touch Sequence)",
            type="primary",
            use_container_width=True,
            disabled=not has_recipients,
            help=btn_help
        )
        if not has_recipients:
            st.caption("Select contacts to generate.")

        if generate_campaign_btn:
            if not selected_contact_ids:
                st.error("Please select at least one contact.")
            else:
                # 1. Synchronize sending schedule directly to system_config in SQLite
                sync_sending_window_to_db(preset_choice, camp_days, camp_start, camp_end)

                # 2. Persist user's configured follow-up sequence interval
                if num_touches >= 2:
                    set_config("followup_delay_days", str(int(touch_configs[1]["delay_days"])))

                batch_seq_id = f"seq_{uuid.uuid4().hex[:8]}"
                neg_keywords_setting = get_config("negative_keywords", "")

                total_expected = len(selected_contact_ids) * num_touches
                st.info(f"Generating {num_touches}-touch sequence drafts for {len(selected_contact_ids)} contact(s) ({total_expected} total drafts)...")
                progress_bar = st.progress(0)

                created_pending = 0
                created_flagged = 0
                flagged_details = []

                # Compute distinct scheduled times for Touch 1 across all contacts
                scheduled_dts_touch1 = calculate_staggered_schedule(
                    total_contacts=len(selected_contact_ids),
                    stagger_mode=stagger_mode_arg,
                    base_dt=datetime.now(),
                    span_hours=float(span_hours),
                    spacing_minutes=float(spacing_minutes),
                    sending_days=camp_days,
                    start_time_str=camp_start,
                    end_time_str=camp_end,
                    use_jitter=use_jitter
                )

                processed_count = 0
                for idx, cid in enumerate(selected_contact_ids):
                    contact = get_contact_by_id(cid)
                    if not contact:
                        continue

                    t1_dt = scheduled_dts_touch1[idx]
                    prev_touch_dt = t1_dt

                    for t_idx, cfg in enumerate(touch_configs):
                        step_num = cfg["step"]
                        step_tpl = get_template_by_id(cfg["template_id"])
                        raw_subj = cfg["subject"].strip() or step_tpl["template_name"]

                        # Resolve Spintax & Variables
                        resolved_body = resolve_template(step_tpl["body_content"], contact)
                        final_html = format_email_html(resolved_body)
                        final_subj = parse_spintax(inject_variables(raw_subj, contact))

                        # Negative keyword scanner
                        combined_text = f"{final_subj} {final_html}"
                        triggers = scan_all_negative_keywords(combined_text, neg_keywords_setting)

                        if triggers:
                            status = "Flagged"
                            trig_str = ", ".join([f"'{t}'" for t in triggers])
                            notes = f"Touch {step_num}/{num_touches}: Flagged for trigger keyword(s): {trig_str}"
                            created_flagged += 1
                            flagged_details.append({
                                "recipient": contact["email"],
                                "touch": f"Touch {step_num}",
                                "triggers": triggers
                            })
                        else:
                            status = "Pending"
                            notes = f"Sequence Touch {step_num}/{num_touches}"
                            created_pending += 1

                        if step_num == 1:
                            touch_dt = t1_dt
                        else:
                            delay_d = cfg["delay_days"]
                            cand_dt = prev_touch_dt + timedelta(days=delay_d)
                            touch_dt = get_next_valid_sending_datetime(
                                base_dt=cand_dt,
                                sending_days=camp_days,
                                start_time_str=camp_start,
                                end_time_str=camp_end
                            )
                            prev_touch_dt = touch_dt

                        scheduled_time_str = touch_dt.strftime("%Y-%m-%d %H:%M:%S")

                        create_email(
                            email_html=final_html,
                            subject=final_subj,
                            recipient=contact["email"],
                            status=status,
                            revision_notes=notes,
                            scheduled_time=scheduled_time_str,
                            sequence_step=step_num,
                            sequence_id=batch_seq_id
                        )

                        processed_count += 1
                        progress_bar.progress(processed_count / total_expected)

                first_res_dt = scheduled_dts_touch1[0]
                last_res_dt = scheduled_dts_touch1[-1]
                st.success(f"Sequence Generation Complete: Created {created_pending} Pending draft(s) and {created_flagged} Flagged draft(s) across {num_touches} touch(es). Touch 1 scheduled between {first_res_dt.strftime('%A %H:%M')} and {last_res_dt.strftime('%A, %b %d at %H:%M')}.")
                if created_flagged > 0:
                    st.warning(f"{created_flagged} draft(s) were flagged by the Negative Keyword Shield:")
                    for fd in flagged_details[:8]:
                        trig_badges = ", ".join([f"`{t}`" for t in fd["triggers"]])
                        st.markdown(f"- **{fd['recipient']}** ({fd['touch']}): Contains restricted trigger keyword(s) {trig_badges}")
                    if len(flagged_details) > 8:
                        st.caption(f"...and {len(flagged_details) - 8} more.")

                st.markdown(f"""
                <div style="background:#EFF6FF; border:1.5px solid #3B82F6; border-radius:10px; padding:14px 18px; margin:14px 0;">
                    <div style="font-weight:800; color:#1D4ED8; font-size:0.95rem;">🚀 {num_touches}-Touch Sequence Ready for Review &amp; Approval</div>
                    <div style="color:#1E3A8A; font-size:0.85rem; margin-top:4px; line-height:1.4;">
                        All {total_expected} sequence drafts are securely registered in your outreach queue.
                        Switch to the <strong>🛡️ Review Queue &amp; Triage</strong> tab to review and approve drafts for automated dispatch.
                    </div>
                </div>
                """, unsafe_allow_html=True)

