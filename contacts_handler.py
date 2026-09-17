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

def generate_csv_template() -> str:
    """
    Generate standard CSV template text showing clean, human-readable columns.
    Uses standard outreach fields (Name, Email, Company, Tags, Role, Website, ASIN)
    instead of intimidating raw JSON strings.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Company", "Tags", "Role", "Website", "ASIN"])
    writer.writerow([
        "Elena Rostova",
        "elena@skinfix.com",
        "Skinfix",
        "Amazon Brand, High Priority",
        "Brand Director",
        "https://skinfix.com",
        "B07XYZ1234"
    ])
    writer.writerow([
        "Marcus Brody",
        "marcus@minoribeauty.com",
        "Minori Beauty",
        "Shopify DTC, Audit Ready",
        "E-commerce Head",
        "https://minoribeauty.com",
        "B08ABC5678"
    ])
    writer.writerow([
        "Amy Chen",
        "amy@mypaume.com",
        "Paume",
        "E-Commerce, Warm Lead",
        "Founder & CEO",
        "https://mypaume.com",
        "B09DEF9012"
    ])
    return output.getvalue()

def export_contacts_to_csv(contacts: List[Dict[str, Any]]) -> str:
    """
    Convert a list of contact dictionaries into a CSV string ready for download.
    Dynamically expands custom variables into their own clean columns (e.g. Role, Website, ASIN)
    so the CSV can be cleanly viewed and edited in Excel or Google Sheets.
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

    # 2. Build header: Base CRM fields + dynamic custom variable columns + Created_At
    header = ["Name", "Email", "Company", "Tags"] + sorted_var_keys + ["Custom_Variables_JSON", "Created_At"]

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

        tags_str = c.get("tags") or ", ".join(c.get("tags_list", []))
        cv_json = json.dumps(cv_dict) if cv_dict else "{}"

        row = [
            c.get("name", ""),
            c.get("email", ""),
            c.get("company", ""),
            tags_str
        ]

        # Append values for each dynamic variable column
        for vk in sorted_var_keys:
            row.append(str(cv_dict.get(vk, "")))

        row.append(cv_json)
        row.append(c.get("created_at", ""))
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

def import_contacts_from_csv(
    file_content: Union[str, bytes],
    db_path: str = DB_FILE
) -> Dict[str, Any]:
    """
    Parse an uploaded CSV file, normalize column headers, and execute deduplicated
    upserts into SQLite contacts table.
    
    SMART COLUMN ABSORPTION:
    Any column that is not a standard CRM field (Name, Email, Company, Tags)
    will automatically be absorbed into custom_variables! No JSON needed!
    
    Returns stats dict: {"total": int, "inserted": int, "updated": int, "errors": list}
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
    tags_col = find_col(["tags", "tag", "labels", "category", "list", "status"])
    vars_col = find_col(["custom_variables", "custom variables", "variables", "attributes", "metadata", "custom_variables_json"])

    if not email_col:
        stats["errors"].append("Could not find an 'Email' or 'Email Address' column in the CSV.")
        return stats

    # Identify all 'extra' columns that should be automatically captured as custom variables
    standard_cols = {name_col, first_name_col, last_name_col, email_col, company_col, tags_col, vars_col}
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

        try:
            cid, is_created = upsert_contact_by_email(
                name=raw_name,
                email=raw_email,
                company=raw_company,
                tags=raw_tags,
                custom_variables=custom_vars_dict,
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
