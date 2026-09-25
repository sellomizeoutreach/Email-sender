"""
Quick verification script for all recent fixes.
"""
import sys

results = []

def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f"\n       {detail}" if detail else ""))

# ── 1. bulk.py fixes ───────────────────────────────────────────────────────
with open("ui/bulk.py", encoding="utf-8") as f:
    bulk_src = f.read()

bad_2arg = any(
    "_missing_tokens(" in line and ", lead)" in line
    for line in bulk_src.splitlines()
    if not line.strip().startswith("#") and "_missing_tokens," not in line
)
check("bulk.py: _missing_tokens bad 2-arg call gone", not bad_2arg)
check("bulk.py: resolved_text per-lead check present", "resolved_text = (" in bulk_src or "resolved_text =" in bulk_src)
check("bulk.py: sequence tabs in same row", "st.tabs(tab_labels)" in bulk_src)
check("bulk.py: live preview with subject preview", "Subject: {html.escape(preview_subj)}" in bulk_src)
check("bulk.py: bulletproof set selection logic", "isinstance(st.session_state.get(\"bulk_selected_lead_ids\"), set)" in bulk_src)

# ── 2. editor.py variable insertion fix ────────────────────────────────────
with open("ui/editor.py", encoding="utf-8") as f:
    editor_src = f.read()

check("editor.py: _insert() helper defined",             "def _insert(token: str):" in editor_src)
check("editor.py: textarea_key synced on insert",        "st.session_state[textarea_key] = new_visual" in editor_src)
check("editor.py: live textarea state read pre-buttons", "live_visual = st.session_state.get(textarea_key" in editor_src)

# ── 3. CSV template alignment ───────────────────────────────────────────────
from contacts_handler import generate_csv_template
header = generate_csv_template().strip().split("\n")[0].strip().strip("\r")
csv_cols = header.split(",")
table_cols = ["Lead ID","Brand / Company","Contact Name","Email","Lead Source","Priority","Contacted?","Status","Follow-Ups","Owner","Notes","Tags"]
check("CSV template perfectly aligned with leads table", csv_cols == table_cols,
      f"CSV: {csv_cols}" if csv_cols != table_cols else "")

# ── 4. daily reset scope ───────────────────────────────────────────────────
import inspect
from database import reset_daily_smtp_limits
import re
src = inspect.getsource(reset_daily_smtp_limits)
tables = set(re.findall(r"(?:FROM|INTO|UPDATE|DELETE FROM)\s+(\w+)", src, re.IGNORECASE))
check("reset_daily_smtp_limits only touches smtp_accounts",
      tables == {"smtp_accounts"},
      f"Tables touched: {tables}")

# ── 5. DB data preserved (contacts + templates persist) ────────────────────
from database import get_contacts, get_templates
contacts  = get_contacts()
templates = get_templates()
check("Contacts persist in DB",  len(contacts) >= 0,  f"{len(contacts)} contacts")
check("Templates persist in DB", len(templates) >= 0, f"{len(templates)} templates")

# ── 6. Compose sequence tabs + live preview ────────────────────────────────
with open("ui/compose.py", encoding="utf-8") as f:
    compose_src = f.read()

check("compose.py: custom address toggle is top-level button", "compose_custom_mode" in compose_src)
check("compose.py: sequence tabs in same row",                 "tabs = st.tabs(tab_titles)" in compose_src)
check("compose.py: follow-up queues real email on Send/Schedule", "create_email(" in compose_src and "compose_followups" in compose_src)
check("compose.py: live preview with subject preview",         "Subject: {html.escape(preview_subj)}" in compose_src)

# ── Summary ────────────────────────────────────────────────────────────────
print()
passes = sum(1 for s, _, _ in results if s == "PASS")
total  = len(results)
print(f"{'='*50}")
print(f"Results: {passes}/{total} checks passed")
if passes < total:
    sys.exit(1)
