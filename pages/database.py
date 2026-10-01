import os
import pandas as pd
import streamlit as st
from functions import functionGetDataFromTable

# -----------------------------------------------------------------------------
# Configuration & Data Retrieval
# -----------------------------------------------------------------------------
SUPABASE_URL_ygntbpro = "https://kocihpxevlowqbguhstf.supabase.co"
SUPABASE_KEY_ygntbpro = "sb_publishable_JtrNLjMNSvZ5LzvXKbv2xw_mj-hl5MD"

SUPABASE_URL = st.secrets.get("SUPABASE_URL_ygntbpro", os.getenv("SUPABASE_URL", SUPABASE_URL_ygntbpro))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY_ygntbpro", os.getenv("SUPABASE_KEY", SUPABASE_KEY_ygntbpro))

@st.cache_data(ttl=600, show_spinner=False)
def fetch_table(table_name: str) -> pd.DataFrame:
    df = functionGetDataFromTable(table_name, SUPABASE_URL, SUPABASE_KEY, page_size=1000)
    if df is None:
        return pd.DataFrame()
    return df

st.title("💾 Database Explorer & Query Engine")
st.caption("Inspect, search, filter, and export live records directly from Supabase")

# Select Table
table_choice = st.selectbox(
    "Select Database Table",
    ["ygntbpro", "target"],
    index=0,
    help="Select which table to view from Supabase",
)

with st.spinner(f"Fetching raw data for '{table_choice}'..."):
    df_raw = fetch_table(table_choice)

if df_raw.empty:
    st.warning(f"No records returned for table '{table_choice}'.")
    st.stop()

# -----------------------------------------------------------------------------
# Metrics Overview
# -----------------------------------------------------------------------------
m1, m2, m3 = st.columns(3)
m1.metric("Total Records", f"{len(df_raw):,}")
m2.metric("Total Columns", f"{len(df_raw.columns):,}")
m3.metric("Memory Usage", f"{df_raw.memory_usage(deep=True).sum() / (1024 * 1024):.2f} MB")

st.divider()

# -----------------------------------------------------------------------------
# Interactive Controls
# -----------------------------------------------------------------------------
ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 2, 1])

with ctrl_col1:
    search_query = st.text_input("🔍 Global Keyword Search", placeholder="Type keyword to filter rows...")

with ctrl_col2:
    selected_cols = st.multiselect(
        "Select Columns to Display",
        options=list(df_raw.columns),
        default=list(df_raw.columns)[:15] if len(df_raw.columns) > 15 else list(df_raw.columns)
    )

with ctrl_col3:
    max_display = st.number_input("Max Rows to Render", min_value=10, max_value=10000, value=500, step=50)

# Filter Data by Search Term
df_display = df_raw.copy()

if search_query:
    # Match text across string representations of all cells
    mask = df_display.astype(str).apply(
        lambda row: row.str.contains(search_query, case=False, na=False).any(),
        axis=1
    )
    df_display = df_display[mask]

# Select target columns
if selected_cols:
    df_display = df_display[selected_cols]

# -----------------------------------------------------------------------------
# Data Table Render
# -----------------------------------------------------------------------------
st.subheader(f"Data Preview ({len(df_display):,} records found)")

st.dataframe(
    df_display.head(max_display),
    use_container_width=True,
    hide_index=False,
)

# -----------------------------------------------------------------------------
# Data Export Options
# -----------------------------------------------------------------------------
st.divider()
st.subheader("📥 Export Data")

e_col1, e_col2 = st.columns(2)

with e_col1:
    csv_data = df_display.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📄 Download CSV (Filtered)",
        data=csv_data,
        file_name=f"{table_choice}_export.csv",
        mime="text/csv",
        use_container_width=True,
    )

with e_col2:
    # Summary statistical preview
    with st.popover("📊 View Data Types & Null Counts"):
        info_df = pd.DataFrame({
            "Column": df_raw.columns,
            "Data Type": df_raw.dtypes.astype(str),
            "Non-Null Count": df_raw.notnull().sum().values,
            "Null Count": df_raw.isnull().sum().values,
        })
        st.dataframe(info_df, use_container_width=True)