"""
Review Queue & Triage Tab for Sellomize Reach.
Dual-pane split preview, visual & HTML source editor, pre-flight MX checks, and scheduled outbox history.
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
    get_approved_due_emails,
    update_email
)
from mx_checker import verify_email_domain_mx, get_cached_domain_mx
from template_engine import audit_email_deliverability, scan_all_negative_keywords
from ui.components import render_html_preview


def render_review_tab():
    """Render Tab 4: Review Queue & Triage Desk."""
    st.subheader("Review Queue & Triage Desk")
    st.caption("Compliance gate: inspect rendered HTML previews, audit domain MX health, and triage flagged drafts before automated dispatch.")

    col_q_filt, col_q_batch = st.columns([2.2, 1.8])
    with col_q_filt:
        review_filter = st.radio(
            "Queue Triage Rail",
            ["All Actionable", "Flagged Only (Action Required)", "Pending Only"],
            horizontal=True,
            key="review_queue_triage_rail"
        )

    if review_filter == "Flagged Only (Action Required)":
        drafts_to_show = get_emails(status="Flagged")
    elif review_filter == "Pending Only":
        drafts_to_show = get_emails(status="Pending")
    else:
        drafts_to_show = [e for e in get_emails() if e["status"] in ["Pending", "Flagged"]]

    # Initialize selection state for each draft (Pending defaults to True, Flagged to False)
    for d in drafts_to_show:
        sel_key = f"sel_draft_{d['id']}"
        if sel_key not in st.session_state:
            st.session_state[sel_key] = (d["status"] == "Pending")

    selected_draft_ids = [d["id"] for d in drafts_to_show if st.session_state.get(f"sel_draft_{d['id']}", False)]
    selected_clean_drafts = [d for d in drafts_to_show if d["id"] in selected_draft_ids and d["status"] == "Pending"]

    with col_q_batch:
        st.write("")
        if drafts_to_show:
            confirm_app_sel = st.checkbox(
                f"Confirm live scheduling for {len(selected_clean_drafts)} selected draft(s)",
                key="chk_confirm_app_sel"
            )
            c_app_b, c_del_b = st.columns([2, 1])
            with c_app_b:
                if st.button(
                    f"⚡ Approve Selected ({len(selected_clean_drafts)})",
                    type="primary",
                    use_container_width=True,
                    disabled=(len(selected_clean_drafts) == 0 or not confirm_app_sel),
                    key="btn_app_selected"
                ):
                    approved_count = 0
                    for cp in selected_clean_drafts:
                        rec = (st.session_state.get(f"recipient_{cp['id']}") or cp.get("recipient") or "").strip()
                        subj = (st.session_state.get(f"subject_{cp['id']}") or cp.get("subject") or "").strip()
                        body = st.session_state.get(f"draft_body_{cp['id']}") or cp.get("email_html") or ""
                        s_date = st.session_state.get(f"sched_date_{cp['id']}")
                        s_time = st.session_state.get(f"sched_time_{cp['id']}")
                        if s_date and s_time:
                            sched_str = f"{s_date.strftime('%Y-%m-%d')} {s_time.strftime('%H:%M:%S')}"
                        else:
                            sched_str = cp.get("scheduled_time")

                        if rec:
                            is_val, _, _ = verify_email_domain_mx(rec)
                            if is_val:
                                approve_email(
                                    email_id=cp["id"],
                                    recipient=rec,
                                    scheduled_time=sched_str,
                                    email_html=body,
                                    subject=subj
                                )
                                approved_count += 1
                    st.success(f"Approved {approved_count} selected draft(s) for scheduled dispatch!")
                    st.rerun()
            with c_del_b:
                if st.button(
                    f"🗑️ Delete ({len(selected_draft_ids)})",
                    use_container_width=True,
                    disabled=(len(selected_draft_ids) == 0),
                    key="btn_bulk_del"
                ):
                    st.session_state["confirm_bulk_del"] = True

    if st.session_state.get("confirm_bulk_del"):
        st.warning(f"Are you sure you want to delete {len(selected_draft_ids)} selected draft(s)? This action cannot be undone.")
        cd1, cd2 = st.columns([1, 1])
        with cd1:
            if st.button("Yes, Delete Selected", type="primary", key="btn_conf_bulk_del", use_container_width=True):
                for sid in selected_draft_ids:
                    delete_email(sid)
                st.session_state["confirm_bulk_del"] = False
                st.warning(f"Deleted {len(selected_draft_ids)} selected draft(s).")
                st.rerun()
        with cd2:
            if st.button("Cancel", key="btn_canc_bulk_del", use_container_width=True):
                st.session_state["confirm_bulk_del"] = False
                st.rerun()

    if not drafts_to_show:
        st.markdown("""
        <div style="background:rgba(8,55,49,0.05); border:1px solid rgba(8,55,49,0.14); border-radius:8px; padding:9px 14px; margin:6px 0 12px; display:flex; align-items:center; justify-content:space-between;">
            <span style="font-size:0.88rem; color:#083731; font-weight:600;">Review queue is clear — no pending drafts awaiting approval.</span>
            <span style="font-size:0.8rem; color:#64748B;">Generate new batches from Sequences & Campaigns</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        col_triage_stat, col_btn_sel_all, col_btn_desel_all = st.columns([2.4, 0.8, 0.8])
        with col_triage_stat:
            st.write(f"Displaying **{len(drafts_to_show)}** email(s) — **{len(selected_clean_drafts)} of {len(drafts_to_show)}** selected to send:")
        with col_btn_sel_all:
            if st.button("Select All Clean", key="btn_sel_all_clean", use_container_width=True):
                for d in drafts_to_show:
                    if d["status"] == "Pending":
                        st.session_state[f"sel_draft_{d['id']}"] = True
                st.rerun()
        with col_btn_desel_all:
            if st.button("Deselect All", key="btn_desel_all", use_container_width=True):
                for d in drafts_to_show:
                    st.session_state[f"sel_draft_{d['id']}"] = False
                st.rerun()

        neg_kw_setting = get_config("negative_keywords", "")

        for draft in drafts_to_show:
            draft_id = draft["id"]
            is_flagged = draft["status"] == "Flagged"
            subject_key = f"subject_{draft_id}"
            recipient_key = f"recipient_{draft_id}"
            sched_date_key = f"sched_date_{draft_id}"
            sched_time_key = f"sched_time_{draft_id}"
            editor_mode_key = f"editor_mode_{draft_id}"
            shared_body_key = f"draft_body_{draft_id}"

            if shared_body_key not in st.session_state:
                st.session_state[shared_body_key] = draft["email_html"]

            current_subject = st.session_state.get(subject_key, draft.get("subject", ""))
            current_body = st.session_state[shared_body_key]
            current_live_triggers = scan_all_negative_keywords(f"{current_subject} {current_body}", neg_kw_setting)

            draft_audit = audit_email_deliverability(current_body, subject=current_subject, custom_negative_keywords=neg_kw_setting)

            if is_flagged:
                if current_live_triggers:
                    trig_title_str = ", ".join(f"'{t}'" for t in current_live_triggers)
                    prefix = f"🚨 FLAGGED [{trig_title_str}]"
                elif draft.get("revision_notes"):
                    prefix = f"🚨 FLAGGED ({draft['revision_notes']})"
                else:
                    prefix = "🚨 FLAGGED"
            else:
                prefix = "📄 Draft"

            container_title = f"{prefix} #{draft_id} (🛡️ {draft_audit['score']}/100) - {current_subject} -> {draft.get('recipient')}"

            col_chk, col_exp = st.columns([0.05, 0.95], vertical_alignment="top")
            with col_chk:
                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
                st.checkbox(
                    f"Select draft #{draft_id}",
                    key=f"sel_draft_{draft_id}",
                    label_visibility="collapsed",
                    disabled=bool(current_live_triggers),
                    help="Check to approve and send this email. Uncheck to hold or skip."
                )

            with col_exp:
                with st.expander(container_title, expanded=is_flagged):
                    # DUAL-PANE SPLIT PREVIEW WORKSPACE
                    col_editor, col_preview = st.columns([1, 1], gap="medium")

                    with col_editor:
                        st.markdown("##### ✏️ Draft Copy & Recipient")
                        updated_subject = st.text_input("Subject Line", value=draft["subject"], key=subject_key)

                        col_r1, col_r2 = st.columns([1.8, 1.2])
                        with col_r1:
                            updated_recipient = st.text_input("Target Recipient Email", value=draft.get("recipient") or "", placeholder="client@target.com", key=recipient_key)
                        with col_r2:
                            st.write("")
                            r_email = updated_recipient.strip()
                            if r_email:
                                c_record = get_contact_by_email(r_email)
                                if c_record and "Invalid MX" in (c_record.get("tags_list") or []):
                                    st.markdown("<div style='margin-top:10px;'><span style='color:#DC2626; font-size:0.8rem; font-weight:700;'>🔴 Dead Domain (Tagged 'Invalid MX')</span></div>", unsafe_allow_html=True)
                                else:
                                    cached_mx = get_cached_domain_mx(r_email)
                                    if cached_mx is not None:
                                        is_val, rec_reason = cached_mx
                                        if is_val:
                                            st.markdown("<div style='margin-top:10px;'><span style='color:#059669; font-size:0.8rem; font-weight:700;'>🟢 Domain MX Active (Cached)</span></div>", unsafe_allow_html=True)
                                        else:
                                            st.markdown(f"<div style='margin-top:10px;'><span style='color:#DC2626; font-size:0.8rem; font-weight:700;'>🔴 Dead Domain: {rec_reason}</span></div>", unsafe_allow_html=True)
                                    else:
                                        st.markdown("<div style='margin-top:10px;'><span style='color:#64748B; font-size:0.8rem;'>⚪ MX Pre-flight on Approval</span></div>", unsafe_allow_html=True)

                        local_now = datetime.now().astimezone()
                        default_date = local_now.date()
                        default_time = (local_now + timedelta(minutes=5)).time()

                        if draft.get("scheduled_time"):
                            try:
                                parsed_dt = datetime.strptime(draft["scheduled_time"], "%Y-%m-%d %H:%M:%S")
                                default_date = parsed_dt.date()
                                default_time = parsed_dt.time()
                            except Exception:
                                pass

                        c_date, c_time = st.columns(2)
                        with c_date:
                            sched_date = st.date_input("Date (Local)", value=default_date, key=sched_date_key)
                        with c_time:
                            sched_time = st.time_input("Time (Local)", value=default_time, key=sched_time_key)

                        scheduled_datetime_str = f"{sched_date.strftime('%Y-%m-%d')} {sched_time.strftime('%H:%M:%S')}"

                        editor_mode = st.radio(
                            "View Mode",
                            ["Visual", "HTML Source"],
                            horizontal=True,
                            key=editor_mode_key
                        )

                        if editor_mode == "Visual":
                            if QUILL_AVAILABLE:
                                st.caption("Visual WYSIWYG Editor")
                                quill_content = st_quill(
                                    value=st.session_state[shared_body_key],
                                    html=True,
                                    key=f"quill_email_{draft_id}"
                                )
                                if quill_content is not None:
                                    st.session_state[shared_body_key] = quill_content
                            else:
                                st.caption("Visual Preview & Editor")
                                edited_txt = st.text_area(
                                    "Visual / Plain Text Content",
                                    value=st.session_state[shared_body_key],
                                    height=220,
                                    key=f"text_email_{draft_id}"
                                )
                                st.session_state[shared_body_key] = edited_txt
                        else:
                            st.caption("HTML Source Mode")
                            source_content = st.text_area(
                                "Raw HTML Code",
                                value=st.session_state[shared_body_key],
                                height=220,
                                key=f"source_email_{draft_id}"
                            )
                            st.session_state[shared_body_key] = source_content

                    with col_preview:
                        st.markdown("##### 👁️ Live Render & Compliance Gate")

                        raw_body = st.session_state[shared_body_key].strip()
                        live_triggers = scan_all_negative_keywords(f"{updated_subject} {raw_body}", neg_kw_setting)

                        if live_triggers:
                            trig_badges = " ".join([f"<span class='badge-flagged' style='margin-right:6px; font-size:0.85rem; font-weight:700;'>{t}</span>" for t in live_triggers])
                            st.markdown(f"""
                            <div class="flagged-card">
                                <div class="flagged-banner">⚠️ RESTRICTED TRIGGER KEYWORD(S) DETECTED</div><br>
                                <strong>Trigger(s) Found:</strong> {trig_badges}<br>
                                <span style="font-size:0.85rem; color:#EF4444;">Draft cannot be dispatched while restricted keywords remain in copy. Remove the trigger words in the editor pane to unblock approval.</span>
                            </div>
                            """, unsafe_allow_html=True)
                        elif is_flagged:
                            st.markdown("""
                            <div style="background:rgba(16,185,129,0.12); border:1px solid #10B981; border-radius:10px; padding:12px 16px; margin-bottom:12px;">
                                <span style="color:#059669; font-weight:700; font-size:0.95rem;">✅ Trigger Keyword Resolved</span><br>
                                <span style="color:#065F46; font-size:0.85rem;">All restricted trigger keywords have been removed from this draft. You may now approve and schedule this email for dispatch.</span>
                            </div>
                            """, unsafe_allow_html=True)

                        saved_sig = get_config("signature_html", "").strip()
                        has_block_tags = any(t in raw_body.lower() for t in ["<p", "<div", "<table", "<br"])
                        if not has_block_tags:
                            body_formatted = "".join(f"<p style='margin: 0 0 1em 0;'>{p.strip()}</p>" for p in raw_body.split("\n\n") if p.strip())
                        else:
                            body_formatted = raw_body

                        preview_html = f"{body_formatted}<br><br>{saved_sig}" if saved_sig else body_formatted
                        render_html_preview(preview_html, height=340)
                        word_cnt = len(re.findall(r"\w+", st.session_state[shared_body_key]))
                        st.caption(f"🛡️ Deliverability Score: **{draft_audit['score']}/100** ({draft_audit['grade']}) • Word Count: ~{word_cnt} words")

                        if draft_audit.get("detected_words"):
                            with st.expander("🔍 Deliverability Trigger Words & Suggestions", expanded=False):
                                for dw in draft_audit["detected_words"]:
                                    sug_str = ", ".join(f"`{s}`" for s in dw["suggestions"][:3])
                                    st.markdown(f"- ⚠️ **`{dw['word']}`** ({dw['count']}x) → *Recommended alternatives:* {sug_str}")

                        st.markdown("---")
                        act_col1, act_col2 = st.columns([2, 1])

                        with act_col1:
                            confirm_single_app = st.checkbox("Confirm scheduled dispatch", key=f"conf_app_{draft_id}")
                            approve_btn = st.button(
                                "✅ Approve & Schedule",
                                key=f"approve_{draft_id}",
                                type="primary",
                                disabled=bool(live_triggers) or (not confirm_single_app),
                                use_container_width=True,
                                help="Remove restricted trigger keywords before approval." if live_triggers else "Schedule for automated dispatch."
                            )
                            if approve_btn:
                                if not updated_recipient.strip():
                                    st.error("Please enter a Target Recipient Email before approving.")
                                else:
                                    is_valid_app, app_reason, _ = verify_email_domain_mx(updated_recipient.strip())
                                    if not is_valid_app:
                                        st.error(f"🚫 Cannot approve: Recipient domain failed pre-flight MX check: {app_reason}. Please verify or fix the recipient email address.")
                                    else:
                                        approve_email(
                                            email_id=draft_id,
                                            recipient=updated_recipient.strip(),
                                            scheduled_time=scheduled_datetime_str,
                                            email_html=st.session_state[shared_body_key],
                                            subject=updated_subject.strip()
                                        )
                                        st.success(f"Draft #{draft_id} marked as 'Approved' for dispatch at {scheduled_datetime_str} (Local Time)!")
                                        st.rerun()

                        with act_col2:
                            if st.session_state.get(f"confirm_del_draft_{draft_id}"):
                                if st.button("Confirm", key=f"conf_del_d_{draft_id}", use_container_width=True):
                                    delete_email(draft_id)
                                    st.session_state[f"confirm_del_draft_{draft_id}"] = False
                                    st.warning(f"Draft #{draft_id} deleted.")
                                    st.rerun()
                            else:
                                if st.button("🗑️ Delete", key=f"del_{draft_id}", use_container_width=True):
                                    st.session_state[f"confirm_del_draft_{draft_id}"] = True
                                    st.rerun()

    # ==============================================================================
    # HISTORICAL OUTBOX & DISPATCH AUDIT LOG
    # ==============================================================================
    st.markdown("---")
    col_outbox_hdr, col_dry_run = st.columns([3, 1])
    with col_outbox_hdr:
        st.markdown("### Dispatched & Scheduled Outbox History")
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

    outbox_filter = st.selectbox(
        "Filter Outbox by Status",
        ["All", "Approved", "Sent", "Flagged", "Account Mismatch", "Error", "Pending"],
        index=0,
        key="outbox_history_filter"
    )

    if outbox_filter == "All":
        filtered_outbox = get_emails()
    else:
        filtered_outbox = get_emails(status=outbox_filter)

    if not filtered_outbox:
        st.info("No emails match the selected outbox filter.")
    else:
        for item in filtered_outbox:
            st_class = "badge-pending"
            if item["status"] == "Approved":
                st_class = "badge-approved"
            elif item["status"] == "Sent":
                st_class = "badge-sent"
            elif item["status"] == "Flagged":
                st_class = "badge-flagged"
            elif item["status"] in ["Account Mismatch", "Error"]:
                st_class = "badge-flagged"

            with st.expander(f"#{item['id']} | [{item['status'].upper()}] {item['subject']} -> {item.get('recipient') or 'No Recipient'}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**Status:** <span class='{st_class}'>{item['status']}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Recipient:** `{item.get('recipient')}`")
                    if item.get("sent_via"):
                        st.markdown(f"**Dispatched Via:** `{item['sent_via']}`")
                    st.markdown(f"**Scheduled Send Time:** `{item.get('scheduled_time')}`")
                    st.markdown(f"**Created:** `{item.get('created_at')}`")
                with c2:
                    if item.get("revision_notes"):
                        st.info(f"**Notes / Trigger:** {item['revision_notes']}")
                    if item.get("error_message"):
                        st.error(f"**Error Details:** {item['error_message']}")

                st.markdown("**Email Content Preview:**")
                render_html_preview(item["email_html"], height=240)

                if item["status"] in ["Account Mismatch", "Error", "Approved", "Flagged"]:
                    if st.button(f"↩️ Reset #{item['id']} to Pending", key=f"reset_{item['id']}"):
                        update_email(email_id=item["id"], status="Pending", error_message=None)
                        st.success(f"Email #{item['id']} reset to Pending.")
                        st.rerun()
