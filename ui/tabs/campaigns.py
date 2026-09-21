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
    get_template_by_id,
    get_contact_by_id,
    create_email
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


def render_campaigns_tab(contacts_list=None, templates_list=None):
    """Render Tab 3: Sequences & Campaigns."""
    if contacts_list is None:
        contacts_list = get_contacts()
    if templates_list is None:
        templates_list = get_templates()

    st.subheader("🚀 Campaign Generator")
    st.caption("Select leads from your CRM, choose a template, and generate contextual personalized emails with automated negative keyword scanning.")

    if not contacts_list:
        st.warning("⚠️ You have no contacts saved. Please add contacts in the 'Contact Manager' tab first.")
    elif not templates_list:
        st.warning("⚠️ You have no templates saved. Please create a template in the 'Template Builder' tab first.")
    else:
        # Recipient Selection & Audience Targeting
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

        col_stage_sel, col_tag_sel, col_tpl_sel = st.columns([1.2, 1, 1])
        with col_stage_sel:
            selected_stage_key = st.selectbox(
                "🎯 Who to Send To",
                options=list(stage_options.keys()),
                format_func=lambda k: stage_options[k],
                help="Choose which contacts to email. Bounced and unsubscribed contacts are automatically excluded."
            )

        with col_tag_sel:
            selected_tag_filter = st.selectbox(
                "🏷️ Filter by Tag",
                options=tag_selector_options
            )

        with col_tpl_sel:
            template_options = {t["id"]: t["template_name"] for t in templates_list}
            selected_template_id = st.selectbox(
                "Select Email Template *",
                options=list(template_options.keys()),
                format_func=lambda tid: template_options[tid]
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

        # Sequence Cadence / Delay Setting
        col_cadence1, col_cadence2 = st.columns([1.2, 2.8])
        with col_cadence1:
            followup_delay_days = st.number_input(
                "Follow-Up Cadence Interval (Days)",
                min_value=1,
                max_value=30,
                value=4,
                help="When emails are dispatched, recipient Next Follow-Up dates will advance by this interval."
            )
        with col_cadence2:
            next_due_date = (datetime.now() + timedelta(days=int(followup_delay_days))).strftime("%B %d, %Y")
            st.info(f"📅 **Follow-Up Cadence**: Next follow-up will be set for **{next_due_date}** (+{followup_delay_days} days). {bounced_excluded_count} bounced/inactive contact(s) automatically protected.")

        if not matching_contacts:
            st.info("No active contacts match the selected group and tag filters. Adjust filter criteria above to queue contacts.")
            selected_contact_ids = []
        else:
            select_all = st.checkbox(f"Select All Matching Contacts ({len(matching_contacts)})", value=True)
            default_selection = [c["id"] for c in matching_contacts] if select_all else []

            contact_options = {
                c["id"]: f"{c['name']} ({c.get('company') or 'No Company'} - {c['email']}) [Status: {c.get('status') or 'Not Contacted'} | Sent: {c.get('follow_ups_sent', 0)}]"
                for c in matching_contacts
            }

            selected_contact_ids = st.multiselect(
                "Target Contacts to Email *",
                options=list(contact_options.keys()),
                default=default_selection,
                format_func=lambda cid: contact_options.get(cid, str(cid)),
                key=f"camp_contacts_select_{selected_stage_key}_{selected_tag_filter}"
            )

        camp_subject_input = st.text_input(
            "Campaign Subject Line",
            value="Quick observation for [Company]",
            help="Supports dynamic variables like [Company], [Name], and Spintax {Option A|Option B}.",
            key="camp_subject_input"
        )

        # Campaign Sending Window & Cadence Setup
        with st.expander("🕒 Campaign Sending Window & Cadence Setup", expanded=True):
            st.markdown("#### 🗓️ When can emails go out? (Sending Window)")
            preset_choice = st.radio(
                "Select Active Sending Schedule",
                ["💼 Business Days (Mon - Fri, 09:00 - 18:00)", "⚡ 24/7 Continuous (All 7 Days)", "🏖️ Custom Schedule"],
                horizontal=True,
                key="camp_sched_preset"
            )

            if preset_choice.startswith("💼 Business"):
                camp_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                camp_start = "09:00"
                camp_end = "18:00"
                st.caption("✅ Sending restricted strictly to Monday–Friday from 09:00 to 18:00.")
            elif preset_choice.startswith("⚡ 24/7"):
                camp_days = list(WEEKDAY_NAMES)
                camp_start = "00:00"
                camp_end = "23:59"
                st.caption("⚡ Continuous dispatch active 24/7 across all 7 days without pauses.")
            else:
                col_sd1, col_sd2, col_sd3 = st.columns([2, 1, 1])
                with col_sd1:
                    camp_days = st.multiselect("Allowed Sending Days", WEEKDAY_NAMES, default=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], key="camp_custom_days")
                with col_sd2:
                    camp_start = st.text_input("Daily Start Time", value="09:00", placeholder="09:00", key="camp_custom_start")
                with col_sd3:
                    camp_end = st.text_input("Daily Cutoff Time", value="18:00", placeholder="18:00", key="camp_custom_end")

            st.markdown("---")
            st.markdown("#### ⏱️ How fast should they send? (Cadence)")
            stagger_strategy = st.radio(
                "Select Cadence Strategy",
                [
                    "⚡ Send all now",
                    "⏱️ Fixed gap between each send",
                    "⏳ Spread evenly across a time span",
                    "📅 Spread evenly across today's window"
                ],
                key="camp_stagger_strategy"
            )

            if stagger_strategy.startswith("⚡ Send all now"):
                span_hours = 0.0
                spacing_minutes = 0.0
                stagger_mode_arg = "none"
                st.caption("All selected drafts will be scheduled for the earliest valid delivery slot.")
            elif stagger_strategy.startswith("⏱️ Fixed gap"):
                spacing_minutes = st.number_input(
                    "Minutes Between Sends",
                    min_value=1.0,
                    max_value=180.0,
                    value=5.0,
                    step=1.0,
                    key="camp_spacing_mins",
                    help="Pause interval between consecutive prospect dispatches."
                )
                span_hours = 4.0
                stagger_mode_arg = "fixed_interval"
            elif stagger_strategy.startswith("⏳ Spread evenly across a time"):
                span_hours = st.number_input(
                    "Span Duration (Hours from now)",
                    min_value=0.5,
                    max_value=168.0,
                    value=4.0,
                    step=0.5,
                    key="camp_span_hours",
                    help="Total hours over which all selected contacts will be evenly distributed."
                )
                spacing_minutes = 5.0
                stagger_mode_arg = "next_x_hours"
            else:
                span_hours = 4.0
                spacing_minutes = 5.0
                stagger_mode_arg = "daily_window"
                st.caption(f"Emails will be spaced evenly across today's active window ({camp_start} – {camp_end}).")

            use_jitter = st.checkbox(
                "🎲 Add natural human jitter",
                value=True,
                help="Adds ±20 to 90 seconds of organic variation so delivery times avoid robotic, fixed-second patterns.",
                key="camp_use_jitter"
            )

            # Real-time Live Schedule Preview & Rollover Analysis
            st.markdown("---")
            st.markdown("##### 📊 Live Schedule Preview")
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
                    st.success(f"✅ {analysis['summary_message']}")
                else:
                    st.warning(f"⚠️ {analysis['warning_message']}")
            else:
                first_slot = get_next_valid_sending_datetime(
                    base_dt=datetime.now(),
                    sending_days=camp_days,
                    start_time_str=camp_start,
                    end_time_str=camp_end
                )
                st.caption(f"ℹ️ Next available delivery slot: **{first_slot.strftime('%A, %b %d at %H:%M')}** (Local Time). Select contacts above to preview full batch cadence.")

        st.markdown("<br>", unsafe_allow_html=True)
        generate_campaign_btn = st.button("🚀 Generate Campaign", type="primary", use_container_width=True, disabled=(len(matching_contacts) == 0))

        if generate_campaign_btn:
            if not selected_contact_ids:
                st.error("Please select at least one contact.")
            else:
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
                st.success(f"🎉 Campaign Generation Complete! Created **{created_pending}** Pending draft(s) and **{created_flagged}** Flagged draft(s) scheduled between **{first_res_dt.strftime('%A %H:%M')}** and **{last_res_dt.strftime('%A, %b %d at %H:%M')}**.")
                if created_flagged > 0:
                    st.warning(f"⚠️ **{created_flagged} draft(s) were flagged by the Negative Keyword Shield:**")
                    for fd in flagged_details[:8]:
                        trig_badges = ", ".join([f"`{t}`" for t in fd["triggers"]])
                        st.markdown(f"- 🚨 **{fd['recipient']}**: Contains restricted trigger keyword(s) {trig_badges}")
                    if len(flagged_details) > 8:
                        st.caption(f"...and {len(flagged_details) - 8} more.")
                    st.info("💡 Flagged drafts are safely queued in the **🛡️ Review Queue & Triage** tab so you can edit and unblock them before dispatch.")
