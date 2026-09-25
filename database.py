"""
database.py - SQLite persistence layer for the local email automation system.
Manages system configuration, contacts CRM, outreach templates, spam/negative keyword lists, and email queue.
"""

import sys
import sqlite3
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union, Tuple, Set
import re
import logging

logger = logging.getLogger("database")

def get_log_file_path() -> str:
    """Determine sellomize.log file location."""
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_dir = os.path.join(appdata, "SellomizeReach")
    else:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "sellomize.log")

try:
    _fh = logging.FileHandler(get_log_file_path(), mode="a", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
    logger.addHandler(_fh)
except Exception:
    pass


try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    Fernet = None
    InvalidToken = Exception
    CRYPTOGRAPHY_AVAILABLE = False

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    KEYRING_AVAILABLE = False

try:
    import win32crypt
    DPAPI_AVAILABLE = True
except ImportError:
    win32crypt = None
    DPAPI_AVAILABLE = False

IDENTIFIER_REGEX = re.compile(r'^[a-zA-Z0-9_]+$')

def validate_identifier(name: str) -> str:
    """Validate that an SQL column or table identifier matches ^[a-zA-Z0-9_]+$ to prevent DDL injection."""
    if not name or not IDENTIFIER_REGEX.match(str(name).strip()):
        raise ValueError(f"Invalid SQL column identifier: {name!r}")
    return str(name).strip()

LEAD_STATUSES = [
    "New",
    "Emailed",
    "Replied",
    "Bounced",
    "Do Not Contact",
]
CONTACT_STATUSES = LEAD_STATUSES

def normalize_lead_status(status: str) -> str:
    """Normalize legacy 17 statuses into the 5 final statuses."""
    s = (status or "New").strip()
    if s in LEAD_STATUSES:
        return s
    s_lower = s.lower()
    if s_lower in ["new", "researched", "drafted", "needs review", "not contacted"]:
        return "New"
    if s_lower in ["sent", "approved", "queued", "follow-up 1", "follow-up 2", "follow-up 3", "emailed", "contacted", "follow-up sent"]:
        return "Emailed"
    if s_lower in ["replied", "interested", "meeting booked"]:
        return "Replied"
    if s_lower in ["bounced"]:
        return "Bounced"
    if s_lower in ["do not contact", "not interested", "paused"]:
        return "Do Not Contact"
    return "New"

KEYRING_SERVICE_NAME = "SellomizeReach"
KEYRING_USERNAME = "smtp_fernet_key"

def _get_dpapi_key_file_path() -> str:
    """Fallback location for DPAPI-protected encryption key."""
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(appdata, "SellomizeReach")
    else:
        data_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(data_dir, ".smtp_dpapi.key")

from security import (
    get_or_create_encryption_key,
    encrypt_smtp_password,
    decrypt_smtp_password,
    sanitize_header,
    sanitize_preview_html,
)
from warmup import (
    get_warmup_info,
    get_effective_daily_limit,
)
from timezone_helper import (
    get_engine_now,
    get_engine_now_str,
)

def _hydrate_smtp_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt the stored password on an account record, setting safety flags if undecryptable."""
    raw_pass = acc.get("password") or ""
    dec_pass, undecryptable = decrypt_smtp_password(raw_pass)
    acc["password"] = dec_pass
    if undecryptable:
        acc["password_undecryptable"] = True
    return acc


def get_db_path() -> str:
    """
    Determine SQLite database location.
    When running in a packaged PyInstaller executable (frozen mode), store in
    %APPDATA%/SellomizeReach/email_system.db so user data persists across updates.
    In development mode, use local email_system.db.
    Can be overridden by setting the SELLOMIZE_DB_PATH environment variable.
    """
    custom = os.environ.get("SELLOMIZE_DB_PATH")
    if custom and custom.strip():
        return custom.strip()
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(appdata, "SellomizeReach")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "email_system.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_system.db")

DB_FILE = get_db_path()

def get_connection(db_path: str = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = DB_FILE):
    """Initialize database tables and default configuration settings."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Configuration table for API keys, models, sender/BCC, spam words, negative keywords
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # 2. Contacts table (Spreadsheet-free CRM with Tagging)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            company TEXT,
            tags TEXT DEFAULT '',
            custom_variables TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        )
    """)

    # Schema migrations for contacts table (Excel CRM fields)
    contact_migrations = [
        ("tags", "TEXT DEFAULT ''"),
        ("lead_source", "TEXT DEFAULT 'Other'"),
        ("priority", "TEXT DEFAULT 'Medium'"),
        ("contacted", "TEXT DEFAULT 'No'"),
        ("date_first_emailed", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'New'"),
        ("follow_ups_sent", "INTEGER DEFAULT 0"),
        ("last_contact_date", "TEXT DEFAULT ''"),
        ("next_follow_up", "TEXT DEFAULT ''"),
        ("owner", "TEXT DEFAULT ''"),
        ("notes", "TEXT DEFAULT ''"),
        ("last_reply_at", "TEXT DEFAULT ''"),
        ("reply_subject", "TEXT DEFAULT ''"),
        ("country_or_timezone", "TEXT DEFAULT ''")
    ]
    for col_name, col_def in contact_migrations:
        try:
            valid_col = validate_identifier(col_name)
            cursor.execute(f"ALTER TABLE contacts ADD COLUMN {valid_col} {col_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.warning(f"OperationalError during contacts migration for {col_name}: {e}")

    # Normalize all contacts to the 5 simplified statuses
    cursor.execute("""
        UPDATE contacts SET status = 'New'
        WHERE status IN ('Researched', 'Drafted', 'Needs Review', 'Not Contacted', 'new', 'New')
    """)
    cursor.execute("""
        UPDATE contacts SET status = 'Emailed'
        WHERE status IN ('Approved', 'Queued', 'Sent', 'Follow-Up 1', 'Follow-Up 2', 'Follow-Up 3', 'emailed', 'Emailed')
    """)
    cursor.execute("""
        UPDATE contacts SET status = 'Replied'
        WHERE status IN ('Interested', 'Meeting Booked', 'replied', 'Replied')
    """)
    cursor.execute("""
        UPDATE contacts SET status = 'Do Not Contact'
        WHERE status IN ('Not Interested', 'Paused', 'do not contact', 'Do Not Contact')
    """)

    # Create leads view for simplified lead access
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS leads AS
        SELECT id, name, email, company, country_or_timezone, status, notes, created_at
        FROM contacts
    """)

    # 3. Templates table (Reusable Spintax & Variable templates)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_name TEXT NOT NULL,
            body_content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    template_migrations = [
        ("name", "TEXT DEFAULT ''"),
        ("subject", "TEXT DEFAULT ''"),
        ("body_html", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT ''")
    ]
    for col_name, col_def in template_migrations:
        try:
            valid_col = validate_identifier(col_name)
            cursor.execute(f"ALTER TABLE templates ADD COLUMN {valid_col} {col_def}")
        except sqlite3.OperationalError:
            pass

    # Ensure name and body_html are synced with template_name and body_content
    cursor.execute("UPDATE templates SET name = template_name WHERE name = '' OR name IS NULL")
    cursor.execute("UPDATE templates SET body_html = body_content WHERE body_html = '' OR body_html IS NULL")

    # 4. Emails queue table for drafts, review, and dispatch status
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            recipient TEXT,
            email_html TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            scheduled_time TEXT,
            revision_notes TEXT,
            variation_num INTEGER DEFAULT 1,
            error_message TEXT,
            sent_via TEXT DEFAULT '',
            smtp_account_id INTEGER DEFAULT NULL,
            opened_at TEXT DEFAULT '',
            open_count INTEGER DEFAULT 0,
            is_bounced INTEGER DEFAULT 0,
            bounce_reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Schema migration: ensure emails tracking and dispatch columns exist
    email_migrations = [
        ("sent_via", "TEXT DEFAULT ''"),
        ("smtp_account_id", "INTEGER DEFAULT NULL"),
        ("opened_at", "TEXT DEFAULT ''"),
        ("open_count", "INTEGER DEFAULT 0"),
        ("is_bounced", "INTEGER DEFAULT 0"),
        ("bounce_reason", "TEXT DEFAULT ''"),
        ("clicked_at", "TEXT DEFAULT ''"),
        ("click_count", "INTEGER DEFAULT 0"),
        ("last_clicked_url", "TEXT DEFAULT ''"),
        ("sequence_step", "INTEGER DEFAULT 1"),
        ("sequence_id", "TEXT DEFAULT ''"),
        ("target_timezone", "TEXT DEFAULT ''"),
        ("target_country", "TEXT DEFAULT ''"),
        ("market_key", "TEXT DEFAULT ''"),
        ("message_id", "TEXT DEFAULT ''"),
        ("in_reply_to", "TEXT DEFAULT ''"),
        ("thread_id", "TEXT DEFAULT ''"),
        ("lead_id", "INTEGER DEFAULT NULL"),
        ("mailbox_id", "INTEGER DEFAULT NULL"),
        ("body_html_resolved", "TEXT DEFAULT ''"),
        ("scheduled_time_utc", "TEXT DEFAULT ''"),
        ("lead_local_time", "TEXT DEFAULT ''"),
        ("thread_refs", "TEXT DEFAULT ''"),
        ("sequence_group", "TEXT DEFAULT ''")
    ]
    for col_name, col_def in email_migrations:
        try:
            valid_col = validate_identifier(col_name)
            cursor.execute(f"ALTER TABLE emails ADD COLUMN {valid_col} {col_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.warning(f"OperationalError during emails migration for {col_name}: {e}")

    # High-performance database indexes for sub-millisecond query execution
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_status_sched ON emails(status, scheduled_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_recipient ON emails(recipient)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_click ON emails(click_count)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_seq ON emails(sequence_id, sequence_step)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status)")

    # 5. SMTP Accounts table for Hostinger / direct SMTP multi-account rotation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS smtp_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            smtp_host TEXT NOT NULL DEFAULT 'smtp.hostinger.com',
            smtp_port INTEGER NOT NULL DEFAULT 465,
            password TEXT NOT NULL,
            daily_limit INTEGER NOT NULL DEFAULT 80,
            sent_today INTEGER NOT NULL DEFAULT 0,
            last_reset_date TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            warmup_enabled INTEGER NOT NULL DEFAULT 0,
            warmup_start_date TEXT NOT NULL DEFAULT '',
            warmup_starting_limit INTEGER NOT NULL DEFAULT 10,
            warmup_daily_increment INTEGER NOT NULL DEFAULT 5,
            warmup_target_limit INTEGER NOT NULL DEFAULT 50,
            created_at TEXT NOT NULL
        )
    """)

    # Schema migrations for smtp_accounts table (Warmup & Ramp-Up schedule)
    smtp_migrations = [
        ("warmup_enabled", "INTEGER DEFAULT 0"),
        ("warmup_start_date", "TEXT DEFAULT ''"),
        ("warmup_starting_limit", "INTEGER DEFAULT 10"),
        ("warmup_daily_increment", "INTEGER DEFAULT 5"),
        ("warmup_target_limit", "INTEGER DEFAULT 50")
    ]
    for col_name, col_def in smtp_migrations:
        try:
            valid_col = validate_identifier(col_name)
            cursor.execute(f"ALTER TABLE smtp_accounts ADD COLUMN {valid_col} {col_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.warning(f"OperationalError during smtp_accounts migration for {col_name}: {e}")

    # 6. Notifications table for incoming prospect replies and alerts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL DEFAULT 'reply',
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            contact_email TEXT DEFAULT '',
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read, created_at)")

    # 7. Processed inbox messages table for IMAP idempotency and deduplication
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_inbox_messages (
            message_id TEXT PRIMARY KEY,
            sender_email TEXT NOT NULL,
            subject TEXT DEFAULT '',
            mailbox TEXT DEFAULT '',
            processed_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_msg_sender ON processed_inbox_messages(sender_email)")

    # 8. Automated sequence rules table (Send-triggered dynamic follow-up engine)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sequence_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sequence_id TEXT NOT NULL,
            contact_id INTEGER NOT NULL,
            contact_email TEXT NOT NULL,
            step_number INTEGER DEFAULT 2,
            delay_unit TEXT DEFAULT 'days',
            delay_value INTEGER DEFAULT 3,
            template_id INTEGER DEFAULT 0,
            custom_subject TEXT DEFAULT '',
            trigger_email_id INTEGER DEFAULT NULL,
            triggered_at TEXT DEFAULT '',
            due_at TEXT DEFAULT '',
            status TEXT DEFAULT 'Waiting_Trigger',
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_seq_rules_status_due ON sequence_rules(status, due_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_seq_rules_email ON sequence_rules(contact_email)")

    # Schema migration: sequence_rules timezone, market, and custom_body columns
    seq_rule_migrations = [
        ("target_timezone", "TEXT DEFAULT ''"),
        ("target_country", "TEXT DEFAULT ''"),
        ("market_key", "TEXT DEFAULT ''"),
        ("custom_body", "TEXT DEFAULT ''"),
        ("thread_reply", "INTEGER DEFAULT 1")
    ]
    for col_name, col_def in seq_rule_migrations:
        try:
            valid_col = validate_identifier(col_name)
            cursor.execute(f"ALTER TABLE sequence_rules ADD COLUMN {valid_col} {col_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.warning(f"OperationalError during sequence_rules migration for {col_name}: {e}")

    # Automatically prune historical duplicate notifications if any exist
    try:
        cursor.execute("""
            DELETE FROM notifications
            WHERE id NOT IN (
                SELECT MAX(id) FROM notifications
                GROUP BY type, LOWER(TRIM(contact_email)), title
            )
        """)
    except Exception:
        pass

    # Drop removed proof_stories table (Section 2.2)
    cursor.execute("DROP TABLE IF EXISTS proof_stories")

    # Canonical Views matching Target Data Model (Section B3)
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS mailboxes AS
        SELECT id, sender_name AS from_name, email AS username, smtp_host AS host, smtp_port AS port,
               password AS password_encrypted, daily_limit,
               warmup_starting_limit AS warmup_start, warmup_daily_increment AS warmup_increment,
               warmup_target_limit AS warmup_cap, warmup_start_date, is_active AS active,
               sender_name AS label
        FROM smtp_accounts
    """)
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS messages AS
        SELECT id, lead_id, recipient AS to_email, smtp_account_id AS mailbox_id, subject,
               COALESCE(NULLIF(body_html_resolved, ''), email_html) AS body_html,
               scheduled_time AS scheduled_time_utc, status, thread_refs, sequence_group,
               error_message AS error, 0 AS attempts, created_at, updated_at AS sent_at
        FROM emails
    """)
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS settings AS
        SELECT key, value FROM system_config
    """)

    # Populate default configuration keys if not already present
    default_configs = {
        "dispatch_method": "hostinger_smtp",
        "min_delay_seconds": "20",
        "max_delay_seconds": "45",
        "sender_email": "",
        "bcc_email": "",
        "schedule_mode": "adaptive_multi_country",
        "default_market": "LOCAL",
        "default_timezone": "Asia/Karachi",
        "negative_keywords": "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash",
        "signature_html": "<p>Best regards,<br><strong>Outreach Team</strong></p>",
        "sending_days": "Monday,Tuesday,Wednesday,Thursday,Friday",
        "sending_start_time": "09:00",
        "sending_end_time": "17:00",
        "default_window_start": "09:00",
        "default_window_end": "17:00",
        "send_delay_seconds": "60",
        "worker_heartbeat": "",
        "send_now_outside_window_policy": "immediate",
        "enforce_sending_window": "false"
    }

    for key, val in default_configs.items():
        cursor.execute("""
            INSERT OR IGNORE INTO system_config (key, value)
            VALUES (?, ?)
        """, (key, val))

    # Add sample template if none exist
    cursor.execute("SELECT COUNT(*) as count FROM templates")
    if cursor.fetchone()["count"] == 0:
        now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        sample_template = (
            "{Hi|Hello|Hey} [Name],<br><br>"
            "I was looking into [Company]'s current product catalog and noticed a few {quick opportunities|easy optimizations|high-impact improvements} on your mobile listings.<br><br>"
            "We recently prepared a 3-point listing teardown showing how {enhancing bullet clarity|optimizing infographics|refining backend keywords} helped similar brands boost conversion rates by 18-24%.<br><br>"
            "Would you be {open to|interested in} reviewing a quick 3-minute video breakdown for [Company] this week?"
        )
        cursor.execute("""
            INSERT INTO templates (template_name, body_content, created_at)
            VALUES (?, ?, ?)
        """, ("E-Commerce Listing Audit Outreach", sample_template, now_iso))

    # Add sample contacts with tags if none exist
    cursor.execute("SELECT COUNT(*) as count FROM contacts")
    if cursor.fetchone()["count"] == 0:
        now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO contacts (name, email, company, tags, custom_variables, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'New', ?)
        """, ("Sarah Jenkins", "sarah@apexoutdoors.com", "Apex Outdoors", "Listing Audit, Outdoor Brands", json.dumps({"Niche": "Outdoor Gear", "Role": "Founder"}), now_iso))
        cursor.execute("""
            INSERT INTO contacts (name, email, company, tags, custom_variables, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'New', ?)
        """, ("Elena Rostova", "elena@skinfix.com", "Skinfix", "Beauty Brands, Q4 Leads", json.dumps({"Niche": "Skincare", "Role": "Brand Director"}), now_iso))
        cursor.execute("""
            INSERT INTO contacts (name, email, company, tags, custom_variables, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'New', ?)
        """, ("Marcus Brody", "marcus@minoribeauty.com", "Minori Beauty", "Beauty Brands, Listing Audit", json.dumps({"Niche": "Cosmetics", "Role": "E-commerce Head"}), now_iso))

    conn.commit()
    conn.close()

    # Automatically restore user data if this is a fresh container / instance
    try:
        auto_restore_backup_if_needed(db_path)
    except Exception:
        pass

# ------------------------------------------------------------------------------
# BACKUP & RESTORE UTILITIES (Data Persistence Protection)
# ------------------------------------------------------------------------------

def get_backup_filepaths() -> List[str]:
    """Returns candidate backup file locations in priority order."""
    paths = []
    local_dir = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(local_dir, "sellomize_backup.json"))
    home_dir = os.path.expanduser("~")
    paths.append(os.path.join(home_dir, ".sellomize_backup.json"))
    return paths

def export_backup_data(db_path: str = DB_FILE) -> Dict[str, Any]:
    """Extracts all mailboxes, settings, templates, and contacts as a serializable dict."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT key, value FROM system_config")
    configs = {row["key"]: row["value"] for row in cur.fetchall()}

    cur.execute("SELECT * FROM smtp_accounts")
    mailboxes = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT * FROM templates")
    templates = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT * FROM contacts")
    contacts = [dict(row) for row in cur.fetchall()]

    conn.close()
    return {
        "version": "1.0",
        "exported_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "system_config": configs,
        "smtp_accounts": mailboxes,
        "templates": templates,
        "contacts": contacts
    }

def import_backup_data(backup_data: Dict[str, Any], db_path: str = DB_FILE) -> Tuple[bool, str]:
    """Restores all mailboxes, configs, templates, and contacts from a backup dict."""
    try:
        conn = get_connection(db_path)
        cur = conn.cursor()

        # 1. System Configs (signature, schedule, etc.)
        configs = backup_data.get("system_config", {})
        for k, v in configs.items():
            cur.execute("""
                INSERT INTO system_config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (k, str(v)))

        # 2. SMTP Accounts / Mailboxes
        mailboxes = backup_data.get("smtp_accounts", [])
        for m in mailboxes:
            m_email = m.get("email", "").strip()
            if not m_email:
                continue
            cur.execute("SELECT id FROM smtp_accounts WHERE email = ?", (m_email,))
            existing = cur.fetchone()
            if existing:
                cur.execute("""
                    UPDATE smtp_accounts SET
                        sender_name = ?, smtp_host = ?, smtp_port = ?, password = ?,
                        daily_limit = ?, is_active = ?, warmup_enabled = ?, warmup_start_date = ?,
                        warmup_starting_limit = ?, warmup_daily_increment = ?, warmup_target_limit = ?
                    WHERE id = ?
                """, (
                    m.get("sender_name", ""), m.get("smtp_host", "smtp.hostinger.com"),
                    m.get("smtp_port", 465), m.get("password", ""), m.get("daily_limit", 80),
                    m.get("is_active", 1), m.get("warmup_enabled", 0), m.get("warmup_start_date", ""),
                    m.get("warmup_starting_limit", 10), m.get("warmup_daily_increment", 5),
                    m.get("warmup_target_limit", 50), existing["id"]
                ))
            else:
                cur.execute("""
                    INSERT INTO smtp_accounts (
                        sender_name, email, smtp_host, smtp_port, password, daily_limit,
                        sent_today, last_reset_date, is_active, warmup_enabled, warmup_start_date,
                        warmup_starting_limit, warmup_daily_increment, warmup_target_limit, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    m.get("sender_name", ""), m_email, m.get("smtp_host", "smtp.hostinger.com"),
                    m.get("smtp_port", 465), m.get("password", ""), m.get("daily_limit", 80),
                    m.get("sent_today", 0), m.get("last_reset_date", ""), m.get("is_active", 1),
                    m.get("warmup_enabled", 0), m.get("warmup_start_date", ""),
                    m.get("warmup_starting_limit", 10), m.get("warmup_daily_increment", 5),
                    m.get("warmup_target_limit", 50), m.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                ))

        # 3. Templates
        templates = backup_data.get("templates", [])
        for t in templates:
            tname = t.get("name") or t.get("template_name") or "Template"
            cur.execute("SELECT id FROM templates WHERE template_name = ? OR name = ?", (tname, tname))
            existing = cur.fetchone()
            body = t.get("body_html") or t.get("body_content") or ""
            subj = t.get("subject", "")
            if existing:
                cur.execute("""
                    UPDATE templates SET name = ?, template_name = ?, subject = ?, body_html = ?, body_content = ?
                    WHERE id = ?
                """, (tname, tname, subj, body, body, existing["id"]))
            else:
                cur.execute("""
                    INSERT INTO templates (name, template_name, subject, body_html, body_content, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (tname, tname, subj, body, body, t.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))))

        # 4. Contacts / CRM
        contacts = backup_data.get("contacts", [])
        for c in contacts:
            c_email = c.get("email", "").strip()
            if not c_email:
                continue
            cur.execute("SELECT id FROM contacts WHERE LOWER(email) = LOWER(?)", (c_email,))
            existing = cur.fetchone()
            if existing:
                cur.execute("""
                    UPDATE contacts SET
                        name = ?, company = ?, tags = ?, custom_variables = ?, status = ?,
                        lead_source = ?, priority = ?, contacted = ?, owner = ?, notes = ?,
                        country_or_timezone = ?
                    WHERE id = ?
                """, (
                    c.get("name", ""), c.get("company", ""), c.get("tags", ""),
                    c.get("custom_variables", "{}"), c.get("status", "New"),
                    c.get("lead_source", "Other"), c.get("priority", "Medium"),
                    c.get("contacted", "No"), c.get("owner", ""), c.get("notes", ""),
                    c.get("country_or_timezone", ""), existing["id"]
                ))
            else:
                cur.execute("""
                    INSERT INTO contacts (
                        name, email, company, tags, custom_variables, status,
                        lead_source, priority, contacted, owner, notes, country_or_timezone, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    c.get("name", ""), c_email, c.get("company", ""), c.get("tags", ""),
                    c.get("custom_variables", "{}"), c.get("status", "New"),
                    c.get("lead_source", "Other"), c.get("priority", "Medium"),
                    c.get("contacted", "No"), c.get("owner", ""), c.get("notes", ""),
                    c.get("country_or_timezone", ""), c.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                ))

        conn.commit()
        conn.close()
        return True, f"Restored {len(mailboxes)} mailboxes, {len(templates)} templates, and {len(contacts)} contacts."
    except Exception as e:
        logger.error(f"Error importing backup: {e}")
        return False, str(e)

def auto_save_backup(db_path: str = DB_FILE):
    """Silently saves a backup snapshot to persistent paths so restarts never lose data."""
    try:
        data = export_backup_data(db_path)
        # Only save if there's actual data worth persisting
        if not (data.get("smtp_accounts") or data.get("templates") or data.get("contacts") or data.get("system_config", {}).get("signature_html")):
            return
        payload = json.dumps(data, indent=2)
        for p in get_backup_filepaths():
            try:
                os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(payload)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"auto_save_backup failed: {e}")

def auto_restore_backup_if_needed(db_path: str = DB_FILE):
    """If database has no user mailboxes, auto-restores from backup file if present."""
    try:
        conn = get_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as count FROM smtp_accounts")
        mailbox_count = cur.fetchone()["count"]
        conn.close()

        if mailbox_count == 0:
            for p in get_backup_filepaths():
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if data and (data.get("smtp_accounts") or data.get("templates") or data.get("system_config", {}).get("signature_html")):
                            import_backup_data(data, db_path)
                            logger.info(f"Auto-restored database from backup file '{p}'.")
                            break
                    except Exception as err:
                        logger.warning(f"Could not load backup from {p}: {err}")
    except Exception as e:
        logger.warning(f"auto_restore_backup_if_needed failed: {e}")

# ------------------------------------------------------------------------------
# SYSTEM CONFIGURATION HELPERS
# ------------------------------------------------------------------------------

def get_config(key: str, default: Optional[str] = None, db_path: str = DB_FILE) -> Optional[str]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_config(key: str, value: str, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO system_config (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()
    auto_save_backup(db_path)

def get_all_configs(db_path: str = DB_FILE) -> Dict[str, str]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM system_config")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

def save_all_configs(config_dict: Dict[str, str], db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    for key, value in config_dict.items():
        cursor.execute("""
            INSERT INTO system_config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
    conn.commit()
    conn.close()
    auto_save_backup(db_path)

# ------------------------------------------------------------------------------
# CAMPAIGN SCHEDULE & SENDING WINDOW HELPERS
# ------------------------------------------------------------------------------

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def is_within_sending_window(
    check_dt: Optional[datetime] = None,
    db_path: str = DB_FILE
) -> Tuple[bool, str]:
    """Compatibility wrapper delegating to scheduler.is_within_sending_window."""
    from scheduler import is_within_sending_window as _fn
    return _fn(check_dt=check_dt, db_path=db_path)

def get_next_valid_sending_datetime(
    base_dt: Optional[datetime] = None,
    delay_minutes: int = 0,
    sending_days: Optional[List[str]] = None,
    start_time_str: Optional[str] = None,
    end_time_str: Optional[str] = None,
    db_path: str = DB_FILE
) -> datetime:
    """Compatibility wrapper delegating to scheduler.get_next_valid_sending_datetime."""
    from scheduler import get_next_valid_sending_datetime as _fn
    return _fn(
        base_dt=base_dt,
        delay_minutes=delay_minutes,
        sending_days=sending_days,
        start_time_str=start_time_str,
        end_time_str=end_time_str,
        db_path=db_path
    )

# ------------------------------------------------------------------------------
# CONTACTS CRM HELPERS
# ------------------------------------------------------------------------------

def _normalize_tags(tags_input: Any) -> str:
    """Helper to normalize tags into a clean comma-separated string."""
    if not tags_input:
        return ""
    if isinstance(tags_input, list):
        items = [str(t).strip() for t in tags_input if str(t).strip()]
    else:
        items = [str(t).strip() for t in str(tags_input).split(",") if str(t).strip()]
    return ", ".join(sorted(list(set(items))))

def create_contact(
    name: str,
    email: str,
    company: str = "",
    tags: Any = "",
    custom_variables: Optional[Dict[str, Any]] = None,
    lead_source: str = "Other",
    priority: str = "Medium",
    contacted: str = "No",
    date_first_emailed: str = "",
    status: str = "New",
    follow_ups_sent: int = 0,
    last_contact_date: str = "",
    next_follow_up: str = "",
    owner: str = "",
    notes: str = "",
    country_or_timezone: str = "",
    db_path: str = DB_FILE
) -> int:
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    vars_json = json.dumps(custom_variables or {})
    tags_str = _normalize_tags(tags)
    norm_status = normalize_lead_status(status)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contacts (
            name, email, company, tags, custom_variables,
            lead_source, priority, contacted, date_first_emailed,
            status, follow_ups_sent, last_contact_date, next_follow_up,
            owner, notes, country_or_timezone, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name.strip(), email.strip(), company.strip(), tags_str, vars_json,
        (lead_source or "Other").strip(), (priority or "Medium").strip(),
        (contacted or "No").strip(), (date_first_emailed or "").strip(),
        norm_status, int(follow_ups_sent or 0),
        (last_contact_date or "").strip(), (next_follow_up or "").strip(),
        (owner or "").strip(), (notes or "").strip(), (country_or_timezone or "").strip(), now_iso
    ))
    contact_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return contact_id

def get_leads(status: Optional[str] = None, search: Optional[str] = None, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve leads (Name, Email, Company, Country/Timezone, Status, Notes) with optional filtering."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    query = "SELECT id, name, email, company, country_or_timezone, status, notes, created_at FROM contacts"
    conditions = []
    params = []
    if status and status != "-- All --" and status != "All":
        conditions.append("status = ?")
        params.append(normalize_lead_status(status))
    if search and search.strip():
        s = f"%{search.strip().lower()}%"
        conditions.append("(LOWER(name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(company) LIKE ?)")
        params.extend([s, s, s])
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_lead_by_id(lead_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Retrieve single lead by ID."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, company, country_or_timezone, status, notes, created_at FROM contacts WHERE id = ?", (lead_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_lead_by_email(email: str, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """Retrieve single lead by email."""
    if not email:
        return None
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, company, country_or_timezone, status, notes, created_at FROM contacts WHERE LOWER(TRIM(email)) = ? LIMIT 1", (email.strip().lower(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_lead(
    name: str,
    email: str,
    company: str = "",
    country_or_timezone: str = "",
    status: str = "New",
    notes: str = "",
    db_path: str = DB_FILE
) -> int:
    return create_contact(
        name=name,
        email=email,
        company=company,
        country_or_timezone=country_or_timezone,
        status=status,
        notes=notes,
        db_path=db_path
    )

def update_lead(
    lead_id: int,
    name: Optional[str] = None,
    email: Optional[str] = None,
    company: Optional[str] = None,
    country_or_timezone: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: str = DB_FILE
):
    update_contact(
        contact_id=lead_id,
        name=name,
        email=email,
        company=company,
        country_or_timezone=country_or_timezone,
        status=status,
        notes=notes,
        db_path=db_path
    )

def delete_lead(lead_id: int, db_path: str = DB_FILE) -> bool:
    return delete_contact(lead_id, db_path=db_path)

def _populate_contact_defaults(d: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all CRM spreadsheet fields have standardized non-null default values."""
    try:
        d["custom_variables_dict"] = json.loads(d.get("custom_variables") or "{}")
    except (json.JSONDecodeError, TypeError, ValueError) as json_err:
        logger.warning(f"Error parsing custom_variables JSON on contact {d.get('id')}: {json_err}")
        d["custom_variables_dict"] = {}
    raw_tags = d.get("tags") or ""
    d["tags_list"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
    d["lead_source"] = d.get("lead_source") or "Other"
    d["priority"] = d.get("priority") or "Medium"
    d["contacted"] = d.get("contacted") or "No"
    d["date_first_emailed"] = d.get("date_first_emailed") or ""
    d["status"] = d.get("status") or "Not Contacted"
    try:
        d["follow_ups_sent"] = int(d.get("follow_ups_sent") if d.get("follow_ups_sent") is not None else 0)
    except (ValueError, TypeError):
        d["follow_ups_sent"] = 0
    d["last_contact_date"] = d.get("last_contact_date") or ""
    d["next_follow_up"] = d.get("next_follow_up") or ""
    d["owner"] = d.get("owner") or ""
    d["notes"] = d.get("notes") or ""
    d["last_reply_at"] = d.get("last_reply_at") or ""
    d["reply_subject"] = d.get("reply_subject") or ""
    return d

def get_contacts(
    tags_filter: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    status_filter: Optional[str] = None,
    db_path: str = DB_FILE
) -> List[Dict[str, Any]]:
    """Retrieve contacts with optional filtering by tags, search query, and status."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    results = []
    normalized_tags_filter = [t.strip().lower() for t in (tags_filter or []) if t.strip()]
    clean_search = search_query.strip().lower() if search_query else ""

    for r in rows:
        d = _populate_contact_defaults(dict(r))

        # Status filter check
        if status_filter and status_filter.strip() and status_filter != "-- All --":
            if d["status"].strip().lower() != status_filter.strip().lower():
                continue

        # Tag filter check (contact must match at least one selected tag if filter is set)
        if normalized_tags_filter:
            tags_lower = [t.lower() for t in d["tags_list"]]
            if not any(filt_tag in tags_lower for filt_tag in normalized_tags_filter):
                continue

        # Search query check
        if clean_search:
            name_match = clean_search in d.get("name", "").lower()
            email_match = clean_search in d.get("email", "").lower()
            company_match = clean_search in (d.get("company") or "").lower()
            owner_match = clean_search in (d.get("owner") or "").lower()
            tag_match = any(clean_search in t for t in [t.lower() for t in d["tags_list"]])
            notes_match = clean_search in (d.get("notes") or "").lower()
            if not (name_match or email_match or company_match or owner_match or tag_match or notes_match):
                continue

        results.append(d)
    return results

def get_contact_by_id(contact_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return _populate_contact_defaults(dict(row))

def get_contact_by_email(email: str, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))", (email.strip(),))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return _populate_contact_defaults(dict(row))

def update_contact(
    contact_id: int,
    name: Optional[str] = None,
    email: Optional[str] = None,
    company: Optional[str] = None,
    tags: Optional[Any] = None,
    custom_variables: Optional[Dict[str, Any]] = None,
    lead_source: Optional[str] = None,
    priority: Optional[str] = None,
    contacted: Optional[str] = None,
    date_first_emailed: Optional[str] = None,
    status: Optional[str] = None,
    follow_ups_sent: Optional[int] = None,
    last_contact_date: Optional[str] = None,
    next_follow_up: Optional[str] = None,
    owner: Optional[str] = None,
    notes: Optional[str] = None,
    country_or_timezone: Optional[str] = None,
    db_path: str = DB_FILE
):
    conn = get_connection(db_path)
    cursor = conn.cursor()

    fields = []
    values = []

    if name is not None:
        fields.append("name = ?")
        values.append(name.strip())
    if email is not None:
        fields.append("email = ?")
        values.append(email.strip())
    if company is not None:
        fields.append("company = ?")
        values.append(company.strip())
    if tags is not None:
        fields.append("tags = ?")
        values.append(_normalize_tags(tags))
    if custom_variables is not None:
        fields.append("custom_variables = ?")
        values.append(json.dumps(custom_variables))
    if lead_source is not None:
        fields.append("lead_source = ?")
        values.append(lead_source.strip())
    if priority is not None:
        fields.append("priority = ?")
        values.append(priority.strip())
    if contacted is not None:
        fields.append("contacted = ?")
        values.append(contacted.strip())
    if date_first_emailed is not None:
        fields.append("date_first_emailed = ?")
        values.append(date_first_emailed.strip())
    if status is not None:
        fields.append("status = ?")
        values.append(normalize_lead_status(status))
    if country_or_timezone is not None:
        fields.append("country_or_timezone = ?")
        values.append(country_or_timezone.strip())
    if follow_ups_sent is not None:
        fields.append("follow_ups_sent = ?")
        values.append(int(follow_ups_sent))
    if last_contact_date is not None:
        fields.append("last_contact_date = ?")
        values.append(last_contact_date.strip())
    if next_follow_up is not None:
        fields.append("next_follow_up = ?")
        values.append(next_follow_up.strip())
    if owner is not None:
        fields.append("owner = ?")
        values.append(owner.strip())
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes.strip())

    if fields:
        values.append(contact_id)
        cursor.execute(f"UPDATE contacts SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()

    conn.close()

def upsert_contact_by_email(
    name: str,
    email: str,
    company: str = "",
    tags: Any = "",
    custom_variables: Optional[Dict[str, Any]] = None,
    lead_source: Optional[str] = None,
    priority: Optional[str] = None,
    contacted: Optional[str] = None,
    date_first_emailed: Optional[str] = None,
    status: Optional[str] = None,
    follow_ups_sent: Optional[int] = None,
    last_contact_date: Optional[str] = None,
    next_follow_up: Optional[str] = None,
    owner: Optional[str] = None,
    notes: Optional[str] = None,
    country_or_timezone: Optional[str] = None,
    db_path: str = DB_FILE
) -> Tuple[int, bool]:
    """
    If an email address already exists in the database, updates the existing row
    by merging new tags and custom variables and updating non-empty CRM fields.
    Returns (contact_id, is_created).
    """
    clean_email = email.strip()
    existing = get_contact_by_email(clean_email, db_path=db_path)

    if existing:
        contact_id = existing["id"]
        # Merge tags
        old_tags = existing.get("tags_list", [])
        new_tags = [t.strip() for t in _normalize_tags(tags).split(",") if t.strip()]
        merged_tags = list(set(old_tags + new_tags))

        # Merge custom variables
        merged_vars = dict(existing.get("custom_variables_dict", {}))
        if custom_variables:
            merged_vars.update(custom_variables)

        # Build update kwargs
        update_kwargs: Dict[str, Any] = {
            "name": name.strip() if name.strip() else existing["name"],
            "company": company.strip() if company.strip() else existing.get("company", ""),
            "tags": merged_tags,
            "custom_variables": merged_vars,
            "db_path": db_path
        }
        if lead_source is not None and lead_source.strip():
            update_kwargs["lead_source"] = lead_source.strip()
        if priority is not None and priority.strip():
            update_kwargs["priority"] = priority.strip()
        if contacted is not None and contacted.strip():
            update_kwargs["contacted"] = contacted.strip()
        if date_first_emailed is not None and date_first_emailed.strip():
            update_kwargs["date_first_emailed"] = date_first_emailed.strip()
        if status is not None and status.strip():
            update_kwargs["status"] = status.strip()
        if country_or_timezone is not None and country_or_timezone.strip():
            update_kwargs["country_or_timezone"] = country_or_timezone.strip()
        if follow_ups_sent is not None:
            update_kwargs["follow_ups_sent"] = int(follow_ups_sent)
        if last_contact_date is not None and last_contact_date.strip():
            update_kwargs["last_contact_date"] = last_contact_date.strip()
        if next_follow_up is not None and next_follow_up.strip():
            update_kwargs["next_follow_up"] = next_follow_up.strip()
        if owner is not None and owner.strip():
            update_kwargs["owner"] = owner.strip()
        if notes is not None and notes.strip():
            update_kwargs["notes"] = notes.strip()

        update_contact(contact_id=contact_id, **update_kwargs)
        return contact_id, False
    else:
        new_id = create_contact(
            name=name,
            email=clean_email,
            company=company,
            tags=tags,
            custom_variables=custom_variables,
            lead_source=lead_source or "Other",
            priority=priority or "Medium",
            contacted=contacted or "No",
            date_first_emailed=date_first_emailed or "",
            status=status or "New",
            follow_ups_sent=follow_ups_sent if follow_ups_sent is not None else 0,
            last_contact_date=last_contact_date or "",
            next_follow_up=next_follow_up or "",
            owner=owner or "",
            notes=notes or "",
            country_or_timezone=country_or_timezone or "",
            db_path=db_path
        )
        return new_id, True

def bulk_update_contact_grid(records: List[Dict[str, Any]], db_path: str = DB_FILE) -> int:
    """
    Bulk update contacts from the editable Excel-like spreadsheet grid.
    Updates each record's editable fields in a single SQLite transaction.
    """
    if not records:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    updated_count = 0
    for rec in records:
        cid = rec.get("id") or rec.get("Lead ID")
        if not cid:
            continue
        try:
            # Handle formatted L-0001 or raw int
            if isinstance(cid, str) and cid.startswith("L-"):
                cid = int(cid.replace("L-", ""))
            else:
                cid = int(cid)
        except Exception:
            continue

        name = str(rec.get("name") or rec.get("Contact Name") or "").strip()
        email = str(rec.get("email") or rec.get("Email Address") or "").replace("mailto:", "").strip()
        company = str(rec.get("company") or rec.get("Company") or "").strip()
        tags = rec.get("tags") or rec.get("Tags") or ""
        tags_str = _normalize_tags(tags)
        lead_source = str(rec.get("lead_source") or rec.get("Lead Source") or "Other").strip()
        priority = str(rec.get("priority") or rec.get("Priority") or "Medium").strip()
        contacted = str(rec.get("contacted") or rec.get("Contacted?") or "No").strip()
        date_first_emailed = str(rec.get("date_first_emailed") or rec.get("Date First Emailed") or "").strip()
        status = str(rec.get("status") or rec.get("Status") or "Not Contacted").strip()
        try:
            raw_sent = rec.get("follow_ups_sent") if rec.get("follow_ups_sent") is not None else rec.get("Follow-Ups Sent")
            follow_ups_sent = int(raw_sent if raw_sent is not None and str(raw_sent).strip() != "" else 0)
        except Exception:
            follow_ups_sent = 0
        last_contact_date = str(rec.get("last_contact_date") or rec.get("Last Contact Date") or "").strip()
        next_follow_up = str(rec.get("next_follow_up") or rec.get("Next Follow-Up") or "").strip()
        owner = str(rec.get("owner") or rec.get("Owner") or "").strip()
        notes = str(rec.get("notes") or rec.get("Notes") or "").strip()

        cursor.execute("""
            UPDATE contacts SET
                name = ?, email = ?, company = ?, tags = ?,
                lead_source = ?, priority = ?, contacted = ?,
                date_first_emailed = ?, status = ?, follow_ups_sent = ?,
                last_contact_date = ?, next_follow_up = ?, owner = ?, notes = ?
            WHERE id = ?
        """, (
            name, email, company, tags_str,
            lead_source, priority, contacted,
            date_first_emailed, status, follow_ups_sent,
            last_contact_date, next_follow_up, owner, notes,
            cid
        ))
        updated_count += 1

    conn.commit()
    conn.close()
    return updated_count

def advance_contact_followup(
    contact_id_or_email: Union[int, str],
    delay_days: int = 4,
    db_path: str = DB_FILE
) -> bool:
    """
    Advance contact outreach sequence when an email is successfully dispatched:
    - Sets contacted = 'Yes'
    - If date_first_emailed is empty, sets it to today (YYYY-MM-DD)
    - Sets last_contact_date to today (YYYY-MM-DD)
    - Increments follow_ups_sent by 1
    - Calculates next_follow_up as today + delay_days (YYYY-MM-DD)
    - Updates status: if 'Not Contacted', transitions to 'Contacted'; if 'Contacted', transitions to 'Follow-Up Sent'
    """
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    next_date_str = (datetime.now().astimezone() + timedelta(days=delay_days)).strftime("%Y-%m-%d")

    conn = get_connection(db_path)
    cursor = conn.cursor()

    if isinstance(contact_id_or_email, int) or (isinstance(contact_id_or_email, str) and contact_id_or_email.isdigit()):
        cursor.execute("SELECT * FROM contacts WHERE id = ?", (int(contact_id_or_email),))
    else:
        cursor.execute("SELECT * FROM contacts WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))", (str(contact_id_or_email).strip(),))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    cid = row["id"]
    curr_first = row["date_first_emailed"] or today_str
    try:
        curr_sent = int(row["follow_ups_sent"] or 0) + 1
    except Exception:
        curr_sent = 1
    curr_status = row["status"] or "New"
    if curr_status in ["Replied", "Bounced", "Do Not Contact"]:
        new_status = curr_status
    else:
        new_status = "Emailed"

    cursor.execute("""
        UPDATE contacts SET
            contacted = 'Yes',
            date_first_emailed = ?,
            last_contact_date = ?,
            follow_ups_sent = ?,
            next_follow_up = ?,
            status = ?
        WHERE id = ?
    """, (curr_first, today_str, curr_sent, next_date_str, new_status, cid))

    conn.commit()
    conn.close()
    return True

PREDEFINED_OUTREACH_TAGS = [
    "Amazon Brand",
    "Shopify DTC",
    "E-Commerce",
    "Wholesale",
    "FBA Private Label",
    "High Priority",
    "Audit Ready",
    "Cold Outreach",
    "Follow-Up Due",
    "Warm Lead",
    "Do Not Contact",
    "Founder / CEO",
    "Marketing Director",
    "Listing Audit"
]

def get_predefined_tags() -> List[str]:
    """Return standard agency predefined outreach tags."""
    return list(PREDEFINED_OUTREACH_TAGS)

def get_all_distinct_tags(include_predefined: bool = True, db_path: str = DB_FILE) -> List[str]:
    """Retrieve all unique tags across all contacts, optionally merged with predefined tags."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT tags FROM contacts WHERE tags IS NOT NULL AND tags != ''")
    rows = cursor.fetchall()
    conn.close()

    tag_set = set(PREDEFINED_OUTREACH_TAGS) if include_predefined else set()
    for r in rows:
        raw = r["tags"]
        for t in raw.split(","):
            cleaned = t.strip()
            if cleaned:
                tag_set.add(cleaned)
    return sorted(list(tag_set))

def get_contacts_by_tag(tag: str, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all contacts associated with a specific tag."""
    return get_contacts(tags_filter=[tag], db_path=db_path)

PREDEFINED_COMMON_VARIABLES = [
    "Role",
    "Website",
    "ASIN",
    "Product",
    "Category",
    "Location",
    "Phone",
    "Store URL",
    "Monthly Revenue"
]

def get_predefined_variable_keys() -> List[str]:
    """Return common standard outreach variable suggestions."""
    return list(PREDEFINED_COMMON_VARIABLES)

def get_all_distinct_custom_variable_keys(include_predefined: bool = True, db_path: str = DB_FILE) -> List[str]:
    """Retrieve all unique custom variable keys present across contacts, plus standard suggestions."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT custom_variables FROM contacts WHERE custom_variables IS NOT NULL AND custom_variables != ''")
    rows = cursor.fetchall()
    conn.close()

    keys_set = set(PREDEFINED_COMMON_VARIABLES) if include_predefined else set()
    for r in rows:
        raw = r["custom_variables"]
        try:
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                for k in parsed.keys():
                    if k and str(k).strip():
                        keys_set.add(str(k).strip())
        except (json.JSONDecodeError, TypeError, ValueError) as json_err:
            logger.warning(f"Error decoding custom_variables JSON in contacts: {json_err}")
    return sorted(list(keys_set))

def parse_variables_from_text(raw_text: str) -> Dict[str, str]:
    """
    Intelligently parse custom variables from user input.
    Supports:
    1. Valid JSON: {"Role": "CEO", "Website": "brand.com"}
    2. Plain key-value lines:
       Role: CEO
       Website: brand.com
       ASIN: B08N5WRWNW
    3. Key = Value lines
    """
    if not raw_text or not raw_text.strip():
        return {}
    
    text = raw_text.strip()
    # Try parsing as JSON first
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return {str(k).strip(): str(v).strip() for k, v in parsed.items() if str(k).strip()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Parse line by line: Key: Value or Key = Value
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        delimiter = None
        if ":" in line:
            delimiter = ":"
        elif "=" in line:
            delimiter = "="
        
        if delimiter:
            parts = line.split(delimiter, 1)
            k = parts[0].strip().strip('"').strip("'")
            v = parts[1].strip().strip('"').strip("'")
            if k:
                result[k] = v
        else:
            if "Note" not in result:
                result["Note"] = line
            else:
                result["Note"] += f"; {line}"
                
    return result

def format_variables_as_lines(variables: Dict[str, Any]) -> str:
    """Format dictionary of variables into clean, human-readable Key: Value lines."""
    if not variables:
        return ""
    lines = []
    for k, v in variables.items():
        if k and str(v).strip():
            lines.append(f"{k}: {v}")
    return "\n".join(lines)

def delete_contact(contact_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()

def bulk_delete_contacts(contact_ids: List[int], db_path: str = DB_FILE) -> int:
    """Delete multiple contacts in a single transaction."""
    if not contact_ids:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in contact_ids)
    cursor.execute(f"DELETE FROM contacts WHERE id IN ({placeholders})", tuple(contact_ids))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def bulk_add_tags_to_contacts(contact_ids: List[int], new_tags: List[str], db_path: str = DB_FILE) -> int:
    """Add one or more tags to selected contacts without removing existing tags."""
    if not contact_ids or not new_tags:
        return 0
    clean_new_tags = [t.strip() for t in new_tags if t.strip()]
    if not clean_new_tags:
        return 0

    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in contact_ids)
    cursor.execute(f"SELECT id, tags FROM contacts WHERE id IN ({placeholders})", tuple(contact_ids))
    rows = cursor.fetchall()

    for r in rows:
        cid = r["id"]
        old_tags = [t.strip() for t in (r["tags"] or "").split(",") if t.strip()]
        merged = sorted(list(set(old_tags + clean_new_tags)))
        merged_str = ", ".join(merged)
        cursor.execute("UPDATE contacts SET tags = ? WHERE id = ?", (merged_str, cid))

    conn.commit()
    conn.close()
    return len(rows)

def bulk_remove_tags_from_contacts(contact_ids: List[int], tags_to_remove: List[str], db_path: str = DB_FILE) -> int:
    """Remove specific tags from selected contacts."""
    if not contact_ids or not tags_to_remove:
        return 0
    remove_lower = set(t.strip().lower() for t in tags_to_remove if t.strip())

    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in contact_ids)
    cursor.execute(f"SELECT id, tags FROM contacts WHERE id IN ({placeholders})", tuple(contact_ids))
    rows = cursor.fetchall()

    for r in rows:
        cid = r["id"]
        old_tags = [t.strip() for t in (r["tags"] or "").split(",") if t.strip()]
        kept = [t for t in old_tags if t.lower() not in remove_lower]
        kept_str = ", ".join(kept)
        cursor.execute("UPDATE contacts SET tags = ? WHERE id = ?", (kept_str, cid))

    conn.commit()
    conn.close()
    return len(rows)

def bulk_set_tags_for_contacts(contact_ids: List[int], new_tags: List[str], db_path: str = DB_FILE) -> int:
    """Replace all tags on selected contacts with the provided tag list."""
    if not contact_ids:
        return 0
    tags_str = _normalize_tags(new_tags)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in contact_ids)
    cursor.execute(f"UPDATE contacts SET tags = ? WHERE id IN ({placeholders})", (tags_str, *contact_ids))
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()
    return updated_count

def bulk_update_contacts_details(
    contact_ids: List[int],
    company: Optional[str] = None,
    custom_vars_to_merge: Optional[Dict[str, Any]] = None,
    db_path: str = DB_FILE
) -> int:
    """Bulk update company or merge custom variables across multiple contacts."""
    if not contact_ids:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in contact_ids)

    if company is not None and company.strip():
        cursor.execute(f"UPDATE contacts SET company = ? WHERE id IN ({placeholders})", (company.strip(), *contact_ids))

    if custom_vars_to_merge:
        cursor.execute(f"SELECT id, custom_variables FROM contacts WHERE id IN ({placeholders})", tuple(contact_ids))
        rows = cursor.fetchall()
        for r in rows:
            cid = r["id"]
            try:
                curr_vars = json.loads(r["custom_variables"] or "{}")
            except (json.JSONDecodeError, TypeError, ValueError) as json_err:
                logger.warning(f"Error parsing custom_variables JSON on contact {cid}: {json_err}")
                curr_vars = {}
            curr_vars.update(custom_vars_to_merge)
            cursor.execute("UPDATE contacts SET custom_variables = ? WHERE id = ?", (json.dumps(curr_vars), cid))

    conn.commit()
    conn.close()
    return len(contact_ids)

def bulk_update_contacts(
    contact_ids: List[int],
    updates: Dict[str, Any],
    db_path: str = DB_FILE
) -> int:
    """
    Bulk update specified fields (status, priority, lead_source, owner, contacted, company, notes)
    across multiple contacts in a single SQL operation.
    """
    if not contact_ids or not updates:
        return 0
    
    allowed_fields = {"status", "priority", "lead_source", "owner", "contacted", "company", "notes"}
    valid_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    if not valid_updates:
        return 0

    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in contact_ids)
    
    set_clauses = []
    params = []
    for k, v in valid_updates.items():
        if k == "status":
            v = normalize_lead_status(v)
        set_clauses.append(f"{validate_identifier(k)} = ?")
        params.append(v)
    
    params.extend(contact_ids)
    sql = f"UPDATE contacts SET {', '.join(set_clauses)} WHERE id IN ({placeholders})"
    cursor.execute(sql, tuple(params))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected

# ------------------------------------------------------------------------------
# TEMPLATES HELPERS
# ------------------------------------------------------------------------------

def create_template(
    template_name: Optional[str] = None,
    body_content: Optional[str] = None,
    name: Optional[str] = None,
    subject: Optional[str] = None,
    body_html: Optional[str] = None,
    db_path: str = DB_FILE
) -> int:
    final_name = (name or template_name or "New Template").strip()
    final_body = (body_html or body_content or "").strip()
    final_subj = (subject or "").strip()
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO templates (template_name, name, subject, body_content, body_html, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (final_name, final_name, final_subj, final_body, final_body, now_iso, now_iso))
    tpl_id = cursor.lastrowid
    conn.commit()
    conn.close()
    auto_save_backup(db_path)
    return tpl_id

def get_templates(db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM templates ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_template_by_id(template_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_template(
    template_id: int,
    template_name: Optional[str] = None,
    body_content: Optional[str] = None,
    name: Optional[str] = None,
    subject: Optional[str] = None,
    body_html: Optional[str] = None,
    db_path: str = DB_FILE
):
    final_name = (name or template_name or "").strip()
    final_body = (body_html or body_content or "").strip()
    final_subj = (subject or "").strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE templates 
        SET template_name = ?, name = ?, body_content = ?, body_html = ?, subject = ?, updated_at = ?
        WHERE id = ?
    """, (final_name, final_name, final_body, final_body, final_subj, now_str, template_id))
    conn.commit()
    conn.close()
    auto_save_backup(db_path)

def delete_template(template_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    auto_save_backup(db_path)

# ------------------------------------------------------------------------------
# PROOF / CLIENT STORY LIBRARY HELPERS (Phase 2)
# ------------------------------------------------------------------------------

def get_proof_stories(angle: Optional[str] = None, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all proof stories, optionally filtered by angle."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if angle and angle.strip():
        cursor.execute("SELECT * FROM proof_stories WHERE LOWER(TRIM(angle)) = LOWER(TRIM(?)) ORDER BY id ASC", (angle.strip(),))
    else:
        cursor.execute("SELECT * FROM proof_stories ORDER BY id ASC")
    rows = cursor.fetchall()
    stories = [dict(r) for r in rows]
    conn.close()
    return stories

def create_proof_story(
    client_name: str,
    angle: str,
    headline: str,
    metric_highlight: str,
    full_story_snippet: str,
    client_type: str = "Brand",
    relevance_tags: str = "",
    db_path: str = DB_FILE
) -> int:
    """Insert a new client proof / case study story."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO proof_stories (
            client_name, client_type, angle, headline, metric_highlight,
            full_story_snippet, relevance_tags, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        client_name.strip(), client_type.strip(), angle.strip(), headline.strip(),
        metric_highlight.strip(), full_story_snippet.strip(), relevance_tags.strip(),
        now_iso
    ))
    story_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return story_id

def update_proof_story(
    story_id: int,
    client_name: Optional[str] = None,
    angle: Optional[str] = None,
    headline: Optional[str] = None,
    metric_highlight: Optional[str] = None,
    full_story_snippet: Optional[str] = None,
    client_type: Optional[str] = None,
    relevance_tags: Optional[str] = None,
    db_path: str = DB_FILE
) -> bool:
    """Update fields of an existing proof story."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    fields = []
    values = []
    if client_name is not None:
        fields.append("client_name = ?")
        values.append(client_name.strip())
    if angle is not None:
        fields.append("angle = ?")
        values.append(angle.strip())
    if headline is not None:
        fields.append("headline = ?")
        values.append(headline.strip())
    if metric_highlight is not None:
        fields.append("metric_highlight = ?")
        values.append(metric_highlight.strip())
    if full_story_snippet is not None:
        fields.append("full_story_snippet = ?")
        values.append(full_story_snippet.strip())
    if client_type is not None:
        fields.append("client_type = ?")
        values.append(client_type.strip())
    if relevance_tags is not None:
        fields.append("relevance_tags = ?")
        values.append(relevance_tags.strip())

    if fields:
        values.append(story_id)
        cursor.execute(f"UPDATE proof_stories SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()
    conn.close()
    return True

def delete_proof_story(story_id: int, db_path: str = DB_FILE) -> bool:
    """Delete a proof story by ID."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proof_stories WHERE id = ?", (story_id,))
    conn.commit()
    conn.close()
    return True


# ------------------------------------------------------------------------------
# EMAILS QUEUE HELPERS
# ------------------------------------------------------------------------------

def create_email(
    email_html: str,
    subject: str = "Partnership Inquiry",
    recipient: str = "",
    scheduled_time: Optional[str] = None,
    status: str = "Pending",
    variation_num: int = 1,
    revision_notes: Optional[str] = None,
    sequence_step: int = 1,
    sequence_id: str = "",
    target_timezone: str = "",
    target_country: str = "",
    market_key: str = "",
    message_id: str = "",
    in_reply_to: str = "",
    thread_id: str = "",
    db_path: str = DB_FILE
) -> int:
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO emails (
            subject, recipient, email_html, status, scheduled_time,
            variation_num, revision_notes, sequence_step, sequence_id,
            target_timezone, target_country, market_key,
            message_id, in_reply_to, thread_id,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        subject, recipient, email_html, status, scheduled_time,
        variation_num, revision_notes, sequence_step, sequence_id,
        target_timezone.strip(), target_country.strip(), market_key.strip(),
        message_id.strip(), in_reply_to.strip(), thread_id.strip(),
        now_iso, now_iso
    ))
    email_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return email_id

def get_emails(status: Optional[str] = None, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM emails WHERE status = ? ORDER BY id DESC", (status,))
    else:
        cursor.execute("SELECT * FROM emails ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_email_by_id(email_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM emails WHERE id = ?", (email_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

_FIELD_UNSET = object()

def update_email(
    email_id: int,
    email_html: Optional[str] = None,
    subject: Optional[str] = None,
    recipient: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    status: Optional[str] = None,
    revision_notes: Any = _FIELD_UNSET,
    error_message: Any = _FIELD_UNSET,
    sequence_step: Any = _FIELD_UNSET,
    sequence_id: Any = _FIELD_UNSET,
    message_id: Any = _FIELD_UNSET,
    in_reply_to: Any = _FIELD_UNSET,
    thread_id: Any = _FIELD_UNSET,
    smtp_account_id: Any = _FIELD_UNSET,
    target_timezone: Any = _FIELD_UNSET,
    sent_via: Any = _FIELD_UNSET,
    db_path: str = DB_FILE,
    **kwargs
):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    fields = ["updated_at = ?"]
    values = [now_iso]

    if email_html is not None:
        fields.append("email_html = ?")
        values.append(email_html)
    if subject is not None:
        fields.append("subject = ?")
        values.append(subject)
    if recipient is not None:
        fields.append("recipient = ?")
        values.append(recipient)
    if scheduled_time is not None:
        fields.append("scheduled_time = ?")
        values.append(scheduled_time)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if revision_notes is not _FIELD_UNSET:
        fields.append("revision_notes = ?")
        values.append(revision_notes)
    if error_message is not _FIELD_UNSET:
        fields.append("error_message = ?")
        values.append(error_message)
    if sequence_step is not _FIELD_UNSET:
        fields.append("sequence_step = ?")
        values.append(sequence_step)
    if sequence_id is not _FIELD_UNSET:
        fields.append("sequence_id = ?")
        values.append(sequence_id)
    if message_id is not _FIELD_UNSET:
        fields.append("message_id = ?")
        values.append(message_id)
    if in_reply_to is not _FIELD_UNSET:
        fields.append("in_reply_to = ?")
        values.append(in_reply_to)
    if thread_id is not _FIELD_UNSET:
        fields.append("thread_id = ?")
        values.append(thread_id)
    if smtp_account_id is not _FIELD_UNSET:
        fields.append("smtp_account_id = ?")
        values.append(smtp_account_id)
    if target_timezone is not _FIELD_UNSET:
        fields.append("target_timezone = ?")
        values.append(target_timezone)
    if sent_via is not _FIELD_UNSET:
        fields.append("sent_via = ?")
        values.append(sent_via)

    for k, v in kwargs.items():
        if k in [
            "opened_at", "open_count", "is_bounced", "bounce_reason",
            "clicked_at", "click_count", "last_clicked_url", "target_country",
            "market_key", "lead_id", "mailbox_id", "body_html_resolved",
            "scheduled_time_utc", "lead_local_time", "thread_refs", "sequence_group"
        ]:
            fields.append(f"{k} = ?")
            values.append(v)

    values.append(email_id)
    query = f"UPDATE emails SET {', '.join(fields)} WHERE id = ?"
    cursor.execute(query, tuple(values))
    conn.commit()
    conn.close()

def approve_email(
    email_id: int,
    recipient: str,
    scheduled_time: Optional[str] = None,
    email_html: Optional[str] = None,
    subject: Optional[str] = None,
    db_path: str = DB_FILE
):
    update_email(
        email_id=email_id,
        email_html=email_html,
        subject=subject,
        recipient=recipient,
        scheduled_time=scheduled_time,
        status="Approved",
        error_message=None,
        revision_notes=None,
        db_path=db_path
    )
    if recipient:
        contact = get_contact_by_email(recipient, db_path=db_path)
        if contact and contact.get("status") in ["New", "Researched", "Drafted", "Needs Review"]:
            update_contact(contact["id"], status="Approved", db_path=db_path)


def flag_email(email_id: int, trigger_word: Union[str, List[str]], db_path: str = DB_FILE):
    """Mark email status as Flagged due to detected negative keyword(s)."""
    if isinstance(trigger_word, (list, tuple, set)):
        trig_str = ", ".join(f"'{w}'" for w in trigger_word)
        notes = f"Flagged for trigger keyword(s): {trig_str}"
    else:
        notes = f"Flagged for trigger keyword(s): '{trigger_word}'"
    update_email(
        email_id=email_id,
        status="Flagged",
        revision_notes=notes,
        db_path=db_path
    )

def get_approved_due_emails(current_time_str: Optional[str] = None, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    if not current_time_str:
        from timezone_helper import get_engine_now_str
        current_time_str = get_engine_now_str()

    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM emails
        WHERE status = 'Approved'
          AND scheduled_time IS NOT NULL
          AND scheduled_time <= ?
        ORDER BY scheduled_time ASC
    """, (current_time_str,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def mark_email_sent(email_id: int, sent_at: Optional[str] = None, message_id: Optional[str] = None, db_path: str = DB_FILE):
    now_iso = sent_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    update_email(
        email_id=email_id,
        status="Sent",
        error_message=None,
        message_id=message_id if message_id else _FIELD_UNSET,
        db_path=db_path
    )
    trigger_sequence_rules_for_sent_email(email_id, sent_at_iso=now_iso, db_path=db_path)

def mark_email_error(email_id: int, status: str, error_message: str, db_path: str = DB_FILE):
    update_email(email_id=email_id, status=status, error_message=error_message, db_path=db_path)

def delete_email(email_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM emails WHERE id = ?", (email_id,))
    conn.commit()
    conn.close()

def bulk_delete_emails(email_ids: List[int], db_path: str = DB_FILE) -> int:
    """Delete multiple emails by their IDs in a single atomic transaction."""
    if not email_ids:
        return 0
    clean_ids = [int(i) for i in email_ids if str(i).isdigit() or isinstance(i, int)]
    if not clean_ids:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in clean_ids)
    cursor.execute(f"DELETE FROM emails WHERE id IN ({placeholders})", clean_ids)
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return max(0, deleted_count)

def clear_outbox_emails(status: Optional[str] = None, exclude_pending: bool = True, db_path: str = DB_FILE) -> int:
    """
    Clear historical outbox emails to prevent clutter and database buildup.
    - If status is provided (e.g. 'Sent', 'Error', 'Account Mismatch'), deletes emails with that status.
    - If status is None or 'All', deletes all historical emails (excluding 'Pending' if exclude_pending=True).
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    query = "DELETE FROM emails WHERE 1=1"
    params = []
    if status and status != "All":
        query += " AND status = ?"
        params.append(status)
    elif exclude_pending:
        query += " AND status != 'Pending'"
    cursor.execute(query, params)
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return max(0, deleted_count)

def get_system_excluded_emails(db_path: str = DB_FILE) -> Set[str]:
    """
    Returns a unified set of lowercased email addresses that belong to internal systems:
    - All configured BCC monitoring/archival addresses (comma-separated or single)
    - The configured system sender_email
    - All Mailbox Fleet sending accounts (active or inactive)
    These addresses should NEVER receive cold outreach drafts or be targeted in campaigns.
    """
    excluded: Set[str] = set()
    try:
        bcc_raw = get_config("bcc_email", default="", db_path=db_path) or ""
        for item in bcc_raw.split(","):
            cleaned = item.strip().lower()
            if cleaned and "@" in cleaned:
                excluded.add(cleaned)
    except Exception as e:
        logger.warning(f"Error reading bcc_email in get_system_excluded_emails: {e}")

    try:
        sender = get_config("sender_email", default="", db_path=db_path) or ""
        cleaned_sender = sender.strip().lower()
        if cleaned_sender and "@" in cleaned_sender:
            excluded.add(cleaned_sender)
    except Exception as e:
        logger.warning(f"Error reading sender_email in get_system_excluded_emails: {e}")

    try:
        accounts = get_smtp_accounts(active_only=False, db_path=db_path)
        for acc in accounts:
            m_email = (acc.get("email") or "").strip().lower()
            if m_email and "@" in m_email:
                excluded.add(m_email)
    except Exception as e:
        logger.warning(f"Error reading smtp accounts in get_system_excluded_emails: {e}")

    return excluded

def cleanup_internal_drafts(db_path: str = DB_FILE) -> int:
    """
    Discards/deletes pending or flagged drafts whose recipient matches any system excluded
    address (configured BCC addresses or sender mailboxes) to clean up accidental queue pollution.
    Returns the count of deleted drafts.
    """
    excluded = get_system_excluded_emails(db_path=db_path)
    if not excluded:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, recipient FROM emails WHERE status IN ('Pending', 'Flagged', 'Action Required')")
    rows = cursor.fetchall()
    to_delete = []
    for r in rows:
        rec = (r["recipient"] or "").strip().lower()
        if rec in excluded:
            to_delete.append(r["id"])
    conn.close()
    if to_delete:
        return bulk_delete_emails(to_delete, db_path=db_path)
    return 0

def record_email_open(email_id: int, db_path: str = DB_FILE) -> bool:
    """
    Called when an email tracking pixel is loaded.
    Records timestamp, increments open_count, and flags contact as 'Opened / Interested'.
    """
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM emails WHERE id = ?", (email_id,))
    email_row = cursor.fetchone()
    if not email_row:
        conn.close()
        return False

    curr_opened_at = email_row["opened_at"] or now_iso
    try:
        curr_count = int(email_row["open_count"] or 0) + 1
    except Exception:
        curr_count = 1

    cursor.execute("""
        UPDATE emails SET opened_at = ?, open_count = ? WHERE id = ?
    """, (curr_opened_at, curr_count, email_id))

    # Update associated contact if found
    recipient = email_row["recipient"]
    if recipient:
        cursor.execute("SELECT id, status, tags FROM contacts WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))", (recipient.strip(),))
        contact_row = cursor.fetchone()
        if contact_row:
            cid = contact_row["id"]
            c_status = contact_row["status"] or ""
            # If lead hasn't replied or closed, update status to Opened / Interested
            if c_status not in ["Replied", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"]:
                cursor.execute("UPDATE contacts SET status = 'Opened / Interested' WHERE id = ?", (cid,))

    conn.commit()
    conn.close()
    return True

def record_email_click(email_id: int, clicked_url: str = "", db_path: str = DB_FILE) -> bool:
    """
    Called when a tracked link in an email is clicked.
    Records timestamp, increments click_count, stores last_clicked_url,
    and updates associated contact with 'Clicked Link' tag and audit note.
    """
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM emails WHERE id = ?", (email_id,))
    email_row = cursor.fetchone()
    if not email_row:
        conn.close()
        return False

    curr_clicked_at = email_row["clicked_at"] or now_iso
    try:
        curr_count = int(email_row["click_count"] or 0) + 1
    except Exception:
        curr_count = 1

    cursor.execute("""
        UPDATE emails SET clicked_at = ?, click_count = ?, last_clicked_url = ? WHERE id = ?
    """, (curr_clicked_at, curr_count, (clicked_url or "")[:250], email_id))

    # Update associated contact if found
    recipient = email_row["recipient"]
    if recipient:
        cursor.execute("SELECT id, status, tags, notes FROM contacts WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))", (recipient.strip(),))
        contact_row = cursor.fetchone()
        if contact_row:
            cid = contact_row["id"]
            c_status = contact_row["status"] or ""

            # Add 'Clicked Link' tag
            old_tags = [t.strip() for t in (contact_row["tags"] or "").split(",") if t.strip()]
            if "Clicked Link" not in old_tags:
                old_tags.append("Clicked Link")
            tags_str = ", ".join(sorted(list(set(old_tags))))

            # Append note
            curr_notes = contact_row["notes"] or ""
            url_hint = f" ({clicked_url[:35]}...)" if clicked_url else ""
            click_note = f"[Clicked Link: {today_str}{url_hint}]"
            if click_note not in curr_notes:
                updated_notes = f"{curr_notes} {click_note}".strip() if curr_notes else click_note
            else:
                updated_notes = curr_notes

            # If lead hasn't replied or closed, promote status to Opened / Interested if still Not Contacted/Contacted
            if c_status not in ["Replied", "Closed Won", "Closed Lost", "Bounced", "Do Not Contact"]:
                cursor.execute("""
                    UPDATE contacts SET tags = ?, notes = ? WHERE id = ?
                """, (tags_str, updated_notes, cid))
            else:
                cursor.execute("""
                    UPDATE contacts SET tags = ?, notes = ? WHERE id = ?
                """, (tags_str, updated_notes, cid))

    conn.commit()
    conn.close()
    return True

def record_email_bounce(
    recipient_email: str,
    bounce_reason: str = "",
    smtp_code: Optional[int] = None,
    db_path: str = DB_FILE
) -> int:
    """
    Record an email bounce with hard vs soft bounce discrimination.
    Hard bounce (e.g. 550, user unknown, mailbox not found):
      - Update emails table: is_bounced = 1, status = 'Bounced'.
      - Quarantine contact: status = 'Bounced', tag 'Bounced', notes audit log.
    Soft bounce (e.g. 452 mailbox full, greylisting, temporary 4xx failure):
      - Update emails table: is_bounced = 0, status = 'Flagged' for retry.
      - Contact status is UNCHANGED (not 'Do Not Contact', not 'Bounced').
      - Add audit note [Soft Bounce: ...] to contact notes.
    """
    clean_email = recipient_email.strip().lower()
    if not clean_email:
        return 0

    # Determine if soft or hard bounce
    is_soft = False
    if smtp_code is not None:
        if 400 <= smtp_code < 500:
            is_soft = True
    else:
        reason_lower = (bounce_reason or "").lower()
        soft_signals = ["452", "451", "421", "mailbox full", "quota exceeded", "greylist", "try again", "temporary", "busy"]
        if any(sig in reason_lower for sig in soft_signals):
            is_soft = True

    conn = get_connection(db_path)
    cursor = conn.cursor()

    if is_soft:
        # 1. Update emails table: mark Flagged for retry, not hard bounced
        cursor.execute("""
            UPDATE emails SET is_bounced = 0, bounce_reason = ?, status = 'Flagged'
            WHERE LOWER(TRIM(recipient)) = ? AND status IN ('Pending', 'Approved', 'Draft')
        """, (f"Soft bounce: {bounce_reason.strip()}", clean_email))

        # 2. Update contacts table: leave status unchanged, add audit note
        cursor.execute("SELECT id, status, tags, notes FROM contacts WHERE LOWER(TRIM(email)) = ?", (clean_email,))
        contact_rows = cursor.fetchall()
        for crow in contact_rows:
            cid = crow["id"]
            curr_notes = crow["notes"] or ""
            reason_note = f"[Soft Bounce: {bounce_reason}]" if bounce_reason else "[Soft Bounce: Temporary Delivery Failure]"
            if reason_note not in curr_notes:
                updated_notes = f"{curr_notes} {reason_note}".strip() if curr_notes else reason_note
            else:
                updated_notes = curr_notes

            # Status is strictly preserved unchanged
            cursor.execute("""
                UPDATE contacts SET notes = ? WHERE id = ?
            """, (updated_notes, cid))

        conn.commit()
        conn.close()
        return len(contact_rows)

    # Hard bounce
    cursor.execute("""
        UPDATE emails SET is_bounced = 1, bounce_reason = ?, status = 'Bounced'
        WHERE LOWER(TRIM(recipient)) = ?
    """, (bounce_reason.strip(), clean_email))

    cursor.execute("SELECT id, tags, notes FROM contacts WHERE LOWER(TRIM(email)) = ?", (clean_email,))
    contact_rows = cursor.fetchall()
    for crow in contact_rows:
        cid = crow["id"]
        old_tags = [t.strip() for t in (crow["tags"] or "").split(",") if t.strip()]
        if "Bounced" not in old_tags:
            old_tags.append("Bounced")
        tags_str = ", ".join(sorted(list(set(old_tags))))

        curr_notes = crow["notes"] or ""
        reason_note = f"[Bounced: {bounce_reason}]" if bounce_reason else "[Bounced NDR]"
        if reason_note not in curr_notes:
            updated_notes = f"{curr_notes} {reason_note}".strip() if curr_notes else reason_note
        else:
            updated_notes = curr_notes

        cursor.execute("""
            UPDATE contacts SET status = 'Bounced', tags = ?, notes = ? WHERE id = ?
        """, (tags_str, updated_notes, cid))

    conn.commit()
    conn.close()
    return len(contact_rows)

def record_email_reply(
    sender_email: str,
    reply_subject: str = "",
    reply_body_snippet: str = "",
    received_at: Optional[str] = None,
    db_path: str = DB_FILE
) -> Dict[str, Any]:
    """
    Called when an incoming reply from a lead is detected (Section 1.9 & 2.4):
    1. Marks that lead 'Replied'.
    2. Pauses (does not delete) that lead's pending scheduled follow-ups.
    3. Records an in-app alert for the operator.
    """
    clean_email = sender_email.strip().lower()
    if not clean_email:
        return {"contact_found": False, "paused_drafts_count": 0, "paused_email_ids": [], "cancelled_drafts_count": 0, "cancelled_email_ids": []}

    now_iso = received_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Update contact(s)
    cursor.execute("SELECT id, name, tags, notes FROM contacts WHERE LOWER(TRIM(email)) = ?", (clean_email,))
    contact_rows = cursor.fetchall()
    contact_found = len(contact_rows) > 0
    contact_names = []

    for crow in contact_rows:
        cid = crow["id"]
        contact_names.append(crow["name"])
        old_tags = [t.strip() for t in (crow["tags"] or "").split(",") if t.strip()]
        if "Replied" not in old_tags:
            old_tags.append("Replied")
        tags_str = ", ".join(sorted(list(set(old_tags))))

        curr_notes = crow["notes"] or ""
        snippet_part = f" - '{reply_subject[:40]}'" if reply_subject else ""
        reply_note = f"[Replied: {today_str}{snippet_part}]"
        if reply_note not in curr_notes:
            updated_notes = f"{curr_notes} {reply_note}".strip() if curr_notes else reply_note
        else:
            updated_notes = curr_notes

        cursor.execute("""
            UPDATE contacts SET
                status = 'Replied',
                contacted = 'Yes',
                last_contact_date = ?,
                last_reply_at = ?,
                reply_subject = ?,
                tags = ?,
                notes = ?
            WHERE id = ?
        """, (today_str, now_iso, (reply_subject or "")[:120], tags_str, updated_notes, cid))


    # 2. Pause pending / scheduled follow-up emails for this lead
    cursor.execute("""
        SELECT id FROM emails
        WHERE LOWER(TRIM(recipient)) = ? 
          AND status IN ('Pending', 'Approved', 'Scheduled', 'Draft', 'Flagged')
          AND sequence_step > 1
    """, (clean_email,))
    pending_emails = cursor.fetchall()
    paused_ids = [r["id"] for r in pending_emails]


    if paused_ids:
        placeholders = ",".join("?" * len(paused_ids))
        cursor.execute(f"""
            UPDATE emails SET
                status = 'Paused',
                revision_notes = 'Auto-paused: lead replied to previous outreach',
                error_message = 'Auto-cancelled: Prospect replied (Replied)'
            WHERE id IN ({placeholders})
        """, tuple(paused_ids))

    # Cancel active sequence rules as well
    cursor.execute("""
        UPDATE sequence_rules SET status = 'Cancelled'
        WHERE LOWER(TRIM(contact_email)) = ? AND status IN ('Waiting_Trigger', 'Scheduled')
    """, (clean_email,))


    disp_name = contact_names[0] if contact_names else clean_email
    notif_title = f"💬 Reply from {disp_name}"
    subj_part = f" ('{reply_subject[:45]}')" if reply_subject else ""
    pause_part = f" — {len(paused_ids)} scheduled follow-up(s) paused." if paused_ids else ""
    notif_body = f"Prospect {clean_email} replied{subj_part}{pause_part}"

    cursor.execute("""
        SELECT id FROM notifications
        WHERE type = 'reply'
          AND LOWER(TRIM(contact_email)) = ?
          AND is_read = 0
    """, (clean_email,))
    existing_unreads = cursor.fetchall()
    if not existing_unreads:
        cursor.execute("""
            INSERT INTO notifications (type, title, message, contact_email, is_read, created_at)
            VALUES ('reply', ?, ?, ?, 0, ?)
        """, (notif_title, notif_body, clean_email, now_iso))

    conn.commit()
    conn.close()

    return {
        "contact_found": contact_found,
        "contact_names": contact_names,
        "paused_drafts_count": len(paused_ids),
        "paused_email_ids": paused_ids,
        "cancelled_drafts_count": len(paused_ids),
        "cancelled_email_ids": paused_ids,
        "sender_email": clean_email,
        "status": "Replied",
        "category": "replied"
    }


# ------------------------------------------------------------------------------
# IN-APP NOTIFICATIONS HELPERS
# ------------------------------------------------------------------------------

def create_notification(
    type: str = "reply",
    title: str = "",
    message: str = "",
    contact_email: str = "",
    is_read: int = 0,
    db_path: str = DB_FILE
) -> int:
    """Create a persistent notification record in SQLite."""
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notifications (type, title, message, contact_email, is_read, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (type, title, message, contact_email, is_read, now_iso))
    notif_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return notif_id

def get_notifications(
    unread_only: bool = False,
    limit: int = 50,
    db_path: str = DB_FILE
) -> List[Dict[str, Any]]:
    """Retrieve notifications ordered by recency."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if unread_only:
        cursor.execute("SELECT * FROM notifications WHERE is_read = 0 ORDER BY id DESC LIMIT ?", (limit,))
    else:
        cursor.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_notification_as_read(notification_id: int, db_path: str = DB_FILE):
    """Mark a specific notification as read."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
    conn.commit()
    conn.close()

def mark_all_notifications_as_read(db_path: str = DB_FILE):
    """Mark all unread notifications as read."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
    conn.commit()
    conn.close()

def get_unread_notifications_count(db_path: str = DB_FILE) -> int:
    """Return count of unread notifications."""
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM notifications WHERE is_read = 0")
        row = cursor.fetchone()
        conn.close()
        return row["count"] if row else 0
    except Exception:
        return 0

def delete_notification(notification_id: int, db_path: str = DB_FILE):
    """Delete a notification by ID."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))
    conn.commit()
    conn.close()

def clear_all_notifications(db_path: str = DB_FILE) -> int:
    """Delete all notifications from the database."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notifications")
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return max(0, count)

def cleanup_duplicate_notifications(db_path: str = DB_FILE) -> int:
    """Prune historical duplicate notifications, preserving the latest notification per contact/event."""
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM notifications
            WHERE id NOT IN (
                SELECT MAX(id) FROM notifications
                GROUP BY type, LOWER(TRIM(contact_email)), title
            )
        """)
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return max(0, deleted)
    except Exception as e:
        logger.warning(f"Error cleaning duplicate notifications: {e}")
        return 0

# ------------------------------------------------------------------------------
# PROCESSED INBOX MESSAGES (IMAP IDEMPOTENCY)
# ------------------------------------------------------------------------------

def is_inbox_message_processed(message_id: str, db_path: str = DB_FILE) -> bool:
    """Check if an IMAP message ID has already been parsed and processed."""
    if not message_id or not message_id.strip():
        return False
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_inbox_messages WHERE message_id = ?", (message_id.strip(),))
    res = cursor.fetchone() is not None
    conn.close()
    return res

def mark_inbox_message_processed(
    message_id: str,
    sender_email: str = "",
    subject: str = "",
    mailbox: str = "",
    db_path: str = DB_FILE
):
    """Record an IMAP message ID as processed to guarantee idempotent inbox scanning."""
    if not message_id or not message_id.strip():
        return
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO processed_inbox_messages (message_id, sender_email, subject, mailbox, processed_at)
        VALUES (?, ?, ?, ?, ?)
    """, (message_id.strip(), sender_email.strip().lower(), (subject or "")[:150], (mailbox or "").strip().lower(), now_iso))
    conn.commit()
    conn.close()

# ------------------------------------------------------------------------------
# AUTOMATED SEND-TRIGGERED SEQUENCE RULES ENGINE
# ------------------------------------------------------------------------------

def create_sequence_rule(
    sequence_id: str,
    contact_id: int,
    contact_email: str,
    step_number: int,
    delay_unit: str,
    delay_value: int,
    template_id: Optional[int] = None,
    custom_subject: str = "",
    custom_body: str = "",
    trigger_email_id: Optional[int] = None,
    target_timezone: str = "",
    target_country: str = "",
    market_key: str = "",
    db_path: str = DB_FILE
) -> int:
    """Register an automated follow-up sequence rule for a contact (supports pre-made template or custom body)."""
    tid_val = int(template_id) if (template_id is not None and str(template_id).isdigit()) else 0
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sequence_rules (
            sequence_id, contact_id, contact_email, step_number,
            delay_unit, delay_value, template_id, custom_subject, custom_body,
            trigger_email_id, target_timezone, target_country, market_key,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Waiting_Trigger', ?)
    """, (
        sequence_id, contact_id, contact_email.strip().lower(), step_number,
        delay_unit.lower(), int(delay_value), tid_val, custom_subject.strip(), custom_body.strip(),
        trigger_email_id, target_timezone.strip(), target_country.strip(), market_key.strip(),
        now_iso
    ))
    rid = cursor.lastrowid
    conn.commit()
    conn.close()
    return rid

def trigger_sequence_rules_for_sent_email(email_id: int, sent_at_iso: Optional[str] = None, db_path: str = DB_FILE) -> int:
    """
    Called when an outreach email is sent: starts the timer for any associated follow-up sequence rules.
    Calculates due_at based on the configured delay (days or hours) from the actual send time.
    """
    if not sent_at_iso:
        sent_at_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    try:
        sent_dt = datetime.strptime(sent_at_iso[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        sent_dt = datetime.now()

    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM sequence_rules
        WHERE trigger_email_id = ? AND status = 'Waiting_Trigger'
    """, (email_id,))
    rules = [dict(r) for r in cursor.fetchall()]

    activated_count = 0
    for r in rules:
        delay_unit = (r.get("delay_unit") or "days").lower()
        delay_val = int(r.get("delay_value") or 3)
        if "hour" in delay_unit:
            due_dt = sent_dt + timedelta(hours=delay_val)
        else:
            due_dt = sent_dt + timedelta(days=delay_val)

        due_iso = due_dt.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE sequence_rules SET
                status = 'Scheduled',
                triggered_at = ?,
                due_at = ?
            WHERE id = ?
        """, (sent_at_iso, due_iso, r["id"]))
        activated_count += 1

    conn.commit()
    conn.close()
    return activated_count

def get_due_sequence_rules(current_time_iso: Optional[str] = None, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all sequence follow-up rules that have passed their scheduled wait interval."""
    if not current_time_iso:
        current_time_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM sequence_rules
        WHERE status = 'Scheduled'
          AND due_at IS NOT NULL
          AND due_at != ''
          AND due_at <= ?
        ORDER BY due_at ASC
    """, (current_time_iso,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_sequence_rule_status(rule_id: int, status: str, db_path: str = DB_FILE):
    """Update lifecycle status of a sequence rule ('Scheduled', 'Generated', 'Cancelled')."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE sequence_rules SET status = ? WHERE id = ?", (status, rule_id))
    conn.commit()
    conn.close()

def cancel_sequence_rules_for_contact(clean_email: str, db_path: str = DB_FILE) -> int:
    """Cancel all active sequence rules for a contact when they reply or bounce."""
    clean = clean_email.strip().lower()
    if not clean:
        return 0
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE sequence_rules SET status = 'Cancelled'
        WHERE LOWER(TRIM(contact_email)) = ? AND status IN ('Waiting_Trigger', 'Scheduled')
    """, (clean,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return max(0, count)

def link_sequence_rule_trigger(sequence_id: str, contact_email: str, step_number: int, trigger_email_id: int, db_path: str = DB_FILE) -> bool:
    """Link a newly created email ID as the trigger for the subsequent sequence step."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE sequence_rules
        SET trigger_email_id = ?
        WHERE sequence_id = ?
          AND LOWER(TRIM(contact_email)) = ?
          AND step_number = ?
          AND status = 'Waiting_Trigger'
    """, (trigger_email_id, sequence_id, contact_email.strip().lower(), step_number))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def get_sequence_rules(status: Optional[str] = None, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve sequence rules optionally filtered by status."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM sequence_rules WHERE status = ? ORDER BY id DESC", (status,))
    else:
        cursor.execute("SELECT * FROM sequence_rules ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_replied_contacts(db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all contacts marked as Replied."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts WHERE status = 'Replied' OR tags LIKE '%Replied%' ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [_populate_contact_defaults(dict(r)) for r in rows]

def get_outreach_analytics(db_path: str = DB_FILE) -> Dict[str, Any]:
    """Calculate core outreach KPIs: Sent, Opens, Open Rate %, Bounces, Bounce Rate %, Replies, Reply Rate %, Follow-ups Due."""
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Contacts stats
    cursor.execute("SELECT COUNT(*) as total FROM contacts")
    total_contacts = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as contacted FROM contacts WHERE contacted = 'Yes'")
    contacted_count = cursor.fetchone()["contacted"]

    cursor.execute("""
        SELECT COUNT(*) as due FROM contacts
        WHERE next_follow_up IS NOT NULL
          AND next_follow_up != ''
          AND next_follow_up <= ?
          AND status NOT IN ('Bounced', 'Do Not Contact', 'Closed Won', 'Closed Lost', 'Replied')
    """, (today_str,))
    followups_due = cursor.fetchone()["due"]

    cursor.execute("SELECT COUNT(*) as total_replied FROM contacts WHERE status IN ('Replied', 'Interested', 'Not Interested', 'Meeting Booked') OR tags LIKE '%Replied%'")
    total_replied = cursor.fetchone()["total_replied"]

    cursor.execute("SELECT COUNT(*) as total_interested FROM contacts WHERE status IN ('Interested', 'Meeting Booked') OR tags LIKE '%Interested%'")
    total_interested = cursor.fetchone()["total_interested"]

    # Emails stats
    cursor.execute("SELECT COUNT(*) as total_sent FROM emails WHERE status = 'Sent'")
    total_sent = cursor.fetchone()["total_sent"]

    cursor.execute("SELECT COUNT(*) as total_opened FROM emails WHERE status = 'Sent' AND open_count > 0")
    total_opened = cursor.fetchone()["total_opened"]

    cursor.execute("SELECT COUNT(*) as total_bounced FROM emails WHERE is_bounced = 1 OR status = 'Bounced'")
    total_bounced = cursor.fetchone()["total_bounced"]

    cursor.execute("SELECT COUNT(*) as total_clicked FROM emails WHERE status = 'Sent' AND click_count > 0")
    total_clicked = cursor.fetchone()["total_clicked"]

    conn.close()

    open_rate = round((total_opened / total_sent * 100), 1) if total_sent > 0 else 0.0
    bounce_rate = round((total_bounced / total_sent * 100), 1) if total_sent > 0 else 0.0
    reply_rate = round((total_replied / contacted_count * 100), 1) if contacted_count > 0 else (round((total_replied / total_sent * 100), 1) if total_sent > 0 else 0.0)
    positive_rate = round((total_interested / contacted_count * 100), 1) if contacted_count > 0 else (round((total_interested / total_sent * 100), 1) if total_sent > 0 else 0.0)
    click_rate = round((total_clicked / total_sent * 100), 1) if total_sent > 0 else 0.0
    ctor_rate = round((total_clicked / total_opened * 100), 1) if total_opened > 0 else 0.0

    return {
        "total_contacts": total_contacts,
        "contacted_count": contacted_count,
        "total_sent": total_sent,
        "total_opened": total_opened,
        "open_rate": open_rate,
        "total_bounced": total_bounced,
        "bounce_rate": bounce_rate,
        "total_replied": total_replied,
        "total_interested": total_interested,
        "positive_rate": positive_rate,
        "reply_rate": reply_rate,
        "total_clicked": total_clicked,
        "click_rate": click_rate,
        "ctor_rate": ctor_rate,
        "followups_due": followups_due
    }

def get_bounced_contacts(db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all contacts marked as bounced."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts WHERE status = 'Bounced' OR tags LIKE '%Bounced%' ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [_populate_contact_defaults(dict(r)) for r in rows]

# ------------------------------------------------------------------------------
# SMTP ACCOUNTS HELPERS (HOSTINGER / MULTI-ACCOUNT ROTATION)
# ------------------------------------------------------------------------------

# (get_warmup_info and get_effective_daily_limit imported from warmup.py)



def add_smtp_account(
    sender_name: str = "",
    email: str = "",
    password: str = "",
    smtp_host: str = "smtp.hostinger.com",
    smtp_port: int = 465,
    daily_limit: int = 80,
    is_active: bool = True,
    warmup_enabled: bool = False,
    warmup_start_date: Optional[str] = None,
    warmup_starting_limit: int = 10,
    warmup_daily_increment: int = 5,
    warmup_target_limit: int = 50,
    name: Optional[str] = None,
    smtp_password: Optional[str] = None,
    smtp_user: Optional[str] = None,
    warmup_start: Optional[int] = None,
    warmup_increment: Optional[int] = None,
    warmup_cap: Optional[int] = None,
    db_path: str = DB_FILE
) -> int:
    """Add a new SMTP account for Hostinger or custom mail server with optional automated warmup schedule."""
    final_name = (name or sender_name or "").strip()
    final_pw = (smtp_password or password or "").strip()
    final_email = email.strip()
    starting_limit = warmup_start if warmup_start is not None else warmup_starting_limit
    increment_limit = warmup_increment if warmup_increment is not None else warmup_daily_increment
    cap_limit = warmup_cap if warmup_cap is not None else warmup_target_limit

    now_iso = get_engine_now_str("%Y-%m-%d %H:%M:%S")
    today_str = get_engine_now_str("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    encrypted_pw = encrypt_smtp_password(final_pw)
    cursor.execute("""
        INSERT INTO smtp_accounts (
            sender_name, email, smtp_host, smtp_port, password,
            daily_limit, sent_today, last_reset_date, is_active,
            warmup_enabled, warmup_start_date, warmup_starting_limit,
            warmup_daily_increment, warmup_target_limit, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        final_name,
        final_email.lower(),
        smtp_host.strip(),
        int(smtp_port),
        encrypted_pw,

        int(daily_limit),
        today_str,
        1 if is_active else 0,
        1 if warmup_enabled else 0,
        (warmup_start_date or today_str).strip(),
        int(starting_limit),
        int(increment_limit),
        int(cap_limit),
        now_iso
    ))
    account_id = cursor.lastrowid
    conn.commit()
    conn.close()
    auto_save_backup(db_path)
    return account_id

create_smtp_account = add_smtp_account

def reset_daily_smtp_limits(db_path: str = DB_FILE, as_of_date: Optional[str] = None, force: bool = False) -> int:
    """
    Reset sent_today counters to 0 for all SMTP accounts.
    If force is True, resets sent_today = 0 for ALL accounts immediately.
    Otherwise, only resets accounts whose last_reset_date is not equal to today
    (strictly evaluated against the UTC+5 engine timeframe).
    Returns the number of accounts updated.
    """
    today_str = as_of_date or get_engine_now_str("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if force:
        cursor.execute("""
            UPDATE smtp_accounts
            SET sent_today = 0, last_reset_date = ?
        """, (today_str,))
    else:
        cursor.execute("""
            UPDATE smtp_accounts
            SET sent_today = 0, last_reset_date = ?
            WHERE last_reset_date != ? OR last_reset_date IS NULL OR last_reset_date = ''
        """, (today_str, today_str))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    if affected > 0:
        logger.info(f"reset_daily_smtp_limits (force={force}): Reset sent_today for {affected} mailbox(es) for date {today_str}.")
    return affected

def get_smtp_accounts(active_only: bool = False, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all or active SMTP sender accounts, hydrating decrypted passwords and refreshing daily limits."""
    reset_daily_smtp_limits(db_path=db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if active_only:
        cursor.execute("SELECT * FROM smtp_accounts WHERE is_active = 1 ORDER BY id ASC")
    else:
        cursor.execute("SELECT * FROM smtp_accounts ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [_hydrate_smtp_account(dict(r)) for r in rows]

def get_smtp_account_by_id(account_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    reset_daily_smtp_limits(db_path=db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,))
    row = cursor.fetchone()
    conn.close()
    return _hydrate_smtp_account(dict(row)) if row else None

def update_smtp_account(
    account_id: int,
    sender_name: Optional[str] = None,
    email: Optional[str] = None,
    password: Optional[str] = None,
    smtp_host: Optional[str] = None,
    smtp_port: Optional[int] = None,
    daily_limit: Optional[int] = None,
    is_active: Optional[bool] = None,
    warmup_enabled: Optional[bool] = None,
    warmup_start_date: Optional[str] = None,
    warmup_starting_limit: Optional[int] = None,
    warmup_daily_increment: Optional[int] = None,
    warmup_target_limit: Optional[int] = None,
    db_path: str = DB_FILE
):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    fields = []
    values = []
    if sender_name is not None:
        fields.append("sender_name = ?")
        values.append(sender_name.strip())
    if email is not None:
        fields.append("email = ?")
        values.append(email.strip().lower())
    if password is not None and password.strip():
        encrypted_pw = encrypt_smtp_password(password.strip())
        fields.append("password = ?")
        values.append(encrypted_pw)
    if smtp_host is not None:
        fields.append("smtp_host = ?")
        values.append(smtp_host.strip())
    if smtp_port is not None:
        fields.append("smtp_port = ?")
        values.append(int(smtp_port))
    if daily_limit is not None:
        fields.append("daily_limit = ?")
        values.append(int(daily_limit))
    if is_active is not None:
        fields.append("is_active = ?")
        values.append(1 if is_active else 0)
    if warmup_enabled is not None:
        fields.append("warmup_enabled = ?")
        values.append(1 if warmup_enabled else 0)
    if warmup_start_date is not None:
        fields.append("warmup_start_date = ?")
        values.append(warmup_start_date.strip())
    if warmup_starting_limit is not None:
        fields.append("warmup_starting_limit = ?")
        values.append(int(warmup_starting_limit))
    if warmup_daily_increment is not None:
        fields.append("warmup_daily_increment = ?")
        values.append(int(warmup_daily_increment))
    if warmup_target_limit is not None:
        fields.append("warmup_target_limit = ?")
        values.append(int(warmup_target_limit))

    if fields:
        values.append(account_id)
        query = f"UPDATE smtp_accounts SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(query, tuple(values))
        conn.commit()
    conn.close()
    auto_save_backup(db_path)

def delete_smtp_account(account_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM smtp_accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    auto_save_backup(db_path)

def get_next_available_smtp_account(db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """
    Get the next available active SMTP account that has not exceeded its effective daily limit
    (respecting automated mailbox warmup ramp-up schedules).
    Automatically resets sent_today counter when the date rolls over.
    Selects the account with the lowest sent_today to balance load across mailboxes.
    """
    today_str = get_engine_now_str("%Y-%m-%d")
    reset_daily_smtp_limits(db_path=db_path, as_of_date=today_str)

    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM smtp_accounts
        WHERE is_active = 1
        ORDER BY sent_today ASC, id ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    for r in rows:
        acc = _hydrate_smtp_account(dict(r))
        eff_limit = get_effective_daily_limit(acc, today_str=today_str)
        if acc["sent_today"] < eff_limit:
            acc["effective_daily_limit"] = eff_limit
            return acc

    return None

def increment_smtp_sent(account_id: int, db_path: str = DB_FILE):
    """Increment sent_today counter for an SMTP account, ensuring proper daily rollover."""
    today_str = get_engine_now_str("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE smtp_accounts
        SET sent_today = CASE WHEN last_reset_date = ? THEN sent_today + 1 ELSE 1 END,
            last_reset_date = ?
        WHERE id = ?
    """, (today_str, today_str, account_id))
    conn.commit()
    conn.close()

def generate_campaign_drafts(
    contact_ids: List[int],
    template_id: int,
    subject_template: str = "",
    sending_days: Optional[List[str]] = None,
    start_time_str: str = "09:00",
    end_time_str: str = "18:00",
    spacing_minutes: int = 5,
    auto_stagger: bool = True,
    base_start_dt: Optional[datetime] = None,
    db_path: str = DB_FILE
) -> Dict[str, Any]:
    """
    Deterministically generate personalized outreach draft emails for target contacts.
    1. Injects recipient dynamic variables ([Name], [Company], custom variables).
    2. Resolves Spintax {option1|option2}.
    3. Formats paragraphs in clean HTML <p> tags.
    4. Evaluates copy against negative keyword guardrails (flags if detected).
    5. Calculates staggered send times adhering to sending window boundaries.
    6. Persists drafts to SQLite emails table.
    """
    from template_engine import (
        resolve_template,
        format_email_html,
        scan_negative_keywords,
        scan_all_negative_keywords,
        parse_spintax,
        inject_variables
    )

    template = get_template_by_id(template_id, db_path=db_path)
    if not template:
        raise ValueError(f"Template ID #{template_id} does not exist.")

    neg_keywords_setting = get_config("negative_keywords", "", db_path=db_path) or ""

    first_slot = get_next_valid_sending_datetime(
        base_dt=base_start_dt or datetime.now(),
        sending_days=sending_days,
        start_time_str=start_time_str,
        end_time_str=end_time_str,
        db_path=db_path
    )

    created_ids = []
    created_pending = 0
    created_flagged = 0
    last_sched_dt = first_slot

    for idx, cid in enumerate(contact_ids):
        contact = get_contact_by_id(cid, db_path=db_path)
        if not contact:
            continue

        # 1. Deterministic Variable Injection & Spintax Resolution
        resolved_body = resolve_template(template["body_content"], contact)
        final_html = format_email_html(resolved_body)

        # 2. Subject Line Resolution with Variables & Spintax
        subj_template = subject_template.strip() if subject_template.strip() else template["template_name"]
        final_subject = parse_spintax(inject_variables(subj_template, contact))

        # 3. Negative Keyword Guardrail
        combined_text = f"{final_subject} {final_html}"
        detected_triggers = scan_all_negative_keywords(combined_text, neg_keywords_setting)

        if detected_triggers:
            status = "Flagged"
            trig_str = ", ".join(f"'{t}'" for t in detected_triggers)
            reason = f"Automated Scan Alert: Negative keyword(s) {trig_str} detected in copy."
            created_flagged += 1
        else:
            status = "Draft"
            reason = None
            created_pending += 1

        # 4. Stagger schedule time if requested
        if idx == 0 or not auto_stagger:
            sched_time = last_sched_dt
        else:
            next_cand = last_sched_dt + timedelta(minutes=int(spacing_minutes))
            sched_time = get_next_valid_sending_datetime(
                base_dt=next_cand,
                sending_days=sending_days,
                start_time_str=start_time_str,
                end_time_str=end_time_str,
                db_path=db_path
            )
            last_sched_dt = sched_time

        eid = create_email(
            email_html=final_html,
            subject=final_subject,
            recipient=contact["email"],
            status=status,
            revision_notes=reason,
            scheduled_time=sched_time.strftime("%Y-%m-%d %H:%M:%S"),
            db_path=db_path
        )
        created_ids.append(eid)

    return {
        "created_count": len(created_ids),
        "pending_count": created_pending,
        "flagged_count": created_flagged,
        "email_ids": created_ids
    }


# ------------------------------------------------------------------------------
# 1:1 TARGETED OUTREACH THREAD & PIPELINE HELPERS
# ------------------------------------------------------------------------------

def get_thread_history(contact_email: str, db_path: str = DB_FILE) -> Dict[str, Any]:
    """
    Fetch the complete chronological conversation timeline for a single contact:
    - contact: Dict representation of contact row with custom_variables parsed
    - emails: List of email rows sent, scheduled, or drafted for this contact
    - notifications: List of reply/inbox notification rows for this contact
    - sequence_rules: List of active/completed sequence rules
    - timeline: Chronological list of events (sent emails, scheduled emails, drafts, replies received)
    """
    clean_email = (contact_email or "").strip().lower()
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Contact record
    cursor.execute("SELECT * FROM contacts WHERE LOWER(TRIM(email)) = ? LIMIT 1", (clean_email,))
    row = cursor.fetchone()
    contact = dict(row) if row else None
    if contact:
        cv_val = contact.get("custom_variables") or "{}"
        if isinstance(cv_val, str):
            try:
                contact["custom_variables_dict"] = json.loads(cv_val)
            except Exception:
                contact["custom_variables_dict"] = {}
        elif isinstance(cv_val, dict):
            contact["custom_variables_dict"] = cv_val
        else:
            contact["custom_variables_dict"] = {}

    # 2. Email history
    cursor.execute("""
        SELECT * FROM emails
        WHERE LOWER(TRIM(recipient)) = ?
        ORDER BY created_at ASC, id ASC
    """, (clean_email,))
    emails = [dict(r) for r in cursor.fetchall()]

    # 3. Notification / reply history
    cursor.execute("""
        SELECT * FROM notifications
        WHERE LOWER(TRIM(contact_email)) = ?
        ORDER BY created_at ASC, id ASC
    """, (clean_email,))
    notifications = [dict(r) for r in cursor.fetchall()]

    # 4. Sequence rules
    cursor.execute("""
        SELECT * FROM sequence_rules
        WHERE LOWER(TRIM(contact_email)) = ?
        ORDER BY step_number ASC, id ASC
    """, (clean_email,))
    sequence_rules = [dict(r) for r in cursor.fetchall()]

    conn.close()

    # 5. Build chronological timeline
    timeline = []
    for em in emails:
        st_val = (em.get("status") or "").lower()
        if st_val == "sent":
            item_type = "email_sent"
            item_dt = em.get("updated_at") or em.get("created_at") or ""
        elif st_val in ["approved", "scheduled"] or em.get("scheduled_time"):
            item_type = "email_scheduled"
            item_dt = em.get("scheduled_time") or em.get("created_at") or ""
        elif st_val in ["bounced", "error"]:
            item_type = "email_error"
            item_dt = em.get("updated_at") or em.get("created_at") or ""
        else:
            item_type = "email_draft"
            item_dt = em.get("created_at") or ""

        timeline.append({
            "type": item_type,
            "timestamp": item_dt,
            "subject": em.get("subject") or "(No Subject)",
            "body": em.get("email_html") or "",
            "step": em.get("sequence_step") or 1,
            "status": em.get("status") or "Draft",
            "message_id": em.get("message_id") or "",
            "in_reply_to": em.get("in_reply_to") or "",
            "open_count": em.get("open_count") or 0,
            "click_count": em.get("click_count") or 0,
            "opened_at": em.get("opened_at") or "",
            "id": em.get("id"),
            "raw": em
        })

    for notif in notifications:
        timeline.append({
            "type": "reply_received",
            "timestamp": notif.get("created_at") or "",
            "subject": notif.get("title") or "Incoming Reply",
            "body": notif.get("message") or "",
            "step": None,
            "status": "Received",
            "message_id": "",
            "in_reply_to": "",
            "open_count": 0,
            "click_count": 0,
            "opened_at": "",
            "id": notif.get("id"),
            "raw": notif
        })

    # Sort timeline by timestamp ascending
    timeline.sort(key=lambda x: str(x.get("timestamp") or ""))

    return {
        "contact": contact,
        "emails": emails,
        "notifications": notifications,
        "sequence_rules": sequence_rules,
        "timeline": timeline
    }


def get_targeted_pipeline_leads(db_path: str = DB_FILE) -> Dict[str, List[Dict[str, Any]]]:
    """
    Categorizes contacts into the 5 core Targeted Outreach pipeline stages:
    - 'drafting': Has draft emails or brand new contact without sent emails.
    - 'scheduled_sent': Initial or follow-up sent / scheduled, actively awaiting prospect response.
    - 'followup_due': Next follow-up is scheduled for today or overdue, or active sequence rule is due.
    - 'replied': Prospect sent an incoming reply, awaiting user action.
    - 'closed': Closed terminal status (Interested, Meeting Booked, Not Interested, Bounced, Do Not Contact).
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM contacts ORDER BY id DESC")
    all_contacts_raw = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM emails ORDER BY id ASC")
    all_emails = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM notifications ORDER BY id ASC")
    all_notifs = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM sequence_rules WHERE status = 'Scheduled' ORDER BY id ASC")
    due_rules = [dict(r) for r in cursor.fetchall()]

    conn.close()

    emails_by_contact: Dict[str, List[Dict[str, Any]]] = {}
    for em in all_emails:
        rcp = (em.get("recipient") or "").strip().lower()
        if rcp:
            emails_by_contact.setdefault(rcp, []).append(em)

    notifs_by_contact: Dict[str, List[Dict[str, Any]]] = {}
    for n in all_notifs:
        ce = (n.get("contact_email") or "").strip().lower()
        if ce:
            notifs_by_contact.setdefault(ce, []).append(n)

    rules_by_contact: Dict[str, List[Dict[str, Any]]] = {}
    for r in due_rules:
        ce = (r.get("contact_email") or "").strip().lower()
        if ce:
            rules_by_contact.setdefault(ce, []).append(r)

    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    now_date_str = now_iso[:10]

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "drafting": [],
        "scheduled_sent": [],
        "followup_due": [],
        "replied": [],
        "closed": []
    }

    closed_statuses = {
        "interested", "not interested", "meeting booked", "bounced",
        "do not contact", "closed won", "closed lost"
    }

    for c in all_contacts_raw:
        c_email = (c.get("email") or "").strip().lower()
        c_status = (c.get("status") or "New").strip().lower()
        c_tags = (c.get("tags") or "").lower()

        c_emails = emails_by_contact.get(c_email, [])
        sent_emails = [e for e in c_emails if (e.get("status") or "").lower() == "sent"]
        scheduled_emails = [e for e in c_emails if (e.get("status") or "").lower() in ["approved", "scheduled"]]
        draft_emails = [e for e in c_emails if (e.get("status") or "").lower() in ["pending", "draft", "flagged", "needs review"]]
        c_notifs = notifs_by_contact.get(c_email, [])
        unread_notifs = [n for n in c_notifs if n.get("is_read") == 0]
        c_rules = rules_by_contact.get(c_email, [])

        lead_summary = {
            "id": c.get("id"),
            "name": c.get("name") or "Unnamed Lead",
            "company": c.get("company") or "",
            "email": c.get("email") or "",
            "status": c.get("status") or "New",
            "last_contact_date": c.get("last_contact_date") or (sent_emails[-1]["created_at"][:10] if sent_emails else ""),
            "next_follow_up": c.get("next_follow_up") or (c_rules[0]["due_at"][:10] if c_rules and c_rules[0].get("due_at") else ""),
            "emails_sent_count": len(sent_emails),
            "drafts_count": len(draft_emails),
            "scheduled_count": len(scheduled_emails),
            "unread_replies_count": len(unread_notifs),
            "last_subject": sent_emails[-1]["subject"] if sent_emails else (draft_emails[-1]["subject"] if draft_emails else ""),
            "raw_contact": c
        }

        # 1. Replied (needs user action)
        if len(unread_notifs) > 0 or c_status == "replied" or "replied" in c_tags:
            buckets["replied"].append(lead_summary)
            continue

        # 2. Closed outcomes
        if c_status in closed_statuses or any(cs in c_tags for cs in ["bounced", "do not contact", "opted-out", "meeting booked"]):
            buckets["closed"].append(lead_summary)
            continue

        # 3. Follow-up Due
        is_fu_due = False
        if c_rules:
            for r in c_rules:
                due_at = r.get("due_at") or ""
                if due_at and due_at <= now_iso:
                    is_fu_due = True
                    break
        if not is_fu_due and c.get("next_follow_up"):
            nfu = c["next_follow_up"].strip()
            if nfu and nfu <= now_date_str and len(sent_emails) > 0:
                is_fu_due = True

        if is_fu_due:
            buckets["followup_due"].append(lead_summary)
            continue

        # 4. Scheduled / Sent (actively in progress awaiting response)
        if len(scheduled_emails) > 0 or len(sent_emails) > 0:
            buckets["scheduled_sent"].append(lead_summary)
            continue

        # 5. Drafting (draft created or not contacted yet)
        buckets["drafting"].append(lead_summary)

    return buckets

# Initialize upon import if DB does not exist
if not os.path.exists(DB_FILE):
    init_db(DB_FILE)
else:
    # Ensure any new tables / migrations are applied
    try:
        init_db(DB_FILE)
    except Exception:
        pass
