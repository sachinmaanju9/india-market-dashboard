"""
India market dashboard - entry point.
Run locally:  python -m streamlit run streamlit_app.py
"""
import streamlit as st

st.set_page_config(page_title="India market dashboard", page_icon="📊", layout="wide")
nav = st.navigation([
    st.Page("views/sectors.py", title="Sectors", icon="📊", default=True),
    st.Page("views/screeners.py", title="Screeners", icon="🔎"),
])
nav.run()
