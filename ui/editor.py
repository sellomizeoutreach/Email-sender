"""
ui/editor.py - Reusable Dual-Mode Email Editor Component for Sellomize Reach.

Features:
- Visual mode: Displays clean, human-readable email copy (no raw <p>, <br>, or style tags).
  Paragraphs are separated by simple line breaks, exactly as a human writes an email.
- Automatic conversion: Transparently converts incoming HTML paragraphs (<p style='...'>, <br>)
  into clean editable text in Visual Mode, and converts them back to structured HTML for dispatch.
- Source mode: Accessible via '</> HTML Source' for power users who want to edit raw HTML code directly.
- Formatting toolbar: Bold, Italic, Underline, Link, Image (Clipboard/Upload/URL), List.
- Variable chips: [Name], [Company], and Variables popover insert tokens immediately into both
  state and widget cache, bypassing Streamlit's widget cache bug.
- Embedded image gallery: Shows thumbnails and [Image 1] tokens without cluttering the text area.
"""

import os
import io
import time
import base64
import html
import re
from typing import Optional, Dict, Any, Tuple
from PIL import ImageGrab, Image
import streamlit as st

from template_engine import resolve_template
from security import sanitize_preview_html
from database import get_config
from ui.components import trigger_toast


def grab_clipboard_image(uploads_dir: str = "assets/uploads") -> Optional[Tuple[str, str, int, int]]:
    """
    Grabs an image directly from the system clipboard.
    Supports screenshots (Win+Shift+S, PrtScn), browser-copied images, and copied image files.
    Automatically downscales if width > 700px to ensure email deliverability.
    Returns (data_uri, file_path, width, height).
    """
    try:
        clip = ImageGrab.grabclipboard()
    except Exception:
        return None

    img = None
    if isinstance(clip, Image.Image):
        img = clip
    elif isinstance(clip, list) and clip:
        first = clip[0]
        if os.path.exists(first) and first.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')):
            try:
                img = Image.open(first)
            except Exception:
                pass

    if img is None:
        return None

    w, h = img.size
    max_w = 700
    if w > max_w:
        ratio = max_w / float(w)
        new_h = int(float(h) * ratio)
        img = img.resize((max_w, new_h), Image.Resampling.LANCZOS)
        w, h = max_w, new_h

    save_format = "PNG" if img.mode in ("RGBA", "LA") else "JPEG"
    ext = "png" if save_format == "PNG" else "jpg"

    if save_format == "JPEG" and img.mode != "RGB":
        img = img.convert("RGB")

    os.makedirs(uploads_dir, exist_ok=True)
    fname = f"pasted_{int(time.time())}_{w}x{h}.{ext}"
    filepath = os.path.join(uploads_dir, fname)

    buf = io.BytesIO()
    if save_format == "JPEG":
        img.save(filepath, format="JPEG", quality=85, optimize=True)
        img.save(buf, format="JPEG", quality=85, optimize=True)
        mime = "image/jpeg"
    else:
        img.save(filepath, format="PNG", optimize=True)
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"

    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    data_uri = f"data:{mime};base64,{b64_str}"
    return data_uri, filepath, w, h


def extract_images_to_placeholders(html_text: str) -> Tuple[str, Dict[str, str]]:
    """
    Replaces raw <img> tags with human-friendly placeholders like [Image 1]
    so the visual text area stays clean and readable.
    """
    if not html_text:
        return "", {}
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
    """Restores [Image X] placeholders back to their full <img> tags."""
    if not text:
        return ""
    restored = text
    for ph, tag in img_map.items():
        restored = restored.replace(ph, tag)
    return restored


def html_to_visual_text(html_content: str) -> Tuple[str, Dict[str, str]]:
    """
    Converts email HTML into clean, human-readable text for the visual editor:
    - Extracts <img> tags to [Image 1] placeholders.
    - Converts <p style='...'>...</p> tags into double newlines (\n\n).
    - Converts <br> tags into single newlines (\n).
    - Strips <div> tags while preserving line breaks.
    - Decodes HTML entities (&nbsp; -> space, etc.).
    Returns (clean_visual_text, img_map).
    """
    if not html_content:
        return "", {}

    # 1. Extract images first
    text, img_map = extract_images_to_placeholders(html_content)

    # 2. Check for HTML structure tags
    has_html = any(tag in text.lower() for tag in ["<p", "<br", "<div"])
    if has_html:
        # Convert paragraph transitions </p><p...> to double newline
        text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
        # Remove opening <p...> at start and closing </p> at end
        text = re.sub(r'^\s*<p[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>\s*$', '', text, flags=re.IGNORECASE)
        # Any remaining <p...> to newline
        text = re.sub(r'<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
        # Convert <br...> to newline
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        # Convert <div> to newline
        text = re.sub(r'</div>\s*<div[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</?div[^>]*>', '\n', text, flags=re.IGNORECASE)
        # Unescape HTML entities
        text = html.unescape(text)
        # Clean up excess blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

    return text, img_map


def visual_text_to_html(visual_text: str, img_map: Dict[str, str]) -> str:
    """
    Converts clean visual text back into structured HTML for email sending and live preview:
    - Restores [Image X] placeholders back to <img> tags.
    - If the visual text contains complex block HTML (table, style, etc.), preserves it.
    - Converts double newlines into clean <p style='margin: 0 0 1em 0;'>...</p> paragraphs.
    - Converts single newlines inside paragraphs into <br>.
    """
    if not visual_text or not visual_text.strip():
        return ""

    text = visual_text.strip()

    # If the text already has full block structure, don't double-wrap
    has_block = any(tag in text.lower() for tag in ["<p", "<div", "<table", "<ul", "<ol", "<h1", "<h2", "<h3"])
    if has_block:
        return restore_images_from_placeholders(text, img_map)

    # Split into paragraphs by double newlines
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    html_paragraphs = []
    for p in paragraphs:
        p_html = p.replace("\n", "<br>")
        html_paragraphs.append(f"<p style='margin: 0 0 1em 0;'>{p_html}</p>")

    full_html = "".join(html_paragraphs)
    return restore_images_from_placeholders(full_html, img_map)


def render_dual_mode_editor(
    key_prefix: str,
    initial_content: str = "",
    sample_lead: Optional[Dict[str, Any]] = None,
    height: int = 240
) -> str:
    """
    Renders a dual-mode rich email editor:
    - Visual mode: clean editable copy (NO raw HTML tags), toolbar, variable chips, image gallery.
    - Source mode: raw HTML textarea for power users.
    """
    state_key    = f"{key_prefix}_body_html"
    mode_key     = f"{key_prefix}_editor_mode"
    textarea_key = f"{key_prefix}_visual_textarea"

    if state_key not in st.session_state:
        st.session_state[state_key] = initial_content
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "visual"

    current_body = st.session_state[state_key]
    is_visual    = (st.session_state[mode_key] == "visual")

    # =========================================================================
    # VISUAL MODE
    # =========================================================================
    if is_visual:
        # Pre-compute clean visual text from stored HTML
        visual_text, img_map = html_to_visual_text(current_body)

        # Read the live text area value
        live_visual = st.session_state.get(textarea_key, visual_text)

        # Helper: append a token to whatever the user has typed so far,
        # then sync BOTH state keys so rerun shows the token in the editor.
        def _insert(token: str):
            new_visual = live_visual + token
            new_html   = visual_text_to_html(new_visual, img_map)
            st.session_state[state_key]    = new_html      # persistent HTML
            st.session_state[textarea_key] = new_visual    # clean visual text
            st.rerun()

        def _insert_html(html_tag: str):
            """Insert formatted text or HTML token."""
            new_visual = live_visual + html_tag
            new_html   = visual_text_to_html(new_visual, img_map)
            st.session_state[state_key]    = new_html
            st.session_state[textarea_key] = new_visual
            st.rerun()

        # ------------------------------------------------------------------
        # Row 1: Formatting toolbar
        # ------------------------------------------------------------------
        tb_cols = st.columns([0.7, 0.7, 0.7, 1.1, 1.2, 1.1, 1.8])

        with tb_cols[0]:
            if st.button("**B**", key=f"{key_prefix}_btn_bold",
                         help="Bold", use_container_width=True):
                _insert_html(" **bold text**")

        with tb_cols[1]:
            if st.button("*I*", key=f"{key_prefix}_btn_italic",
                         help="Italic", use_container_width=True):
                _insert_html(" *italic text*")

        with tb_cols[2]:
            if st.button("U̲", key=f"{key_prefix}_btn_underline",
                         help="Underline", use_container_width=True):
                _insert_html(" <u>underlined text</u>")

        with tb_cols[3]:
            with st.popover("🔗 Link", help="Insert Hyperlink", use_container_width=True):
                st.markdown("**Insert Hyperlink**")
                l_url = st.text_input("Link URL", value="https://", key=f"{key_prefix}_pop_url")
                l_txt = st.text_input("Link Text", value="click here", key=f"{key_prefix}_pop_txt")
                if st.button("Insert Link", type="primary",
                             key=f"{key_prefix}_pop_ins_link", use_container_width=True):
                    tag = (f'<a href="{html.escape(l_url)}" '
                           f'style="color:#083731; font-weight:600; text-decoration:underline;">'
                           f'{html.escape(l_txt)}</a>')
                    _insert_html(f" {tag}")

        with tb_cols[4]:
            with st.popover("🖼️ Image", help="Insert or Paste Image", use_container_width=True):
                st.markdown("**Insert or Paste Image**")
                img_method = st.radio(
                    "Method",
                    ["📋 Paste from Clipboard (Fastest)", "File Upload", "Image URL"],
                    key=f"{key_prefix}_img_src",
                    horizontal=True
                )
                if img_method.startswith("📋 Paste"):
                    st.markdown("""
                    <div style="background:#F8FAFC; border:1px solid #CBD5E1; border-radius:8px; padding:10px; margin-bottom:10px;">
                        <div style="font-weight:700; color:#083731; font-size:13px;">📋 Instant Clipboard Paste</div>
                        <div style="font-size:12px; color:#64748B; margin-top:3px;">
                            Copy any image or screenshot (<kbd style="background:#E2E8F0; padding:1px 5px; border-radius:3px;">Win+Shift+S</kbd>
                            or right-click → Copy Image), then click below.
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button("📋 Paste Image from Clipboard", type="primary",
                                 key=f"{key_prefix}_btn_paste_clip", use_container_width=True):
                        res = grab_clipboard_image()
                        if res:
                            data_uri, fpath, w, h = res
                            tag = (f'<img src="{data_uri}" alt="Pasted Image" '
                                   f'style="max-width:100%; height:auto; border-radius:6px; margin:8px 0; display:block;" />')
                            _insert_html(f"\n{tag}\n")
                            trigger_toast(f"Pasted image ({w}x{h}px) from clipboard!", icon="📋")
                        else:
                            st.warning("⚠️ No image found on clipboard. Take a screenshot (Win+Shift+S) or copy an image, then click Paste.")

                elif img_method == "File Upload":
                    st.caption("Upload PNG, JPEG, or WebP. Images are optimized for email delivery.")
                    up_file = st.file_uploader("Choose Image", type=["png", "jpg", "jpeg", "webp"],
                                               key=f"{key_prefix}_img_up")
                    if up_file:
                        raw_bytes = up_file.read()
                        try:
                            pil_img = Image.open(io.BytesIO(raw_bytes))
                            w, h = pil_img.size
                            if w > 700:
                                ratio = 700 / float(w)
                                pil_img = pil_img.resize((700, int(h * ratio)), Image.Resampling.LANCZOS)
                                w, h = 700, int(h * ratio)
                            buf = io.BytesIO()
                            pil_img.save(buf, format="PNG", optimize=True)
                            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        except Exception:
                            b64 = base64.b64encode(raw_bytes).decode("utf-8")
                        mime = up_file.type or "image/png"
                        tag  = (f'<img src="data:{mime};base64,{b64}" alt="Uploaded Image" '
                                f'style="max-width:100%; height:auto; border-radius:6px; margin:8px 0; display:block;" />')
                        if st.button("Insert Uploaded Image", type="primary",
                                     key=f"{key_prefix}_btn_ins_up_img", use_container_width=True):
                            _insert_html(f"\n{tag}\n")
                            trigger_toast("Image inserted into email!", icon="🖼️")
                else:
                    img_url = st.text_input("Image Direct URL", placeholder="https://sellomize.com/logo.png",
                                            key=f"{key_prefix}_img_url_val")
                    img_alt = st.text_input("Alt Text", value="Image", key=f"{key_prefix}_img_alt_val")
                    if st.button("Insert URL Image", type="primary",
                                 key=f"{key_prefix}_btn_ins_url_img", use_container_width=True):
                        if img_url.strip():
                            tag = (f'<img src="{html.escape(img_url.strip())}" alt="{html.escape(img_alt)}" '
                                   f'style="max-width:100%; height:auto; border-radius:6px; margin:8px 0; display:block;" />')
                            _insert_html(f"\n{tag}\n")
                            trigger_toast("Image inserted!", icon="🖼️")

        with tb_cols[5]:
            with st.popover("📋 List", help="Insert Bullet or Numbered List", use_container_width=True):
                st.markdown("**Insert List**")
                if st.button("• Bulleted List", key=f"{key_prefix}_btn_ul", use_container_width=True):
                    _insert("\n- Point one\n- Point two\n")
                if st.button("1. Numbered List", key=f"{key_prefix}_btn_ol", use_container_width=True):
                    _insert("\n1. First step\n2. Second step\n")

        with tb_cols[6]:
            if st.button("</> HTML Source", key=f"{key_prefix}_btn_to_source",
                         help="Switch to raw HTML source code", use_container_width=True):
                st.session_state[state_key] = visual_text_to_html(live_visual, img_map)
                st.session_state[mode_key] = "source"
                st.rerun()

        # ------------------------------------------------------------------
        # Row 2: Personalization chips
        # ------------------------------------------------------------------
        chip_cols = st.columns([1.1, 1.3, 1.3, 1.5])

        with chip_cols[0]:
            if st.button("👤 [Name]", key=f"{key_prefix}_chip_name",
                         help="Insert [Name] token — resolves to recipient's name",
                         use_container_width=True):
                _insert(" [Name]")

        with chip_cols[1]:
            if st.button("🏢 [Company]", key=f"{key_prefix}_chip_comp",
                         help="Insert [Company] token — resolves to recipient's company",
                         use_container_width=True):
                _insert(" [Company]")

        with chip_cols[2]:
            with st.popover("➕ Variables", help="Insert personalization tokens",
                            use_container_width=True):
                st.markdown("**Personalization Tokens**")
                st.caption("Click any token to insert it into your email body.")
                v1, v2 = st.columns(2)
                with v1:
                    if st.button("{first_name}", key=f"{key_prefix}_var_fn", use_container_width=True):
                        _insert(" {first_name}")
                    if st.button("[First Name]", key=f"{key_prefix}_var_fn_bracket", use_container_width=True):
                        _insert(" [First Name]")
                    if st.button("[Email]", key=f"{key_prefix}_var_email", use_container_width=True):
                        _insert(" [Email]")
                with v2:
                    if st.button("{company}", key=f"{key_prefix}_var_c", use_container_width=True):
                        _insert(" {company}")
                    if st.button("[Website]", key=f"{key_prefix}_var_site", use_container_width=True):
                        _insert(" [Website]")
                    if st.button("[Tags]", key=f"{key_prefix}_var_tags", use_container_width=True):
                        _insert(" [Tags]")

        with chip_cols[3]:
            if st.button("🖋️ Signature", key=f"{key_prefix}_chip_sig",
                         help="Append saved corporate signature",
                         use_container_width=True):
                sig = get_config("signature_html", "") or "Best regards,\nOutreach Team"
                _insert(f"\n\n{sig}")

        # ------------------------------------------------------------------
        # Image gallery (visual thumbnails for embedded images)
        # ------------------------------------------------------------------
        if img_map:
            st.markdown(
                f"<div style='font-size:12px; font-weight:700; color:#083731; margin:6px 0 4px;'>"
                f"🖼️ Images in this email ({len(img_map)}):</div>",
                unsafe_allow_html=True
            )
            for ph, img_tag in img_map.items():
                with st.container(border=True):
                    c_thumb, c_lbl = st.columns([1, 4], vertical_alignment="center")
                    with c_thumb:
                        src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
                        img_src = src_match.group(1) if src_match else ""
                        st.markdown(
                            f'<img src="{img_src}" style="max-height:48px; max-width:80px; '
                            f'border-radius:4px; object-fit:cover; border:1px solid #CBD5E1;" />',
                            unsafe_allow_html=True
                        )
                    with c_lbl:
                        st.markdown(
                            f"<div style='font-size:12px; font-weight:700; color:#083731;'>{ph}</div>"
                            f"<div style='font-size:11px; color:#64748B;'>Positioned as {ph} — move or delete the token in the text area.</div>",
                            unsafe_allow_html=True
                        )
                        if st.button(f"🗑️ Remove {ph}", key=f"{key_prefix}_del_{ph}"):
                            st.session_state[state_key] = st.session_state[state_key].replace(img_tag, "")
                            trigger_toast(f"Removed {ph}.", icon="🗑️")
                            st.rerun()

        # ------------------------------------------------------------------
        # Main visual text area — displays clean readable email text
        # ------------------------------------------------------------------
        edited_val = st.text_area(
            "Visual Content Editor",
            value=visual_text,
            height=height,
            key=textarea_key,
            label_visibility="collapsed",
            help="Type or paste your email body here. Use the toolbar buttons above to format or insert variables."
        )

        # Convert clean visual text back to structured HTML for dispatch & preview
        st.session_state[state_key] = visual_text_to_html(edited_val, img_map)

    # =========================================================================
    # SOURCE MODE (raw HTML)
    # =========================================================================
    else:
        src_c1, src_c2 = st.columns([3.5, 1.5], vertical_alignment="center")
        with src_c1:
            st.markdown(
                "<div style='font-size:12px; color:#64748B; padding:4px 0;'>"
                "<b>Source View (Raw HTML)</b> — Direct HTML editing. Tags and attributes preserved exactly.</div>",
                unsafe_allow_html=True
            )
        with src_c2:
            if st.button("👁️ Visual Editor", key=f"{key_prefix}_btn_to_visual",
                         help="Switch back to visual editor", use_container_width=True):
                raw_val = st.session_state.get(f"{key_prefix}_source_textarea", st.session_state[state_key])
                clean_vis, _ = html_to_visual_text(raw_val)
                st.session_state[state_key]    = raw_val
                st.session_state[textarea_key] = clean_vis
                st.session_state[mode_key]     = "visual"
                st.rerun()

        raw_source_val = st.text_area(
            "Source HTML",
            value=st.session_state[state_key],
            height=height + 40,
            key=f"{key_prefix}_source_textarea",
            label_visibility="collapsed"
        )
        st.session_state[state_key] = raw_source_val

    return st.session_state[state_key]
