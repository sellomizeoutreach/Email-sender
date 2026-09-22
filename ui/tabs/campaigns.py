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
    create_sequence_rule,
    get_sequence_rules,
    create_template,
    DB_FILE
)
from scheduler import (
    get_next_valid_sending_datetime,
    calculate_staggered_schedule,
    analyze_schedule_overflow,
    WEEKDAY_NAMES
)
from timezone_helper import (
    TARGET_MARKETS,
    get_market_info,
    get_market_current_time,
    get_time_difference_summary,
    calculate_market_aware_schedule,
    is_within_market_hours
)
from template_engine import (
    resolve_template,
    format_email_html,
    inject_variables,
    parse_spintax,
    scan_negative_keywords,
    scan_all_negative_keywords
)
from ui.components import render_tab_header, render_html_preview


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


def render_touch_composer(
    touch_step: int,
    touch_label: str,
    template_options: dict,
    sample_contact: dict,
    neg_keywords_setting: str,
    default_subj: str,
    default_body: str,
    include_delay: bool = False,
    default_delay_val: int = 3,
    default_delay_unit: str = "Days"
) -> dict:
    """
    Renders an authoring card for an outreach touch.
    Enables user to either select a pre-made template OR write the email copy themselves.
    Supports variables, spintax, live sandboxed preview, spam shield, and optional template saving.
    """
    tpl_keys = list(template_options.keys())
    d_val = default_delay_val
    d_unit = default_delay_unit

    if include_delay:
        col_d1, col_d2, col_d_info = st.columns([1.1, 1.1, 2.8])
        with col_d1:
            d_val = st.number_input(
                f"Wait Delay (after Touch {touch_step - 1}) *",
                min_value=1,
                max_value=720,
                value=default_delay_val,
                key=f"camp_t{touch_step}_val",
                help=f"Wait interval after Touch {touch_step - 1} send before generating this follow-up."
            )
        with col_d2:
            d_unit = st.selectbox(
                "Delay Unit",
                options=["Days", "Hours"],
                index=0 if default_delay_unit.lower() == "days" else 1,
                key=f"camp_t{touch_step}_unit"
            )
        with col_d_info:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            st.caption(f"⚡ Follow-up draft will auto-generate **+{d_val} {d_unit.lower()}** after Touch {touch_step - 1} is sent if no reply is received.")

    col_m1, col_m2 = st.columns([2.2, 1.8])
    with col_m1:
        author_mode = st.radio(
            f"Select Drafting Mode for {touch_label}",
            options=["📋 Select Pre-Made Template", "✍️ Write Email / Copy Yourself"],
            index=0,
            horizontal=True,
            key=f"camp_author_mode_{touch_step}"
        )

    if author_mode.startswith("📋"):
        col_tpl, col_subj = st.columns([1.2, 1.8])
        with col_tpl:
            def_idx = min(touch_step - 1, len(tpl_keys) - 1) if tpl_keys else 0
            sel_tpl_id = st.selectbox(
                "Select Pre-Made Template *",
                options=tpl_keys,
                index=def_idx,
                format_func=lambda tid: template_options[tid],
                key=f"camp_tpl_{touch_step}"
            )
        with col_subj:
            subj_val = st.text_input(
                "Subject Line",
                value=default_subj,
                help="Supports [Name], [Company], and {A|B} Spintax.",
                key=f"camp_subj_{touch_step}"
            )

        chosen_tpl = get_template_by_id(sel_tpl_id)
        raw_body = chosen_tpl["body_content"] if chosen_tpl else ""
        raw_subj = subj_val.strip() or (chosen_tpl["template_name"] if chosen_tpl else "Outreach")

        preview_subj = parse_spintax(inject_variables(raw_subj, sample_contact))
        preview_body = resolve_template(raw_body, sample_contact)
        final_html = format_email_html(preview_body)
        triggers = scan_all_negative_keywords(f"{preview_subj} {preview_body}", neg_keywords_setting)

        with st.expander(f"👁️ Preview with Sample Lead ({sample_contact.get('name', 'Alex')})", expanded=False):
            st.markdown(f"**Subject:** `{preview_subj}`")
            st.markdown("**Body Preview:**")
            render_html_preview(final_html, height=180)

        if triggers:
            trig_chips = ", ".join([f"`{t}`" for t in triggers])
            st.warning(f"⚠️ {touch_label} contains restricted spam trigger(s): {trig_chips}. Drafts will be flagged for review.")
        else:
            st.markdown("""
            <div style="background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.22); border-radius:8px; padding:6px 12px; margin:4px 0 10px;">
                <span style="color:#059669; font-weight:700; font-size:0.82rem;">✅ Deliverability Shield Clean:</span>
                <span style="color:#475569; font-size:0.8rem;"> No restricted spam triggers detected.</span>
            </div>
            """, unsafe_allow_html=True)

        return {
            "step": touch_step,
            "label": touch_label,
            "source_type": "premade",
            "template_id": sel_tpl_id,
            "subject": subj_val,
            "custom_body": "",
            "delay_value": int(d_val) if include_delay else 0,
            "delay_unit": d_unit.lower() if include_delay else "days",
            "save_as_template": False,
            "new_template_name": ""
        }
    else:
        # User writes email themselves
        subj_val = st.text_input(
            "Subject Line *",
            value=default_subj,
            help="Supports [Name], [Company], and {A|B} Spintax.",
            key=f"camp_custom_subj_{touch_step}"
        )

        st.markdown("""
        <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:4px; font-size:0.8rem; color:#475569;">
            <strong style="color:#083731;">Supported Variables:</strong>
            <code>[Name]</code>
            <code>[Company]</code>
            <code>[Website]</code>
            <code>[ASIN]</code>
            <code>[Role]</code>
            <span style="margin-left:8px; color:#64748B;">• Spintax: <code>{Quick note|Quick question|Following up}</code></span>
        </div>
        """, unsafe_allow_html=True)

        body_val = st.text_area(
            f"Email Body Content ({touch_label}) *",
            value=default_body,
            height=180,
            key=f"camp_custom_body_{touch_step}",
            help="Write your custom email content here. Variables like [Name] and [Company] will be personalized per lead."
        )

        col_save_chk, col_save_name = st.columns([1.4, 2.6])
        with col_save_chk:
            save_tpl = st.checkbox("💾 Save as Reusable Template", key=f"camp_save_tpl_{touch_step}", help="Automatically save this copy to your Template Builder library")
        with col_save_name:
            new_tpl_name = ""
            if save_tpl:
                def_tpl_title = f"{touch_label} - {datetime.now().strftime('%b %d')}"
                new_tpl_name = st.text_input("Template Name *", value=def_tpl_title, key=f"camp_new_name_{touch_step}")

        preview_subj = parse_spintax(inject_variables(subj_val, sample_contact))
        preview_body = resolve_template(body_val, sample_contact)
        final_html = format_email_html(preview_body)
        triggers = scan_all_negative_keywords(f"{preview_subj} {preview_body}", neg_keywords_setting)

        with st.expander(f"👁️ Live Preview for {sample_contact.get('name', 'Alex')} ({touch_label})", expanded=True):
            st.markdown(f"**Subject:** `{preview_subj}`")
            st.markdown("**Body Preview:**")
            render_html_preview(final_html, height=180)

        if triggers:
            trig_chips = ", ".join([f"`{t}`" for t in triggers])
            st.warning(f"⚠️ {touch_label} contains restricted spam trigger(s): {trig_chips}. Drafts will be flagged for review.")
        else:
            st.markdown("""
            <div style="background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.22); border-radius:8px; padding:6px 12px; margin:4px 0 10px;">
                <span style="color:#059669; font-weight:700; font-size:0.82rem;">✅ Deliverability Shield Clean:</span>
                <span style="color:#475569; font-size:0.8rem;"> No restricted spam triggers detected.</span>
            </div>
            """, unsafe_allow_html=True)

        return {
            "step": touch_step,
            "label": touch_label,
            "source_type": "custom",
            "template_id": None,
            "subject": subj_val,
            "custom_body": body_val,
            "delay_value": int(d_val) if include_delay else 0,
            "delay_unit": d_unit.lower() if include_delay else "days",
            "save_as_template": bool(save_tpl),
            "new_template_name": new_tpl_name.strip() if save_tpl else ""
        }


def render_campaigns_tab(contacts_list=None, templates_list=None):
    """Render Campaign Generator section of Dispatch & Review."""
    if contacts_list is None:
        contacts_list = get_contacts()
    if templates_list is None:
        templates_list = get_templates()

    render_tab_header("🚀 Dispatch & Review", "Launch outreach campaigns, compose sequences, inspect rendered HTML previews, and triage drafts.")

    if not contacts_list:
        st.warning("You have no contacts saved. Please add contacts in the Contacts tab first.")
    elif not templates_list:
        st.warning("You have no templates saved. Please create a template in the Template Builder tab first.")
    else:
        # Base filter: Exclude Bounced, Closed Lost, and Do Not Contact unless specifically requested
        active_candidates = [
            c for c in contacts_list
            if c.get("status") not in ["Bounced", "Do Not Contact", "Closed Lost"] and not c.get("is_bounced")
        ]
        bounced_excluded_count = len(contacts_list) - len(active_candidates)

        if not active_candidates:
            st.warning("All contacts are currently marked as Bounced, Closed Lost, or Do Not Contact. Add or reactivate leads in the Leads tab.")
            return

        # 1. AUDIENCE SELECTION & TARGETING MODES
        st.markdown("### 1. Who are you emailing?")
        audience_mode = st.radio(
            "Select Outreach Mode",
            [
                "👤 Single Contact (1-to-1 Sequence / Direct Outreach)",
                "🎯 Cherry-Pick Specific Contacts (Direct Multi-Select)",
                "👥 Bulk Segment (Filter by CRM Lifecycle Stage & Tag)"
            ],
            index=0,
            horizontal=True,
            key="camp_audience_mode"
        )

        contact_id_map = {c["id"]: c for c in active_candidates}
        contact_id_keys = list(contact_id_map.keys())

        if audience_mode.startswith("👤 Single Contact"):
            st.caption("Target a single lead directly from your CRM. Perfect for high-touch, personalized 1-to-1 outreach or multi-touch sequences.")
            selected_single_id = st.selectbox(
                "Choose Recipient *",
                options=contact_id_keys,
                format_func=lambda cid: f"{contact_id_map[cid]['name']} ({contact_id_map[cid].get('company') or 'No Company'} — {contact_id_map[cid]['email']})",
                key="camp_single_contact_picker"
            )
            single_lead = contact_id_map[selected_single_id]
            selected_contact_ids = [selected_single_id]
            matching_contacts = [single_lead]

            # Crisp lead dossier card
            st.markdown(f"""
            <div style="background:#FFFFFF; border:1.5px solid rgba(8,55,49,0.18); border-radius:8px; padding:10px 16px; margin:6px 0 14px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;">
                <div>
                    <div style="font-weight:800; color:#083731; font-size:0.95rem;">👤 {single_lead['name']}</div>
                    <div style="font-size:0.82rem; color:#64748B;">{single_lead.get('company') or 'No Company'} • <strong style="color:#083731;">{single_lead['email']}</strong></div>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <span style="background:rgba(8,55,49,0.08); color:#083731; font-weight:700; font-size:0.75rem; padding:3px 9px; border-radius:12px;">CRM Status: {single_lead.get('status') or 'Not Contacted'}</span>
                    <span style="background:rgba(253,77,27,0.1); color:#FD4D1B; font-weight:700; font-size:0.75rem; padding:3px 9px; border-radius:12px;">Sent: {single_lead.get('follow_ups_sent', 0)}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        elif audience_mode.startswith("🎯 Cherry-Pick Specific Contacts"):
            st.caption("Search across your entire CRM and individually select exactly which contacts to email.")
            col_pk1, col_pk2 = st.columns([3.2, 0.8], vertical_alignment="bottom")
            with col_pk1:
                selected_cherry_ids = st.multiselect(
                    "Search and Select Contacts *",
                    options=contact_id_keys,
                    default=[contact_id_keys[0]] if contact_id_keys else [],
                    format_func=lambda cid: f"{contact_id_map[cid]['name']} ({contact_id_map[cid].get('company') or 'No Company'} — {contact_id_map[cid]['email']})",
                    help="Type to search any contact by name, company, or email address.",
                    key="camp_cherry_pick_multisel"
                )
            with col_pk2:
                if st.button("Clear All", key="btn_clear_cherry", use_container_width=True):
                    st.session_state["camp_cherry_pick_multisel"] = []
                    st.rerun()

            selected_contact_ids = selected_cherry_ids
            matching_contacts = [contact_id_map[cid] for cid in selected_cherry_ids]

            st.markdown(f"""
            <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:8px; padding:9px 14px; display:flex; align-items:center; justify-content:space-between; margin:6px 0 12px;">
                <div>
                    <strong style="color:#083731; font-size:0.92rem;">🎯 {len(selected_contact_ids)} specific contact(s) selected</strong>
                    <span style="color:#64748B; font-size:0.82rem; margin-left:6px;">(out of {len(active_candidates)} active CRM contacts)</span>
                </div>
                <span style="font-size:0.78rem; color:#2563EB; font-weight:700;">Handpicked recipients</span>
            </div>
            """, unsafe_allow_html=True)

        else:
            # Mode 3: Bulk Segment (Filter by Stage & Tag)
            stage_options = {
                "all": "All Contacts",
                "new": "New Leads (Never emailed)",
                "followup1": "Contacted (Touch 1 Sent)",
                "followup2": "Follow-Up Sent (2+ Touches)",
                "due": "Due for Follow-Up Today",
                "replied": "Replied / Engaged Leads (Previously Responded)"
            }

            all_distinct_tags = get_all_distinct_tags()
            tag_selector_options = ["-- All Tags --"] + all_distinct_tags

            col_stage_sel, col_tag_sel = st.columns([1.5, 1])
            with col_stage_sel:
                selected_stage_key = st.selectbox(
                    "Filter by CRM Lifecycle Stage",
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
            elif selected_stage_key == "due":
                stage_filtered = [
                    c for c in active_candidates
                    if (c.get("next_follow_up") and c.get("next_follow_up") <= today_str)
                ]
            elif selected_stage_key == "replied":
                stage_filtered = [
                    c for c in active_candidates
                    if (c.get("status") == "Replied" or "Replied" in (c.get("tags") or "") or "Replied" in (c.get("tags_list") or []))
                ]
            else:
                stage_filtered = active_candidates

            # Tag Filtering
            if selected_tag_filter != "-- All Tags --":
                matching_contacts = [c for c in stage_filtered if selected_tag_filter in (c.get("tags_list") or [])]
            else:
                matching_contacts = stage_filtered

            # Quick search inside segment
            camp_search = st.text_input("🔍 Search within this segment (Name, Company, or Email)", placeholder="Type to narrow down leads...", key="camp_seg_search")
            if camp_search.strip():
                q = camp_search.strip().lower()
                matching_contacts = [c for c in matching_contacts if q in c['name'].lower() or q in (c.get('company') or '').lower() or q in c['email'].lower()]

            # Recipient Selection List (Responsive session-state binding without recursive rerun loops)
            if not matching_contacts:
                st.warning("No active contacts match the selected group, tag, or search filter. Adjust filter criteria above to queue contacts.")
                selected_contact_ids = []
            else:
                current_filter = (selected_stage_key, selected_tag_filter, camp_search)
                prev_filter = st.session_state.get("camp_prev_filter")

                if prev_filter != current_filter:
                    for c in matching_contacts:
                        st.session_state[f"camp_chk_{c['id']}"] = True
                    st.session_state["camp_prev_filter"] = current_filter

                for c in matching_contacts:
                    k = f"camp_chk_{c['id']}"
                    if k not in st.session_state:
                        st.session_state[k] = True

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

                # Expandable / Scrollable Individual Checkbox Tagger
                with st.expander(f"📋 Check/Uncheck Individual Contacts ({selected_count} of {len(matching_contacts)} Selected)", expanded=True):
                    st.caption("Check or uncheck individual contacts in this segment:")
                    with st.container(height=260):
                        for c in matching_contacts:
                            cid = c["id"]
                            lbl = f"{c['name']} ({c.get('company') or 'No Company'} — {c['email']}) [Status: {c.get('status') or 'Not Contacted'} | Sent: {c.get('follow_ups_sent', 0)}]"
                            st.checkbox(lbl, key=f"camp_chk_{cid}")

                selected_contact_ids = [c["id"] for c in matching_contacts if st.session_state.get(f"camp_chk_{c['id']}", True)]

        # Sequence Milestone Interval
        col_seq_days, col_seq_info = st.columns([1.2, 2.8])
        with col_seq_days:
            followup_delay_days = st.number_input(
                "Days until next sequence step",
                min_value=1,
                max_value=30,
                value=int(get_config("followup_delay_days", "4") or 4),
                help="When this campaign email is dispatched, recipient Next Follow-Up dates in CRM advance by this interval.",
                key="camp_followup_days_input"
            )
        with col_seq_info:
            next_due_date = (datetime.now() + timedelta(days=int(followup_delay_days))).strftime("%B %d, %Y")
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            st.caption(f"Next follow-up due: **{next_due_date}** (+{followup_delay_days} days). {bounced_excluded_count} bounced/inactive contact(s) automatically protected.")

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

        def_pitch_subj = "Quick observation for [Company]"
        def_pitch_body = (
            "Hi [Name],\n\n"
            "I noticed [Company] and was really impressed by your recent growth.\n\n"
            "We help companies like yours scale their outbound reach and generate consistent enterprise leads without landing in spam.\n\n"
            "Do you have 10 minutes this Thursday or Friday for a brief call?\n\n"
            "Best regards,"
        )

        def_fu1_subj = "Re: Quick observation for [Company]"
        def_fu1_body = (
            "Hi [Name],\n\n"
            "Just following up on my previous note to see if you had a chance to review.\n\n"
            "I know how busy things get—would you be open to a quick 5-minute chat next week to see if this could be relevant for [Company]?\n\n"
            "Best,"
        )

        def_fu2_subj = "Final quick note for [Company]"
        def_fu2_body = (
            "Hi [Name],\n\n"
            "I haven't heard back, so I assume scaling outreach isn't a priority for [Company] right now.\n\n"
            "No worries at all! If your priorities change in the future, feel free to reach back out anytime.\n\n"
            "Wishing you and [Company] continued success!"
        )

        touch_configs = []

        if num_touches == 1:
            st.markdown("##### Touch 1: Initial Pitch")
            cfg1 = render_touch_composer(
                touch_step=1,
                touch_label="Touch 1 (Initial Pitch)",
                template_options=template_options,
                sample_contact=sample_contact,
                neg_keywords_setting=neg_keywords_setting,
                default_subj=def_pitch_subj,
                default_body=def_pitch_body,
                include_delay=False
            )
            touch_configs.append(cfg1)
        else:
            tab_titles = ["Touch 1 (Initial Pitch)", "Touch 2 (Follow-Up 1)"]
            if num_touches == 3:
                tab_titles.append("Touch 3 (Final Follow-Up)")

            touch_ui_tabs = st.tabs(tab_titles)

            with touch_ui_tabs[0]:
                st.caption("Initial outreach message to introduce your agency or service.")
                cfg1 = render_touch_composer(
                    touch_step=1,
                    touch_label="Touch 1 (Initial Pitch)",
                    template_options=template_options,
                    sample_contact=sample_contact,
                    neg_keywords_setting=neg_keywords_setting,
                    default_subj=def_pitch_subj,
                    default_body=def_pitch_body,
                    include_delay=False
                )
                touch_configs.append(cfg1)

            with touch_ui_tabs[1]:
                st.caption("First follow-up email, automatically generated after the configured wait interval if the prospect does not reply to Touch 1.")
                cfg2 = render_touch_composer(
                    touch_step=2,
                    touch_label="Touch 2 (Follow-Up 1)",
                    template_options=template_options,
                    sample_contact=sample_contact,
                    neg_keywords_setting=neg_keywords_setting,
                    default_subj=def_fu1_subj,
                    default_body=def_fu1_body,
                    include_delay=True,
                    default_delay_val=3,
                    default_delay_unit="Days"
                )
                touch_configs.append(cfg2)

            if num_touches == 3:
                with touch_ui_tabs[2]:
                    st.caption("Final follow-up or polite breakup email, automatically generated if no reply is received to Touch 2.")
                    cfg3 = render_touch_composer(
                        touch_step=3,
                        touch_label="Touch 3 (Final Follow-Up)",
                        template_options=template_options,
                        sample_contact=sample_contact,
                        neg_keywords_setting=neg_keywords_setting,
                        default_subj=def_fu2_subj,
                        default_body=def_fu2_body,
                        include_delay=True,
                        default_delay_val=4,
                        default_delay_unit="Days"
                    )
                    touch_configs.append(cfg3)

        st.markdown("""
        <div style="background:rgba(8,55,49,0.04); border:1px solid rgba(8,55,49,0.12); border-radius:8px; padding:8px 12px; margin:8px 0 14px;">
            <span style="font-size:0.82rem; color:#083731; font-weight:700;">🛡️ Intelligent Reply Guard Active:</span>
            <span style="font-size:0.8rem; color:#475569;"> When a prospect replies, subsequent automated follow-ups (Touch 2 &amp; 3) are automatically cancelled. One-time emails, single 1-to-1 outreach, and marketing campaigns are always preserved.</span>
        </div>
        """, unsafe_allow_html=True)

        # 3. DESTINATION MARKET, SENDING WINDOW & DISPATCH PACING
        st.markdown("---")
        st.markdown("### Campaign Destination Market, Sending Window & Cadence Setup")
        st.caption("Configure target country working hours, dispatch pacing intervals, and compliance logging.")

        # Target Market Selector
        st.markdown("#### 1. Where are your prospects located? (Target Country & Timezone)")
        db_default_market = get_config("default_market", "CA_EAST")
        market_keys = list(TARGET_MARKETS.keys())
        def_m_idx = market_keys.index(db_default_market) if db_default_market in market_keys else 0

        col_tm1, col_tm2 = st.columns([1.8, 1.2])
        with col_tm1:
            selected_market_key = st.selectbox(
                "Destination Market & Country *",
                options=market_keys,
                index=def_m_idx,
                format_func=lambda k: TARGET_MARKETS[k]["label"],
                key="camp_target_market_select",
                help="Select target country. Send times will automatically adapt to the recipient's local business hours without time-drift."
            )
        with col_tm2:
            m_info = TARGET_MARKETS[selected_market_key]
            m_now = get_market_current_time(selected_market_key)
            diff_str = get_time_difference_summary(selected_market_key)
            is_m_open, _ = is_within_market_hours(selected_market_key)
            status_badge = "<span style='background:#16A34A; color:white; font-size:0.72rem; font-weight:800; padding:2px 7px; border-radius:10px;'>MARKET OPEN</span>" if is_m_open else "<span style='background:#CA8A04; color:white; font-size:0.72rem; font-weight:800; padding:2px 7px; border-radius:10px;'>MARKET CLOSED</span>"

            st.markdown(f"""
            <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.18); border-radius:8px; padding:9px 12px; margin-top:5px;">
                <div style="font-size:0.75rem; color:#64748B; font-weight:700;">PROSPECT LOCAL TIME {status_badge}</div>
                <div style="font-weight:800; color:#083731; font-size:1.0rem;">{m_now.strftime('%I:%M %p')} <span style="font-size:0.75rem; color:#64748B;">{m_now.strftime('%Z')}</span></div>
                <div style="font-size:0.78rem; color:#475569;">{diff_str}</div>
            </div>
            """, unsafe_allow_html=True)

        bcc_conf = get_config("bcc_email", "")
        if bcc_conf:
            st.markdown(f"""
            <div style="background:rgba(8,55,49,0.05); border:1px solid rgba(8,55,49,0.15); border-radius:6px; padding:6px 12px; margin:6px 0 12px;">
                <span style="font-size:0.8rem; color:#083731; font-weight:700;">📬 Outbound Compliance BCC Active:</span>
                <span style="font-size:0.8rem; color:#0F172A; font-family:monospace;"> {bcc_conf}</span>
                <span style="font-size:0.75rem; color:#64748B; margin-left:8px;">(Configured in Settings)</span>
            </div>
            """, unsafe_allow_html=True)

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

        # Section 2 & 3: Advanced Cadence & Throttle Settings (Progressive Disclosure)
        with st.expander("⚙️ Advanced Cadence & Throttle Settings", expanded=False):
            st.markdown("##### 1. Allowed Working Hours (In Target Market)")
            preset_choice = st.radio(
                "Select Active Sending Schedule",
                ["Business Days (Mon - Fri, 09:00 - 17:00 Target Time)", "24/7 Continuous (All 7 Days)", "Custom Schedule"],
                index=default_preset_idx,
                horizontal=True,
                key="camp_sched_preset"
            )

            if preset_choice.startswith("Business Days"):
                camp_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                camp_start = m_info.get("default_start", "09:00")
                camp_end = m_info.get("default_end", "17:00")
                st.caption(f"Sending restricted strictly to Monday through Friday from {camp_start} to {camp_end} in {m_info.get('country', 'target market')}.")
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
            st.markdown("##### 2. How fast should this batch send? (Batch Dispatch Pacing)")
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
            if selected_market_key != "LOCAL":
                market_schedule_pairs = calculate_market_aware_schedule(
                    total_contacts=n_sel,
                    market_key_or_tz=selected_market_key,
                    stagger_mode=stagger_mode_arg,
                    span_hours=float(span_hours),
                    spacing_minutes=float(spacing_minutes),
                    days=camp_days,
                    start_time=camp_start,
                    end_time=camp_end,
                    use_jitter=False
                )
                preview_schedule = [p[1] for p in market_schedule_pairs]
                market_preview = [p[0] for p in market_schedule_pairs]
            else:
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
                market_preview = preview_schedule

            analysis = analyze_schedule_overflow(
                scheduled_dts=preview_schedule,
                end_time_str=camp_end,
                reference_dt=datetime.now()
            )

            col_prev1, col_prev2 = st.columns(2)
            with col_prev1:
                st.markdown(f"**Touch 1 First Send:**")
                if selected_market_key != "LOCAL":
                    st.markdown(f"• Prospect Local: `{market_preview[0].strftime('%a, %b %d at %I:%M %p %Z')}`")
                    st.markdown(f"• Host PC Send: `{preview_schedule[0].strftime('%a, %b %d at %I:%M %p')}`")
                else:
                    st.markdown(f"`{preview_schedule[0].strftime('%a, %b %d at %H:%M')}`")

            with col_prev2:
                st.markdown(f"**Touch 1 Final Send:**")
                if selected_market_key != "LOCAL":
                    st.markdown(f"• Prospect Local: `{market_preview[-1].strftime('%a, %b %d at %I:%M %p %Z')}`")
                    st.markdown(f"• Host PC Send: `{preview_schedule[-1].strftime('%a, %b %d at %I:%M %p')}`")
                else:
                    st.markdown(f"`{preview_schedule[-1].strftime('%a, %b %d at %H:%M')}`")

            if num_touches >= 2:
                t2_val = touch_configs[1]["delay_value"]
                t2_unit = touch_configs[1]["delay_unit"]
                t2_delta = timedelta(hours=t2_val) if "hour" in t2_unit else timedelta(days=t2_val)
                t2_est = get_next_valid_sending_datetime(
                    base_dt=analysis['first_dt'] + t2_delta,
                    sending_days=camp_days,
                    start_time_str=camp_start,
                    end_time_str=camp_end
                )
                st.markdown(f"**Touch 2 Follow-Up 1 Starts:** `{t2_est.strftime('%a, %b %d at %H:%M')}` (+{t2_val} {t2_unit} after Touch 1 dispatch)")

            if num_touches == 3:
                t3_val = touch_configs[2]["delay_value"]
                t3_unit = touch_configs[2]["delay_unit"]
                t3_delta = timedelta(hours=t3_val) if "hour" in t3_unit else timedelta(days=t3_val)
                t3_est = get_next_valid_sending_datetime(
                    base_dt=t2_est + t3_delta,
                    sending_days=camp_days,
                    start_time_str=camp_start,
                    end_time_str=camp_end
                )
                st.markdown(f"**Touch 3 Final Touch Starts:** `{t3_est.strftime('%a, %b %d at %H:%M')}` (+{t3_val} {t3_unit} after Touch 2 dispatch)")

            if num_touches > 1:
                st.info(f"⚡ Dynamic Send-Triggered Cadence: **{n_sel} Touch 1 outreach draft(s)** will be created immediately in Review Queue. **{n_sel * (num_touches - 1)} automated follow-up rule(s)** will be registered. When Touch 1 is sent, the system automatically starts the delay timer and generates the personalized follow-up with the selected template if no reply has been received.")
            else:
                st.info(f"⚡ Single-Touch Outreach: **{n_sel} draft(s)** will be created and queued in Review Queue.")

            if analysis["fits_today"]:
                st.success(analysis["summary_message"])
            else:
                st.warning(analysis["warning_message"])
        else:
            st.info("0 contacts selected. Select contacts from the audience list above to preview scheduled delivery times.")

        # 4. PRIMARY ACTION (Generate Campaign)
        st.markdown("<br>", unsafe_allow_html=True)
        has_recipients = len(selected_contact_ids) > 0
        if len(selected_contact_ids) == 1 and matching_contacts:
            recip_name = matching_contacts[0]["name"]
            if num_touches > 1:
                btn_title = f"🚀 Create {num_touches}-Touch Sequence for {recip_name}"
            else:
                btn_title = f"🚀 Generate Outreach Email for {recip_name}"
            btn_help = f"Generate {num_touches} personalized draft(s) for {recip_name}."
        else:
            btn_title = f"🚀 Generate Campaign ({num_touches}-Touch Sequence for {len(selected_contact_ids)} Contacts)"
            btn_help = f"Create {num_touches}-touch sequence drafts for {len(selected_contact_ids)} selected contacts." if has_recipients else "Select at least one contact above to generate campaign drafts."

        generate_campaign_btn = st.button(
            btn_title,
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
                    set_config("followup_delay_days", str(int(touch_configs[1]["delay_value"])))
                    batch_seq_id = f"seq_{uuid.uuid4().hex[:8]}"
                else:
                    batch_seq_id = ""

                neg_keywords_setting = get_config("negative_keywords", "")

                total_contacts = len(selected_contact_ids)
                st.info(f"Generating outreach for {total_contacts} contact(s)...")
                progress_bar = st.progress(0)

                created_pending = 0
                created_flagged = 0
                flagged_details = []
                registered_rules = 0

                # Save any custom email copy as reusable templates if requested by user
                for cfg in touch_configs:
                    if cfg.get("save_as_template") and cfg.get("custom_body") and cfg.get("new_template_name"):
                        tpl_name = cfg["new_template_name"].strip()
                        if tpl_name:
                            created_tpl_id = create_template(tpl_name, cfg["custom_body"])
                            cfg["template_id"] = created_tpl_id

                # Compute distinct scheduled times for Touch 1 across all contacts
                if selected_market_key != "LOCAL":
                    market_schedule_pairs = calculate_market_aware_schedule(
                        total_contacts=total_contacts,
                        market_key_or_tz=selected_market_key,
                        stagger_mode=stagger_mode_arg,
                        span_hours=float(span_hours),
                        spacing_minutes=float(spacing_minutes),
                        days=camp_days,
                        start_time=camp_start,
                        end_time=camp_end,
                        use_jitter=use_jitter
                    )
                    scheduled_dts_touch1 = [p[1] for p in market_schedule_pairs]
                else:
                    scheduled_dts_touch1 = calculate_staggered_schedule(
                        total_contacts=total_contacts,
                        stagger_mode=stagger_mode_arg,
                        base_dt=datetime.now(),
                        span_hours=float(span_hours),
                        spacing_minutes=float(spacing_minutes),
                        sending_days=camp_days,
                        start_time_str=camp_start,
                        end_time_str=camp_end,
                        use_jitter=use_jitter
                    )

                m_tz = m_info.get("timezone", "LOCAL")
                m_country = m_info.get("country", "")

                for idx, cid in enumerate(selected_contact_ids):
                    contact = get_contact_by_id(cid)
                    if not contact:
                        continue

                    t1_dt = scheduled_dts_touch1[idx]
                    t1_cfg = touch_configs[0]
                    if t1_cfg.get("source_type") == "custom" or t1_cfg.get("custom_body"):
                        raw_subj_1 = t1_cfg["subject"].strip() or "Quick observation for [Company]"
                        raw_body_1 = t1_cfg["custom_body"]
                    else:
                        step1_tpl = get_template_by_id(t1_cfg["template_id"])
                        raw_subj_1 = t1_cfg["subject"].strip() or (step1_tpl["template_name"] if step1_tpl else "Partnership Outreach")
                        raw_body_1 = step1_tpl["body_content"] if step1_tpl else ""

                    # Resolve Spintax & Variables for Touch 1
                    resolved_body_1 = resolve_template(raw_body_1, contact)
                    final_html_1 = format_email_html(resolved_body_1)
                    final_subj_1 = parse_spintax(inject_variables(raw_subj_1, contact))

                    # Negative keyword scanner for Touch 1
                    combined_text_1 = f"{final_subj_1} {final_html_1}"
                    triggers_1 = scan_all_negative_keywords(combined_text_1, neg_keywords_setting)

                    if triggers_1:
                        status = "Flagged"
                        trig_str = ", ".join([f"'{t}'" for t in triggers_1])
                        notes = f"Touch 1/{num_touches}: Flagged for trigger keyword(s): {trig_str}" if num_touches > 1 else f"Flagged for trigger keyword(s): {trig_str}"
                        created_flagged += 1
                        flagged_details.append({
                            "recipient": contact["email"],
                            "touch": "Touch 1 (Initial Pitch)",
                            "triggers": triggers_1
                        })
                    else:
                        status = "Pending"
                        if num_touches > 1:
                            notes = f"Sequence Touch 1/{num_touches}"
                        elif total_contacts == 1:
                            notes = "One-Time Outreach Email"
                        else:
                            notes = "Marketing Campaign Email"
                        created_pending += 1

                    sched_time_str_1 = t1_dt.strftime("%Y-%m-%d %H:%M:%S")

                    # Create Touch 1 Email in emails table
                    t1_email_id = create_email(
                        email_html=final_html_1,
                        subject=final_subj_1,
                        recipient=contact["email"],
                        status=status,
                        revision_notes=notes,
                        scheduled_time=sched_time_str_1,
                        sequence_step=1,
                        sequence_id=batch_seq_id,
                        target_timezone=m_tz,
                        target_country=m_country,
                        market_key=selected_market_key
                    )

                    # If multi-touch, register subsequent sequence rules to auto-generate upon send
                    if num_touches >= 2:
                        t2_cfg = touch_configs[1]
                        create_sequence_rule(
                            sequence_id=batch_seq_id,
                            contact_id=contact["id"],
                            contact_email=contact["email"],
                            step_number=2,
                            delay_unit=t2_cfg["delay_unit"],
                            delay_value=t2_cfg["delay_value"],
                            template_id=t2_cfg.get("template_id"),
                            custom_subject=t2_cfg["subject"],
                            custom_body=t2_cfg.get("custom_body", ""),
                            trigger_email_id=t1_email_id,
                            target_timezone=m_tz,
                            target_country=m_country,
                            market_key=selected_market_key
                        )
                        registered_rules += 1

                    if num_touches == 3:
                        t3_cfg = touch_configs[2]
                        create_sequence_rule(
                            sequence_id=batch_seq_id,
                            contact_id=contact["id"],
                            contact_email=contact["email"],
                            step_number=3,
                            delay_unit=t3_cfg["delay_unit"],
                            delay_value=t3_cfg["delay_value"],
                            template_id=t3_cfg.get("template_id"),
                            custom_subject=t3_cfg["subject"],
                            custom_body=t3_cfg.get("custom_body", ""),
                            trigger_email_id=None,
                            target_timezone=m_tz,
                            target_country=m_country,
                            market_key=selected_market_key
                        )
                        registered_rules += 1

                    progress_bar.progress((idx + 1) / total_contacts)

                first_res_dt = scheduled_dts_touch1[0]
                last_res_dt = scheduled_dts_touch1[-1]

                if num_touches > 1:
                    st.success(f"Sequence Setup Complete: Created {created_pending} Pending Touch 1 draft(s) (and {created_flagged} Flagged) scheduled between {first_res_dt.strftime('%A %H:%M')} and {last_res_dt.strftime('%A, %b %d at %H:%M')}, and registered {registered_rules} automated follow-up sequence rule(s).")
                    st.markdown(f"""
                    <div style="background:#EFF6FF; border:1.5px solid #3B82F6; border-radius:10px; padding:14px 18px; margin:14px 0;">
                        <div style="font-weight:800; color:#1D4ED8; font-size:0.95rem;">🚀 Automated Follow-Up Sequence Active</div>
                        <div style="color:#1E3A8A; font-size:0.85rem; margin-top:4px; line-height:1.4;">
                            Touch 1 drafts are now ready in the <strong>🛡️ Review Queue &amp; Triage</strong> tab.
                            Once Touch 1 is dispatched, our background engine automatically starts the delay timer ({touch_configs[1]['delay_value']} {touch_configs[1]['delay_unit']}).
                            If the prospect does not reply, the system will <strong>automatically generate the follow-up draft</strong> using your selected template!
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.success(f"Outreach Generation Complete: Created {created_pending} Pending draft(s) (and {created_flagged} Flagged) scheduled between {first_res_dt.strftime('%A %H:%M')} and {last_res_dt.strftime('%A, %b %d at %H:%M')}.")
                    st.markdown("""
                    <div style="background:#EFF6FF; border:1.5px solid #3B82F6; border-radius:10px; padding:14px 18px; margin:14px 0;">
                        <div style="font-weight:800; color:#1D4ED8; font-size:0.95rem;">🚀 Outreach Email Drafts Ready for Review</div>
                        <div style="color:#1E3A8A; font-size:0.85rem; margin-top:4px; line-height:1.4;">
                            Drafts are securely queued in the <strong>🛡️ Review Queue &amp; Triage</strong> tab for approval.
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                if created_flagged > 0:
                    st.warning(f"{created_flagged} draft(s) were flagged by the Negative Keyword Shield:")
                    for fd in flagged_details[:8]:
                        trig_badges = ", ".join([f"`{t}`" for t in fd["triggers"]])
                        st.markdown(f"- **{fd['recipient']}** ({fd['touch']}): Contains restricted trigger keyword(s) {trig_badges}")
                    if len(flagged_details) > 8:
                        st.caption(f"...and {len(flagged_details) - 8} more.")

        # Active Automated Follow-Up Pipeline Monitor
        all_rules = get_sequence_rules()
        if all_rules:
            active_rules = [r for r in all_rules if r.get("status") in ["Waiting_Trigger", "Scheduled"]]
            with st.expander(f"🔄 Automated Follow-Up Pipeline Monitor ({len(active_rules)} Active / {len(all_rules)} Total)", expanded=False):
                st.caption("These rules automatically generate follow-up email drafts once their prerequisite outreach email is sent and the configured wait delay elapses (unless the prospect replies).")
                rule_display_items = []
                for r in all_rules[:60]:
                    rule_display_items.append({
                        "Rule ID": f"#{r['id']}",
                        "Prospect": r.get("contact_email"),
                        "Sequence Step": f"Touch {r.get('step_number')}",
                        "Wait Delay": f"{r.get('delay_value')} {r.get('delay_unit')}",
                        "Trigger Send": r.get("triggered_at") or "Awaiting Touch 1 send",
                        "Scheduled Due": r.get("due_at") or "Pending trigger",
                        "Engine Status": r.get("status")
                    })
                try:
                    import pandas as pd
                    st.dataframe(pd.DataFrame(rule_display_items), use_container_width=True, hide_index=True)
                except Exception:
                    st.write(rule_display_items)


