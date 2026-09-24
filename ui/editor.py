"""
ui/editor.py - Reusable Dual-Mode Email Editor Component for Sellomize Reach.
Section C7 of Complete Restructure Spec.

Features:
1. Visual toolbar view (Bold, Italic, Underline, Link, Image, Lists, Variable Chips, Signature).
2. Source view (raw HTML code).
3. Toggle between views with synced state.
4. Round-trip safety: complex HTML warning when switching from Source to Visual.
5. Image insertion:
   - Direct 1-Click Clipboard Paste (Win+Shift+S, browser Copy Image, copied graphics).
   - Local File Upload with auto-downscaling to < 700px.
   - External Image URL.
6. Clean Visual Image display: Images appear visually as real images with clean [Image 1]
   tokens in the text area, completely eliminating miles of raw base64 HTML text.
7. Live preview rendering as the single source of truth.
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
    Automatically downscales if width > 700px to ensure email deliverability and avoid Gmail clipping.
    Saves to uploads_dir and returns (data_uri, file_path, width, height).
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

    # Preserve RGBA for PNG transparency; convert to RGB for JPEG
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
    Replaces raw <img> tags with human-friendly placeholders like [Image 1], [Image 2]
    so the visual text area remains clean, readable, and free of massive base64 code.
    """
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
    """
    Restores [Image X] placeholders back to their full HTML <img> tags for preview and dispatch.
    """
    restored = text
    for ph, tag in img_map.items():
        restored = restored.replace(ph, tag)
    return restored


def render_dual_mode_editor(
    key_prefix: str,
    initial_content: str = "",
    sample_lead: Optional[Dict[str, Any]] = None,
    height: int = 240
) -> str:
    """
    Renders a standalone dual-mode rich email editor matching sellomize_reference.html:
    - Row 1: Formatting icons (B, I, U̲, 🔗, 🖼️, 📋) and Mode Switcher (</> HTML Source / 👁️ Visual Editor)
    - Row 2 (Visual mode): Personalization chips (👤 [Name], 🏢 [Company], ➕ Variables, 🖋️ Signature)
    - Image Popover (🖼️): Clipboard paste, local file upload, or direct URL.
    - Attached Images Gallery: Renders the actual visual image so users never see raw HTML text.
    - Source mode: Raw HTML textarea with syntax preserved and complex HTML warning badge.
    - Live preview sync.
    """
    state_key = f"{key_prefix}_body_html"
    mode_key = f"{key_prefix}_editor_mode"

    if state_key not in st.session_state:
        st.session_state[state_key] = initial_content

    if mode_key not in st.session_state:
        st.session_state[mode_key] = "visual"

    current_body = st.session_state[state_key]
    is_visual = (st.session_state[mode_key] == "visual")

    # --- 1. VISUAL VIEW ---
    if is_visual:
        # Row 1: Formatting Tools (Icons) + Mode Switcher
        tb_cols = st.columns([0.75, 0.75, 0.75, 0.85, 0.85, 0.85, 1.0, 2.3])

        with tb_cols[0]:
            if st.button("**B**", key=f"{key_prefix}_btn_bold", help="Bold (Ctrl+B)", use_container_width=True):
                st.session_state[state_key] = current_body + " <strong>bold text</strong>"
                st.rerun()
        with tb_cols[1]:
            if st.button("*I*", key=f"{key_prefix}_btn_italic", help="Italic (Ctrl+I)", use_container_width=True):
                st.session_state[state_key] = current_body + " <em>italic text</em>"
                st.rerun()
        with tb_cols[2]:
            if st.button("U̲", key=f"{key_prefix}_btn_underline", help="Underline", use_container_width=True):
                st.session_state[state_key] = current_body + " <u>underlined text</u>"
                st.rerun()
        with tb_cols[3]:
            with st.popover("🔗", help="Insert Hyperlink", use_container_width=True):
                st.markdown("**Insert Hyperlink**")
                l_url = st.text_input("Link URL", value="https://", key=f"{key_prefix}_pop_url")
                l_txt = st.text_input("Link Text", value="click here", key=f"{key_prefix}_pop_txt")
                if st.button("Insert Link", type="primary", key=f"{key_prefix}_pop_ins_link", use_container_width=True):
                    tag = f'<a href="{html.escape(l_url)}" style="color:#083731; font-weight:600; text-decoration:underline;">{html.escape(l_txt)}</a>'
                    st.session_state[state_key] = current_body + f" {tag}"
                    st.rerun()
        with tb_cols[4]:
            with st.popover("🖼️", help="Insert or Paste Image", use_container_width=True):
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
                        <div style="font-weight:700; color:#083731; font-size:13px; display:flex; align-items:center; gap:6px;">
                            <span>📋</span> Instant Clipboard Paste
                        </div>
                        <div style="font-size:12px; color:#64748B; margin-top:3px; line-height:1.4;">
                            Copy any image or screenshot (<kbd style="background:#E2E8F0; padding:1px 5px; border-radius:3px;">Win+Shift+S</kbd> or right-click 'Copy Image' on any page), then click the button below.
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button("📋 Paste Image from Clipboard", type="primary", key=f"{key_prefix}_btn_paste_clip", use_container_width=True):
                        res = grab_clipboard_image()
                        if res:
                            data_uri, fpath, w, h = res
                            tag = f'<img src="{data_uri}" alt="Pasted Image" style="max-width:100%; height:auto; border-radius:6px; margin:8px 0; display:block;" />'
                            st.session_state[state_key] = current_body + f"\n{tag}\n"
                            trigger_toast(f"Pasted image ({w}x{h}px) from clipboard!", icon="📋")
                            st.rerun()
                        else:
                            st.warning("⚠️ No image found on your clipboard. Take a screenshot (Win+Shift+S) or copy an image, then click Paste.")
                elif img_method == "File Upload":
                    st.caption("Upload PNG, JPEG, or WebP. Images are optimized automatically for fast delivery.")
                    up_file = st.file_uploader("Choose Image", type=["png", "jpg", "jpeg", "webp"], key=f"{key_prefix}_img_up")
                    if up_file:
                        raw_bytes = up_file.read()
                        try:
                            pil_img = Image.open(io.BytesIO(raw_bytes))
                            w, h = pil_img.size
                            if w > 700:
                                ratio = 700 / float(w)
                                new_h = int(float(h) * ratio)
                                pil_img = pil_img.resize((700, new_h), Image.Resampling.LANCZOS)
                                w, h = 700, new_h
                            buf = io.BytesIO()
                            pil_img.save(buf, format="PNG", optimize=True)
                            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        except Exception:
                            b64 = base64.b64encode(raw_bytes).decode("utf-8")
                        mime = up_file.type or "image/png"
                        tag = f'<img src="data:{mime};base64,{b64}" alt="Uploaded Image" style="max-width:100%; height:auto; border-radius:6px; margin:8px 0; display:block;" />'
                        if st.button("Insert Uploaded Image", type="primary", key=f"{key_prefix}_btn_ins_up_img", use_container_width=True):
                            st.session_state[state_key] = current_body + f"\n{tag}\n"
                            trigger_toast("Image inserted into email!", icon="🖼️")
                            st.rerun()
                else:
                    img_url = st.text_input("Image Direct URL", placeholder="https://sellomize.com/logo.png", key=f"{key_prefix}_img_url_val")
                    img_alt = st.text_input("Alt Text", value="Image", key=f"{key_prefix}_img_alt_val")
                    if st.button("Insert URL Image", type="primary", key=f"{key_prefix}_btn_ins_url_img", use_container_width=True):
                        if img_url.strip():
                            tag = f'<img src="{html.escape(img_url.strip())}" alt="{html.escape(img_alt)}" style="max-width:100%; height:auto; border-radius:6px; margin:8px 0; display:block;" />'
                            st.session_state[state_key] = current_body + f"\n{tag}\n"
                            trigger_toast("Image inserted!", icon="🖼️")
                            st.rerun()

        with tb_cols[5]:
            with st.popover("📋", help="Insert Bullet or Numbered List", use_container_width=True):
                st.markdown("**Insert List**")
                if st.button("• Bulleted List", key=f"{key_prefix}_btn_ul", use_container_width=True):
                    st.session_state[state_key] = current_body + "\n<ul>\n  <li>Point one</li>\n  <li>Point two</li>\n</ul>"
                    st.rerun()
                if st.button("1. Numbered List", key=f"{key_prefix}_btn_ol", use_container_width=True):
                    st.session_state[state_key] = current_body + "\n<ol>\n  <li>First step</li>\n  <li>Second step</li>\n</ol>"
                    st.rerun()
        with tb_cols[6]:
            st.empty()
        with tb_cols[7]:
            if st.button("</> HTML Source", key=f"{key_prefix}_btn_to_source", help="Switch to raw HTML source code", use_container_width=True):
                st.session_state[mode_key] = "source"
                st.rerun()

        # Row 2: Personalization Chips & Signature
        chip_cols = st.columns([1, 1.2, 1.2, 1.5])
        with chip_cols[0]:
            if st.button("👤 [Name]", key=f"{key_prefix}_chip_name", help="Insert recipient [Name] token", use_container_width=True):
                st.session_state[state_key] = current_body + " [Name]"
                st.rerun()
        with chip_cols[1]:
            if st.button("🏢 [Company]", key=f"{key_prefix}_chip_comp", help="Insert company [Company] token", use_container_width=True):
                st.session_state[state_key] = current_body + " [Company]"
                st.rerun()
        with chip_cols[2]:
            with st.popover("➕ Variables", help="Insert additional recipient variables", use_container_width=True):
                st.markdown("**Personalization Tokens**")
                v1, v2 = st.columns(2)
                with v1:
                    if st.button("{first_name}", key=f"{key_prefix}_var_fn", use_container_width=True):
                        st.session_state[state_key] = current_body + " {first_name}"
                        st.rerun()
                    if st.button("[First Name]", key=f"{key_prefix}_var_fn_bracket", use_container_width=True):
                        st.session_state[state_key] = current_body + " [First Name]"
                        st.rerun()
                with v2:
                    if st.button("{company}", key=f"{key_prefix}_var_c", use_container_width=True):
                        st.session_state[state_key] = current_body + " {company}"
                        st.rerun()
                    if st.button("[Website]", key=f"{key_prefix}_var_site", use_container_width=True):
                        st.session_state[state_key] = current_body + " [Website]"
                        st.rerun()
        with chip_cols[3]:
            if st.button("🖋️ Append Signature", key=f"{key_prefix}_chip_sig", help="Append saved corporate signature", use_container_width=True):
                saved_sig = get_config("signature_html", "") or "<p>Best regards,<br><strong>Outreach Team</strong></p>"
                st.session_state[state_key] = current_body + f"\n<br>\n{saved_sig}"
                st.rerun()

        # Complex HTML roundtrip warning if table or CSS styles are in content
        is_complex = any(tag in current_body.lower() for tag in ["<table", "<style", "<script", "<svg", "<iframe"])
        if is_complex:
            st.caption("⚠️ Complex HTML detected (tables/styles). Switch to **</> HTML Source** to preserve exact code structure.")

        # Extract images into clean placeholders [Image 1], [Image 2] so the visual editor text area stays clean
        visual_text, img_map = extract_images_to_placeholders(current_body)

        # If images are present, display the real visual images!
        if img_map:
            st.markdown(f"<div style='font-size:12px; font-weight:700; color:#083731; margin:6px 0 4px;'>🖼️ Images in this email ({len(img_map)}):</div>", unsafe_allow_html=True)
            for ph, img_tag in img_map.items():
                src_match = re.search(r'src=[\'"]([^\'"]+)[\'"]', img_tag, re.IGNORECASE)
                src_val = src_match.group(1) if src_match else ""
                if src_val:
                    c_img, c_lbl = st.columns([1, 4], vertical_alignment="center")
                    with c_img:
                        st.markdown(f'<div style="border:1px solid #CBD5E1; border-radius:6px; padding:3px; background:#FFFFFF; display:inline-block; max-height:80px; overflow:hidden;"><img src="{src_val}" style="max-height:74px; max-width:100%; object-fit:contain; border-radius:4px; display:block;" /></div>', unsafe_allow_html=True)
                    with c_lbl:
                        st.markdown(f"<div style='font-size:12px; font-weight:700; color:#083731;'>{ph}</div><div style='font-size:11px; color:#64748B;'>Positioned in text as {ph} — you can move it or delete it.</div>", unsafe_allow_html=True)
                        if st.button(f"🗑️ Remove {ph}", key=f"{key_prefix}_del_{ph}", use_container_width=False):
                            st.session_state[state_key] = st.session_state[state_key].replace(img_tag, "")
                            trigger_toast(f"Removed {ph}.", icon="🗑️")
                            st.rerun()

        # Textarea representation for visual mode editing
        edited_val = st.text_area(
            "Visual Content Editor",
            value=visual_text,
            height=height,
            key=f"{key_prefix}_visual_textarea",
            label_visibility="collapsed"
        )
        # Restore placeholders back to full <img> tags for state storage
        st.session_state[state_key] = restore_images_from_placeholders(edited_val, img_map)

    # --- 2. SOURCE VIEW (RAW HTML) ---
    else:
        src_c1, src_c2 = st.columns([3.5, 1.5])
        with src_c1:
            st.markdown("<div style='font-size:12px; color:#64748B; padding:6px 0;'><b>Source View (Raw HTML)</b> — Direct HTML editing. Styling and tags are preserved exactly.</div>", unsafe_allow_html=True)
        with src_c2:
            if st.button("👁️ Visual Editor", key=f"{key_prefix}_btn_to_visual", help="Switch back to visual toolbar editor", use_container_width=True):
                st.session_state[mode_key] = "visual"
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
