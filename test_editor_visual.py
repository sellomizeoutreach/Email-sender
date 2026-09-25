import re
import html
from typing import Tuple, Dict

def extract_images_to_placeholders(html_text: str) -> Tuple[str, Dict[str, str]]:
    img_pattern = re.compile(r'<img\s+[^>]*?>', re.IGNORECASE)
    tags = img_pattern.findall(html_text)
    img_map = {}
    clean_text = html_text
    for i, tag in enumerate(tags, 1):
        ph = f"[Image {i}]"
        img_map[ph] = tag
        clean_text = clean_text.replace(tag, ph, 1)
    return clean_text, img_map

def restore_images_from_placeholders(text: str, img_map: Dict[str, str]) -> str:
    restored = text
    for ph, tag in img_map.items():
        restored = restored.replace(ph, tag)
    return restored

def html_to_visual_text(html_content: str) -> Tuple[str, Dict[str, str]]:
    """Converts email HTML into clean, human-readable text for the visual editor."""
    if not html_content:
        return "", {}
    
    # 1. Extract images first so they are not affected by text cleanup
    text, img_map = extract_images_to_placeholders(html_content)

    # 2. Check if text has paragraph / break tags
    has_html_structure = any(tag in text.lower() for tag in ["<p", "<br", "<div"])
    if has_html_structure:
        # Convert </p><p...> to double newline
        text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
        # Remove opening <p...> at start and closing </p> at end
        text = re.sub(r'^\s*<p[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>\s*$', '', text, flags=re.IGNORECASE)
        # Any remaining <p...> to newline
        text = re.sub(r'<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
        # Convert <br...> to single newline
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        # Convert <div> to newline
        text = re.sub(r'</div>\s*<div[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</?div[^>]*>', '\n', text, flags=re.IGNORECASE)
        # Unescape HTML entities
        text = html.unescape(text)
        # Clean up excess blank lines (max 2 consecutive newlines)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

    return text, img_map

def visual_text_to_html(visual_text: str, img_map: Dict[str, str]) -> str:
    """Converts clean visual text back into structured HTML for email sending/preview."""
    if not visual_text or not visual_text.strip():
        return ""
    
    text = visual_text.strip()

    # If the text already has block tags (<p>, <div>, <table>, <ul>, etc.), preserve them
    has_block = any(tag in text.lower() for tag in ["<p", "<div", "<table", "<ul", "<ol", "<h1", "<h2", "<h3"])
    if has_block:
        restored = restore_images_from_placeholders(text, img_map)
        return restored

    # Split into paragraphs by double newlines
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    html_paragraphs = []
    for p in paragraphs:
        # Single newline within paragraph becomes <br>
        p_html = p.replace("\n", "<br>")
        html_paragraphs.append(f"<p style='margin: 0 0 1em 0;'>{p_html}</p>")
    
    full_html = "".join(html_paragraphs)
    return restore_images_from_placeholders(full_html, img_map)

# Test with user's exact case from media_1790341275880.png
raw_from_outbox = "<p style='margin: 0 0 1em 0;'>Hi Chris,</p><p style='margin: 0 0 1em 0;'>Just wanted to follow up on my last email about Hey Honey.</p><p style='margin: 0 0 1em 0;'>I know things get busy, so I wanted to bring this back to the top of your inbox.</p>"
visual, img_map = html_to_visual_text(raw_from_outbox)
print("1. Visual Text shown to user:")
print("---")
print(visual)
print("---")

re_html = visual_text_to_html(visual, img_map)
print("2. HTML generated for sending:")
print(re_html)

# Test with Compose initial content with <br><br>
raw_from_compose = "Hi [Name],<br><br>We haven't been properly introduced, but I was looking through [Company] on Amazon.<br><br>I'd be glad to take a look together."
visual2, img_map2 = html_to_visual_text(raw_from_compose)
print("\n3. Compose visual text:")
print("---")
print(visual2)
print("---")

assert "<p" not in visual
assert "<br" not in visual
assert "Hi Chris," in visual
assert "<p" not in visual2
assert "<br" not in visual2
print("\nALL TESTS PASSED!")
