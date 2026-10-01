import os
from datetime import timedelta

import pandas as pd
import streamlit as st
from supabase import Client, create_client

try:
    from functions import functionGetDataFromTable
except ImportError:
    functionGetDataFromTable = None


# ============================================================
# Configuration
# ============================================================

SUPABASE_URL = st.secrets.get(
    "SUPABASE_URL_ygntbpro",
    os.getenv("SUPABASE_URL_ygntbpro", os.getenv("SUPABASE_URL", "")),
)
SUPABASE_KEY = st.secrets.get(
    "SUPABASE_KEY_ygntbpro",
    os.getenv("SUPABASE_KEY_ygntbpro", os.getenv("SUPABASE_KEY", "")),
)

TABLE_NAME = "ygntbpro"
TARGET_TABLE = "target"
# MAX_ROWS = 1000
BATCH_SIZE = 1000

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error(
        "Supabase credentials are missing. Configure "
        "`SUPABASE_URL_ygntbpro` and `SUPABASE_KEY_ygntbpro` "
        "in Streamlit Secrets or environment variables."
    )
    st.stop()


# ============================================================
# Supabase clients
# ============================================================

@st.cache_resource
def get_base_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


base_supabase = get_base_client()


def get_user_client() -> Client:
    """Return a client using the current authenticated access token."""
    session = st.session_state.get("session")
    if not session:
        return base_supabase

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)

    if access_token and refresh_token:
        try:
            client.auth.set_session(access_token, refresh_token)
            return client
        except Exception:
            pass

    if access_token:
        client.postgrest.auth(access_token)

    return client


# ============================================================
# Session state
# ============================================================

DEFAULTS = {
    "session": None,
    "user_role": None,
    "grid_version": 0,
    "filter_version": 0,
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),
    "editor_df": None,
    "editor_source_df": None,
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# Authentication
# ============================================================

def login_user(email: str, password: str):
    try:
        response = base_supabase.auth.sign_in_with_password(
            {"email": email.strip(), "password": password}
        )

        if not response.session:
            return False, "Login failed: no authenticated session was returned."

        st.session_state.session = response.session
        user_client = get_user_client()

        role_result = (
            user_client.table("user_roles")
            .select("role")
            .eq("user_id", response.session.user.id)
            .limit(1)
            .execute()
        )

        st.session_state.user_role = (
            role_result.data[0].get("role", "viewer")
            if role_result.data
            else "viewer"
        )

        return True, "Login successful."

    except Exception as exc:
        return False, str(exc)


def logout_user():
    try:
        base_supabase.auth.sign_out()
    except Exception:
        pass

    for key, value in DEFAULTS.items():
        st.session_state[key] = value

    st.rerun()


if not st.session_state.session:
    st.title("🔑 YgnTBPro Database Login")

    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        login_clicked = st.form_submit_button(
            "Login", use_container_width=True, type="primary"
        )

    if login_clicked:
        success, message = login_user(email, password)
        if success:
            st.success(message)
            st.rerun()
        else:
            st.error(message)

    st.stop()


user_role = st.session_state.user_role or "viewer"
can_edit = user_role in {"editor", "admin"}
can_add = user_role == "admin"
can_delete = user_role == "admin"

st.sidebar.title("YgnTBPro System")
st.sidebar.write(f"**User Role:** `{user_role}`")
if st.sidebar.button("Logout", use_container_width=True):
    logout_user()


# ============================================================
# Utility functions
# ============================================================

def resolve_column(columns, *candidates):
    """Resolve a column name case-insensitively without changing the DB schema."""
    lookup = {str(c).lower(): c for c in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        found = lookup.get(str(candidate).lower())
        if found is not None:
            return found
    return None


def values_equal(left, right) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    return left == right


def clean_value(value):
    """Convert Pandas/NumPy values to JSON-serializable Python values."""
    if value is None:
        return None

    # Handle pandas/NumPy missing values
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    # NumPy scalar -> native Python scalar
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass

    # Pandas Timestamp
    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    # Python date/datetime
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except (ValueError, TypeError):
            pass

    return value
    
def make_json_safe(data):
    """Recursively convert Pandas/NumPy values to JSON-safe Python values."""
    if isinstance(data, dict):
        return {
            str(key): make_json_safe(value)
            for key, value in data.items()
        }

    if isinstance(data, (list, tuple)):
        return [make_json_safe(value) for value in data]

    if isinstance(data, set):
        return [make_json_safe(value) for value in data]

    if data is None:
        return None

    try:
        if pd.isna(data):
            return None
    except (TypeError, ValueError):
        pass

    # NumPy scalar types: int64, float64, bool_, etc.
    if hasattr(data, "item"):
        try:
            return data.item()
        except (ValueError, TypeError):
            pass

    # Pandas Timestamp / Python datetime/date
    if hasattr(data, "isoformat") and not isinstance(data, str):
        try:
            return data.isoformat()
        except (ValueError, TypeError):
            pass

    return data

def add_pending_update(primary_id, changes):
    primary_id = str(primary_id)
    current = st.session_state.pending_updates.get(primary_id, {}).copy()

    for column, value in changes.items():
        if column not in {"updated_at"}:
            current[column] = clean_value(value)

    if current:
        st.session_state.pending_updates[primary_id] = current
        st.session_state.pending_deletes.discard(primary_id)


def capture_editor_changes(source_df: pd.DataFrame, edited_df: pd.DataFrame, primary_key: str):
    """Compare editor output with the source dataframe.

    This avoids relying on Streamlit's internal edited_rows/added_rows/deleted_rows
    structures, which are implementation details and can change between releases.
    """
    if source_df is None or edited_df is None:
        return

    source = source_df.reset_index(drop=True).copy()
    edited = edited_df.reset_index(drop=True).copy()

    if primary_key not in source.columns or primary_key not in edited.columns:
        return

    # Existing records: compare by primary key.
    source_ids = {
        str(v): i
        for i, v in enumerate(source[primary_key])
        if pd.notna(v) and str(v).strip()
    }
    edited_ids = {
        str(v)
        for v in edited[primary_key]
        if pd.notna(v) and str(v).strip()
    }

    # Deleted existing records.
    for patient_id in source_ids:
        if patient_id not in edited_ids:
            st.session_state.pending_deletes.add(patient_id)
            st.session_state.pending_updates.pop(patient_id, None)
        else:
            st.session_state.pending_deletes.discard(patient_id)

    # Updated existing records.
    comparable_columns = [
        c for c in source.columns
        if c in edited.columns and c not in {primary_key, "updated_at"}
    ]

    for patient_id, source_index in source_ids.items():
        if patient_id not in edited_ids:
            continue

        edited_index = next(
            (
                i for i, value in enumerate(edited[primary_key])
                if pd.notna(value) and str(value).strip() == patient_id
            ),
            None,
        )
        if edited_index is None:
            continue

        changes = {}
        for column in comparable_columns:
            old_value = source.at[source_index, column]
            new_value = edited.at[edited_index, column]
            if not values_equal(old_value, new_value):
                changes[column] = new_value

        current_pending = st.session_state.pending_updates.get(patient_id, {}).copy()

        for column in comparable_columns:
            old_value = source.at[source_index, column]
            new_value = edited.at[edited_index, column]
            if values_equal(old_value, new_value):
                current_pending.pop(column, None)
            else:
                current_pending[column] = clean_value(new_value)

        if current_pending:
            st.session_state.pending_updates[patient_id] = current_pending
        else:
            st.session_state.pending_updates.pop(patient_id, None)

    # New rows have no primary key yet. Empty rows are ignored.
    for _, row in edited.iterrows():
        patient_id = row.get(primary_key)
        if pd.notna(patient_id) and str(patient_id).strip():
            continue

        clean_row = {
            column: clean_value(value)
            for column, value in row.items()
            if column != "updated_at"
        }

        if any(value not in (None, "") for value in clean_row.values()):
            if clean_row not in st.session_state.pending_inserts:
                st.session_state.pending_inserts.append(clean_row)


def pending_changes_count():
    return (
        len(st.session_state.pending_updates)
        + len(st.session_state.pending_inserts)
        + len(st.session_state.pending_deletes)
    )


# @st.cache_data(ttl=600, show_spinner=False)
# def fetch_table_data(table_name: str) -> pd.DataFrame:
#     if functionGetDataFromTable:
#         try:
#             df = functionGetDataFromTable(
#                 table_name,
#                 SUPABASE_URL,
#                 SUPABASE_KEY,
#                 page_size=MAX_ROWS,
#             )
#             if isinstance(df, pd.DataFrame):
#                 return df
#         except Exception:
#             pass

#     response = (
#         base_supabase.table(table_name)
#         .select("*")
#         .limit(MAX_ROWS)
#         .execute()
#     )
#     return pd.DataFrame(response.data or [])

@st.cache_data(ttl=600, show_spinner=False)
def fetch_table_data(table_name: str) -> pd.DataFrame:

    rows = []
    start = 0
    while True:
        end = start + BATCH_SIZE - 1
        response = (
            base_supabase
            .table(table_name)
            .select("*")
            .range(start, end)
            .execute()
        )
        batch = response.data or []
        if not batch:
            break
        rows.extend(batch)
        # Last batch reached.
        if len(batch) < BATCH_SIZE:
            break
        start += BATCH_SIZE

    return pd.DataFrame(rows)

def fetch_all_from_query(query_builder, batch_size=BATCH_SIZE):

    rows = []
    start = 0
    while True:
        end = start + batch_size - 1
        response = (
            query_builder(start, end)
            .execute()
        )
        batch = response.data or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < batch_size:
            break
        start += batch_size

    return pd.DataFrame(rows)


# @st.cache_data(ttl=60, show_spinner=False)
# def get_unique_values(table_name: str, column: str):
#     try:
#         result = (
#             base_supabase.table(table_name)
#             .select(column)
#             .limit(10000)
#             .execute()
#         )
#         values = {
#             str(row[column]).strip()
#             for row in (result.data or [])
#             if row.get(column) is not None and str(row[column]).strip()
#         }
#         return sorted(values, key=str.lower)
#     except Exception:
#         return []

@st.cache_data(ttl=600, show_spinner=False)
def get_unique_values(
    table_name: str,
    column: str,
):
    """
    Get ALL unique values from a column using pagination.
    """

    values = set()

    start = 0

    while True:

        end = (
            start
            + BATCH_SIZE
            - 1
        )

        response = (
            base_supabase
            .table(table_name)
            .select(column)
            .range(start, end)
            .execute()
        )

        batch = response.data or []

        if not batch:
            break

        for row in batch:

            value = row.get(column)

            if (
                value is not None
                and str(value).strip()
            ):

                values.add(
                    str(value).strip()
                )

        if len(batch) < BATCH_SIZE:
            break

        start += BATCH_SIZE

    return sorted(
        values,
        key=str.lower,
    )

def clear_pending_changes():
    st.session_state.pending_updates = {}
    st.session_state.pending_inserts = []
    st.session_state.pending_deletes = set()
    st.session_state.editor_source_df = None
    st.session_state.editor_df = None
    st.session_state.grid_version += 1


# ============================================================
# Explorer
# ============================================================

tabs = st.tabs(["💾 Explorer & Search", "✏️ Data Editor & Sync"])

with tabs[0]:
    st.title("💾 Database Explorer & Query Engine")
    st.caption("Inspect, search, filter, and export live records from Supabase.")

    table_choice = st.selectbox(
        "Select Database Table",
        [TABLE_NAME, TARGET_TABLE],
        index=0,
    )

    with st.spinner(f"Fetching raw data for '{table_choice}'…"):
        try:
            df_raw = fetch_table_data(table_choice)
        except Exception as exc:
            st.error(f"Error fetching data: {exc}")
            df_raw = pd.DataFrame()

    if df_raw.empty:
        st.warning(f"No records returned for table '{table_choice}'.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Records Loaded", f"{len(df_raw):,}")
        m2.metric("Total Columns", f"{len(df_raw.columns):,}")
        m3.metric(
            "Memory Usage",
            f"{df_raw.memory_usage(deep=True).sum() / (1024 * 1024):.2f} MB",
        )

        st.divider()

        ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 2, 1])
        with ctrl_col1:
            search_query = st.text_input(
                "🔍 Global Keyword Search",
                placeholder="Type keyword to filter rows…",
            )
        with ctrl_col2:
            selected_cols = st.multiselect(
                "Select Columns to Display",
                options=list(df_raw.columns),
                default=(
                    list(df_raw.columns)[:15]
                    if len(df_raw.columns) > 15
                    else list(df_raw.columns)
                ),
            )
        with ctrl_col3:
            max_display = st.number_input(
                "Max Rows to Render",
                min_value=10,
                max_value=10000,
                value=500,
                step=50,
            )

        df_display = df_raw.copy()

        if search_query:
            mask = df_display.astype(str).apply(
                lambda row: row.str.contains(
                    search_query,
                    case=False,
                    na=False,
                    regex=False,
                ).any(),
                axis=1,
            )
            df_display = df_display[mask]

        if selected_cols:
            df_display = df_display[selected_cols]

        st.subheader(f"Data Preview ({len(df_display):,} records found)")
        st.dataframe(
            df_display.head(int(max_display)),
            use_container_width=True,
            hide_index=False,
        )

        st.divider()
        st.subheader("📥 Export Data")

        csv_data = df_display.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📄 Download CSV (Filtered)",
            data=csv_data,
            file_name=f"{table_choice}_export.csv",
            mime="text/csv",
            use_container_width=True,
        )

        with st.popover("📊 View Data Types & Null Counts"):
            info_df = pd.DataFrame(
                {
                    "Column": df_raw.columns,
                    "Data Type": df_raw.dtypes.astype(str),
                    "Non-Null Count": df_raw.notnull().sum().values,
                    "Null Count": df_raw.isnull().sum().values,
                }
            )
            st.dataframe(info_df, use_container_width=True, hide_index=True)


# ============================================================
# Data editor
# ============================================================

with tabs[1]:
    st.title("🩺 YgnTBPro Data Editor")
    st.caption(
        "Edit records locally, review pending changes, then synchronize them to Supabase."
    )

    # Discover actual column names once so filters work with quoted/mixed-case
    # PostgreSQL columns as well as lowercase schemas.
    with st.spinner("Reading database structure…"):
        try:
            schema_sample = (
                base_supabase.table(TABLE_NAME)
                .select("*")
                .limit(1)
                .execute()
            )
            schema_df = pd.DataFrame(schema_sample.data or [])
        except Exception as exc:
            st.error(f"Unable to inspect the database table: {exc}")
            schema_df = pd.DataFrame()

    if schema_df.empty:
        st.warning("The `ygntbpro` table is empty or its schema could not be read.")
        st.stop()

    columns = list(schema_df.columns)

    primary_key = resolve_column(columns, "PatientID", "patientid", "patient_id")
    date_column = resolve_column(columns, "Date", "date")
    filter_columns = {
        "Visit No": resolve_column(columns, "visitno", "Visit_no"),
        "SR No": resolve_column(columns, "srno", "Sr_No"),
        "Patient ID": primary_key,
        "Team": resolve_column(columns, "team", "Team"),
        "TSP": resolve_column(columns, "tsp", "Tsp", "TSP"),
        "Approach": resolve_column(columns, "approach", "Approach"),
        "Case": resolve_column(columns, "case", "Case"),
    }

    if not primary_key:
        st.error(
            "No Patient ID column was found. Expected one of: "
            "`PatientID`, `patientid`, or `patient_id`."
        )
        st.stop()

    st.subheader("🔎 Filter Records")
    fv = st.session_state.filter_version
    col1, col2, col3 = st.columns(3)

    filter_values = {}

    with col1:
        for label in ["Visit No", "SR No", "Patient ID"]:
            actual = filter_columns[label]
            if actual:
                filter_values[actual] = st.multiselect(
                    label,
                    get_unique_values(TABLE_NAME, actual),
                    key=f"filter_{label}_{fv}",
                )

    with col2:
        for label in ["Team", "TSP", "Approach"]:
            actual = filter_columns[label]
            if actual:
                filter_values[actual] = st.multiselect(
                    label,
                    get_unique_values(TABLE_NAME, actual),
                    key=f"filter_{label}_{fv}",
                )

    with col3:
        for label in ["Case"]:
            actual = filter_columns[label]
            if actual:
                filter_values[actual] = st.multiselect(
                    label,
                    get_unique_values(TABLE_NAME, actual),
                    key=f"filter_{label}_{fv}",
                )

        date_from = st.date_input("Date From", value=None, key=f"date_from_{fv}")
        date_to = st.date_input("Date To", value=None, key=f"date_to_{fv}")

    if date_from and date_to and date_from > date_to:
        st.error("Date From cannot be later than Date To.")
        st.stop()

    if st.button("🔄 Reset Filters", use_container_width=True):
        st.session_state.filter_version += 1
        st.session_state.grid_version += 1
        st.session_state.editor_source_df = None
        st.session_state.editor_df = None
        st.rerun()

    # Query using the authenticated client so RLS policies are respected.
    # client = get_user_client()
    # query = client.table(TABLE_NAME).select("*")

    # for column, values in filter_values.items():
    #     if values:
    #         query = query.in_(column, values)

    # if date_column and date_from:
    #     query = query.gte(date_column, date_from.isoformat())

    # if date_column and date_to:
    #     next_day = date_to + timedelta(days=1)
    #     query = query.lt(date_column, next_day.isoformat())

    # try:
    #     result = (
    #         query
    #         .order(primary_key, desc=True)
    #         .limit(MAX_ROWS)
    #         .execute()
    #     )
    #     df = pd.DataFrame(result.data or [])
    # except Exception as exc:
    #     st.error(f"Error loading data: {exc}")
    #     df = pd.DataFrame()

# ============================================================
# LOAD ALL FILTERED DATA
# ============================================================

    client = get_user_client()
    def build_editor_query(start, end):

        query = (
            client
            .table(TABLE_NAME)
            .select("*")
        )

        for column, values in filter_values.items():

            if values:

                query = query.in_(
                    column,
                    values,
                )

        if date_column and date_from:

            query = query.gte(
                date_column,
                date_from.isoformat(),
            )

        if date_column and date_to:

            next_day = (
                date_to
                + timedelta(days=1)
            )

            query = query.lt(
                date_column,
                next_day.isoformat(),
            )

        query = query.order(
            primary_key,
            desc=True,
        )

        query = query.range(
            start,
            end,
        )

        return query

    try:

        df = fetch_all_from_query(
            build_editor_query,
            batch_size=BATCH_SIZE,
        )

    except Exception as exc:

        st.error(
            f"Error loading data: {exc}"
        )

        df = pd.DataFrame()


        if not df.empty:
            st.caption(
                        f"Showing all {len(df):,} matching record(s). "
                        f"Data was loaded in {BATCH_SIZE:,}-row batches."
                    )

            disabled_cols = [
                column for column in [primary_key, "updated_at"]
                if column in df.columns
            ]

            editor_key = f"consultation_grid_{st.session_state.grid_version}"

            # Keep a stable source snapshot for comparison.
            st.session_state.editor_source_df = df.copy()

            edited_df = st.data_editor(
                df,
                key=editor_key,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic" if can_add else "fixed",
                disabled=disabled_cols,
            )

            st.session_state.editor_df = edited_df.copy()

            # Capture only differences from the current source snapshot.
            if can_edit:
                capture_editor_changes(
                    st.session_state.editor_source_df,
                    edited_df,
                    primary_key,
                )
            elif not edited_df.equals(df):
                st.warning("Your role is read-only; edits will not be synchronized.")

        else:
            st.info("No records found for the active filters.")

    # ========================================================
    # Pending changes
    # ========================================================

    pending_rows = []

    for pid, changes in st.session_state.pending_updates.items():
        row = {"Action": "UPDATE", primary_key: pid}
        row.update(changes)
        pending_rows.append(row)

    for row in st.session_state.pending_inserts:
        display_row = {"Action": "INSERT"}
        display_row.update(row)
        pending_rows.append(display_row)

    for pid in sorted(st.session_state.pending_deletes):
        pending_rows.append({"Action": "DELETE", primary_key: pid})

    if pending_rows:
        st.subheader(f"📝 Pending Changes ({len(pending_rows)})")
        st.dataframe(
            pd.DataFrame(pending_rows),
            use_container_width=True,
            hide_index=True,
        )

        col_sync, col_discard = st.columns(2)

        with col_sync:
            sync_clicked = st.button(
                "💾 Sync Changes",
                type="primary",
                disabled=not can_edit,
                use_container_width=True,
            )

        with col_discard:
            discard_clicked = st.button(
                "↩️ Discard Changes",
                use_container_width=True,
            )

        if discard_clicked:
            clear_pending_changes()
            st.rerun()

        if sync_clicked:
            sync_client = get_user_client()
            success_count = 0
            errors = []

            # -----------------------------
            # Updates
            # -----------------------------
            successful_updates = []
            for pid, update_data in list(st.session_state.pending_updates.items()):
                if not update_data:
                    continue

                try:
                    safe_update_data = make_json_safe(update_data)

                    response = (
                        sync_client
                        .table(TABLE_NAME)
                        .update(safe_update_data)
                        .eq(primary_key, pid)
                        .execute()
                    )
                    if response.data:
                        success_count += 1
                        successful_updates.append(pid)
                    else:
                        errors.append(f"UPDATE affected no row: {pid}")
                except Exception as exc:
                    errors.append(f"UPDATE {pid}: {exc}")

            # -----------------------------
            # Inserts
            # -----------------------------
            successful_inserts = []
            if st.session_state.pending_inserts:
                if not can_add:
                    errors.append("INSERT requires admin permissions.")
                else:
                    for index, row_data in enumerate(
                        list(st.session_state.pending_inserts)
                    ):
                        try:
                            safe_row_data = make_json_safe(row_data)
                            response = (
                                sync_client
                                .table(TABLE_NAME)
                                .insert(safe_row_data)
                                .execute()
                            )
                            if response.data:
                                success_count += 1
                                successful_inserts.append(index)
                            else:
                                errors.append(f"INSERT returned no row: {index}")
                        except Exception as exc:
                            errors.append(f"INSERT row {index}: {exc}")

            # -----------------------------
            # Deletes
            # -----------------------------
            successful_deletes = []
            if st.session_state.pending_deletes:
                if not can_delete:
                    errors.append("DELETE requires admin permissions.")
                else:
                    for pid in list(st.session_state.pending_deletes):
                        try:
                            response = (
                                sync_client
                                .table(TABLE_NAME)
                                .delete()
                                .eq(primary_key, pid)
                                .execute()
                            )
                            if response.data:
                                success_count += 1
                                successful_deletes.append(pid)
                            else:
                                errors.append(f"DELETE affected no row: {pid}")
                        except Exception as exc:
                            errors.append(f"DELETE {pid}: {exc}")

            # Remove only operations confirmed successful.
            for pid in successful_updates:
                st.session_state.pending_updates.pop(pid, None)

            for index in reversed(successful_inserts):
                del st.session_state.pending_inserts[index]

            for pid in successful_deletes:
                st.session_state.pending_deletes.discard(pid)

            if success_count:
                st.success(f"✅ {success_count} change(s) synchronized.")

            if errors:
                st.error(f"❌ {len(errors)} error(s) occurred.")
                for error in errors:
                    st.warning(error)

            if success_count:
                fetch_table_data.clear()
                get_unique_values.clear()
                st.session_state.grid_version += 1
                st.session_state.editor_source_df = None
                st.session_state.editor_df = None

            if errors:
                st.warning(
                    "Only operations confirmed by Supabase were removed from "
                    "the pending list. Review the remaining changes before retrying."
                )

            if success_count:
                st.rerun()

    else:
        st.caption("No pending changes.")
