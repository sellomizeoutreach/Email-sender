"""
smtp_dispatcher.py - Core SMTP Dispatcher for Hostinger and custom mail servers.
Supports SSL (Port 465) and STARTTLS (Port 587), MIME-compliant multipart HTML,
connection testing, and error recovery.
"""

import smtplib
try:
    import imaplib
except ImportError:
    imaplib = None
import email
import ssl
import re
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger("smtp_dispatcher")

def html_to_plain_text(html_content: str) -> str:
    """Convert HTML content into clean plain text for multipart emails."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")
        for br in soup.find_all(["br"]):
            br.replace_with("\n")
        for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
            block.append("\n")
        text = soup.get_text()
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(l for l in lines if l)
    except Exception:
        # Fallback regex strip
        text = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(l for l in lines if l)

def test_smtp_connection(
    smtp_host: str = "",
    smtp_port: int = 465,
    email: str = "",
    password: str = "",
    timeout: int = 12,
    **kwargs
) -> Tuple[bool, str]:
    """
    Test credentials and SSL/TLS handshake with Hostinger or any SMTP server.
    Flexible signature accepting smtp_host/host, smtp_port/port, email/username.
    Returns (True, success_msg) or (False, error_msg).
    """
    host = (smtp_host or kwargs.get("host") or "").strip()
    port = int(kwargs.get("port") or smtp_port or 465)
    user = (email or kwargs.get("username") or "").strip()
    pwd = (password or kwargs.get("password") or "").strip()

    if not host or not user or not pwd:
        return False, "Host, email address, and password are required."

    context = ssl.create_default_context()

    try:
        if port == 465:
            # SSL Connection (Standard for Hostinger)
            with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
                server.login(user, pwd)
                return True, f"Authentication successful on {host}:{port} via SSL!"
        else:
            # TLS / STARTTLS Connection (Port 587 or 25)
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(user, pwd)
                return True, f"Authentication successful on {host}:{port} via STARTTLS!"
    except smtplib.SMTPAuthenticationError as auth_err:
        logger.warning(f"SMTP authentication failed for {user}@{host}: {auth_err}")
        return False, f"Authentication failed: Check your email and password. ({auth_err.smtp_error.decode(errors='ignore') if hasattr(auth_err, 'smtp_error') else auth_err})"
    except smtplib.SMTPConnectError as conn_err:
        logger.error(f"Failed to connect to {host}:{port}: {conn_err}")
        return False, f"Could not connect to {host}:{port}. Check host and port."
    except Exception as e:
        logger.error(f"SMTP error during test connection to {host}: {e}")
        return False, f"Connection failed: {str(e)}"

def send_smtp_email(
    smtp_account: Dict[str, Any],
    recipient: str,
    subject: str,
    html_content: str,
    bcc_email: Optional[str] = None,
    timeout: int = 20
) -> Tuple[bool, str]:
    """
    Dispatch an email through an active Hostinger SMTP account.
    Constructs multipart (plain text + HTML) for maximum deliverability.
    """
    host = smtp_account.get("smtp_host", "smtp.hostinger.com").strip()
    port = int(smtp_account.get("smtp_port", 465))
    user = smtp_account["email"].strip()
    pwd = smtp_account["password"].strip()
    sender_name = smtp_account.get("sender_name", "").strip() or user.split("@")[0]

    if not recipient or not recipient.strip():
        return False, "Recipient email is missing."

    target_recipient = recipient.strip()
    domain = user.split("@")[-1] if "@" in user else "sellomize.com"

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject.strip()
    msg["From"] = formataddr((sender_name, user))
    msg["To"] = target_recipient
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)

    # Attach plain text version
    plain_text = html_to_plain_text(html_content)
    part_text = MIMEText(plain_text, "plain", "utf-8")
    msg.attach(part_text)

    # Attach HTML version
    part_html = MIMEText(html_content, "html", "utf-8")
    msg.attach(part_html)

    # Build recipient list including optional BCC
    destinations = [target_recipient]
    if bcc_email and bcc_email.strip():
        bcc_clean = bcc_email.strip()
        msg["Bcc"] = bcc_clean
        destinations.append(bcc_clean)

    context = ssl.create_default_context()

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
                server.login(user, pwd)
                server.send_message(msg, from_addr=user, to_addrs=destinations)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(user, pwd)
                server.send_message(msg, from_addr=user, to_addrs=destinations)

        logger.info(f"Successfully sent email to '{target_recipient}' via Hostinger account '{user}'.")
        return True, f"Sent via Hostinger SMTP ({user})"
    except smtplib.SMTPAuthenticationError as auth_err:
        err_msg = f"SMTP Authentication error for {user}: {auth_err}"
        logger.error(err_msg)
        return False, err_msg
    except Exception as send_err:
        err_msg = f"SMTP Dispatch failed via {user}: {send_err}"
        logger.error(err_msg)
        return False, err_msg

def extract_bounced_info_from_msg(msg) -> Tuple[Optional[str], str]:
    """
    Extract failed recipient email address and diagnostic reason from an NDR message.
    """
    failed_email = None
    reason = "Delivery failed / NDR"

    # 1. Check direct headers
    if msg.get("X-Failed-Recipients"):
        failed_email = msg.get("X-Failed-Recipients").strip()

    # 2. Walk MIME parts
    body_text = ""
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type == "message/delivery-status":
            status_payload = part.get_payload()
            if isinstance(status_payload, list):
                for subpart in status_payload:
                    sub_text = str(subpart)
                    match_rec = re.search(r"Final-Recipient:\s*(?:rfc822;)?\s*([^\s;<>]+@[^\s;<>]+)", sub_text, re.I)
                    if match_rec:
                        failed_email = match_rec.group(1).strip()
                    match_diag = re.search(r"Diagnostic-Code:\s*(.+)", sub_text, re.I)
                    if match_diag:
                        reason = match_diag.group(1).strip()
            elif isinstance(status_payload, str):
                match_rec = re.search(r"Final-Recipient:\s*(?:rfc822;)?\s*([^\s;<>]+@[^\s;<>]+)", status_payload, re.I)
                if match_rec:
                    failed_email = match_rec.group(1).strip()
                match_diag = re.search(r"Diagnostic-Code:\s*(.+)", status_payload, re.I)
                if match_diag:
                    reason = match_diag.group(1).strip()
        elif content_type in ["text/plain", "text/html"]:
            try:
                payload_bytes = part.get_payload(decode=True)
                if payload_bytes:
                    body_text += " " + payload_bytes.decode("utf-8", errors="ignore")
                else:
                    raw_str = part.get_payload()
                    if isinstance(raw_str, str):
                        body_text += " " + raw_str
            except Exception:
                pass

    if not failed_email and body_text:
        patterns = [
            r"Final-Recipient:\s*(?:rfc822;)?\s*<?([^\s;<>]+@[^\s;<>]+)>?",
            r"Original-Recipient:\s*(?:rfc822;)?\s*<?([^\s;<>]+@[^\s;<>]+)>?",
            r"failed(?:\s+to\s+deliver)?\s+to\s+<?([^\s;<>]+@[^\s;<>]+)>?",
            r"<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>:",
            r"to:\s*<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>"
        ]
        for pat in patterns:
            m = re.search(pat, body_text, re.I)
            if m:
                failed_email = m.group(1).strip()
                break

    if body_text and reason == "Delivery failed / NDR":
        diag_match = re.search(r"(55[0-9]\s+[0-9.]+\s+[^.\n\r]+)", body_text)
        if diag_match:
            reason = diag_match.group(1).strip()

    return failed_email, reason

def scan_hostinger_inbox(
    smtp_account: Dict[str, Any],
    imap_host: str = "imap.hostinger.com",
    imap_port: int = 993,
    max_emails: int = 50,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Single-pass high-performance IMAP scanner for Hostinger mailboxes:
    1. Connects via IMAP SSL (:993) with readonly=True.
    2. Streams message headers using BODY.PEEK[HEADER.FIELDS] for fast execution.
    3. Detects NDR bounce notices and flags bounces in SQLite.
    4. Detects incoming replies from CRM prospects, marks contact as 'Replied',
       and automatically cancels pending/approved follow-up emails in Outbox.
    Returns: {"bounces": [...], "replies": [...], "scanned_count": N, "mailbox": user}
    """
    from database import (
        record_email_bounce,
        record_email_reply,
        get_connection,
        DB_FILE
    )
    target_db = db_path or DB_FILE

    user = smtp_account.get("email", "").strip()
    pwd = smtp_account.get("password", "").strip()
    host = smtp_account.get("imap_host") or imap_host

    result = {
        "bounces": [],
        "replies": [],
        "scanned_count": 0,
        "mailbox": user
    }

    if imaplib is None:
        logger.warning("imaplib standard library module is not available; IMAP inbox scanning skipped.")
        return result

    if not user or not pwd:
        return result

    # Preload target CRM and outreach recipient emails into a set for O(1) matching
    target_lead_emails = set()
    try:
        conn = get_connection(target_db)
        cursor = conn.cursor()
        cursor.execute("SELECT LOWER(TRIM(email)) as em FROM contacts WHERE email IS NOT NULL AND email != ''")
        for r in cursor.fetchall():
            if r["em"]:
                target_lead_emails.add(r["em"])
        cursor.execute("SELECT DISTINCT LOWER(TRIM(recipient)) as em FROM emails WHERE recipient IS NOT NULL AND recipient != ''")
        for r in cursor.fetchall():
            if r["em"]:
                target_lead_emails.add(r["em"])
        conn.close()
    except Exception as db_err:
        logger.warning(f"Could not load target lead emails for reply matching: {db_err}")

    mail = None
    try:
        context = ssl.create_default_context()
        mail = imaplib.IMAP4_SSL(host, imap_port, ssl_context=context)
        mail.login(user, pwd)
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            return result

        # Fetch recent message IDs
        status, msg_ids = mail.search(None, "ALL")
        id_list = msg_ids[0].split() if (status == "OK" and msg_ids and msg_ids[0]) else []
        recent_ids = id_list[-max_emails:] if len(id_list) > max_emails else id_list
        result["scanned_count"] = len(recent_ids)

        for m_id in recent_ids:
            # 1. Fetch lightweight headers only (BODY.PEEK avoids marking email as read)
            res, header_data = mail.fetch(m_id, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])")
            if res != "OK" or not header_data or not header_data[0]:
                continue

            raw_header = header_data[0][1]
            if not isinstance(raw_header, bytes):
                continue

            header_msg = email.message_from_bytes(raw_header)
            from_raw = header_msg.get("From", "")
            subject = header_msg.get("Subject", "")
            date_str = header_msg.get("Date", "")

            # Extract clean email address from From header
            _, parsed_email = email.utils.parseaddr(from_raw)
            clean_from = parsed_email.strip().lower()

            # Check if this is an NDR bounce
            is_bounce_notice = (
                "mailer-daemon" in clean_from
                or "postmaster" in clean_from
                or any(w in (subject or "").lower() for w in ["delivery status", "undeliverable", "failure notice", "returned mail"])
            )

            if is_bounce_notice:
                # Fetch full RFC822 message to parse bounce diagnostics
                b_res, b_data = mail.fetch(m_id, "(RFC822)")
                if b_res == "OK" and b_data and b_data[0] and isinstance(b_data[0][1], bytes):
                    full_msg = email.message_from_bytes(b_data[0][1])
                    failed_email, reason = extract_bounced_info_from_msg(full_msg)
                    if failed_email and "@" in failed_email and failed_email.lower() != user.lower():
                        record_email_bounce(failed_email, bounce_reason=reason, db_path=target_db)
                        result["bounces"].append({
                            "email": failed_email,
                            "reason": reason,
                            "mailbox": user
                        })
                continue

            # Check if this is an incoming reply from a known CRM lead
            if clean_from and clean_from != user.lower() and clean_from in target_lead_emails:
                reply_info = record_email_reply(
                    sender_email=clean_from,
                    reply_subject=subject,
                    received_at=date_str,
                    db_path=target_db
                )
                result["replies"].append({
                    "email": clean_from,
                    "subject": subject,
                    "mailbox": user,
                    "cancelled_followups": reply_info.get("cancelled_drafts_count", 0)
                })
                logger.info(f"[Reply Detector] Detected reply from {clean_from}! Subject: '{subject}'. Auto-cancelled {reply_info.get('cancelled_drafts_count', 0)} follow-up draft(s).")

        logger.info(f"Scanned {len(recent_ids)} message header(s) for {user}; detected {len(result['bounces'])} bounce(s), {len(result['replies'])} reply/replies.")
    except Exception as e:
        logger.warning(f"Error scanning IMAP inbox for {user}: {e}")
    finally:
        if mail:
            try:
                mail.close()
            except Exception:
                pass
            try:
                mail.logout()
            except Exception:
                pass

    return result

def scan_all_hostinger_inbox(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Scan all active Hostinger mailboxes in a single pass for bounces and prospect replies.
    """
    from database import get_smtp_accounts, DB_FILE
    target_db = db_path or DB_FILE
    accounts = get_smtp_accounts(active_only=True, db_path=target_db)

    all_bounces = []
    all_replies = []
    total_scanned = 0

    for acc in accounts:
        inbox_result = scan_hostinger_inbox(acc, db_path=target_db)
        all_bounces.extend(inbox_result.get("bounces", []))
        all_replies.extend(inbox_result.get("replies", []))
        total_scanned += inbox_result.get("scanned_count", 0)

    return {
        "bounces": all_bounces,
        "replies": all_replies,
        "total_bounces": len(all_bounces),
        "total_replies": len(all_replies),
        "total_scanned": total_scanned,
        "accounts_scanned": len(accounts)
    }

def scan_hostinger_bounces(
    smtp_account: Dict[str, Any],
    imap_host: str = "imap.hostinger.com",
    imap_port: int = 993,
    max_emails: int = 40,
    db_path: Optional[str] = None
) -> List[Dict[str, str]]:
    """Backward-compatible wrapper for single-mailbox bounce scan."""
    res = scan_hostinger_inbox(smtp_account, imap_host=imap_host, imap_port=imap_port, max_emails=max_emails, db_path=db_path)
    return res.get("bounces", [])

def scan_all_hostinger_bounces(db_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Backward-compatible wrapper for all-mailboxes bounce scan."""
    res = scan_all_hostinger_inbox(db_path=db_path)
    return res.get("bounces", [])