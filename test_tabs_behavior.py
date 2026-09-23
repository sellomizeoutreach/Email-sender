from streamlit.testing.v1 import AppTest

script = """
import streamlit as st

TAB_NAMES = [
    "Leads",
    "Templates",
    "Dispatch",
    "Analytics",
    "Settings"
]

col1, col2 = st.columns(2)
with col1:
    if st.button("Send One-Time Batch"):
        st.session_state["main_app_tabs"] = "Dispatch"
        st.session_state["camp_audience_mode"] = "Cherry-Pick"
        st.session_state["camp_cherry_pick_multisel"] = [1, 2]
        st.session_state["camp_seq_touches"] = "Once"
        st.rerun()

tabs = st.tabs(TAB_NAMES, key="main_app_tabs", on_change="rerun")

with tabs[0]:
    st.write("LEADS TAB OPEN")

with tabs[2]:
    st.write("DISPATCH TAB OPEN")
    st.write(f"Audience mode: {st.session_state.get('camp_audience_mode')}")
    st.write(f"Cherry pick: {st.session_state.get('camp_cherry_pick_multisel')}")
    st.write(f"Touches: {st.session_state.get('camp_seq_touches')}")
"""

at = AppTest.from_string(script)
at.run()
print("Initial:", [m.value for m in at.markdown])

at.button[0].click()
at.run()
print("After Send One-Time Batch click:", [m.value for m in at.markdown])
print("Active tab in session_state:", at.session_state["main_app_tabs"])
