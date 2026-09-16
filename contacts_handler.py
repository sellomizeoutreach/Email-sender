"""
contacts_handler.py - CSV Import, Export, and Template Generation for the internal CRM.
Handles column normalization, deduplication upserts, and tag serialization.
"""

import io
import csv
import json
from typing import List, Dict, Any, Union, Tuple
import pandas as pd

from database import upsert_contact_by_email, DB_FILE

def generate_csv_template() -> str:
    """
    Generate standard CSV template text showing the exact column structure
    and sample leads for brand outreach.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Company", "Tags", "Custom_Variables"])
    writer.writerow([
        "Elena Rostova",
        "elena@skinfix.com",
        "Skinfix",
        "Beauty Brands, Q4 Leads",
        '{"Role": "Brand Director", "Category": "Skincare"}'
    ])
    writer.writerow([
        "Marcus Brody",
        "marcus@minoribeauty.com",
        "Minori Beauty",
        "Beauty Brands, Listing Audit",
        '{"Role": "E-commerce Head", "Category": "Clean Cosmetics"}'
    ])
    writer.writerow([
        "Amy Chen",
        "amy@mypaume.com",
        "Paume",
        "Beauty Brands, Personal Care",
        '{"Role": "Founder", "Category": "Handcare"}'
    ])
    return output.getvalue()

def export_contacts_to_csv(contacts: List[Dict[str, Any]]) -> str:
    """
    Convert a list of contact dictionaries into a CSV string ready for download.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Company", "Tags", "Custom_Variables", "Created_At"])

    for c in contacts:
        # Format custom variables back to clean JSON string
        cv = c.get("custom_variables")
        if isinstance(cv, dict):
            cv_str = json.dumps(cv)
        elif isinstance(cv, str):
            cv_str = cv
        else:
            cv_str = json.dumps(c.get("custom_variables_dict") or {})

        tags_str = c.get("tags") or ", ".join(c.get("tags_list", []))

        writer.writerow([
            c.get("name", ""),
            c.get("email", ""),
            c.get("company", ""),
            tags_str,
            cv_str,
            c.get("created_at", "")
        ])

    return output.getvalue()

def import_contacts_from_csv(
    file_content: Union[str, bytes],
    db_path: str = DB_FILE
) -> Dict[str, Any]:
    """
    Parse an uploaded CSV file, normalize column headers, and execute deduplicated
    upserts into SQLite contacts table.
    If an email already exists, updates the row with merged tags and custom variables.
    Returns stats dict: {"total": int, "inserted": int, "updated": int, "errors": list}
    """
    if isinstance(file_content, bytes):
        content_str = file_content.decode("utf-8", errors="replace")
    else:
        content_str = file_content

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

    # Map possible column header variations to canonical fields
    def find_col(possible_names: List[str]) -> str:
        for p in possible_names:
            for actual in (reader.fieldnames or []):
                if actual and actual.strip().lower() == p.lower():
                    return actual
        return ""

    name_col = find_col(["name", "full name", "contact name", "lead name"])
    email_col = find_col(["email", "email address", "contact email", "e-mail"])
    company_col = find_col(["company", "company name", "organization", "brand", "account"])
    tags_col = find_col(["tags", "tag", "labels", "category", "list"])
    vars_col = find_col(["custom_variables", "custom variables", "variables", "attributes", "metadata"])

    if not email_col:
        stats["errors"].append("Could not find an 'Email' or 'Email Address' column in the CSV.")
        return stats

    row_num = 1
    for row in reader:
        row_num += 1
        raw_email = (row.get(email_col) or "").strip()
        if not raw_email or "@" not in raw_email:
            # Skip empty or invalid email rows
            continue

        raw_name = (row.get(name_col) or "").strip() if name_col else ""
        if not raw_name:
            # Fallback to email username if name is missing
            raw_name = raw_email.split("@")[0].replace(".", " ").title()

        raw_company = (row.get(company_col) or "").strip() if company_col else ""
        raw_tags = (row.get(tags_col) or "").strip() if tags_col else ""

        # Parse custom variables
        raw_vars = (row.get(vars_col) or "").strip() if vars_col else ""
        custom_vars_dict = {}
        if raw_vars:
            try:
                parsed_json = json.loads(raw_vars)
                if isinstance(parsed_json, dict):
                    custom_vars_dict = parsed_json
            except Exception:
                # If not JSON, save as note
                custom_vars_dict = {"Note": raw_vars}

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
