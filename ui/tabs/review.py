"""
Review Queue & Triage Tab for Sellomize Reach.
Step 3 in the 3-Step Outreach Pipeline:
Master-Detail split pane triage, isolated noise sub-tabs (Clean vs Action Required),
one-click bulk approvals, visual WYSIWYG editor with live preview, and scheduled outbox history.
"""

from datetime import datetime, timedelta
import re
import streamlit as st
try:
    from streamlit_quill import st_quill
    QUILL_AVAILABLE = True
except ImportError:
    QUILL_AVAILABLE = False

from database import (
    get_emails,
    approve_email,
    get_contact_by_email,
    get_config,
    delete_email,
    bulk_delete_emails,
    clear_outbox_emails,
    get_approved_due_emails,
    update_email,
    create_notification
)
from mx_checker import verify_email_domain_mx, get_cached_domain_mx
from template_engine import audit_email_deliverability, scan_all_negative_keywords
from timezone_helper import get_zoneinfo
from ui.components import render_html_preview, trigger_toast


def render_review_tab():
    """Render Step 3: The Triage Desk with 1-click clean approvals, isolated noise sub-tabs, and visual WYSIWYG editor."""
    # Anchor & Smooth Auto-Scroll Handler
    st.markdown('<div id="triage-desk-anchor"></div>', unsafe_allow_html=True)
    if st.session_state.get("scroll_to_triage"):
        st.markdown("""
        <script>
            setTimeout(() => {
                const el = document.getElementById('triage-desk-anchor');
                if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
            }, 120);
        </script>
        """, unsafe_allow_html=True)
        st.session_state["scroll_to_triage"] = False

    with st.container(border=True):
        st.markdown("### 🛡️ Step 3: The Triage Desk (Review Queue & Approvals)")
        st.caption("Inspect pending drafts, audit deliverability, and approve emails before scheduled dispatch.")

        all_emails = get_emails()

        # ==============================================================================
        # 🌟 ZERO-DATA EMPTY STATE (INBOX ZERO)
        # ==============================================================================
        if not all_emails:
            st.markdown("""
            <div style="background:#F0FDF4; border:1.5px solid #BBF7D0; border-radius:12px; padding:36px 24px; text-align:center; margin: 12px 0 16px;">
                <div style="font-size:2.8rem; margin-bottom:10px;">✅</div>
                <div style="font-size:1.35rem; font-weight:800; color:#166534;">Inbox Zero! Generate a new campaign to see drafts here.</div>
                <div style="font-size:0.92rem; color:#475569; max-width:560px; margin:8px auto 20px; line-height:1.5;">
                    There are no email drafts currently queued for review or awaiting dispatch. Target your leads and schedule an outreach sequence in the Generation Wizard above.
                </div>
            </div>
            """, unsafe_allow_html=True)

            col_c1, col_c2, col_c3 = st.columns([1.5, 2, 1.5])
            with col_c2:
                if st.button("🚀 Launch Campaign Wizard", type="primary", use_container_width=True, key="btn_review_empty_launch_wiz"):
                    st.session_state["scroll_to_wizard"] = True
                    st.rerun()
            return

        neg_kw_setting = get_config("negative_keywords", "")

        def is_clean_draft(email):
            if email.get("status") != "Pending":
                return False
            subj = email.get("subject") or ""
            body = email.get("email_html") or ""
            triggers = scan_all_negative_keywords(f"{subj} {body}", neg_kw_setting)
            return len(triggers) == 0

        clean_drafts = [e for e in all_emails if is_clean_draft(e)]
        flagged_drafts = [
            e for e in all_emails
            if e.get("status") in ["Flagged", "Account Mismatch", "Error"]
            or (e.get("status") == "Pending" and not is_clean_draft(e))
        ]
        approved_drafts = [e for e in all_emails if e.get("status") == "Approved"]
        sent_drafts = [e for e in all_emails if e.get("status") == "Sent"]

        clean_count = len(clean_drafts)
        flagged_count = len(flagged_drafts)
        approved_count = len(approved_drafts)
        sent_count = len(sent_drafts)

        # --------------------------------------------------------------------------
        # 1-CLICK BULK APPROVALS BAR (Top of Review Queue)
        # --------------------------------------------------------------------------
        col_app_btn, col_app_info = st.columns([1.8, 2.2], vertical_alignment="center")
        with col_app_btn:
            approve_all_clean_btn = st.button(
                f"🚀 Approve All Clean Drafts ({clean_count})",
                type="primary",
                disabled=(clean_count == 0),
                use_container_width=True,
                help="Bypass manual inspection: schedule all deliverability-verified drafts that passed spam checks.",
                key="btn_approve_all_clean_desk"
            )
        with col_app_info:
            if clean_count > 0:
                st.markdown(
                    f"<div style='font-size:0.86rem; color:#166534; font-weight:700;'>⚡ {clean_count} clean draft(s) passed all safety checks.</div>"
                    f"<div style='font-size:0.78rem; color:#64748B;'>Click to bypass manual review and schedule them all for delivery.</div>",
                    unsafe_allow_html=True
                )
            elif flagged_count > 0:
                st.markdown(
                    f"<div style='font-size:0.86rem; color:#991B1B; font-weight:700;'>⚠️ {flagged_count} item(s) require action in the 'Action Required' tab below.</div>"
                    f"<div style='font-size:0.78rem; color:#64748B;'>Resolve flagged keywords or errors before dispatch.</div>",
                    unsafe_allow_html=True
                )
            else:
                st.caption("✅ No pending clean drafts awaiting review.")

        if approve_all_clean_btn and clean_count > 0:
            approved_num = 0
            for cd in clean_drafts:
                approve_email(
                    email_id=cd["id"],
                    recipient=cd["recipient"],
                    scheduled_time=cd["scheduled_time"],
                    email_html=cd["email_html"],
                    subject=cd["subject"]
                )
                approved_num += 1
            trigger_toast(f"Approved {approved_num} clean draft(s) for scheduled dispatch!", icon="🚀")
            create_notification(
                type="success",
                title="Bulk Approvals Complete",
                message=f"Scheduled {approved_num} clean draft(s) for automated dispatch."
            )
            st.rerun()

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

        # --------------------------------------------------------------------------
        # ISOLATED SUB-TABS (Clean Drafts vs Action Required vs History)
        # --------------------------------------------------------------------------
        tab_clean, tab_flagged, tab_approved, tab_history = st.tabs([
            f"✅ Clean Drafts ({clean_count})",
            f"⚠️ Action Required ({flagged_count})",
            f"📅 Approved & Scheduled ({approved_count})",
            f"📤 Dispatched History ({sent_count})"
        ])

        with tab_clean:
            _render_draft_inspector(clean_drafts, "clean", is_flagged_view=False)

        with tab_flagged:
            _render_draft_inspector(flagged_drafts, "flagged", is_flagged_view=True)

        with tab_approved:
            _render_draft_inspector(approved_drafts, "approved", is_flagged_view=False)

        with tab_history:
            _render_outbox_history(all_emails)


def _render_draft_inspector(drafts_to_show, tab_key_prefix, is_flagged_view=False):
    """Render Master-Detail split pane with visual WYSIWYG editor and expandable raw HTML."""
    if not drafts_to_show:
        if is_flagged_view:
            st.markdown("""
            <div style="background:#F0FDF4; border:1px solid #BBF7D0; border-radius:8px; padding:16px 20px; margin:12px 0;">
                <span style="font-size:0.92rem; color:#166534; font-weight:700;">🎉 Zero Flagged Emails!</span>
                <div style="font-size:0.82rem; color:#15803D; margin-top:3px;">All generated drafts passed the Negative Keyword Shield and MX domain verifications.</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background:rgba(8,55,49,0.04); border:1px solid rgba(8,55,49,0.12); border-radius:8px; padding:14px 18px; margin:12px 0; display:flex; align-items:center; justify-content:space-between;">
                <span style="font-size:0.9rem; color:#083731; font-weight:600;">✅ No drafts found in this category.</span>
                <span style="font-size:0.82rem; color:#64748B;">Generate new outreach batches in Steps 1 &amp; 2 above.</span>
            </div>
            """, unsafe_allow_html=True)
        return

    neg_kw_setting = get_config("negative_keywords", "")
    draft_id_map = {d["id"]: d for d in drafts_to_show}
    draft_ids = list(draft_id_map.keys())

    active_key = f"active_draft_{tab_key_prefix}"
    if active_key not in st.session_state or st.session_state[active_key] not in draft_id_map:
        st.session_state[active_key] = draft_ids[0]

    # Initialize batch selection states
    for d in drafts_to_show:
        sel_k = f"sel_{tab_key_prefix}_{d['id']}"
        if sel_k not in st.session_state:
            st.session_state[sel_k] = True

    selected_draft_ids = [d["id"] for d in drafts_to_show if st.session_state.get(f"sel_{tab_key_prefix}_{d['id']}", False)]

    # ==============================================================================
    # MASTER-DETAIL SPLIT PANE (col1: Master List [1.1], col2: Detail Inspector [2.1])
    # ==============================================================================
    col_master, col_detail = st.columns([1.1, 2.1], gap="medium")

    # --------------------------------------------------------------------------
    # LEFT COLUMN (Master List & Quick Filters)
    # --------------------------------------------------------------------------
    with col_master:
        st.markdown(f"##### 📋 Drafts List ({len(drafts_to_show)})")

        c_sel1, c_sel2 = st.columns(2)
        with c_sel1:
            def _cb_sel_all():
                for d in drafts_to_show:
                    st.session_state[f"sel_{tab_key_prefix}_{d['id']}"] = True
            st.button("Select All", key=f"btn_sel_all_{tab_key_prefix}", on_click=_cb_sel_all, use_container_width=True)

        with c_sel2:
            def _cb_desel_all():
                for d in drafts_to_show:
                    st.session_state[f"sel_{tab_key_prefix}_{d['id']}"] = False
            st.button("Deselect", key=f"btn_desel_all_{tab_key_prefix}", on_click=_cb_desel_all, use_container_width=True)

        # Batch Action Bar if drafts selected
        if selected_draft_ids:
            col_b_act1, col_b_act2 = st.columns(2)
            with col_b_act1:
                if not is_flagged_view and tab_key_prefix == "clean":
                    if st.button(f"⚡ Approve ({len(selected_draft_ids)})", key=f"btn_bulk_app_{tab_key_prefix}", type="primary", use_container_width=True):
                        app_cnt = 0
                        for sid in selected_draft_ids:
                            dr = draft_id_map[sid]
                            approve_email(
                                email_id=dr["id"],
                                recipient=dr["recipient"],
                                scheduled_time=dr["scheduled_time"],
                                email_html=dr["email_html"],
                                subject=dr["subject"]
                            )
                            app_cnt += 1
                        trigger_toast(f"Approved {app_cnt} draft(s)!", icon="⚡")
                        st.rerun()
            with col_b_act2:
                if st.session_state.get(f"confirm_del_batch_{tab_key_prefix}"):
                    if st.button(f"Confirm Delete", type="primary", key=f"btn_conf_del_b_{tab_key_prefix}", use_container_width=True):
                        for sid in selected_draft_ids:
                            delete_email(sid)
                        st.session_state[f"confirm_del_batch_{tab_key_prefix}"] = False
                        trigger_toast(f"Deleted {len(selected_draft_ids)} draft(s).", icon="🗑️")
                        st.rerun()
                else:
                    if st.button(f"🗑️ Delete ({len(selected_draft_ids)})", key=f"btn_del_b_{tab_key_prefix}", use_container_width=True):
                        st.session_state[f"confirm_del_batch_{tab_key_prefix}"] = True
                        st.rerun()

        st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)

        def format_draft_entry(did):
            dr = draft_id_map[did]
            status_icon = "🚨" if dr["status"] in ["Flagged", "Account Mismatch", "Error"] else ("🟢" if dr["status"] == "Approved" else "📄")
            step_tag = f" [T{dr.get('sequence_step')}]" if dr.get("sequence_step", 1) > 1 else ""
            recip = dr.get("recipient") or "No Recipient"
            subj = dr.get("subject") or "No Subject"
            if len(subj) > 16:
                subj = subj[:16] + "…"
            return f"{status_icon} #{did}{step_tag} {recip} — {subj}"

        chosen_active_id = st.radio(
            "Active Draft",
            options=draft_ids,
            format_func=format_draft_entry,
            key=active_key,
            label_visibility="collapsed"
        )

    # --------------------------------------------------------------------------
    # RIGHT COLUMN (Detail Inspector & WYSIWYG Editor)
    # --------------------------------------------------------------------------
    with col_detail:
        active_draft = draft_id_map[chosen_active_id]
        draft_id = active_draft["id"]
        is_flagged = (active_draft["status"] in ["Flagged", "Account Mismatch", "Error"])

        subject_key = f"subj_{tab_key_prefix}_{draft_id}"
        recipient_key = f"recip_{tab_key_prefix}_{draft_id}"
        sched_date_key = f"sdate_{tab_key_prefix}_{draft_id}"
        sched_time_key = f"stime_{tab_key_prefix}_{draft_id}"
        shared_body_key = f"body_{tab_key_prefix}_{draft_id}"

        if shared_body_key not in st.session_state:
            st.session_state[shared_body_key] = active_draft["email_html"]

        current_subject = st.session_state.get(subject_key, active_draft.get("subject", ""))
        current_body = st.session_state[shared_body_key]
        current_live_triggers = scan_all_negative_keywords(f"{current_subject} {current_body}", neg_kw_setting)
        draft_audit = audit_email_deliverability(current_body, subject=current_subject, custom_negative_keywords=neg_kw_setting)

        seq_step = active_draft.get("sequence_step") or 1
        has_seq_id = bool(active_draft.get("sequence_id"))
        if seq_step > 1:
            seq_label = f"Touch {seq_step} (Automated Follow-Up)"
        elif has_seq_id:
            seq_label = "Touch 1 (Sequence Pitch)"
        else:
            seq_label = "One-Time Outreach"

        # Prominent Alert Banner for Negative Keywords or Errors
        if current_live_triggers:
            trig_badges = ", ".join([f'"{t}"' for t in current_live_triggers])
            st.markdown(f"""
            <div style="background:#FFFBEB; border:1.5px solid #F59E0B; border-radius:8px; padding:12px 16px; margin-bottom:12px;">
                <div style="font-weight:700; color:#B45309; font-size:0.92rem;">⚠️ Negative Keyword Shield: Contains restricted word(s): {trig_badges}</div>
                <div style="font-size:0.8rem; color:#92400E; margin-top:3px;">Edit the draft copy below to remove restricted words before approving.</div>
            </div>
            """, unsafe_allow_html=True)
        elif is_flagged:
            st.markdown(f"""
            <div style="background:#FEF2F2; border:1.5px solid #EF4444; border-radius:8px; padding:12px 16px; margin-bottom:12px;">
                <div style="font-weight:700; color:#B91C1C; font-size:0.92rem;">🚨 Action Required: {active_draft['status']}</div>
                <div style="font-size:0.8rem; color:#991B1B; margin-top:2px;">{active_draft.get('error_message') or active_draft.get('revision_notes') or 'Unknown error flagged'}</div>
            </div>
            """, unsafe_allow_html=True)

        # Header Badge
        score_color = "#10B981" if draft_audit["score"] >= 80 else ("#F59E0B" if draft_audit["score"] >= 60 else "#EF4444")
        st.markdown(f"""
        <div style="background:#FFFFFF; border:1px solid rgba(8,55,49,0.16); border-radius:8px; padding:9px 14px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <div>
                <strong style="color:#083731; font-size:0.95rem;">Draft #{draft_id}</strong>
                <span style="color:#64748B; font-size:0.82rem; margin-left:8px;">({seq_label})</span>
            </div>
            <div style="font-size:0.8rem; font-weight:700; color:{score_color};">
                🛡️ Deliverability Score: {draft_audit['score']}/100 ({draft_audit['grade']})
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Recipient & Subject Line
        col_sr1, col_sr2 = st.columns([1.8, 1.2])
        with col_sr1:
            updated_subject = st.text_input("Subject Line", value=active_draft.get("subject", ""), key=subject_key)
        with col_sr2:
            updated_recipient = st.text_input("Recipient Email", value=active_draft.get("recipient") or "", key=recipient_key)

        # Visual WYSIWYG Editor
        if QUILL_AVAILABLE:
            st.markdown("<div style='font-size:0.82rem; font-weight:700; color:#334155; margin:6px 0 2px;'>Visual Email Editor (WYSIWYG)</div>", unsafe_allow_html=True)
            quill_output = st_quill(
                value=current_body,
                html=True,
                key=f"quill_{tab_key_prefix}_{draft_id}"
            )
            if quill_output is not None and quill_output.strip() != "":
                current_body = quill_output
                st.session_state[shared_body_key] = quill_output
        else:
            updated_text = st.text_area(
                "Email Body Content",
                value=st.session_state[shared_body_key],
                height=180,
                key=f"txt_{tab_key_prefix}_{draft_id}"
            )
            current_body = updated_text
            st.session_state[shared_body_key] = updated_text

        # Hidden Raw HTML behind Expander
        with st.expander("</> Edit Raw HTML Source", expanded=False):
            st.caption("Inspect and edit raw HTML tags, inline CSS tables, and tracking markup.")
            raw_html_edit = st.text_area(
                "Raw HTML Code",
                value=current_body,
                height=140,
                key=f"raw_html_{tab_key_prefix}_{draft_id}",
                help="Raw HTML tags and attributes."
            )
            if raw_html_edit != current_body:
                current_body = raw_html_edit
                st.session_state[shared_body_key] = raw_html_edit

        # Rendered Live Preview
        with st.expander("👁️ Live Rendered Preview", expanded=True):
            raw_body = current_body.strip()
            saved_sig = get_config("signature_html", "").strip()
            has_block_tags = any(t in raw_body.lower() for t in ["<p", "<div", "<table", "<br"])
            if not has_block_tags:
                body_formatted = "".join(f"<p style='margin: 0 0 1em 0;'>{p.strip()}</p>" for p in raw_body.split("\n\n") if p.strip())
            else:
                body_formatted = raw_body

            preview_html = f"{body_formatted}<br><br>{saved_sig}" if saved_sig else body_formatted
            render_html_preview(preview_html, height=200)

        # Local Dispatch Date & Time
        local_now = datetime.now().astimezone()
        default_date = local_now.date()
        default_time = (local_now + timedelta(minutes=5)).time()

        if active_draft.get("scheduled_time"):
            try:
                parsed_dt = datetime.strptime(active_draft["scheduled_time"], "%Y-%m-%d %H:%M:%S")
                default_date = parsed_dt.date()
                default_time = parsed_dt.time()
            except Exception:
                pass

        c_date, c_time = st.columns(2)
        with c_date:
            sched_date = st.date_input("Scheduled Date (Local)", value=default_date, key=sched_date_key)
        with c_time:
            sched_time = st.time_input("Scheduled Time (Local)", value=default_time, key=sched_time_key)

        scheduled_datetime_str = f"{sched_date.strftime('%Y-%m-%d')} {sched_time.strftime('%H:%M:%S')}"

        # Action Buttons
        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        col_act1, col_act2, col_act3 = st.columns([1.6, 1.3, 1.3])

        with col_act1:
            approve_btn = st.button(
                "🚀 Approve & Schedule",
                key=f"approve_{tab_key_prefix}_{draft_id}",
                type="primary",
                disabled=bool(current_live_triggers),
                use_container_width=True,
                help="Remove restricted trigger keywords before approval." if current_live_triggers else "Schedule for automated dispatch."
            )
            if approve_btn:
                rec_clean = updated_recipient.strip()
                if not rec_clean:
                    st.error("Please enter a Target Recipient Email before approving.")
                else:
                    is_valid_app, app_reason, _ = verify_email_domain_mx(rec_clean)
                    if not is_valid_app:
                        st.error(f"🚫 Cannot approve: Recipient domain failed pre-flight MX check: {app_reason}.")
                    else:
                        approve_email(
                            email_id=draft_id,
                            recipient=rec_clean,
                            scheduled_time=scheduled_datetime_str,
                            email_html=current_body,
                            subject=updated_subject.strip()
                        )
                        trigger_toast(f"Draft #{draft_id} approved for dispatch!", icon="🚀")
                        st.rerun()

        with col_act2:
            save_btn = st.button(
                "✏️ Save Edits",
                key=f"save_edits_{tab_key_prefix}_{draft_id}",
                use_container_width=True,
                help="Save draft edits without approving yet."
            )
            if save_btn:
                new_status = "Pending" if (active_draft["status"] == "Flagged" and not current_live_triggers) else active_draft["status"]
                update_email(
                    email_id=draft_id,
                    status=new_status,
                    subject=updated_subject.strip(),
                    recipient=updated_recipient.strip(),
                    email_html=current_body,
                    scheduled_time=scheduled_datetime_str
                )
                trigger_toast(f"Edits saved for Draft #{draft_id}.", icon="✏️")
                st.rerun()

        with col_act3:
            if st.session_state.get(f"confirm_del_{tab_key_prefix}_{draft_id}"):
                col_dc1, col_dc2 = st.columns(2)
                with col_dc1:
                    if st.button("Confirm", key=f"conf_del_{tab_key_prefix}_{draft_id}", use_container_width=True):
                        delete_email(draft_id)
                        st.session_state[f"confirm_del_{tab_key_prefix}_{draft_id}"] = False
                        trigger_toast(f"Draft #{draft_id} discarded.", icon="🗑️")
                        st.rerun()
                with col_dc2:
                    if st.button("Cancel", key=f"canc_del_{tab_key_prefix}_{draft_id}", use_container_width=True):
                        st.session_state[f"confirm_del_{tab_key_prefix}_{draft_id}"] = False
                        st.rerun()
            else:
                if st.button("🗑️ Discard Draft", key=f"del_{tab_key_prefix}_{draft_id}", use_container_width=True):
                    st.session_state[f"confirm_del_{tab_key_prefix}_{draft_id}"] = True
                    st.rerun()


def _render_outbox_history(all_emails):
    """Render Dispatched and Historical Outbox Log."""
    col_outbox_hdr, col_dry_run = st.columns([3, 1])
    with col_outbox_hdr:
        st.markdown("##### 📬 Dispatched & Scheduled Outbox History")
        st.caption("Inspect outgoing queue, review historical dispatches, and manually reset failed drafts.")
    with col_dry_run:
        dry_run_btn = st.button("🧪 Dry Run (Check Due)", key="dry_run_outbox", help="Check approved emails due for dispatch without sending.")
        if dry_run_btn:
            curr_local_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            due = get_approved_due_emails(curr_local_time)
            if due:
                st.info(f"🔎 Dry Run: Found {len(due)} approved email(s) currently due for dispatch at {curr_local_time} (Local Time).")
            else:
                st.info(f"🔎 Dry Run: 0 approved emails currently due for dispatch at {curr_local_time} (Local Time).")

    col_outbox_filter, col_outbox_tools = st.columns([1.4, 2.6])
    with col_outbox_filter:
        outbox_filter = st.selectbox(
            "Filter Outbox by Status",
            ["All", "Sent", "Approved", "Flagged", "Account Mismatch", "Error", "Pending"],
            key="outbox_history_filter"
        )

    if outbox_filter == "All":
        filtered_outbox = get_emails()
    else:
        filtered_outbox = get_emails(status=outbox_filter)

    selected_outbox_ids = [item["id"] for item in filtered_outbox if st.session_state.get(f"sel_outbox_{item['id']}", False)]
    s_cnt = len(selected_outbox_ids)

    with col_outbox_tools:
        c_sel_tools, c_del_sel, c_clear_hist = st.columns([1.5, 1.3, 1.4])
        with c_sel_tools:
            c_s1, c_s2 = st.columns(2)
            with c_s1:
                def _cb_ob_sel_all():
                    for item in filtered_outbox:
                        st.session_state[f"sel_outbox_{item['id']}"] = True
                st.button("Select All", key="btn_outbox_sel_all", on_click=_cb_ob_sel_all, use_container_width=True, disabled=not filtered_outbox)
            with c_s2:
                def _cb_ob_desel_all():
                    for item in filtered_outbox:
                        st.session_state[f"sel_outbox_{item['id']}"] = False
                st.button("Deselect", key="btn_outbox_desel_all", on_click=_cb_ob_desel_all, use_container_width=True, disabled=not filtered_outbox)

        with c_del_sel:
            if st.session_state.get("confirm_outbox_bulk_del"):
                if st.button(f"Confirm ({s_cnt})", type="primary", use_container_width=True, key="btn_conf_outbox_del"):
                    deleted = bulk_delete_emails(selected_outbox_ids)
                    for sid in selected_outbox_ids:
                        st.session_state.pop(f"sel_outbox_{sid}", None)
                    st.session_state["confirm_outbox_bulk_del"] = False
                    trigger_toast(f"Deleted {deleted} email(s) from outbox.", icon="🗑️")
                    st.rerun()
            else:
                if st.button(f"🗑️ Delete ({s_cnt})", disabled=(s_cnt == 0), use_container_width=True, key="btn_outbox_del_sel", help="Delete selected emails from outbox"):
                    st.session_state["confirm_outbox_bulk_del"] = True
                    st.rerun()

        with c_clear_hist:
            clear_btn_label = "🧹 Clear Sent" if outbox_filter in ["All", "Sent"] else f"🧹 Clear {outbox_filter}"
            if st.session_state.get("confirm_outbox_clear_all"):
                if st.button("Confirm Clear", type="primary", use_container_width=True, key="btn_conf_outbox_clear"):
                    target_status = "Sent" if outbox_filter in ["All", "Sent"] else outbox_filter
                    deleted = clear_outbox_emails(status=target_status)
                    st.session_state["confirm_outbox_clear_all"] = False
                    trigger_toast(f"Purged {deleted} '{target_status}' email(s) from outbox.", icon="🧹")
                    st.rerun()
            else:
                target_hint = "Sent emails" if outbox_filter in ["All", "Sent"] else f"all '{outbox_filter}' emails"
                if st.button(clear_btn_label, use_container_width=True, key="btn_outbox_clear_all", help=f"Purge historical {target_hint} from SQLite."):
                    st.session_state["confirm_outbox_clear_all"] = True
                    st.rerun()

    if not filtered_outbox:
        st.info("No emails match the selected outbox filter.")
    else:
        for item in filtered_outbox:
            st_class = "badge-pending"
            if item["status"] == "Approved":
                st_class = "badge-approved"
            elif item["status"] == "Sent":
                st_class = "badge-sent"
            elif item["status"] in ["Flagged", "Account Mismatch", "Error"]:
                st_class = "badge-flagged"

            col_chk, col_exp = st.columns([0.04, 0.96], vertical_alignment="top")
            with col_chk:
                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
                st.checkbox(
                    f"Select email #{item['id']}",
                    key=f"sel_outbox_{item['id']}",
                    label_visibility="collapsed",
                    help=f"Select email #{item['id']} for bulk deletion"
                )

            with col_exp:
                with st.expander(f"#{item['id']} | [{item['status'].upper()}] {item['subject']} -> {item.get('recipient') or 'No Recipient'}"):
                    if item.get("created_at"):
                        st.markdown(f"<div class='timestamp-right'>Created: {item['created_at'][:19]}</div>", unsafe_allow_html=True)
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Status:** <span class='{st_class}'>{item['status']}</span>", unsafe_allow_html=True)
                        st.markdown(f"**Recipient:** `{item.get('recipient')}`")
                        if item.get("sent_via"):
                            st.markdown(f"**Dispatched Via:** `{item['sent_via']}`")
                        st.markdown(f"**Scheduled Send Time:** `{item.get('scheduled_time')}`")
                    with c2:
                        if item.get("revision_notes"):
                            st.info(f"**Notes / Trigger:** {item['revision_notes']}")
                        if item.get("error_message"):
                            st.error(f"**Error Details:** {item['error_message']}")

                    st.markdown("**Email Content Preview:**")
                    render_html_preview(item["email_html"], height=200)

                    col_act_left, col_act_right = st.columns([1.2, 1])
                    with col_act_left:
                        if item["status"] in ["Account Mismatch", "Error", "Approved", "Flagged"]:
                            if st.button(f"↩️ Reset #{item['id']} to Pending", key=f"reset_{item['id']}"):
                                update_email(email_id=item["id"], status="Pending", error_message=None)
                                trigger_toast(f"Email #{item['id']} reset to Pending.", icon="↩️")
                                st.rerun()
                    with col_act_right:
                        if st.session_state.get(f"confirm_del_outbox_{item['id']}"):
                            if st.button(f"Confirm Delete #{item['id']}", type="primary", key=f"conf_del_ob_{item['id']}", use_container_width=True):
                                delete_email(item["id"])
                                st.session_state.pop(f"sel_outbox_{item['id']}", None)
                                st.session_state[f"confirm_del_outbox_{item['id']}"] = False
                                trigger_toast(f"Deleted email #{item['id']}.", icon="🗑️")
                                st.rerun()
                        else:
                            if st.button(f"🗑️ Delete Email", key=f"del_ob_{item['id']}", use_container_width=True, help="Permanently delete this email record"):
                                st.session_state[f"confirm_del_outbox_{item['id']}"] = True
                                st.rerun()
