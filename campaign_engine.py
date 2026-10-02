"""
campaign_engine.py - Autonomous Multi-Step Campaign Sending Engine & Stop Rules.
Phase 4 of Sellomize Reach Campaigns Module.

Key Capabilities:
1. Multi-Step Execution Loop:
   - Evaluates active campaigns and queries enrolled contacts due for outreach.
   - Enforces Step 1 Day 0 dispatch and subsequent wait delays (Days & Hours).
2. Schedule & Window Compliance:
   - Enforces campaign-specific timezones (US Eastern, US Pacific, UK, etc.).
   - Validates active days (e.g. Mon–Fri) and active hours (e.g. 09:00–18:00).
   - Enforces campaign daily sending limits and delay between emails.
3. Stop Rules:
   - Suppresses contacts marked 'Do Not Contact', 'Opted-Out', or 'Suppressed'.
   - Halts further sequence steps immediately if prospect has replied or converted.
   - Halts and quarantines on hard NDR bounces.
   - Enforces per-step conditions ('no_reply' vs 'always').
4. Idempotency & Concurrency:
   - Atomically transitions states (pending -> sending -> scheduled/completed) to prevent double sends.
5. Real-Time Tracking & Event Logging:
   - Logs 'sent', 'opened', 'replied', 'bounced', 'unsubscribed' to campaign_events.
   - Integrates with tracker pixel and Hostinger SMTP rotation.
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta, time as dtime, timezone
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from database import (
    get_connection,
    get_all_campaigns,
    get_campaign,
    update_campaign,
    get_campaign_steps,
    record_campaign_event,
    get_config,
    get_next_available_smtp_account,
    increment_smtp_sent,
    mark_email_sent,
    mark_email_error,
    advance_contact_followup,
    record_email_bounce,
    create_email,
    DB_FILE,
)
from template_engine import resolve_template, _missing_tokens
from smtp_dispatcher import send_smtp_email, sanitize_header
from tracker import inject_tracking_and_links
from mx_checker import verify_email_domain_mx
from timezone_helper import get_engine_now, get_engine_now_str

logger = logging.getLogger("campaign_engine")


def is_campaign_in_window(campaign: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validates whether the current moment falls within the campaign's active sending window:
    - Active days (e.g. Mon, Tue, Wed, Thu, Fri)
    - Active hours (send_window_start to send_window_end)
    - Timezone compliance
    """
    raw_tz = campaign.get("timezone") or "America/New_York"
    # Extract clean tz identifier if label has notes, e.g. "America/New_York (US East)"
    tz_id = raw_tz.split(" ")[0].strip()
    try:
        camp_tz = ZoneInfo(tz_id)
    except Exception:
        camp_tz = ZoneInfo("America/New_York")

    now_camp = datetime.now(timezone.utc).astimezone(camp_tz)
    weekday_short = now_camp.strftime("%a")  # 'Mon', 'Tue', etc.
    weekday_full = now_camp.strftime("%A")   # 'Monday', 'Tuesday', etc.

    raw_days = campaign.get("send_days") or "Mon,Tue,Wed,Thu,Fri"
    allowed_days = [d.strip() for d in raw_days.split(",") if d.strip()]

    # Match either Mon or Monday
    is_active_day = any(d.lower() in [weekday_short.lower(), weekday_full.lower()] for d in allowed_days)
    if not is_active_day:
        return False, f"Today ({weekday_short}) is not an active sending day ({raw_days}) in {tz_id}."

    # Active hours check
    win_start = campaign.get("send_window_start") or "09:00"
    win_end = campaign.get("send_window_end") or "18:00"

    try:
        sh, sm = map(int, win_start.split(":"))
        eh, em = map(int, win_end.split(":"))
        start_time = dtime(sh, sm)
        end_time = dtime(eh, em)
    except Exception:
        start_time = dtime(9, 0)
        end_time = dtime(18, 0)

    cur_time = now_camp.time()
    if not (start_time <= cur_time <= end_time):
        return False, f"Current time {cur_time.strftime('%H:%M')} is outside window {win_start}-{win_end} in {tz_id}."

    return True, "Within sending window"


def get_campaign_today_sent_count(campaign_id: int, db_path: str = DB_FILE) -> int:
    """Returns number of emails dispatched for this campaign today."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    today_prefix = datetime.now().astimezone().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM campaign_events
        WHERE campaign_id = ? AND event_type = 'sent' AND created_at LIKE ?
    """, (campaign_id, f"{today_prefix}%"))
    row = cursor.fetchone()
    conn.close()
    return int(row["cnt"]) if row else 0


def get_due_campaign_contacts(campaign_id: int, limit: int = 10, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """
    Retrieve enrolled contacts ready for their next step in this campaign:
    - Step 1 (current_step == 0): pending or scheduled with next_send_at <= now (or empty)
    - Follow-ups (current_step >= 1): scheduled with next_send_at <= now
    """
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()

    query = """
        SELECT cc.*, c.name, c.email, c.company, c.status as lead_status, c.tags as lead_tags, c.notes as lead_notes, c.custom_variables
        FROM campaign_contacts cc
        JOIN contacts c ON cc.contact_id = c.id
        WHERE cc.campaign_id = ?
          AND cc.state IN ('pending', 'scheduled')
          AND (cc.next_send_at = '' OR cc.next_send_at IS NULL OR cc.next_send_at <= ?)
        ORDER BY cc.id ASC
        LIMIT ?
    """
    cursor.execute(query, (campaign_id, now_str, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def process_campaign_contact_step(
    campaign: Dict[str, Any],
    contact_rec: Dict[str, Any],
    steps: List[Dict[str, Any]],
    dry_run: bool = False,
    db_path: str = DB_FILE
) -> Tuple[bool, str]:
    """
    Processes and dispatches the due sequence step for a single enrolled contact.
    Enforces stop rules, template resolution, idempotency locks, and event logging.
    Returns (success, message).
    """
    camp_id = campaign["id"]
    cc_id = contact_rec["id"]
    lead_id = contact_rec["contact_id"]
    lead_email = (contact_rec.get("email") or "").strip()
    lead_name = contact_rec.get("name") or "there"
    curr_step = int(contact_rec.get("current_step", 0))
    target_pos = curr_step + 1

    now_dt = datetime.now().astimezone()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 1. STOP RULES CHECK
    lead_status = (contact_rec.get("lead_status") or "").lower()
    lead_tags = (contact_rec.get("lead_tags") or "").lower()

    # Rule A: Unsubscribe / Suppression
    if lead_status in ["do not contact", "opted-out", "suppressed"] or "do not contact" in lead_tags:
        _update_contact_state(cc_id, "unsubscribed", now_str, db_path)
        record_campaign_event(camp_id, lead_id, None, "unsubscribed", {"reason": "Suppressed status in CRM"}, db_path=db_path)
        logger.info(f"[Campaign #{camp_id}] Suppressed contact {lead_email} marked unsubscribed.")
        return False, "Contact suppressed"

    # Rule B: Hard Bounce
    if lead_status == "bounced" or "bounced" in lead_tags:
        _update_contact_state(cc_id, "bounced", now_str, db_path)
        record_campaign_event(camp_id, lead_id, None, "bounced", {"reason": "Previous bounce detected"}, db_path=db_path)
        logger.info(f"[Campaign #{camp_id}] Bounced contact {lead_email} stopped.")
        return False, "Contact bounced"

    # Rule C: Prospect Replied or Converted
    if lead_status in ["replied", "interested", "meeting booked", "closed won"] or "replied" in lead_tags or contact_rec.get("converted") == 1:
        _update_contact_state(cc_id, "replied", now_str, db_path)
        record_campaign_event(camp_id, lead_id, None, "replied", {"reason": "Prospect replied or converted"}, db_path=db_path)
        logger.info(f"[Campaign #{camp_id}] Prospect {lead_email} already replied/converted. Sequence halted.")
        return False, "Contact replied"

    # 2. LOCATE TARGET STEP
    target_step = next((s for s in steps if int(s.get("position", 0)) == target_pos), None)
    if not target_step:
        # All steps completed!
        _update_contact_state(cc_id, "completed", now_str, db_path)
        record_campaign_event(camp_id, lead_id, None, "completed", {"total_steps": len(steps)}, db_path=db_path)
        logger.info(f"[Campaign #{camp_id}] Contact {lead_email} reached end of sequence ({len(steps)} steps completed).")
        return False, "Sequence completed"

    # Rule D: Condition Check on Target Step
    cond = (target_step.get("condition") or "no_reply").lower()
    if cond == "no_reply" and lead_status in ["replied", "interested"]:
        _update_contact_state(cc_id, "replied", now_str, db_path)
        return False, "Halted by no_reply condition"

    # 3. ATOMIC LOCK (Set state to 'sending')
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
        UPDATE campaign_contacts
        SET state = 'sending', last_event_at = ?
        WHERE id = ? AND state IN ('pending', 'scheduled')
    """, (now_str, cc_id))
    locked = cur.rowcount > 0
    conn.commit()
    conn.close()

    if not locked:
        return False, "Contact state locked by concurrent process"

    # 4. RESOLVE TEMPLATE & TOKENS
    contact_dict = {
        "id": lead_id,
        "name": lead_name,
        "first_name": lead_name.split()[0] if lead_name else "there",
        "company": contact_rec.get("company") or "",
        "email": lead_email,
        "tags": contact_rec.get("lead_tags") or "",
    }
    # Parse custom variables
    try:
        cv_raw = contact_rec.get("custom_variables")
        if cv_raw:
            contact_dict.update(json.loads(cv_raw))
    except Exception:
        pass

    raw_subject = target_step.get("subject", "")
    raw_body = target_step.get("body_html", "")

    resolved_subj = resolve_template(raw_subject, contact_dict)
    resolved_body = resolve_template(raw_body, contact_dict)

    # Send Guard check
    unfilled = _missing_tokens(resolved_subj) + _missing_tokens(resolved_body)
    if unfilled:
        _update_contact_state(cc_id, "error", now_str, db_path)
        record_campaign_event(camp_id, lead_id, target_step["id"], "error", {"reason": f"Unfilled tokens: {', '.join(unfilled)}"}, db_path=db_path)
        logger.warning(f"[Campaign #{camp_id}] Unfilled tokens for {lead_email}: {unfilled}. Skipped.")
        return False, f"Unfilled tokens: {', '.join(unfilled)}"

    # Append Corporate Signature if enabled on this sequence step
    include_sig = bool(int(target_step.get("include_signature", 1 if target_pos == 1 else 0)))
    sig_html = get_config("signature_html", db_path=db_path) or "<p>Best regards,<br>Jack Connor<br>Sellomize</p>"
    if include_sig and sig_html and sig_html not in resolved_body:
        full_html = f"{resolved_body}<br><br>{sig_html}"
    else:
        full_html = resolved_body

    # 5. PRE-FLIGHT MX VERIFICATION
    enforce_mx = (get_config("enforce_mx_check", "true", db_path=db_path) or "true").strip().lower() == "true"
    if enforce_mx and not dry_run:
        is_mx_ok, mx_reason, _ = verify_email_domain_mx(lead_email)
        if not is_mx_ok:
            _update_contact_state(cc_id, "bounced", now_str, db_path)
            record_email_bounce(lead_email, bounce_reason=f"Campaign MX fail: {mx_reason}", db_path=db_path)
            record_campaign_event(camp_id, lead_id, target_step["id"], "bounced", {"reason": mx_reason}, db_path=db_path)
            logger.warning(f"[Campaign #{camp_id}] MX check failed for {lead_email}: {mx_reason}")
            return False, f"MX check failed: {mx_reason}"

    # 6. ASSIGN ACTIVE SMTP ACCOUNT
    smtp_account = get_next_available_smtp_account(db_path=db_path)
    if not smtp_account:
        if dry_run:
            smtp_account = {"id": 0, "email": "dryrun@sellomize.com"}
        else:
            # Revert lock to pending for next cycle
            _update_contact_state(cc_id, "pending", now_str, db_path)
            logger.warning(f"[Campaign #{camp_id}] No available active SMTP account with quota. Postponing dispatch.")
            return False, "No active SMTP account available"

    # Create Outbox record for audit and tracking
    email_id = create_email(
        subject=resolved_subj,
        recipient=lead_email,
        email_html=full_html,
        status="Sending",
        scheduled_time=now_str,
        sequence_step=target_pos,
        target_timezone=campaign.get("timezone", ""),
        db_path=db_path
    )

    # Inject tracking pixel & click tracking
    final_payload = inject_tracking_and_links(full_html, email_id)

    # 7. DISPATCH VIA SMTP
    if dry_run:
        logger.info(f"[DRY RUN Campaign #{camp_id}] Step {target_pos} to {lead_email} via {smtp_account['email']}.")
        success = True
        msg_id = "DRY-RUN-ID"
    else:
        msg_id_tracker = []
        success, err_msg = send_smtp_email(
            smtp_account=smtp_account,
            recipient=lead_email,
            subject=resolved_subj,
            html_content=final_payload,
            message_id_out=msg_id_tracker
        )
        msg_id = msg_id_tracker[0] if msg_id_tracker else ""

    if success:
        mark_email_sent(email_id, message_id=msg_id, db_path=db_path)
        increment_smtp_sent(smtp_account["id"], db_path=db_path)
        advance_contact_followup(lead_id, delay_days=int(target_step.get("wait_days", 3)), db_path=db_path)
        record_campaign_event(
            camp_id, lead_id, target_step["id"], "sent",
            {"email_id": email_id, "subject": resolved_subj, "step": target_pos, "smtp": smtp_account["email"]},
            db_path=db_path
        )

        # 8. SCHEDULE NEXT STEP OR MARK COMPLETED
        next_step = next((s for s in steps if int(s.get("position", 0)) == target_pos + 1), None)
        if next_step:
            w_days = int(next_step.get("wait_days", 3))
            w_hours = int(next_step.get("wait_hours", 0))
            next_send_dt = now_dt + timedelta(days=w_days, hours=w_hours)
            next_send_str = next_send_dt.strftime("%Y-%m-%d %H:%M:%S")

            conn = get_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
                UPDATE campaign_contacts
                SET current_step = ?, state = 'scheduled', next_send_at = ?, last_event_at = ?
                WHERE id = ?
            """, (target_pos, next_send_str, now_str, cc_id))
            conn.commit()
            conn.close()
            logger.info(f"[Campaign #{camp_id}] Step {target_pos} sent to {lead_email}. Next step scheduled for {next_send_str}.")
        else:
            # Reached end of sequence
            conn = get_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
                UPDATE campaign_contacts
                SET current_step = ?, state = 'completed', next_send_at = '', last_event_at = ?
                WHERE id = ?
            """, (target_pos, now_str, cc_id))
            conn.commit()
            conn.close()
            record_campaign_event(camp_id, lead_id, None, "completed", {"total_steps": target_pos}, db_path=db_path)
            logger.info(f"[Campaign #{camp_id}] Final step {target_pos} sent to {lead_email}. Sequence completed.")

        return True, "Step dispatched successfully"

    else:
        mark_email_error(email_id, status="Error", error_message=err_msg, db_path=db_path)
        _update_contact_state(cc_id, "error", now_str, db_path)
        record_campaign_event(camp_id, lead_id, target_step["id"], "error", {"error": err_msg}, db_path=db_path)
        logger.error(f"[Campaign #{camp_id}] Dispatch failed for {lead_email}: {err_msg}")
        return False, f"Dispatch failed: {err_msg}"


def _update_contact_state(cc_id: int, new_state: str, event_time: str, db_path: str = DB_FILE):
    """Helper to update a contact's enrollment state in campaign_contacts."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
        UPDATE campaign_contacts
        SET state = ?, last_event_at = ?
        WHERE id = ?
    """, (new_state, event_time, cc_id))
    conn.commit()
    conn.close()


def run_campaign_engine_cycle(dry_run: bool = False, db_path: str = DB_FILE) -> Dict[str, Any]:
    """
    Main polling cycle for automated campaigns.
    Called on each background worker cycle by scheduler.py.
    """
    active_campaigns = get_all_campaigns(status_filter="Active", db_path=db_path)
    if not active_campaigns:
        return {"active_campaigns": 0, "dispatched": 0}

    total_dispatched = 0

    for camp in active_campaigns:
        cid = camp["id"]

        # Check sending window (days and hours in campaign timezone)
        in_win, win_reason = is_campaign_in_window(camp)
        if not in_win and not dry_run:
            logger.debug(f"[Campaign #{cid}] '{camp['name']}' paused: {win_reason}")
            continue

        # Check daily sending limit for this campaign
        daily_lim = int(camp.get("daily_limit", 50))
        sent_today = get_campaign_today_sent_count(cid, db_path=db_path)
        if sent_today >= daily_lim and not dry_run:
            logger.debug(f"[Campaign #{cid}] Daily limit reached ({sent_today}/{daily_lim}). Pausing until tomorrow.")
            continue

        # Fetch sequence steps
        steps = get_campaign_steps(cid, db_path=db_path)
        if not steps:
            logger.debug(f"[Campaign #{cid}] Has no sequence steps configured.")
            continue

        # Fetch batch of due contacts (up to 5 per cycle per campaign to prevent spikes)
        remaining_quota = daily_lim - sent_today
        batch_limit = min(remaining_quota, 5) if not dry_run else 5
        due_contacts = get_due_campaign_contacts(cid, limit=batch_limit, db_path=db_path)

        if not due_contacts:
            # Fast indexed check: are there any pending/scheduled/sending contacts remaining?
            conn = get_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM campaign_contacts
                WHERE campaign_id = ? AND state IN ('pending', 'scheduled', 'sending')
                LIMIT 1
            """, (cid,))
            has_pending = cur.fetchone() is not None
            if not has_pending:
                cur.execute("SELECT COUNT(*) as total FROM campaign_contacts WHERE campaign_id = ?", (cid,))
                t_row = cur.fetchone()
                total_cnt = int(t_row["total"] or 0) if t_row else 0
                if total_cnt > 0:
                    update_campaign(cid, status="Completed", db_path=db_path)
                    logger.info(f"[Campaign #{cid}] '{camp['name']}' automatically completed (all {total_cnt} contacts processed).")
            conn.close()
            continue

        delay_seconds = float(camp.get("delay_seconds", 60))

        for idx, contact_rec in enumerate(due_contacts):
            ok, msg = process_campaign_contact_step(
                campaign=camp,
                contact_rec=contact_rec,
                steps=steps,
                dry_run=dry_run,
                db_path=db_path
            )
            if ok:
                total_dispatched += 1
                # Enforce configured inter-email delay if more due contacts in this cycle
                if idx < len(due_contacts) - 1 and not dry_run:
                    # Enforce delay up to 15s to keep cycle responsive
                    sleep_dur = min(delay_seconds, 15.0)
                    time.sleep(sleep_dur)

    return {
        "active_campaigns": len(active_campaigns),
        "dispatched": total_dispatched
    }
