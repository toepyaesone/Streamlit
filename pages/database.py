# database.py

import uuid
from datetime import date, datetime, time
from decimal import Decimal

import numpy as np
import pandas as pd
import streamlit as st
from supabase import create_client
from st_aggrid import (
    AgGrid,
    DataReturnMode,
    GridOptionsBuilder,
    GridUpdateMode,
    JsCode,
)


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="YgnTBPro Database",
    page_icon="🗄️",
    layout="wide",
)


# ============================================================
# CONFIGURATION
# ============================================================

TABLE_NAME = "ygntbpro"
USER_ROLE_TABLE = "user_roles"

PRIMARY_KEY = "PatientID"

BATCH_SIZE = 1000

# Candidate date columns.
# The first existing column will be used for the date filter.
DATE_COLUMNS = [
    "DiagnosisDate",
    "DateDiagnosis",
    "Diagnosis_Date",
    "DateofDiagnosis",
    "DateOfDiagnosis",
    "Diagnosisdate",
]


# ============================================================
# SUPABASE CONNECTION
# ============================================================

def get_supabase_credentials():
    """
    Read Supabase credentials from Streamlit secrets.

    Recommended:
        [supabase]
        url = "https://xxxxx.supabase.co"
        key = "your-publishable-key"
    """

    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]

        return url, key

    except Exception:
        # Optional compatibility with projects using flat secrets.
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]

            return url, key

        except Exception as exc:
            st.error(
                "Supabase credentials were not found in Streamlit secrets."
            )
            st.exception(exc)
            st.stop()


SUPABASE_URL, SUPABASE_KEY = get_supabase_credentials()


@st.cache_resource
def get_base_client():
    """
    Anonymous/base Supabase client.

    Authentication is performed using this client.
    Database operations after login use get_user_client().
    """

    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )


base_supabase = get_base_client()


# ============================================================
# SESSION DEFAULTS
# ============================================================

DEFAULTS = {
    # Authentication
    "session": None,
    "authenticated": False,
    "user_email": None,
    "user_id": None,

    # Role
    "user_role": "viewer",
    "role": "viewer",

    # Database
    "db_df": None,

    # Pending changes
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Grid
    "selected_grid_rows": [],
    "grid_version": 0,

    # Filters
    "filter_version": 0,
}


def initialize_session_state():
    for key, value in DEFAULTS.items():

        if key not in st.session_state:

            # Copy mutable objects.
            if isinstance(value, dict):
                st.session_state[key] = value.copy()

            elif isinstance(value, list):
                st.session_state[key] = value.copy()

            elif isinstance(value, set):
                st.session_state[key] = value.copy()

            else:
                st.session_state[key] = value


initialize_session_state()


# ============================================================
# JSON / SUPABASE SAFE VALUE CONVERSION
# ============================================================

def json_safe(value):
    """
    Convert Pandas / NumPy / Decimal / datetime values
    into values that Supabase/PostgREST can serialize.
    """

    if value is None:
        return None

    if pd.isna(value):
        return None

    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, np.ndarray):
        return value.tolist()

    if isinstance(value, (list, tuple)):
        return [json_safe(x) for x in value]

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    return value


def clean_record(record):
    """
    Convert a Pandas record to a JSON-safe dictionary.
    """

    cleaned = {}

    for key, value in record.items():

        # Internal AG Grid field.
        if key == "_temp_id":
            continue

        cleaned[key] = json_safe(value)

    return cleaned


# ============================================================
# AUTHENTICATED SUPABASE CLIENT
# ============================================================

def get_user_client():
    """
    Return a Supabase client authenticated with the
    currently logged-in user's access token.

    This allows Supabase RLS to operate as the logged-in user.
    """

    session = st.session_state.get("session")

    if not session:
        return base_supabase

    user_client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    try:
        user_client.auth.set_session(
            session.access_token,
            session.refresh_token,
        )

    except Exception:

        # Compatibility fallback for some supabase-py versions.
        try:
            user_client.postgrest.auth(
                session.access_token
            )

        except Exception:
            raise

    return user_client


# ============================================================
# AUTHENTICATION
# ============================================================

def login_user(email: str, password: str):

    try:

        email = email.strip()

        if not email or not password:
            return False, "Please enter both email and password."

        response = base_supabase.auth.sign_in_with_password(
            {
                "email": email,
                "password": password,
            }
        )

        if not response.session:
            return (
                False,
                "Login failed: no authenticated session was returned.",
            )

        # ----------------------------------------------------
        # Save authentication session
        # ----------------------------------------------------

        st.session_state.session = response.session

        st.session_state.user_email = email.lower()

        st.session_state.user_id = (
            response.session.user.id
        )

        st.session_state.authenticated = True

        # ----------------------------------------------------
        # Authenticated client
        # ----------------------------------------------------

        user_client = get_user_client()

        # ----------------------------------------------------
        # Get role from user_roles
        # using authenticated user's UUID
        # ----------------------------------------------------

        role_result = (
            user_client
            .table(USER_ROLE_TABLE)
            .select("role")
            .eq(
                "user_id",
                response.session.user.id,
            )
            .limit(1)
            .execute()
        )

        if role_result.data:

            user_role = (
                role_result.data[0].get("role")
                or "viewer"
            )

            user_role = str(
                user_role
            ).strip().lower()

        else:

            user_role = "viewer"

        # ----------------------------------------------------
        # Validate role
        # ----------------------------------------------------

        if user_role not in {
            "viewer",
            "editor",
            "admin",
        }:

            user_role = "viewer"

        # Store both names for compatibility.
        st.session_state.user_role = user_role
        st.session_state.role = user_role

        return True, "Login successful."

    except Exception as exc:

        # Clear partially created authentication state.
        st.session_state.session = None
        st.session_state.authenticated = False
        st.session_state.user_email = None
        st.session_state.user_id = None
        st.session_state.user_role = "viewer"
        st.session_state.role = "viewer"

        return False, str(exc)


def logout_user():

    try:
        base_supabase.auth.sign_out()

    except Exception:
        pass

    for key, value in DEFAULTS.items():

        if isinstance(value, dict):
            st.session_state[key] = value.copy()

        elif isinstance(value, list):
            st.session_state[key] = value.copy()

        elif isinstance(value, set):
            st.session_state[key] = value.copy()

        else:
            st.session_state[key] = value

    st.rerun()


# ============================================================
# LOGIN SCREEN
# ============================================================

if not st.session_state.session:

    st.title("🔑 YgnTBPro Database Login")

    with st.form("login_form"):

        email = st.text_input(
            "Email"
        )

        password = st.text_input(
            "Password",
            type="password",
        )

        login_clicked = st.form_submit_button(
            "Login",
            use_container_width=True,
            type="primary",
        )

    if login_clicked:

        success, message = login_user(
            email,
            password,
        )

        if success:

            st.success(message)

            st.rerun()

        else:

            st.error(message)

    st.stop()


# ============================================================
# CURRENT USER / PERMISSIONS
# ============================================================

user_email = (
    st.session_state.get("user_email")
    or ""
)

user_id = (
    st.session_state.get("user_id")
)

user_role = (
    st.session_state.get("user_role")
    or st.session_state.get("role")
    or "viewer"
)

user_role = str(
    user_role
).strip().lower()


if user_role not in {
    "viewer",
    "editor",
    "admin",
}:

    user_role = "viewer"


# Keep both names synchronized.
st.session_state.user_role = user_role
st.session_state.role = user_role


# Permissions
can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"

can_delete = user_role == "admin"


# ============================================================
# AUTHENTICATED DATABASE CLIENT
# ============================================================

client = get_user_client()


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("YgnTBPro System")

if user_email:

    st.sidebar.write(
        f"**User:** `{user_email}`"
    )

st.sidebar.write(
    f"**User Role:** `{user_role}`"
)

if st.sidebar.button(
    "Logout",
    use_container_width=True,
):

    logout_user()


# ============================================================
# DATABASE FUNCTIONS
# ============================================================

def load_all_rows():

    rows = []

    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        response = (
            client
            .table(TABLE_NAME)
            .select("*")
            .order(
                PRIMARY_KEY,
                desc=False,
            )
            .range(start, end)
            .execute()
        )

        batch = response.data or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < BATCH_SIZE:
            break

        start += BATCH_SIZE

    if not rows:

        return pd.DataFrame()

    df = pd.DataFrame(rows)

    return df


@st.cache_data(
    ttl=0,
    show_spinner=False,
)
def load_database_snapshot(access_token):

    """
    Cached only for the current access token.
    """

    temp_client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    session = st.session_state.get("session")

    if session:

        try:

            temp_client.auth.set_session(
                session.access_token,
                session.refresh_token,
            )

        except Exception:

            try:
                temp_client.postgrest.auth(
                    access_token
                )

            except Exception:
                pass

    rows = []

    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        response = (
            temp_client
            .table(TABLE_NAME)
            .select("*")
            .order(
                PRIMARY_KEY,
                desc=False,
            )
            .range(start, end)
            .execute()
        )

        batch = response.data or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < BATCH_SIZE:
            break

        start += BATCH_SIZE

    return pd.DataFrame(rows)


def refresh_database():

    session = st.session_state.get("session")

    if not session:
        return pd.DataFrame()

    try:

        df = load_database_snapshot(
            session.access_token
        )

        st.session_state.db_df = df

        return df

    except Exception as exc:

        st.error(
            f"Unable to load database: {exc}"
        )

        return pd.DataFrame()


# ============================================================
# INITIAL DATABASE LOAD
# ============================================================

if st.session_state.db_df is None:

    with st.spinner(
        "Loading YgnTBPro data..."
    ):

        refresh_database()


db_df = st.session_state.db_df

if db_df is None:
    db_df = pd.DataFrame()


# ============================================================
# PENDING CHANGE HELPERS
# ============================================================

def pending_changes_count():

    return (
        len(st.session_state.pending_updates)
        + len(st.session_state.pending_inserts)
        + len(st.session_state.pending_deletes)
    )


def clear_pending_changes():

    st.session_state.pending_updates = {}
    st.session_state.pending_inserts = []
    st.session_state.pending_deletes = set()

    st.session_state.selected_grid_rows = []

    st.session_state.grid_version += 1


def get_existing_record(primary_value):

    if db_df is None or db_df.empty:
        return None

    if PRIMARY_KEY not in db_df.columns:
        return None

    matches = db_df[
        db_df[PRIMARY_KEY].astype(str)
        == str(primary_value)
    ]

    if matches.empty:
        return None

    return matches.iloc[0].to_dict()


def is_pending_delete(primary_value):

    return str(primary_value) in {
        str(x)
        for x in st.session_state.pending_deletes
    }


# ============================================================
# APPLY PENDING CHANGES TO DATABASE SNAPSHOT
# ============================================================

def build_display_dataframe():

    if db_df is None:
        df = pd.DataFrame()

    else:
        df = db_df.copy()

    # --------------------------------------------------------
    # Existing-row updates
    # --------------------------------------------------------

    if not df.empty and PRIMARY_KEY in df.columns:

        for primary_value, changes in (
            st.session_state.pending_updates.items()
        ):

            mask = (
                df[PRIMARY_KEY].astype(str)
                == str(primary_value)
            )

            for column, value in changes.items():

                if column in df.columns:

                    df.loc[mask, column] = value

    # --------------------------------------------------------
    # Existing-row deletes
    # --------------------------------------------------------

    if (
        not df.empty
        and PRIMARY_KEY in df.columns
        and st.session_state.pending_deletes
    ):

        deleted_ids = {
            str(x)
            for x in st.session_state.pending_deletes
        }

        df = df[
            ~df[PRIMARY_KEY]
            .astype(str)
            .isin(deleted_ids)
        ]

    # --------------------------------------------------------
    # Pending new rows
    # --------------------------------------------------------

    if st.session_state.pending_inserts:

        insert_df = pd.DataFrame(
            st.session_state.pending_inserts
        )

        if not insert_df.empty:

            # Ensure same columns.
            for column in df.columns:

                if column not in insert_df.columns:
                    insert_df[column] = None

            for column in insert_df.columns:

                if column not in df.columns:
                    df[column] = None

            insert_df = insert_df[
                df.columns.tolist()
            ]

            df = pd.concat(
                [
                    df,
                    insert_df,
                ],
                ignore_index=True,
            )

    # --------------------------------------------------------
    # Ensure internal temporary ID exists
    # --------------------------------------------------------

    if "_temp_id" not in df.columns:

        df["_temp_id"] = [
            str(uuid.uuid4())
            for _ in range(len(df))
        ]

    return df.reset_index(drop=True)


# ============================================================
# DATE COLUMN DETECTION
# ============================================================

def find_date_column(df):

    if df is None or df.empty:
        return None

    for column in DATE_COLUMNS:

        if column in df.columns:
            return column

    return None


date_column = find_date_column(db_df)


# ============================================================
# SIDEBAR FILTERS
# ============================================================

st.sidebar.divider()

st.sidebar.subheader("Filters")

filter_date_from = None
filter_date_to = None

if date_column:

    st.sidebar.caption(
        f"Date column: {date_column}"
    )

    filter_date_from = st.sidebar.date_input(
        "Date From",
        value=None,
        key="filter_date_from",
    )

    filter_date_to = st.sidebar.date_input(
        "Date To",
        value=None,
        key="filter_date_to",
    )


if st.sidebar.button(
    "Reset Date Filter",
    use_container_width=True,
):

    st.session_state.filter_date_from = None
    st.session_state.filter_date_to = None

    st.session_state.filter_version += 1

    st.rerun()


# ============================================================
# BUILD DISPLAY DATA
# ============================================================

display_df = build_display_dataframe()


# ============================================================
# APPLY DATE FILTER
# ============================================================

if (
    date_column
    and not display_df.empty
):

    if (
        filter_date_from
        and filter_date_to
    ):

        date_values = pd.to_datetime(
            display_df[date_column],
            errors="coerce",
        )

        start_date = pd.Timestamp(
            filter_date_from
        )

        end_date = (
            pd.Timestamp(filter_date_to)
            + pd.Timedelta(days=1)
        )

        mask = (
            date_values >= start_date
        ) & (
            date_values < end_date
        )

        display_df = display_df[mask]

    elif filter_date_from:

        date_values = pd.to_datetime(
            display_df[date_column],
            errors="coerce",
        )

        start_date = pd.Timestamp(
            filter_date_from
        )

        display_df = display_df[
            date_values >= start_date
        ]

    elif filter_date_to:

        date_values = pd.to_datetime(
            display_df[date_column],
            errors="coerce",
        )

        end_date = (
            pd.Timestamp(filter_date_to)
            + pd.Timedelta(days=1)
        )

        display_df = display_df[
            date_values < end_date
        ]


# ============================================================
# PAGE HEADER
# ============================================================

st.title("🗄️ YgnTBPro Database")

header_col1, header_col2, header_col3 = st.columns(
    [2, 1, 1]
)

with header_col1:

    st.caption(
        f"Logged in as: {user_email}"
    )

with header_col2:

    st.metric(
        "Role",
        user_role.upper(),
    )

with header_col3:

    st.metric(
        "Pending Changes",
        pending_changes_count(),
    )


# ============================================================
# DATABASE SUMMARY
# ============================================================

total_rows = (
    len(db_df)
    if db_df is not None
    else 0
)

display_rows = len(display_df)

summary_col1, summary_col2, summary_col3 = st.columns(3)

with summary_col1:

    st.metric(
        "Database Rows",
        f"{total_rows:,}",
    )

with summary_col2:

    st.metric(
        "Displayed Rows",
        f"{display_rows:,}",
    )

with summary_col3:

    st.metric(
        "Pending Changes",
        pending_changes_count(),
    )


# ============================================================
# ACTION BUTTONS
# ============================================================

action_col1, action_col2, action_col3, action_col4 = st.columns(
    4
)


# ------------------------------------------------------------
# Add Row
# ------------------------------------------------------------

with action_col1:

    if can_add:

        if st.button(
            "➕ Add Row",
            use_container_width=True,
        ):

            # Create an empty record using database columns.
            new_record = {}

            for column in db_df.columns:

                if column != "_temp_id":
                    new_record[column] = None

            new_record["_temp_id"] = str(
                uuid.uuid4()
            )

            st.session_state.pending_inserts.append(
                new_record
            )

            st.session_state.grid_version += 1

            st.rerun()

    else:

        st.button(
            "➕ Add Row",
            disabled=True,
            use_container_width=True,
        )


# ------------------------------------------------------------
# Delete Selected
# ------------------------------------------------------------

with action_col2:

    if can_delete:

        if st.button(
            "🗑️ Delete Selected",
            use_container_width=True,
        ):

            selected_rows = (
                st.session_state.selected_grid_rows
            )

            deleted_count = 0

            for row in selected_rows:

                temp_id = row.get("_temp_id")

                # New unsaved row.
                new_rows = []

                removed_new = False

                for new_row in (
                    st.session_state.pending_inserts
                ):

                    if (
                        temp_id
                        and new_row.get("_temp_id")
                        == temp_id
                    ):

                        removed_new = True
                        continue

                    new_rows.append(new_row)

                if removed_new:

                    st.session_state.pending_inserts = (
                        new_rows
                    )

                    deleted_count += 1

                    continue

                # Existing database row.
                primary_value = row.get(
                    PRIMARY_KEY
                )

                if primary_value is None:
                    continue

                st.session_state.pending_deletes.add(
                    str(primary_value)
                )

                # Remove pending updates for deleted row.
                st.session_state.pending_updates.pop(
                    str(primary_value),
                    None,
                )

                deleted_count += 1

            st.session_state.selected_grid_rows = []

            if deleted_count:

                st.session_state.grid_version += 1

                st.success(
                    f"{deleted_count} row(s) marked for deletion."
                )

                st.rerun()

    else:

        st.button(
            "🗑️ Delete Selected",
            disabled=True,
            use_container_width=True,
        )


# ------------------------------------------------------------
# Sync
# ------------------------------------------------------------

with action_col3:

    if st.button(
        "💾 Sync Changes",
        disabled=(
            pending_changes_count() == 0
        ),
        use_container_width=True,
        type="primary",
    ):

        # Function defined below.
        sync_result = sync_changes()

        if sync_result:

            st.rerun()


# ------------------------------------------------------------
# Discard
# ------------------------------------------------------------

with action_col4:

    if st.button(
        "↩️ Discard Changes",
        disabled=(
            pending_changes_count() == 0
        ),
        use_container_width=True,
    ):

        clear_pending_changes()

        st.success(
            "All pending changes were discarded."
        )

        st.rerun()


# ============================================================
# SYNC FUNCTION
# ============================================================

def sync_changes():

    updates = dict(
        st.session_state.pending_updates
    )

    inserts = list(
        st.session_state.pending_inserts
    )

    deletes = set(
        st.session_state.pending_deletes
    )

    total_operations = (
        len(updates)
        + len(inserts)
        + len(deletes)
    )

    if total_operations == 0:

        st.info(
            "There are no pending changes."
        )

        return False

    success_updates = set()
    success_inserts = []
    success_deletes = set()

    errors = []

    progress = st.progress(
        0,
        text="Synchronizing changes...",
    )

    completed = 0

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    for record in inserts:

        payload = clean_record(record)

        # Do not send internal temporary field.
        payload.pop(
            "_temp_id",
            None,
        )

        # If PatientID is empty, remove it so the
        # database can generate it if configured as identity.
        if (
            PRIMARY_KEY in payload
            and (
                payload[PRIMARY_KEY] is None
                or str(payload[PRIMARY_KEY]).strip() == ""
            )
        ):

            payload.pop(
                PRIMARY_KEY,
                None,
            )

        try:

            client.table(
                TABLE_NAME
            ).insert(
                payload
            ).execute()

            success_inserts.append(record)

        except Exception as exc:

            errors.append(
                f"INSERT failed: {exc}"
            )

        completed += 1

        progress.progress(
            min(
                completed / total_operations,
                1.0,
            )
        )

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for primary_value, changes in updates.items():

        if not changes:
            success_updates.add(
                str(primary_value)
            )
            completed += 1
            continue

        payload = clean_record(changes)

        try:

            (
                client
                .table(TABLE_NAME)
                .update(payload)
                .eq(
                    PRIMARY_KEY,
                    json_safe(primary_value),
                )
                .execute()
            )

            success_updates.add(
                str(primary_value)
            )

        except Exception as exc:

            errors.append(
                f"UPDATE {primary_value} failed: {exc}"
            )

        completed += 1

        progress.progress(
            min(
                completed / total_operations,
                1.0,
            )
        )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    for primary_value in deletes:

        try:

            (
                client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    PRIMARY_KEY,
                    json_safe(primary_value),
                )
                .execute()
            )

            success_deletes.add(
                str(primary_value)
            )

        except Exception as exc:

            errors.append(
                f"DELETE {primary_value} failed: {exc}"
            )

        completed += 1

        progress.progress(
            min(
                completed / total_operations,
                1.0,
            )
        )

    progress.empty()

    # --------------------------------------------------------
    # Remove ONLY successful operations
    # --------------------------------------------------------

    for primary_value in success_updates:

        st.session_state.pending_updates.pop(
            primary_value,
            None,
        )

    for primary_value in success_deletes:

        st.session_state.pending_deletes.discard(
            primary_value
        )

    if success_inserts:

        successful_temp_ids = {
            row.get("_temp_id")
            for row in success_inserts
        }

        st.session_state.pending_inserts = [
            row
            for row in st.session_state.pending_inserts
            if row.get("_temp_id")
            not in successful_temp_ids
        ]

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    if errors:

        st.warning(
            f"Sync completed with {len(errors)} error(s)."
        )

        for error in errors:
            st.error(error)

    else:

        st.success(
            f"Successfully synchronized "
            f"{total_operations} operation(s)."
        )

    # --------------------------------------------------------
    # Reload only after successful operations.
    # --------------------------------------------------------

    if (
        not st.session_state.pending_updates
        and not st.session_state.pending_inserts
        and not st.session_state.pending_deletes
    ):

        try:

            st.cache_data.clear()

            refresh_database()

            st.session_state.selected_grid_rows = []

            st.session_state.grid_version += 1

        except Exception as exc:

            st.warning(
                f"Changes were synchronized, "
                f"but data refresh failed: {exc}"
            )

    return True


# ============================================================
# VIEW / EDIT PERMISSION INFORMATION
# ============================================================

if user_role == "viewer":

    st.info(
        "Viewer mode: data can be viewed but not edited."
    )

elif user_role == "editor":

    st.info(
        "Editor mode: existing records can be edited. "
        "Adding and deleting records is disabled."
    )

elif user_role == "admin":

    st.info(
        "Admin mode: editing, adding, and deleting records are enabled."
    )


# ============================================================
# PREPARE AG GRID DATA
# ============================================================

grid_df = display_df.copy()

# Make sure there is an internal ID.
if "_temp_id" not in grid_df.columns:

    grid_df["_temp_id"] = [
        str(uuid.uuid4())
        for _ in range(len(grid_df))
    ]


# ============================================================
# AG GRID COLUMN CONFIGURATION
# ============================================================

gb = GridOptionsBuilder.from_dataframe(
    grid_df
)

gb.configure_default_column(
    editable=can_edit,
    sortable=True,
    filter=True,
    resizable=True,
    minWidth=100,
)

# ------------------------------------------------------------
# Primary key
# ------------------------------------------------------------

if PRIMARY_KEY in grid_df.columns:

    gb.configure_column(
        PRIMARY_KEY,
        editable=False,
        filter=True,
        sortable=True,
    )


# ------------------------------------------------------------
# Internal temporary ID
# ------------------------------------------------------------

gb.configure_column(
    "_temp_id",
    hide=True,
    editable=False,
)


# ------------------------------------------------------------
# Selection
# ------------------------------------------------------------

gb.configure_selection(
    selection_mode="multiple",
    use_checkbox=True,
)


# ------------------------------------------------------------
# Date columns
# ------------------------------------------------------------

for column in grid_df.columns:

    if column == "_temp_id":
        continue

    if (
        "date" in column.lower()
        or "time" in column.lower()
    ):

        gb.configure_column(
            column,
            filter="agDateColumnFilter",
        )


# ------------------------------------------------------------
# Grid JavaScript
# ------------------------------------------------------------

gb.configure_grid_options(
    stopEditingWhenCellsLoseFocus=True,
    suppressRowClickSelection=False,
)


grid_options = gb.build()


# ============================================================
# RENDER AG GRID
# ============================================================

grid_response = AgGrid(
    grid_df,
    gridOptions=grid_options,

    height=650,

    data_return_mode=DataReturnMode.AS_INPUT,

    update_mode=(
        GridUpdateMode.VALUE_CHANGED
        | GridUpdateMode.SELECTION_CHANGED
    ),

    fit_columns_on_grid_load=False,

    allow_unsafe_jscode=True,

    key=f"ygn_tb_grid_{st.session_state.grid_version}",
)


# ============================================================
# PROCESS GRID CHANGES
# ============================================================

returned_df = grid_response.get(
    "data"
)

selected_rows = grid_response.get(
    "selected_rows",
)


# ============================================================
# STORE SELECTION
# ============================================================

if selected_rows is not None:

    if isinstance(
        selected_rows,
        pd.DataFrame,
    ):

        st.session_state.selected_grid_rows = (
            selected_rows.to_dict(
                orient="records"
            )
        )

    elif isinstance(
        selected_rows,
        list,
    ):

        st.session_state.selected_grid_rows = (
            selected_rows
        )


# ============================================================
# DETECT EDITED ROWS
# ============================================================

if (
    returned_df is not None
    and can_edit
):

    if not isinstance(
        returned_df,
        pd.DataFrame,
    ):

        returned_df = pd.DataFrame(
            returned_df
        )

    if not returned_df.empty:

        # ----------------------------------------------------
        # Process each returned row
        # ----------------------------------------------------

        for _, row in returned_df.iterrows():

            row_dict = row.to_dict()

            temp_id = row_dict.get(
                "_temp_id"
            )

            # ------------------------------------------------
            # NEW ROW
            # ------------------------------------------------

            is_new_row = False

            for new_row in (
                st.session_state.pending_inserts
            ):

                if (
                    temp_id
                    and new_row.get("_temp_id")
                    == temp_id
                ):

                    is_new_row = True

                    break

            if is_new_row:

                for i, new_row in enumerate(
                    st.session_state.pending_inserts
                ):

                    if (
                        new_row.get("_temp_id")
                        == temp_id
                    ):

                        cleaned_row = clean_record(
                            row_dict
                        )

                        cleaned_row["_temp_id"] = (
                            temp_id
                        )

                        st.session_state.pending_inserts[
                            i
                        ] = cleaned_row

                        break

                continue

            # ------------------------------------------------
            # EXISTING ROW
            # ------------------------------------------------

            primary_value = row_dict.get(
                PRIMARY_KEY
            )

            if (
                primary_value is None
                or (
                    isinstance(
                        primary_value,
                        float,
                    )
                    and pd.isna(primary_value)
                )
            ):

                continue

            primary_key_string = str(
                primary_value
            )

            original_record = get_existing_record(
                primary_value
            )

            if original_record is None:
                continue

            changes = {}

            for column, current_value in row_dict.items():

                if column == "_temp_id":
                    continue

                if column not in original_record:
                    continue

                original_value = (
                    original_record[column]
                )

                # Normalize missing values.
                current_missing = (
                    current_value is None
                    or pd.isna(current_value)
                )

                original_missing = (
                    original_value is None
                    or pd.isna(original_value)
                )

                if (
                    current_missing
                    and original_missing
                ):
                    continue

                if (
                    current_missing
                    != original_missing
                ):

                    changes[column] = (
                        None
                        if current_missing
                        else json_safe(current_value)
                    )

                    continue

                # Compare normalized values.
                current_normalized = json_safe(
                    current_value
                )

                original_normalized = json_safe(
                    original_value
                )

                if (
                    str(current_normalized)
                    != str(original_normalized)
                ):

                    changes[column] = (
                        current_normalized
                    )

            # ------------------------------------------------
            # Merge changes with existing pending changes
            # ------------------------------------------------

            if changes:

                existing_changes = (
                    st.session_state
                    .pending_updates
                    .get(
                        primary_key_string,
                        {},
                    )
                )

                existing_changes.update(
                    changes
                )

                st.session_state.pending_updates[
                    primary_key_string
                ] = existing_changes

            else:

                # If the row has been restored to its
                # database value, remove its pending update.
                st.session_state.pending_updates.pop(
                    primary_key_string,
                    None,
                )


# ============================================================
# PENDING CHANGES SUMMARY
# ============================================================

pending_update_count = len(
    st.session_state.pending_updates
)

pending_insert_count = len(
    st.session_state.pending_inserts
)

pending_delete_count = len(
    st.session_state.pending_deletes
)

if (
    pending_update_count
    or pending_insert_count
    or pending_delete_count
):

    st.divider()

    st.subheader(
        "Pending Changes"
    )

    pc1, pc2, pc3 = st.columns(3)

    with pc1:

        st.metric(
            "Updates",
            pending_update_count,
        )

    with pc2:

        st.metric(
            "New Rows",
            pending_insert_count,
        )

    with pc3:

        st.metric(
            "Deletes",
            pending_delete_count,
        )


# ============================================================
# DEBUG / STATUS
# ============================================================

with st.expander(
    "Database Status",
    expanded=False,
):

    st.write(
        {
            "User": user_email,
            "User ID": user_id,
            "Role": user_role,
            "Database table": TABLE_NAME,
            "Primary key": PRIMARY_KEY,
            "Database rows": len(db_df),
            "Displayed rows": len(display_df),
            "Pending updates": pending_update_count,
            "Pending inserts": pending_insert_count,
            "Pending deletes": pending_delete_count,
        }
    )