"""
database.py - SQLite persistence layer for the local email automation system.
Manages system configuration, contacts CRM, outreach templates, spam/negative keyword lists, and email queue.
"""

import sys
import sqlite3
import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

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

    # Schema migration: ensure tags column exists if table was previously created
    try:
        cursor.execute("ALTER TABLE contacts ADD COLUMN tags TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists

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
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Populate default configuration keys if not already present
    default_configs = {
        "gemini_api_key": "AQ.Ab8RN6JyptGhhfk8w83PSpKVcFpmNJOA7aoEJtiB2BCEEiuwVw",
        "gcp_project_id": "606768026327",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "primary_model": "gemini/gemini-1.5-flash",
        "fallback_model": "gpt-4o-mini",
        "sender_email": "",
        "bcc_email": "",
        "spam_blocklist": "guarantee, 100% free, act now, no catch, risk-free, winner, congratulations, make money fast",
        "negative_keywords": "unsubscribe, free, guarantee, 100%, act now, urgent, winner, risk-free, spam, credit card, no catch, cash",
        "signature_html": "<p>Best regards,<br><strong>Listing Audit Team</strong><br><a href='https://example.com'>example.com</a></p>"
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
    db_path: str = DB_FILE
) -> int:
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    vars_json = json.dumps(custom_variables or {})
    tags_str = _normalize_tags(tags)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contacts (name, email, company, tags, custom_variables, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name.strip(), email.strip(), company.strip(), tags_str, vars_json, now_iso))
    contact_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return contact_id

def get_contacts(
    tags_filter: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    db_path: str = DB_FILE
) -> List[Dict[str, Any]]:
    """Retrieve contacts with optional filtering by tags and search query."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    results = []
    normalized_tags_filter = [t.strip().lower() for t in (tags_filter or []) if t.strip()]
    clean_search = search_query.strip().lower() if search_query else ""

    for r in rows:
        d = dict(r)
        # Parse custom variables
        try:
            d["custom_variables_dict"] = json.loads(d.get("custom_variables") or "{}")
        except Exception:
            d["custom_variables_dict"] = {}

        # Parse tags into list
        raw_tags = d.get("tags") or ""
        tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
        d["tags_list"] = tags_list
        tags_lower = [t.lower() for t in tags_list]

        # Tag filter check (contact must match at least one selected tag if filter is set)
        if normalized_tags_filter:
            if not any(filt_tag in tags_lower for filt_tag in normalized_tags_filter):
                continue

        # Search query check
        if clean_search:
            name_match = clean_search in d.get("name", "").lower()
            email_match = clean_search in d.get("email", "").lower()
            company_match = clean_search in (d.get("company") or "").lower()
            tag_match = any(clean_search in t for t in tags_lower)
            if not (name_match or email_match or company_match or tag_match):
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
    d = dict(row)
    try:
        d["custom_variables_dict"] = json.loads(d.get("custom_variables") or "{}")
    except Exception:
        d["custom_variables_dict"] = {}
    raw_tags = d.get("tags") or ""
    d["tags_list"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
    return d

def get_contact_by_email(email: str, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contacts WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))", (email.strip(),))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["custom_variables_dict"] = json.loads(d.get("custom_variables") or "{}")
    except Exception:
        d["custom_variables_dict"] = {}
    raw_tags = d.get("tags") or ""
    d["tags_list"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
    return d

def update_contact(
    contact_id: int,
    name: str,
    email: str,
    company: str = "",
    tags: Any = "",
    custom_variables: Optional[Dict[str, Any]] = None,
    db_path: str = DB_FILE
):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    vars_json = json.dumps(custom_variables or {})
    tags_str = _normalize_tags(tags)
    cursor.execute("""
        UPDATE contacts SET name = ?, email = ?, company = ?, tags = ?, custom_variables = ?
        WHERE id = ?
    """, (name.strip(), email.strip(), company.strip(), tags_str, vars_json, contact_id))
    conn.commit()
    conn.close()

def upsert_contact_by_email(
    name: str,
    email: str,
    company: str = "",
    tags: Any = "",
    custom_variables: Optional[Dict[str, Any]] = None,
    db_path: str = DB_FILE
) -> (int, bool):
    """
    If an email address already exists in the database, updates the existing row
    by merging new tags and custom variables rather than creating a duplicate.
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

        # Update contact record
        update_name = name.strip() if name.strip() else existing["name"]
        update_company = company.strip() if company.strip() else existing.get("company", "")

        update_contact(
            contact_id=contact_id,
            name=update_name,
            email=clean_email,
            company=update_company,
            tags=merged_tags,
            custom_variables=merged_vars,
            db_path=db_path
        )
        return contact_id, False
    else:
        new_id = create_contact(
            name=name,
            email=clean_email,
            company=company,
            tags=tags,
            custom_variables=custom_variables,
            db_path=db_path
        )
        return new_id, True

def get_all_distinct_tags(db_path: str = DB_FILE) -> List[str]:
    """Retrieve all unique tags across all contacts for filtering and autocomplete."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT tags FROM contacts WHERE tags IS NOT NULL AND tags != ''")
    rows = cursor.fetchall()
    conn.close()

    tag_set = set()
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

def delete_contact(contact_id: int, db_path: str = DB_FILE):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()

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

# Initialize upon import if DB does not exist
if not os.path.exists(DB_FILE):
    init_db(DB_FILE)
