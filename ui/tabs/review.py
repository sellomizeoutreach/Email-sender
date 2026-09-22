"""
Review Queue & Triage Tab for Sellomize Reach.
Master-Detail split pane triage (col1, col2 = st.columns([1, 2])), live sandboxed preview,
visual & HTML source editor, pre-flight MX checks, and scheduled outbox history.
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
    update_email
)
from mx_checker import verify_email_domain_mx, get_cached_domain_mx
from template_engine import audit_email_deliverability, scan_all_negative_keywords
from timezone_helper import get_zoneinfo
from ui.components import render_html_preview


def render_review_tab():
    """Render Review Queue & Triage Desk with status bar, split pane, and inline actions."""
    st.markdown("### 🛡️ Pre-Flight Review Queue & Triage Desk")
    st.caption("Inspect pending drafts, audit deliverability, and triage flagged emails before scheduled dispatch.")

    all_emails = get_emails()
    needs_review_count = len([e for e in all_emails if e["status"] == "Pending"])
    flagged_count = len([e for e in all_emails if e["status"] in ["Flagged", "Account Mismatch", "Error"]])
    approved_count = len([e for e in all_emails if e["status"] == "Approved"])
    sent_count = len([e for e in all_emails if e["status"] == "Sent"])

    pills_options = [
        f"Needs Review ({needs_review_count})",
        f"Flagged / Spam Shield ({flagged_count})",
        f"Approved & Scheduled ({approved_count})",
        f"Dispatched History ({sent_count})"
    ]

    pills_fn = getattr(st, "pills", None)
    if pills_fn:
        selected_pill = pills_fn(
            "Triage Status Filter",
            options=pills_options,
            default=pills_options[0] if needs_review_count > 0 else (pills_options[1] if flagged_count > 0 else pills_options[0]),
            key="review_status_pill",
            label_visibility="collapsed"
        )
    else:
        selected_pill = st.radio(
            "Triage Status Filter",
            options=pills_options,
            index=0 if needs_review_count > 0 else (1 if flagged_count > 0 else 0),
            horizontal=True,
            key="review_status_pill",
            label_visibility="collapsed"
        )

    if not selected_pill:
        selected_pill = pills_options[0]

    if selected_pill.startswith("Needs Review"):
        drafts_to_show = [e for e in all_emails if e["status"] == "Pending"]
        active_pane_mode = "split_pane"
    elif selected_pill.startswith("Flagged"):
        drafts_to_show = [e for e in all_emails if e["status"] in ["Flagged", "Account Mismatch", "Error"]]
        active_pane_mode = "split_pane"
    elif selected_pill.startswith("Approved"):
        drafts_to_show = [e for e in all_emails if e["status"] == "Approved"]
        active_pane_mode = "split_pane"
    else:
        drafts_to_show = [e for e in all_emails if e["status"] == "Sent"]
        active_pane_mode = "history"

    if active_pane_mode == "split_pane":
        # Initialize selection state for each draft (Pending defaults to True, others to False)
        for d in drafts_to_show:
            sel_key = f"sel_draft_{d['id']}"
            if sel_key not in st.session_state:
                st.session_state[sel_key] = (d["status"] == "Pending")

        selected_draft_ids = [d["id"] for d in drafts_to_show if st.session_state.get(f"sel_draft_{d['id']}", False)]
        selected_clean_drafts = [d for d in drafts_to_show if d["id"] in selected_draft_ids and d["status"] == "Pending"]

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
            st.markdown(f"""
            <div style="background:rgba(8,55,49,0.05); border:1px solid rgba(8,55,49,0.14); border-radius:8px; padding:14px 18px; margin:12px 0 16px; display:flex; align-items:center; justify-content:space-between;">
                <span style="font-size:0.9rem; color:#083731; font-weight:600;">✅ No drafts found in '{selected_pill.split('(')[0].strip()}'.</span>
                <span style="font-size:0.82rem; color:#64748B;">Generate new outreach batches from the Campaign Creator tab.</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            neg_kw_setting = get_config("negative_keywords", "")
            draft_id_map = {d["id"]: d for d in drafts_to_show}
            draft_ids = list(draft_id_map.keys())

            # Validate / set active draft
            if "active_review_draft_id" not in st.session_state or st.session_state["active_review_draft_id"] not in draft_id_map:
                flagged_ids = [d["id"] for d in drafts_to_show if d["status"] in ["Flagged", "Account Mismatch", "Error"]]
                st.session_state["active_review_draft_id"] = flagged_ids[0] if flagged_ids else draft_ids[0]

            # ==============================================================================
            # MASTER-DETAIL SPLIT PANE (col1: Master List [1], col2: Detail Inspector [2])
            # ==============================================================================
            col_master, col_detail = st.columns([1, 2], gap="large")

            # --------------------------------------------------------------------------
            # LEFT COLUMN (col1 - Master): Compact Selection List & Batch Actions
            # --------------------------------------------------------------------------
            with col_master:
                st.markdown(f"##### 📋 Drafts ({len(drafts_to_show)})")

                if selected_pill.startswith("Needs Review"):
                    st.caption(f"**{len(selected_clean_drafts)} of {len(drafts_to_show)}** selected for batch dispatch")

                    c_sel1, c_sel2 = st.columns(2)
                    with c_sel1:
                        if st.button("Select Clean", key="btn_sel_all_clean", use_container_width=True, help="Select all non-flagged drafts"):
                            for d in drafts_to_show:
                                if d["status"] == "Pending":
                                    st.session_state[f"sel_draft_{d['id']}"] = True
                            st.rerun()
                    with c_sel2:
                        if st.button("Deselect All", key="btn_desel_all", use_container_width=True):
                            for d in drafts_to_show:
                                st.session_state[f"sel_draft_{d['id']}"] = False
                            st.rerun()

                    # Batch Execution Bar
                    if selected_clean_drafts:
                        chk_app = st.checkbox(
                            f"Confirm batch send ({len(selected_clean_drafts)})",
                            key="chk_confirm_app_sel"
                        )
                        if st.button(
                            f"⚡ Approve Selected ({len(selected_clean_drafts)})",
                            type="primary",
                            use_container_width=True,
                            disabled=not chk_app,
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
                            st.success(f"Approved {approved_count} draft(s) for scheduled dispatch!")
                            st.rerun()

                if selected_draft_ids:
                    if st.button(
                        f"🗑️ Delete Selected ({len(selected_draft_ids)})",
                        use_container_width=True,
                        key="btn_bulk_del"
                    ):
                        st.session_state["confirm_bulk_del"] = True

                st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
                st.markdown("###### Select Draft:")

                def format_draft_entry(did):
                    dr = draft_id_map[did]
                    status_icon = "🚨" if dr["status"] in ["Flagged", "Account Mismatch", "Error"] else ("🟢" if dr["status"] == "Approved" else "📄")
                    step_tag = f" [T{dr.get('sequence_step')}]" if dr.get("sequence_step", 1) > 1 else ""
                    recip = dr.get("recipient") or "No Recipient"
                    subj = dr.get("subject") or "No Subject"
                    if len(subj) > 18:
                        subj = subj[:18] + "…"
                    return f"{status_icon} #{did}{step_tag} {recip} — {subj}"

                active_id = st.radio(
                    "Active Draft",
                    options=draft_ids,
                    format_func=format_draft_entry,
                    key="active_review_draft_id",
                    label_visibility="collapsed"
                )

                st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                st.checkbox(
                    f"Include draft #{active_id} in batch operations",
                    key=f"sel_draft_{active_id}"
                )

            # --------------------------------------------------------------------------
            # RIGHT COLUMN (col2 - Detail): Prominent Banner, Inline Editor, Side-by-Side Actions
            # --------------------------------------------------------------------------
            with col_detail:
                draft = draft_id_map[active_id]
                draft_id = draft["id"]
                is_flagged = (draft["status"] in ["Flagged", "Account Mismatch", "Error"])
                subject_key = f"subject_{draft_id}"
                recipient_key = f"recipient_{draft_id}"
                sched_date_key = f"sched_date_{draft_id}"
                sched_time_key = f"sched_time_{draft_id}"
                shared_body_key = f"draft_body_{draft_id}"

                if shared_body_key not in st.session_state:
                    st.session_state[shared_body_key] = draft["email_html"]

                current_subject = st.session_state.get(subject_key, draft.get("subject", ""))
                current_body = st.session_state[shared_body_key]
                current_live_triggers = scan_all_negative_keywords(f"{current_subject} {current_body}", neg_kw_setting)
                draft_audit = audit_email_deliverability(current_body, subject=current_subject, custom_negative_keywords=neg_kw_setting)

                seq_step = draft.get("sequence_step") or 1
                has_seq_id = bool(draft.get("sequence_id"))
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
                    if draft["status"] in ["Account Mismatch", "Error"]:
                        st.markdown(f"""
                        <div style="background:#FEF2F2; border:1.5px solid #EF4444; border-radius:8px; padding:12px 16px; margin-bottom:12px;">
                            <div style="font-weight:700; color:#B91C1C; font-size:0.92rem;">🚨 Exception: {draft['status']}</div>
                            <div style="font-size:0.8rem; color:#991B1B; margin-top:2px;">{draft.get('error_message') or draft.get('revision_notes') or 'Unknown error occurred'}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                        <div style="background:#F0FDF4; border:1px solid #10B981; border-radius:8px; padding:10px 14px; margin-bottom:12px;">
                            <span style="color:#059669; font-weight:700; font-size:0.9rem;">✅ Restricted Keywords Cleared:</span>
                            <span style="color:#065F46; font-size:0.82rem; margin-left:4px;">No restricted trigger keywords found in current text. Click [🚀 Approve & Schedule] or [✏️ Save Edits] to update.</span>
                        </div>
                        """, unsafe_allow_html=True)

                # Header info card
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

                # Inline Editable Fields
                col_sub_rec1, col_sub_rec2 = st.columns([1.8, 1.2])
                with col_sub_rec1:
                    updated_subject = st.text_input("Subject Line", value=draft.get("subject", ""), key=subject_key)
                with col_sub_rec2:
                    updated_recipient = st.text_input("Recipient Email", value=draft.get("recipient") or "", key=recipient_key)

                # Body text area directly editable without extra toggles
                updated_body = st.text_area(
                    "Email Body Content",
                    value=st.session_state[shared_body_key],
                    height=200,
                    key=f"text_email_{draft_id}",
                    help="Edit email body directly. HTML tags and text formatting are supported."
                )
                st.session_state[shared_body_key] = updated_body

                # Schedule local date & time
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
                    sched_date = st.date_input("Scheduled Date (Local)", value=default_date, key=sched_date_key)
                with c_time:
                    sched_time = st.time_input("Scheduled Time (Local)", value=default_time, key=sched_time_key)

                scheduled_datetime_str = f"{sched_date.strftime('%Y-%m-%d')} {sched_time.strftime('%H:%M:%S')}"

                # Collapsible Rendered Live Preview
                with st.expander("👁️ Live Rendered Preview", expanded=False):
                    raw_body = updated_body.strip()
                    saved_sig = get_config("signature_html", "").strip()
                    has_block_tags = any(t in raw_body.lower() for t in ["<p", "<div", "<table", "<br"])
                    if not has_block_tags:
                        body_formatted = "".join(f"<p style='margin: 0 0 1em 0;'>{p.strip()}</p>" for p in raw_body.split("\n\n") if p.strip())
                    else:
                        body_formatted = raw_body

                    preview_html = f"{body_formatted}<br><br>{saved_sig}" if saved_sig else body_formatted
                    render_html_preview(preview_html, height=220)

                # Clear Action Group side-by-side at bottom
                st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
                col_act1, col_act2, col_act3 = st.columns([1.6, 1.3, 1.3])

                with col_act1:
                    approve_btn = st.button(
                        "🚀 Approve & Schedule",
                        key=f"approve_{draft_id}",
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
                                    email_html=updated_body,
                                    subject=updated_subject.strip()
                                )
                                st.success(f"Draft #{draft_id} marked as 'Approved' for dispatch at {scheduled_datetime_str}!")
                                st.rerun()

                with col_act2:
                    save_btn = st.button(
                        "✏️ Save Edits",
                        key=f"save_edits_{draft_id}",
                        use_container_width=True,
                        help="Save draft edits without approving yet."
                    )
                    if save_btn:
                        new_status = "Pending" if (draft["status"] == "Flagged" and not current_live_triggers) else draft["status"]
                        update_email(
                            email_id=draft_id,
                            status=new_status,
                            subject=updated_subject.strip(),
                            recipient=updated_recipient.strip(),
                            email_html=updated_body,
                            scheduled_time=scheduled_datetime_str
                        )
                        st.success(f"Edits saved for Draft #{draft_id}.")
                        st.rerun()

                with col_act3:
                    if st.session_state.get(f"confirm_del_draft_{draft_id}"):
                        col_dc1, col_dc2 = st.columns(2)
                        with col_dc1:
                            if st.button("Confirm", key=f"conf_del_d_{draft_id}", use_container_width=True):
                                delete_email(draft_id)
                                st.session_state[f"confirm_del_draft_{draft_id}"] = False
                                st.warning(f"Draft #{draft_id} discarded.")
                                st.rerun()
                        with col_dc2:
                            if st.button("Cancel", key=f"canc_del_d_{draft_id}", use_container_width=True):
                                st.session_state[f"confirm_del_draft_{draft_id}"] = False
                                st.rerun()
                    else:
                        if st.button("🗑️ Discard Draft", key=f"del_{draft_id}", use_container_width=True):
                            st.session_state[f"confirm_del_draft_{draft_id}"] = True
                            st.rerun()

    # ==============================================================================
    # HISTORICAL OUTBOX & DISPATCH AUDIT LOG (Collapsible Section)
    # ==============================================================================
    st.markdown("---")
    is_hist_expanded = (active_pane_mode == "history")
    with st.expander("📬 Dispatched & Scheduled Outbox History", expanded=is_hist_expanded):
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

        col_outbox_filter, col_outbox_tools = st.columns([1.4, 2.6])
        with col_outbox_filter:
            def_ob_idx = 1 if is_hist_expanded else 0
            outbox_filter = st.selectbox(
                "Filter Outbox by Status",
                ["All", "Sent", "Approved", "Flagged", "Account Mismatch", "Error", "Pending"],
                index=def_ob_idx,
                key="outbox_history_filter"
            )

        if outbox_filter == "All":
            filtered_outbox = get_emails()
        else:
            filtered_outbox = get_emails(status=outbox_filter)

        selected_outbox_ids = [item["id"] for item in filtered_outbox if st.session_state.get(f"sel_outbox_{item['id']}", False)]
        s_cnt = len(selected_outbox_ids)

        with col_outbox_tools:
            st.write("")
            c_sel_tools, c_del_sel, c_clear_hist = st.columns([1.5, 1.3, 1.4])
            with c_sel_tools:
                c_s1, c_s2 = st.columns(2)
                with c_s1:
                    if st.button("Select All", key="btn_outbox_sel_all", use_container_width=True, disabled=not filtered_outbox):
                        for item in filtered_outbox:
                            st.session_state[f"sel_outbox_{item['id']}"] = True
                        st.rerun()
                with c_s2:
                    if st.button("Deselect", key="btn_outbox_desel_all", use_container_width=True, disabled=not filtered_outbox):
                        for item in filtered_outbox:
                            st.session_state[f"sel_outbox_{item['id']}"] = False
                        st.rerun()

            with c_del_sel:
                if st.session_state.get("confirm_outbox_bulk_del"):
                    if st.button(f"Confirm ({s_cnt})", type="primary", use_container_width=True, key="btn_conf_outbox_del"):
                        deleted = bulk_delete_emails(selected_outbox_ids)
                        for sid in selected_outbox_ids:
                            st.session_state.pop(f"sel_outbox_{sid}", None)
                        st.session_state["confirm_outbox_bulk_del"] = False
                        st.success(f"Deleted {deleted} email(s) from outbox.")
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
                        st.success(f"Purged {deleted} '{target_status}' email(s) from outbox.")
                        st.rerun()
                else:
                    target_hint = "Sent emails" if outbox_filter in ["All", "Sent"] else f"all '{outbox_filter}' emails"
                    if st.button(clear_btn_label, use_container_width=True, key="btn_outbox_clear_all", help=f"Purge historical {target_hint} so they don't pile up in SQLite."):
                        st.session_state["confirm_outbox_clear_all"] = True
                        st.rerun()

        if st.session_state.get("confirm_outbox_bulk_del") or st.session_state.get("confirm_outbox_clear_all"):
            c_warn, c_canc = st.columns([3, 1])
            with c_warn:
                st.warning("⚠️ Permanent Action: Selected emails will be permanently removed from the database.")
            with c_canc:
                if st.button("Cancel Action", key="btn_canc_outbox_acts", use_container_width=True):
                    st.session_state["confirm_outbox_bulk_del"] = False
                    st.session_state["confirm_outbox_clear_all"] = False
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
                elif item["status"] == "Flagged":
                    st_class = "badge-flagged"
                elif item["status"] in ["Account Mismatch", "Error"]:
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
                        render_html_preview(item["email_html"], height=240)

                        col_act_left, col_act_right = st.columns([1.2, 1])
                        with col_act_left:
                            if item["status"] in ["Account Mismatch", "Error", "Approved", "Flagged"]:
                                if st.button(f"↩️ Reset #{item['id']} to Pending", key=f"reset_{item['id']}"):
                                    update_email(email_id=item["id"], status="Pending", error_message=None)
                                    st.success(f"Email #{item['id']} reset to Pending.")
                                    st.rerun()
                        with col_act_right:
                            if st.session_state.get(f"confirm_del_outbox_{item['id']}"):
                                if st.button(f"Confirm Delete #{item['id']}", type="primary", key=f"conf_del_ob_{item['id']}", use_container_width=True):
                                    delete_email(item["id"])
                                    st.session_state.pop(f"sel_outbox_{item['id']}", None)
                                    st.session_state[f"confirm_del_outbox_{item['id']}"] = False
                                    st.warning(f"Deleted email #{item['id']}.")
                                    st.rerun()
                            else:
                                if st.button(f"🗑️ Delete Email", key=f"del_ob_{item['id']}", use_container_width=True, help="Permanently delete this email record"):
                                    st.session_state[f"confirm_del_outbox_{item['id']}"] = True
                                    st.rerun()
