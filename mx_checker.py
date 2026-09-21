"""
mx_checker.py - Pre-flight Mail Exchanger (MX) & DNS Domain Sanity Verification
Shields outbound sender reputation by validating domain existence and MX records
before dispatching through Hostinger SMTP or adding leads to outreach queues.
Features in-memory thread-safe caching for sub-microsecond repetitive lookups.
"""

import re
import socket
import logging
import threading
from typing import Tuple, List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Fast email validation regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)$"
)

import time
from collections import OrderedDict

MAX_CACHE_SIZE = 1000
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# In-memory thread-safe domain LRU cache
# Maps domain -> ( (is_valid, reason, records), timestamp )
_MX_CACHE: OrderedDict[str, Tuple[Tuple[bool, str, List[str]], float]] = OrderedDict()
_CACHE_LOCK = threading.Lock()

# Try importing dnspython
_DNS_AVAILABLE = False
try:
    import dns.resolver
    import dns.exception
    _DNS_AVAILABLE = True
except ImportError:
    _DNS_AVAILABLE = False
    logger.info("dnspython not found; mx_checker will utilize native socket host resolution.")


def clear_mx_cache() -> None:
    """Clear the in-memory domain cache."""
    with _CACHE_LOCK:
        _MX_CACHE.clear()


def get_cached_domain_count() -> int:
    """Return the number of cached domains."""
    with _CACHE_LOCK:
        return len(_MX_CACHE)


def get_domain_from_email(email_address: str) -> Optional[str]:
    """Extract and validate domain part from an email address."""
    if not email_address or not isinstance(email_address, str):
        return None
    match = EMAIL_REGEX.match(email_address.strip())
    if not match:
        return None
    domain = match.group(1).lower().strip()
    if "." not in domain or len(domain) < 3 or len(domain) > 253:
        return None
    # Check TLD length
    tld = domain.split(".")[-1]
    if len(tld) < 2 or not tld.isalpha():
        return None
    return domain

def get_cached_domain_mx(email_address: str) -> Optional[Tuple[bool, str]]:
    """
    Check if the domain's MX status is already in the in-memory LRU cache and unexpired.
    Never initiates a live network or DNS query.
    Returns (is_valid, reason) or None if not cached.
    """
    clean_email = (email_address or "").strip()
    if not clean_email:
        return None
    domain = get_domain_from_email(clean_email)
    if not domain:
        return False, "Invalid email format"
    with _CACHE_LOCK:
        if domain in _MX_CACHE:
            cached_res, cached_ts = _MX_CACHE[domain]
            if time.time() - cached_ts <= CACHE_TTL_SECONDS:
                return cached_res[0], cached_res[1]
    return None


def verify_email_domain_mx(

    email_address: str,
    timeout: float = 3.0,
    use_cache: bool = True
) -> Tuple[bool, str, List[str]]:
    """
    Validate whether an email address has a resolvable domain with active mail exchangers.
    
    Returns:
        (is_valid, reason, records)
        - is_valid: bool (True if valid MX or A record found, False otherwise)
        - reason: diagnostic message explaining the result
        - records: list of MX hostnames or resolved IP addresses
    """
    clean_email = (email_address or "").strip()
    if not clean_email:
        return False, "Recipient email address is empty.", []

    domain = get_domain_from_email(clean_email)
    if not domain:
        return False, f"Invalid email format or malformed domain in '{clean_email}'.", []

    # Check in-memory cache first for O(1) instantaneous lookup
    if use_cache:
        with _CACHE_LOCK:
            if domain in _MX_CACHE:
                cached_res, cached_ts = _MX_CACHE[domain]
                if time.time() - cached_ts <= CACHE_TTL_SECONDS:
                    _MX_CACHE.move_to_end(domain)
                    return cached_res
                else:
                    # Expired entry (>24h) -> evict and resolve fresh
                    del _MX_CACHE[domain]

    result: Tuple[bool, str, List[str]]

    # 1. Primary path: dnspython resolver
    if _DNS_AVAILABLE:
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = timeout
            resolver.lifetime = timeout + 1.0

            # Query MX records
            try:
                mx_answers = resolver.resolve(domain, "MX")
                mx_records = [r.exchange.to_text().rstrip(".") for r in mx_answers]
                if mx_records:
                    result = (True, f"Valid MX record(s) found: {', '.join(mx_records[:2])}", mx_records)
                else:
                    result = _check_rfc5321_a_record(resolver, domain)
            except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                # RFC 5321 fallback: if domain has no MX record, check for an A record
                result = _check_rfc5321_a_record(resolver, domain)
            except dns.resolver.NXDOMAIN:
                result = (False, f"Domain '{domain}' does not exist (NXDOMAIN).", [])
            except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
                logger.warning(f"DNS query timed out for '{domain}'. Falling back to socket host check.")
                result = _fallback_socket_check(domain)

        except Exception as e:
            logger.warning(f"dnspython query error for '{domain}': {e}. Falling back to socket.")
            result = _fallback_socket_check(domain)
    else:
        # 2. Fallback path: native socket
        result = _fallback_socket_check(domain)

    # Cache result in LRU cache with current timestamp
    if use_cache:
        with _CACHE_LOCK:
            if domain in _MX_CACHE:
                _MX_CACHE.move_to_end(domain)
            _MX_CACHE[domain] = (result, time.time())
            while len(_MX_CACHE) > MAX_CACHE_SIZE:
                _MX_CACHE.popitem(last=False)

    return result


def _check_rfc5321_a_record(resolver: Any, domain: str) -> Tuple[bool, str, List[str]]:
    """Check for A record when MX record is not found (RFC 5321 implicit MX)."""
    try:
        a_answers = resolver.resolve(domain, "A")
        ips = [r.to_text() for r in a_answers]
        if ips:
            return (True, f"Implicit MX: No MX record, but active A record found ({ips[0]}).", ips)
    except Exception:
        pass
    return (False, f"No MX or A records found for domain '{domain}'.", [])


def _fallback_socket_check(domain: str) -> Tuple[bool, str, List[str]]:
    """Fallback validation using standard library socket resolution."""
    try:
        ip = socket.gethostbyname(domain)
        return (True, f"Domain '{domain}' resolved successfully via host resolver ({ip}).", [ip])
    except socket.gaierror as gai_err:
        return (False, f"Domain '{domain}' does not exist: {gai_err}", [])
    except Exception as exc:
        return (False, f"Domain '{domain}' resolution error: {exc}", [])


def batch_verify_contacts_mx(
    contacts: List[Dict[str, Any]],
    update_db: bool = False,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Verify MX and domain health across a batch of contacts.
    Optionally applies 'Invalid MX' tag and diagnostic notes in SQLite for quarantined leads.
    """
    total = len(contacts)
    valid_count = 0
    invalid_count = 0
    invalid_contacts = []
    cached_hits = 0

    from database import DB_FILE
    target_db = db_path or DB_FILE

    for contact in contacts:
        email = (contact.get("email") or contact.get("Email Address") or "").strip()
        cid = contact.get("id") or contact.get("Lead ID")
        if not email:
            invalid_count += 1
            invalid_contacts.append({"id": cid, "email": "", "reason": "Missing email address"})
            continue

        domain = get_domain_from_email(email)
        with _CACHE_LOCK:
            if domain and domain in _MX_CACHE:
                cached_hits += 1

        is_valid, reason, records = verify_email_domain_mx(email)
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1
            invalid_contacts.append({
                "id": cid,
                "email": email,
                "domain": domain or "unknown",
                "reason": reason
            })

            # If update_db is requested, tag contact in database
            if update_db and cid:
                try:
                    _quarantine_contact_db(cid, reason, target_db)
                except Exception as err:
                    logger.error(f"Failed to update quarantined contact #{cid}: {err}")

    return {
        "total": total,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "cached_lookups": cached_hits,
        "invalid_contacts": invalid_contacts
    }


def _quarantine_contact_db(contact_id: Any, reason: str, db_path: str) -> None:
    """Internal helper to add 'Invalid MX' tag and diagnostic note to a contact."""
    from database import get_connection
    conn = get_connection(db_path)
    cursor = conn.cursor()

    try:
        if isinstance(contact_id, str) and contact_id.startswith("L-"):
            cid = int(contact_id.replace("L-", ""))
        else:
            cid = int(contact_id)
    except Exception:
        conn.close()
        return

    cursor.execute("SELECT tags, notes FROM contacts WHERE id = ?", (cid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return

    current_tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
    if "Invalid MX" not in current_tags:
        current_tags.append("Invalid MX")
    tags_str = ", ".join(sorted(list(set(current_tags))))

    curr_notes = row["notes"] or ""
    diagnostic_note = f"[MX Pre-Flight Failed: {reason}]"
    if diagnostic_note not in curr_notes:
        updated_notes = f"{curr_notes} {diagnostic_note}".strip() if curr_notes else diagnostic_note
    else:
        updated_notes = curr_notes

    cursor.execute("""
        UPDATE contacts SET tags = ?, notes = ? WHERE id = ?
    """, (tags_str, updated_notes, cid))
    conn.commit()
    conn.close()
