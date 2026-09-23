"""
contacts_handler.py - CSV Import, Export, and Template Generation for the internal CRM.
Handles column normalization, automatic absorption of extra columns into custom variables,
deduplication upserts, and tag serialization.
"""

import io
import csv
import json
from typing import List, Dict, Any, Union

from database import upsert_contact_by_email, DB_FILE
from mx_checker import verify_email_domain_mx

def generate_csv_template() -> str:
    """
    Generate standard CSV template text showing clean, human-readable columns.
    Uses standard outreach fields (Name, Email, Company, Tags, Role, Website, ASIN)
    instead of intimidating raw JSON strings.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Lead ID", "Company", "Contact Name", "Email Address", "Lead Source",
        "Priority", "Contacted?", "Date First Emailed", "Status", "Follow-Ups Sent",
        "Last Contact Date", "Next Follow-Up", "Owner", "Notes", "Tags",
        "Role", "Website", "Amazon Store URL", "Product Category", "Relevant Service",
        "Listing Issues", "Amazon Issues", "Brand Observation", "Verified Location",
        "Research Date", "Research Source", "ASIN"
    ])
    writer.writerow([
        "L-0001",
        "Skinfix",
        "Elena Rostova",
        "elena@skinfix.com",
        "Website",
        "High",
        "No",
        "",
        "Not Contacted",
        0,
        "",
        "2026-09-22",
        "Alex M",
        "Interested in A+ Content teardown",
        "Amazon Brand, High Priority",
        "Brand Director",
        "https://skinfix.com",
        "https://amazon.com/stores/skinfix",
        "Skincare & Barrier Creams",
        "Listing + A+",
        "Missing comparison chart in A+ module",
        "High ACoS on branded terms",
        "Top seller on Sephora but listing conversion lagging on Amazon",
        "New York, NY",
        "2026-09-23",
        "Amazon Storefront & Brand Site",
        "B07XYZ1234"
    ])
    writer.writerow([
        "L-0002",
        "Minori Beauty",
        "Marcus Brody",
        "marcus@minoribeauty.com",
        "Cold Outreach",
        "Medium",
        "Yes",
        "2026-09-15",
        "Follow-Up Sent",
        1,
        "2026-09-15",
        "2026-09-20",
        "Jack C",
        "Check back after product launch",
        "Shopify DTC, Audit Ready",
        "E-commerce Head",
        "https://minoribeauty.com",
        "B08ABC5678"
    ])
    return output.getvalue()

def export_contacts_to_csv(contacts: List[Dict[str, Any]]) -> str:
    """
    Convert a list of contact dictionaries into a CSV string ready for download.
    Matches the Excel spreadsheet layout with dedicated columns for Lead ID, Company,
    Contact Name, Email Address, Lead Source, Priority, Contacted?, Date First Emailed,
    Status, Follow-Ups Sent, Last Contact Date, Next Follow-Up, Owner, Notes, Tags,
    plus dynamically expanded custom variables (Role, Website, ASIN, etc.).
    """
    # 1. Discover all unique custom variable keys across the contacts
    all_var_keys = set()
    for c in contacts:
        cv_dict = c.get("custom_variables_dict")
        if not cv_dict and isinstance(c.get("custom_variables"), dict):
            cv_dict = c.get("custom_variables")
        elif not cv_dict and isinstance(c.get("custom_variables"), str):
            try:
                cv_dict = json.loads(c.get("custom_variables") or "{}")
            except Exception:
                cv_dict = {}
        if isinstance(cv_dict, dict):
            for k in cv_dict.keys():
                if k and str(k).strip():
                    all_var_keys.add(str(k).strip())

    sorted_var_keys = sorted(list(all_var_keys))

    # 2. Build header: Standard CRM fields matching the Excel layout + dynamic custom variable columns + Created_At
    header = [
        "Lead ID", "Company", "Contact Name", "Email Address", "Lead Source",
        "Priority", "Contacted?", "Date First Emailed", "Status", "Follow-Ups Sent",
        "Last Contact Date", "Next Follow-Up", "Owner", "Notes", "Tags"
    ] + sorted_var_keys + ["Created_At"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)

    for c in contacts:
        # Resolve variables dict
        cv_dict = c.get("custom_variables_dict")
        if not cv_dict and isinstance(c.get("custom_variables"), dict):
            cv_dict = c.get("custom_variables")
        elif not cv_dict and isinstance(c.get("custom_variables"), str):
            try:
                cv_dict = json.loads(c.get("custom_variables") or "{}")
            except Exception:
                cv_dict = {}
        if not isinstance(cv_dict, dict):
            cv_dict = {}

        lead_id = f"L-{c['id']:04d}" if c.get("id") else ""
        tags_str = c.get("tags") or ", ".join(c.get("tags_list", []))

        row = [
            lead_id,
            c.get("company") or "",
            c.get("name") or "",
            c.get("email") or "",
            c.get("lead_source") or "Other",
            c.get("priority") or "Medium",
            c.get("contacted") or "No",
            c.get("date_first_emailed") or "",
            c.get("status") or "Not Contacted",
            c.get("follow_ups_sent") if c.get("follow_ups_sent") is not None else 0,
            c.get("last_contact_date") or "",
            c.get("next_follow_up") or "",
            c.get("owner") or "",
            c.get("notes") or "",
            tags_str
        ]

        # Append values for each dynamic variable column
        for vk in sorted_var_keys:
            row.append(str(cv_dict.get(vk, "")))

        row.append(c.get("created_at") or "")
        writer.writerow(row)

    return output.getvalue()

def normalize_variable_header(col_name: str) -> str:
    """
    Standardize common column headers into clean, predictable variable names.
    Examples:
    - 'job_title' / 'position' -> 'Role'
    - 'store url' / 'domain' -> 'Website'
    - 'mobile' / 'cell' -> 'Phone'
    - 'asin' -> 'ASIN'
    """
    clean = col_name.strip()
    lower = clean.lower().replace("_", " ").replace("-", " ")
    
    # Structured Research Fields
    if lower in ["amazon store url", "amazon url", "store link", "amazon storefront", "amazon store"]:
        return "Amazon Store URL"
    if lower in ["product category", "product niche", "brand category"]:
        return "Product Category"
    if lower in ["relevant service", "service needed", "pitch service", "target service"]:
        return "Relevant Service"
    if lower in ["listing issue", "listing issues", "listing problem", "listing audit"]:
        return "Listing Issues"
    if lower in ["amazon issue", "amazon issues", "amazon problem"]:
        return "Amazon Issues"
    if lower in ["brand observation", "amazon observation", "brand observations", "audit notes"]:
        return "Brand Observation"
    if lower in ["verified location", "hq location", "verified city"]:
        return "Verified Location"
    if lower in ["research date", "date researched"]:
        return "Research Date"
    if lower in ["research source", "source researched"]:
        return "Research Source"

    if lower in ["role", "job title", "title", "position", "designation", "job"]:
        return "Role"
    if lower in ["website", "domain", "url", "web", "website url", "store url", "shop url"]:
        return "Website"
    if lower in ["phone", "phone number", "mobile", "cell", "telephone", "contact number"]:
        return "Phone"
    if lower in ["asin", "amazon asin", "product asin"]:
        return "ASIN"
    if lower in ["location", "city", "country", "state", "address"]:
        return "Location"
    if lower in ["category", "industry", "niche", "vertical"]:
        return "Category"
    if lower in ["revenue", "monthly revenue", "annual revenue"]:
        return "Monthly Revenue"
    if lower in ["product", "product name", "hero product"]:
        return "Product"
    
    # Otherwise, clean up and capitalize appropriately
    words = clean.replace("_", " ").split()
    return " ".join(w.capitalize() for w in words)

def import_contacts_from_csv(file_content: Union[str, bytes], verify_mx: bool = False, db_path: str = DB_FILE) -> Dict[str, Any]:
    """
    Import contacts from raw CSV text or bytes.
    Deduplicates against SQLite contacts by email address.
    If contact exists, updates fields and merges tags/variables.
    If new, inserts full contact record.
    
    SMART COLUMN ABSORPTION:
    Any column that is not a standard CRM field (Name, Email, Company, Tags)
    will automatically be absorbed into custom_variables! No JSON needed!
    
    Optional verify_mx: When True, performs pre-flight MX validation on domain.
    
    Returns stats dict: {"total": int, "inserted": int, "updated": int, "invalid_mx": int, "errors": list}
    """
    if isinstance(file_content, bytes):
        content_str = file_content.decode("utf-8", errors="replace")
    else:
        content_str = file_content

    # Handle BOM (Byte Order Mark) if present from Excel exports
    if content_str.startswith("\ufeff"):
        content_str = content_str[1:]

    reader = csv.DictReader(io.StringIO(content_str))

    stats = {
        "total": 0,
        "inserted": 0,
        "updated": 0,
        "invalid_mx": 0,
        "errors": []
    }

    if not reader.fieldnames:
        stats["errors"].append("The uploaded CSV file is empty or has no header row.")
        return stats

    # Map standard column header variations
    def find_col(possible_names: List[str]) -> str:
        for p in possible_names:
            for actual in (reader.fieldnames or []):
                if actual and actual.strip().lower() == p.lower():
                    return actual
        return ""

    name_col = find_col(["name", "full name", "contact name", "lead name"])
    first_name_col = find_col(["first name", "firstname", "fname", "given name"])
    last_name_col = find_col(["last name", "lastname", "lname", "surname"])
    email_col = find_col(["email", "email address", "contact email", "e-mail", "work email"])
    company_col = find_col(["company", "company name", "organization", "brand", "account", "business"])
    tags_col = find_col(["tags", "tag", "labels", "category", "list"])
    lead_id_col = find_col(["lead id", "lead_id", "id"])
    lead_source_col = find_col(["lead source", "lead_source", "source"])
    priority_col = find_col(["priority"])
    contacted_col = find_col(["contacted", "contacted?"])
    date_first_emailed_col = find_col(["date first emailed", "date_first_emailed", "first emailed"])
    status_col = find_col(["status", "lead status", "outreach status"])
    follow_ups_sent_col = find_col(["follow-ups sent", "follow_ups_sent", "followups sent", "followups", "follow-ups"])
    last_contact_date_col = find_col(["last contact date", "last_contact_date", "last contacted"])
    next_follow_up_col = find_col(["next follow-up", "next_follow_up", "next follow up", "follow-up date"])
    owner_col = find_col(["owner", "assigned to", "lead owner", "sales rep"])
    notes_col = find_col(["notes", "note", "comments", "remark"])
    vars_col = find_col(["custom_variables", "custom variables", "variables", "attributes", "metadata", "custom_variables_json"])

    if not email_col:
        stats["errors"].append("Could not find an 'Email' or 'Email Address' column in the CSV.")
        return stats

    # Identify all 'extra' columns that should be automatically captured as custom variables
    standard_cols = {
        name_col, first_name_col, last_name_col, email_col, company_col, tags_col,
        lead_id_col, lead_source_col, priority_col, contacted_col, date_first_emailed_col,
        status_col, follow_ups_sent_col, last_contact_date_col, next_follow_up_col,
        owner_col, notes_col, vars_col
    }
    extra_cols = [c for c in (reader.fieldnames or []) if c and c not in standard_cols and c.strip()]

    row_num = 1
    for row in reader:
        row_num += 1
        raw_email = (row.get(email_col) or "").strip()
        if not raw_email or "@" not in raw_email:
            # Skip empty or invalid email rows
            continue

        # Resolve Name: full name, or combine first + last, or fallback to email username
        raw_name = ""
        if name_col and row.get(name_col):
            raw_name = (row.get(name_col) or "").strip()
        elif first_name_col and row.get(first_name_col):
            fn = (row.get(first_name_col) or "").strip()
            ln = (row.get(last_name_col) or "").strip() if last_name_col else ""
            raw_name = f"{fn} {ln}".strip()
        if not raw_name:
            raw_name = raw_email.split("@")[0].replace(".", " ").title()

        raw_company = (row.get(company_col) or "").strip() if company_col else ""
        raw_tags = (row.get(tags_col) or "").strip() if tags_col else ""
        raw_source = (row.get(lead_source_col) or "").strip() if lead_source_col else None
        raw_priority = (row.get(priority_col) or "").strip() if priority_col else None
        raw_contacted = (row.get(contacted_col) or "").strip() if contacted_col else None
        raw_first_date = (row.get(date_first_emailed_col) or "").strip() if date_first_emailed_col else None
        raw_status = (row.get(status_col) or "").strip() if status_col else None
        raw_sent_val = None
        if follow_ups_sent_col and row.get(follow_ups_sent_col):
            try:
                raw_sent_val = int(row.get(follow_ups_sent_col))
            except Exception:
                pass
        raw_last_date = (row.get(last_contact_date_col) or "").strip() if last_contact_date_col else None
        raw_next_date = (row.get(next_follow_up_col) or "").strip() if next_follow_up_col else None
        raw_owner = (row.get(owner_col) or "").strip() if owner_col else None
        raw_notes = (row.get(notes_col) or "").strip() if notes_col else None

        # Parse any explicit JSON variables column
        custom_vars_dict = {}
        raw_vars = (row.get(vars_col) or "").strip() if vars_col else ""
        if raw_vars:
            try:
                parsed_json = json.loads(raw_vars)
                if isinstance(parsed_json, dict):
                    custom_vars_dict.update({str(k): str(v) for k, v in parsed_json.items()})
            except Exception:
                custom_vars_dict["Note"] = raw_vars

        # SMART ABSORPTION: Automatically capture all other extra columns as custom variables!
        for ecol in extra_cols:
            val = (row.get(ecol) or "").strip()
            if val:
                normalized_key = normalize_variable_header(ecol)
                # Only populate if not already provided by explicit JSON
                if normalized_key not in custom_vars_dict:
                    custom_vars_dict[normalized_key] = val

        # Optional Pre-flight MX record verification during CSV import
        if verify_mx:
            is_valid_mx, mx_err, _ = verify_email_domain_mx(raw_email)
            if not is_valid_mx:
                stats["invalid_mx"] += 1
                if raw_tags:
                    raw_tags = f"{raw_tags}, Invalid MX"
                else:
                    raw_tags = "Invalid MX"
                mx_note = f"[MX Pre-Flight Failed: {mx_err}]"
                raw_notes = f"{raw_notes} {mx_note}".strip() if raw_notes else mx_note

        try:
            cid, is_created = upsert_contact_by_email(
                name=raw_name,
                email=raw_email,
                company=raw_company,
                tags=raw_tags,
                custom_variables=custom_vars_dict,
                lead_source=raw_source,
                priority=raw_priority,
                contacted=raw_contacted,
                date_first_emailed=raw_first_date,
                status=raw_status,
                follow_ups_sent=raw_sent_val,
                last_contact_date=raw_last_date,
                next_follow_up=raw_next_date,
                owner=raw_owner,
                notes=raw_notes,
                db_path=db_path
            )
            stats["total"] += 1
            if is_created:
                stats["inserted"] += 1
            else:
                stats["updated"] += 1
        except Exception as e:
            stats["errors"].append(f"Row {row_num} ({raw_email}): {str(e)}")

    return stats
