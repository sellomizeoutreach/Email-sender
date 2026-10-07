"""
worker.py - Standalone DB-backed Delivery & Queue Worker for Sellomize Reach.

Spec Section 2 & 9:
- Storage: S3-compatible object storage or local assets
- Metadata: DB-backed job queue `send_jobs` + dedicated worker owns scheduled sends.
- Polling: Polls send_jobs for status='scheduled' AND send_at <= now().
- Sends via Hostinger SMTP (rotating accounts and daily limits).
- Idempotency: Unique idempotency_key prevents duplicate sends across retries or double triggers.
- Bounce handling: Marks status='bounced' + human-readable last_error on SMTP bounce responses.
- Google Sheets writeback: On scheduled/sent, synchronizes status and dates to Google Sheets.
- Keeps system heartbeat updated in system_config.
"""

import sys
import os
import time
import re
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from database import (
    get_due_send_jobs,
    claim_send_job_for_sending,
    mark_send_job_sent,
    mark_send_job_failed,
    get_send_job_by_id,
    get_config,
    set_config,
    get_next_available_smtp_account,
    increment_smtp_sent,
    record_email_bounce,
    advance_contact_followup,
    get_contact_by_email,
    init_db,
    DB_FILE,
    get_log_file_path
)
from smtp_dispatcher import send_smtp_email, sanitize_header, scan_all_hostinger_bounces
from tracker import wrap_links_with_click_tracking, inject_tracking_pixel
from mx_checker import verify_email_domain_mx
from timezone_helper import get_engine_now
from sheets_sync import sync_lead_sent_to_sheets
from template_engine import deduplicate_email_signature


# Configure logging
handlers = [logging.StreamHandler(sys.stdout)]
try:
    file_handler = logging.FileHandler(get_log_file_path(), mode="a", encoding="utf-8")
    handlers.append(file_handler)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Worker] %(message)s",
    handlers=handlers
)
logger = logging.getLogger("worker")


def execute_send_job(job: Dict[str, Any], dry_run: bool = False, db_path: str = DB_FILE) -> bool:
    """
    Execute a single send_job.
    1. Atomically claims job (status: 'sending')
    2. Performs pre-flight DNC check and MX verification
    3. Fetches rotating SMTP account with available quota
    4. Delivers email with inline CID images and plain-text fallback via send_smtp_email
    5. On success: marks 'sent', increments daily counter, advances follow-up, triggers Sheets writeback
    6. On failure/bounce: marks 'bounced' or 'failed' with descriptive last_error
    """
    job_id = job["id"]

    # Atomically claim to prevent double-execution
    if not claim_send_job_for_sending(job_id, db_path=db_path):
        logger.info(f"Send job {job_id} already claimed by another worker. Skipping.")
        return False

    to_addrs: List[str] = job.get("to_addrs") or []
    if isinstance(to_addrs, str):
        to_addrs = [a.strip() for a in re.split(r'[,;]+', to_addrs) if a.strip()]

    if not to_addrs:
        err = "Job has no recipient email addresses."
        mark_send_job_failed(job_id, err, db_path=db_path)
        return False

    primary_recipient = to_addrs[0].strip()
    recipient_header = ", ".join(to_addrs)
    subject = sanitize_header(job.get("subject", ""))
    body_html = job.get("body_html", "")
    lead_id = job.get("lead_id", "")
    bcc_addrs = job.get("bcc_addrs") or []
    bcc_str = ", ".join(bcc_addrs) if isinstance(bcc_addrs, list) else str(bcc_addrs or "")

    # 1. Pre-flight DNC / Opt-Out compliance check
    for r in to_addrs:
        contact_rec = get_contact_by_email(r, db_path=db_path)
        if contact_rec and (contact_rec.get("status") in ["Do Not Contact", "unsubscribed", "Unsubscribed"]):
            suppress_msg = f"Suppressed (CAN-SPAM/DNC): Recipient '{r}' is marked as 'Do Not Contact'."
            logger.info(f"Send job {job_id}: {suppress_msg}")
            mark_send_job_failed(job_id, suppress_msg, db_path=db_path)
            return False

    # 2. Pre-flight MX record check
    enforce_mx = (get_config("enforce_mx_check", "true", db_path=db_path) or "true").strip().lower() == "true"
    if enforce_mx:
        for r in to_addrs:
            is_valid, mx_reason, _ = verify_email_domain_mx(r)
            if not is_valid:
                bounce_err = f"Pre-flight MX verification failed for '{r}': {mx_reason}"
                logger.warning(f"Send job {job_id}: {bounce_err}")
                mark_send_job_failed(job_id, bounce_err, is_bounce=True, db_path=db_path)
                record_email_bounce(recipient_email=r, bounce_reason=bounce_err, db_path=db_path)
                return False

    # 3. Fetch active Hostinger SMTP account with available quota
    smtp_account = get_next_available_smtp_account(db_path=db_path)
    if not smtp_account:
        err_msg = "No active Hostinger SMTP account available or daily quota exhausted."
        logger.warning(f"Send job {job_id}: {err_msg}")
        mark_send_job_failed(job_id, err_msg, db_path=db_path)
        return False

    # 4. Attach signature if configured (universally deduplicated)
    sig_html = (get_config("signature_html", db_path=db_path) or "").strip()
    combined_body = deduplicate_email_signature(
        body_html,
        signature_html=sig_html,
        include_signature=True
    )


    # Dry-run bypass
    if dry_run:
        logger.info(f"[DRY RUN] Send job {job_id} would dispatch to {recipient_header} via {smtp_account['email']}")
        mark_send_job_sent(job_id, db_path=db_path)
        return True

    # 5. Dispatch via Hostinger SMTP (with inline CID & alt text plain text fallback)
    msg_id_tracker = []
    success, resp_msg = send_smtp_email(
        smtp_account=smtp_account,
        recipient=recipient_header,
        subject=subject,
        html_content=combined_body,
        bcc_email=bcc_str,
        message_id_out=msg_id_tracker
    )

    if success:
        now_dt = datetime.now()
        mark_send_job_sent(job_id, sent_at=now_dt, db_path=db_path)
        increment_smtp_sent(smtp_account["id"], db_path=db_path)

        # Advance CRM follow-up and contact date
        fu_delay = int(get_config("followup_delay_days", "4", db_path=db_path) or 4)
        for r in to_addrs:
            advance_contact_followup(r, delay_days=fu_delay, db_path=db_path)

        # Google Sheets writeback
        try:
            sync_lead_sent_to_sheets(
                lead_email=primary_recipient,
                lead_id=lead_id,
                status="Emailed",
                contacted="Yes",
                sent_at=now_dt,
                follow_up_step=1
            )
        except Exception as sheet_err:
            logger.warning(f"Sheets writeback error for job {job_id}: {sheet_err}")

        logger.info(f"Successfully dispatched send_job {job_id} to '{recipient_header}' via '{smtp_account['email']}'.")
        return True
    else:
        is_bounce = False
        lower_resp = (resp_msg or "").lower()
        if any(w in lower_resp for w in ("550", "551", "552", "553", "554", "user unknown", "mailbox unavailable", "rejected", "does not exist")):
            is_bounce = True
            for r in to_addrs:
                record_email_bounce(recipient_email=r, bounce_reason=resp_msg, db_path=db_path)

        logger.error(f"Failed to dispatch send_job {job_id}: {resp_msg}")
        mark_send_job_failed(job_id, resp_msg, is_bounce=is_bounce, db_path=db_path)
        return False


def run_worker_cycle(dry_run: bool = False, db_path: str = DB_FILE) -> int:
    """
    Run a single polling cycle across send_jobs:
    - Checks due scheduled jobs (send_at <= now)
    - Dispatches due jobs with jitter delay
    - Records heartbeat in system_config
    Returns count of successfully processed jobs.
    """
    now_engine = get_engine_now()
    now_str = now_engine.strftime("%Y-%m-%d %H:%M:%S")

    # Update heartbeat
    try:
        set_config("worker_heartbeat", now_str, db_path=db_path)
    except Exception:
        pass

    due_jobs = get_due_send_jobs(cutoff_dt=now_engine, limit=20, db_path=db_path)
    if not due_jobs:
        return 0

    logger.info(f"Worker cycle: found {len(due_jobs)} due send job(s) ready for dispatch.")
    sent_count = 0

    min_delay = int(get_config("min_delay_seconds", "20", db_path=db_path) or 20)
    max_delay = int(get_config("max_delay_seconds", "45", db_path=db_path) or 45)

    for i, job in enumerate(due_jobs):
        ok = execute_send_job(job, dry_run=dry_run, db_path=db_path)
        if ok:
            sent_count += 1

        # Inter-email jitter pacing
        if i < len(due_jobs) - 1 and not dry_run:
            import random
            delay = random.uniform(min_delay, max_delay)
            logger.info(f"Pacing delay: sleeping {delay:.1f}s before next send job...")
            time.sleep(delay)

    return sent_count


def run_worker_daemon(interval: int = 60, dry_run: bool = False, db_path: str = DB_FILE):
    """Run persistent polling worker loop indefinitely."""
    logger.info(f"Starting Sellomize Reach Delivery Worker daemon (interval: {interval}s, db: {db_path})...")
    init_db(db_path)

    bounce_scan_interval = 600  # Scan IMAP bounce inbox every 10 mins
    last_bounce_scan = 0

    while True:
        try:
            sent = run_worker_cycle(dry_run=dry_run, db_path=db_path)
            if sent > 0:
                logger.info(f"Worker cycle dispatched {sent} send job(s).")

            now_ts = time.time()
            if now_ts - last_bounce_scan > bounce_scan_interval:
                try:
                    scan_all_hostinger_bounces()
                    last_bounce_scan = now_ts
                except Exception as b_err:
                    logger.warning(f"IMAP bounce scan check error: {b_err}")

        except KeyboardInterrupt:
            logger.info("Worker stopped by user.")
            break
        except Exception as e:
            logger.error(f"Worker cycle uncaught exception: {e}", exc_info=True)

        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sellomize Reach Standalone Delivery Worker")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    parser.add_argument("--dry-run", action="store_true", help="Run without actually sending emails")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    parser.add_argument("--db", type=str, default=DB_FILE, help="Path to SQLite database file")
    args = parser.parse_args()

    if args.once:
        dispatched = run_worker_cycle(dry_run=args.dry_run, db_path=args.db)
        print(f"Dispatched {dispatched} send job(s).")
    else:
        run_worker_daemon(interval=args.interval, dry_run=args.dry_run, db_path=args.db)
