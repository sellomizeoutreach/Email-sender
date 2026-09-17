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
from datetime import datetime
from typing import Optional

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
    mark_email_sent,
    mark_email_error,
    update_email,
    get_next_available_smtp_account,
    increment_smtp_sent,
    advance_contact_followup,
    is_within_sending_window,
    init_db,
    DB_FILE
)
from smtp_dispatcher import send_smtp_email, scan_all_hostinger_bounces, scan_all_hostinger_inbox
from tracker import inject_tracking_pixel, inject_tracking_and_links, start_tracking_server

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Scheduler] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scheduler")

def get_local_system_time_str() -> str:
    """
    Return current time in Local System Time formatted strictly as YYYY-MM-DD HH:MM:SS.
    Standardized with app.py to prevent UTC mismatches.
    """
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

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

def dispatch_email_hostinger(email_record: dict, dry_run: bool = False):
    """
    Dispatch a single approved email record through Hostinger Direct SMTP.
    Rotates through active Hostinger SMTP accounts, respects daily limits,
    attaches HTML body and signature, and sends via SSL/TLS.
    """
    email_id = email_record["id"]
    recipient = email_record.get("recipient", "").strip()
    subject = email_record.get("subject", "Listing Audit").strip()
    approved_email_html = email_record.get("email_html", "").strip()

    logger.info(f"[Hostinger SMTP] Processing Email ID #{email_id} for recipient '{recipient}'...")

    if not recipient:
        err_msg = "Recipient email address is missing or empty."
        logger.warning(f"Email ID #{email_id}: {err_msg}")
        mark_email_error(email_id, status="Error", error_message=err_msg)
        return

    # Fetch next active Hostinger account in rotation
    smtp_account = get_next_available_smtp_account()
    if not smtp_account:
        err_msg = "No active Hostinger SMTP account available (or all configured accounts have reached their daily sending limit)."
        logger.warning(f"Email ID #{email_id}: {err_msg}")
        mark_email_error(email_id, status="Error", error_message=err_msg)
        return

    # Prepare signature, tracking pixel, and payload
    signature_html = (get_config("signature_html") or "").strip()
    bcc_address = (get_config("bcc_email") or "").strip()
    combined_body = f"{approved_email_html}<br><br>{signature_html}" if signature_html else approved_email_html
    final_payload = inject_tracking_and_links(combined_body, email_id)

    if dry_run:
        logger.info(f"[DRY RUN Hostinger SMTP] Would send Email ID #{email_id} to '{recipient}' from '{smtp_account['email']}' via Hostinger.")
        mark_email_sent(email_id)
        advance_contact_followup(recipient, delay_days=4)
        return

    success, msg = send_smtp_email(
        smtp_account=smtp_account,
        recipient=recipient,
        subject=subject,
        html_content=final_payload,
        bcc_email=bcc_address
    )

    if success:
        mark_email_sent(email_id)
        increment_smtp_sent(smtp_account["id"])
        update_email(
            email_id=email_id,
            sent_via=f"Hostinger ({smtp_account['email']})",
            smtp_account_id=smtp_account["id"]
        )
        # Advance contact outreach status, date, and next follow-up
        advance_contact_followup(recipient, delay_days=4)
        logger.info(f"Successfully dispatched Email ID #{email_id} to '{recipient}' via Hostinger account '{smtp_account['email']}'.")
    else:
        mark_email_error(email_id, status="Error", error_message=msg)

def dispatch_email_outlook(email_record: dict, dry_run: bool = False):
    """
    Dispatch a single approved email record through Outlook.
    Handles COM thread safety, account verification, HTML concatenation with signature, BCC, and status updates.
    """
    email_id = email_record["id"]
    recipient = email_record.get("recipient", "").strip()
    subject = email_record.get("subject", "Listing Audit").strip()
    approved_email_html = email_record.get("email_html", "").strip()

    logger.info(f"[Outlook] Processing Email ID #{email_id} for recipient '{recipient}'...")

    if not recipient:
        err_msg = "Recipient email address is missing or empty."
        logger.warning(f"Email ID #{email_id}: {err_msg}")
        mark_email_error(email_id, status="Error", error_message=err_msg)
        return

    # Fetch configuration
    designated_sender = (get_config("sender_email") or "").strip()
    bcc_address = (get_config("bcc_email") or "").strip()
    signature_html = (get_config("signature_html") or "").strip()

    if dry_run:
        logger.info(f"[DRY RUN Outlook] Would send Email ID #{email_id} to '{recipient}' from '{designated_sender}' with BCC '{bcc_address}'")
        mark_email_sent(email_id)
        return

    # 1. Connect to Outlook with explicit COM initialization
    try:
        outlook_app = get_outlook_application()
    except Exception as e:
        mark_email_error(email_id, status="Error", error_message=f"Outlook COM connection failed: {e}")
        return

    try:
        # 2. Match designated Sender Email in Outlook Accounts
        if designated_sender:
            matching_account = find_matching_account(outlook_app, designated_sender)
            if not matching_account:
                err_msg = f"Account Mismatch: No Outlook account with SmtpAddress matching '{designated_sender}' found."
                logger.error(f"Email ID #{email_id}: {err_msg}")
                mark_email_error(email_id, status="Account Mismatch", error_message=err_msg)
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
        if signature_html:
            combined_body = f"{approved_email_html}<br><br>{signature_html}"
        else:
            combined_body = approved_email_html
        final_payload = inject_tracking_and_links(combined_body, email_id)

        # Assign directly to HTMLBody
        mail.HTMLBody = final_payload

        # Apply matching account if configured
        if matching_account:
            assign_sender_account(mail, matching_account)

        # Dispatch
        mail.Send()
        logger.info(f"Successfully dispatched Email ID #{email_id} to '{recipient}'.")
        mark_email_sent(email_id)
        advance_contact_followup(recipient, delay_days=4)

    except Exception as dispatch_err:
        logger.error(f"Error dispatching Email ID #{email_id}: {dispatch_err}")
        mark_email_error(email_id, status="Error", error_message=str(dispatch_err))
    finally:
        # Clean up COM references on this cycle
        if COM_AVAILABLE and pythoncom:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

# Backward compatibility alias
dispatch_email = dispatch_email_outlook

def run_scheduler_cycle(dry_run: bool = False, db_path: Optional[str] = None) -> int:
    """
    Check database for due approved emails and dispatch them.
    Explicitly enforces comparison against Local System Time (YYYY-MM-DD HH:MM:SS)
    and validates whether current time falls within allowed business sending days & hours.
    """
    target_db = db_path or DB_FILE
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

        for idx, email_rec in enumerate(due_emails):
            if dispatch_method == "hostinger_smtp":
                dispatch_email_hostinger(email_rec, dry_run=dry_run)
            else:
                dispatch_email_outlook(email_rec, dry_run=dry_run)

            # Apply randomized anti-spam delay between emails if more than one
            if idx < count - 1 and not dry_run:
                delay = random.uniform(min_delay, max_delay)
                logger.info(f"Enforcing human-like anti-spam delay of {delay:.1f}s before next email...")
                time.sleep(delay)
    else:
        logger.debug(f"Heartbeat: No due approved emails at Local Time {now_local_str}.")

    return count

def start_scheduler_loop(interval: int = 60, stop_event=None):
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

        # Periodically scan Hostinger IMAP for NDR bounces & prospect replies (e.g. every 10 cycles ~ 10 mins)
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
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
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
