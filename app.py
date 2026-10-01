import streamlit as st

# Configure global page settings
st.set_page_config(
    page_title="YgnTBPro System",
    page_icon="🫁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Multi-page setup using Streamlit's st.navigation
dashboard_page = st.Page("pages/dashboard.py", title="Dashboard", icon="📊", default=True)
database_page = st.Page("pages/database.py", title="Database Explorer", icon="💾")

pg = st.navigation({
    "Main App": [dashboard_page, database_page]
})

pg.run()

