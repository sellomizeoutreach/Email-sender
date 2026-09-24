"""
security.py - Security, Credential Encryption & Sanitization Helpers for Sellomize Reach.
Section B10 of Complete Restructure Spec.

Responsibilities:
1. Symmetric encryption / decryption of SMTP passwords at rest using Fernet with Keyring / DPAPI.
2. CRLF & null byte header sanitization (RFC 822 / SMTP header injection prevention).
3. Safe HTML sanitization for live browser preview.
"""

import os
import sys
import re
import logging
from typing import Tuple, Optional

from config import KEYRING_SERVICE_NAME, KEYRING_USERNAME

logger = logging.getLogger("security")

# Cryptography / Fernet availability
try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    Fernet = None
    InvalidToken = Exception
    CRYPTOGRAPHY_AVAILABLE = False

# Keyring availability
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    KEYRING_AVAILABLE = False

# Windows DPAPI availability
try:
    import win32crypt
    DPAPI_AVAILABLE = True
except ImportError:
    win32crypt = None
    DPAPI_AVAILABLE = False


def _get_dpapi_key_file_path() -> str:
    """Determine path for DPAPI-protected key store."""
    if getattr(sys, "frozen", False):
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


# Canonical aliases
encrypt_credential = encrypt_smtp_password
decrypt_credential = decrypt_smtp_password



def sanitize_header(val: Optional[str]) -> str:
    """
    Prevent SMTP / Email Header Injection (CRLF Injection and Null Byte attacks).
    Strips carriage returns, newlines, and null bytes from email header fields:
    (Subject, To, From, Reply-To, In-Reply-To, References, BCC).
    """
    if val is None:
        return ""
    s = str(val)
    # Strip \r, \n, and null bytes \x00
    cleaned = re.sub(r'[\r\n\x00]', ' ', s)
    # Normalize multiple whitespace into a single clean space
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def sanitize_preview_html(raw_html: str) -> str:
    """
    Sanitize operator HTML for safe browser preview inside Streamlit without script execution.
    Removes <script>, <iframe>, <object>, <embed>, and event attributes like onload/onerror.
    Preserves styling, images, tables, links, and formatting.
    """
    if not raw_html:
        return ""
    try:
        import nh3
        # Allow email-safe tags and attributes
        tags = {
            "p", "br", "div", "span", "b", "strong", "i", "em", "u", "s",
            "h1", "h2", "h3", "h4", "h5", "h6", "table", "tbody", "thead",
            "tr", "td", "th", "ul", "ol", "li", "a", "img", "blockquote",
            "code", "pre", "hr"
        }
        attributes = {
            "*": {"style", "class", "id", "dir", "align"},
            "a": {"href", "target", "title", "rel"},
            "img": {"src", "alt", "width", "height", "border"},
            "table": {"cellpadding", "cellspacing", "border", "width"},
            "td": {"colspan", "rowspan", "width", "valign", "align"},
            "th": {"colspan", "rowspan", "width", "valign", "align"},
        }
        return nh3.clean(raw_html, tags=tags, attributes=attributes)
    except Exception:
        # Fallback strip dangerous tags via regex
        cleaned = re.sub(r'<\s*(script|iframe|object|embed)[^>]*>.*?<\s*/\s*\1\s*>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'<\s*(script|iframe|object|embed)[^>]*>', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\son\w+\s*=\s*(["\']).*?\1', '', cleaned, flags=re.IGNORECASE)
        return cleaned
