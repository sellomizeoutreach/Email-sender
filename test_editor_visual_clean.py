"""Verify ui/editor.py clean visual mode and HTML conversion."""
from ui.editor import html_to_visual_text, visual_text_to_html

# 1. Test from Outbox email record
outbox_html = (
    "<p style='margin: 0 0 1em 0;'>Hi Chris,</p>"
    "<p style='margin: 0 0 1em 0;'>Just wanted to follow up on my last email about Hey Honey.</p>"
    "<p style='margin: 0 0 1em 0;'>I know things get busy, so I wanted to bring this back to the top of your inbox.</p>"
)

visual_text, img_map = html_to_visual_text(outbox_html)
print("=== Clean Visual Output for Editor ===")
print(visual_text)
print("======================================")

assert "<p" not in visual_text, "Visual text should NOT contain <p> tags!"
assert "</p>" not in visual_text, "Visual text should NOT contain </p> tags!"
assert "style=" not in visual_text, "Visual text should NOT contain style attributes!"
assert "Hi Chris," in visual_text
assert "Hey Honey" in visual_text

# 2. Test round-trip back to HTML
restored_html = visual_text_to_html(visual_text, img_map)
print("\n=== Restored HTML for Dispatch ===")
print(restored_html)
print("==================================")

assert "<p style='margin: 0 0 1em 0;'>Hi Chris,</p>" in restored_html
assert "Hey Honey" in restored_html

# 3. Test with <br> breaks
br_input = "Hi [Name],<br><br>First paragraph.<br><br>Second paragraph."
vis_br, _ = html_to_visual_text(br_input)
assert "<br" not in vis_br, "Visual text should not contain <br> tags!"
assert "First paragraph." in vis_br

print("\nSUCCESS: All visual editor conversion checks PASSED!")
