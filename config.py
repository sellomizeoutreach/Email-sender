"""
config.py - Central Configuration & System Constants for Sellomize Reach.
Section B2 of Complete Restructure Spec.

Defines constants, defaults, ports, limits, table names, and polling intervals.
All modules import configuration settings from here.
"""

import os
import sys

# ==============================================================================
# 1. DATABASE & PERSISTENCE PATHS
# ==============================================================================
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
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(appdata, "SellomizeReach")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "email_system.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_system.db")

DB_FILE = get_db_path()

def get_log_file_path() -> str:
    """Determine sellomize.log file location."""
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_dir = os.path.join(appdata, "SellomizeReach")
    else:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "sellomize.log")

LOG_FILE = get_log_file_path()

# ==============================================================================
# 2. CANONICAL TABLE NAMES
# ==============================================================================
TABLE_LEADS = "leads"
TABLE_TEMPLATES = "templates"
TABLE_MAILBOXES = "mailboxes"
TABLE_MESSAGES = "messages"
TABLE_SETTINGS = "settings"

# ==============================================================================
# 3. WORKER & NETWORKING DEFAULTS
# ==============================================================================
WORKER_POLL_INTERVAL_SECONDS = 12
IMAP_CHECK_INTERVAL_SECONDS = 180  # Slower cadence for reply/bounce inbox scan
DEFAULT_SMTP_HOST = "smtp.hostinger.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_IMAP_HOST = "imap.hostinger.com"
DEFAULT_IMAP_PORT = 993
TRACKING_SERVER_PORT = 8502

# Explicit network timeouts (prevent hangs)
SMTP_TIMEOUT_SECONDS = 10.0
TEST_CONNECTION_TIMEOUT_SECONDS = 5.0
IMAP_TIMEOUT_SECONDS = 10.0

# ==============================================================================
# 4. SENDING WINDOW & DISPATCH PACING
# ==============================================================================
DEFAULT_WINDOW_START = "09:00"
DEFAULT_WINDOW_END = "17:00"
DEFAULT_SENDING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DEFAULT_JITTER_MIN_SECONDS = 45
DEFAULT_JITTER_MAX_SECONDS = 120
DEFAULT_SEND_DELAY_SECONDS = 60

DEFAULT_DAILY_LIMIT = 50
DEFAULT_START_TIME = DEFAULT_WINDOW_START
DEFAULT_END_TIME = DEFAULT_WINDOW_END
DEFAULT_DAYS = DEFAULT_SENDING_DAYS
DEFAULT_MIN_JITTER = DEFAULT_JITTER_MIN_SECONDS
DEFAULT_MAX_JITTER = DEFAULT_JITTER_MAX_SECONDS
DEFAULT_TIMEZONE = "Asia/Karachi"
ENGINE_TIMEZONE = "Asia/Karachi"
ENGINE_UTC_OFFSET_HOURS = 5
DEFAULT_SPAM_SCORE_THRESHOLD = 80
DEFAULT_NEGATIVE_KEYWORD_ACTION = "warn"

SEND_NOW_OUTSIDE_WINDOW_POLICY_HOLD = "hold"
SEND_NOW_OUTSIDE_WINDOW_POLICY_SEND = "send"
DEFAULT_SEND_NOW_POLICY = SEND_NOW_OUTSIDE_WINDOW_POLICY_HOLD

# ==============================================================================
# 5. LEAD & MESSAGE STATUSES
# ==============================================================================
LEAD_STATUSES = [
    "New",
    "Emailed",
    "Replied",
    "Bounced",
    "Do Not Contact",
]

# Aliases
CONTACT_STATUSES = LEAD_STATUSES

MESSAGE_STATUS_DRAFT = "Draft"
MESSAGE_STATUS_SCHEDULED = "Scheduled"
MESSAGE_STATUS_SENDING = "Sending"
MESSAGE_STATUS_SENT = "Sent"
MESSAGE_STATUS_PAUSED = "Paused"
MESSAGE_STATUS_CANCELLED = "Cancelled"
MESSAGE_STATUS_FAILED = "Failed"
MESSAGE_STATUS_BOUNCED = "Bounced"

MESSAGE_STATUSES = [
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_SCHEDULED,
    MESSAGE_STATUS_SENDING,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_PAUSED,
    MESSAGE_STATUS_CANCELLED,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_BOUNCED,
]

# ==============================================================================
# 6. CREDENTIAL ENCRYPTION KEYS & IDENTIFIERS
# ==============================================================================
KEYRING_SERVICE_NAME = "SellomizeReach"
KEYRING_USERNAME = "HostingerSMTP"
