import re
import html

def html_to_visual(raw_html: str) -> str:
    """Convert raw HTML with <p>, <br>, <div> into clean, human-readable visual text."""
    if not raw_html:
        return ""
    text = raw_html
    # Replace </p><p...> with double newline
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
    # Remove leading <p...> and trailing </p>
    text = re.sub(r'^\s*<p[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>\s*$', '', text, flags=re.IGNORECASE)
    # Replace remaining <p...> with double newline
    text = re.sub(r'<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
    # Replace <br...> with single newline
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Replace <div> and </div> with newline
    text = re.sub(r'</?div[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Unescape HTML entities (&nbsp;, &amp;, etc.)
    text = html.unescape(text)
    # Clean up excess consecutive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def visual_to_html(visual_text: str) -> str:
    """Convert clean visual text back to structured HTML paragraphs for email dispatch."""
    if not visual_text or not visual_text.strip():
        return ""
    text = visual_text.strip()
    # Split paragraphs by double newlines
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    html_paragraphs = []
    for p in paragraphs:
        # Convert single newlines inside paragraph to <br>
        p_html = p.replace("\n", "<br>")
        html_paragraphs.append(f"<p style='margin: 0 0 1em 0;'>{p_html}</p>")
    return "".join(html_paragraphs)

test_input = "<p style='margin: 0 0 1em 0;'>Hi Chris,</p><p style='margin: 0 0 1em 0;'>Just wanted to follow up on my last email about Hey Honey.</p><p style='margin: 0 0 1em 0;'>I know things get busy, so I wanted to bring this back to the top of your inbox.</p>"

visual = html_to_visual(test_input)
print("=== Visual Text ===")
print(visual)
print("\n=== Re-converted to HTML ===")
html_out = visual_to_html(visual)
print(html_out)

assert "<p" not in visual
assert "Hi Chris," in visual
assert "Hey Honey" in visual
print("\nSUCCESS: Perfectly clean visual editing without HTML source code!")
