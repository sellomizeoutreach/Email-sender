"""
ui/campaign_sequence.py - Campaign Sequence Builder Component.
Phase 3 Implementation:
- Multi-step sequence authoring with per-step wait times (Days, Hours) and conditions.
- Rich text composing powered by ui/rich_editor with native image paste and drag-drop.
- Load from and Save to the existing Templates store.
- Live resolved preview for any sample lead with signature.
- Test email dispatch per step via active Hostinger SMTP.
- Step reordering (Move Up / Down), duplication, and deletion.
- Strict Send Guard validation (unfilled token blocking and empty content prevention).
"""

import os
import re
import html
from typing import List, Dict, Any, Optional, Tuple
import streamlit as st

from database import (
    get_templates,
    create_template,
    get_contacts,
    get_config,
    get_smtp_accounts,
    get_next_available_smtp_account,
    DB_FILE,
)
from template_engine import resolve_template, _missing_tokens, inject_variables
from smtp_dispatcher import send_smtp_email
from ui.components import trigger_toast
from ui.rich_editor import render_rich_editor


def validate_sequence_steps(steps: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    Validates all steps in a sequence for launch readiness:
    - No empty subjects or bodies.
    - No unfilled [Token] placeholders remaining.
    Returns (is_valid, list_of_error_messages).
    """
    errors = []
    if not steps:
        return False, ["Sequence must have at least one step."]

    valid_tokens = {"[Name]", "[First Name]", "[Company]", "[Email]", "[Website]", "[Tags]"}

    for idx, stp in enumerate(steps, 1):
        subj = (stp.get("subject") or "").strip()
        body = (stp.get("body_html") or "").strip()

        if not subj:
            errors.append(f"Step {idx}: Subject line cannot be empty.")
        if not body:
            errors.append(f"Step {idx}: Email body cannot be empty.")

        # Check for unclosed/unfilled brackets
        subj_missing = [t for t in _missing_tokens(subj) if t not in valid_tokens]
        body_missing = [t for t in _missing_tokens(body) if t not in valid_tokens]

        if subj_missing:
            errors.append(f"Step {idx} Subject has unfilled tokens: {', '.join(subj_missing)}")
        if body_missing:
            errors.append(f"Step {idx} Body has unfilled tokens: {', '.join(body_missing)}")

    return len(errors) == 0, errors


def render_sequence_builder(
    steps: List[Dict[str, Any]],
    all_contacts: List[Dict[str, Any]],
    all_templates: List[Dict[str, Any]],
    key_prefix: str = "seq_builder",
    read_only: bool = False
) -> List[Dict[str, Any]]:
    """
    Renders the interactive sequence builder interface.
    Returns the updated list of steps.
    """
    if not steps:
        steps = [{
            "position": 1,
            "subject": "Quick question regarding [Company]",
            "body_html": "<p>Hi [First Name],</p><p>We noticed [Company] on Amazon and wanted to connect.</p><p>Best regards,<br>Jack Connor</p>",
            "wait_days": 0,
            "wait_hours": 0,
            "condition": "no_reply"
        }]

    # Ensure step positions are 1-indexed and consecutive
    for idx, stp in enumerate(steps, 1):
        stp["position"] = idx

    st.markdown("##### ✉️ Campaign Sequence Steps")
    st.caption("Step 1 is dispatched at Day 0. Subsequent follow-ups wait for the configured delay and condition.")

    # Sample lead selector for live preview
    sample_lead = all_contacts[0] if all_contacts else {
        "name": "Zoey Pu",
        "company": "Kaleidos Makeup",
        "email": "zoey.pu@kaleidosmakeup.com"
    }

    # Render each step
    for idx, stp in enumerate(steps):
        pos = idx + 1
        is_first = (pos == 1)
        is_last = (pos == len(steps))

        with st.container(border=True):
            # Step header row
            sh_c1, sh_c2, sh_c3 = st.columns([5.5, 3.5, 3.0], vertical_alignment="center")

            with sh_c1:
                step_title = f"Step {pos} · Initial Outreach (Day 0)" if is_first else f"Step {pos} · Follow-Up #{pos-1}"
                st.markdown(f"<div style='font-size:16px; font-weight:800; color:#083731;'>{step_title}</div>", unsafe_allow_html=True)

            with sh_c2:
                # Load Template and Save Template popovers
                if not read_only:
                    t_c1, t_c2 = st.columns(2)
                    with t_c1:
                        with st.popover("📥 Load Template", key=f"{key_prefix}_load_tpl_{pos}", use_container_width=True):
                            st.markdown("**Load Existing Template**")
                            tpl_options = {t["id"]: t["name"] or t.get("template_name") or f"Template #{t['id']}" for t in all_templates}
                            if not tpl_options:
                                st.caption("No saved templates found.")
                            else:
                                sel_tid = st.selectbox("Choose Template", options=list(tpl_options.keys()), format_func=lambda x: tpl_options[x], key=f"{key_prefix}_sel_tpl_{pos}")
                                if st.button("Apply Template", type="primary", key=f"{key_prefix}_apply_tpl_{pos}", use_container_width=True):
                                    picked = next((t for t in all_templates if t["id"] == sel_tid), None)
                                    if picked:
                                        stp["subject"] = picked.get("subject") or stp["subject"]
                                        stp["body_html"] = picked.get("body_html") or picked.get("body_content") or stp["body_html"]
                                        stp["template_id"] = picked["id"]
                                        trigger_toast(f"Loaded '{picked.get('name') or picked.get('template_name')}' into Step {pos}!", icon="📥")
                                        st.rerun()
                                if st.button("🗑️ Delete from Library", key=f"{key_prefix}_del_lib_tpl_{pos}", use_container_width=True):
                                    from database import delete_template
                                    delete_template(sel_tid)
                                    trigger_toast("Template removed from library.", icon="🗑️")
                                    st.rerun()
                    with t_c2:
                        with st.popover("💾 Save Template", key=f"{key_prefix}_save_tpl_{pos}", use_container_width=True):
                            st.markdown("**Save as Template**")
                            new_t_name = st.text_input("Template Name", value=f"Sequence Step {pos}", key=f"{key_prefix}_new_tname_{pos}")
                            if st.button("Save to Library", type="primary", key=f"{key_prefix}_do_save_tpl_{pos}", use_container_width=True):
                                if new_t_name.strip():
                                    new_tid = create_template(name=new_t_name.strip(), subject=stp["subject"], body_html=stp["body_html"])
                                    trigger_toast(f"Saved Template #{new_tid} to library!", icon="💾")
                                    st.rerun()

            with sh_c3:
                # Reorder and Delete controls
                if not read_only:
                    r_c1, r_c2, r_c3, r_c4 = st.columns(4, vertical_alignment="center")
                    with r_c1:
                        if not is_first and st.button("⬆️", key=f"{key_prefix}_up_{pos}", help="Move Step Up"):
                            steps[idx], steps[idx-1] = steps[idx-1], steps[idx]
                            st.rerun()
                    with r_c2:
                        if not is_last and st.button("⬇️", key=f"{key_prefix}_down_{pos}", help="Move Step Down"):
                            steps[idx], steps[idx+1] = steps[idx+1], steps[idx]
                            st.rerun()
                    with r_c3:
                        if st.button("📑", key=f"{key_prefix}_dup_{pos}", help="Duplicate Step"):
                            dup = dict(stp)
                            dup["position"] = pos + 1
                            steps.insert(idx + 1, dup)
                            st.rerun()
                    with r_c4:
                        if len(steps) > 1 and st.button("🗑️", key=f"{key_prefix}_del_{pos}", help="Delete Step"):
                            steps.pop(idx)
                            st.rerun()

            # Wait time & condition settings (steps 2+)
            if not is_first:
                w_row1, w_row2, w_row3 = st.columns([2.5, 2.5, 5], vertical_alignment="center")
                with w_row1:
                    stp["wait_days"] = st.number_input(
                        f"Wait Days after Step {pos-1}",
                        min_value=0, max_value=60,
                        value=int(stp.get("wait_days", 3)),
                        key=f"{key_prefix}_wait_d_{pos}",
                        disabled=read_only
                    )
                with w_row2:
                    stp["wait_hours"] = st.number_input(
                        "Wait Hours",
                        min_value=0, max_value=23,
                        value=int(stp.get("wait_hours", 0)),
                        key=f"{key_prefix}_wait_h_{pos}",
                        disabled=read_only
                    )
                with w_row3:
                    cond_opts = ["Send only if no reply (Default)", "Send unconditionally"]
                    curr_cond = 0 if stp.get("condition", "no_reply") == "no_reply" else 1
                    sel_cond = st.selectbox(
                        "Sending Condition",
                        options=cond_opts,
                        index=curr_cond,
                        key=f"{key_prefix}_cond_{pos}",
                        disabled=read_only
                    )
                    stp["condition"] = "no_reply" if sel_cond.startswith("Send only") else "always"

            # Subject Line & Same Thread Toggle
            sub_col1, sub_col2 = st.columns([7, 3], vertical_alignment="center")
            with sub_col1:
                stp["subject"] = st.text_input(
                    f"Subject Line (Step {pos})",
                    value=stp.get("subject", ""),
                    key=f"{key_prefix}_subj_{pos}",
                    disabled=read_only,
                    placeholder="e.g. Quick question regarding [Company]"
                )
            with sub_col2:
                if not is_first:
                    is_re_thread = bool("Re:" in stp.get("subject", ""))
                    same_thread = st.checkbox("🧵 Same Thread (Re:)", value=is_re_thread, key=f"{key_prefix}_thread_{pos}", disabled=read_only)
                    if same_thread and not stp["subject"].startswith("Re:"):
                        prev_subj = steps[idx-1].get("subject", "")
                        clean_prev = re.sub(r'^(Re:\s*)+', '', prev_subj, flags=re.IGNORECASE)
                        stp["subject"] = f"Re: {clean_prev}"
                        st.rerun()

            # Rich Email Editor for Step Body
            st.markdown("<div style='font-size:12px; font-weight:700; color:#083731; margin-top:4px;'>Email Body:</div>", unsafe_allow_html=True)
            if not read_only:
                updated_body = render_rich_editor(
                    initial_html=stp.get("body_html", ""),
                    key=f"{key_prefix}_editor_{pos}",
                    height=220,
                    owner_type="campaign_step",
                    owner_id=stp.get("id")
                )
                stp["body_html"] = updated_body
            else:
                st.markdown(stp.get("body_html", ""), unsafe_allow_html=True)

            # Live Preview & Test Email Bar
            st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
            prev_c1, prev_c2 = st.columns([7, 3], vertical_alignment="center")

            with prev_c1:
                st.markdown(
                    f"<div style='font-size:13px; color:#475569;'>👁️ <b>Live Preview</b> — <i>{html.escape(sample_lead.get('name', 'Sample Lead'))}</i></div>",
                    unsafe_allow_html=True
                )

            with prev_c2:
                with st.popover("📤 Send Test Email", key=f"{key_prefix}_test_pop_{pos}", use_container_width=True):
                    st.markdown("**Send Test Email to Me**")
                    test_to = st.text_input("Recipient", value="sales@sellomize.com", key=f"{key_prefix}_test_to_{pos}")
                    if st.button("Send Test", type="primary", key=f"{key_prefix}_test_btn_{pos}", use_container_width=True):
                        # Fetch active sender
                        active_acc = get_next_available_smtp_account()
                        if not active_acc:
                            st.error("No active SMTP account available to send test email.")
                        else:
                            sig_html = get_config("signature_html", "") or "Best regards,<br>Jack Connor<br>Sellomize"
                            res_subj = resolve_template(stp.get("subject", ""), sample_lead)
                            res_body = resolve_template(stp.get("body_html", ""), sample_lead)
                            full_payload = f"{res_body}<br><br>{sig_html}"
                            ok, msg = send_smtp_email(
                                smtp_account=active_acc,
                                recipient=test_to.strip(),
                                subject=f"[TEST Step {pos}] {res_subj}",
                                html_content=full_payload
                            )
                            if ok:
                                trigger_toast(f"Test email for Step {pos} dispatched to {test_to}!", icon="🚀")
                            else:
                                st.error(f"Failed to send test: {msg}")

            # Live Preview expander — placed BELOW columns to avoid height jitter (Pattern 6 fix)
            with st.expander(f"👁️ Live Preview — {sample_lead.get('name', 'Sample Lead')}", expanded=False):
                sig_html = get_config("signature_html", "") or "Best regards,<br>Jack Connor<br>Sellomize"
                resolved_subj = resolve_template(stp.get("subject", ""), sample_lead)
                resolved_body = resolve_template(stp.get("body_html", ""), sample_lead)
                full_preview_html = f"{resolved_body}<br><br>{sig_html}"

                # Missing tokens check
                unfilled_subj = _missing_tokens(resolved_subj)
                unfilled_body = _missing_tokens(resolved_body)
                if unfilled_subj or unfilled_body:
                    st.error(f"⚠️ Unfilled Tokens Detected: {', '.join(unfilled_subj + unfilled_body)}")
                else:
                    st.caption("🛡️ Send Guard Clear — No unfilled variables detected.")

                st.markdown(f"**Subject:** {html.escape(resolved_subj)}")
                st.markdown("<hr style='margin:6px 0; border:0; border-top:1px solid #CBD5E1;'>", unsafe_allow_html=True)
                st.markdown(full_preview_html, unsafe_allow_html=True)



    # Add Step Button
    if not read_only:
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        if st.button("➕ Add Sequence Step", key=f"{key_prefix}_btn_add_step"):
            new_pos = len(steps) + 1
            prev_subj = steps[-1].get("subject", "Outreach") if steps else "Outreach"
            clean_prev = re.sub(r'^(Re:\s*)+', '', prev_subj, flags=re.IGNORECASE)
            steps.append({
                "position": new_pos,
                "subject": f"Re: {clean_prev}",
                "body_html": "<p>Hi [First Name],</p><p>Wanted to follow up on my previous note. Did you have a chance to look at this?</p><p>Best regards,<br>Jack Connor</p>",
                "wait_days": 3,
                "wait_hours": 0,
                "condition": "no_reply"
            })
            st.rerun()

    return steps
