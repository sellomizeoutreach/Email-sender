"""
scheduler.py - Standalone email dispatch scheduler interfacing with Microsoft Outlook via pywin32.
Runs independently of Streamlit, polls SQLite database every 60 seconds, and dispatches due approved emails.

Key Constraints Enforced:
1. COM Threading Safety: Explicitly calls pythoncom.CoInitialize() directly before calling
   win32com.client.Dispatch("Outlook.Application") in each polling cycle.
2. Local System Timezone: Strictly compares scheduled_time against Local System Time (YYYY-MM-DD HH:MM:SS)
   to ensure accurate send timing without UTC drift.
3. Sender Account Verification: Matches designated sender against outlook.Session.Accounts,
   falling back to OLE invoke DISPID 64209 if property assignment fails.
4. HTML Body Concatenation: Combines approved HTML with saved signature HTML.
"""

import time
import sys
import random
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

# Import COM modules (Windows desktop only)
try:
    import pythoncom
    import win32com.client
    COM_AVAILABLE = True
except ImportError:
    pythoncom = None
    win32com = None
    COM_AVAILABLE = False

from database import (
    get_approved_due_emails,
    get_config,
    set_config,
    mark_email_sent,
    mark_email_error,
    update_email,
    get_next_available_smtp_account,
    increment_smtp_sent,
    advance_contact_followup,
    record_email_bounce,
    init_db,
    DB_FILE,
    get_log_file_path
)
from smtp_dispatcher import send_smtp_email, sanitize_header, scan_all_hostinger_bounces, scan_all_hostinger_inbox
from tracker import (
    inject_tracking_pixel,
    inject_tracking_and_links,
    wrap_links_with_click_tracking,
    start_tracking_server
)
from mx_checker import verify_email_domain_mx
from timezone_helper import (
    is_within_market_hours,
    get_market_info,
    calculate_market_aware_schedule,
    TARGET_MARKETS,
    get_engine_now,
    get_engine_now_str
)


# Configure logging with both console and sellomize.log file handler
handlers = [logging.StreamHandler(sys.stdout)]
try:
    file_handler = logging.FileHandler(get_log_file_path(), mode="a", encoding="utf-8")
    handlers.append(file_handler)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Scheduler] %(message)s",
    handlers=handlers
)
logger = logging.getLogger("scheduler")

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def is_within_sending_window(
    check_dt: Optional[datetime] = None,
    db_path: str = DB_FILE,
    email_record: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str]:
    """
    Evaluate whether a given datetime (or current local time) falls inside the
    configured campaign sending window and allowed business days.
    If schedule_mode is 'adaptive_multi_country' and an email_record is provided
    with target_timezone, it validates against the target market's business hours.
    Returns (True, message) if dispatch is allowed, or (False, reason) if paused.
    """
    enforce_str = get_config("enforce_sending_window", "false", db_path=db_path) or "false"
    enforce = enforce_str.strip().lower() in ["true", "1", "yes", "on"]
    if not enforce:
        return True, "Sending window enforcement disabled (24/7 delivery allowed)"

    sched_mode = (get_config("schedule_mode", "adaptive_multi_country", db_path=db_path) or "adaptive_multi_country").strip()
    if sched_mode == "continuous":
        return True, "Continuous 24/7 delivery enabled"

    # Adaptive multi-country evaluation:
    if sched_mode == "adaptive_multi_country" and email_record and email_record.get("target_timezone") and email_record["target_timezone"].upper() != "LOCAL":
        m_tz = email_record["target_timezone"]
        m_key = email_record.get("market_key") or m_tz
        m_info = get_market_info(m_key)
        m_days = m_info.get("days", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
        m_start = m_info.get("default_start", "09:00")
        m_end = m_info.get("default_end", "17:00")
        return is_within_market_hours(
            market_key_or_tz=m_tz,
            days=m_days,
            start_time=m_start,
            end_time=m_end,
            reference_dt=check_dt
        )

    # Office hours check in UTC+5 engine timeframe
    dt = check_dt or get_engine_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_engine_now().tzinfo)

    day_name = dt.strftime("%A")
    raw_days = get_config("sending_days", "Monday,Tuesday,Wednesday,Thursday,Friday", db_path=db_path) or ""
    allowed_days = [d.strip().capitalize() for d in raw_days.split(",") if d.strip()]
    if not allowed_days:
        allowed_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    if day_name not in allowed_days:
        return False, f"Today ({day_name}) is outside allowed sending days ({', '.join(allowed_days)})"

    start_str = (get_config("sending_start_time", "09:00", db_path=db_path) or "09:00").strip()
    end_str = (get_config("sending_end_time", "18:00", db_path=db_path) or "18:00").strip()

    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
    except (ValueError, AttributeError) as t_err:
        logger.warning(f"Error parsing sending window time '{start_str}'-'{end_str}': {t_err}. Using default 09:00-18:00.")
        sh, sm, eh, em = 9, 0, 18, 0

    curr_mins = dt.hour * 60 + dt.minute
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    if start_mins <= end_mins:
        inside = (start_mins <= curr_mins < end_mins)
        if not inside:
            if curr_mins < start_mins:
                return False, f"Current time ({dt.strftime('%H:%M')}) is before daily start time ({start_str})"
            else:
                return False, f"Current time ({dt.strftime('%H:%M')}) is past daily cutoff time ({end_str})"
    else:
        # Crosses midnight (e.g. 21:00 - 05:00)
        inside = (curr_mins >= start_mins or curr_mins < end_mins)
        if not inside:
            return False, f"Current time ({dt.strftime('%H:%M')}) is outside overnight window ({start_str}-{end_str})"

    return True, f"Inside outbound window ({day_name} {start_str}-{end_str})"

def get_next_valid_sending_datetime(
    base_dt: Optional[datetime] = None,
    delay_minutes: int = 0,
    sending_days: Optional[List[str]] = None,
    start_time_str: Optional[str] = None,
    end_time_str: Optional[str] = None,
    db_path: str = DB_FILE
) -> datetime:
    """
    Calculate the next valid sending datetime adhering to allowed days of week
    and daily working hours. If outside hours or on a weekend/pause day, advances
    to the next allowed day at start_time.
    """
    dt = base_dt or get_engine_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_engine_now().tzinfo)

    if delay_minutes > 0:
        dt = dt + timedelta(minutes=delay_minutes)

    enforce_str = get_config("enforce_sending_window", "true", db_path=db_path) or "true"
    enforce = enforce_str.strip().lower() in ["true", "1", "yes", "on"]
    if not enforce and sending_days is None and start_time_str is None and end_time_str is None:
        return dt

    if sending_days is not None and len(sending_days) > 0:
        allowed_days = [d.strip().capitalize() for d in sending_days if d.strip()]
    else:
        raw_days = get_config("sending_days", "Monday,Tuesday,Wednesday,Thursday,Friday", db_path=db_path) or ""
        allowed_days = [d.strip().capitalize() for d in raw_days.split(",") if d.strip()]
        if not allowed_days:
            allowed_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    s_str = (start_time_str or get_config("sending_start_time", "09:00", db_path=db_path) or "09:00").strip()
    e_str = (end_time_str or get_config("sending_end_time", "18:00", db_path=db_path) or "18:00").strip()

    try:
        sh, sm = map(int, s_str.split(":"))
        eh, em = map(int, e_str.split(":"))
    except (ValueError, AttributeError) as t_err:
        logger.warning(f"Error parsing sending window time '{s_str}'-'{e_str}': {t_err}. Using default 09:00-18:00.")
        sh, sm, eh, em = 9, 0, 18, 0

    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    # Loop up to 14 days to find next valid time slot
    for _ in range(14):
        day_name = dt.strftime("%A")
        curr_mins = dt.hour * 60 + dt.minute

        if day_name in allowed_days:
            if curr_mins < start_mins:
                return dt.replace(hour=sh, minute=sm, second=0, microsecond=0)
            elif curr_mins < end_mins:
                return dt
            else:
                tomorrow = dt + timedelta(days=1)
                dt = tomorrow.replace(hour=sh, minute=sm, second=0, microsecond=0)
        else:
            tomorrow = dt + timedelta(days=1)
            dt = tomorrow.replace(hour=sh, minute=sm, second=0, microsecond=0)

    return dt


def calculate_staggered_schedule(
    total_contacts: int,
    stagger_mode: str = "fixed_interval",
    base_dt: Optional[datetime] = None,
    span_hours: float = 4.0,
    spacing_minutes: float = 5.0,
    sending_days: Optional[List[str]] = None,
    start_time_str: str = "09:00",
    end_time_str: str = "18:00",
    use_jitter: bool = False,
    jitter_seed: Optional[int] = None
) -> List[datetime]:
    """
    Calculate an individual scheduled datetime for each contact in a campaign batch.

    Supported stagger_mode values:
    - 'send_now' or 'none': Schedule all contacts for the earliest valid delivery slot.
    - 'fixed_interval' or 'fixed_gap': Step by a fixed spacing_minutes per contact.
    - 'next_x_hours' or 'span_hours': Distribute all contacts evenly across the next X hours from base_dt.
    - 'daily_window': Distribute all contacts evenly across the active daily window (start_time to end_time).

    Sending Window Hard Constraint & Rollover Pacing:
    The sending window is the hard constraint. Always. If a schedule reaches or exceeds today's
    daily cutoff (end_time_str), the remaining contacts roll over to the next allowed sending day
    at start_time_str and continue pacing forward from there at the same cadence spacing, without clumping.
    """
    if total_contacts <= 0:
        return []

    import random
    rng = random.Random(jitter_seed) if jitter_seed is not None else random

    dt = base_dt or get_engine_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_engine_now().tzinfo)

    normalized_mode = (stagger_mode or "fixed_interval").strip().lower()

    if "hour" in normalized_mode or "span" in normalized_mode:
        total_span_mins = max(1.0, float(span_hours) * 60.0)
        step_mins = (total_span_mins / total_contacts) if total_contacts > 1 else 0.0
    elif "window" in normalized_mode or "daily" in normalized_mode:
        try:
            sh, sm = map(int, start_time_str.split(":"))
            eh, em = map(int, end_time_str.split(":"))
        except Exception:
            sh, sm, eh, em = 9, 0, 18, 0
        start_mins = sh * 60 + sm
        end_mins = eh * 60 + em
        if end_mins > start_mins:
            win_mins = float(end_mins - start_mins)
        else:
            win_mins = float((24 * 60 - start_mins) + end_mins)
        win_mins = max(1.0, win_mins)
        step_mins = (win_mins / total_contacts) if total_contacts > 1 else 0.0
    elif "none" in normalized_mode or "no" in normalized_mode or "now" in normalized_mode:
        step_mins = 0.0
    else:
        step_mins = max(0.0, float(spacing_minutes))

    scheduled_dts = []
    prev_nominal = None

    for i in range(total_contacts):
        if i == 0:
            nominal = get_next_valid_sending_datetime(
                base_dt=dt,
                delay_minutes=0,
                sending_days=sending_days,
                start_time_str=start_time_str,
                end_time_str=end_time_str
            )
        else:
            if step_mins <= 0:
                nominal = prev_nominal
            else:
                cand = prev_nominal + timedelta(minutes=step_mins)
                nominal = get_next_valid_sending_datetime(
                    base_dt=cand,
                    delay_minutes=0,
                    sending_days=sending_days,
                    start_time_str=start_time_str,
                    end_time_str=end_time_str
                )
        prev_nominal = nominal

        if use_jitter and step_mins > 0:
            max_jitter_sec = min(90.0, max(20.0, step_mins * 60.0 * 0.2))
            jitter_sec = rng.uniform(-max_jitter_sec, max_jitter_sec)
            actual_dt = nominal + timedelta(seconds=jitter_sec)

            try:
                sh, sm = map(int, start_time_str.split(":"))
                eh, em = map(int, end_time_str.split(":"))
            except Exception:
                sh, sm, eh, em = 9, 0, 18, 0

            day_start = nominal.replace(hour=sh, minute=sm, second=0, microsecond=0)
            if eh == 24 and em == 0:
                day_end = nominal.replace(hour=23, minute=59, second=59, microsecond=0)
            else:
                day_end = nominal.replace(hour=eh, minute=em, second=0, microsecond=0) - timedelta(seconds=1)

            if actual_dt < day_start:
                actual_dt = day_start
            elif actual_dt > day_end:
                actual_dt = day_end

            if scheduled_dts and actual_dt <= scheduled_dts[-1]:
                actual_dt = scheduled_dts[-1] + timedelta(seconds=1)
        else:
            actual_dt = nominal

        scheduled_dts.append(actual_dt)

    return scheduled_dts


def analyze_schedule_overflow(
    scheduled_dts: List[datetime],
    end_time_str: str = "18:00",
    reference_dt: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Analyze scheduled datetimes to detect if contacts spill beyond the initial day's cutoff.
    Returns detailed breakdown including today's count, overflow count, and transparent warning text.
    """
    if not scheduled_dts:
        return {
            "total_count": 0,
            "today_count": 0,
            "overflow_count": 0,
            "fits_today": True,
            "first_dt": None,
            "last_dt": None,
            "warning_message": None,
            "summary_message": "No contacts selected."
        }

    first_dt = scheduled_dts[0]
    last_dt = scheduled_dts[-1]
    first_date = first_dt.date()

    today_contacts = [dt for dt in scheduled_dts if dt.date() == first_date]
    overflow_contacts = [dt for dt in scheduled_dts if dt.date() > first_date]

    total_count = len(scheduled_dts)
    today_count = len(today_contacts)
    overflow_count = len(overflow_contacts)
    fits_today = (overflow_count == 0)

    ref = reference_dt or get_engine_now()
    if ref.tzinfo is None and first_dt.tzinfo is not None:
        ref = ref.replace(tzinfo=first_dt.tzinfo)

    ref_date = ref.date()
    if first_date == ref_date:
        first_day_label = "today"
    elif first_date == ref_date + timedelta(days=1):
        first_day_label = "tomorrow"
    else:
        first_day_label = first_dt.strftime("%A, %b %d")

    warning_message = None
    if overflow_count > 0:
        next_dt = overflow_contacts[0]
        next_date = next_dt.date()
        if next_date == ref_date + timedelta(days=1):
            next_day_label = "tomorrow"
        elif next_date == first_date + timedelta(days=1):
            next_day_label = "tomorrow"
        else:
            next_day_label = next_dt.strftime("%A")

        next_start_str = next_dt.strftime("%H:%M")
        if first_day_label == "today":
            warning_message = (
                f"{total_count} contacts won't all fit before {end_time_str} today — "
                f"{overflow_count} will continue {next_day_label} from {next_start_str}."
            )
        else:
            warning_message = (
                f"{total_count} contacts won't all fit before {end_time_str} on {first_day_label} — "
                f"{overflow_count} will continue {next_day_label} from {next_start_str}."
            )

    if total_count == 1:
        summary_message = f"1 contact scheduled for {first_dt.strftime('%A, %b %d at %H:%M')}."
    elif fits_today:
        summary_message = (
            f"All {total_count} contacts will be dispatched {first_day_label} "
            f"between {first_dt.strftime('%H:%M')} and {last_dt.strftime('%H:%M')}."
        )
    else:
        summary_message = (
            f"Scheduled from {first_dt.strftime('%A, %b %d at %H:%M')} "
            f"through {last_dt.strftime('%A, %b %d at %H:%M')}."
        )

    return {
        "total_count": total_count,
        "today_count": today_count,
        "overflow_count": overflow_count,
        "fits_today": fits_today,
        "first_dt": first_dt,
        "last_dt": last_dt,
        "warning_message": warning_message,
        "summary_message": summary_message
    }



def get_local_system_time_str() -> str:
    """
    Return current time in Engine Timeframe (UTC+5) formatted strictly as YYYY-MM-DD HH:MM:SS.
    Standardized across the engine to prevent UTC mismatches.
    """
    return get_engine_now_str()

def get_outlook_application():
    """
    Import pywin32 and dispatch Outlook.Application.
    Explicitly initializes COM on the current thread using pythoncom.CoInitialize()
    directly before win32com.client.Dispatch to avoid threading crashes.
    """
    if not COM_AVAILABLE:
        raise RuntimeError(
            "Microsoft Outlook COM dispatch is only supported on Windows desktop with Outlook installed. "
            "Cloud environments (e.g. Streamlit Cloud) run the UI and CRM layers."
        )
    try:
        # Explicit COM initialization for the current polling thread
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        return outlook
    except Exception as e:
        logger.error(f"Failed to initialize Outlook COM interface: {e}")
        raise

def find_matching_account(outlook_app, designated_sender: str):
    """
    Iterate through outlook.Session.Accounts to find the account where
    account.SmtpAddress matches the designated email.
    """
    if not designated_sender:
        return None

    target = designated_sender.strip().lower()
    try:
        accounts = outlook_app.Session.Accounts
        for account in accounts:
            try:
                smtp_addr = getattr(account, "SmtpAddress", "")
                if smtp_addr and smtp_addr.strip().lower() == target:
                    return account
            except Exception as acc_err:
                logger.debug(f"Error checking account properties: {acc_err}")
                continue
    except Exception as e:
        logger.error(f"Error accessing Outlook Session Accounts: {e}")
    return None

def assign_sender_account(mail_item, account):
    """
    Apply the matching account to the outgoing message using mail.SendUsingAccount = account.
    If a Python COM property error occurs, use the fallback mail._oleobj_.Invoke(*(64209, 0, 8, 0, account)).
    """
    try:
        mail_item.SendUsingAccount = account
        logger.info(f"Successfully assigned account via mail.SendUsingAccount: {getattr(account, 'SmtpAddress', account)}")
    except Exception as com_err:
        logger.warning(f"Standard SendUsingAccount assignment failed ({com_err}). Attempting OLE fallback...")
        try:
            # DISPID 64209 is SendUsingAccount in the Outlook Object Model
            mail_item._oleobj_.Invoke(*(64209, 0, 8, 0, account))
            logger.info("Successfully assigned account via OLE Invoke (DISPID 64209).")
        except Exception as ole_err:
            logger.error(f"OLE Invoke fallback also failed: {ole_err}")
            raise RuntimeError(f"Could not bind Outlook account to message: {ole_err}")

def dispatch_email_hostinger(email_record: dict, dry_run: bool = False, db_path: str = DB_FILE):
    """
    Dispatch a single approved email record through Hostinger Direct SMTP.
    Rotates through active Hostinger SMTP accounts, respects daily limits,
    attaches HTML body and signature, and sends via SSL/TLS.
    Shields reputation with pre-flight MX and DNS sanity verification.
    """
    email_id = email_record["id"]
    recipient = sanitize_header(email_record.get("recipient", ""))
    subject = sanitize_header(email_record.get("subject", "Listing Audit"))
    approved_email_html = email_record.get("email_html", "").strip()

    logger.info(f"[Hostinger SMTP] Processing Email ID #{email_id} for recipient '{recipient}'...")

    if not recipient:
        err_msg = "Recipient email address is missing or empty."
        logger.warning(f"Email ID #{email_id}: {err_msg}")
        mark_email_error(email_id, status="Error", error_message=err_msg, db_path=db_path)
        return False

    # Pre-flight MX record and domain sanity check
    enforce_mx = (get_config("enforce_mx_check", "true", db_path=db_path) or "true").strip().lower() == "true"
    if enforce_mx:
        is_valid, mx_reason, _ = verify_email_domain_mx(recipient)
        if not is_valid:
            bounce_err = f"Pre-flight MX check failed: {mx_reason}"
            logger.warning(f"Email ID #{email_id}: Intercepting dead domain dispatch for '{recipient}'. Reason: {bounce_err}")
            mark_email_error(email_id, status="Bounced", error_message=bounce_err, db_path=db_path)
            record_email_bounce(recipient_email=recipient, bounce_reason=bounce_err, db_path=db_path)
            return False

    # Fetch next active Hostinger account in rotation
    smtp_account = get_next_available_smtp_account(db_path=db_path)
    if not smtp_account:
        err_msg = "No active Hostinger SMTP account available (or all configured accounts have reached their daily sending limit)."
        logger.warning(f"Email ID #{email_id}: {err_msg}")
        mark_email_error(email_id, status="Error", error_message=err_msg, db_path=db_path)
        return False

    # Prepare signature, tracking pixel, and payload
    signature_html = (get_config("signature_html", db_path=db_path) or "").strip()
    bcc_address = sanitize_header(email_record.get("bcc_email") or get_config("bcc_email", db_path=db_path) or "")

    # Click tracking (if enabled with a public domain) applies ONLY to campaign body links.
    # Corporate signature links (e.g. sellomize.com) remain 100% direct and pristine.
    body_with_links = wrap_links_with_click_tracking(approved_email_html, email_id)
    combined_body = f"{body_with_links}<br><br>{signature_html}" if signature_html else body_with_links
    final_payload = inject_tracking_pixel(combined_body, email_id)

    followup_delay = int(get_config("followup_delay_days", "4", db_path=db_path) or 4)

    if dry_run:
        logger.info(f"[DRY RUN Hostinger SMTP] Would send Email ID #{email_id} to '{recipient}' from '{smtp_account['email']}' via Hostinger.")
        mark_email_sent(email_id, db_path=db_path)
        advance_contact_followup(recipient, delay_days=followup_delay, db_path=db_path)
        return True

    in_reply_to_header = email_record.get("in_reply_to") or None
    msg_id_tracker = []
    success, msg = send_smtp_email(
        smtp_account=smtp_account,
        recipient=recipient,
        subject=subject,
        html_content=final_payload,
        bcc_email=bcc_address,
        in_reply_to=in_reply_to_header,
        message_id_out=msg_id_tracker
    )

    try:
        if success:
            sent_msg_id = msg_id_tracker[0] if msg_id_tracker else ""
            mark_email_sent(email_id, message_id=sent_msg_id, db_path=db_path)
            increment_smtp_sent(smtp_account["id"], db_path=db_path)
            try:
                update_email(
                    email_id=email_id,
                    sent_via=f"Hostinger ({smtp_account['email']})",
                    smtp_account_id=smtp_account["id"],
                    db_path=db_path
                )
            except Exception as upd_err:
                logger.warning(f"Could not update sent_via for email #{email_id}: {upd_err}")

            advance_contact_followup(recipient, delay_days=followup_delay, db_path=db_path)
            logger.info(f"Successfully dispatched Email ID #{email_id} to '{recipient}' via Hostinger account '{smtp_account['email']}'.")
            return True
        else:
            mark_email_error(email_id, status="Error", error_message=msg, db_path=db_path)
            return False
    except Exception as dispatch_err:
        logger.error(f"Error during post-dispatch processing for Email ID #{email_id}: {dispatch_err}")
        mark_email_error(email_id, status="Error", error_message=str(dispatch_err), db_path=db_path)
        return False

def dispatch_email_outlook(email_record: dict, dry_run: bool = False, db_path: str = DB_FILE):
    """
    Dispatch a single approved email record through Outlook.
    Handles COM thread safety, account verification, HTML concatenation with signature, BCC, and status updates.
    Shields reputation with pre-flight MX and DNS sanity verification.
    """
    email_id = email_record["id"]
    recipient = sanitize_header(email_record.get("recipient", ""))
    subject = sanitize_header(email_record.get("subject", "Listing Audit"))
    approved_email_html = email_record.get("email_html", "").strip()

    logger.info(f"[Outlook] Processing Email ID #{email_id} for recipient '{recipient}'...")

    if not recipient:
        err_msg = "Recipient email address is missing or empty."
        logger.warning(f"Email ID #{email_id}: {err_msg}")
        mark_email_error(email_id, status="Error", error_message=err_msg, db_path=db_path)
        return

    # Pre-flight MX record and domain sanity check
    enforce_mx = (get_config("enforce_mx_check", "true", db_path=db_path) or "true").strip().lower() == "true"
    if enforce_mx:
        is_valid, mx_reason, _ = verify_email_domain_mx(recipient)
        if not is_valid:
            bounce_err = f"Pre-flight MX check failed: {mx_reason}"
            logger.warning(f"Email ID #{email_id}: Intercepting dead domain dispatch for '{recipient}'. Reason: {bounce_err}")
            mark_email_error(email_id, status="Bounced", error_message=bounce_err, db_path=db_path)
            record_email_bounce(recipient_email=recipient, bounce_reason=bounce_err, db_path=db_path)
            return

    # Fetch configuration
    designated_sender = sanitize_header(get_config("sender_email", db_path=db_path) or "")
    bcc_address = sanitize_header(email_record.get("bcc_email") or get_config("bcc_email", db_path=db_path) or "")
    signature_html = (get_config("signature_html", db_path=db_path) or "").strip()

    followup_delay = int(get_config("followup_delay_days", "4", db_path=db_path) or 4)

    if dry_run:
        logger.info(f"[DRY RUN Outlook] Would send Email ID #{email_id} to '{recipient}' from '{designated_sender}' with BCC '{bcc_address}'")
        mark_email_sent(email_id, db_path=db_path)
        advance_contact_followup(recipient, delay_days=followup_delay, db_path=db_path)
        return

    # 1. Connect to Outlook with explicit COM initialization
    try:
        outlook_app = get_outlook_application()
    except Exception as e:
        mark_email_error(email_id, status="Error", error_message=f"Outlook COM connection failed: {e}", db_path=db_path)
        return

    try:
        # 2. Match designated Sender Email in Outlook Accounts
        if designated_sender:
            matching_account = find_matching_account(outlook_app, designated_sender)
            if not matching_account:
                err_msg = f"Account Mismatch: No Outlook account with SmtpAddress matching '{designated_sender}' found."
                logger.error(f"Email ID #{email_id}: {err_msg}")
                mark_email_error(email_id, status="Account Mismatch", error_message=err_msg, db_path=db_path)
                return
        else:
            matching_account = None

        # 3. Create MailItem (0 = olMailItem)
        mail = outlook_app.CreateItem(0)
        mail.To = recipient
        mail.Subject = subject

        # Attach pre-configured BCC address if set
        if bcc_address:
            mail.BCC = bcc_address

        # Concatenate approved HTML body with signature HTML and tracking pixel
        # Click tracking applies ONLY to campaign body links, NEVER to corporate signature links
        body_with_links = wrap_links_with_click_tracking(approved_email_html, email_id)
        if signature_html:
            combined_body = f"{body_with_links}<br><br>{signature_html}"
        else:
            combined_body = body_with_links
        final_payload = inject_tracking_pixel(combined_body, email_id)

        # Assign directly to HTMLBody
        mail.HTMLBody = final_payload

        # Apply matching account if configured
        if matching_account:
            assign_sender_account(mail, matching_account)

        # Dispatch
        mail.Send()
        logger.info(f"Successfully dispatched Email ID #{email_id} to '{recipient}'.")
        mark_email_sent(email_id, db_path=db_path)
        advance_contact_followup(recipient, delay_days=followup_delay, db_path=db_path)

    except Exception as dispatch_err:
        logger.error(f"Error dispatching Email ID #{email_id}: {dispatch_err}")
        mark_email_error(email_id, status="Error", error_message=str(dispatch_err), db_path=db_path)
    finally:
        # Clean up COM references on this cycle
        if COM_AVAILABLE and pythoncom:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

# Backward compatibility alias
dispatch_email = dispatch_email_outlook

def process_due_sequence_rules(db_path: Optional[str] = None) -> int:
    """
    Check for sequence rules whose delay timer has expired since the outreach email was sent.
    If the contact hasn't replied, automatically generates the follow-up email draft with
    the specified template and variables, scheduled for the earliest valid sending window slot.
    """
    target_db = db_path or DB_FILE
    from database import (
        get_due_sequence_rules,
        mark_sequence_rule_status,
        get_contact_by_email,
        get_contact_by_id,
        get_template_by_id,
        create_email,
        create_notification,
        create_sequence_rule,
        link_sequence_rule_trigger,
        get_config
    )
    from template_engine import resolve_template, format_email_html, parse_spintax, inject_variables, scan_all_negative_keywords

    now_iso = get_engine_now_str()
    due_rules = get_due_sequence_rules(current_time_iso=now_iso, db_path=target_db)
    generated_count = 0

    neg_keywords_setting = get_config("negative_keywords", "", db_path=target_db)

    for rule in due_rules:
        rid = rule["id"]
        c_email = (rule.get("contact_email") or "").strip().lower()
        contact = get_contact_by_email(c_email, db_path=target_db)
        if not contact and rule.get("contact_id"):
            contact = get_contact_by_id(rule["contact_id"], db_path=target_db)

        if not contact:
            mark_sequence_rule_status(rid, "Cancelled", db_path=target_db)
            continue

        c_status = contact.get("status") or ""
        # Check if prospect replied or bounced or opted out
        if c_status in ["Replied", "Bounced", "Do Not Contact", "Closed Won", "Closed Lost"] or "Replied" in (contact.get("tags") or ""):
            mark_sequence_rule_status(rid, "Cancelled", db_path=target_db)
            logger.info(f"[Sequence Engine] Cancelled Rule #{rid} for {c_email} (Contact status: {c_status}).")
            continue

        custom_body = (rule.get("custom_body") or "").strip()
        if custom_body:
            raw_body = custom_body
        elif rule.get("template_id") and rule.get("template_id") > 0:
            tpl = get_template_by_id(rule["template_id"], db_path=target_db)
            if not tpl:
                logger.warning(f"[Sequence Engine] Rule #{rid}: Template ID #{rule['template_id']} not found.")
                mark_sequence_rule_status(rid, "Cancelled", db_path=target_db)
                continue
            raw_body = tpl["body_content"]
        else:
            logger.warning(f"[Sequence Engine] Rule #{rid}: Neither custom_body nor template_id configured.")
            mark_sequence_rule_status(rid, "Cancelled", db_path=target_db)
            continue

        raw_subj = rule.get("custom_subject") or f"Re: Follow up for {contact.get('company') or contact['name']}"
        resolved_subj = parse_spintax(inject_variables(raw_subj, contact))
        resolved_body = resolve_template(raw_body, contact)
        final_html = format_email_html(resolved_body)

        combined_text = f"{resolved_subj} {final_html}"
        triggers = scan_all_negative_keywords(combined_text, neg_keywords_setting)

        step_num = rule.get("step_number", 2)
        delay_val = rule.get("delay_value", 3)
        delay_unit = rule.get("delay_unit", "days")

        if triggers:
            email_status = "Flagged"
            trig_str = ", ".join([f"'{t}'" for t in triggers])
            notes = f"Sequence Step {step_num}: Flagged for trigger keyword(s): {trig_str}"
        else:
            email_status = "Pending"
            notes = f"Sequence Step {step_num} (Auto-generated after {delay_val} {delay_unit} delay)"

        m_tz = rule.get("target_timezone")
        m_key = rule.get("market_key") or m_tz
        m_country = rule.get("target_country")
        if m_tz and m_tz.upper() != "LOCAL":
            pairs = calculate_market_aware_schedule(
                total_contacts=1,
                market_key_or_tz=m_key,
                stagger_mode="send_now"
            )
            if pairs:
                sched_time_str = pairs[0][1].strftime("%Y-%m-%d %H:%M:%S")
            else:
                next_dt = get_next_valid_sending_datetime(delay_minutes=0, db_path=target_db)
                sched_time_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            next_dt = get_next_valid_sending_datetime(delay_minutes=0, db_path=target_db)
            sched_time_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")

        new_eid = create_email(
            email_html=final_html,
            subject=resolved_subj,
            recipient=c_email,
            status=email_status,
            scheduled_time=sched_time_str,
            revision_notes=notes,
            sequence_step=step_num,
            sequence_id=rule.get("sequence_id", ""),
            target_timezone=m_tz,
            target_country=m_country,
            market_key=m_key,
            db_path=target_db
        )

        mark_sequence_rule_status(rid, "Generated", db_path=target_db)
        generated_count += 1

        # If Step 2 was just generated, link new_eid as trigger for Step 3 rule
        if step_num == 2 and rule.get("sequence_id"):
            link_sequence_rule_trigger(
                sequence_id=rule["sequence_id"],
                contact_email=c_email,
                step_number=3,
                trigger_email_id=new_eid,
                db_path=target_db
            )

        create_notification(
            type="system",
            title=f"⚡ Auto-Generated Follow-Up for {contact['name']}",
            message=f"Created Step {step_num} follow-up draft ('{resolved_subj}') for {c_email} after {delay_val} {delay_unit} send delay.",
            contact_email=c_email,
            db_path=target_db
        )
        logger.info(f"[Sequence Engine] Auto-generated follow-up draft #{new_eid} for {c_email} (Step {step_num}).")

    return generated_count

def run_scheduler_cycle(dry_run: bool = False, db_path: Optional[str] = None) -> int:
    """
    Check database for due approved emails and dispatch them.
    Explicitly enforces comparison against Local System Time (YYYY-MM-DD HH:MM:SS)
    and validates whether current time falls within allowed business sending days & hours.
    Also processes automated follow-up sequence rules whose send delay has elapsed.
    """
    target_db = db_path or DB_FILE

    # Record worker heartbeat timestamp in UTC+5
    try:
        set_config("worker_heartbeat", get_engine_now_str(), db_path=target_db)
    except Exception:
        pass

    # 1. Process automated follow-up sequence rules waiting on send delays
    try:
        process_due_sequence_rules(db_path=target_db)
    except Exception as seq_err:
        logger.warning(f"Error processing due sequence rules: {seq_err}")

    # 2. Check sending window (global / office hours mode)
    sched_mode = (get_config("schedule_mode", "adaptive_multi_country", db_path=target_db) or "adaptive_multi_country").strip()
    if sched_mode != "adaptive_multi_country":
        in_window, window_msg = is_within_sending_window(db_path=target_db)
        if not in_window and not dry_run:
            logger.info(f"[Scheduler] Dispatch paused: {window_msg}.")
            return 0

    now_local_str = get_local_system_time_str()
    due_emails = get_approved_due_emails(now_local_str, db_path=target_db)
    count = len(due_emails)

    if count > 0:
        dispatch_method = get_config("dispatch_method", "hostinger_smtp", db_path=target_db)
        try:
            min_delay = float(get_config("min_delay_seconds", "20", db_path=target_db))
            max_delay = float(get_config("max_delay_seconds", "45", db_path=target_db))
            if min_delay < 0: min_delay = 5.0
            if max_delay < min_delay: max_delay = min_delay + 5.0
        except Exception:
            min_delay, max_delay = 20.0, 45.0

        logger.info(f"Found {count} approved email(s) due at {now_local_str}. Dispatch Engine: '{dispatch_method}'.")

        dispatched_count = 0
        for idx, email_rec in enumerate(due_emails):
            # Evaluate individual recipient's market window if adaptive mode
            email_in_window, email_window_msg = is_within_sending_window(
                email_record=email_rec,
                db_path=target_db
            )
            if not email_in_window and not dry_run:
                logger.info(f"[Scheduler] Postponing Email ID #{email_rec['id']} for '{email_rec.get('recipient')}': {email_window_msg}")
                continue

            if dispatch_method == "hostinger_smtp":
                dispatch_email_hostinger(email_rec, dry_run=dry_run, db_path=target_db)
            else:
                dispatch_email_outlook(email_rec, dry_run=dry_run, db_path=target_db)

            dispatched_count += 1

            # Apply randomized anti-spam delay between emails if more than one
            if idx < count - 1 and not dry_run:
                delay = random.uniform(min_delay, max_delay)
                logger.info(f"Enforcing human-like anti-spam delay of {delay:.1f}s before next email...")
                time.sleep(delay)
        return dispatched_count
    else:
        logger.debug(f"Heartbeat: No due approved emails at Local Time {now_local_str}.")
        return 0

def start_scheduler_loop(interval: int = 15, stop_event=None):
    """
    Run the scheduler loop continuously. Used by launcher.py to run the
    scheduler as a background thread within the unified desktop app.
    """
    init_db()
    try:
        start_tracking_server(port=8502)
    except Exception as t_err:
        logger.warning(f"Could not auto-start open tracking server: {t_err}")

    logger.info("Background Email Dispatch & Open Tracking Scheduler loop started.")
    cycle_counter = 0

    while stop_event is None or not stop_event.is_set():
        cycle_counter += 1
        try:
            run_scheduler_cycle(dry_run=False)
        except Exception as cycle_err:
            logger.error(f"Unexpected error in scheduler cycle: {cycle_err}")

        # Periodically scan Hostinger IMAP for NDR bounces & prospect replies (e.g. every 10 cycles)
        if cycle_counter % 10 == 0:
            try:
                logger.info("Running scheduled Hostinger inbox scan (bounces & prospect replies)...")
                inbox_stats = scan_all_hostinger_inbox()
                b_cnt = inbox_stats.get("total_bounces", 0)
                r_cnt = inbox_stats.get("total_replies", 0)
                if b_cnt > 0 or r_cnt > 0:
                    logger.info(f"[Scheduler] IMAP scan detected {b_cnt} bounce(s) and {r_cnt} prospect reply/replies.")
            except Exception as scan_err:
                logger.debug(f"Periodic inbox check skipped: {scan_err}")

        # Sleep in increments of 1 second for responsive shutdown
        slept = 0
        while slept < interval and (stop_event is None or not stop_event.is_set()):
            time.sleep(1)
            slept += 1

def main():
    parser = argparse.ArgumentParser(description="Standalone Outlook Email Dispatch Scheduler")
    parser.add_argument("--interval", type=int, default=15, help="Polling interval in seconds (default: 15)")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without sending real Outlook emails")
    args = parser.parse_args()

    # Ensure DB exists
    init_db()

    logger.info("==================================================")
    logger.info("Email Automation Scheduler Service Initialized")
    logger.info(f"Local System Time: {get_local_system_time_str()}")
    logger.info(f"Polling Interval: {args.interval} seconds")
    logger.info(f"Dry Run Mode: {args.dry_run}")
    logger.info("==================================================")

    if args.once:
        run_scheduler_cycle(dry_run=args.dry_run)
        logger.info("Single run completed.")
        return

    try:
        while True:
            try:
                run_scheduler_cycle(dry_run=args.dry_run)
            except Exception as cycle_err:
                logger.error(f"Unexpected error in scheduler cycle: {cycle_err}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")

if __name__ == "__main__":
    main()
