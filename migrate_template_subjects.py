"""Migrate existing templates: populate empty subject with a sensible default."""
from database import get_templates, update_template

templates = get_templates()
fixed = 0
for t in templates:
    if not t.get("subject"):
        tid   = t["id"]
        tname = t.get("template_name") or t.get("name") or ""
        tbody = t.get("body_content") or t.get("body_html") or ""
        print(f"Template id={tid} ({tname!r}) has no subject — leaving as empty string")
        update_template(template_id=tid, name=tname, subject="", body_html=tbody)
        fixed += 1

print(f"Migrated {fixed} template(s)")
print("All templates:")
for t in get_templates():
    tid   = t["id"]
    tname = t.get("template_name") or t.get("name") or ""
    tsubj = t.get("subject")
    tbody = t.get("body_content") or t.get("body_html") or ""
    print(f"  id={tid} name={tname!r} subject={tsubj!r} body_len={len(tbody)}")
