"""
Corporate HTML Signature Studio Tab for Sellomize Reach.
Dedicated tab for designing, editing, previewing, and saving rich HTML email signatures.
"""

import streamlit as st
from database import get_config, set_config
from ui.components import render_html_preview

DEFAULT_SIGNATURE_TEMPLATE = """<div>
<table cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif; max-width:650px; color:#083731;">
  <tbody>
    <tr>
      <td style="padding-right:20px; vertical-align:top;">
        <img src="https://sellomize.com/wp-content/uploads/2026/05/cropped-amazon-aligators.png" width="110" style="display:block;" alt="Sellomize Logo">
        <br>
      </td>
      <td style="padding:0 20px; border-left:2px solid #FD4D1B; vertical-align:top;">
        <div style="font-size:20px; font-weight:bold; color:#083731;">Jack Connor</div>
        <div style="font-size:14px; color:#FD4D1B; margin:4px 0 8px;">Business Development Officer</div>
        <div style="font-size:14px; line-height:1.7;">
          <div><b>Sellomize</b></div>
          <div>Amazon Brand Management</div>
        </div>
        <div style="margin-top:10px; font-size:14px; line-height:1.7;">
          <div><a href="mailto:info@sellomize.com" style="color:#083731; text-decoration:none;">info@sellomize.com</a></div>
          <div>+1 646-351-0812</div>
          <div><a href="https://sellomize.com" target="_blank" style="color:#083731; text-decoration:none;">sellomize.com</a></div>
        </div>
      </td>
    </tr>
  </tbody>
</table>
</div>"""


def render_signature_tab():
    """Render dedicated Corporate HTML Signature workspace."""
    st.subheader("Corporate HTML Signature")
    st.caption("Manage your team's HTML email signature appended automatically to outgoing campaign emails.")

    sig_key = "sig_shared_content"
    if sig_key not in st.session_state:
        st.session_state[sig_key] = get_config("signature_html", "")

    col_editor, col_preview = st.columns([1.1, 1.1])

    with col_editor:
        st.markdown("##### Signature Code")
        st.caption("Paste or edit raw HTML table code below. Inline CSS is recommended for maximum email client compatibility.")

        col_act1, col_act2 = st.columns([1.4, 1])
        with col_act1:
            if st.button("Load Sellomize Template", key="tab_load_sig_btn", use_container_width=True):
                st.session_state[sig_key] = DEFAULT_SIGNATURE_TEMPLATE
                set_config("signature_html", DEFAULT_SIGNATURE_TEMPLATE)
                st.success("Loaded default Sellomize signature template.")
                st.rerun()
        with col_act2:
            confirm_clear = st.checkbox("Confirm clear", key="tab_confirm_clear_sig")
            if st.button("Clear Signature", key="tab_clear_sig_btn", use_container_width=True, disabled=not confirm_clear):
                st.session_state[sig_key] = ""
                set_config("signature_html", "")
                st.info("Signature cleared.")
                st.rerun()

        sig_code = st.text_area(
            "HTML Signature Code",
            value=st.session_state[sig_key],
            height=280,
            key="tab_sig_textarea",
            help="Raw HTML signature. Appended to the bottom of all dispatched outreach emails."
        )
        st.session_state[sig_key] = sig_code

        if st.button("Save Signature", type="primary", use_container_width=True, key="tab_save_sig_btn"):
            set_config("signature_html", sig_code.strip())
            st.success("Corporate HTML signature saved successfully.")
            st.rerun()

    with col_preview:
        st.markdown("##### Live Visual Preview")
        st.caption("Real-time preview rendered exactly as prospect mail clients see it.")

        active_sig = st.session_state[sig_key].strip() or get_config("signature_html", "").strip()
        if active_sig:
            render_html_preview(active_sig, height=340)
        else:
            st.info("No active signature configured. Click 'Load Sellomize Template' on the left or paste your HTML code.")
