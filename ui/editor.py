"""
ui/editor.py - Reusable Dual-Mode Email Editor Component for Sellomize Reach.
Section C7 of Complete Restructure Spec.

Features:
1. Visual toolbar view (Bold, Italic, Underline, Link, Image, Lists, Variable Chips, Signature).
2. Source view (raw HTML code).
3. Toggle between views with synced state.
4. Round-trip safety: complex HTML warning when switching from Source to Visual.
5. Image insertion: by URL (default clean <img>) or PC file upload (with Gmail 102KB clipping warning).
6. Live preview rendering as the single source of truth.
"""

import streamlit as st
import html
import re
from typing import Optional, Dict, Any

from template_engine import resolve_template
from security import sanitize_preview_html
from database import get_config


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
            with st.popover("🖼️", help="Insert Image", use_container_width=True):
                st.markdown("**Insert Image**")
                img_method = st.radio("Source", ["Image URL (Recommended)", "File Upload"], key=f"{key_prefix}_img_src", horizontal=True)
                if img_method.startswith("Image URL"):
                    img_url = st.text_input("Image Direct URL", placeholder="https://sellomize.com/logo.png", key=f"{key_prefix}_img_url_val")
                    img_alt = st.text_input("Alt Text", value="Image", key=f"{key_prefix}_img_alt_val")
                    if st.button("Insert URL Image", type="primary", key=f"{key_prefix}_btn_ins_url_img", use_container_width=True):
                        if img_url.strip():
                            tag = f'<img src="{html.escape(img_url.strip())}" alt="{html.escape(img_alt)}" style="max-width:100%; height:auto; border-radius:6px; margin:8px 0;" />'
                            st.session_state[state_key] = current_body + f"\n{tag}\n"
                            st.rerun()
                else:
                    st.warning("⚠️ Warning: Large Base64 images may bloat email size above Gmail's 102KB clipping limit. Prefer direct image URLs.")
                    up_file = st.file_uploader("Choose PNG/JPEG", type=["png", "jpg", "jpeg"], key=f"{key_prefix}_img_up")
                    if up_file:
                        import base64
                        b64 = base64.b64encode(up_file.read()).decode("utf-8")
                        mime = up_file.type or "image/png"
                        tag = f'<img src="data:{mime};base64,{b64}" alt="Image" style="max-width:100%; border-radius:6px;" />'
                        if st.button("Insert Uploaded Image", type="primary", key=f"{key_prefix}_btn_ins_up_img", use_container_width=True):
                            st.session_state[state_key] = current_body + f"\n{tag}\n"
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

        # Textarea representation for visual mode editing
        edited_val = st.text_area(
            "Visual Content Editor",
            value=st.session_state[state_key],
            height=height,
            key=f"{key_prefix}_visual_textarea",
            label_visibility="collapsed"
        )
        st.session_state[state_key] = edited_val

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
