import streamlit as st

st.set_page_config(
    page_title="Yangon TB Project",
    page_icon="🫁",
    layout="wide",
    initial_sidebar_state="expanded",
)

dashboard_page = st.Page(
    "pages/dashboard.py",
    title="DASHBOARD",
    icon="📊",
    default=True,
)
database_page = st.Page(
    "pages/database.py",
    title="DATABASE",
    icon="💾",
)

pg = st.navigation({"Main App": [dashboard_page, database_page]})
pg.run()

