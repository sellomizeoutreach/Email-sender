"""
warmup.py - Mailbox Warmup Progression & Capacity Arithmetic for Sellomize Reach.
Section B6 of Complete Restructure Spec.

Responsibilities:
1. Deterministic mailbox warmup ramp:
   today_cap = min(daily_limit, warmup_start + (warmup_increment * days_elapsed), warmup_cap)
2. Day progression calculation and daily capacity auditing.
"""

from datetime import datetime
import logging
from typing import Dict, Any, Optional, Union
from timezone_helper import get_engine_now

logger = logging.getLogger("warmup")


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
    target_limit = int(
        account.get("warmup_cap")
        or account.get("warmup_target_limit")
        or account.get("daily_limit")
        or 50
    )
    raw_start = (
        account.get("warmup_start")
        if account.get("warmup_start") is not None
        else account.get("warmup_starting_limit")
    )
    start_lim = int(raw_start if raw_start is not None else 10)
    raw_inc = (
        account.get("warmup_increment")
        if account.get("warmup_increment") is not None
        else account.get("warmup_daily_increment")
    )
    inc = int(raw_inc if raw_inc is not None else 5)

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
        today = datetime.strptime(today_str, "%Y-%m-%d").date() if today_str else get_engine_now().date()
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


def get_effective_daily_limit(
    account: Dict[str, Any],
    today_str: Optional[str] = None,
    target_date: Optional[Union[str, datetime]] = None
) -> int:
    """Calculate the active daily sending cap for an SMTP account using get_warmup_info."""
    if target_date is not None:
        if hasattr(target_date, "strftime"):
            today_str = target_date.strftime("%Y-%m-%d")
        else:
            today_str = str(target_date)
    return get_warmup_info(account, today_str=today_str)["effective_limit"]


def calculate_warmup_limit(
    start_date_str: str,
    start_limit: int,
    daily_increment: int,
    target_limit: int,
    as_of_date: Optional[Any] = None
) -> int:
    """Calculate the effective limit directly from parameters for test/simulation."""
    today_str = as_of_date.strftime("%Y-%m-%d") if hasattr(as_of_date, "strftime") else (str(as_of_date) if as_of_date else None)
    dummy_acc = {
        "warmup_enabled": True,
        "warmup_start_date": start_date_str,
        "warmup_starting_limit": start_limit,
        "warmup_daily_increment": daily_increment,
        "warmup_target_limit": target_limit,
        "daily_limit": target_limit,
    }
    return get_warmup_info(dummy_acc, today_str=today_str)["effective_limit"]

