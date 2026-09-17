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

def scan_hostinger_bounces(
    smtp_account: Dict[str, Any],
    imap_host: str = "imap.hostinger.com",
    imap_port: int = 993,
    max_emails: int = 40,
    db_path: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Connect to Hostinger IMAP on port 993 SSL, search for NDR/bounce notices in INBOX,
    extract failed recipient emails and reasons, and mark them as bounced in SQLite.
    """
    from database import record_email_bounce, DB_FILE
    target_db = db_path or DB_FILE

    user = smtp_account.get("email", "").strip()
    pwd = smtp_account.get("password", "").strip()
    host = smtp_account.get("imap_host") or imap_host

    if imaplib is None:
        logger.warning("imaplib standard library module is not available; IMAP bounce scanning skipped.")
        return []

    if not user or not pwd:
        return []

    detected_bounces = []
    mail = None
    try:
        context = ssl.create_default_context()
        mail = imaplib.IMAP4_SSL(host, imap_port, ssl_context=context)
        mail.login(user, pwd)
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            return []

        search_criteria = '(OR (FROM "MAILER-DAEMON") (FROM "postmaster"))'
        status, msg_ids = mail.search(None, search_criteria)
        id_list = msg_ids[0].split() if (status == "OK" and msg_ids and msg_ids[0]) else []

        if not id_list:
            status, msg_ids2 = mail.search(None, '(OR (SUBJECT "Delivery Status") (SUBJECT "Undelivered"))')
            if status == "OK" and msg_ids2 and msg_ids2[0]:
                id_list = msg_ids2[0].split()

        recent_ids = id_list[-max_emails:] if len(id_list) > max_emails else id_list

        for m_id in recent_ids:
            res, data = mail.fetch(m_id, "(RFC822)")
            if res != "OK" or not data or not data[0]:
                continue
            raw_email = data[0][1]
            if not isinstance(raw_email, bytes):
                continue
            msg = email.message_from_bytes(raw_email)
            failed_email, reason = extract_bounced_info_from_msg(msg)
            if failed_email and "@" in failed_email and failed_email.lower() != user.lower():
                record_email_bounce(failed_email, bounce_reason=reason, db_path=target_db)
                detected_bounces.append({
                    "email": failed_email,
                    "reason": reason,
                    "mailbox": user
                })

        logger.info(f"Scanned {len(recent_ids)} NDR notice(s) for {user}; detected {len(detected_bounces)} bounce(s).")
    except Exception as e:
        logger.warning(f"Error scanning IMAP bounces for {user}: {e}")
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

    return detected_bounces

def scan_all_hostinger_bounces(db_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Scan all active Hostinger mailboxes for bounce notifications."""
    from database import get_smtp_accounts, DB_FILE
    target_db = db_path or DB_FILE
    accounts = get_smtp_accounts(active_only=True, db_path=target_db)
    all_bounces = []
    for acc in accounts:
        bounces = scan_hostinger_bounces(acc, db_path=target_db)
        all_bounces.extend(bounces)
    return all_bounces