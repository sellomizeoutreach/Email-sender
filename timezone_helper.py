"""
timezone_helper.py - Country-wise and timezone-aware scheduling engine for Sellomize Reach.
Provides destination market presets (Canada, Australia, US, UK, Europe, etc.),
live timezone offsets, market-hour validation, and dual-clock scheduling calculators.
"""

from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger("SellomizeTimezone")

# Predefined Target Markets and Standard Timezones
TARGET_MARKETS: Dict[str, Dict[str, Any]] = {
    "CA_EAST": {
        "label": "🇨🇦 Canada (Eastern - Toronto, Montreal, Ottawa)",
        "timezone": "America/Toronto",
        "country": "Canada",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "CA_WEST": {
        "label": "🇨🇦 Canada (Pacific - Vancouver)",
        "timezone": "America/Vancouver",
        "country": "Canada",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "US_EAST": {
        "label": "🇺🇸 United States (Eastern - New York, Miami, Atlanta)",
        "timezone": "America/New_York",
        "country": "United States",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "US_CENTRAL": {
        "label": "🇺🇸 United States (Central - Chicago, Dallas, Austin)",
        "timezone": "America/Chicago",
        "country": "United States",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "US_WEST": {
        "label": "🇺🇸 United States (Pacific - Los Angeles, San Francisco, Seattle)",
        "timezone": "America/Los_Angeles",
        "country": "United States",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "AU_EAST": {
        "label": "🇦🇺 Australia (Eastern - Sydney, Melbourne, Brisbane)",
        "timezone": "Australia/Sydney",
        "country": "Australia",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "AU_WEST": {
        "label": "🇦🇺 Australia (Western - Perth)",
        "timezone": "Australia/Perth",
        "country": "Australia",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "UK": {
        "label": "🇬🇧 United Kingdom & Ireland (London, Manchester, Dublin)",
        "timezone": "Europe/London",
        "country": "United Kingdom",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "EU_CENTRAL": {
        "label": "🇪🇺 Europe (Central - Berlin, Paris, Amsterdam, Madrid)",
        "timezone": "Europe/Berlin",
        "country": "Europe",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "AE_GULF": {
        "label": "🇦🇪 United Arab Emirates & Gulf (Dubai, Abu Dhabi)",
        "timezone": "Asia/Dubai",
        "country": "UAE",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "SG_ASIA": {
        "label": "🇸🇬 Singapore & Hong Kong",
        "timezone": "Asia/Singapore",
        "country": "Singapore",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "IN_ASIA": {
        "label": "🇮🇳 India",
        "timezone": "Asia/Kolkata",
        "country": "India",
        "default_start": "09:30",
        "default_end": "18:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "UTC": {
        "label": "🌐 UTC / Universal Coordinated Time",
        "timezone": "UTC",
        "country": "Global",
        "default_start": "09:00",
        "default_end": "17:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    },
    "LOCAL": {
        "label": "💻 Host PC Local System Time",
        "timezone": "LOCAL",
        "country": "Local",
        "default_start": "09:00",
        "default_end": "18:00",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    }
}


def get_market_info(market_key: str) -> Dict[str, Any]:
    """Retrieve configuration metadata for a market key, falling back to LOCAL."""
    return TARGET_MARKETS.get(market_key, TARGET_MARKETS["LOCAL"])


def get_zoneinfo(tz_identifier: str) -> Optional[ZoneInfo]:
    """Safely obtain a ZoneInfo object, or None for LOCAL/invalid."""
    if not tz_identifier or tz_identifier.upper() in ["LOCAL", ""]:
        return None
    try:
        return ZoneInfo(tz_identifier)
    except Exception as e:
        logger.warning(f"Invalid timezone identifier '{tz_identifier}': {e}. Using local time.")
        return None


def get_market_current_time(market_key_or_tz: str) -> datetime:
    """Get the current time in the specified market or timezone identifier."""
    if market_key_or_tz in TARGET_MARKETS:
        tz_id = TARGET_MARKETS[market_key_or_tz]["timezone"]
    else:
        tz_id = market_key_or_tz

    zi = get_zoneinfo(tz_id)
    if zi:
        return datetime.now(zi)
    return datetime.now().astimezone()


def get_time_difference_summary(market_key_or_tz: str) -> str:
    """
    Format a human-readable summary of the time difference between the target market and the Host PC.
    Example: '-9 hours behind your Host PC' or '+5 hours ahead of your Host PC'.
    """
    target_dt = get_market_current_time(market_key_or_tz)
    host_dt = datetime.now().astimezone()

    target_offset = target_dt.utcoffset() or timedelta(0)
    host_offset = host_dt.utcoffset() or timedelta(0)
    diff_seconds = (target_offset - host_offset).total_seconds()
    diff_hours = diff_seconds / 3600.0

    if abs(diff_hours) < 0.1:
        return "Same time as your Host PC"
    elif diff_hours > 0:
        if diff_hours == int(diff_hours):
            return f"+{int(diff_hours)} hours ahead of your Host PC"
        return f"+{diff_hours:.1f} hours ahead of your Host PC"
    else:
        abs_h = abs(diff_hours)
        if abs_h == int(abs_h):
            return f"-{int(abs_h)} hours behind your Host PC"
        return f"-{abs_h:.1f} hours behind your Host PC"


def is_within_market_hours(
    market_key_or_tz: str,
    days: Optional[List[str]] = None,
    start_time: str = "09:00",
    end_time: str = "17:00",
    reference_dt: Optional[datetime] = None
) -> Tuple[bool, str]:
    """
    Check if the reference datetime (or now) is within allowed business days and hours
    in the prospect's destination market timezone.
    """
    if market_key_or_tz in TARGET_MARKETS:
        tz_id = TARGET_MARKETS[market_key_or_tz]["timezone"]
    else:
        tz_id = market_key_or_tz

    zi = get_zoneinfo(tz_id)
    if reference_dt is None:
        if zi:
            m_dt = datetime.now(zi)
        else:
            m_dt = datetime.now().astimezone()
    else:
        if zi:
            m_dt = reference_dt.astimezone(zi)
        else:
            m_dt = reference_dt.astimezone()

    allowed_days = [d.capitalize() for d in days] if days else ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    current_day = m_dt.strftime("%A")
    if current_day not in allowed_days:
        return False, f"Today ({current_day}) is outside business days in target market ({', '.join(allowed_days)})"

    try:
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
    except Exception:
        sh, sm, eh, em = 9, 0, 17, 0

    curr_mins = m_dt.hour * 60 + m_dt.minute
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    time_display = m_dt.strftime("%H:%M")
    tz_code = m_dt.strftime("%Z") or tz_id

    if start_mins <= end_mins:
        inside = (start_mins <= curr_mins < end_mins)
        if not inside:
            if curr_mins < start_mins:
                return False, f"Current time ({time_display} {tz_code}) is before target market start time ({start_time})"
            else:
                return False, f"Current time ({time_display} {tz_code}) is past target market cutoff time ({end_time})"
    else:
        # Crosses midnight
        inside = (curr_mins >= start_mins or curr_mins < end_mins)
        if not inside:
            return False, f"Current time ({time_display} {tz_code}) is outside target market window ({start_time}-{end_time})"

    return True, f"Inside target market business window ({current_day} {start_time}-{end_time} {tz_code})"


def get_next_valid_market_datetime(
    market_key_or_tz: str,
    base_market_dt: Optional[datetime] = None,
    days: Optional[List[str]] = None,
    start_time: str = "09:00",
    end_time: str = "17:00"
) -> datetime:
    """
    Calculate the next datetime that falls within the target market's business window.
    Operates in the market's timezone and returns a timezone-aware datetime in that zone.
    """
    if market_key_or_tz in TARGET_MARKETS:
        tz_id = TARGET_MARKETS[market_key_or_tz]["timezone"]
    else:
        tz_id = market_key_or_tz

    zi = get_zoneinfo(tz_id)
    if base_market_dt is None:
        dt = datetime.now(zi) if zi else datetime.now().astimezone()
    else:
        dt = base_market_dt.astimezone(zi) if zi else base_market_dt.astimezone()

    allowed_days = [d.capitalize() for d in days] if days else ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    try:
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
    except Exception:
        sh, sm, eh, em = 9, 0, 17, 0

    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    for _ in range(14):
        day_name = dt.strftime("%A")
        curr_mins = dt.hour * 60 + dt.minute

        if day_name in allowed_days:
            if curr_mins < start_mins:
                return dt.replace(hour=sh, minute=sm, second=0, microsecond=0)
            elif curr_mins < end_mins:
                return dt
            else:
                tomorrow = dt + timedelta(days=1)
                dt = tomorrow.replace(hour=sh, minute=sm, second=0, microsecond=0)
        else:
            tomorrow = dt + timedelta(days=1)
            dt = tomorrow.replace(hour=sh, minute=sm, second=0, microsecond=0)

    return dt


def calculate_market_aware_schedule(
    total_contacts: int,
    market_key_or_tz: str,
    stagger_mode: str = "fixed_interval",
    span_hours: float = 4.0,
    spacing_minutes: float = 5.0,
    days: Optional[List[str]] = None,
    start_time: str = "09:00",
    end_time: str = "17:00",
    use_jitter: bool = False,
    jitter_seed: Optional[int] = None
) -> List[Tuple[datetime, datetime]]:
    """
    Calculate sending schedule for N contacts adhering strictly to the target market's business window.
    Returns a list of tuples: (market_datetime, host_datetime).
    - `market_datetime`: The time the email will arrive in the prospect's local clock (e.g. 09:15 AM EDT).
    - `host_datetime`: The exact corresponding local time on the Host PC when it must be dispatched.
    """
    if total_contacts <= 0:
        return []

    import random
    rng = random.Random(jitter_seed) if jitter_seed is not None else random

    if market_key_or_tz in TARGET_MARKETS:
        tz_id = TARGET_MARKETS[market_key_or_tz]["timezone"]
    else:
        tz_id = market_key_or_tz

    zi = get_zoneinfo(tz_id)
    host_tz = datetime.now().astimezone().tzinfo

    # 1. Determine earliest valid send time in the target market
    market_now = datetime.now(zi) if zi else datetime.now().astimezone()
    first_market_dt = get_next_valid_market_datetime(
        market_key_or_tz=tz_id,
        base_market_dt=market_now,
        days=days,
        start_time=start_time,
        end_time=end_time
    )

    normalized_mode = (stagger_mode or "fixed_interval").strip().lower()
    if "hour" in normalized_mode or "span" in normalized_mode:
        total_span_mins = max(1.0, float(span_hours) * 60.0)
        step_mins = (total_span_mins / total_contacts) if total_contacts > 1 else 0.0
    elif "window" in normalized_mode or "daily" in normalized_mode:
        try:
            sh, sm = map(int, start_time.split(":"))
            eh, em = map(int, end_time.split(":"))
        except Exception:
            sh, sm, eh, em = 9, 0, 17, 0
        win_mins = max(1.0, float((eh * 60 + em) - (sh * 60 + sm)))
        step_mins = (win_mins / total_contacts) if total_contacts > 1 else 0.0
    elif "none" in normalized_mode or "now" in normalized_mode:
        step_mins = 0.0
    else:
        step_mins = max(0.5, float(spacing_minutes))

    scheduled_pairs: List[Tuple[datetime, datetime]] = []
    cursor_dt = first_market_dt

    try:
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
    except Exception:
        sh, sm, eh, em = 9, 0, 17, 0
    end_mins = eh * 60 + em
    allowed_days = [d.capitalize() for d in days] if days else ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    for i in range(total_contacts):
        if i == 0:
            target_cand = cursor_dt
        else:
            target_cand = cursor_dt + timedelta(minutes=step_mins)
            if use_jitter and step_mins > 0:
                jitter_secs = rng.uniform(-45.0, 45.0)
                target_cand += timedelta(seconds=jitter_secs)

        # Check if rollover to next day is required
        c_mins = target_cand.hour * 60 + target_cand.minute
        c_day = target_cand.strftime("%A")

        if c_day not in allowed_days or c_mins >= end_mins:
            target_cand = get_next_valid_market_datetime(
                market_key_or_tz=tz_id,
                base_market_dt=target_cand + timedelta(days=1),
                days=days,
                start_time=start_time,
                end_time=end_time
            )

        cursor_dt = target_cand

        # Convert to host PC local datetime
        if zi:
            host_equiv = cursor_dt.astimezone(host_tz)
        else:
            host_equiv = cursor_dt

        scheduled_pairs.append((cursor_dt, host_equiv))

    return scheduled_pairs
