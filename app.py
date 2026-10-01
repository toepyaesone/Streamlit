# import streamlit as st

# # Configure global page settings
# st.set_page_config(
#     page_title="YgnTBPro System",
#     page_icon="🫁",
#     layout="wide",
#     initial_sidebar_state="expanded",
# )

# # Multi-page setup using Streamlit's st.navigation
# dashboard_page = st.Page("pages/dashboard.py", title="Dashboard", icon="📊", default=True)
# database_page = st.Page("pages/database.py", title="Database Explorer", icon="💾")

# pg = st.navigation({
#     "Main App": [dashboard_page, database_page]
# })

# pg.run()

import os
import streamlit as st
from supabase import create_client, Client

# Must be the first Streamlit command
st.set_page_config(
    page_title="YgnTBPro Management System",
    page_icon="🩺",
    layout="wide"
)

# Supabase Credentials Setup
DEFAULT_URL = "https://kocihpxevlowqbguhstf.supabase.co"
DEFAULT_KEY = "sb_publishable_JtrNLjMNSvZ5LzvXKbv2xw_mj-hl5MD"

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", DEFAULT_URL))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", DEFAULT_KEY))

# Global Supabase Client
@st.cache_resource
def get_base_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

base_supabase = get_base_client()

def get_user_client():
    session = st.session_state.get("session")
    if not session:
        return base_supabase
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client.postgrest.auth(session.access_token)
    return client

# Global Session State Initialization
defaults = {
    "session": None,
    "user_role": None,
    "grid_version": 0,
    "filter_version": 0,
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),
    "editor_df": None,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# Top Landing Header
st.title("🩺 YgnTBPro Portal")
st.markdown("Welcome to the YgnTBPro Management Information System.")
st.markdown("Use the navigation sidebar to access modules:")
st.markdown("- **📊 Dashboard:** Public analytics and program summary metrics.")
st.markdown("- **💾 Database:** Query, edit, and sync records *(Requires user login)*.")