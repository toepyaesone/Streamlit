# database.py

import os
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
USER_ROLE_TABLE = "user_roles"

BATCH_SIZE = 1000
EDITOR_PAGE_SIZE_DEFAULT = 300
EXPLORER_DISPLAY_LIMIT = 1000


# ============================================================
# SESSION DEFAULTS
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
    "editor_source_key": None,

    "editor_loaded": False,
    "editor_page": 1,
    "editor_page_size": EDITOR_PAGE_SIZE_DEFAULT,

    "editor_filters": {
        "patient_id": [],
        "team": [],
        "tsp": [],
        "approach": [],
        "case": [],
        "visit_no": [],
        "sr_no": [],
        "ward_village": [],
        "date_from": None,
        "date_to": None,
    },

    "editor_filter_options": {},
    "editor_filter_options_loaded": False,

    "explorer_search": "",
    "explorer_columns": [],
    "explorer_loaded": False,
}


for key, default_value in DEFAULTS.items():
    if key not in st.session_state:
        if isinstance(default_value, dict):
            st.session_state[key] = default_value.copy()
        elif isinstance(default_value, list):
            st.session_state[key] = default_value.copy()
        elif isinstance(default_value, set):
            st.session_state[key] = default_value.copy()
        else:
            st.session_state[key] = default_value


# ============================================================
# GENERAL UTILITIES
# ============================================================

def is_null_like(value: Any) -> bool:
    if value is None:
        return True

    if value is pd.NA:
        return True

    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def clean_value(value: Any) -> Any:
    """
    Convert Pandas / NumPy / Decimal / datetime values into
    Supabase/PostgREST-safe Python values.
    """

    if is_null_like(value):
        return None

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        if np.isnan(value) or np.isinf(value):
            return None
        return float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, np.ndarray):
        return [clean_value(x) for x in value.tolist()]

    if isinstance(value, list):
        return [clean_value(x) for x in value]

    if isinstance(value, tuple):
        return [clean_value(x) for x in value]

    if isinstance(value, dict):
        return {
            str(k): clean_value(v)
            for k, v in value.items()
        }

    return value


def make_json_safe(data: Any) -> Any:
    return clean_value(data)


def values_equal(left: Any, right: Any) -> bool:
    left = clean_value(left)
    right = clean_value(right)

    if left is None and right is None:
        return True

    return left == right


def resolve_column(columns, *candidates):
    """
    Return the actual database column name matching one of
    the supplied candidate names.
    """

    if not columns:
        return None

    normalized = {
        str(column).strip().lower(): column
        for column in columns
    }

    for candidate in candidates:
        if not candidate:
            continue

        actual = normalized.get(
            str(candidate).strip().lower()
        )

        if actual:
            return actual

    return None


# ============================================================
# SUPABASE CONFIGURATION
# ============================================================

def get_secret(name: str, default=None):
    """
    Read a value from Streamlit secrets first, then environment.
    """

    try:
        value = st.secrets.get(name)
        if value is not None:
            return value
    except Exception:
        pass

    return os.getenv(name, default)


def get_supabase_config():
    url = get_secret(
        "SUPABASE_URL_ygntbpro",
        get_secret("SUPABASE_URL"),
    )

    key = get_secret(
        "SUPABASE_KEY_ygntbpro",
        get_secret("SUPABASE_KEY"),
    )

    if not url:
        raise RuntimeError(
            "Supabase URL is not configured."
        )

    if not key:
        raise RuntimeError(
            "Supabase key is not configured."
        )

    return url, key


@st.cache_resource
def get_base_client() -> Client:
    url, key = get_supabase_config()
    return create_client(url, key)


def get_user_client() -> Client:
    """
    Create an authenticated Supabase client using the current
    user's access/refresh tokens.

    This is the client used for database operations so that
    Supabase RLS policies apply to the logged-in user.
    """

    session = st.session_state.get("session")

    if not session:
        raise RuntimeError(
            "No authenticated Supabase session."
        )

    url, key = get_supabase_config()

    client = create_client(url, key)

    access_token = getattr(
        session,
        "access_token",
        None,
    )

    refresh_token = getattr(
        session,
        "refresh_token",
        None,
    )

    if access_token:
        try:
            client.auth.set_session(
                access_token,
                refresh_token,
            )
        except Exception:
            try:
                client.postgrest.auth(access_token)
            except Exception as exc:
                raise RuntimeError(
                    f"Unable to set authenticated Supabase session: {exc}"
                )

    return client


# ============================================================
# LOGIN / LOGOUT
# ============================================================

def login_user(email: str, password: str):
    """
    Authenticate user and obtain role from user_roles using
    the authenticated Supabase user's UUID.

    IMPORTANT:
    Role lookup is by user_id, NOT email.
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

        user_id = response.session.user.id

        role_result = (
            user_client
            .table(USER_ROLE_TABLE)
            .select("role")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        role = "viewer"

        if role_result.data:
            role = (
                role_result.data[0]
                .get("role", "viewer")
                or "viewer"
            )

        role = str(role).strip().lower()

        if role not in {"viewer", "editor", "admin"}:
            role = "viewer"

        st.session_state.user_role = role

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

    st.title("YgnTBPro Database")

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
            st.error(
                "Please enter both email and password."
            )
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
# AUTHENTICATED USER
# ============================================================

user_role = (
    st.session_state.user_role
    or "viewer"
)

user_role = str(user_role).lower()

can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"
can_delete = user_role == "admin"


# ============================================================
# USER CLIENT
# ============================================================

try:
    client = get_user_client()
except Exception as exc:
    st.error(
        f"Unable to create authenticated database client: {exc}"
    )
    st.stop()


# ============================================================
# HEADER
# ============================================================

header_col1, header_col2, header_col3 = st.columns(
    [5, 2, 1]
)

with header_col1:
    st.title("YgnTBPro Database")

with header_col2:
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
# TABLE SCHEMA
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def get_table_columns(_url: str, _table_name: str):
    """
    Get one row to determine the available columns.

    Cache only schema information, not authenticated data.
    """

    base_client = get_base_client()

    response = (
        base_client
        .table(_table_name)
        .select("*")
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return []

    return list(rows[0].keys())


try:
    supabase_url, _ = get_supabase_config()

    columns = get_table_columns(
        supabase_url,
        TABLE_NAME,
    )

except Exception as exc:
    st.error(
        f"Unable to read table schema: {exc}"
    )
    st.stop()


if not columns:
    st.warning(
        f"No columns were returned from `{TABLE_NAME}`."
    )
    st.stop()


# ============================================================
# RESOLVE DATABASE COLUMNS
# ============================================================

db_columns = {
    "primary_key": resolve_column(
        columns,
        "PatientID",
        "patientid",
        "patient_id",
    ),

    "date": resolve_column(
        columns,
        "Date",
        "date",
    ),

    "visit_no": resolve_column(
        columns,
        "Visit_no",
        "visit_no",
        "VisitNo",
        "visitno",
    ),

    "sr_no": resolve_column(
        columns,
        "Sr_No",
        "sr_no",
        "SR_No",
        "SrNo",
        "srno",
    ),

    "team": resolve_column(
        columns,
        "Team",
        "team",
    ),

    "tsp": resolve_column(
        columns,
        "TSP",
        "Tsp",
        "tsp",
    ),

    "approach": resolve_column(
        columns,
        "Approach",
        "approach",
    ),

    "case": resolve_column(
        columns,
        "Case",
        "case",
    ),

    "ward_village": resolve_column(
        columns,
        "WardVillage",
        "Ward_Village",
        "ward_village",
        "wardvillage",
    ),

    "updated_at": resolve_column(
        columns,
        "updated_at",
        "Updated_at",
        "updatedAt",
    ),
}


PRIMARY_KEY = db_columns["primary_key"]


if not PRIMARY_KEY:
    st.error(
        "PatientID primary-key column could not be identified."
    )
    st.stop()


# ============================================================
# BATCH DATA LOADING
# ============================================================

def fetch_all_rows(
    table_name: str,
    supabase_client: Client,
    select_columns: str = "*",
    order_column: str | None = None,
    descending: bool = False,
):
    """
    Retrieve all rows using PostgREST range pagination.

    BATCH_SIZE is only the request size; it is NOT a maximum
    number of records.
    """

    rows = []
    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        query = (
            supabase_client
            .table(table_name)
            .select(select_columns)
        )

        if order_column:
            query = query.order(
                order_column,
                desc=descending,
            )

        response = (
            query
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


def fetch_all_from_query(
    query_builder,
    batch_size=BATCH_SIZE,
):
    """
    Paginate an already-filtered query.
    """

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


# ============================================================
# UNIQUE FILTER VALUES
# ============================================================

def get_unique_filter_values(
    supabase_client: Client,
    filter_columns: dict,
):
    """
    Retrieve all required filter columns together.

    This is considerably more efficient than making one
    full-table query per filter.
    """

    actual_columns = [
        column
        for column in filter_columns.values()
        if column
    ]

    actual_columns = list(
        dict.fromkeys(actual_columns)
    )

    if not actual_columns:
        return {}

    select_columns = ",".join(
        actual_columns
    )

    unique_values = {
        name: set()
        for name in filter_columns
    }

    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        response = (
            supabase_client
            .table(TABLE_NAME)
            .select(select_columns)
            .range(start, end)
            .execute()
        )

        batch = response.data or []

        if not batch:
            break

        for row in batch:

            for filter_name, column in filter_columns.items():

                if not column:
                    continue

                value = row.get(column)

                if is_null_like(value):
                    continue

                value = clean_value(value)

                if value is None:
                    continue

                try:
                    unique_values[filter_name].add(
                        value
                    )
                except TypeError:
                    unique_values[filter_name].add(
                        str(value)
                    )

        if len(batch) < BATCH_SIZE:
            break

        start += BATCH_SIZE

    result = {}

    for filter_name, values in unique_values.items():

        try:
            result[filter_name] = sorted(
                values
            )
        except Exception:
            result[filter_name] = sorted(
                values,
                key=lambda x: str(x),
            )

    return result


def load_editor_filter_options():

    filter_columns = {
        "patient_id": db_columns.get(
            "primary_key"
        ),
        "team": db_columns.get(
            "team"
        ),
        "tsp": db_columns.get(
            "tsp"
        ),
        "approach": db_columns.get(
            "approach"
        ),
        "case": db_columns.get(
            "case"
        ),
        "visit_no": db_columns.get(
            "visit_no"
        ),
        "sr_no": db_columns.get(
            "sr_no"
        ),
        "ward_village": db_columns.get(
            "ward_village"
        ),
    }

    with st.spinner(
        "Loading unique filter values..."
    ):

        options = get_unique_filter_values(
            client,
            filter_columns,
        )

    st.session_state.editor_filter_options = options
    st.session_state.editor_filter_options_loaded = True


# ============================================================
# EDITOR QUERY
# ============================================================

def build_editor_query(
    supabase_client,
    filters,
):

    query = (
        supabase_client
        .table(TABLE_NAME)
        .select("*")
    )

    filter_mapping = {
        "patient_id": db_columns.get(
            "primary_key"
        ),
        "team": db_columns.get(
            "team"
        ),
        "tsp": db_columns.get(
            "tsp"
        ),
        "approach": db_columns.get(
            "approach"
        ),
        "case": db_columns.get(
            "case"
        ),
        "visit_no": db_columns.get(
            "visit_no"
        ),
        "sr_no": db_columns.get(
            "sr_no"
        ),
        "ward_village": db_columns.get(
            "ward_village"
        ),
    }

    for filter_name, column in filter_mapping.items():

        if not column:
            continue

        selected_values = (
            filters.get(filter_name, [])
        )

        if not selected_values:
            continue

        cleaned_values = [
            clean_value(value)
            for value in selected_values
        ]

        cleaned_values = [
            value
            for value in cleaned_values
            if value is not None
        ]

        if cleaned_values:
            query = query.in_(
                column,
                cleaned_values,
            )

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

            next_day = (
                pd.Timestamp(date_to)
                + pd.Timedelta(days=1)
            )

            query = query.lt(
                date_column,
                next_day.date().isoformat(),
            )

    if PRIMARY_KEY:
        query = query.order(
            PRIMARY_KEY,
            desc=True,
        )

    return query


def load_editor_page(
    supabase_client,
    filters,
    page,
    page_size,
):
    """
    Load page_size + 1 rows.

    Because PostgREST range() is inclusive, the end index is
    start + page_size.

    We deliberately do not use `.select(..., count="exact")`
    because the installed supabase-py/PostgREST version may
    not support that keyword.
    """

    query = build_editor_query(
        supabase_client,
        filters,
    )

    start = max(
        0,
        (page - 1) * page_size,
    )

    end = start + page_size

    response = (
        query
        .range(start, end)
        .execute()
    )

    rows = response.data or []

    has_next_page = (
        len(rows) > page_size
    )

    if has_next_page:
        rows = rows[:page_size]

    df = pd.DataFrame(rows)

    return (
        df,
        has_next_page,
    )


# ============================================================
# PENDING CHANGES
# ============================================================

def add_pending_update(
    primary_id,
    changes,
):
    if primary_id is None:
        return

    primary_id = clean_value(
        primary_id
    )

    if not changes:
        return

    existing = st.session_state.pending_updates.get(
        primary_id,
        {},
    )

    existing.update(
        {
            column: clean_value(value)
            for column, value in changes.items()
        }
    )

    st.session_state.pending_updates[
        primary_id
    ] = existing


def remove_pending_update(
    primary_id,
):
    st.session_state.pending_updates.pop(
        clean_value(primary_id),
        None,
    )


def capture_grid_changes(
    source_df: pd.DataFrame,
    edited_df: pd.DataFrame,
):
    """
    Compare the currently displayed grid against the original
    loaded page.

    IMPORTANT:
    Missing rows are NOT treated as deleted. Deletion must be
    explicit through Delete Selected.
    """

    if source_df is None:
        return

    if edited_df is None:
        return

    if PRIMARY_KEY not in source_df.columns:
        return

    if PRIMARY_KEY not in edited_df.columns:
        return

    source_by_id = {}

    for _, row in source_df.iterrows():

        primary_id = row.get(
            PRIMARY_KEY
        )

        if is_null_like(primary_id):
            continue

        source_by_id[
            clean_value(primary_id)
        ] = row.to_dict()

    for _, row in edited_df.iterrows():

        primary_id = row.get(
            PRIMARY_KEY
        )

        if is_null_like(primary_id):
            continue

        primary_id = clean_value(
            primary_id
        )

        if primary_id not in source_by_id:
            continue

        original = source_by_id[
            primary_id
        ]

        changes = {}

        for column in edited_df.columns:

            if column == PRIMARY_KEY:
                continue

            if (
                db_columns.get("updated_at")
                and column
                == db_columns["updated_at"]
            ):
                continue

            old_value = original.get(
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


def pending_changes_count():

    update_count = len(
        st.session_state.pending_updates
    )

    insert_count = len(
        st.session_state.pending_inserts
    )

    delete_count = len(
        st.session_state.pending_deletes
    )

    return (
        update_count
        + insert_count
        + delete_count
    )


def clear_pending_changes():

    st.session_state.pending_updates = {}
    st.session_state.pending_inserts = []
    st.session_state.pending_deletes = set()


# ============================================================
# APPLY PENDING CHANGES TO CURRENT DATAFRAME
# ============================================================

def apply_pending_changes_to_df(
    df: pd.DataFrame,
):

    if df is None:
        return df

    if df.empty:
        return df

    result = df.copy()

    if PRIMARY_KEY not in result.columns:
        return result

    for index, row in result.iterrows():

        primary_id = row.get(
            PRIMARY_KEY
        )

        if is_null_like(primary_id):
            continue

        primary_id = clean_value(
            primary_id
        )

        changes = (
            st.session_state.pending_updates.get(
                primary_id,
                {},
            )
        )

        for column, value in changes.items():

            if column in result.columns:
                result.at[
                    index,
                    column,
                ] = value

    return result


# ============================================================
# EXPLICIT DELETE SELECTED
# ============================================================

def delete_selected_rows(
    selected_rows,
):

    if not selected_rows:
        return 0

    deleted_count = 0

    for row in selected_rows:

        primary_id = row.get(
            PRIMARY_KEY
        )

        if is_null_like(primary_id):
            continue

        primary_id = clean_value(
            primary_id
        )

        # If it is a pending insert, remove the insert.
        insert_removed = False

        remaining_inserts = []

        for insert in st.session_state.pending_inserts:

            insert_id = clean_value(
                insert.get(PRIMARY_KEY)
            )

            if insert_id == primary_id:
                insert_removed = True
                continue

            remaining_inserts.append(
                insert
            )

        st.session_state.pending_inserts = (
            remaining_inserts
        )

        if insert_removed:
            deleted_count += 1
            continue

        # Otherwise queue the existing DB row for deletion.
        st.session_state.pending_deletes.add(
            primary_id
        )

        remove_pending_update(
            primary_id
        )

        deleted_count += 1

    return deleted_count


# ============================================================
# ADD NEW RECORD
# ============================================================

def add_new_record(
    new_record,
):

    cleaned = {
        column: clean_value(value)
        for column, value in new_record.items()
    }

    primary_id = cleaned.get(
        PRIMARY_KEY
    )

    if (
        primary_id is not None
        and str(primary_id).strip() != ""
    ):

        existing_ids = set(
            st.session_state.pending_updates.keys()
        )

        existing_ids.update(
            clean_value(
                row.get(PRIMARY_KEY)
            )
            for row
            in st.session_state.pending_inserts
            if row.get(PRIMARY_KEY) is not None
        )

        if primary_id in existing_ids:
            return False, (
                "A record with this PatientID "
                "already exists in pending changes."
            )

    st.session_state.pending_inserts.append(
        cleaned
    )

    return True, "New record added to pending changes."


# ============================================================
# SYNC TO SUPABASE
# ============================================================

def sync_pending_changes(
    supabase_client,
):

    errors = []

    successful_updates = []
    successful_inserts = []
    successful_deletes = []

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for primary_id, changes in list(
        st.session_state.pending_updates.items()
    ):

        if primary_id in (
            st.session_state.pending_deletes
        ):
            continue

        try:

            payload = make_json_safe(
                changes
            )

            (
                supabase_client
                .table(TABLE_NAME)
                .update(payload)
                .eq(
                    PRIMARY_KEY,
                    make_json_safe(primary_id),
                )
                .execute()
            )

            successful_updates.append(
                primary_id
            )

        except Exception as exc:

            errors.append(
                f"UPDATE {primary_id}: {exc}"
            )

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    for insert_record in list(
        st.session_state.pending_inserts
    ):

        try:

            payload = make_json_safe(
                insert_record
            )

            (
                supabase_client
                .table(TABLE_NAME)
                .insert(payload)
                .execute()
            )

            successful_inserts.append(
                id(insert_record)
            )

        except Exception as exc:

            patient_id = insert_record.get(
                PRIMARY_KEY
            )

            errors.append(
                f"INSERT {patient_id}: {exc}"
            )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    for primary_id in list(
        st.session_state.pending_deletes
    ):

        try:

            (
                supabase_client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    PRIMARY_KEY,
                    make_json_safe(primary_id),
                )
                .execute()
            )

            successful_deletes.append(
                primary_id
            )

        except Exception as exc:

            errors.append(
                f"DELETE {primary_id}: {exc}"
            )

    # --------------------------------------------------------
    # REMOVE ONLY SUCCESSFUL OPERATIONS
    # --------------------------------------------------------

    for primary_id in successful_updates:
        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    if successful_inserts:

        successful_insert_ids = set(
            successful_inserts
        )

        st.session_state.pending_inserts = [
            record
            for record
            in st.session_state.pending_inserts
            if id(record)
            not in successful_insert_ids
        ]

    for primary_id in successful_deletes:
        st.session_state.pending_deletes.discard(
            primary_id
        )

    return (
        successful_updates,
        successful_inserts,
        successful_deletes,
        errors,
    )


# ============================================================
# RESET EDITOR FILTERS
# ============================================================

def reset_editor_filters():

    st.session_state.editor_filters = {
        "patient_id": [],
        "team": [],
        "tsp": [],
        "approach": [],
        "case": [],
        "visit_no": [],
        "sr_no": [],
        "ward_village": [],
        "date_from": None,
        "date_to": None,
    }

    # Reset widget states.
    st.session_state["filter_patient_id"] = []
    st.session_state["filter_team"] = []
    st.session_state["filter_tsp"] = []
    st.session_state["filter_approach"] = []
    st.session_state["filter_case"] = []
    st.session_state["filter_visit_no"] = []
    st.session_state["filter_sr_no"] = []
    st.session_state["filter_ward_village"] = []

    st.session_state["filter_date_from"] = None
    st.session_state["filter_date_to"] = None

    st.session_state.editor_page = 1
    st.session_state.editor_loaded = True
    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.filter_version += 1
    st.session_state.grid_version += 1


# ============================================================
# ADD RECORD FORM
# ============================================================

def show_add_record_form():

    st.subheader("Add New Record")

    new_record = {}

    editable_columns = [
        column
        for column in columns
        if column != db_columns.get(
            "updated_at"
        )
    ]

    form_columns = st.columns(3)

    for index, column in enumerate(
        editable_columns
    ):

        with form_columns[
            index % 3
        ]:

            new_record[column] = st.text_input(
                column,
                key=f"new_record_{column}",
            )

    if st.button(
        "Add to Pending Changes",
        type="primary",
        use_container_width=True,
    ):

        cleaned_record = {}

        for column in editable_columns:

            value = new_record.get(
                column
            )

            if value == "":
                value = None

            cleaned_record[column] = value

        success, message = add_new_record(
            cleaned_record
        )

        if success:
            st.success(message)
            st.rerun()
        else:
            st.error(message)


# ============================================================
# DATABASE EDITOR
# ============================================================

st.divider()

st.header("Database Editor")

if not can_edit:

    st.info(
        "Your current role is Viewer. "
        "You can view records but cannot edit the database."
    )


# ============================================================
# FILTER OPTION LOADING
# ============================================================

filter_col1, filter_col2 = st.columns(
    [5, 1]
)

with filter_col1:

    if not st.session_state.editor_filter_options_loaded:

        if st.button(
            "Load Filter Lists",
            use_container_width=True,
        ):
            load_editor_filter_options()
            st.rerun()

with filter_col2:

    if st.button(
        "Refresh Lists",
        use_container_width=True,
    ):
        st.session_state.editor_filter_options_loaded = False
        st.session_state.editor_filter_options = {}
        load_editor_filter_options()
        st.rerun()


if not st.session_state.editor_filter_options_loaded:

    st.info(
        "Click **Load Filter Lists** to load unique values "
        "for the multi-select filters."
    )

else:

    options = (
        st.session_state.editor_filter_options
    )

    # --------------------------------------------------------
    # MULTISELECT FILTERS
    # --------------------------------------------------------

    row1 = st.columns(4)

    with row1[0]:

        patient_id_values = st.multiselect(
            "Patient ID",
            options.get(
                "patient_id",
                [],
            ),
            key="filter_patient_id",
            help=(
                "Select one or more Patient IDs. "
                "You can type to search the list."
            ),
        )

    with row1[1]:

        team_values = st.multiselect(
            "Team",
            options.get(
                "team",
                [],
            ),
            key="filter_team",
        )

    with row1[2]:

        tsp_values = st.multiselect(
            "TSP",
            options.get(
                "tsp",
                [],
            ),
            key="filter_tsp",
        )

    with row1[3]:

        approach_values = st.multiselect(
            "Approach",
            options.get(
                "approach",
                [],
            ),
            key="filter_approach",
        )

    row2 = st.columns(4)

    with row2[0]:

        case_values = st.multiselect(
            "Case",
            options.get(
                "case",
                [],
            ),
            key="filter_case",
        )

    with row2[1]:

        visit_no_values = st.multiselect(
            "Visit No",
            options.get(
                "visit_no",
                [],
            ),
            key="filter_visit_no",
        )

    with row2[2]:

        sr_no_values = st.multiselect(
            "SR No",
            options.get(
                "sr_no",
                [],
            ),
            key="filter_sr_no",
        )

    with row2[3]:

        ward_village_values = st.multiselect(
            "Ward / Village",
            options.get(
                "ward_village",
                [],
            ),
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
            [100, 300, 500, 1000],
            index=1,
            key="editor_page_size",
        )

    # --------------------------------------------------------
    # FILTER ACTIONS
    # --------------------------------------------------------

    action1, action2 = st.columns(
        [1, 1]
    )

    with action1:

        apply_clicked = st.button(
            "Apply Filters",
            type="primary",
            use_container_width=True,
        )

    with action2:

        reset_clicked = st.button(
            "Reset Filters",
            use_container_width=True,
        )

    if apply_clicked:

        st.session_state.editor_filters = {
            "patient_id": list(
                patient_id_values
            ),
            "team": list(
                team_values
            ),
            "tsp": list(
                tsp_values
            ),
            "approach": list(
                approach_values
            ),
            "case": list(
                case_values
            ),
            "visit_no": list(
                visit_no_values
            ),
            "sr_no": list(
                sr_no_values
            ),
            "ward_village": list(
                ward_village_values
            ),
            "date_from": date_from,
            "date_to": date_to,
        }

        st.session_state.editor_page = 1
        st.session_state.editor_loaded = True

        # This only resets the display snapshot.
        # Pending changes are NOT cleared.
        st.session_state.editor_source_df = None
        st.session_state.editor_source_key = None

        st.session_state.filter_version += 1
        st.session_state.grid_version += 1

        st.rerun()

    if reset_clicked:
        reset_editor_filters()
        st.rerun()


# ============================================================
# LOAD EDITOR PAGE
# ============================================================

if st.session_state.editor_loaded:

    filters = st.session_state.editor_filters

    current_page = (
        st.session_state.editor_page
    )

    current_page_size = (
        st.session_state.editor_page_size
    )

    try:

        with st.spinner(
            "Loading database records..."
        ):

            editor_df, has_next_page = (
                load_editor_page(
                    client,
                    filters,
                    current_page,
                    current_page_size,
                )
            )

    except Exception as exc:

        st.error(
            f"Unable to load records: {exc}"
        )

        editor_df = pd.DataFrame()
        has_next_page = False


    # --------------------------------------------------------
    # APPLY PENDING UPDATES
    # --------------------------------------------------------

    editor_df = apply_pending_changes_to_df(
        editor_df
    )


    # --------------------------------------------------------
    # SOURCE SNAPSHOT
    # --------------------------------------------------------

    filter_signature = repr(
        {
            "filters": filters,
            "page": current_page,
            "page_size": current_page_size,
        }
    )

    if (
        st.session_state.editor_source_key
        != filter_signature
    ):

        # IMPORTANT:
        # Snapshot the unmodified database page before
        # applying pending updates.
        try:

            source_df, _ = load_editor_page(
                client,
                filters,
                current_page,
                current_page_size,
            )

        except Exception:
            source_df = editor_df.copy()

        st.session_state.editor_source_df = (
            source_df.copy()
        )

        st.session_state.editor_source_key = (
            filter_signature
        )


    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    pending_count = pending_changes_count()

    status1, status2, status3 = st.columns(
        3
    )

    with status1:
        st.metric(
            "Records on page",
            len(editor_df),
        )

    with status2:
        st.metric(
            "Pending changes",
            pending_count,
        )

    with status3:
        st.metric(
            "Current page",
            current_page,
        )


    # --------------------------------------------------------
    # AG GRID
    # --------------------------------------------------------

    if editor_df.empty:

        st.info(
            "No records found for the selected filters."
        )

    else:

        display_df = editor_df.copy()

        # Ensure JSON-friendly display values.
        for column in display_df.columns:

            display_df[column] = (
                display_df[column]
                .map(clean_value)
            )

        gb = GridOptionsBuilder.from_dataframe(
            display_df
        )

        gb.configure_default_column(
            editable=can_edit,
            resizable=True,
            sortable=True,
            filter=True,
            wrapText=False,
            autoHeight=False,
        )

        gb.configure_selection(
            "multiple",
            use_checkbox=True,
        )

        gb.configure_pagination(
            enabled=False
        )

        gb.configure_grid_options(
            suppressRowClickSelection=True,
            enableRangeSelection=True,
            rowSelection="multiple",
        )

        # ----------------------------------------------------
        # Disable primary key and updated_at
        # ----------------------------------------------------

        gb.configure_column(
            PRIMARY_KEY,
            editable=False,
        )

        updated_at_column = db_columns.get(
            "updated_at"
        )

        if updated_at_column:
            gb.configure_column(
                updated_at_column,
                editable=False,
            )

        grid_options = gb.build()

        # ----------------------------------------------------
        # JS to ensure selected rows are clearly identifiable
        # ----------------------------------------------------

        grid_options["getRowId"] = JsCode(
            f"""
            function(params) {{
                return String(
                    params.data["{PRIMARY_KEY}"]
                );
            }}
            """
        )

        grid_response = AgGrid(
            display_df,
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
            width="100%",
            reload_data=False,
            key=(
                f"database_grid_"
                f"{st.session_state.grid_version}_"
                f"{st.session_state.filter_version}_"
                f"{current_page}"
            ),
        )

        # ----------------------------------------------------
        # CAPTURE EDITS
        # ----------------------------------------------------

        edited_grid_df = (
            grid_response.get(
                "data",
                display_df,
            )
        )

        if isinstance(
            edited_grid_df,
            pd.DataFrame,
        ):

            source_df = (
                st.session_state.editor_source_df
            )

            if source_df is not None:

                capture_grid_changes(
                    source_df,
                    edited_grid_df,
                )


        # ----------------------------------------------------
        # SELECTED ROWS
        # ----------------------------------------------------

        selected_rows = (
            grid_response.get(
                "selected_rows",
                [],
            )
        )

        if isinstance(
            selected_rows,
            pd.DataFrame,
        ):
            selected_rows = (
                selected_rows
                .to_dict("records")
            )

        if selected_rows is None:
            selected_rows = []


        # ----------------------------------------------------
        # DELETE SELECTED
        # ----------------------------------------------------

        if can_delete:

            if st.button(
                "Delete Selected",
                type="secondary",
                disabled=not selected_rows,
                use_container_width=True,
            ):

                deleted = delete_selected_rows(
                    selected_rows
                )

                if deleted:
                    st.success(
                        f"{deleted} record(s) "
                        "added to pending deletion."
                    )

                    st.session_state.grid_version += 1
                    st.rerun()


        # ----------------------------------------------------
        # PAGINATION
        # ----------------------------------------------------

        st.divider()

        page_col1, page_col2, page_col3 = st.columns(
            [1, 2, 1]
        )

        with page_col1:

            if st.button(
                "← Previous",
                disabled=current_page <= 1,
                use_container_width=True,
            ):

                st.session_state.editor_page = (
                    max(
                        1,
                        current_page - 1,
                    )
                )

                st.session_state.editor_source_df = None
                st.session_state.editor_source_key = None
                st.session_state.grid_version += 1

                st.rerun()

        with page_col2:

            page_text = (
                f"Page {current_page}"
            )

            if has_next_page:
                page_text += "  •  More records available"

            st.markdown(
                f"<div style='text-align:center;"
                f"padding-top:8px'>{page_text}</div>",
                unsafe_allow_html=True,
            )

        with page_col3:

            if st.button(
                "Next →",
                disabled=not has_next_page,
                use_container_width=True,
            ):

                st.session_state.editor_page = (
                    current_page + 1
                )

                st.session_state.editor_source_df = None
                st.session_state.editor_source_key = None
                st.session_state.grid_version += 1

                st.rerun()


# ============================================================
# PENDING CHANGES / SYNC
# ============================================================

st.divider()

st.header("Pending Changes")

pending_updates = (
    st.session_state.pending_updates
)

pending_inserts = (
    st.session_state.pending_inserts
)

pending_deletes = (
    st.session_state.pending_deletes
)

pending_total = pending_changes_count()


if pending_total == 0:

    st.info(
        "No pending changes."
    )

else:

    update_count = len(
        pending_updates
    )

    insert_count = len(
        pending_inserts
    )

    delete_count = len(
        pending_deletes
    )

    summary1, summary2, summary3 = st.columns(
        3
    )

    with summary1:
        st.metric(
            "Updates",
            update_count,
        )

    with summary2:
        st.metric(
            "Inserts",
            insert_count,
        )

    with summary3:
        st.metric(
            "Deletes",
            delete_count,
        )


    # --------------------------------------------------------
    # UPDATE PREVIEW
    # --------------------------------------------------------

    if pending_updates:

        st.subheader("Pending Updates")

        update_preview = []

        for primary_id, changes in (
            pending_updates.items()
        ):

            for column, value in changes.items():

                update_preview.append(
                    {
                        PRIMARY_KEY: primary_id,
                        "Change Type": "UPDATE",
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


    # --------------------------------------------------------
    # INSERT PREVIEW
    # --------------------------------------------------------

    if pending_inserts:

        st.subheader("Pending Inserts")

        insert_preview = (
            pd.DataFrame(
                pending_inserts
            )
        )

        st.dataframe(
            insert_preview,
            use_container_width=True,
            hide_index=True,
        )


    # --------------------------------------------------------
    # DELETE PREVIEW
    # --------------------------------------------------------

    if pending_deletes:

        st.subheader("Pending Deletes")

        st.dataframe(
            pd.DataFrame(
                {
                    PRIMARY_KEY: list(
                        pending_deletes
                    ),
                    "Change Type": [
                        "DELETE"
                        for _ in pending_deletes
                    ],
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


    # --------------------------------------------------------
    # SYNC / DISCARD
    # --------------------------------------------------------

    sync_col, discard_col = st.columns(
        2
    )

    with sync_col:

        if st.button(
            "Sync Changes to Supabase",
            type="primary",
            use_container_width=True,
            disabled=not can_edit,
        ):

            with st.spinner(
                "Synchronizing changes..."
            ):

                (
                    successful_updates,
                    successful_inserts,
                    successful_deletes,
                    errors,
                ) = sync_pending_changes(
                    client
                )

            if successful_updates:
                st.success(
                    f"{len(successful_updates)} "
                    "update(s) synchronized."
                )

            if successful_inserts:
                st.success(
                    f"{len(successful_inserts)} "
                    "insert(s) synchronized."
                )

            if successful_deletes:
                st.success(
                    f"{len(successful_deletes)} "
                    "delete(s) synchronized."
                )

            if errors:

                st.error(
                    "Some changes could not be synchronized."
                )

                for error in errors:
                    st.write(
                        f"- {error}"
                    )

            if not errors:

                st.session_state.editor_source_df = None
                st.session_state.editor_source_key = None
                st.session_state.grid_version += 1

                st.rerun()

    with discard_col:

        if st.button(
            "Discard All Pending Changes",
            use_container_width=True,
        ):

            clear_pending_changes()

            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1

            st.success(
                "All pending changes discarded."
            )

            st.rerun()


# ============================================================
# ADMIN: ADD RECORD
# ============================================================

if can_add:

    st.divider()

    with st.expander(
        "➕ Add New Record",
        expanded=False,
    ):

        show_add_record_form()


# ============================================================
# EXPLORER
# ============================================================

st.divider()

st.header("Explorer")

st.caption(
    "Search and inspect database records. "
    "Explorer search is applied only to text-like columns, "
    "so numeric columns are not incorrectly queried with ILIKE."
)


# ============================================================
# EXPLORER COLUMN TYPE DETECTION
# ============================================================

def get_explorer_searchable_columns(
    supabase_client,
    all_columns,
):
    """
    Determine text-like columns from a representative sample.

    Numeric/date columns are excluded from ILIKE searches.
    """

    try:

        response = (
            supabase_client
            .table(TABLE_NAME)
            .select("*")
            .limit(100)
            .execute()
        )

        rows = response.data or []

        if not rows:
            return []

        sample_df = pd.DataFrame(
            rows
        )

        searchable = []

        for column in all_columns:

            if column not in sample_df.columns:
                continue

            series = sample_df[column]

            # Empty/all-null samples cannot reliably tell us
            # that a column is numeric, so skip them rather
            # than applying ILIKE blindly.
            non_null = series.dropna()

            if non_null.empty:
                continue

            if (
                pd.api.types.is_object_dtype(
                    series
                )
                or pd.api.types.is_string_dtype(
                    series
                )
            ):

                # Exclude obvious date/datetime columns.
                if pd.api.types.is_datetime64_any_dtype(
                    series
                ):
                    continue

                searchable.append(
                    column
                )

        return searchable

    except Exception:
        return []


if not st.session_state.explorer_searchable_columns:

    searchable_columns = (
        get_explorer_searchable_columns(
            client,
            columns,
        )
    )

    st.session_state.explorer_searchable_columns = (
        searchable_columns
    )

else:

    searchable_columns = (
        st.session_state.explorer_searchable_columns
    )


# ============================================================
# EXPLORER SEARCH
# ============================================================

explorer_search = st.text_input(
    "Search",
    value=st.session_state.explorer_search,
    placeholder=(
        "Enter keyword to search text columns..."
    ),
)

selected_explorer_columns = st.multiselect(
    "Searchable columns",
    searchable_columns,
    default=(
        st.session_state.explorer_columns
        if st.session_state.explorer_columns
        else searchable_columns
    ),
)


explorer_col1, explorer_col2 = st.columns(
    2
)

with explorer_col1:

    explorer_load = st.button(
        "Search Explorer",
        type="primary",
        use_container_width=True,
    )

with explorer_col2:

    explorer_clear = st.button(
        "Clear Explorer",
        use_container_width=True,
    )


if explorer_clear:

    st.session_state.explorer_search = ""
    st.session_state.explorer_columns = []
    st.session_state.explorer_loaded = False

    st.rerun()


if explorer_load:

    st.session_state.explorer_search = (
        explorer_search.strip()
    )

    st.session_state.explorer_columns = (
        selected_explorer_columns
    )

    st.session_state.explorer_loaded = True

    st.rerun()


# ============================================================
# RUN EXPLORER QUERY
# ============================================================

if st.session_state.explorer_loaded:

    search_text = (
        st.session_state.explorer_search
    )

    search_columns = (
        st.session_state.explorer_columns
    )

    try:

        query = (
            client
            .table(TABLE_NAME)
            .select("*")
        )

        if search_text and search_columns:

            # Escape wildcard characters so that the user's
            # search is treated as text rather than a pattern.
            safe_search = (
                search_text
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )

            # ------------------------------------------------
            # IMPORTANT:
            # Only text columns reach ILIKE.
            # Numeric columns are never passed to ILIKE.
            # ------------------------------------------------

            conditions = []

            for column in search_columns:

                conditions.append(
                    f"{column}.ilike.%{safe_search}%"
                )

            if conditions:

                # PostgREST OR syntax.
                query = query.or_(
                    ",".join(
                        conditions
                    )
                )

        # ----------------------------------------------------
        # Get only display limit + 1 rows so that we can tell
        # the user that more records exist.
        # ----------------------------------------------------

        response = (
            query
            .range(
                0,
                EXPLORER_DISPLAY_LIMIT,
            )
            .execute()
        )

        rows = response.data or []

        explorer_has_more = (
            len(rows)
            > EXPLORER_DISPLAY_LIMIT
        )

        if explorer_has_more:
            rows = rows[
                :EXPLORER_DISPLAY_LIMIT
            ]

        explorer_df = pd.DataFrame(
            rows
        )

        if explorer_df.empty:

            st.info(
                "No records found."
            )

        else:

            if explorer_has_more:

                st.info(
                    f"Showing the first "
                    f"{EXPLORER_DISPLAY_LIMIT:,} "
                    "matching records."
                )

            else:

                st.success(
                    f"{len(explorer_df):,} "
                    "matching record(s) found."
                )

            st.dataframe(
                explorer_df,
                use_container_width=True,
                hide_index=True,
            )


            # ------------------------------------------------
            # DATA INFORMATION
            # ------------------------------------------------

            with st.expander(
                "Data Information"
            ):

                info_df = pd.DataFrame(
                    {
                        "Column": explorer_df.columns,
                        "Data Type": [
                            str(
                                explorer_df[column].dtype
                            )
                            for column
                            in explorer_df.columns
                        ],
                        "Non-null": [
                            int(
                                explorer_df[column]
                                .notna()
                                .sum()
                            )
                            for column
                            in explorer_df.columns
                        ],
                        "Null": [
                            int(
                                explorer_df[column]
                                .isna()
                                .sum()
                            )
                            for column
                            in explorer_df.columns
                        ],
                    }
                )

                st.dataframe(
                    info_df,
                    use_container_width=True,
                    hide_index=True,
                )


            # ------------------------------------------------
            # CSV EXPORT
            # ------------------------------------------------

            export_query = (
                client
                .table(TABLE_NAME)
                .select("*")
            )

            if search_text and search_columns:

                safe_search = (
                    search_text
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )

                conditions = [
                    f"{column}.ilike.%{safe_search}%"
                    for column
                    in search_columns
                ]

                if conditions:

                    export_query = (
                        export_query.or_(
                            ",".join(
                                conditions
                            )
                        )
                    )

            with st.spinner(
                "Preparing CSV export..."
            ):

                export_df = (
                    fetch_all_from_query(
                        lambda start, end:
                        export_query.range(
                            start,
                            end,
                        )
                    )
                )

            csv_data = (
                export_df
                .to_csv(index=False)
                .encode("utf-8-sig")
            )

            st.download_button(
                "Download All Matching Records as CSV",
                data=csv_data,
                file_name=(
                    "ygntbpro_explorer.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )

    except Exception as exc:

        st.error(
            f"Unable to search Explorer: {exc}"
        )


# ============================================================
# FOOTER
# ============================================================

st.caption(
    f"Table: `{TABLE_NAME}`  •  "
    f"Role: `{user_role}`  •  "
    f"Pending changes: `{pending_changes_count()}`"
)