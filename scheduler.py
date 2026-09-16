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
    init_db
)

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

def dispatch_email(email_record: dict, dry_run: bool = False):
    """
    Dispatch a single approved email record through Outlook.
    Handles COM thread safety, account verification, HTML concatenation with signature, BCC, and status updates.
    """
    email_id = email_record["id"]
    recipient = email_record.get("recipient", "").strip()
    subject = email_record.get("subject", "Listing Audit").strip()
    approved_email_html = email_record.get("email_html", "").strip()

    logger.info(f"Processing Email ID #{email_id} for recipient '{recipient}'...")

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
        logger.info(f"[DRY RUN] Would send Email ID #{email_id} to '{recipient}' from '{designated_sender}' with BCC '{bcc_address}'")
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

        # Concatenate approved HTML body with signature HTML
        if signature_html:
            final_payload = f"{approved_email_html}<br><br>{signature_html}"
        else:
            final_payload = approved_email_html

        # Assign directly to HTMLBody
        mail.HTMLBody = final_payload

        # Apply matching account if configured
        if matching_account:
            assign_sender_account(mail, matching_account)

        # Dispatch
        mail.Send()
        logger.info(f"Successfully dispatched Email ID #{email_id} to '{recipient}'.")
        mark_email_sent(email_id)

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

def run_scheduler_cycle(dry_run: bool = False) -> int:
    """
    Check database for due approved emails and dispatch them.
    Explicitly enforces comparison against Local System Time (YYYY-MM-DD HH:MM:SS).
    Returns count processed.
    """
    now_local_str = get_local_system_time_str()
    due_emails = get_approved_due_emails(now_local_str)
    count = len(due_emails)

    if count > 0:
        logger.info(f"Found {count} approved email(s) scheduled on or before Local Time {now_local_str}.")
        for email_rec in due_emails:
            dispatch_email(email_rec, dry_run=dry_run)
    else:
        logger.debug(f"Heartbeat: No due approved emails at Local Time {now_local_str}.")

    return count

def start_scheduler_loop(interval: int = 60, stop_event=None):
    """
    Run the scheduler loop continuously. Used by launcher.py to run the
    scheduler as a background thread within the unified desktop app.
    """
    init_db()
    logger.info("Background Outlook Dispatch Scheduler loop started.")
    while stop_event is None or not stop_event.is_set():
        try:
            run_scheduler_cycle(dry_run=False)
        except Exception as cycle_err:
            logger.error(f"Unexpected error in scheduler cycle: {cycle_err}")

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
