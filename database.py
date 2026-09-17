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

def get_db_path() -> str:
    """
    Determine SQLite database location.
    When running in a packaged PyInstaller executable (frozen mode), store in
    %APPDATA%/SellomizeReach/email_system.db so user data persists across updates.
    In development mode, use local email_system.db.
    """
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
            cursor.execute(f"ALTER TABLE contacts ADD COLUMN {col_name} {col_def}")
        except Exception:
            pass

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
        ("last_clicked_url", "TEXT DEFAULT ''")
    ]
    for col_name, col_def in email_migrations:
        try:
            cursor.execute(f"ALTER TABLE emails ADD COLUMN {col_name} {col_def}")
        except Exception:
            pass

    # High-performance database indexes for sub-millisecond query execution
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_status_sched ON emails(status, scheduled_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_recipient ON emails(recipient)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_click ON emails(click_count)")
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
            cursor.execute(f"ALTER TABLE smtp_accounts ADD COLUMN {col_name} {col_def}")
        except Exception:
            pass

    # Populate default configuration keys if not already present
    default_configs = {
        "gemini_api_key": "AQ.Ab8RN6JyptGhhfk8w83PSpKVcFpmNJOA7aoEJtiB2BCEEiuwVw",
        "gcp_project_id": "606768026327",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "primary_model": "gemini/gemini-1.5-flash",
        "fallback_model": "gpt-4o-mini",
        "dispatch_method": "hostinger_smtp",
        "min_delay_seconds": "20",
        "max_delay_seconds": "45",
        "sender_email": "",
        "bcc_email": "",
        "spam_blocklist": "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast",
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
    """
    Evaluate whether a given datetime (or current local time) falls inside the
    configured campaign sending window and allowed business days.
    Returns (True, message) if dispatch is allowed, or (False, reason) if paused.
    """
    enforce_str = get_config("enforce_sending_window", "true", db_path=db_path) or "true"
    enforce = enforce_str.strip().lower() in ["true", "1", "yes", "on"]
    if not enforce:
        return True, "Sending window enforcement disabled (24/7 delivery allowed)"

    dt = check_dt or datetime.now().astimezone()
    if dt.tzinfo is None:
        dt = dt.astimezone()

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
    except Exception:
        sh, sm, eh, em = 9, 0, 18, 0

    curr_mins = dt.hour * 60 + dt.minute
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    if curr_mins < start_mins:
        return False, f"Current time ({dt.strftime('%H:%M')}) is before daily start time ({start_str})"
    if curr_mins >= end_mins:
        return False, f"Current time ({dt.strftime('%H:%M')}) is past daily cutoff time ({end_str})"

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
    dt = base_dt or datetime.now().astimezone()
    if dt.tzinfo is None:
        dt = dt.astimezone()

    if delay_minutes > 0:
        dt = dt + timedelta(minutes=delay_minutes)

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
    except Exception:
        sh, sm, eh, em = 9, 0, 18, 0

    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    # Loop up to 14 days to find next valid time slot
    for _ in range(14):
        day_name = dt.strftime("%A")
        curr_mins = dt.hour * 60 + dt.minute

        if day_name in allowed_days:
            if curr_mins < start_mins:
                # Before start time today -> snap to start time today
                return dt.replace(hour=sh, minute=sm, second=0, microsecond=0)
            elif curr_mins < end_mins:
                # Within window today -> use as is
                return dt
            else:
                # Past end time today -> advance to tomorrow at start time
                tomorrow = dt + timedelta(days=1)
                dt = tomorrow.replace(hour=sh, minute=sm, second=0, microsecond=0)
        else:
            # Non-sending day -> advance to tomorrow at start time
            tomorrow = dt + timedelta(days=1)
            dt = tomorrow.replace(hour=sh, minute=sm, second=0, microsecond=0)

    return dt

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
    except Exception:
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
    except Exception:
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
        except Exception:
            pass
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
        except Exception:
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
            except Exception:
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
    db_path: str = DB_FILE
) -> int:
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO emails (subject, recipient, email_html, status, scheduled_time, variation_num, revision_notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (subject, recipient, email_html, status, scheduled_time, variation_num, revision_notes, now_iso, now_iso))
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

def update_email(
    email_id: int,
    email_html: Optional[str] = None,
    subject: Optional[str] = None,
    recipient: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    status: Optional[str] = None,
    revision_notes: Optional[str] = None,
    error_message: Optional[str] = None,
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
    if revision_notes is not None:
        fields.append("revision_notes = ?")
        values.append(revision_notes)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)

    values.append(email_id)
    query = f"UPDATE emails SET {', '.join(fields)} WHERE id = ?"
    cursor.execute(query, tuple(values))
    conn.commit()
    conn.close()

def approve_email(
    email_id: int,
    recipient: str,
    scheduled_time: str,
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
        db_path=db_path
    )

def flag_email(email_id: int, trigger_word: str, db_path: str = DB_FILE):
    """Mark email status as Flagged due to a detected negative keyword."""
    update_email(
        email_id=email_id,
        status="Flagged",
        revision_notes=f"Flagged for negative keyword: '{trigger_word}'",
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

def mark_email_sent(email_id: int, db_path: str = DB_FILE):
    update_email(email_id=email_id, status="Sent", error_message=None, db_path=db_path)

def mark_email_error(email_id: int, status: str, error_message: str, db_path: str = DB_FILE):
    update_email(email_id=email_id, status=status, error_message=error_message, db_path=db_path)

def delete_email(email_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM emails WHERE id = ?", (email_id,))
    conn.commit()
    conn.close()

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

def record_email_bounce(recipient_email: str, bounce_reason: str = "", db_path: str = DB_FILE) -> int:
    """
    Mark all emails and contacts associated with recipient_email as bounced.
    """
    clean_email = recipient_email.strip().lower()
    if not clean_email:
        return 0

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Update emails table
    cursor.execute("""
        UPDATE emails SET is_bounced = 1, bounce_reason = ?, status = 'Bounced'
        WHERE LOWER(TRIM(recipient)) = ?
    """, (bounce_reason.strip(), clean_email))

    # 2. Update contacts table
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

    # 2. Auto-cancel all pending / approved outbox emails for this contact
    cursor.execute("""
        SELECT id FROM emails
        WHERE LOWER(TRIM(recipient)) = ? AND status IN ('Pending', 'Approved')
    """, (clean_email,))
    pending_emails = cursor.fetchall()
    cancelled_ids = [r["id"] for r in pending_emails]

    if cancelled_ids:
        cursor.execute("""
            UPDATE emails SET
                status = 'Cancelled',
                error_message = 'Auto-cancelled: Prospect replied to outreach'
            WHERE LOWER(TRIM(recipient)) = ? AND status IN ('Pending', 'Approved')
        """, (clean_email,))

    conn.commit()
    conn.close()

    return {
        "contact_found": contact_found,
        "contact_names": contact_names,
        "cancelled_drafts_count": len(cancelled_ids),
        "cancelled_email_ids": cancelled_ids,
        "sender_email": clean_email
    }

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

def get_effective_daily_limit(account: Dict[str, Any], today_str: Optional[str] = None) -> int:
    """
    Calculate the active daily sending cap for an SMTP account.
    If warmup is enabled:
        days_elapsed = max(0, (today - warmup_start_date).days)
        effective_limit = min(warmup_target_limit, warmup_starting_limit + (days_elapsed * warmup_daily_increment))
    If warmup is disabled:
        effective_limit = daily_limit
    """
    if not account.get("warmup_enabled"):
        return int(account.get("daily_limit", 50))

    try:
        start_date_str = (account.get("warmup_start_date") or "").strip()
        if not start_date_str:
            return int(account.get("daily_limit", 50))
        start_date = datetime.strptime(start_date_str.split()[0], "%Y-%m-%d").date()
        today = datetime.strptime(today_str, "%Y-%m-%d").date() if today_str else datetime.now().astimezone().date()
        days_elapsed = max(0, (today - start_date).days)
        start_lim = int(account.get("warmup_starting_limit") if account.get("warmup_starting_limit") is not None else 10)
        inc = int(account.get("warmup_daily_increment") if account.get("warmup_daily_increment") is not None else 5)
        target = int(account.get("warmup_target_limit") if account.get("warmup_target_limit") is not None else account.get("daily_limit", 50))
        calculated = start_lim + (days_elapsed * inc)
        return min(calculated, target)
    except Exception:
        return int(account.get("daily_limit", 50))

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
        password.strip(),
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
    """Retrieve all or active SMTP sender accounts."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if active_only:
        cursor.execute("SELECT * FROM smtp_accounts WHERE is_active = 1 ORDER BY id ASC")
    else:
        cursor.execute("SELECT * FROM smtp_accounts ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_smtp_account_by_id(account_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

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
        fields.append("password = ?")
        values.append(password.strip())
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
        acc = dict(r)
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

# Initialize upon import if DB does not exist
if not os.path.exists(DB_FILE):
    init_db(DB_FILE)
else:
    # Ensure any new tables / migrations are applied
    try:
        init_db(DB_FILE)
    except Exception:
        pass
