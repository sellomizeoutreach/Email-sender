"""
smtp_dispatcher.py - Core SMTP Dispatcher for Hostinger and custom mail servers.
Supports SSL (Port 465) and STARTTLS (Port 587), MIME-compliant multipart HTML,
connection testing, and error recovery.
"""

import smtplib
import ssl
import re
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Dict, Any, Tuple, Optional

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
    smtp_host: str,
    smtp_port: int,
    email: str,
    password: str,
    timeout: int = 12
) -> Tuple[bool, str]:
    """
    Test credentials and SSL/TLS handshake with Hostinger or any SMTP server.
    Returns (True, success_msg) or (False, error_msg).
    """
    host = smtp_host.strip()
    port = int(smtp_port)
    user = email.strip()
    pwd = password.strip()

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