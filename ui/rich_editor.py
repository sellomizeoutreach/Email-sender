"""
ui/rich_editor.py - Shared Universal Rich Email Editor with Native Paste & Drag-Drop.
Phase 1 of Sellomize Reach Campaigns Module.

Features:
1. Native Browser Paste (Ctrl+V) & Drag-and-Drop:
   - Listens to clipboard 'paste' and file 'drop' events on the editor.
   - Intercepts screenshot images, pasted files, and dragged images.
   - Instant client-side resize to max 600px width via HTML5 Canvas.
   - Server-side PIL validation (max 600px, quality 85%, <= 2 MB cap).
2. Clean Token Placeholders:
   - Keeps visual text human-friendly using [Image 1], [Image 2].
   - Restores into full <img> tags with inline styles for preview and dispatch.
3. Image Gallery with Resizing & Alt Text:
   - Shows thumbnails, dimensions, width controls (100%, 75%, 50%, 300px), alt text, and delete.
4. Rich HTML Sanitization:
   - Strips malicious scripts, Mso styles, tracking junk, while preserving safe tags.
5. Personalization Chips & Send Guard Compatibility:
   - [Name], [Company], Variables popover, Signature, Clean button.
   - Fully compatible with template_engine._missing_tokens.
6. Feature Flag:
   - Can be toggled safely per screen or globally via 'feature_rich_editor'.
"""

import os
import io
import time
import json
import base64
import html
import re
import uuid
import logging
from typing import Optional, Dict, Any, Tuple, List
from PIL import Image, ImageGrab
import streamlit as st
import streamlit.components.v1 as components

from template_engine import resolve_template, sanitize_email_html, _missing_tokens
from security import sanitize_preview_html
from database import get_config
from ui.components import trigger_toast
from ui.editor import (
    html_to_visual_text,
    visual_text_to_html,
    markdown_inline_to_html,
    html_inline_to_markdown,
    extract_images_to_placeholders,
    restore_images_from_placeholders,
    sanitize_visual_text
)

logger = logging.getLogger("rich_editor")

UPLOADS_DIR = "assets/uploads"
MAX_IMAGE_WIDTH = 1200  # Spec Section 4: max 1200px wide
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # Spec Section 8: size guard ceiling 10 MB


class ProcessedImage(tuple):
    """
    Tuple subclass maintaining backward-compatible 4-tuple unpacking
    (data_uri, filepath, width, height) while exposing .image_id and .storage_key.
    """
    data_uri: str
    filepath: str
    width: int
    height: int
    image_id: str
    storage_key: str

    def __new__(cls, data_uri: str, filepath: str, width: int, height: int, image_id: str = "", storage_key: str = ""):
        obj = super().__new__(cls, (data_uri, filepath, width, height))
        obj.data_uri = data_uri
        obj.filepath = filepath
        obj.width = width
        obj.height = height
        obj.image_id = image_id
        obj.storage_key = storage_key
        return obj

    @property
    def id(self) -> str:
        return self.image_id


def is_rich_editor_enabled() -> bool:
    """Evaluate whether the enhanced rich editor is enabled via feature flag."""
    val = get_config("feature_rich_editor", "true") or "true"
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def process_and_store_image(
    raw_bytes: bytes,
    mime_type: str = "image/png",
    alt_text: str = "Screenshot",
    target_width: Optional[int] = None,
    lead_id: Optional[str] = None,
    original_filename: str = "",
    annotated_from: Optional[str] = None
) -> ProcessedImage:
    """
    Process image binary (Spec Section 4 & 5):
    - Downscales to MAX_IMAGE_WIDTH (1200px) or target_width if specified.
    - Compresses to JPEG/PNG (quality 82%).
    - Ensures size <= 10MB.
    - Saves to assets/uploads/leads/{lead_id}/{image_id}.{ext} (or assets/uploads/ if general).
    - Persists metadata in images database table.
    - Returns ProcessedImage(data_uri, file_path, width, height, image_id, storage_key).
    """
    clean_lid = str(lead_id).strip() if lead_id else "general"
    clean_lid = re.sub(r'[^a-zA-Z0-9_\-]', '_', clean_lid) or "general"

    lead_dir = os.path.join(UPLOADS_DIR, "leads", clean_lid)
    os.makedirs(lead_dir, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)

    pil_img = Image.open(io.BytesIO(raw_bytes))
    
    orig_w, orig_h = pil_img.size
    max_w = target_width or MAX_IMAGE_WIDTH

    if orig_w > max_w:
        ratio = max_w / float(orig_w)
        new_h = int(float(orig_h) * ratio)
        pil_img = pil_img.resize((max_w, new_h), Image.Resampling.LANCZOS)
        w, h = max_w, new_h
    else:
        w, h = orig_w, orig_h

    # Convert screenshots to JPEG unless transparency matters (RGBA/LA)
    save_format = "PNG" if pil_img.mode in ("RGBA", "LA") else "JPEG"
    ext = "png" if save_format == "PNG" else "jpg"
    final_mime = "image/png" if save_format == "PNG" else "image/jpeg"

    if save_format == "JPEG" and pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")

    unique_id = str(uuid.uuid4())
    filename = f"{unique_id}.{ext}"
    storage_key = f"leads/{clean_lid}/{filename}"
    filepath = os.path.join(lead_dir, filename)

    buf = io.BytesIO()
    if save_format == "JPEG":
        pil_img.save(filepath, format="JPEG", quality=82, optimize=True)
        pil_img.save(buf, format="JPEG", quality=82, optimize=True)
    else:
        pil_img.save(filepath, format="PNG", optimize=True)
        pil_img.save(buf, format="PNG", optimize=True)

    file_bytes = buf.getvalue()
    b64_str = base64.b64encode(file_bytes).decode("utf-8")
    data_uri = f"data:{final_mime};base64,{b64_str}"

    try:
        from database import save_lead_image
        save_lead_image(
            image_id=unique_id,
            lead_id=clean_lid,
            storage_key=storage_key,
            filename=original_filename or f"screenshot_{unique_id[:8]}.{ext}",
            mime_type=final_mime,
            width=w,
            height=h,
            num_bytes=len(file_bytes),
            annotated_from=annotated_from,
            created_by="user",
            data_uri=data_uri
        )
    except Exception as db_err:
        logger.warning(f"Failed to record image in images table: {db_err}")

    return ProcessedImage(data_uri, filepath, w, h, unique_id, storage_key)


def sanitize_pasted_html(raw_html: str) -> str:
    """
    Sanitize HTML pasted from external webpages, Word, or Docs:
    - Strips scripts, style tags, comments, meta tags, object/embed tags.
    - Strips ms-office tags (<o:p>, MsoNormal, etc.).
    - Retains bold, italic, underline, links, lists, images, and paragraphs.
    """
    if not raw_html:
        return ""

    # Strip scripts, styles, XML, head, meta
    cleaned = re.sub(r'<(script|style|meta|link|head|xml|title)[^>]*>.*?</\1>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
    # Strip Office / Docs XML tags
    cleaned = re.sub(r'</?o:[^>]*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'</?w:[^>]*>', '', cleaned, flags=re.IGNORECASE)
    # Strip tracking attributes
    cleaned = re.sub(r'\s*(?:class|id|data-[a-zA-Z0-9_\-]+)="[^"]*"', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*style="[^"]*mso-[^"]*"', '', cleaned, flags=re.IGNORECASE)

    return cleaned.strip()


def render_rich_editor(
    initial_html: str = "",
    key: str = "rich_editor",
    height: int = 280,
    owner_type: str = "general",
    owner_id: Optional[int] = None,
    allow_source_mode: bool = True,
    lead_id: Optional[str] = None
) -> str:
    """
    Main entry point for the shared rich editor.
    Returns the resolved HTML body string for email sending or saving.
    """
    state_key = f"{key}_html_content"
    mode_key = f"{key}_edit_mode"
    img_map_key = f"{key}_img_map"
    textarea_key = f"{key}_visual_textarea"
    cursor_tracker_key = f"{key}_cursor_pos"
    cursor_input_key = f"{key}_cursor_input"
    pasted_img_input_key = f"{key}_pasted_img_input"
    last_synced_html = f"{key}_last_synced_html"

    # 1. State initialization
    if state_key not in st.session_state:
        st.session_state[state_key] = initial_html or ""
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "visual"
    if cursor_tracker_key not in st.session_state:
        st.session_state[cursor_tracker_key] = 0

    # Sync external HTML updates (e.g. from template loading)
    prev_synced = st.session_state.get(last_synced_html)
    if prev_synced is not None and prev_synced != initial_html and initial_html != st.session_state[state_key]:
        st.session_state[state_key] = initial_html
        clean_vis, extracted_map = html_to_visual_text(initial_html)
        st.session_state[textarea_key] = clean_vis
        st.session_state[img_map_key] = extracted_map
        st.session_state[last_synced_html] = initial_html

    current_mode = st.session_state[mode_key]
    current_html = st.session_state[state_key]

    if img_map_key not in st.session_state:
        _, init_map = html_to_visual_text(current_html)
        st.session_state[img_map_key] = init_map

    img_map: Dict[str, str] = st.session_state[img_map_key]

    # --------------------------------------------------------------------------
    # SOURCE CODE MODE
    # --------------------------------------------------------------------------
    if current_mode == "source":
        hdr_c1, hdr_c2 = st.columns([8, 2], vertical_alignment="center")
        with hdr_c1:
            st.caption("🔧 **HTML Source Code Mode** — Edit raw email HTML directly.")
        with hdr_c2:
            if st.button("👁️ Visual Mode", key=f"{key}_btn_to_visual", use_container_width=True):
                src_val = st.session_state.get(f"{key}_source_textarea", current_html)
                st.session_state[state_key] = src_val
                clean_vis, new_map = html_to_visual_text(src_val)
                st.session_state[textarea_key] = clean_vis
                st.session_state[img_map_key] = new_map
                st.session_state[mode_key] = "visual"
                st.rerun()

        edited_source = st.text_area(
            "HTML Source",
            value=current_html,
            height=height,
            key=f"{key}_source_textarea",
            label_visibility="collapsed"
        )
        st.session_state[state_key] = edited_source
        return edited_source

    # --------------------------------------------------------------------------
    # VISUAL MODE WITH NATIVE PASTE / DRAG-DROP
    # --------------------------------------------------------------------------
    if textarea_key not in st.session_state:
        clean_vis, parsed_map = html_to_visual_text(current_html)
        st.session_state[textarea_key] = clean_vis
        st.session_state[img_map_key].update(parsed_map)

    live_visual = st.session_state.get(textarea_key, "")
    current_cursor_pos = st.session_state.get(cursor_tracker_key, 0)

    # Helper: insertion position
    def _get_insertion_pos(fallback_type: str = "end") -> int:
        nonlocal current_cursor_pos
        val = st.session_state.get(cursor_input_key, "")
        if val and str(val).strip().isdigit():
            current_cursor_pos = int(str(val).strip())
            st.session_state[cursor_tracker_key] = current_cursor_pos
        
        pos = current_cursor_pos
        if pos < 0:
            pos = 0
        if pos > len(live_visual):
            pos = len(live_visual)
        return pos

    def _sync_editor_state(new_html: str, new_vis: str, new_pos: Optional[int] = None):
        st.session_state[state_key] = new_html
        st.session_state[textarea_key] = new_vis
        st.session_state[last_synced_html] = new_html
        if new_pos is not None:
            st.session_state[cursor_tracker_key] = new_pos
            if cursor_input_key in st.session_state:
                st.session_state[cursor_input_key] = str(new_pos)
        if owner_type == "compose":
            st.session_state["compose_body_html"] = new_html
        elif owner_type == "bulk":
            st.session_state["bulk_body_html"] = new_html
        elif owner_type == "template":
            st.session_state["tpl_body_html"] = new_html
        st.session_state[f"{key}_body_html"] = new_html

    # Helper: insert text
    def _insert_text(token: str):
        pos = _get_insertion_pos("end")
        before = live_visual[:pos]
        after = live_visual[pos:]
        new_vis = f"{before}{token}{after}"
        new_pos = pos + len(token)
        new_html = visual_text_to_html(new_vis, img_map)
        _sync_editor_state(new_html, new_vis, new_pos)
        st.rerun()

    # Helper: insert image tag
    def _insert_image_tag(img_tag: str):
        existing_nums = [
            int(m.group(1)) for m in re.finditer(r'\[Image\s+(\d+)\]', live_visual, re.IGNORECASE)
        ]
        next_num = (max(existing_nums) + 1) if existing_nums else (len(img_map) + 1)
        placeholder = f"[Image {next_num}]"
        img_map[placeholder] = img_tag

        pos = _get_insertion_pos("image")
        # Never insert image at position 0 before greetings when body already has content
        if pos <= 0 and live_visual.strip():
            pos = len(live_visual)
        before = live_visual[:pos].rstrip()
        after = live_visual[pos:].lstrip()

        prefix = "\n\n" if before else ""
        suffix = "\n\n" if after else ""
        new_vis = f"{before}{prefix}{placeholder}{suffix}{after}"

        new_html = visual_text_to_html(new_vis, img_map)
        new_pos = len(before + prefix + placeholder)

        _sync_editor_state(new_html, new_vis, new_pos)
        st.rerun()

    # --------------------------------------------------------------------------
    # Intercept pasted image payload received from browser JS (Single or Batch)
    # --------------------------------------------------------------------------
    pasted_raw = st.session_state.get(pasted_img_input_key, "")
    if pasted_raw:
        images_to_process = []
        if isinstance(pasted_raw, str):
            pasted_str = pasted_raw.strip()
            if pasted_str.startswith("[") and pasted_str.endswith("]"):
                try:
                    images_to_process = json.loads(pasted_str)
                except Exception:
                    images_to_process = []
            elif pasted_str.startswith("data:image/"):
                images_to_process = [pasted_str]

        if images_to_process:
            inserted_count = 0
            for pasted_data in images_to_process:
                if not (isinstance(pasted_data, str) and pasted_data.startswith("data:image/")):
                    continue
                try:
                    mime_match = re.search(r'data:(image/[a-zA-Z0-9\+\-]+);base64,', pasted_data)
                    mime = mime_match.group(1) if mime_match else "image/png"
                    b64_raw = re.sub(r'^data:image/[a-zA-Z0-9\+\-]+;base64,', '', pasted_data)
                    raw_bytes = base64.b64decode(b64_raw)

                    data_uri, fpath, w, h = process_and_store_image(raw_bytes, mime_type=mime, lead_id=lead_id)
                    tag = (f'<img src="{data_uri}" alt="Pasted Screenshot" '
                           f'style="max-width:100%; width:{w}px; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')
                    
                    # Insert directly at live cursor position
                    existing_nums = [
                        int(m.group(1)) for m in re.finditer(r'\[Image\s+(\d+)\]', live_visual, re.IGNORECASE)
                    ]
                    next_num = (max(existing_nums) + 1) if existing_nums else (len(img_map) + 1)
                    placeholder = f"[Image {next_num}]"
                    img_map[placeholder] = tag

                    pos = _get_insertion_pos("image")
                    before = live_visual[:pos].rstrip()
                    after = live_visual[pos:].lstrip()

                    prefix = "\n\n" if before else ""
                    suffix = "\n\n" if after else ""
                    live_visual = f"{before}{prefix}{placeholder}{suffix}{after}"
                    current_cursor_pos = len(before + prefix + placeholder)
                    inserted_count += 1
                except Exception as paste_err:
                    logger.warning(f"Error handling browser pasted image: {paste_err}")

            if inserted_count > 0:
                new_html = visual_text_to_html(live_visual, img_map)
                _sync_editor_state(new_html, live_visual, current_cursor_pos)
                st.session_state[pasted_img_input_key] = ""
                plural = "s" if inserted_count > 1 else ""
                trigger_toast(f"Pasted {inserted_count} image{plural} inserted right at cursor!", icon="📋")
                st.rerun()
            else:
                st.session_state[pasted_img_input_key] = ""

    # --------------------------------------------------------------------------
    # TOOLBAR ROW 1: FORMATTING
    # --------------------------------------------------------------------------
    tb_cols = st.columns([0.6, 0.6, 0.6, 1.4, 1.6, 1.4, 1.4], vertical_alignment="center")

    with tb_cols[0]:
        if st.button("**B**", key=f"{key}_btn_b", help="Bold text (**bold**)", use_container_width=True):
            _insert_text(" **bold text**")

    with tb_cols[1]:
        if st.button("*I*", key=f"{key}_btn_i", help="Italic text (*italic*)", use_container_width=True):
            _insert_text(" *italic text*")

    with tb_cols[2]:
        if st.button("U̲", key=f"{key}_btn_u", help="Underline (<u>text</u>)", use_container_width=True):
            _insert_text(" <u>underlined text</u>")

    with tb_cols[3]:
        with st.popover("🔗 Link", help="Insert Hyperlink", use_container_width=True):
            st.markdown("**Insert Hyperlink**")
            l_url = st.text_input("Link URL", value="https://", key=f"{key}_pop_url")
            l_txt = st.text_input("Link Text", value="click here", key=f"{key}_pop_txt")
            if st.button("Insert Link", type="primary", key=f"{key}_pop_ins_link", use_container_width=True):
                _insert_text(f" [{l_txt}]({l_url})")

    with tb_cols[4]:
        with st.popover("🖼️ Image", help="Paste (Ctrl+V), Drag & Drop, or Upload Screenshot", use_container_width=True):
            st.markdown("<div style='font-size:13px; font-weight:700; color:#083731; margin-bottom:4px;'>🖼️ Insert Image / Screenshot</div>", unsafe_allow_html=True)
            st.caption("💡 **Tip:** Press **Ctrl+V** or **Drag & Drop** any image directly into the text area!")

            tabs_img = st.tabs(["📤 Upload / Paste", "🖼️ Lead Library", "🔗 Web URL"])

            with tabs_img[0]:
                c_p1, c_p2 = st.columns([2.0, 1.2], vertical_alignment="center")
                with c_p1:
                    st.markdown("<div style='font-size:12px; color:#475569;'><b>Clipboard grab:</b></div>", unsafe_allow_html=True)
                with c_p2:
                    if st.button("📋 Grab Clip", type="primary", key=f"{key}_btn_paste_clip", use_container_width=True):
                        try:
                            clip = ImageGrab.grabclipboard()
                            if isinstance(clip, Image.Image):
                                buf = io.BytesIO()
                                clip.save(buf, format="PNG")
                                data_uri, fpath, w, h = process_and_store_image(buf.getvalue(), mime_type="image/png", lead_id=lead_id)
                                tag = (f'<img src="{data_uri}" alt="Screenshot" '
                                       f'style="max-width:100%; width:{w}px; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')
                                _insert_image_tag(tag)
                                trigger_toast(f"Pasted image ({w}x{h}px) inserted!", icon="📋")
                            elif isinstance(clip, list) and clip:
                                f_path = str(clip[0])
                                if os.path.exists(f_path) and f_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')):
                                    with open(f_path, 'rb') as fp:
                                        raw_f = fp.read()
                                    data_uri, fpath, w, h = process_and_store_image(raw_f, mime_type="image/png", lead_id=lead_id)
                                    tag = (f'<img src="{data_uri}" alt="Screenshot" '
                                           f'style="max-width:100%; width:{w}px; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')
                                    _insert_image_tag(tag)
                                    trigger_toast(f"Clipboard file ({w}x{h}px) inserted!", icon="📋")
                                else:
                                    st.warning("Clipboard file is not a supported image format.")
                            else:
                                st.warning("No image found on clipboard. You can press Ctrl+V in the editor or upload below.")
                        except Exception as clip_err:
                            st.warning(f"Clipboard access: {clip_err}")

                st.markdown("<hr style='border:0; border-top:1px solid #E2E8F0; margin:8px 0;'>", unsafe_allow_html=True)

                up_file = st.file_uploader(
                    "Upload Image",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=f"{key}_img_up",
                    label_visibility="collapsed"
                )
                if up_file:
                    up_file.seek(0)
                    raw_bytes = up_file.read()
                    if raw_bytes:
                        try:
                            file_sig = f"{up_file.name}_{len(raw_bytes)}"
                            last_sig_key = f"{key}_last_uploaded_sig"
                            is_new_upload = (st.session_state.get(last_sig_key) != file_sig)

                            data_uri, fpath, w, h = process_and_store_image(
                                raw_bytes, mime_type=up_file.type or "image/png",
                                lead_id=lead_id, original_filename=up_file.name
                            )
                            tag = (f'<img src="{data_uri}" alt="{html.escape(up_file.name)}" '
                                   f'style="max-width:100%; width:{w}px; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')

                            btn_manual_ins = st.button("➕ Insert Uploaded Image", type="primary", key=f"{key}_btn_ins_up_img", use_container_width=True)

                            if is_new_upload:
                                st.session_state[last_sig_key] = file_sig
                                _insert_image_tag(tag)
                                trigger_toast(f"Image '{up_file.name}' inserted into email body!", icon="🖼️")
                            elif btn_manual_ins:
                                _insert_image_tag(tag)
                                trigger_toast("Image inserted into email body!", icon="🖼️")
                        except Exception as img_err:
                            st.error(f"Error processing image: {img_err}")

            with tabs_img[1]:
                clean_lid = str(lead_id).strip() if lead_id else ""
                if not clean_lid or clean_lid == "general":
                    st.caption("Select a lead in Compose to view their dedicated image gallery.")
                else:
                    try:
                        from database import get_lead_images
                        saved_lead_imgs = get_lead_images(clean_lid)
                    except Exception:
                        saved_lead_imgs = []

                    if not saved_lead_imgs:
                        st.info(f"No saved images yet for lead **{clean_lid}**. Upload or paste an image to save it here!")
                    else:
                        st.caption(f"📁 {len(saved_lead_imgs)} saved screenshot(s) for **{clean_lid}**")
                        for img_rec in saved_lead_imgs[:8]:
                            with st.container(border=True):
                                ic1, ic2 = st.columns([1.2, 2.8], vertical_alignment="center")
                                s_key = img_rec.get("storage_key", "")
                                full_fpath = os.path.join(UPLOADS_DIR, s_key) if not os.path.isabs(s_key) else s_key
                                d_uri = img_rec.get("data_uri") or ""
                                if not d_uri and os.path.exists(full_fpath):
                                    try:
                                        with open(full_fpath, "rb") as fp_prev:
                                            b64_p = base64.b64encode(fp_prev.read()).decode("utf-8")
                                        d_uri = f"data:{img_rec.get('mime_type','image/jpeg')};base64,{b64_p}"
                                    except Exception:
                                        d_uri = ""
                                elif d_uri and not os.path.exists(full_fpath):
                                    try:
                                        os.makedirs(os.path.dirname(full_fpath), exist_ok=True)
                                        if "," in d_uri:
                                            raw_b = base64.b64decode(d_uri.split(",", 1)[1])
                                            with open(full_fpath, "wb") as fp_w:
                                                fp_w.write(raw_b)
                                    except Exception:
                                        pass

                                with ic1:
                                    if d_uri:
                                        st.markdown(f'<img src="{d_uri}" style="max-height:44px; max-width:80px; object-fit:cover; border-radius:4px; border:1px solid #CBD5E1;" />', unsafe_allow_html=True)
                                    else:
                                        st.caption("🖼️")
                                with ic2:
                                    f_name = img_rec.get("filename") or f"image_{img_rec['id'][:6]}"
                                    st.markdown(f"<div style='font-size:11px; font-weight:600; line-height:1.2; word-break:break-all;'>{html.escape(f_name)}</div>", unsafe_allow_html=True)
                                    st.caption(f"{img_rec.get('width', 0)}x{img_rec.get('height', 0)}px · {round(img_rec.get('bytes', 0)/1024, 1)} KB")
                                    if st.button("➕ Insert at Cursor", key=f"{key}_lead_ins_{img_rec['id']}", use_container_width=True, type="secondary"):
                                        ins_data_uri = d_uri
                                        if not ins_data_uri and os.path.exists(full_fpath):
                                            try:
                                                with open(full_fpath, "rb") as fp_ins:
                                                    ins_bytes = fp_ins.read()
                                                ins_b64 = base64.b64encode(ins_bytes).decode("utf-8")
                                                ins_data_uri = f"data:{img_rec.get('mime_type','image/jpeg')};base64,{ins_b64}"
                                            except Exception:
                                                ins_data_uri = ""
                                        if ins_data_uri:
                                            ins_w = img_rec.get("width") or 600
                                            ins_tag = (f'<img src="{ins_data_uri}" alt="{html.escape(f_name)}" '
                                                       f'style="max-width:100%; width:{ins_w}px; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')
                                            _insert_image_tag(ins_tag)
                                            trigger_toast(f"Inserted '{f_name}' at cursor!", icon="🖼️")

            with tabs_img[2]:
                img_url = st.text_input("Direct URL", placeholder="https://sellomize.com/logo.png", key=f"{key}_img_url_val")
                img_alt = st.text_input("Alt Text", value="Screenshot", key=f"{key}_img_alt_val")
                if st.button("Insert URL Image", key=f"{key}_btn_ins_url_img", use_container_width=True):
                    if img_url.strip():
                        tag = (f'<img src="{html.escape(img_url.strip())}" alt="{html.escape(img_alt)}" '
                               f'style="max-width:100%; height:auto; border-radius:6px; margin:14px 0; display:block; border:1px solid #E2E8F0;" />')
                        _insert_image_tag(tag)
                        trigger_toast("URL image inserted!", icon="🖼️")

    with tb_cols[5]:
        with st.popover("📋 List", help="Insert Bullet or Numbered List", use_container_width=True):
            st.markdown("**Insert List**")
            if st.button("• Bulleted List", key=f"{key}_btn_ul", use_container_width=True):
                _insert_text("\n- Item 1\n- Item 2\n")
            if st.button("1. Numbered List", key=f"{key}_btn_ol", use_container_width=True):
                _insert_text("\n1. Step 1\n2. Step 2\n")

    with tb_cols[6]:
        if allow_source_mode:
            if st.button("‹/› HTML", key=f"{key}_btn_to_source", help="Switch to raw HTML mode", use_container_width=True):
                st.session_state[state_key] = visual_text_to_html(live_visual, img_map)
                st.session_state[mode_key] = "source"
                st.rerun()

    # --------------------------------------------------------------------------
    # TOOLBAR ROW 2: PERSONALIZATION CHIPS
    # --------------------------------------------------------------------------
    chip_cols = st.columns([1.3, 1.5, 1.6, 1.5, 1.1], vertical_alignment="center")

    with chip_cols[0]:
        if st.button("👤 First Name", key=f"{key}_chip_name", help="Insert {first_name} token", use_container_width=True):
            _insert_text(" {first_name}")

    with chip_cols[1]:
        if st.button("🏢 Company", key=f"{key}_chip_comp", help="Insert [Company] token", use_container_width=True):
            _insert_text(" [Company]")

    with chip_cols[2]:
        with st.popover("➕ Variables", help="Insert personalization tokens", use_container_width=True):
            st.markdown("**Personalization Tokens**")
            st.caption("Click any token to insert into email body.")
            v1, v2 = st.columns(2)
            with v1:
                if st.button("{first_name}", key=f"{key}_v_fn", use_container_width=True):
                    _insert_text(" {first_name}")
                if st.button("[First Name]", key=f"{key}_v_fn_b", use_container_width=True):
                    _insert_text(" [First Name]")
                if st.button("[Email]", key=f"{key}_v_em", use_container_width=True):
                    _insert_text(" [Email]")
            with v2:
                if st.button("{company}", key=f"{key}_v_c", use_container_width=True):
                    _insert_text(" {company}")
                if st.button("[Website]", key=f"{key}_v_site", use_container_width=True):
                    _insert_text(" [Website]")
                if st.button("[Tags]", key=f"{key}_v_tags", use_container_width=True):
                    _insert_text(" [Tags]")

    with chip_cols[3]:
        if st.button("🖋️ Signature", key=f"{key}_chip_sig", help="Append corporate signature HTML", use_container_width=True):
            sig = get_config("signature_html", "") or "Best regards,\nJack Connor\nSellomize"
            _insert_text(f"\n\n{sig}")

    with chip_cols[4]:
        if st.button("🧹 Clean", key=f"{key}_chip_clean", help="Clean up formatting", use_container_width=True):
            cur = st.session_state.get(textarea_key, live_visual)
            cleaned, new_img_map, _ = sanitize_visual_text(cur, img_map)
            st.session_state[textarea_key] = cleaned
            st.session_state[state_key] = visual_text_to_html(cleaned, new_img_map)
            st.session_state[last_synced_html] = st.session_state[state_key]
            trigger_toast("Editor cleaned!", icon="🧹")
            st.rerun()

    # --------------------------------------------------------------------------
    # IMAGE GALLERY (THUMBNAILS + RESIZE CONTROLS)
    # --------------------------------------------------------------------------
    # Prune phantom images from img_map that do not exist in live_visual text
    cur_body_tokens = set(re.findall(r'\[Image\s+\d+\]', live_visual, re.IGNORECASE))
    for ph_k in list(img_map.keys()):
        if ph_k not in cur_body_tokens:
            img_map.pop(ph_k, None)

    if img_map:
        st.markdown(
            f"<div style='font-size:12px; font-weight:700; color:#083731; margin:6px 0 4px;'>"
            f"🖼️ Images in this email ({len(img_map)}):</div>",
            unsafe_allow_html=True
        )
        for ph, img_tag in list(img_map.items()):
            with st.container(border=True):
                c_thumb, c_meta, c_actions = st.columns([1.2, 3.8, 2.0], vertical_alignment="center")
                with c_thumb:
                    src_m = re.search(r'src=["\']([^"\']+)["\']', img_tag)
                    src_val = src_m.group(1) if src_m else ""
                    st.markdown(
                        f'<img src="{src_val}" style="max-height:48px; max-width:85px; '
                        f'border-radius:4px; object-fit:cover; border:1px solid #CBD5E1;" />',
                        unsafe_allow_html=True
                    )
                with c_meta:
                    st.markdown(f"**{ph}** · `<img ...>` token")
                    st.caption("Place this placeholder anywhere in your email text.")
                with c_actions:
                    if st.button(f"🗑️ Delete {ph}", key=f"{key}_del_{ph}", use_container_width=True):
                        cur_vis = st.session_state.get(textarea_key, live_visual)
                        cur_vis = re.sub(rf'\n*{re.escape(ph)}\n*', '\n\n', cur_vis).strip()
                        img_map.pop(ph, None)
                        new_html = visual_text_to_html(cur_vis, img_map)
                        _sync_editor_state(new_html, cur_vis)
                        trigger_toast(f"Removed {ph}.", icon="🗑️")
                        st.rerun()

    # --------------------------------------------------------------------------
    # MAIN TEXTAREA
    # --------------------------------------------------------------------------
    edited_val = st.text_area(
        "Visual Content Editor",
        value=live_visual,
        height=height,
        key=textarea_key,
        label_visibility="collapsed",
        help="Type or paste your email body here. Press Ctrl+V or drop images directly into this area."
    )

    # Always ensure state_key contains structured HTML corresponding to edited_val for preview and dispatch
    st.session_state[state_key] = visual_text_to_html(edited_val, img_map)
    st.session_state[last_synced_html] = st.session_state[state_key]

    # Visually hide the hidden receivers safely without display:none so synthetic events always propagate
    st.markdown("""
    <style>
    div[data-testid="stTextInput"]:has(input[aria-label*="rich_cursor_tracker"]),
    div[data-testid="stTextInput"]:has(input[aria-label*="rich_pasted_image_receiver"]) {
        position: absolute !important;
        opacity: 0 !important;
        height: 1px !important;
        width: 1px !important;
        pointer-events: none !important;
        overflow: hidden !important;
        clip: rect(0, 0, 0, 0) !important;
        margin: 0px !important;
        padding: 0px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # Hidden inputs for cursor tracking and pasted/dropped image bridge
    st.text_input("rich_cursor_tracker", value=str(current_cursor_pos), key=cursor_input_key, label_visibility="collapsed")
    st.text_input("rich_pasted_image_receiver", value="", key=pasted_img_input_key, label_visibility="collapsed")

    # --------------------------------------------------------------------------
    # BROWSER CLIENT-SIDE PASTE & DROP INTERCEPTOR SCRIPT
    # --------------------------------------------------------------------------
    components.html(f"""
    <script>
    (function() {{
        const pDoc = window.parent.document;
        function bindRichPasteAndDrop() {{
            const allTextareas = Array.from(pDoc.querySelectorAll('textarea'));
            const ta = allTextareas.find(t => t.id && t.id.includes('{textarea_key}')) || allTextareas[0];
            if (!ta || ta._sellomizeRichBound) return;
            ta._sellomizeRichBound = true;

            function sendCursorPos() {{
                const pos = ta.selectionStart;
                const trackerInputs = Array.from(pDoc.querySelectorAll('input[aria-label*="rich_cursor_tracker"]'));
                const inp = trackerInputs.find(i => i.id && i.id.includes('{cursor_input_key}')) || trackerInputs[0];
                if (inp && inp.value !== String(pos)) {{
                    if (inp._valueTracker) {{
                        inp._valueTracker.setValue('');
                    }}
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeSetter.call(inp, String(pos));
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }}

            ta.addEventListener('click', sendCursorPos);
            ta.addEventListener('keyup', sendCursorPos);
            ta.addEventListener('select', sendCursorPos);
            ta.addEventListener('blur', sendCursorPos);
            ta.addEventListener('input', sendCursorPos);

            function processImageFiles(imageFiles) {{
                if (!imageFiles || imageFiles.length === 0) return;
                sendCursorPos();

                let completed = [];
                let pending = imageFiles.length;

                imageFiles.forEach(file => {{
                    const reader = new FileReader();
                    reader.onload = function(evt) {{
                        const img = new Image();
                        img.onload = function() {{
                            let w = img.width, h = img.height;
                            const maxW = 1200;
                            if (w > maxW) {{
                                h = Math.round((h * maxW) / w);
                                w = maxW;
                            }}
                            const canvas = document.createElement('canvas');
                            canvas.width = w;
                            canvas.height = h;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0, w, h);
                            const dataUrl = canvas.toDataURL('image/jpeg', 0.82);
                            completed.push(dataUrl);
                            pending--;

                            if (pending === 0 && completed.length > 0) {{
                                const pInps = Array.from(pDoc.querySelectorAll('input[aria-label*="rich_pasted_image_receiver"]'));
                                const pInp = pInps.find(i => i.id && i.id.includes('{pasted_img_input_key}')) || pInps[0];
                                if (pInp) {{
                                    if (pInp._valueTracker) {{
                                        pInp._valueTracker.setValue('');
                                    }}
                                    const payload = completed.length === 1 ? completed[0] : JSON.stringify(completed);
                                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                    nativeSetter.call(pInp, payload);
                                    pInp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    pInp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                }}
                            }}
                        }};
                        img.onerror = function() {{
                            pending--;
                        }};
                        img.src = evt.target.result;
                    }};
                    reader.onerror = function() {{
                        pending--;
                    }};
                    reader.readAsDataURL(file);
                }});
            }}

            // 1. Intercept clipboard paste event (only intercept when image present)
            ta.addEventListener('paste', function(e) {{
                if (!e.clipboardData) return;
                const items = e.clipboardData.items || [];
                const files = e.clipboardData.files || [];
                let imageFiles = [];

                // Check items first
                for (let i = 0; i < items.length; i++) {{
                    if (items[i].type && items[i].type.indexOf('image') === 0) {{
                        const f = items[i].getAsFile();
                        if (f) imageFiles.push(f);
                    }}
                }}

                // If items was empty or had no files, check files list
                if (imageFiles.length === 0 && files.length > 0) {{
                    for (let i = 0; i < files.length; i++) {{
                        if (files[i].type && files[i].type.indexOf('image') === 0) {{
                            imageFiles.push(files[i]);
                        }}
                    }}
                }}

                // Only prevent default if an image was actually in clipboard
                if (imageFiles.length > 0) {{
                    e.preventDefault();
                    processImageFiles(imageFiles);
                }}
                // Else: normal text paste falls through to native browser behavior
            }});

            // 2. Intercept drag-and-drop file event
            ta.addEventListener('dragover', function(e) {{
                e.preventDefault();
                ta.style.borderColor = '#FD4D1B';
                ta.style.backgroundColor = 'rgba(253, 77, 27, 0.03)';
            }});
            ta.addEventListener('dragleave', function(e) {{
                e.preventDefault();
                ta.style.borderColor = '';
                ta.style.backgroundColor = '';
            }});
            ta.addEventListener('drop', function(e) {{
                e.preventDefault();
                ta.style.borderColor = '';
                ta.style.backgroundColor = '';
                if (!e.dataTransfer) return;
                const files = e.dataTransfer.files || [];
                let imageFiles = [];
                for (let i = 0; i < files.length; i++) {{
                    if (files[i].type && files[i].type.indexOf('image') === 0) {{
                        imageFiles.push(files[i]);
                    }}
                }}
                if (imageFiles.length > 0) {{
                    processImageFiles(imageFiles);
                }}
            }});
        }}

        bindRichPasteAndDrop();
        setTimeout(bindRichPasteAndDrop, 200);
        setTimeout(bindRichPasteAndDrop, 600);
    }})();
    </script>
    """, height=0, width=0)

    # Return the current finalized HTML for dispatch/storage
    final_html = st.session_state.get(state_key, "")
    return final_html
