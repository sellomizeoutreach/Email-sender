"""
Campaign Generator Tab for Sellomize Reach.
Allows audience targeting, sequence stage filtering, schedule window setup, variable/spintax injection, and compliance scanning.
"""

from datetime import datetime, timedelta
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

    st.subheader("Campaign Generator")
    st.caption("Select leads from your CRM, choose a template, and generate contextual personalized emails with automated negative keyword scanning.")

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

        # 2. MESSAGE CONTENT (Template + Subject Line)
        col_tpl_sel, col_subj_sel = st.columns([1.2, 1.8])
        with col_tpl_sel:
            template_options = {t["id"]: t["template_name"] for t in templates_list}
            selected_template_id = st.selectbox(
                "Select Email Template *",
                options=list(template_options.keys()),
                format_func=lambda tid: template_options[tid],
                key="camp_template_select"
            )
        with col_subj_sel:
            camp_subject_input = st.text_input(
                "Campaign Subject Line",
                value="Quick observation for [Company]",
                help="Supports dynamic variables like [Company], [Name], and Spintax {Option A|Option B}.",
                key="camp_subject_input"
            )

        # Real-time Template Deliverability & Spintax Pre-Flight Scanner
        chosen_template = get_template_by_id(selected_template_id)
        if chosen_template:
            neg_keywords_setting = get_config("negative_keywords", "")
            sample_contact = get_contact_by_id(selected_contact_ids[0]) if selected_contact_ids else (matching_contacts[0] if matching_contacts else {"name": "Alex", "company": "Acme Corp", "email": "prospect@acme.com"})
            preview_subject = parse_spintax(inject_variables(camp_subject_input.strip() or chosen_template["template_name"], sample_contact))
            preview_body = resolve_template(chosen_template["body_content"], sample_contact)
            combined_sample_text = f"{preview_subject} {preview_body}"
            sample_triggers = scan_all_negative_keywords(combined_sample_text, neg_keywords_setting)

            if sample_triggers:
                trig_chips = ", ".join([f"`{t}`" for t in sample_triggers])
                st.markdown(f"""
                <div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); border-radius:8px; padding:7px 12px; margin:6px 0 10px;">
                    <span style="color:#DC2626; font-weight:700; font-size:0.83rem;">⚠️ Negative Keyword Shield Notice:</span>
                    <span style="color:#475569; font-size:0.8rem;"> Template contains restricted trigger keyword(s) {trig_chips}. Drafts generated from this template will be flagged for review in the Review Queue.</span>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div style="background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.22); border-radius:8px; padding:6px 12px; margin:6px 0 10px;">
                    <span style="color:#059669; font-weight:700; font-size:0.82rem;">✅ Deliverability Shield Clean:</span>
                    <span style="color:#475569; font-size:0.8rem;"> No restricted spam triggers detected in subject line or template copy.</span>
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
                st.markdown(f"**First Send:** `{analysis['first_dt'].strftime('%a, %b %d at %H:%M')}`")
            with col_prev2:
                st.markdown(f"**Final Send:** `{analysis['last_dt'].strftime('%a, %b %d at %H:%M')}`")

            if analysis["fits_today"]:
                st.success(analysis["summary_message"])
            else:
                st.warning(analysis["warning_message"])
        else:
            st.info("0 contacts selected. Select contacts from the audience list above to preview scheduled delivery times.")

        # 4. PRIMARY ACTION (Generate Campaign)
        st.markdown("<br>", unsafe_allow_html=True)
        has_recipients = len(selected_contact_ids) > 0
        btn_help = "Create personalized drafts for selected contacts." if has_recipients else "Select at least one contact above to generate campaign drafts."
        generate_campaign_btn = st.button(
            "Generate Campaign",
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
                set_config("followup_delay_days", str(int(followup_delay_days)))

                chosen_template = get_template_by_id(selected_template_id)
                neg_keywords_setting = get_config("negative_keywords", "")

                st.info(f"Generating personalized emails for {len(selected_contact_ids)} contact(s) using template '{chosen_template['template_name']}'...")
                progress_bar = st.progress(0)

                created_pending = 0
                created_flagged = 0
                flagged_details = []

                # Compute distinct scheduled times for all contacts
                scheduled_dts = calculate_staggered_schedule(
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

                for idx, cid in enumerate(selected_contact_ids):
                    contact = get_contact_by_id(cid)

                    # 1. Deterministic Variable Injection & Spintax Resolution
                    resolved_body = resolve_template(chosen_template["body_content"], contact)
                    final_html = format_email_html(resolved_body)

                    # 2. Subject Line Resolution with Variables & Spintax
                    subj_template = camp_subject_input.strip() if camp_subject_input.strip() else chosen_template["template_name"]
                    final_subject = parse_spintax(inject_variables(subj_template, contact))

                    # 3. Negative Keyword Scanner (detect all triggers)
                    combined_text = f"{final_subject} {final_html}"
                    detected_triggers = scan_all_negative_keywords(combined_text, neg_keywords_setting)

                    if detected_triggers:
                        status = "Flagged"
                        trig_str = ", ".join([f"'{t}'" for t in detected_triggers])
                        notes = f"Flagged for trigger keyword(s): {trig_str}"
                        created_flagged += 1
                        flagged_details.append({
                            "recipient": contact["email"],
                            "triggers": detected_triggers
                        })
                    else:
                        status = "Pending"
                        notes = None
                        created_pending += 1

                    # 4. Use calculated distinct scheduled time
                    sched_dt = scheduled_dts[idx]
                    scheduled_time_str = sched_dt.strftime("%Y-%m-%d %H:%M:%S")

                    create_email(
                        email_html=final_html,
                        subject=final_subject,
                        recipient=contact["email"],
                        status=status,
                        revision_notes=notes,
                        scheduled_time=scheduled_time_str
                    )

                    progress_bar.progress((idx + 1) / len(selected_contact_ids))

                first_res_dt = scheduled_dts[0]
                last_res_dt = scheduled_dts[-1]
                st.success(f"Campaign Generation Complete: Created {created_pending} Pending draft(s) and {created_flagged} Flagged draft(s) scheduled between {first_res_dt.strftime('%A %H:%M')} and {last_res_dt.strftime('%A, %b %d at %H:%M')}.")
                if created_flagged > 0:
                    st.warning(f"{created_flagged} draft(s) were flagged by the Negative Keyword Shield:")
                    for fd in flagged_details[:8]:
                        trig_badges = ", ".join([f"`{t}`" for t in fd["triggers"]])
                        st.markdown(f"- **{fd['recipient']}**: Contains restricted trigger keyword(s) {trig_badges}")
                    if len(flagged_details) > 8:
                        st.caption(f"...and {len(flagged_details) - 8} more.")

                st.markdown("""
                <div style="background:#EFF6FF; border:1.5px solid #3B82F6; border-radius:10px; padding:14px 18px; margin:14px 0;">
                    <div style="font-weight:800; color:#1D4ED8; font-size:0.95rem;">🚀 Campaign Drafts Ready for Approval</div>
                    <div style="color:#1E3A8A; font-size:0.85rem; margin-top:4px; line-height:1.4;">
                        All generated drafts are safely stored in your outreach database.
                        Switch to the <strong>🛡️ Review Queue &amp; Triage</strong> tab at the top of your dashboard to review, edit, or selectively approve drafts for dispatch.
                    </div>
                </div>
                """, unsafe_allow_html=True)

