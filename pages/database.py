# database.py
# ============================================================
# Optimized Supabase / Streamlit database editor
#
# Features
#   - Supabase email/password authentication
#   - RLS-aware authenticated client
#   - user_roles lookup by authenticated user UUID
#   - Viewer / Editor / Admin permissions
#   - Server-side filtering
#   - Server-side pagination
#   - AG Grid editor
#   - Persistent pending UPDATE / INSERT / DELETE
#   - Add Row
#   - Delete Selected
#   - Sync / Discard
#   - Explorer
#   - CSV export
#   - JSON-safe Supabase payloads
#
# Table:
#   ygntbpro
#
# Primary key:
#   PatientID
# ============================================================

import os
from datetime import date, datetime, timedelta
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
# STREAMLIT PAGE CONFIG
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
ROLE_TABLE = "user_roles"

PRIMARY_KEY_CANDIDATES = [
    "PatientID",
    "patientid",
    "patient_id",
]

DATE_CANDIDATES = [
    "Date",
    "date",
]

# Server request size.
# This is NOT the maximum number of records in the database.
BATCH_SIZE = 1000

# Number of rows rendered in AG Grid at one time.
EDITOR_PAGE_SIZE = 300

# Explorer display limit.
EXPLORER_DISPLAY_LIMIT = 1000

# Number of records requested for CSV export per batch.
CSV_BATCH_SIZE = 1000


# ============================================================
# SESSION STATE
# ============================================================

DEFAULTS = {
    "session": None,
    "user_role": None,

    # Editor
    "editor_loaded": False,
    "editor_query_version": 0,
    "editor_page": 1,
    "editor_page_size": EDITOR_PAGE_SIZE,

    # Current loaded editor filters
    "editor_filters": None,

    # Pending changes
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # New rows
    "new_row_buffer": [],

    # UI
    "active_tab": "Editor",
    "explorer_query_version": 0,

    # Prevent repeated login/error messages
    "login_message": None,
}


for key, default_value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default_value


# ============================================================
# ENVIRONMENT / SECRETS
# ============================================================

def get_secret(name: str, default: str | None = None) -> str | None:
    """
    Read a configuration value from Streamlit secrets first,
    then environment variables.
    """

    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass

    return os.getenv(name, default)


SUPABASE_URL = (
    get_secret("SUPABASE_URL_ygntbpro")
    or get_secret("SUPABASE_URL")
)

SUPABASE_KEY = (
    get_secret("SUPABASE_KEY_ygntbpro")
    or get_secret("SUPABASE_KEY")
)


# ============================================================
# BASIC VALIDATION
# ============================================================

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error(
        "Supabase configuration is missing. "
        "Please configure SUPABASE_URL_ygntbpro and "
        "SUPABASE_KEY_ygntbpro in Streamlit secrets."
    )
    st.stop()


# ============================================================
# SUPABASE CLIENT
# ============================================================

@st.cache_resource(show_spinner=False)
def get_base_client() -> Client:
    """
    Create the base Supabase client.

    This client is used only for operations that do not need
    the logged-in user's session.
    """

    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )


def get_user_client() -> Client:
    """
    Create an authenticated Supabase client using the current
    Streamlit session.

    IMPORTANT:
    Do not cache this client because it contains user-specific
    authentication state.
    """

    session = st.session_state.get("session")

    if not session:
        raise RuntimeError("No authenticated session.")

    client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)

    if not access_token:
        raise RuntimeError("Authenticated session has no access token.")

    # Preferred method
    try:
        if refresh_token:
            client.auth.set_session(
                access_token,
                refresh_token,
            )
        else:
            # Some versions still accept set_session with
            # an empty/None refresh token.
            client.auth.set_session(
                access_token,
                "",
            )

        return client

    except Exception:
        # Compatibility fallback for different supabase-py versions.
        try:
            client.postgrest.auth(access_token)
            return client
        except Exception as exc:
            raise RuntimeError(
                f"Unable to establish authenticated Supabase client: {exc}"
            )


# ============================================================
# AUTHENTICATION
# ============================================================

def login_user(email: str, password: str):
    """
    Authenticate user and retrieve role by authenticated user UUID.

    Role lookup is intentionally NOT performed by email.
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
            return False, "Login failed: no authenticated session was returned."

        st.session_state.session = response.session

        user_client = get_user_client()

        role_result = (
            user_client.table(ROLE_TABLE)
            .select("role")
            .eq(
                "user_id",
                response.session.user.id,
            )
            .limit(1)
            .execute()
        )

        role = (
            role_result.data[0].get("role", "viewer")
            if role_result.data
            else "viewer"
        )

        role = str(role).strip().lower()

        if role not in {"viewer", "editor", "admin"}:
            role = "viewer"

        st.session_state.user_role = role

        return True, "Login successful."

    except Exception as exc:
        return False, str(exc)


def logout_user():
    """
    Logout current user and clear local application state.
    """

    try:
        base_supabase = get_base_client()
        base_supabase.auth.sign_out()
    except Exception:
        pass

    for key, value in DEFAULTS.items():

        # Create a fresh set for pending deletes.
        if key == "pending_deletes":
            st.session_state[key] = set()
        elif key == "pending_updates":
            st.session_state[key] = {}
        elif key == "pending_inserts":
            st.session_state[key] = []
        else:
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
            type="primary",
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
# CURRENT USER / PERMISSIONS
# ============================================================

user_role = (
    st.session_state.get("user_role")
    or "viewer"
).lower()

can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"

can_delete = user_role == "admin"

current_session = st.session_state.session

current_user = getattr(
    current_session,
    "user",
    None,
)

current_email = (
    getattr(current_user, "email", None)
    if current_user
    else None
)

current_user_id = (
    getattr(current_user, "id", None)
    if current_user
    else None
)


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def resolve_column(
    columns,
    *candidates,
):
    """
    Return the actual column name matching one of the candidates.
    Matching is case-insensitive.
    """

    columns = list(columns)

    lower_map = {
        str(column).lower(): column
        for column in columns
    }

    for candidate in candidates:

        if candidate in columns:
            return candidate

        actual = lower_map.get(
            str(candidate).lower()
        )

        if actual is not None:
            return actual

    return None


def values_equal(left, right) -> bool:
    """
    Robust comparison for pandas/NumPy/date/NaN values.
    """

    if left is None and right is None:
        return True

    try:
        if pd.isna(left) and pd.isna(right):
            return True
    except Exception:
        pass

    try:
        return bool(left == right)
    except Exception:
        return str(left) == str(right)


def clean_value(value):
    """
    Convert pandas/NumPy values into values suitable for Supabase JSON.
    """

    if value is None:
        return None

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        if np.isnan(value):
            return None
        return float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, float):
        if np.isnan(value) or np.isinf(value):
            return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    return value


def make_json_safe(data):
    """
    Recursively make dictionaries/lists JSON safe.
    """

    if isinstance(data, dict):

        return {
            str(key): make_json_safe(value)
            for key, value in data.items()
        }

    if isinstance(data, (list, tuple, set)):

        return [
            make_json_safe(value)
            for value in data
        ]

    return clean_value(data)


def dataframe_to_records(df: pd.DataFrame):
    """
    Convert DataFrame to JSON-safe record dictionaries.
    """

    if df is None or df.empty:
        return []

    records = df.to_dict(
        orient="records"
    )

    return make_json_safe(records)


# ============================================================
# PENDING CHANGES
# ============================================================

def add_pending_update(
    primary_id,
    changes: dict,
):
    """
    Add or merge an UPDATE into pending_updates.
    """

    primary_id = clean_value(primary_id)

    if primary_id is None:
        return

    if primary_id in st.session_state.pending_deletes:
        return

    if primary_id not in st.session_state.pending_updates:
        st.session_state.pending_updates[primary_id] = {}

    st.session_state.pending_updates[primary_id].update(
        make_json_safe(changes)
    )


def remove_pending_update(
    primary_id,
):
    st.session_state.pending_updates.pop(
        primary_id,
        None,
    )


def add_pending_delete(
    primary_id,
):
    """
    Add a DELETE while removing any pending UPDATE for
    the same record.
    """

    primary_id = clean_value(primary_id)

    if primary_id is None:
        return

    st.session_state.pending_deletes.add(
        primary_id
    )

    st.session_state.pending_updates.pop(
        primary_id,
        None,
    )


def add_pending_insert(
    record: dict,
):
    """
    Add a new record to pending inserts.
    """

    record = make_json_safe(record)

    if not record:
        return

    st.session_state.pending_inserts.append(
        record
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
    st.session_state.new_row_buffer = []


# ============================================================
# SCHEMA
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_table_columns_cached():
    """
    Get column names using the public/base client.

    Schema is not user-specific, so a short cache is appropriate.
    """

    client = get_base_client()

    response = (
        client.table(TABLE_NAME)
        .select("*")
        .limit(1)
        .execute()
    )

    data = response.data or []

    if not data:
        return []

    return list(
        data[0].keys()
    )


def get_table_columns():
    return get_table_columns_cached()


# ============================================================
# PAGINATED SUPABASE QUERY
# ============================================================

def fetch_page_from_query(
    query_builder,
    page: int,
    page_size: int,
):
    """
    Execute a query for exactly one UI page.

    This prevents the entire table from being sent to Streamlit.
    """

    page = max(
        1,
        int(page),
    )

    page_size = max(
        1,
        int(page_size),
    )

    start = (
        page - 1
    ) * page_size

    end = (
        start
        + page_size
        - 1
    )

    response = (
        query_builder
        .range(start, end)
        .execute()
    )

    rows = response.data or []

    return pd.DataFrame(rows)


# ============================================================
# FILTER QUERY BUILDER
# ============================================================

def apply_text_filter(
    query,
    column,
    value,
):
    """
    Case-insensitive contains filter.

    Uses ilike so users can enter partial values.
    """

    if (
        column
        and value is not None
        and str(value).strip()
    ):

        value = str(value).strip()

        # Escape characters that have special meaning in
        # PostgreSQL LIKE expressions.
        value = (
            value
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

        query = query.ilike(
            column,
            f"%{value}%",
        )

    return query


def apply_exact_filter(
    query,
    column,
    value,
):
    if (
        column
        and value is not None
        and str(value).strip()
    ):

        query = query.eq(
            column,
            str(value).strip(),
        )

    return query


def build_filtered_query(
    client,
    columns,
    filters,
    count=False,
):
    """
    Build a server-side filtered query.

    All filtering occurs in PostgreSQL/PostgREST.
    """

    query = (
        client
        .table(TABLE_NAME)
        .select(
            "*",
            count="exact" if count else None,
        )
    )

    # --------------------------------------------------------
    # Patient ID
    # --------------------------------------------------------

    patient_col = resolve_column(
        columns,
        *PRIMARY_KEY_CANDIDATES,
    )

    patient_id = filters.get(
        "patient_id",
        "",
    )

    if (
        patient_col
        and patient_id
        and str(patient_id).strip()
    ):

        # PatientID is normally unique, so exact matching is
        # substantially faster than loading all IDs.
        query = query.eq(
            patient_col,
            str(patient_id).strip(),
        )

    # --------------------------------------------------------
    # Text filters
    # --------------------------------------------------------

    mapping = {
        "team": [
            "team",
            "Team",
        ],
        "tsp": [
            "tsp",
            "Tsp",
            "TSP",
        ],
        "approach": [
            "approach",
            "Approach",
        ],
        "case": [
            "case",
            "Case",
        ],
        "visit_no": [
            "visitno",
            "Visit_no",
            "visit_no",
        ],
        "sr_no": [
            "srno",
            "Sr_No",
            "sr_no",
        ],
        "ward_village": [
            "wardvillage",
            "WardVillage",
            "ward_village",
        ],
    }

    for filter_name, candidates in mapping.items():

        column = resolve_column(
            columns,
            *candidates,
        )

        query = apply_text_filter(
            query,
            column,
            filters.get(
                filter_name,
                "",
            ),
        )

    # --------------------------------------------------------
    # Date range
    # --------------------------------------------------------

    date_col = resolve_column(
        columns,
        *DATE_CANDIDATES,
    )

    date_from = filters.get(
        "date_from"
    )

    date_to = filters.get(
        "date_to"
    )

    if date_col:

        if date_from:
            query = query.gte(
                date_col,
                date_from.isoformat(),
            )

        if date_to:
            next_day = (
                date_to
                + timedelta(days=1)
            )

            # Use < next day rather than <= 23:59:59.
            query = query.lt(
                date_col,
                next_day.isoformat(),
            )

    # --------------------------------------------------------
    # Stable ordering
    # --------------------------------------------------------

    if patient_col:
        query = query.order(
            patient_col,
            desc=True,
        )

    return query


# ============================================================
# RECORD COUNT
# ============================================================

def get_filtered_count(
    client,
    columns,
    filters,
):
    """
    Get exact matching row count.

    This is a separate request because the actual page query
    only needs the visible rows.
    """

    try:

        query = build_filtered_query(
            client=client,
            columns=columns,
            filters=filters,
            count=True,
        )

        response = (
            query
            .range(0, 0)
            .execute()
        )

        return (
            response.count
            if response.count is not None
            else None
        )

    except Exception:
        return None


# ============================================================
# APPLY PENDING CHANGES TO DISPLAYED PAGE
# ============================================================

def merge_pending_updates_into_page(
    df: pd.DataFrame,
):
    """
    Overlay pending UPDATE values onto the currently displayed
    page so edits remain visible after pagination/filter changes.
    """

    if df is None or df.empty:
        return df

    if not st.session_state.pending_updates:
        return df

    primary_col = resolve_column(
        df.columns,
        *PRIMARY_KEY_CANDIDATES,
    )

    if not primary_col:
        return df

    result = df.copy()

    for index, row in result.iterrows():

        primary_id = clean_value(
            row.get(primary_col)
        )

        if primary_id in st.session_state.pending_updates:

            changes = st.session_state.pending_updates[
                primary_id
            ]

            for column, value in changes.items():

                if column in result.columns:
                    result.at[
                        index,
                        column,
                    ] = value

    return result


def remove_pending_deleted_rows(
    df: pd.DataFrame,
):
    """
    Do not display records that have been marked for deletion.
    """

    if df is None or df.empty:
        return df

    if not st.session_state.pending_deletes:
        return df

    primary_col = resolve_column(
        df.columns,
        *PRIMARY_KEY_CANDIDATES,
    )

    if not primary_col:
        return df

    mask = ~df[
        primary_col
    ].map(clean_value).isin(
        st.session_state.pending_deletes
    )

    return df.loc[
        mask
    ].reset_index(
        drop=True
    )


# ============================================================
# CAPTURE AG GRID EDITS
# ============================================================

def capture_grid_changes(
    original_df: pd.DataFrame,
    edited_df: pd.DataFrame,
):
    """
    Compare only the currently displayed page.

    IMPORTANT:
    Missing rows are NOT treated as deletions.
    Deletion is explicit through Delete Selected.

    This is necessary for server-side pagination.
    """

    if (
        original_df is None
        or edited_df is None
        or original_df.empty
        or edited_df.empty
    ):
        return

    primary_col = resolve_column(
        original_df.columns,
        *PRIMARY_KEY_CANDIDATES,
    )

    if not primary_col:
        return

    original = original_df.copy()
    edited = edited_df.copy()

    if primary_col not in edited.columns:
        return

    original_index = {}

    for _, row in original.iterrows():

        primary_id = clean_value(
            row.get(primary_col)
        )

        if primary_id is not None:
            original_index[
                primary_id
            ] = row

    for _, row in edited.iterrows():

        primary_id = clean_value(
            row.get(primary_col)
        )

        if primary_id is None:
            continue

        if primary_id not in original_index:
            continue

        original_row = original_index[
            primary_id
        ]

        changes = {}

        for column in edited.columns:

            if column == primary_col:
                continue

            if column not in original.columns:
                continue

            old_value = original_row.get(
                column
            )

            new_value = row.get(
                column
            )

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


# ============================================================
# AG GRID
# ============================================================

def create_grid_options(
    df: pd.DataFrame,
    primary_col: str | None,
    editable: bool,
    selected_ids: set,
):
    """
    Create optimized AG Grid configuration.
    """

    gb = GridOptionsBuilder.from_dataframe(
        df
    )

    gb.configure_default_column(
        editable=editable,
        resizable=True,
        sortable=True,
        filter=True,
        minWidth=110,
    )

    if primary_col:
        gb.configure_column(
            primary_col,
            editable=False,
            pinned="left",
            checkboxSelection=can_delete,
            headerCheckboxSelection=can_delete,
            width=160,
        )

    updated_at_col = resolve_column(
        df.columns,
        "updated_at",
        "Updated_at",
        "updatedAt",
    )

    if updated_at_col:
        gb.configure_column(
            updated_at_col,
            editable=False,
        )

    # Keep grid height manageable.
    gb.configure_grid_options(
        rowHeight=32,
        headerHeight=38,
        suppressRowClickSelection=False,
        enableRangeSelection=True,
        pagination=False,
        animateRows=False,
        suppressColumnVirtualisation=False,
        suppressRowVirtualisation=False,
        rowBuffer=5,
    )

    if can_delete:
        gb.configure_selection(
            selection_mode="multiple",
            use_checkbox=True,
            header_checkbox=True,
        )

    # JavaScript to format nulls consistently.
    null_formatter = JsCode(
        """
        function(params) {
            if (params.value === null ||
                params.value === undefined ||
                params.value === "") {
                return "";
            }
            return params.value;
        }
        """
    )

    for column in df.columns:
        gb.configure_column(
            column,
            valueFormatter=null_formatter,
        )

    return gb.build()


# ============================================================
# FETCH EDITOR PAGE
# ============================================================

def load_editor_page(
    client,
    columns,
    filters,
    page,
    page_size,
):
    """
    Load only one page from Supabase.
    """

    query = build_filtered_query(
        client=client,
        columns=columns,
        filters=filters,
        count=False,
    )

    df = fetch_page_from_query(
        query_builder=query,
        page=page,
        page_size=page_size,
    )

    if df.empty:
        return df

    df = remove_pending_deleted_rows(
        df
    )

    df = merge_pending_updates_into_page(
        df
    )

    return df


# ============================================================
# SYNC OPERATIONS
# ============================================================

def sync_pending_changes():
    """
    Apply UPDATE / INSERT / DELETE operations to Supabase.

    Successful operations are removed from the pending queue.
    Failed operations remain pending.
    """

    if pending_changes_count() == 0:
        return 0, 0, []

    client = get_user_client()

    errors = []
    success_count = 0
    attempted_count = 0

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for primary_id, changes in list(
        st.session_state.pending_updates.items()
    ):

        attempted_count += 1

        try:

            if not changes:
                del st.session_state.pending_updates[
                    primary_id
                ]
                continue

            payload = make_json_safe(
                changes
            )

            (
                client
                .table(TABLE_NAME)
                .update(payload)
                .eq(
                    resolve_primary_key_name(),
                    primary_id,
                )
                .execute()
            )

            del st.session_state.pending_updates[
                primary_id
            ]

            success_count += 1

        except Exception as exc:

            errors.append(
                f"UPDATE {primary_id}: {exc}"
            )

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    successful_inserts = []

    for index, record in enumerate(
        list(st.session_state.pending_inserts)
    ):

        attempted_count += 1

        try:

            payload = make_json_safe(
                record
            )

            if not payload:
                successful_inserts.append(
                    index
                )
                continue

            (
                client
                .table(TABLE_NAME)
                .insert(payload)
                .execute()
            )

            successful_inserts.append(
                index
            )

            success_count += 1

        except Exception as exc:

            errors.append(
                f"INSERT row {index + 1}: {exc}"
            )

    if successful_inserts:

        st.session_state.pending_inserts = [
            record
            for index, record in enumerate(
                st.session_state.pending_inserts
            )
            if index not in successful_inserts
        ]

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    for primary_id in list(
        st.session_state.pending_deletes
    ):

        attempted_count += 1

        try:

            (
                client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    resolve_primary_key_name(),
                    primary_id,
                )
                .execute()
            )

            st.session_state.pending_deletes.remove(
                primary_id
            )

            success_count += 1

        except Exception as exc:

            errors.append(
                f"DELETE {primary_id}: {exc}"
            )

    return (
        success_count,
        attempted_count,
        errors,
    )


# ============================================================
# PRIMARY KEY RESOLUTION
# ============================================================

def resolve_primary_key_name():
    columns = get_table_columns()

    primary_col = resolve_column(
        columns,
        *PRIMARY_KEY_CANDIDATES,
    )

    if not primary_col:
        raise RuntimeError(
            "PatientID primary key column was not found."
        )

    return primary_col


# ============================================================
# HEADER
# ============================================================

header_col1, header_col2, header_col3 = st.columns(
    [5, 2, 1]
)

with header_col1:

    st.title("🗄️ YgnTBPro Database")

with header_col2:

    st.caption(
        f"User: {current_email or 'Unknown'}"
    )

    st.caption(
        f"Role: **{user_role.upper()}**"
    )

with header_col3:

    if st.button(
        "Logout",
        use_container_width=True,
    ):
        logout_user()


# ============================================================
# ROLE INFORMATION
# ============================================================

if user_role == "viewer":

    st.info(
        "Viewer mode: records can be viewed and exported, "
        "but cannot be changed."
    )

elif user_role == "editor":

    st.info(
        "Editor mode: existing records can be edited. "
        "Adding and deleting records requires Admin permission."
    )

elif user_role == "admin":

    st.success(
        "Admin mode: edit, add and delete permissions enabled."
    )


# ============================================================
# PENDING CHANGES SUMMARY
# ============================================================

pending_updates_count = len(
    st.session_state.pending_updates
)

pending_inserts_count = len(
    st.session_state.pending_inserts
)

pending_deletes_count = len(
    st.session_state.pending_deletes
)

total_pending = (
    pending_updates_count
    + pending_inserts_count
    + pending_deletes_count
)


if total_pending:

    c1, c2, c3, c4, c5 = st.columns(
        [1, 1, 1, 1.3, 1]
    )

    with c1:
        st.metric(
            "Pending",
            total_pending,
        )

    with c2:
        st.metric(
            "Updates",
            pending_updates_count,
        )

    with c3:
        st.metric(
            "Inserts",
            pending_inserts_count,
        )

    with c4:
        st.metric(
            "Deletes",
            pending_deletes_count,
        )

    with c5:

        if st.button(
            "Discard All",
            use_container_width=True,
        ):

            clear_pending_changes()

            st.rerun()


# ============================================================
# LOAD SCHEMA
# ============================================================

try:

    table_columns = get_table_columns()

except Exception as exc:

    st.error(
        f"Unable to read {TABLE_NAME} schema: {exc}"
    )
    st.stop()


if not table_columns:

    st.error(
        f"No columns were found in {TABLE_NAME}."
    )
    st.stop()


primary_col = resolve_column(
    table_columns,
    *PRIMARY_KEY_CANDIDATES,
)

date_col = resolve_column(
    table_columns,
    *DATE_CANDIDATES,
)


# ============================================================
# MAIN TABS
# ============================================================

editor_tab, explorer_tab = st.tabs(
    [
        "✏️ Database Editor",
        "🔎 Explorer",
    ]
)


# ============================================================
# EDITOR TAB
# ============================================================

with editor_tab:

    st.subheader("Database Editor")

    # --------------------------------------------------------
    # FILTER PANEL
    # --------------------------------------------------------

    with st.expander(
        "🔍 Filters",
        expanded=True,
    ):

        f1, f2, f3, f4 = st.columns(4)

        with f1:

            patient_id_filter = st.text_input(
                "Patient ID",
                key="filter_patient_id",
                placeholder="Exact PatientID",
            )

            team_filter = st.text_input(
                "Team",
                key="filter_team",
                placeholder="e.g. 5",
            )

        with f2:

            tsp_filter = st.text_input(
                "TSP",
                key="filter_tsp",
                placeholder="e.g. SDG",
            )

            approach_filter = st.text_input(
                "Approach",
                key="filter_approach",
                placeholder="PPM / DC / Mobile Visit",
            )

        with f3:

            case_filter = st.text_input(
                "Case",
                key="filter_case",
                placeholder="e.g. TB",
            )

            visit_no_filter = st.text_input(
                "Visit No",
                key="filter_visit_no",
            )

        with f4:

            sr_no_filter = st.text_input(
                "SR No",
                key="filter_sr_no",
            )

            ward_village_filter = st.text_input(
                "Ward / Village",
                key="filter_ward_village",
            )

        d1, d2 = st.columns(2)

        with d1:

            date_from = st.date_input(
                "Date From",
                value=None,
                key="filter_date_from",
            )

        with d2:

            date_to = st.date_input(
                "Date To",
                value=None,
                key="filter_date_to",
            )

        if (
            date_from
            and date_to
            and date_from > date_to
        ):

            st.error(
                "Date From cannot be later than Date To."
            )

        # ----------------------------------------------------
        # ACTIONS
        # ----------------------------------------------------

        a1, a2, a3 = st.columns(
            [1.4, 1, 1]
        )

        with a1:

            apply_filters = st.button(
                "🔄 Load / Apply Filters",
                type="primary",
                use_container_width=True,
            )

        with a2:

            reset_filters = st.button(
                "↺ Reset Filters",
                use_container_width=True,
            )

        with a3:

            if st.button(
                "⟳ Refresh",
                use_container_width=True,
            ):

                st.session_state.editor_query_version += 1
                st.rerun()

    # --------------------------------------------------------
    # RESET FILTERS
    # --------------------------------------------------------

    if reset_filters:

        for key in [
            "filter_patient_id",
            "filter_team",
            "filter_tsp",
            "filter_approach",
            "filter_case",
            "filter_visit_no",
            "filter_sr_no",
            "filter_ward_village",
        ]:

            st.session_state[key] = ""

        st.session_state.filter_date_from = None
        st.session_state.filter_date_to = None

        # IMPORTANT:
        # Pending changes are NOT cleared.
        st.session_state.editor_page = 1

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
        st.session_state.editor_query_version += 1

        st.rerun()

    # --------------------------------------------------------
    # APPLY FILTERS
    # --------------------------------------------------------

    if apply_filters:

        if (
            date_from
            and date_to
            and date_from > date_to
        ):

            st.error(
                "Please correct the date range."
            )

        else:

            st.session_state.editor_filters = {
                "patient_id": patient_id_filter.strip(),
                "team": team_filter.strip(),
                "tsp": tsp_filter.strip(),
                "approach": approach_filter.strip(),
                "case": case_filter.strip(),
                "visit_no": visit_no_filter.strip(),
                "sr_no": sr_no_filter.strip(),
                "ward_village": ward_village_filter.strip(),
                "date_from": date_from,
                "date_to": date_to,
            }

            st.session_state.editor_page = 1
            st.session_state.editor_loaded = True
            st.session_state.editor_query_version += 1

            st.rerun()

    # --------------------------------------------------------
    # DEFAULT FILTER STATE
    # --------------------------------------------------------

    if st.session_state.editor_filters is None:

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

    filters = st.session_state.editor_filters

    # --------------------------------------------------------
    # FETCH CURRENT PAGE
    # --------------------------------------------------------

    if st.session_state.editor_loaded:

        try:

            user_client = get_user_client()

            with st.spinner(
                "Loading records..."
            ):

                total_count = get_filtered_count(
                    client=user_client,
                    columns=table_columns,
                    filters=filters,
                )

                editor_page = st.session_state.editor_page

                page_size = st.session_state.editor_page_size

                editor_df = load_editor_page(
                    client=user_client,
                    columns=table_columns,
                    filters=filters,
                    page=editor_page,
                    page_size=page_size,
                )

        except Exception as exc:

            st.error(
                f"Unable to load records: {exc}"
            )

            editor_df = pd.DataFrame()
            total_count = None

        # ----------------------------------------------------
        # PAGE CALCULATION
        # ----------------------------------------------------

        if total_count is not None:

            total_pages = max(
                1,
                int(
                    np.ceil(
                        total_count
                        / page_size
                    )
                ),
            )

        else:

            total_pages = (
                max(
                    1,
                    editor_page,
                )
                if len(editor_df) >= page_size
                else editor_page
            )

        # ----------------------------------------------------
        # PAGE NAVIGATION
        # ----------------------------------------------------

        nav1, nav2, nav3, nav4, nav5 = st.columns(
            [1, 1, 2, 1, 1]
        )

        with nav1:

            previous_page = st.button(
                "← Previous",
                disabled=editor_page <= 1,
                use_container_width=True,
            )

        with nav2:

            next_disabled = (
                editor_page >= total_pages
                if total_count is not None
                else len(editor_df) < page_size
            )

            next_page = st.button(
                "Next →",
                disabled=next_disabled,
                use_container_width=True,
            )

        if previous_page:

            st.session_state.editor_page = max(
                1,
                editor_page - 1,
            )

            st.rerun()

        if next_page:

            st.session_state.editor_page = (
                editor_page + 1
            )

            st.rerun()

        with nav3:

            if total_count is not None:

                first_row = (
                    (editor_page - 1)
                    * page_size
                    + 1
                )

                last_row = min(
                    editor_page * page_size,
                    total_count,
                )

                st.caption(
                    f"Rows {first_row:,}–{last_row:,} "
                    f"of {total_count:,}"
                )

            else:

                st.caption(
                    f"Page {editor_page}"
                )

        with nav4:

            new_page = st.number_input(
                "Page",
                min_value=1,
                max_value=max(
                    1,
                    total_pages,
                ),
                value=editor_page,
                step=1,
                label_visibility="collapsed",
            )

        with nav5:

            page_sizes = [
                100,
                300,
                500,
                1000,
            ]

            selected_page_size = st.selectbox(
                "Rows",
                page_sizes,
                index=page_sizes.index(
                    st.session_state.editor_page_size
                )
                if st.session_state.editor_page_size
                in page_sizes
                else 1,
                label_visibility="collapsed",
            )

        if new_page != editor_page:

            st.session_state.editor_page = int(
                new_page
            )

            st.rerun()

        if selected_page_size != st.session_state.editor_page_size:

            st.session_state.editor_page_size = (
                selected_page_size
            )

            st.session_state.editor_page = 1

            st.rerun()

        # ----------------------------------------------------
        # EDITOR
        # ----------------------------------------------------

        if editor_df.empty:

            st.info(
                "No records found for the selected filters."
            )

        else:

            # Keep an immutable snapshot of the currently
            # displayed page.
            original_page_df = editor_df.copy()

            st.caption(
                "Edit cells directly. Changes remain pending "
                "until you press Sync."
            )

            grid_options = create_grid_options(
                df=editor_df,
                primary_col=primary_col,
                editable=can_edit,
                selected_ids=set(),
            )

            grid_result = AgGrid(
                editor_df,
                gridOptions=grid_options,
                data_return_mode=DataReturnMode.AS_INPUT,
                update_mode=(
                    GridUpdateMode.VALUE_CHANGED
                    | GridUpdateMode.SELECTION_CHANGED
                ),
                fit_columns_on_grid_load=False,
                allow_unsafe_jscode=True,
                enable_enterprise_modules=False,
                height=650,
                theme="streamlit",
                key=(
                    f"editor_grid_"
                    f"{st.session_state.editor_query_version}_"
                    f"{editor_page}_"
                    f"{st.session_state.editor_page_size}"
                ),
            )

            edited_page_df = grid_result.get(
                "data",
                editor_df,
            )

            # ------------------------------------------------
            # Capture edits
            # ------------------------------------------------

            if can_edit:

                capture_grid_changes(
                    original_df=original_page_df,
                    edited_df=edited_page_df,
                )

            # ------------------------------------------------
            # Selected rows
            # ------------------------------------------------

            selected_rows = grid_result.get(
                "selected_rows",
                [],
            )

            if selected_rows is None:
                selected_rows = []

            selected_ids = []

            for row in selected_rows:

                selected_id = clean_value(
                    row.get(primary_col)
                    if primary_col
                    else None
                )

                if selected_id is not None:
                    selected_ids.append(
                        selected_id
                    )

            # ------------------------------------------------
            # Delete selected
            # ------------------------------------------------

            if can_delete:

                if st.button(
                    f"🗑️ Delete Selected "
                    f"({len(selected_ids)})",
                    disabled=not selected_ids,
                    type="secondary",
                ):

                    for selected_id in selected_ids:
                        add_pending_delete(
                            selected_id
                        )

                    st.success(
                        f"{len(selected_ids)} record(s) "
                        "marked for deletion."
                    )

                    st.rerun()

    # ========================================================
    # ADD NEW ROW
    # ========================================================

    if can_add:

        st.divider()

        with st.expander(
            "➕ Add New Record",
            expanded=False,
        ):

            st.caption(
                "Enter values for a new record. "
                "The row is not written to Supabase until Sync."
            )

            # Use all table columns, but avoid automatic fields.
            editable_insert_columns = [
                column
                for column in table_columns
                if column.lower()
                not in {
                    "updated_at",
                    "created_at",
                }
            ]

            if primary_col in editable_insert_columns:

                st.caption(
                    f"{primary_col} should normally be entered "
                    "if it is not database-generated."
                )

            insert_values = {}

            # 3-column layout makes a large schema more compact.
            insert_columns = st.columns(3)

            for index, column in enumerate(
                editable_insert_columns
            ):

                with insert_columns[
                    index % 3
                ]:

                    insert_values[column] = st.text_input(
                        column,
                        key=f"new_record_{column}",
                    )

            if st.button(
                "Add to Pending Inserts",
                type="primary",
                use_container_width=True,
            ):

                record = {}

                for column, value in insert_values.items():

                    if (
                        value is not None
                        and str(value).strip() != ""
                    ):

                        record[column] = value

                if not record:

                    st.warning(
                        "Please enter at least one value."
                    )

                else:

                    add_pending_insert(
                        record
                    )

                    # Clear widgets for next new row.
                    for column in editable_insert_columns:

                        key = f"new_record_{column}"

                        if key in st.session_state:
                            st.session_state[key] = ""

                    st.success(
                        "New record added to pending inserts."
                    )

                    st.rerun()

    # ========================================================
    # PENDING CHANGE PREVIEW
    # ========================================================

    if pending_changes_count():

        st.divider()

        st.subheader(
            "Pending Changes"
        )

        # ----------------------------------------------------
        # Updates
        # ----------------------------------------------------

        if st.session_state.pending_updates:

            st.markdown(
                f"**UPDATE — "
                f"{len(st.session_state.pending_updates):,} record(s)**"
            )

            update_preview = []

            for primary_id, changes in (
                st.session_state.pending_updates.items()
            ):

                row = {
                    "Action": "UPDATE",
                    primary_col or "PatientID": primary_id,
                    "Changes": "; ".join(
                        f"{key}={value}"
                        for key, value in changes.items()
                    ),
                }

                update_preview.append(
                    row
                )

            st.dataframe(
                pd.DataFrame(update_preview),
                use_container_width=True,
                hide_index=True,
            )

        # ----------------------------------------------------
        # Inserts
        # ----------------------------------------------------

        if st.session_state.pending_inserts:

            st.markdown(
                f"**INSERT — "
                f"{len(st.session_state.pending_inserts):,} record(s)**"
            )

            insert_preview = pd.DataFrame(
                st.session_state.pending_inserts
            )

            st.dataframe(
                insert_preview,
                use_container_width=True,
                hide_index=True,
            )

        # ----------------------------------------------------
        # Deletes
        # ----------------------------------------------------

        if st.session_state.pending_deletes:

            st.markdown(
                f"**DELETE — "
                f"{len(st.session_state.pending_deletes):,} record(s)**"
            )

            delete_preview = pd.DataFrame(
                {
                    primary_col or "PatientID":
                        list(
                            st.session_state.pending_deletes
                        )
                }
            )

            st.dataframe(
                delete_preview,
                use_container_width=True,
                hide_index=True,
            )

        # ----------------------------------------------------
        # SYNC
        # ----------------------------------------------------

        sync_col1, sync_col2 = st.columns(
            [2, 1]
        )

        with sync_col1:

            if st.button(
                f"💾 Sync All Changes ({total_pending})",
                type="primary",
                use_container_width=True,
            ):

                with st.spinner(
                    "Synchronizing changes..."
                ):

                    try:

                        (
                            success_count,
                            attempted_count,
                            errors,
                        ) = sync_pending_changes()

                        if errors:

                            st.warning(
                                f"{success_count} operation(s) "
                                "completed, but some operations failed."
                            )

                            for error in errors:
                                st.error(error)

                        else:

                            st.success(
                                f"{success_count} operation(s) "
                                "synchronized successfully."
                            )

                        if success_count:

                            st.session_state.editor_query_version += 1

                            st.rerun()

                    except Exception as exc:

                        st.error(
                            f"Synchronization failed: {exc}"
                        )

        with sync_col2:

            if st.button(
                "Discard",
                use_container_width=True,
            ):

                clear_pending_changes()

                st.rerun()


# ============================================================
# EXPLORER TAB
# ============================================================

with explorer_tab:

    st.subheader(
        "🔎 Database Explorer"
    )

    st.caption(
        "Explorer uses server-side filtering and pagination. "
        "It does not load the complete table into the browser."
    )

    # --------------------------------------------------------
    # Explorer filters
    # --------------------------------------------------------

    with st.expander(
        "Explorer Filters",
        expanded=True,
    ):

        e1, e2, e3, e4 = st.columns(4)

        with e1:

            explorer_keyword = st.text_input(
                "Keyword",
                placeholder="Search PatientID or other fields",
                key="explorer_keyword",
            )

        with e2:

            explorer_column = st.selectbox(
                "Search Column",
                [
                    "All columns",
                    *table_columns,
                ],
                key="explorer_column",
            )

        with e3:

            explorer_date_from = st.date_input(
                "Date From",
                value=None,
                key="explorer_date_from",
            )

        with e4:

            explorer_date_to = st.date_input(
                "Date To",
                value=None,
                key="explorer_date_to",
            )

        if st.button(
            "🔎 Search",
            type="primary",
            use_container_width=True,
        ):

            st.session_state.explorer_query_version += 1
            st.rerun()

    # --------------------------------------------------------
    # Explorer query
    # --------------------------------------------------------

    explorer_client = get_user_client()

    explorer_query = (
        explorer_client
        .table(TABLE_NAME)
        .select("*")
    )

    # Date filters
    if date_col:

        if explorer_date_from:

            explorer_query = explorer_query.gte(
                date_col,
                explorer_date_from.isoformat(),
            )

        if explorer_date_to:

            explorer_query = explorer_query.lt(
                date_col,
                (
                    explorer_date_to
                    + timedelta(days=1)
                ).isoformat(),
            )

    # Keyword
    if explorer_keyword.strip():

        keyword = (
            explorer_keyword
            .strip()
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

        if (
            explorer_column != "All columns"
            and explorer_column in table_columns
        ):

            explorer_query = explorer_query.ilike(
                explorer_column,
                f"%{keyword}%",
            )

        else:

            # Search selected practical columns instead of
            # generating a very large OR expression across
            # all ~150 columns.
            searchable_columns = [
                column
                for column in [
                    primary_col,
                    resolve_column(
                        table_columns,
                        "tsp",
                        "Tsp",
                        "TSP",
                    ),
                    resolve_column(
                        table_columns,
                        "approach",
                        "Approach",
                    ),
                    resolve_column(
                        table_columns,
                        "team",
                        "Team",
                    ),
                    resolve_column(
                        table_columns,
                        "case",
                        "Case",
                    ),
                    resolve_column(
                        table_columns,
                        "wardvillage",
                        "WardVillage",
                    ),
                ]
                if column
            ]

            if searchable_columns:

                or_conditions = ",".join(
                    f"{column}.ilike.%{keyword}%"
                    for column in searchable_columns
                )

                explorer_query = explorer_query.or_(
                    or_conditions
                )

    if primary_col:

        explorer_query = explorer_query.order(
            primary_col,
            desc=True,
        )

    # --------------------------------------------------------
    # Explorer display query
    # --------------------------------------------------------

    try:

        explorer_response = (
            explorer_query
            .range(
                0,
                EXPLORER_DISPLAY_LIMIT - 1,
            )
            .execute()
        )

        explorer_df = pd.DataFrame(
            explorer_response.data or []
        )

    except Exception as exc:

        st.error(
            f"Explorer query failed: {exc}"
        )

        explorer_df = pd.DataFrame()

    # --------------------------------------------------------
    # Display
    # --------------------------------------------------------

    if explorer_df.empty:

        st.info(
            "No records found."
        )

    else:

        st.success(
            f"Showing up to "
            f"{EXPLORER_DISPLAY_LIMIT:,} matching records."
        )

        st.dataframe(
            explorer_df,
            use_container_width=True,
            hide_index=True,
            height=650,
        )

        # ----------------------------------------------------
        # CSV EXPORT
        # ----------------------------------------------------

        st.divider()

        st.subheader(
            "CSV Export"
        )

        st.caption(
            "Export uses the current Explorer filters. "
            "Only the export operation retrieves all matching rows."
        )

        if st.button(
            "⬇️ Prepare CSV Export",
            use_container_width=True,
        ):

            try:

                rows = []

                start = 0

                while True:

                    end = (
                        start
                        + CSV_BATCH_SIZE
                        - 1
                    )

                    response = (
                        explorer_query
                        .range(
                            start,
                            end,
                        )
                        .execute()
                    )

                    batch = response.data or []

                    if not batch:
                        break

                    rows.extend(
                        batch
                    )

                    if len(batch) < CSV_BATCH_SIZE:
                        break

                    start += CSV_BATCH_SIZE

                export_df = pd.DataFrame(
                    rows
                )

                csv_data = export_df.to_csv(
                    index=False
                ).encode(
                    "utf-8-sig"
                )

                st.download_button(
                    label=(
                        f"⬇️ Download CSV "
                        f"({len(export_df):,} rows)"
                    ),
                    data=csv_data,
                    file_name=(
                        f"{TABLE_NAME}_export_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                )

            except Exception as exc:

                st.error(
                    f"CSV export failed: {exc}"
                )


# ============================================================
# FOOTER
# ============================================================

st.divider()

footer_col1, footer_col2 = st.columns(2)

with footer_col1:

    st.caption(
        f"Table: `{TABLE_NAME}`"
    )

with footer_col2:

    st.caption(
        f"Authenticated user: "
        f"{current_email or 'Unknown'}"
    )