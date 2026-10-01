# database.py
# ============================================================
# YgnTBPro - Supabase Database Editor
# Streamlit + Supabase + AG Grid
#
# Permissions:
#   viewer -> view only
#   editor -> update existing records
#   admin  -> update + insert + delete
#
# Primary key:
#   PatientID
#
# Supabase table:
#   ygntbpro
# ============================================================

import os
import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from supabase import Client, create_client

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

PRIMARY_KEY_CANDIDATES = [
    "PatientID",
    "patientid",
    "patient_id",
]

DATE_COLUMN_CANDIDATES = [
    "Date",
    "date",
]

UPDATED_AT_CANDIDATES = [
    "updated_at",
    "Updated_at",
    "updatedAt",
]

# Supabase/PostgREST request size.
# This is NOT a maximum number of database rows.
BATCH_SIZE = 1000

# AG Grid page size.
EDITOR_PAGE_SIZE_DEFAULT = 300

EDITOR_PAGE_SIZE_OPTIONS = [
    100,
    300,
    500,
    1000,
]

# Maximum number displayed in Explorer.
EXPLORER_DISPLAY_LIMIT = 1000

# CSV export is still retrieved in batches.
CSV_BATCH_SIZE = 1000


# ============================================================
# KNOWN FILTER TYPES
# ============================================================

# These columns are expected to be integer/numeric fields
# in YgnTBPro.
#
# IMPORTANT:
# Do NOT use ilike() against PostgreSQL integer columns.
# ilike() becomes ~~* and causes:
#
#   operator does not exist: integer ~~* unknown
#
NUMERIC_FILTERS = {
    "team",
    "visit_no",
    "sr_no",
}

# Text filters can safely use ilike().
TEXT_FILTERS = {
    "tsp",
    "approach",
    "case",
    "ward_village",
}


# ============================================================
# SESSION STATE
# ============================================================

DEFAULTS = {
    "session": None,
    "user_role": None,

    # Editor state
    "editor_page": 1,
    "editor_page_size": EDITOR_PAGE_SIZE_DEFAULT,
    "editor_filters": {},
    "editor_loaded": False,

    # Last database snapshot represented by the grid.
    "editor_source_df": None,

    # Persistent changes waiting to be synchronized.
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Versions
    "grid_version": 0,
    "filter_version": 0,

    # New-record form
    "show_add_form": False,

    # Explorer
    "explorer_loaded": False,
}


for key, default_value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default_value


# ============================================================
# SUPABASE CLIENTS
# ============================================================

def get_secret(name: str, default: str = "") -> str:
    """
    Read configuration from Streamlit secrets first,
    then environment variables.
    """

    try:
        value = st.secrets.get(name)
    except Exception:
        value = None

    if value is not None and str(value).strip():
        return str(value).strip()

    value = os.getenv(name)

    if value is not None and str(value).strip():
        return str(value).strip()

    return default


@st.cache_resource
def get_base_client() -> Client:
    """
    Base Supabase client.

    Used mainly for schema/discovery operations.
    Authenticated database operations should use get_user_client().
    """

    url = (
        get_secret("SUPABASE_URL_ygntbpro")
        or get_secret("SUPABASE_URL")
    )

    key = (
        get_secret("SUPABASE_KEY_ygntbpro")
        or get_secret("SUPABASE_KEY")
    )

    if not url or not key:
        raise RuntimeError(
            "Supabase configuration is missing. "
            "Please configure SUPABASE_URL_ygntbpro and "
            "SUPABASE_KEY_ygntbpro in Streamlit secrets."
        )

    return create_client(url, key)


def get_user_client() -> Client:
    """
    Return a Supabase client authenticated with the current
    user's access/refresh token.

    All database operations that must respect RLS should use
    this client.
    """

    session = st.session_state.get("session")

    if not session:
        raise RuntimeError("No authenticated session.")

    base_client = get_base_client()

    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)

    if not access_token:
        raise RuntimeError("Authenticated session has no access token.")

    # Create a fresh client for the current authenticated user.
    user_client = create_client(
        base_client.supabase_url,
        base_client.supabase_key,
    )

    try:
        if refresh_token:
            user_client.auth.set_session(
                access_token,
                refresh_token,
            )
        else:
            # Fallback for environments where only access token
            # is available.
            try:
                user_client.postgrest.auth(access_token)
            except Exception:
                pass

    except Exception:
        try:
            user_client.postgrest.auth(access_token)
        except Exception:
            pass

    return user_client


# ============================================================
# AUTHENTICATION
# ============================================================

def login_user(email: str, password: str):
    """
    Authenticate user and retrieve role by authenticated
    Supabase Auth user UUID.

    IMPORTANT:
    Role is NOT determined by email.
    """

    try:
        base_supabase = get_base_client()

        response = base_supabase.auth.sign_in_with_password(
            {
                "email": email.strip(),
                "password": password,
            }
        )

        if not response.session:
            return (
                False,
                "Login failed: no authenticated session was returned.",
            )

        st.session_state.session = response.session

        user_client = get_user_client()

        role_result = (
            user_client
            .table("user_roles")
            .select("role")
            .eq(
                "user_id",
                response.session.user.id,
            )
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
        base_supabase = get_base_client()
        base_supabase.auth.sign_out()
    except Exception:
        pass

    for key, value in DEFAULTS.items():
        st.session_state[key] = value

    st.rerun()


# ============================================================
# LOGIN SCREEN
# ============================================================

if not st.session_state.session:

    st.title("🗄️ YgnTBPro Database")

    st.subheader("Login")

    with st.form("login_form"):

        email = st.text_input(
            "Email",
            autocomplete="email",
        )

        password = st.text_input(
            "Password",
            type="password",
            autocomplete="current-password",
        )

        submitted = st.form_submit_button(
            "Login",
            use_container_width=True,
        )

    if submitted:

        if not email.strip() or not password:
            st.error("Please enter both email and password.")

        else:

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
# CURRENT USER / ROLE
# ============================================================

user_role = (
    st.session_state.get("user_role")
    or "viewer"
)

user_role = str(user_role).strip().lower()

if user_role not in {
    "viewer",
    "editor",
    "admin",
}:
    user_role = "viewer"

can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"

can_delete = user_role == "admin"


# ============================================================
# GENERAL UTILITIES
# ============================================================

def resolve_column(
    columns,
    *candidates,
):
    """
    Return the actual database column name matching one
    of the supplied candidates, case-insensitively.
    """

    if not columns:
        return None

    lookup = {
        str(column).lower(): column
        for column in columns
    }

    for candidate in candidates:

        if candidate is None:
            continue

        actual = lookup.get(
            str(candidate).lower()
        )

        if actual is not None:
            return actual

    return None


def is_null_like(value) -> bool:
    """
    Safely determine whether a value should be treated as null.
    """

    if value is None:
        return True

    try:
        result = pd.isna(value)

        if isinstance(result, bool):
            return result

        if isinstance(result, np.bool_):
            return bool(result)

    except Exception:
        pass

    return False


def values_equal(left, right) -> bool:
    """
    Robust comparison for Pandas / NumPy / dates / NaN.
    """

    if is_null_like(left) and is_null_like(right):
        return True

    if is_null_like(left) != is_null_like(right):
        return False

    try:
        result = left == right

        if isinstance(result, (bool, np.bool_)):
            return bool(result)

    except Exception:
        pass

    return str(left) == str(right)


def clean_value(value):
    """
    Convert Pandas/NumPy values to normal Python values.
    """

    if value is None:
        return None

    if is_null_like(value):
        return None

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).to_pydatetime()

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value

    if isinstance(value, time):
        return value

    return value


def make_json_safe(value):
    """
    Recursively convert values to JSON/PostgREST-safe
    Python types.

    Fixes errors such as:
        Object of type int64 is not JSON serializable
    """

    if value is None:
        return None

    if isinstance(value, dict):
        return {
            str(key): make_json_safe(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            make_json_safe(item)
            for item in value
        ]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        if np.isnan(value) or np.isinf(value):
            return None

        return float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    return value


def clean_record(record: dict) -> dict:
    """
    Convert an entire record to PostgREST-safe values.
    """

    return make_json_safe(
        {
            key: clean_value(value)
            for key, value in record.items()
        }
    )


# ============================================================
# SCHEMA
# ============================================================

def get_table_columns() -> list:
    """
    Retrieve actual column names from the table.
    """

    client = get_base_client()

    response = (
        client
        .table(TABLE_NAME)
        .select("*")
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return []

    return list(rows[0].keys())


def resolve_database_columns(columns):
    """
    Resolve the important columns in YgnTBPro.
    """

    return {
        "primary_key": resolve_column(
            columns,
            *PRIMARY_KEY_CANDIDATES,
        ),

        "date": resolve_column(
            columns,
            *DATE_COLUMN_CANDIDATES,
        ),

        "updated_at": resolve_column(
            columns,
            *UPDATED_AT_CANDIDATES,
        ),

        "team": resolve_column(
            columns,
            "team",
            "Team",
        ),

        "tsp": resolve_column(
            columns,
            "tsp",
            "Tsp",
            "TSP",
        ),

        "approach": resolve_column(
            columns,
            "approach",
            "Approach",
        ),

        "case": resolve_column(
            columns,
            "case",
            "Case",
        ),

        "visit_no": resolve_column(
            columns,
            "visit_no",
            "Visit_no",
            "visitno",
            "VisitNo",
        ),

        "sr_no": resolve_column(
            columns,
            "sr_no",
            "Sr_No",
            "srno",
            "SrNo",
        ),

        "ward_village": resolve_column(
            columns,
            "ward_village",
            "WardVillage",
            "Ward_Village",
            "wardvillage",
        ),
    }


# ============================================================
# PAGINATION
# ============================================================

def fetch_all_rows(
    table_name: str,
    client: Client,
    select_columns: str = "*",
    order_column: str | None = None,
    descending: bool = False,
):
    """
    Retrieve all rows using PostgREST pagination.

    BATCH_SIZE only controls request size.
    """

    rows = []
    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        query = (
            client
            .table(table_name)
            .select(select_columns)
        )

        if order_column:
            query = query.order(
                order_column,
                desc=descending,
            )

        query = query.range(
            start,
            end,
        )

        response = query.execute()

        batch = response.data or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < BATCH_SIZE:
            break

        start += BATCH_SIZE

    return pd.DataFrame(rows)


def fetch_all_from_query(
    query_builder,
    batch_size: int = BATCH_SIZE,
):
    """
    Execute an already-filtered query repeatedly using
    PostgREST range pagination.

    IMPORTANT:
    query_builder must recreate the same filters and order
    for every range.
    """

    rows = []
    start = 0

    while True:

        end = start + batch_size - 1

        response = query_builder(
            start,
            end,
        ).execute()

        batch = response.data or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < batch_size:
            break

        start += batch_size

    return pd.DataFrame(rows)


# ============================================================
# UNIQUE VALUES
# ============================================================

def get_unique_values(
    client: Client,
    column: str,
):
    """
    Retrieve unique values from a column without assuming
    that the table has fewer than 10,000 rows.
    """

    if not column:
        return []

    rows = []
    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        response = (
            client
            .table(TABLE_NAME)
            .select(column)
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

    values = set()

    for row in rows:

        value = row.get(column)

        if is_null_like(value):
            continue

        values.add(str(value))

    return sorted(values)


# ============================================================
# PENDING CHANGES
# ============================================================

def add_pending_update(
    primary_id,
    changes: dict,
):
    """
    Add/update changes for one existing record.
    """

    if primary_id is None:
        return

    key = str(primary_id)

    if key not in st.session_state.pending_updates:
        st.session_state.pending_updates[key] = {}

    st.session_state.pending_updates[key].update(
        changes
    )

    # A record that is being updated should not simultaneously
    # remain in pending delete state.
    st.session_state.pending_deletes.discard(key)


def add_pending_insert(record: dict):
    """
    Add a new record to the pending insert list.
    """

    clean = clean_record(record)

    st.session_state.pending_inserts.append(
        clean
    )


def capture_grid_changes(
    source_df: pd.DataFrame,
    edited_df: pd.DataFrame,
    primary_key: str,
):
    """
    Compare the original grid page against the edited grid page.

    IMPORTANT:
    This function does NOT infer deletion from missing rows.

    Deletion is handled explicitly by AG Grid selection +
    Delete Selected.

    This prevents changing filters/pages from accidentally
    generating DELETE operations.
    """

    if source_df is None:
        return

    if edited_df is None:
        return

    if primary_key not in source_df.columns:
        return

    if primary_key not in edited_df.columns:
        return

    source = source_df.copy()
    edited = edited_df.copy()

    source = source.reset_index(drop=True)
    edited = edited.reset_index(drop=True)

    source_ids = {
        str(row[primary_key]): row
        for _, row in source.iterrows()
        if not is_null_like(row[primary_key])
    }

    for _, edited_row in edited.iterrows():

        primary_id = edited_row.get(
            primary_key
        )

        # Blank primary key means a new record.
        if is_null_like(primary_id):
            continue

        primary_id_str = str(primary_id)

        if primary_id_str not in source_ids:
            continue

        source_row = source_ids[
            primary_id_str
        ]

        changes = {}

        for column in edited.columns:

            if column == primary_key:
                continue

            old_value = source_row.get(column)
            new_value = edited_row.get(column)

            if not values_equal(
                old_value,
                new_value,
            ):
                changes[column] = clean_value(
                    new_value
                )

        if changes:
            add_pending_update(
                primary_id,
                changes,
            )


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

    st.session_state.editor_source_df = None


def mark_selected_for_delete(
    selected_rows,
    primary_key: str,
):
    """
    Mark explicitly selected AG Grid records for deletion.
    """

    if not selected_rows:
        return

    for row in selected_rows:

        if primary_key not in row:
            continue

        primary_id = row.get(primary_key)

        if is_null_like(primary_id):
            continue

        key = str(primary_id)

        st.session_state.pending_deletes.add(
            key
        )

        # Remove pending updates because delete takes
        # precedence over update.
        st.session_state.pending_updates.pop(
            key,
            None,
        )


# ============================================================
# FILTER HELPERS
# ============================================================

def parse_integer_filter(value):
    """
    Convert numeric filter text into an integer.

    Returns None if invalid/blank.
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        return int(text)
    except (ValueError, TypeError):
        return None


def escape_ilike_value(value: str) -> str:
    """
    Escape %, _, and backslash for PostgreSQL ILIKE.
    """

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def apply_editor_filter(
    query,
    column,
    value,
    filter_name,
):
    """
    Apply the correct PostgreSQL operator.

    Numeric:
        eq()

    Text:
        ilike()

    PatientID:
        exact equality.
    """

    if not column:
        return query

    if value is None:
        return query

    value = str(value).strip()

    if not value:
        return query

    # --------------------------------------------------------
    # PRIMARY KEY
    # --------------------------------------------------------

    if filter_name == "patient_id":

        return query.eq(
            column,
            value,
        )

    # --------------------------------------------------------
    # NUMERIC COLUMNS
    # --------------------------------------------------------

    if filter_name in NUMERIC_FILTERS:

        numeric_value = parse_integer_filter(
            value
        )

        # Invalid numeric filter:
        # do not send an invalid ilike() request.
        if numeric_value is None:
            return query

        return query.eq(
            column,
            numeric_value,
        )

    # --------------------------------------------------------
    # TEXT COLUMNS
    # --------------------------------------------------------

    if filter_name in TEXT_FILTERS:

        safe_value = escape_ilike_value(
            value
        )

        return query.ilike(
            column,
            f"%{safe_value}%",
        )

    return query


# ============================================================
# EDITOR QUERY
# ============================================================

def build_editor_query(
    client: Client,
    columns,
    db_columns,
    filters: dict,
):
    """
    Build the filtered editor query.

    This function applies filters but does not execute.
    """

    query = (
        client
        .table(TABLE_NAME)
        .select("*")
    )

    # --------------------------------------------------------
    # FILTER MAPPING
    # --------------------------------------------------------

    mapping = {
        "patient_id": (
            db_columns.get("primary_key"),
        ),

        "team": (
            db_columns.get("team"),
        ),

        "tsp": (
            db_columns.get("tsp"),
        ),

        "approach": (
            db_columns.get("approach"),
        ),

        "case": (
            db_columns.get("case"),
        ),

        "visit_no": (
            db_columns.get("visit_no"),
        ),

        "sr_no": (
            db_columns.get("sr_no"),
        ),

        "ward_village": (
            db_columns.get("ward_village"),
        ),
    }

    # --------------------------------------------------------
    # TEXT / NUMERIC FILTERS
    # --------------------------------------------------------

    for filter_name, candidates in mapping.items():

        column = None

        for candidate in candidates:

            if candidate:
                column = resolve_column(
                    columns,
                    candidate,
                )

                if column:
                    break

        query = apply_editor_filter(
            query=query,
            column=column,
            value=filters.get(
                filter_name,
                "",
            ),
            filter_name=filter_name,
        )

    # --------------------------------------------------------
    # DATE FILTER
    # --------------------------------------------------------

    date_column = db_columns.get("date")

    date_from = filters.get(
        "date_from"
    )

    date_to = filters.get(
        "date_to"
    )

    if date_column:

        if date_from:

            query = query.gte(
                date_column,
                date_from.isoformat(),
            )

        if date_to:

            # Use < next day rather than <= end-of-day.
            next_day = (
                date_to
                + pd.Timedelta(days=1)
            )

            query = query.lt(
                date_column,
                next_day.date().isoformat(),
            )

    # --------------------------------------------------------
    # STABLE ORDER
    # --------------------------------------------------------

    primary_key = db_columns.get(
        "primary_key"
    )

    if primary_key:

        query = query.order(
            primary_key,
            desc=True,
        )

    return query


# ============================================================
# LOAD EDITOR PAGE
# ============================================================

def load_editor_page(
    client: Client,
    columns,
    db_columns,
    filters,
    page: int,
    page_size: int,
):
    """
    Load one server-side page.

    Returns:
        dataframe,
        total_count
    """

    # Build query with exact filters.
    base_query = build_editor_query(
        client=client,
        columns=columns,
        db_columns=db_columns,
        filters=filters,
    )

    # Get count separately.
    count_query = (
        client
        .table(TABLE_NAME)
        .select(
            "*",
            count="exact",
        )
    )

    # Reapply same filters to count query.
    count_query = build_editor_query(
        client=client,
        columns=columns,
        db_columns=db_columns,
        filters=filters,
    )

    # Execute count query.
    count_response = count_query.execute()

    total_count = (
        count_response.count
        if count_response.count is not None
        else 0
    )

    # Page range.
    start = max(
        0,
        (page - 1) * page_size,
    )

    end = start + page_size - 1

    # Apply range to the already-filtered query.
    response = (
        base_query
        .range(start, end)
        .execute()
    )

    rows = response.data or []

    df = pd.DataFrame(rows)

    return df, total_count


# ============================================================
# APPLY PENDING CHANGES TO GRID
# ============================================================

def merge_pending_changes_into_page(
    df: pd.DataFrame,
    primary_key: str,
):
    """
    Overlay pending UPDATE values onto currently displayed rows.

    Pending DELETE records are removed from the displayed grid.

    Pending INSERT records are NOT automatically added to the
    database grid because they do not yet exist in Supabase.
    """

    if df is None or df.empty:
        return df

    result = df.copy()

    # --------------------------------------------------------
    # Remove pending deletes
    # --------------------------------------------------------

    if primary_key in result.columns:

        delete_ids = {
            str(value)
            for value in st.session_state.pending_deletes
        }

        if delete_ids:

            mask = (
                ~result[primary_key]
                .astype(str)
                .isin(delete_ids)
            )

            result = result.loc[
                mask
            ].copy()

    # --------------------------------------------------------
    # Overlay pending updates
    # --------------------------------------------------------

    if (
        primary_key in result.columns
        and st.session_state.pending_updates
    ):

        for index in result.index:

            primary_id = result.at[
                index,
                primary_key,
            ]

            if is_null_like(primary_id):
                continue

            key = str(primary_id)

            changes = (
                st.session_state.pending_updates
                .get(key)
            )

            if not changes:
                continue

            for column, value in changes.items():

                if column in result.columns:
                    result.at[
                        index,
                        column,
                    ] = value

    return result


# ============================================================
# BUILD AG GRID
# ============================================================

def build_grid_options(
    df: pd.DataFrame,
    primary_key: str,
    updated_at_column: str | None,
    editable: bool,
):
    """
    Configure AG Grid.
    """

    gb = GridOptionsBuilder.from_dataframe(
        df
    )

    gb.configure_default_column(
        editable=editable,
        sortable=True,
        filter=True,
        resizable=True,
        minWidth=100,
    )

    # --------------------------------------------------------
    # Primary key
    # --------------------------------------------------------

    if primary_key in df.columns:

        gb.configure_column(
            primary_key,
            editable=False,
            pinned="left",
        )

    # --------------------------------------------------------
    # Updated timestamp
    # --------------------------------------------------------

    if (
        updated_at_column
        and updated_at_column in df.columns
    ):

        gb.configure_column(
            updated_at_column,
            editable=False,
        )

    # --------------------------------------------------------
    # Row selection
    # --------------------------------------------------------

    if can_delete:

        gb.configure_selection(
            selection_mode="multiple",
            use_checkbox=True,
            header_checkbox=True,
        )

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

    gb.configure_pagination(
        enabled=False
    )

    gb.configure_grid_options(
        rowHeight=32,
        headerHeight=38,
        suppressRowClickSelection=False,
        animateRows=False,
    )

    # --------------------------------------------------------
    # JavaScript: visually highlight changed cells
    # --------------------------------------------------------

    cell_style = JsCode(
        """
        function(params) {
            if (
                params.value !== null &&
                params.value !== undefined
            ) {
                return {};
            }
            return {};
        }
        """
    )

    # Avoid forcing cellStyle onto every column because the
    # grid can become slower with very wide tables.

    return gb.build()


# ============================================================
# EXPLORER SEARCH
# ============================================================

def build_explorer_query(
    client: Client,
    columns,
    db_columns,
    keyword: str,
    selected_columns: list[str],
):
    """
    Build Explorer query.

    IMPORTANT:
    The global keyword search only uses TEXT columns.

    We intentionally do not generate:
        numeric_column.ilike(...)

    because PostgreSQL integer ~~* text causes error 42883.
    """

    select_columns = (
        selected_columns
        if selected_columns
        else ["*"]
    )

    query = (
        client
        .table(TABLE_NAME)
        .select(
            ",".join(select_columns)
            if selected_columns
            else "*"
        )
    )

    keyword = str(
        keyword or ""
    ).strip()

    if keyword:

        safe_keyword = escape_ilike_value(
            keyword
        )

        # Known text columns only.
        text_columns = []

        for key in [
            "tsp",
            "approach",
            "case",
            "ward_village",
        ]:

            column = db_columns.get(key)

            if column and column in columns:
                text_columns.append(column)

        # PatientID is handled specially:
        # exact equality is safe for both text and numeric
        # types, but we do not know its type here.
        primary_key = db_columns.get(
            "primary_key"
        )

        # Build OR expression only from text columns.
        #
        # If there are no text columns, skip global keyword
        # filtering instead of sending an invalid query.
        if text_columns:

            expressions = [
                f"{column}.ilike.%{safe_keyword}%"
                for column in text_columns
            ]

            query = query.or_(
                ",".join(expressions)
            )

    # Stable order.
    primary_key = db_columns.get(
        "primary_key"
    )

    if primary_key:
        query = query.order(
            primary_key,
            desc=True,
        )

    return query


# ============================================================
# CSV EXPORT
# ============================================================

def export_filtered_csv(
    client: Client,
    columns,
    db_columns,
    keyword,
    selected_columns,
):
    """
    Retrieve ALL matching Explorer records and return CSV.
    """

    def query_builder(
        start,
        end,
    ):

        query = build_explorer_query(
            client=client,
            columns=columns,
            db_columns=db_columns,
            keyword=keyword,
            selected_columns=selected_columns,
        )

        return query.range(
            start,
            end,
        )

    df = fetch_all_from_query(
        query_builder,
        batch_size=CSV_BATCH_SIZE,
    )

    if df.empty:
        return b""

    return df.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )


# ============================================================
# SYNC
# ============================================================

def sync_pending_changes(
    client: Client,
    primary_key: str,
):
    """
    Synchronize pending UPDATE / INSERT / DELETE operations.

    Failed operations remain pending so they are not lost.
    """

    update_errors = []
    insert_errors = []
    delete_errors = []

    successful_updates = []
    successful_inserts = []
    successful_deletes = []

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for primary_id, changes in list(
        st.session_state.pending_updates.items()
    ):

        try:

            payload = clean_record(
                changes
            )

            if not payload:
                successful_updates.append(
                    primary_id
                )
                continue

            (
                client
                .table(TABLE_NAME)
                .update(payload)
                .eq(
                    primary_key,
                    clean_value(primary_id),
                )
                .execute()
            )

            successful_updates.append(
                primary_id
            )

        except Exception as exc:

            update_errors.append(
                f"UPDATE {primary_id}: {exc}"
            )

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    remaining_inserts = []

    for record in list(
        st.session_state.pending_inserts
    ):

        try:

            payload = clean_record(
                record
            )

            if not payload:
                continue

            (
                client
                .table(TABLE_NAME)
                .insert(payload)
                .execute()
            )

            successful_inserts.append(
                record
            )

        except Exception as exc:

            insert_errors.append(
                f"INSERT: {exc}"
            )

            remaining_inserts.append(
                record
            )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    for primary_id in list(
        st.session_state.pending_deletes
    ):

        try:

            (
                client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    primary_key,
                    clean_value(primary_id),
                )
                .execute()
            )

            successful_deletes.append(
                primary_id
            )

        except Exception as exc:

            delete_errors.append(
                f"DELETE {primary_id}: {exc}"
            )

    # --------------------------------------------------------
    # Remove successful operations only
    # --------------------------------------------------------

    for primary_id in successful_updates:

        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    st.session_state.pending_inserts = (
        remaining_inserts
    )

    for primary_id in successful_deletes:

        st.session_state.pending_deletes.discard(
            primary_id
        )

    # --------------------------------------------------------
    # Result
    # --------------------------------------------------------

    all_errors = (
        update_errors
        + insert_errors
        + delete_errors
    )

    return {
        "successful_updates": len(
            successful_updates
        ),
        "successful_inserts": len(
            successful_inserts
        ),
        "successful_deletes": len(
            successful_deletes
        ),
        "errors": all_errors,
    }


# ============================================================
# HEADER
# ============================================================

header_col1, header_col2 = st.columns(
    [8, 2]
)

with header_col1:

    st.title(
        "🗄️ YgnTBPro Database"
    )

    st.caption(
        f"Role: **{user_role.upper()}**"
    )

with header_col2:

    if st.button(
        "Logout",
        use_container_width=True,
    ):
        logout_user()


# ============================================================
# LOAD SUPABASE CLIENT / SCHEMA
# ============================================================

try:

    client = get_user_client()

    columns = get_table_columns()

    if not columns:
        st.error(
            f"No columns were returned from `{TABLE_NAME}`."
        )
        st.stop()

    db_columns = resolve_database_columns(
        columns
    )

    primary_key = db_columns.get(
        "primary_key"
    )

    if not primary_key:

        st.error(
            "Could not identify the PatientID primary-key column."
        )

        st.write(
            "Detected columns:",
            columns,
        )

        st.stop()

except Exception as exc:

    st.error(
        f"Unable to initialize database: {exc}"
    )

    st.stop()


# ============================================================
# TABS
# ============================================================

tab_editor, tab_explorer = st.tabs(
    [
        "📝 Database Editor",
        "🔎 Explorer",
    ]
)


# ============================================================
# DATABASE EDITOR
# ============================================================

with tab_editor:

    st.subheader(
        "Database Editor"
    )

    # --------------------------------------------------------
    # Permission information
    # --------------------------------------------------------

    if user_role == "viewer":

        st.info(
            "Viewer access: records can be viewed but not modified."
        )

    elif user_role == "editor":

        st.info(
            "Editor access: existing records can be updated. "
            "Adding and deleting records requires Admin access."
        )

    else:

        st.info(
            "Admin access: update, insert and delete operations are enabled."
        )

    # --------------------------------------------------------
    # FILTER RESET CALLBACK
    # --------------------------------------------------------

    def reset_editor_filters():

        filter_keys = [
            "filter_patient_id",
            "filter_team",
            "filter_tsp",
            "filter_approach",
            "filter_case",
            "filter_visit_no",
            "filter_sr_no",
            "filter_ward_village",
        ]

        for key in filter_keys:
            st.session_state[key] = ""

        st.session_state[
            "filter_date_from"
        ] = None

        st.session_state[
            "filter_date_to"
        ] = None

        st.session_state.editor_filters = {}

        st.session_state.editor_page = 1

        st.session_state.editor_loaded = False

        # IMPORTANT:
        # Do NOT clear pending changes here.
        st.session_state.filter_version += 1

    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------

    with st.expander(
        "🔍 Filters",
        expanded=True,
    ):

        row1 = st.columns(4)

        with row1[0]:

            patient_id_filter = st.text_input(
                "Patient ID",
                key="filter_patient_id",
            )

        with row1[1]:

            team_filter = st.text_input(
                "Team",
                key="filter_team",
                help="Numeric exact match.",
            )

        with row1[2]:

            tsp_filter = st.text_input(
                "TSP",
                key="filter_tsp",
            )

        with row1[3]:

            approach_filter = st.text_input(
                "Approach",
                key="filter_approach",
            )

        row2 = st.columns(4)

        with row2[0]:

            case_filter = st.text_input(
                "Case",
                key="filter_case",
            )

        with row2[1]:

            visit_no_filter = st.text_input(
                "Visit No",
                key="filter_visit_no",
                help="Numeric exact match.",
            )

        with row2[2]:

            sr_no_filter = st.text_input(
                "SR No",
                key="filter_sr_no",
                help="Numeric exact match.",
            )

        with row2[3]:

            ward_village_filter = st.text_input(
                "Ward / Village",
                key="filter_ward_village",
            )

        row3 = st.columns(3)

        with row3[0]:

            date_from = st.date_input(
                "Date From",
                value=None,
                key="filter_date_from",
            )

        with row3[1]:

            date_to = st.date_input(
                "Date To",
                value=None,
                key="filter_date_to",
            )

        with row3[2]:

            page_size = st.selectbox(
                "Rows per page",
                EDITOR_PAGE_SIZE_OPTIONS,
                index=(
                    EDITOR_PAGE_SIZE_OPTIONS.index(
                        st.session_state.editor_page_size
                    )
                    if st.session_state.editor_page_size
                    in EDITOR_PAGE_SIZE_OPTIONS
                    else 1
                ),
            )

        button_col1, button_col2 = st.columns(
            [1, 1]
        )

        with button_col1:

            apply_filters = st.button(
                "🔄 Load / Apply Filters",
                type="primary",
                use_container_width=True,
            )

        with button_col2:

            reset_filters = st.button(
                "↩️ Reset Filters",
                use_container_width=True,
                on_click=reset_editor_filters,
            )

    # --------------------------------------------------------
    # APPLY FILTERS
    # --------------------------------------------------------

    if apply_filters:

        st.session_state.editor_filters = {
            "patient_id": patient_id_filter,
            "team": team_filter,
            "tsp": tsp_filter,
            "approach": approach_filter,
            "case": case_filter,
            "visit_no": visit_no_filter,
            "sr_no": sr_no_filter,
            "ward_village": ward_village_filter,
            "date_from": date_from,
            "date_to": date_to,
        }

        st.session_state.editor_page = 1

        st.session_state.editor_page_size = (
            page_size
        )

        st.session_state.editor_loaded = True

        # Important:
        # We intentionally do not clear pending changes.
        st.session_state.editor_source_df = None

    # --------------------------------------------------------
    # DEFAULT INITIAL LOAD
    # --------------------------------------------------------

    if not st.session_state.editor_loaded:

        st.session_state.editor_filters = {
            "patient_id": "",
            "team": "",
            "tsp": "",
            "approach": "",
            "case": "",
            "visit_no": "",
            "sr_no": "",
            "ward_village": "",
            "date_from": None,
            "date_to": None,
        }

        st.session_state.editor_loaded = True

    # --------------------------------------------------------
    # LOAD PAGE
    # --------------------------------------------------------

    try:

        current_page = (
            st.session_state.editor_page
        )

        current_page_size = (
            st.session_state.editor_page_size
        )

        filters = (
            st.session_state.editor_filters
        )

        df, total_count = load_editor_page(
            client=client,
            columns=columns,
            db_columns=db_columns,
            filters=filters,
            page=current_page,
            page_size=current_page_size,
        )

        # ----------------------------------------------------
        # Overlay pending updates/deletes.
        # ----------------------------------------------------

        display_df = (
            merge_pending_changes_into_page(
                df,
                primary_key,
            )
        )

        # ----------------------------------------------------
        # Capture the source snapshot for this exact page.
        #
        # Always update when a fresh database page is loaded.
        # Pending changes themselves are preserved separately.
        # ----------------------------------------------------

        st.session_state.editor_source_df = (
            df.copy()
        )

    except Exception as exc:

        st.error(
            f"Unable to load records: {exc}"
        )

        st.stop()

    # ========================================================
    # PAGINATION INFORMATION
    # ========================================================

    total_pages = max(
        1,
        math.ceil(
            total_count
            / current_page_size
        ),
    )

    # The displayed page can become empty after pending deletes.
    displayed_count = len(
        display_df
    )

    info_col1, info_col2, info_col3 = st.columns(
        [2, 3, 2]
    )

    with info_col1:

        st.metric(
            "Database Records",
            f"{total_count:,}",
        )

    with info_col2:

        st.write(
            f"Page **{current_page} / {total_pages}** "
            f"· Showing **{displayed_count:,}** rows"
        )

    with info_col3:

        pending_count = (
            pending_changes_count()
        )

        if pending_count:

            st.warning(
                f"Pending changes: {pending_count}"
            )

        else:

            st.success(
                "No pending changes"
            )

    # ========================================================
    # AG GRID
    # ========================================================

    if display_df.empty:

        st.info(
            "No records found for the current filters."
        )

    else:

        grid_options = build_grid_options(
            df=display_df,
            primary_key=primary_key,
            updated_at_column=db_columns.get(
                "updated_at"
            ),
            editable=can_edit,
        )

        grid_key = (
            "ygntbpro_grid_"
            f"{st.session_state.grid_version}_"
            f"{st.session_state.filter_version}_"
            f"{current_page}"
        )

        grid_response = AgGrid(
            display_df,
            gridOptions=grid_options,
            data_return_mode=(
                DataReturnMode.AS_INPUT
            ),
            update_mode=(
                GridUpdateMode.VALUE_CHANGED
                | GridUpdateMode.SELECTION_CHANGED
            ),
            fit_columns_on_grid_load=False,
            allow_unsafe_jscode=True,
            enable_enterprise_modules=False,
            height=600,
            width="100%",
            reload_data=False,
            key=grid_key,
        )

        # ----------------------------------------------------
        # Capture edits
        # ----------------------------------------------------

        edited_rows = grid_response.get(
            "data"
        )

        if edited_rows is not None:

            if isinstance(
                edited_rows,
                pd.DataFrame,
            ):

                edited_df = (
                    edited_rows.copy()
                )

            else:

                edited_df = pd.DataFrame(
                    edited_rows
                )

            capture_grid_changes(
                source_df=(
                    st.session_state
                    .editor_source_df
                ),
                edited_df=edited_df,
                primary_key=primary_key,
            )

    # ========================================================
    # GRID ACTIONS
    # ========================================================

    action_col1, action_col2, action_col3, action_col4 = (
        st.columns(4)
    )

    # --------------------------------------------------------
    # Delete Selected
    # --------------------------------------------------------

    with action_col1:

        if can_delete:

            delete_clicked = st.button(
                "🗑️ Delete Selected",
                use_container_width=True,
            )

            if delete_clicked:

                selected_rows = (
                    grid_response.get(
                        "selected_rows",
                        [],
                    )
                    if "grid_response" in locals()
                    else []
                )

                if isinstance(
                    selected_rows,
                    pd.DataFrame,
                ):

                    selected_rows = (
                        selected_rows
                        .to_dict("records")
                    )

                mark_selected_for_delete(
                    selected_rows,
                    primary_key,
                )

                if selected_rows:

                    st.success(
                        f"Marked {len(selected_rows)} "
                        "record(s) for deletion."
                    )

                    st.rerun()

                else:

                    st.warning(
                        "Please select at least one record."
                    )

        else:

            st.button(
                "🗑️ Delete Selected",
                disabled=True,
                use_container_width=True,
            )

    # --------------------------------------------------------
    # Add New Record
    # --------------------------------------------------------

    with action_col2:

        if can_add:

            if st.button(
                "➕ Add New Record",
                use_container_width=True,
            ):

                st.session_state.show_add_form = (
                    not st.session_state.show_add_form
                )

                st.rerun()

        else:

            st.button(
                "➕ Add New Record",
                disabled=True,
                use_container_width=True,
            )

    # --------------------------------------------------------
    # Previous
    # --------------------------------------------------------

    with action_col3:

        previous_disabled = (
            current_page <= 1
        )

        if st.button(
            "⬅️ Previous",
            disabled=previous_disabled,
            use_container_width=True,
        ):

            st.session_state.editor_page = (
                current_page - 1
            )

            # Do not clear pending changes.
            st.session_state.editor_source_df = None

            st.rerun()

    # --------------------------------------------------------
    # Next
    # --------------------------------------------------------

    with action_col4:

        next_disabled = (
            current_page >= total_pages
        )

        if st.button(
            "Next ➡️",
            disabled=next_disabled,
            use_container_width=True,
        ):

            st.session_state.editor_page = (
                current_page + 1
            )

            # Do not clear pending changes.
            st.session_state.editor_source_df = None

            st.rerun()

    # ========================================================
    # ADD NEW RECORD FORM
    # ========================================================

    if (
        can_add
        and st.session_state.show_add_form
    ):

        st.divider()

        st.subheader(
            "➕ Add New Record"
        )

        st.caption(
            "Enter values for the new record. "
            "The record will remain pending until Sync."
        )

        with st.form(
            "add_new_record_form"
        ):

            add_values = {}

            # Wide tables can have many columns.
            # Use 4 columns for compact entry.
            form_columns = st.columns(4)

            for index, column in enumerate(
                columns
            ):

                with form_columns[
                    index % 4
                ]:

                    # Primary key is required/important,
                    # but still rendered as input.
                    add_values[column] = (
                        st.text_input(
                            column,
                            key=(
                                f"new_record_"
                                f"{index}_"
                                f"{st.session_state.grid_version}"
                            ),
                        )
                    )

            submitted_new = st.form_submit_button(
                "Add to Pending Changes",
                type="primary",
                use_container_width=True,
            )

        if submitted_new:

            record = {}

            for column, value in add_values.items():

                if str(value).strip() == "":
                    continue

                record[column] = value

            if not record.get(primary_key):

                st.error(
                    f"{primary_key} is required."
                )

            else:

                # Convert known numeric columns.
                for numeric_key in [
                    db_columns.get("team"),
                    db_columns.get("visit_no"),
                    db_columns.get("sr_no"),
                ]:

                    if (
                        numeric_key
                        and numeric_key in record
                    ):

                        parsed = parse_integer_filter(
                            record[numeric_key]
                        )

                        if parsed is not None:
                            record[numeric_key] = parsed

                add_pending_insert(
                    record
                )

                st.success(
                    "New record added to pending changes."
                )

                st.session_state.show_add_form = (
                    False
                )

                st.rerun()

    # ========================================================
    # PENDING CHANGES
    # ========================================================

    pending_count = (
        pending_changes_count()
    )

    if pending_count:

        st.divider()

        st.subheader(
            f"⏳ Pending Changes ({pending_count})"
        )

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        summary_col1, summary_col2, summary_col3 = st.columns(
            3
        )

        with summary_col1:

            st.metric(
                "Updates",
                len(
                    st.session_state.pending_updates
                ),
            )

        with summary_col2:

            st.metric(
                "Inserts",
                len(
                    st.session_state.pending_inserts
                ),
            )

        with summary_col3:

            st.metric(
                "Deletes",
                len(
                    st.session_state.pending_deletes
                ),
            )

        # ----------------------------------------------------
        # Updates
        # ----------------------------------------------------

        if st.session_state.pending_updates:

            st.markdown(
                "**UPDATE**"
            )

            update_preview = []

            for (
                primary_id,
                changes,
            ) in st.session_state.pending_updates.items():

                for column, value in changes.items():

                    update_preview.append(
                        {
                            primary_key: primary_id,
                            "Column": column,
                            "New Value": value,
                        }
                    )

            if update_preview:

                st.dataframe(
                    pd.DataFrame(
                        update_preview
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        # ----------------------------------------------------
        # Inserts
        # ----------------------------------------------------

        if st.session_state.pending_inserts:

            st.markdown(
                "**INSERT**"
            )

            st.dataframe(
                pd.DataFrame(
                    st.session_state.pending_inserts
                ),
                use_container_width=True,
                hide_index=True,
            )

        # ----------------------------------------------------
        # Deletes
        # ----------------------------------------------------

        if st.session_state.pending_deletes:

            st.markdown(
                "**DELETE**"
            )

            st.write(
                list(
                    st.session_state.pending_deletes
                )
            )

        # ----------------------------------------------------
        # Sync / Discard
        # ----------------------------------------------------

        sync_col, discard_col = st.columns(
            2
        )

        with sync_col:

            sync_clicked = st.button(
                "💾 Sync Changes to Supabase",
                type="primary",
                use_container_width=True,
            )

        with discard_col:

            discard_clicked = st.button(
                "↩️ Discard All Pending Changes",
                use_container_width=True,
            )

        if discard_clicked:

            clear_pending_changes()

            st.success(
                "All pending changes have been discarded."
            )

            st.session_state.grid_version += 1

            st.rerun()

        if sync_clicked:

            with st.spinner(
                "Synchronizing changes..."
            ):

                result = (
                    sync_pending_changes(
                        client=client,
                        primary_key=primary_key,
                    )
                )

            if result["errors"]:

                st.error(
                    "Some changes could not be synchronized."
                )

                for error in result["errors"]:
                    st.error(error)

            else:

                st.success(
                    "All pending changes synchronized successfully."
                )

            if (
                result["successful_updates"]
                or result["successful_inserts"]
                or result["successful_deletes"]
            ):

                st.info(
                    " | ".join(
                        [
                            (
                                f"Updated: "
                                f"{result['successful_updates']}"
                            ),
                            (
                                f"Inserted: "
                                f"{result['successful_inserts']}"
                            ),
                            (
                                f"Deleted: "
                                f"{result['successful_deletes']}"
                            ),
                        ]
                    )
                )

            st.session_state.editor_source_df = None

            st.session_state.grid_version += 1

            st.rerun()


# ============================================================
# EXPLORER
# ============================================================

with tab_explorer:

    st.subheader(
        "🔎 Database Explorer"
    )

    st.caption(
        "Explorer is read-only. "
        "Global keyword search uses text columns only."
    )

    explorer_col1, explorer_col2 = st.columns(
        [3, 2]
    )

    with explorer_col1:

        explorer_keyword = st.text_input(
            "Keyword",
            key="explorer_keyword",
            placeholder=(
                "Search TSP, Approach, Case, Ward/Village..."
            ),
        )

    with explorer_col2:

        explorer_columns = st.multiselect(
            "Columns",
            options=columns,
            default=[],
            key="explorer_columns",
        )

    explorer_button_col1, explorer_button_col2 = st.columns(
        2
    )

    with explorer_button_col1:

        load_explorer = st.button(
            "🔎 Search / Load",
            type="primary",
            use_container_width=True,
        )

    with explorer_button_col2:

        export_csv = st.button(
            "⬇️ Export ALL Matching CSV",
            use_container_width=True,
        )

    # --------------------------------------------------------
    # Explorer search
    # --------------------------------------------------------

    if load_explorer:

        try:

            explorer_query = build_explorer_query(
                client=client,
                columns=columns,
                db_columns=db_columns,
                keyword=explorer_keyword,
                selected_columns=explorer_columns,
            )

            explorer_response = (
                explorer_query
                .range(
                    0,
                    EXPLORER_DISPLAY_LIMIT - 1,
                )
                .execute()
            )

            explorer_rows = (
                explorer_response.data
                or []
            )

            if explorer_rows:

                explorer_df = pd.DataFrame(
                    explorer_rows
                )

                st.session_state[
                    "explorer_loaded"
                ] = True

                st.session_state[
                    "explorer_result"
                ] = explorer_df

            else:

                st.session_state[
                    "explorer_loaded"
                ] = True

                st.session_state[
                    "explorer_result"
                ] = pd.DataFrame()

        except Exception as exc:

            st.error(
                f"Unable to search Explorer: {exc}"
            )

    # --------------------------------------------------------
    # Export ALL
    # --------------------------------------------------------

    if export_csv:

        try:

            with st.spinner(
                "Preparing complete CSV export..."
            ):

                csv_bytes = export_filtered_csv(
                    client=client,
                    columns=columns,
                    db_columns=db_columns,
                    keyword=explorer_keyword,
                    selected_columns=explorer_columns,
                )

            if csv_bytes:

                st.download_button(
                    label="⬇️ Download CSV",
                    data=csv_bytes,
                    file_name=(
                        "ygntbpro_export.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                )

                st.success(
                    "CSV prepared successfully."
                )

            else:

                st.warning(
                    "No matching records found."
                )

        except Exception as exc:

            st.error(
                f"Unable to export CSV: {exc}"
            )

    # --------------------------------------------------------
    # Display result
    # --------------------------------------------------------

    if st.session_state.get(
        "explorer_loaded",
        False,
    ):

        explorer_df = st.session_state.get(
            "explorer_result",
            pd.DataFrame(),
        )

        if explorer_df.empty:

            st.info(
                "No records found."
            )

        else:

            st.success(
                f"Displaying up to "
                f"{EXPLORER_DISPLAY_LIMIT:,} "
                "matching records."
            )

            st.dataframe(
                explorer_df,
                use_container_width=True,
                height=600,
                hide_index=True,
            )

            # ------------------------------------------------
            # Basic information
            # ------------------------------------------------

            with st.expander(
                "📊 Data Information"
            ):

                info_col1, info_col2, info_col3 = st.columns(
                    3
                )

                with info_col1:

                    st.metric(
                        "Rows displayed",
                        f"{len(explorer_df):,}",
                    )

                with info_col2:

                    st.metric(
                        "Columns",
                        f"{len(explorer_df.columns):,}",
                    )

                with info_col3:

                    st.metric(
                        "Missing values",
                        f"{int(explorer_df.isna().sum().sum()):,}",
                    )

                dtype_df = pd.DataFrame(
                    {
                        "Column": explorer_df.columns,
                        "Data Type": [
                            str(dtype)
                            for dtype in explorer_df.dtypes
                        ],
                        "Null Count": [
                            int(
                                explorer_df[column]
                                .isna()
                                .sum()
                            )
                            for column in explorer_df.columns
                        ],
                    }
                )

                st.dataframe(
                    dtype_df,
                    use_container_width=True,
                    hide_index=True,
                )