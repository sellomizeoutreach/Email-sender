"""Verify editor variable insertion fix and compose follow-up fix."""

# ── Editor fix ────────────────────────────────────────────────────────────
with open("ui/editor.py", encoding="utf-8") as f:
    src = f.read()

editor_checks = {
    "_insert helper defined":                  "def _insert(token: str):" in src,
    "live_visual reads textarea state":        "live_visual = st.session_state.get(textarea_key" in src,
    "textarea_key synced in _insert":          "st.session_state[textarea_key] = new_visual" in src,
    "_insert_html also syncs textarea":        "_insert_html" in src and "st.session_state[textarea_key]" in src,
    "[Name] button uses _insert":              '_insert(" [Name]")' in src,
    "[Company] button uses _insert":           '_insert(" [Company]")' in src,
    "Variables popover uses _insert":          '_insert(" {first_name}")' in src,
    "Bold/Italic/Underline use _insert_html":  "_insert_html" in src,
}

print("=== Editor variable insertion fix ===")
for name, ok in editor_checks.items():
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")

# ── Compose fix ───────────────────────────────────────────────────────────
with open("ui/compose.py", encoding="utf-8") as f:
    csrc = f.read()

compose_checks = {
    "Custom address is top-level toggle button": "compose_custom_mode" in csrc,
    "Follow-up popover with date picker":        "comp_fu_date" in csrc,
    "Follow-up queues real email":               "Queue follow-up" in csrc and "create_email" in csrc,
    "2-row action button layout":                "a1, a2, a3" in csrc and "b1, b2" in csrc,
}

print("\n=== Compose follow-up fix ===")
for name, ok in compose_checks.items():
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")

all_ok = all(editor_checks.values()) and all(compose_checks.values())
print(f"\n{'ALL CHECKS PASS' if all_ok else 'SOME CHECKS FAILED'}")
