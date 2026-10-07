"""
sheets_sync.py - Google Sheets writeback adapter for Sellomize Reach.

Spec Section 9:
"Google Sheets writeback: on scheduled/sent, a service account updates the Email Leads
row (Contacted?, dates, Status, Follow-Ups Sent) — removes the manual sheet step."

Supports:
- Service account JSON key (file path or json string via settings/env)
- Auto-lookup by lead email or lead_id
- Updates columns: Contacted? (Yes), First Contacted (YYYY-MM-DD), Status (Emailed/Sent), Follow-Ups Sent (N)
- Graceful offline / non-configured fallback with informative logs.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

logger = logging.getLogger("sheets_sync")

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    gspread = None
    Credentials = None
    GSPREAD_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def is_sheets_sync_enabled() -> bool:
    """Check if Google Sheets writeback credentials and spreadsheet are configured."""
    from database import get_config
    enabled = (get_config("sheets_sync_enabled", "false") or "false").lower() in ("true", "1", "yes")
    sheet_id = get_config("sheets_spreadsheet_id", "") or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    creds_json = get_config("sheets_service_account_json", "") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    creds_file = get_config("sheets_service_account_path", "") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    return enabled and bool(sheet_id) and bool(creds_json or creds_file)


def _get_gspread_client():
    """Authenticate and return gspread client."""
    if not GSPREAD_AVAILABLE:
        raise RuntimeError("gspread and google-auth packages are not installed.")

    from database import get_config
    creds_json = get_config("sheets_service_account_json", "") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    creds_file = get_config("sheets_service_account_path", "") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    if creds_json and creds_json.strip():
        data = json.loads(creds_json)
        creds = Credentials.from_service_account_info(data, scopes=SCOPES)
    elif creds_file and os.path.exists(creds_file.strip()):
        creds = Credentials.from_service_account_file(creds_file.strip(), scopes=SCOPES)
    else:
        raise ValueError("No valid Google service account credentials found.")

    return gspread.authorize(creds)


def sync_lead_sent_to_sheets(
    lead_email: str,
    lead_id: Optional[str] = None,
    status: str = "Emailed",
    contacted: str = "Yes",
    sent_at: Optional[datetime] = None,
    follow_up_step: int = 1
) -> bool:
    """
    Update the lead row in Google Sheets when an email is sent.
    Updates columns if found:
    - 'Contacted?' or 'Contacted' -> 'Yes'
    - 'Status' -> status (e.g. 'Emailed')
    - 'First Contacted' or 'Date Contacted' -> sent_at date string (if step == 1 or empty)
    - 'Follow-Ups Sent' or 'Follow-up Sent' -> follow_up_step - 1 (or step count)
    - 'Last Contacted' -> sent_at date string
    """
    if not is_sheets_sync_enabled():
        logger.debug("Google Sheets writeback is not enabled or credentials not configured. Skipping.")
        return False

    if not GSPREAD_AVAILABLE:
        logger.warning("Google Sheets writeback requested but gspread library is missing.")
        return False

    from database import get_config
    sheet_id = get_config("sheets_spreadsheet_id", "") or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    sheet_name = get_config("sheets_worksheet_name", "Email Leads")

    clean_email = (lead_email or "").strip().lower()
    clean_lead_id = (lead_id or "").strip()
    if not clean_email and not clean_lead_id:
        return False

    now_dt = sent_at or datetime.now()
    now_date_str = now_dt.strftime("%Y-%m-%d")

    try:
        client = _get_gspread_client()
        sh = client.open_by_key(sheet_id)
        try:
            worksheet = sh.worksheet(sheet_name)
        except Exception:
            worksheet = sh.get_worksheet(0)

        # Get header row
        headers = [h.strip() for h in worksheet.row_values(1)]
        col_map = {h.lower(): i + 1 for i, h in enumerate(headers)}

        # Find matching row index
        match_row_idx = None
        if clean_email and ("email" in col_map or "email address" in col_map):
            email_col = col_map.get("email") or col_map.get("email address")
            cell = worksheet.find(clean_email, in_column=email_col)
            if cell:
                match_row_idx = cell.row

        if not match_row_idx and clean_lead_id and ("lead id" in col_map or "lead_id" in col_map or "id" in col_map):
            lid_col = col_map.get("lead id") or col_map.get("lead_id") or col_map.get("id")
            cell = worksheet.find(clean_lead_id, in_column=lid_col)
            if cell:
                match_row_idx = cell.row

        if not match_row_idx:
            logger.info(f"[Sheets Sync] Lead '{clean_email or clean_lead_id}' not found in sheet '{sheet_name}'.")
            return False

        # Prepare updates
        updates = []
        def set_col(header_aliases: List[str], val: Any):
            for alias in header_aliases:
                c = col_map.get(alias.lower())
                if c:
                    updates.append({
                        "range": gspread.utils.rowcol_to_a1(match_row_idx, c),
                        "values": [[str(val)]]
                    })
                    break

        set_col(["Contacted?", "Contacted"], contacted)
        set_col(["Status", "Lead Status"], status)
        set_col(["Last Contacted", "Last Contact Date"], now_date_str)

        if follow_up_step <= 1:
            set_col(["First Contacted", "Date Contacted", "Date First Emailed"], now_date_str)
        else:
            fu_count = follow_up_step - 1
            set_col(["Follow-Ups Sent", "Follow-up Sent", "Followups Sent"], fu_count)

        if updates:
            worksheet.batch_update(updates)
            logger.info(f"[Sheets Sync] Updated row {match_row_idx} for lead '{clean_email}' in Google Sheets.")
            return True

        return False
    except Exception as e:
        logger.warning(f"[Sheets Sync] Failed to update lead in Google Sheets: {e}")
        return False
