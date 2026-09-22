"""
database.py - SQLite persistence layer for the local email automation system.
Manages system configuration, contacts CRM, outreach templates, spam/negative keyword lists, and email queue.
"""

import sys
import sqlite3
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union, Tuple
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

def get_or_create_encryption_key() -> bytes:
    """
    Retrieve or generate a 256-bit Fernet key stored securely in the OS keychain via keyring.
    Falls back gracefully to Windows DPAPI if keyring is unavailable or restricted.
    """
    if KEYRING_AVAILABLE and keyring:
        try:
            stored_key = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)
            if stored_key:
                return stored_key.encode("utf-8")
            if Fernet:
                new_key = Fernet.generate_key().decode("utf-8")
                keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, new_key)
                return new_key.encode("utf-8")
        except Exception as e:
            logger.warning(f"Keyring access error: {e}. Falling back to DPAPI.")

    if DPAPI_AVAILABLE and win32crypt and Fernet:
        key_file = _get_dpapi_key_file_path()
        try:
            if os.path.exists(key_file):
                with open(key_file, "rb") as f:
                    encrypted_data = f.read()
                decrypted = win32crypt.CryptUnprotectData(encrypted_data, None, None, None, 0)[1]
                return decrypted
            else:
                new_key = Fernet.generate_key()
                protected_data = win32crypt.CryptProtectData(new_key, "SellomizeKey", None, None, None, 0)
                with open(key_file, "wb") as f:
                    f.write(protected_data)
                return new_key
        except Exception as e:
            logger.warning(f"DPAPI key management error: {e}")

    logger.warning("Neither Keyring nor DPAPI available. Using local ephemeral fallback key.")
    if Fernet:
        return b"4XW7c1o3K9nL0pQ_vRtY2uI5eA8sD6fG1hJ4kL7zX9c="
    return b"fallback_insecure_key_32_bytes_!"

def encrypt_smtp_password(plain_password: str) -> str:
    """
    Encrypt plaintext password using Fernet symmetric encryption.
    Returns ciphertext string starting with 'gAAAAA'.
    """
    if not plain_password:
        return ""
    if not CRYPTOGRAPHY_AVAILABLE or not Fernet:
        logger.warning("Cryptography library not available; returning plaintext.")
        return plain_password
    try:
        key = get_or_create_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(plain_password.strip().encode("utf-8"))
        return encrypted.decode("utf-8")
    except Exception as e:
        logger.error(f"Error encrypting password: {e}")
        return plain_password

def decrypt_smtp_password(raw_value: str) -> Tuple[str, bool]:
    """
    Decrypt an SMTP password stored in SQLite.
    Returns (decrypted_password, is_undecryptable).
    - If raw_value is empty: returns ('', False)
    - If raw_value is legacy plaintext (not starting with 'gAAAAA'): returns (raw_value, False)
    - If decryption fails (corrupted token or DB moved across machines): returns ('', True)
    """
    if not raw_value or not raw_value.strip():
        return "", False
    val = raw_value.strip()
    if not val.startswith("gAAAAA"):
        # Legacy unencrypted plaintext password
        return val, False
    if not CRYPTOGRAPHY_AVAILABLE or not Fernet:
        logger.warning("Cryptography library not available to decrypt password.")
        return "", True
    try:
        key = get_or_create_encryption_key()
        f = Fernet(key)
        decrypted = f.decrypt(val.encode("utf-8")).decode("utf-8")
        return decrypted, False
    except (InvalidToken, Exception) as e:
        logger.warning(f"Failed to decrypt SMTP password with current OS keychain key: {e}")
        return "", True

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
        ("status", "TEXT DEFAULT 'Not Contacted'"),
        ("follow_ups_sent", "INTEGER DEFAULT 0"),
        ("last_contact_date", "TEXT DEFAULT ''"),
        ("next_follow_up", "TEXT DEFAULT ''"),
        ("owner", "TEXT DEFAULT ''"),
        ("notes", "TEXT DEFAULT ''"),
        ("last_reply_at", "TEXT DEFAULT ''"),
        ("reply_subject", "TEXT DEFAULT ''"),
    ]
    for col_name, col_def in contact_migrations:
        try:
            valid_col = validate_identifier(col_name)
            cursor.execute(f"ALTER TABLE contacts ADD COLUMN {valid_col} {col_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.warning(f"OperationalError during contacts migration for {col_name}: {e}")

    # 3. Templates table (Reusable Spintax & Variable templates)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_name TEXT NOT NULL,
            body_content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

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
        ("market_key", "TEXT DEFAULT ''")
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
        ("custom_body", "TEXT DEFAULT ''")
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

    # Populate default configuration keys if not already present
    default_configs = {
        "dispatch_method": "hostinger_smtp",
        "min_delay_seconds": "20",
        "max_delay_seconds": "45",
        "sender_email": "",
        "bcc_email": "",
        "schedule_mode": "adaptive_multi_country",
        "default_market": "CA_EAST",
        "negative_keywords": "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash",
        "signature_html": "<p>Best regards,<br><strong>Listing Audit Team</strong><br><a href='https://example.com'>example.com</a></p>",
        "sending_days": "Monday,Tuesday,Wednesday,Thursday,Friday",
        "sending_start_time": "09:00",
        "sending_end_time": "18:00",
        "enforce_sending_window": "true"
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
            INSERT INTO contacts (name, email, company, tags, custom_variables, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("Sarah Jenkins", "sarah@apexoutdoors.com", "Apex Outdoors", "Listing Audit, Outdoor Brands", json.dumps({"Niche": "Outdoor Gear", "Role": "Founder"}), now_iso))
        cursor.execute("""
            INSERT INTO contacts (name, email, company, tags, custom_variables, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("Elena Rostova", "elena@skinfix.com", "Skinfix", "Beauty Brands, Q4 Leads", json.dumps({"Niche": "Skincare", "Role": "Brand Director"}), now_iso))
        cursor.execute("""
            INSERT INTO contacts (name, email, company, tags, custom_variables, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("Marcus Brody", "marcus@minoribeauty.com", "Minori Beauty", "Beauty Brands, Listing Audit", json.dumps({"Niche": "Cosmetics", "Role": "E-commerce Head"}), now_iso))

    conn.commit()
    conn.close()

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
    status: str = "Not Contacted",
    follow_ups_sent: int = 0,
    last_contact_date: str = "",
    next_follow_up: str = "",
    owner: str = "",
    notes: str = "",
    db_path: str = DB_FILE
) -> int:
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    vars_json = json.dumps(custom_variables or {})
    tags_str = _normalize_tags(tags)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contacts (
            name, email, company, tags, custom_variables,
            lead_source, priority, contacted, date_first_emailed,
            status, follow_ups_sent, last_contact_date, next_follow_up,
            owner, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name.strip(), email.strip(), company.strip(), tags_str, vars_json,
        (lead_source or "Other").strip(), (priority or "Medium").strip(),
        (contacted or "No").strip(), (date_first_emailed or "").strip(),
        (status or "Not Contacted").strip(), int(follow_ups_sent or 0),
        (last_contact_date or "").strip(), (next_follow_up or "").strip(),
        (owner or "").strip(), (notes or "").strip(), now_iso
    ))
    contact_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return contact_id

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
        values.append(status.strip())
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
    db_path: str = DB_FILE
) -> (int, bool):
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
            status=status or "Not Contacted",
            follow_ups_sent=follow_ups_sent if follow_ups_sent is not None else 0,
            last_contact_date=last_contact_date or "",
            next_follow_up=next_follow_up or "",
            owner=owner or "",
            notes=notes or "",
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
        email = str(rec.get("email") or rec.get("Email Address") or "").strip()
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
    curr_status = row["status"] or "Not Contacted"

    if curr_status in ["Not Contacted", "", None]:
        new_status = "Contacted"
    elif curr_status in ["Contacted", "Follow-Up Sent"]:
        new_status = "Follow-Up Sent"
    else:
        new_status = curr_status

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

# ------------------------------------------------------------------------------
# TEMPLATES HELPERS
# ------------------------------------------------------------------------------

def create_template(
    template_name: str,
    body_content: str,
    db_path: str = DB_FILE
) -> int:
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO templates (template_name, body_content, created_at)
        VALUES (?, ?, ?)
    """, (template_name.strip(), body_content.strip(), now_iso))
    tpl_id = cursor.lastrowid
    conn.commit()
    conn.close()
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
    template_name: str,
    body_content: str,
    db_path: str = DB_FILE
):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE templates SET template_name = ?, body_content = ?
        WHERE id = ?
    """, (template_name.strip(), body_content.strip(), template_id))
    conn.commit()
    conn.close()

def delete_template(template_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()

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
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        subject, recipient, email_html, status, scheduled_time,
        variation_num, revision_notes, sequence_step, sequence_id,
        target_timezone.strip(), target_country.strip(), market_key.strip(),
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
    db_path: str = DB_FILE
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
        current_time_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

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

def mark_email_sent(email_id: int, sent_at: Optional[str] = None, db_path: str = DB_FILE):
    now_iso = sent_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    update_email(email_id=email_id, status="Sent", error_message=None, db_path=db_path)
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
    Called when an incoming reply from a contact/lead is detected:
    1. Updates contact status to 'Replied' and tags with 'Replied'.
    2. Records reply timestamp and notes.
    3. Automatically cancels all pending/approved follow-up emails queued for this contact.
    Returns details of affected contacts and cancelled emails.
    """
    clean_email = sender_email.strip().lower()
    if not clean_email:
        return {"contact_found": False, "cancelled_drafts_count": 0, "cancelled_email_ids": []}

    now_iso = received_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Update contact(s)
    cursor.execute("SELECT id, name, tags, notes, status FROM contacts WHERE LOWER(TRIM(email)) = ?", (clean_email,))
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

    # 2. Auto-cancel queued sequence follow-up touches (sequence_step > 1) for this contact.
    # One-time emails (sequence_step <= 1, single outreach, or marketing campaigns) are preserved
    # so users can freely send single 1-to-1 emails or marketing campaign blasts after a reply.
    cursor.execute("""
        SELECT id FROM emails
        WHERE LOWER(TRIM(recipient)) = ? 
          AND status IN ('Pending', 'Approved', 'Flagged')
          AND sequence_step > 1
    """, (clean_email,))
    pending_emails = cursor.fetchall()
    cancelled_ids = [r["id"] for r in pending_emails]

    if cancelled_ids:
        cursor.execute("""
            UPDATE emails SET
                status = 'Cancelled',
                error_message = 'Auto-cancelled: Prospect replied to outreach (sequence follow-up cancelled; one-time mails preserved)'
            WHERE LOWER(TRIM(recipient)) = ? 
              AND status IN ('Pending', 'Approved', 'Flagged')
              AND sequence_step > 1
        """, (clean_email,))

    # Cancel any pending follow-up sequence rules for this contact
    cursor.execute("""
        UPDATE sequence_rules SET status = 'Cancelled'
        WHERE LOWER(TRIM(contact_email)) = ? AND status IN ('Waiting_Trigger', 'Scheduled')
    """, (clean_email,))

    # 3. Record persistent in-app reply notification (deduplicated against existing unread alerts)
    disp_name = contact_names[0] if contact_names else clean_email
    notif_title = f"💬 New Reply from {disp_name}"
    subj_part = f" ('{reply_subject[:45]}')" if reply_subject else ""
    canc_part = f" — {len(cancelled_ids)} scheduled sequence follow-up(s) auto-cancelled (one-time mails preserved)." if cancelled_ids else " (one-time emails preserved)."
    notif_body = f"Prospect {clean_email} responded to outreach{subj_part}{canc_part}"

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
            VALUES (?, ?, ?, ?, 0, ?)
        """, ("reply", notif_title, notif_body, clean_email, now_iso))

    conn.commit()
    conn.close()

    return {
        "contact_found": contact_found,
        "contact_names": contact_names,
        "cancelled_drafts_count": len(cancelled_ids),
        "cancelled_email_ids": cancelled_ids,
        "sender_email": clean_email
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

    cursor.execute("SELECT COUNT(*) as total_replied FROM contacts WHERE status = 'Replied' OR tags LIKE '%Replied%'")
    total_replied = cursor.fetchone()["total_replied"]

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

def get_warmup_info(account: Dict[str, Any], today_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Single source of truth for warmup progress and effective daily sending limits.
    Returns:
        {
            "is_warmup": bool,
            "day_num": int,
            "effective_limit": int,
            "target_limit": int,
            "days_elapsed": int
        }
    """
    is_warmup = bool(account.get("warmup_enabled"))
    target_limit = int(account.get("warmup_target_limit") or account.get("daily_limit") or 50)
    start_lim = int(account.get("warmup_starting_limit") if account.get("warmup_starting_limit") is not None else 10)
    inc = int(account.get("warmup_daily_increment") if account.get("warmup_daily_increment") is not None else 5)

    if not is_warmup:
        eff = int(account.get("daily_limit", 50))
        return {
            "is_warmup": False,
            "day_num": 1,
            "effective_limit": eff,
            "target_limit": target_limit,
            "days_elapsed": 0
        }

    start_date_str = (account.get("warmup_start_date") or "").strip()
    if not start_date_str:
        eff = int(account.get("daily_limit", 50))
        return {
            "is_warmup": True,
            "day_num": 1,
            "effective_limit": eff,
            "target_limit": target_limit,
            "days_elapsed": 0
        }

    try:
        start_date = datetime.strptime(start_date_str.split()[0], "%Y-%m-%d").date()
        today = datetime.strptime(today_str, "%Y-%m-%d").date() if today_str else datetime.now().astimezone().date()
        days_elapsed = max(0, (today - start_date).days)
        day_num = days_elapsed + 1
        effective_limit = min(target_limit, start_lim + (days_elapsed * inc))
        return {
            "is_warmup": True,
            "day_num": day_num,
            "effective_limit": effective_limit,
            "target_limit": target_limit,
            "days_elapsed": days_elapsed
        }
    except (ValueError, TypeError, AttributeError) as d_err:
        logger.warning(f"Error calculating warmup info: {d_err}. Falling back to default daily limit.")
        return {
            "is_warmup": True,
            "day_num": 1,
            "effective_limit": int(account.get("daily_limit", 50)),
            "target_limit": target_limit,
            "days_elapsed": 0
        }

def get_effective_daily_limit(account: Dict[str, Any], today_str: Optional[str] = None) -> int:
    """Calculate the active daily sending cap for an SMTP account using get_warmup_info."""
    return get_warmup_info(account, today_str=today_str)["effective_limit"]


def add_smtp_account(
    sender_name: str,
    email: str,
    password: str,
    smtp_host: str = "smtp.hostinger.com",
    smtp_port: int = 465,
    daily_limit: int = 80,
    is_active: bool = True,
    warmup_enabled: bool = False,
    warmup_start_date: Optional[str] = None,
    warmup_starting_limit: int = 10,
    warmup_daily_increment: int = 5,
    warmup_target_limit: int = 50,
    db_path: str = DB_FILE
) -> int:
    """Add a new SMTP account for Hostinger or custom mail server with optional automated warmup schedule."""
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    encrypted_pw = encrypt_smtp_password(password.strip())
    cursor.execute("""
        INSERT INTO smtp_accounts (
            sender_name, email, smtp_host, smtp_port, password,
            daily_limit, sent_today, last_reset_date, is_active,
            warmup_enabled, warmup_start_date, warmup_starting_limit,
            warmup_daily_increment, warmup_target_limit, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sender_name.strip(),
        email.strip().lower(),
        smtp_host.strip(),
        int(smtp_port),
        encrypted_pw,
        int(daily_limit),
        today_str,
        1 if is_active else 0,
        1 if warmup_enabled else 0,
        (warmup_start_date or today_str).strip(),
        int(warmup_starting_limit),
        int(warmup_daily_increment),
        int(warmup_target_limit),
        now_iso
    ))
    account_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return account_id

def get_smtp_accounts(active_only: bool = False, db_path: str = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieve all or active SMTP sender accounts, hydrating decrypted passwords."""
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

def delete_smtp_account(account_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM smtp_accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

def get_next_available_smtp_account(db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    """
    Get the next available active SMTP account that has not exceeded its effective daily limit
    (respecting automated mailbox warmup ramp-up schedules).
    Automatically resets sent_today counter when the date rolls over.
    Selects the account with the lowest sent_today to balance load across mailboxes.
    """
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Reset counters for accounts from previous days
    cursor.execute("""
        UPDATE smtp_accounts
        SET sent_today = 0, last_reset_date = ?
        WHERE last_reset_date != ?
    """, (today_str, today_str))
    conn.commit()

    # 2. Find active accounts where sent_today < effective daily limit
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
    """Increment sent_today counter for an SMTP account."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    cursor.execute("""
        UPDATE smtp_accounts
        SET sent_today = sent_today + 1, last_reset_date = ?
        WHERE id = ?
    """, (today_str, account_id))
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

# Initialize upon import if DB does not exist
if not os.path.exists(DB_FILE):
    init_db(DB_FILE)
else:
    # Ensure any new tables / migrations are applied
    try:
        init_db(DB_FILE)
    except Exception:
        pass
