"""Test full template create/read/update/load cycle."""
from database import (
    get_templates, get_template_by_id, create_template,
    update_template, delete_template
)

PASS = True

# ── 1. Existing templates ─────────────────────────────────────────────────
templates = get_templates()
print(f"Templates in DB: {len(templates)}")
for t in templates:
    tid   = t["id"]
    tname = t.get("template_name") or t.get("name") or f"Template #{tid}"
    tsubj = t.get("subject") or "No Subject"
    tbody = t.get("body_content") or t.get("body_html") or ""
    print(f"  id={tid}  name={repr(tname[:40])}")
    print(f"       subj={repr(tsubj[:50])}")
    print(f"       body={repr(tbody[:60])}...")
    print(f"       body_present={bool(tbody)}")

# ── 2. Create → read back ─────────────────────────────────────────────────
print("\n--- Create / Update / Read cycle ---")
new_id = create_template(
    template_name="Test Load Template",
    body_content="<p>Hi [Name], testing [Company].</p>"
)
print(f"Created id: {new_id}")

update_template(
    template_id=new_id,
    name="Test Load Template",
    subject="Load check for [Company]",
    body_html="<p>Hi [Name], testing [Company].</p>"
)

fetched = get_template_by_id(new_id)
ok_name = bool(fetched.get("template_name") or fetched.get("name"))
ok_subj = bool(fetched.get("subject"))
ok_body = bool(fetched.get("body_content") or fetched.get("body_html"))
print(f"Name field readable : {ok_name}")
print(f"Subject field readable : {ok_subj}")
print(f"Body field readable : {ok_body}")

# ── 3. Appears in list ────────────────────────────────────────────────────
all_ids = [t["id"] for t in get_templates()]
in_list = new_id in all_ids
print(f"Appears in get_templates() list : {in_list}")

# ── 4. Load path (exactly what Compose + Bulk use) ────────────────────────
all_tpls = get_templates()
choices = {}
for t in all_tpls:
    tname = t.get("template_name") or t.get("name") or f"Template #{t['id']}"
    tsubj = t.get("subject") or "No Subject"
    tbody = t.get("body_content") or t.get("body_html") or ""
    label = f"{tname} — {tsubj}"
    choices[label] = t

print(f"\nBulk/Compose selectbox would show {len(choices)} template(s):")
for label in choices:
    print(f"  {repr(label[:70])}")

# ── 5. Cleanup ────────────────────────────────────────────────────────────
delete_template(new_id)
print(f"\nCleanup: deleted test id={new_id}")

# ── Summary ───────────────────────────────────────────────────────────────
all_ok = ok_name and ok_subj and ok_body and in_list and len(choices) >= 1
print(f"\n{'='*50}")
print(f"ALL TEMPLATE LOAD CHECKS: {'PASS' if all_ok else 'FAIL'}")
