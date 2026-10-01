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

# Supabase/PostgREST request batch size.
# This is NOT the maximum number of database records.
BATCH_SIZE = 1000

EDITOR_PAGE_SIZE_DEFAULT = 300
EXPLORER_DISPLAY_LIMIT = 1000


# ============================================================
# SESSION STATE
# ============================================================

DEFAULTS = {
    # Authentication
    "session": None,
    "user_role": None,

    # General
    "grid_version": 0,
    "filter_version": 0,

    # Pending changes
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Editor
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

    # Unique filter options
    "editor_filter_options": {},
    "editor_filter_options_loaded": False,

    # Explorer
    "explorer_search": "",
    "explorer_columns": [],
    "explorer_loaded": False,
}


def initialize_session_state():

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


initialize_session_state()


# ============================================================
# GENERAL UTILITIES
# ============================================================

def is_null_like(value: Any) -> bool:

    if value is None:
        return True

    if value is pd.NA:
        return True

    try:
        result = pd.isna(value)

        if isinstance(result, (bool, np.bool_)):
            return bool(result)

    except Exception:
        pass

    return False


def clean_value(value: Any) -> Any:
    """
    Convert Pandas / NumPy / Decimal / datetime values
    into Supabase/PostgREST-safe Python values.
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
        return [
            clean_value(item)
            for item in value.tolist()
        ]

    if isinstance(value, list):
        return [
            clean_value(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            clean_value(item)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            str(key): clean_value(val)
            for key, val in value.items()
        }

    return value


def make_json_safe(value):
    return clean_value(value)


def values_equal(left, right) -> bool:

    left = clean_value(left)
    right = clean_value(right)

    if left is None and right is None:
        return True

    return left == right


def resolve_column(
    columns,
    *candidates,
):

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

def get_secret(
    name: str,
    default=None,
):

    try:

        value = st.secrets.get(name)

        if value is not None:
            return value

    except Exception:
        pass

    return os.getenv(
        name,
        default,
    )


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

    return create_client(
        url,
        key,
    )


def get_user_client() -> Client:
    """
    Create a Supabase client authenticated with the current
    user's access token.

    Database queries therefore operate under the user's RLS
    policies.
    """

    session = st.session_state.get(
        "session"
    )

    if not session:
        raise RuntimeError(
            "No authenticated Supabase session."
        )

    url, key = get_supabase_config()

    client = create_client(
        url,
        key,
    )

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

    if not access_token:
        raise RuntimeError(
            "Authenticated session has no access token."
        )

    try:

        client.auth.set_session(
            access_token,
            refresh_token,
        )

    except Exception:

        try:

            client.postgrest.auth(
                access_token
            )

        except Exception as exc:

            raise RuntimeError(
                "Unable to authenticate Supabase client: "
                f"{exc}"
            )

    return client


# ============================================================
# LOGIN
# ============================================================

def login_user(
    email: str,
    password: str,
):

    try:

        base_supabase = get_base_client()

        response = (
            base_supabase
            .auth
            .sign_in_with_password(
                {
                    "email": email.strip(),
                    "password": password,
                }
            )
        )

        if not response.session:

            return (
                False,
                "Login failed: no authenticated session was returned.",
            )

        # Store authenticated session.
        st.session_state.session = (
            response.session
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # Role is looked up by authenticated USER UUID.
        # NOT by email.
        # ----------------------------------------------------

        user_client = get_user_client()

        user_id = (
            response.session.user.id
        )

        role_result = (
            user_client
            .table(USER_ROLE_TABLE)
            .select("role")
            .eq(
                "user_id",
                user_id,
            )
            .limit(1)
            .execute()
        )

        role = "viewer"

        if role_result.data:

            role = (
                role_result.data[0]
                .get("role")
                or "viewer"
            )

        role = str(
            role
        ).strip().lower()

        if role not in {
            "viewer",
            "editor",
            "admin",
        }:

            role = "viewer"

        st.session_state.user_role = role

        return (
            True,
            "Login successful.",
        )

    except Exception as exc:

        return (
            False,
            str(exc),
        )


# ============================================================
# LOGOUT
# ============================================================

def logout_user():

    try:

        base_supabase = get_base_client()

        base_supabase.auth.sign_out()

    except Exception:
        pass

    for key, default_value in DEFAULTS.items():

        if isinstance(default_value, dict):
            st.session_state[key] = (
                default_value.copy()
            )

        elif isinstance(default_value, list):
            st.session_state[key] = (
                default_value.copy()
            )

        elif isinstance(default_value, set):
            st.session_state[key] = (
                default_value.copy()
            )

        else:
            st.session_state[key] = default_value

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
# USER PERMISSIONS
# ============================================================

user_role = (
    st.session_state.user_role
    or "viewer"
)

user_role = str(
    user_role
).strip().lower()

can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"
can_delete = user_role == "admin"


# ============================================================
# AUTHENTICATED CLIENT
# ============================================================

try:

    client = get_user_client()

except Exception as exc:

    st.error(
        "Unable to create authenticated database client: "
        f"{exc}"
    )

    st.stop()


# ============================================================
# HEADER
# ============================================================

header1, header2, header3 = st.columns(
    [5, 2, 1]
)

with header1:

    st.title(
        "YgnTBPro Database"
    )

with header2:

    st.caption(
        f"Role: **{user_role.upper()}**"
    )

with header3:

    if st.button(
        "Logout",
        use_container_width=True,
    ):

        logout_user()


# ============================================================
# TABLE SCHEMA
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def get_table_columns(
    supabase_url,
    table_name,
):

    base_client = get_base_client()

    response = (
        base_client
        .table(table_name)
        .select("*")
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return []

    return list(
        rows[0].keys()
    )


try:

    supabase_url, _ = (
        get_supabase_config()
    )

    columns = get_table_columns(
        supabase_url,
        TABLE_NAME,
    )

except Exception as exc:

    st.error(
        "Unable to read table schema: "
        f"{exc}"
    )

    st.stop()


if not columns:

    st.error(
        f"No columns were returned from `{TABLE_NAME}`."
    )

    st.stop()


# ============================================================
# DATABASE COLUMN MAPPING
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


PRIMARY_KEY = db_columns.get(
    "primary_key"
)

if not PRIMARY_KEY:

    st.error(
        "PatientID primary-key column could not be identified."
    )

    st.stop()


# ============================================================
# BATCH FETCH
# ============================================================

def fetch_all_rows(
    table_name,
    supabase_client,
    select_columns="*",
    order_column=None,
    descending=False,
):

    rows = []
    start = 0

    while True:

        end = (
            start
            + BATCH_SIZE
            - 1
        )

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

        batch = (
            response.data
            or []
        )

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

    rows = []
    start = 0

    while True:

        end = (
            start
            + batch_size
            - 1
        )

        response = (
            query_builder(
                start,
                end,
            )
            .execute()
        )

        batch = (
            response.data
            or []
        )

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < batch_size:
            break

        start += batch_size

    return pd.DataFrame(rows)


# ============================================================
# UNIQUE FILTER OPTIONS
# ============================================================

def get_unique_filter_values(
    supabase_client,
    filter_columns,
):

    actual_columns = [
        column
        for column in filter_columns.values()
        if column
    ]

    actual_columns = list(
        dict.fromkeys(
            actual_columns
        )
    )

    if not actual_columns:
        return {}

    select_columns = ",".join(
        actual_columns
    )

    values_by_filter = {
        name: set()
        for name in filter_columns
    }

    start = 0

    while True:

        end = (
            start
            + BATCH_SIZE
            - 1
        )

        response = (
            supabase_client
            .table(TABLE_NAME)
            .select(select_columns)
            .range(start, end)
            .execute()
        )

        batch = (
            response.data
            or []
        )

        if not batch:
            break

        for row in batch:

            for filter_name, column in (
                filter_columns.items()
            ):

                if not column:
                    continue

                value = row.get(
                    column
                )

                if is_null_like(value):
                    continue

                value = clean_value(
                    value
                )

                if value is None:
                    continue

                try:

                    values_by_filter[
                        filter_name
                    ].add(value)

                except TypeError:

                    values_by_filter[
                        filter_name
                    ].add(
                        str(value)
                    )

        if len(batch) < BATCH_SIZE:
            break

        start += BATCH_SIZE

    result = {}

    for filter_name, values in (
        values_by_filter.items()
    ):

        try:

            result[filter_name] = sorted(
                values
            )

        except Exception:

            result[filter_name] = sorted(
                values,
                key=lambda value: str(value),
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

        options = (
            get_unique_filter_values(
                client,
                filter_columns,
            )
        )

    st.session_state.editor_filter_options = (
        options
    )

    st.session_state.editor_filter_options_loaded = (
        True
    )


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

    # --------------------------------------------------------
    # MULTI-VALUE FILTERS
    # --------------------------------------------------------

    for filter_name, column in (
        filter_mapping.items()
    ):

        if not column:
            continue

        selected_values = filters.get(
            filter_name,
            [],
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

    # --------------------------------------------------------
    # DATE FILTER
    # --------------------------------------------------------

    date_column = db_columns.get(
        "date"
    )

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

    # --------------------------------------------------------
    # STABLE ORDER
    # --------------------------------------------------------

    if PRIMARY_KEY:

        query = query.order(
            PRIMARY_KEY,
            desc=True,
        )

    return query


# ============================================================
# LOAD ONE EDITOR PAGE
# ============================================================

def load_editor_page(
    supabase_client,
    filters,
    page,
    page_size,
):

    query = build_editor_query(
        supabase_client,
        filters,
    )

    start = max(
        0,
        (page - 1) * page_size,
    )

    # range() is inclusive.
    # Request one additional row to detect next page.
    end = (
        start
        + page_size
    )

    response = (
        query
        .range(start, end)
        .execute()
    )

    rows = (
        response.data
        or []
    )

    has_next_page = (
        len(rows)
        > page_size
    )

    if has_next_page:

        rows = rows[
            :page_size
        ]

    return (
        pd.DataFrame(rows),
        has_next_page,
    )


# ============================================================
# PENDING UPDATE
# ============================================================

def add_pending_update(
    primary_id,
    changes,
):

    if is_null_like(primary_id):
        return

    primary_id = clean_value(
        primary_id
    )

    if not changes:
        return

    existing = (
        st.session_state.pending_updates.get(
            primary_id,
            {},
        )
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

    primary_id = clean_value(
        primary_id
    )

    st.session_state.pending_updates.pop(
        primary_id,
        None,
    )


# ============================================================
# CAPTURE GRID CHANGES
# ============================================================

def capture_grid_changes(
    source_df,
    edited_df,
):

    if source_df is None:
        return

    if edited_df is None:
        return

    if source_df.empty:
        return

    if edited_df.empty:
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

        primary_id = clean_value(
            primary_id
        )

        source_by_id[
            primary_id
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
                db_columns.get(
                    "updated_at"
                )
                and column
                == db_columns[
                    "updated_at"
                ]
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


# ============================================================
# PENDING COUNTS
# ============================================================

def pending_changes_count():

    return (
        len(
            st.session_state.pending_updates
        )
        + len(
            st.session_state.pending_inserts
        )
        + len(
            st.session_state.pending_deletes
        )
    )


def clear_pending_changes():

    st.session_state.pending_updates = {}
    st.session_state.pending_inserts = []
    st.session_state.pending_deletes = set()


# ============================================================
# APPLY PENDING UPDATES TO DISPLAY
# ============================================================

def apply_pending_changes_to_df(
    df,
):

    if df is None:
        return df

    if df.empty:
        return df

    if PRIMARY_KEY not in df.columns:
        return df

    result = df.copy()

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
            st.session_state
            .pending_updates
            .get(
                primary_id,
                {},
            )
        )

        for column, value in (
            changes.items()
        ):

            if column in result.columns:

                result.at[
                    index,
                    column,
                ] = value

    return result


# ============================================================
# DELETE SELECTED
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

        # ----------------------------------------------------
        # If this is a pending insert, remove it from pending
        # inserts instead of creating a database DELETE.
        # ----------------------------------------------------

        remaining_inserts = []
        removed_insert = False

        for record in (
            st.session_state.pending_inserts
        ):

            record_id = record.get(
                PRIMARY_KEY
            )

            if (
                not is_null_like(record_id)
                and clean_value(record_id)
                == primary_id
            ):

                removed_insert = True
                continue

            remaining_inserts.append(
                record
            )

        st.session_state.pending_inserts = (
            remaining_inserts
        )

        if removed_insert:

            deleted_count += 1

            continue

        # ----------------------------------------------------
        # Existing database record
        # ----------------------------------------------------

        st.session_state.pending_deletes.add(
            primary_id
        )

        remove_pending_update(
            primary_id
        )

        deleted_count += 1

    return deleted_count


# ============================================================
# ADD RECORD
# ============================================================

def add_new_record(
    record,
):

    cleaned_record = {
        column: clean_value(value)
        for column, value in record.items()
    }

    primary_id = cleaned_record.get(
        PRIMARY_KEY
    )

    if not is_null_like(primary_id):

        primary_id = clean_value(
            primary_id
        )

        # Check pending inserts
        for existing in (
            st.session_state.pending_inserts
        ):

            existing_id = existing.get(
                PRIMARY_KEY
            )

            if (
                not is_null_like(existing_id)
                and clean_value(existing_id)
                == primary_id
            ):

                return (
                    False,
                    "This PatientID already exists "
                    "in pending inserts.",
                )

    st.session_state.pending_inserts.append(
        cleaned_record
    )

    return (
        True,
        "New record added to pending changes.",
    )


# ============================================================
# SYNC
# ============================================================

def sync_pending_changes(
    supabase_client,
):

    errors = []

    successful_updates = []
    successful_inserts = []
    successful_deletes = []

    # ========================================================
    # UPDATE
    # ========================================================

    for primary_id, changes in list(
        st.session_state
        .pending_updates
        .items()
    ):

        # If deletion is pending for the same record,
        # do not send its update.
        if (
            primary_id
            in st.session_state.pending_deletes
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
                    make_json_safe(
                        primary_id
                    ),
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

    # ========================================================
    # INSERT
    # ========================================================

    for record in list(
        st.session_state.pending_inserts
    ):

        try:

            payload = make_json_safe(
                record
            )

            (
                supabase_client
                .table(TABLE_NAME)
                .insert(payload)
                .execute()
            )

            successful_inserts.append(
                id(record)
            )

        except Exception as exc:

            patient_id = record.get(
                PRIMARY_KEY
            )

            errors.append(
                f"INSERT {patient_id}: {exc}"
            )

    # ========================================================
    # DELETE
    # ========================================================

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
                    make_json_safe(
                        primary_id
                    ),
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

    # ========================================================
    # REMOVE SUCCESSFUL UPDATES
    # ========================================================

    for primary_id in successful_updates:

        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    # ========================================================
    # REMOVE SUCCESSFUL INSERTS
    # ========================================================

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

    # ========================================================
    # REMOVE SUCCESSFUL DELETES
    # ========================================================

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

    # --------------------------------------------------------
    # Do NOT clear pending changes here.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Reset multiselect widget states.
    # --------------------------------------------------------

    st.session_state.filter_patient_id = []
    st.session_state.filter_team = []
    st.session_state.filter_tsp = []
    st.session_state.filter_approach = []
    st.session_state.filter_case = []
    st.session_state.filter_visit_no = []
    st.session_state.filter_sr_no = []
    st.session_state.filter_ward_village = []

    st.session_state.filter_date_from = None
    st.session_state.filter_date_to = None

    # --------------------------------------------------------
    # Reset page
    # --------------------------------------------------------

    st.session_state.editor_page = 1
    st.session_state.editor_loaded = True

    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.filter_version += 1
    st.session_state.grid_version += 1


# ============================================================
# DATABASE EDITOR
# ============================================================

st.divider()

st.header(
    "Database Editor"
)

if not can_edit:

    st.info(
        "Viewer mode: records can be viewed, "
        "but database editing is disabled."
    )


# ============================================================
# FILTER LIST LOADING
# ============================================================

load_col1, load_col2 = st.columns(
    [4, 1]
)

with load_col1:

    if not (
        st.session_state
        .editor_filter_options_loaded
    ):

        if st.button(
            "Load Filter Lists",
            use_container_width=True,
        ):

            load_editor_filter_options()

            st.rerun()


with load_col2:

    if st.button(
        "Refresh Lists",
        use_container_width=True,
    ):

        st.session_state.editor_filter_options_loaded = (
            False
        )

        st.session_state.editor_filter_options = {}

        load_editor_filter_options()

        st.rerun()


if not (
    st.session_state
    .editor_filter_options_loaded
):

    st.info(
        "Click **Load Filter Lists** to load the "
        "unique values for the Database Editor filters."
    )

else:

    options = (
        st.session_state
        .editor_filter_options
    )

    # ========================================================
    # MULTISELECT FILTERS
    # ========================================================

    filter_row1 = st.columns(4)

    with filter_row1[0]:

        patient_id_values = st.multiselect(
            "Patient ID",

            options.get(
                "patient_id",
                [],
            ),

            key="filter_patient_id",

            help=(
                "Select one or more Patient IDs. "
                "The dropdown is searchable."
            ),
        )

    with filter_row1[1]:

        team_values = st.multiselect(
            "Team",
            options.get(
                "team",
                [],
            ),
            key="filter_team",
        )

    with filter_row1[2]:

        tsp_values = st.multiselect(
            "TSP",
            options.get(
                "tsp",
                [],
            ),
            key="filter_tsp",
        )

    with filter_row1[3]:

        approach_values = st.multiselect(
            "Approach",
            options.get(
                "approach",
                [],
            ),
            key="filter_approach",
        )

    filter_row2 = st.columns(4)

    with filter_row2[0]:

        case_values = st.multiselect(
            "Case",
            options.get(
                "case",
                [],
            ),
            key="filter_case",
        )

    with filter_row2[1]:

        visit_no_values = st.multiselect(
            "Visit No",
            options.get(
                "visit_no",
                [],
            ),
            key="filter_visit_no",
        )

    with filter_row2[2]:

        sr_no_values = st.multiselect(
            "SR No",
            options.get(
                "sr_no",
                [],
            ),
            key="filter_sr_no",
        )

    with filter_row2[3]:

        ward_village_values = st.multiselect(
            "Ward / Village",
            options.get(
                "ward_village",
                [],
            ),
            key="filter_ward_village",
        )

    filter_row3 = st.columns(3)

    with filter_row3[0]:

        date_from = st.date_input(
            "Date From",
            value=None,
            key="filter_date_from",
        )

    with filter_row3[1]:

        date_to = st.date_input(
            "Date To",
            value=None,
            key="filter_date_to",
        )

    with filter_row3[2]:

        page_size = st.selectbox(
            "Rows per page",
            [100, 300, 500, 1000],
            index=1,
            key="editor_page_size",
        )

    # ========================================================
    # FILTER ACTIONS
    # ========================================================

    action_col1, action_col2 = st.columns(
        2
    )

    with action_col1:

        apply_filters = st.button(
            "Apply Filters",
            type="primary",
            use_container_width=True,
        )

    with action_col2:

        reset_filters = st.button(
            "Reset Filters",
            use_container_width=True,
        )

    if apply_filters:

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

        st.session_state.editor_page_size = (
            page_size
        )

        st.session_state.editor_loaded = True

        # Only invalidate the displayed database snapshot.
        # Pending changes remain untouched.
        st.session_state.editor_source_df = None
        st.session_state.editor_source_key = None

        st.session_state.filter_version += 1
        st.session_state.grid_version += 1

        st.rerun()

    if reset_filters:

        reset_editor_filters()

        st.rerun()


# ============================================================
# LOAD EDITOR
# ============================================================

if st.session_state.editor_loaded:

    filters = (
        st.session_state.editor_filters
    )

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
    # Apply pending updates to displayed values
    # --------------------------------------------------------

    editor_df = (
        apply_pending_changes_to_df(
            editor_df
        )
    )


    # --------------------------------------------------------
    # Source snapshot
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

        try:

            source_df, _ = (
                load_editor_page(
                    client,
                    filters,
                    current_page,
                    current_page_size,
                )
            )

        except Exception:

            source_df = editor_df.copy()

        st.session_state.editor_source_df = (
            source_df.copy()
        )

        st.session_state.editor_source_key = (
            filter_signature
        )


    # ========================================================
    # STATUS
    # ========================================================

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
            pending_changes_count(),
        )

    with status3:

        st.metric(
            "Page",
            current_page,
        )


    # ========================================================
    # AG GRID
    # ========================================================

    if editor_df.empty:

        st.info(
            "No records found for the selected filters."
        )

    else:

        display_df = editor_df.copy()

        # ----------------------------------------------------
        # Safe display conversion
        # ----------------------------------------------------

        for column in display_df.columns:

            display_df[column] = (
                display_df[column]
                .map(clean_value)
            )

        # ----------------------------------------------------
        # Grid builder
        # ----------------------------------------------------

        gb = GridOptionsBuilder.from_dataframe(
            display_df
        )

        # ----------------------------------------------------
        # DEFAULT COLUMN SETTINGS
        # ----------------------------------------------------

        gb.configure_default_column(

            editable=can_edit,

            resizable=True,

            sortable=True,

            filter=True,

            minWidth=120,

            flex=0,

            wrapText=False,

            autoHeight=False,
        )

        # ----------------------------------------------------
        # Selection
        # ----------------------------------------------------

        gb.configure_selection(
            "multiple",
            use_checkbox=True,
        )

        # ----------------------------------------------------
        # No AG Grid internal pagination
        # ----------------------------------------------------

        gb.configure_pagination(
            enabled=False
        )

        # ----------------------------------------------------
        # Grid options
        # ----------------------------------------------------

        gb.configure_grid_options(

            suppressRowClickSelection=True,

            enableRangeSelection=True,

            rowSelection="multiple",

            suppressColumnVirtualisation=False,

            suppressSizeToFit=True,

            enterNavigatesVertically=True,

            enterNavigatesVerticallyAfterEdit=True,
        )

        # ----------------------------------------------------
        # Patient ID
        # ----------------------------------------------------

        gb.configure_column(

            PRIMARY_KEY,

            editable=False,

            minWidth=170,

            width=190,
        )

        # ----------------------------------------------------
        # Updated At
        # ----------------------------------------------------

        updated_at_column = (
            db_columns.get(
                "updated_at"
            )
        )

        if updated_at_column:

            gb.configure_column(

                updated_at_column,

                editable=False,

                minWidth=180,

                width=200,
            )

        # ----------------------------------------------------
        # Column widths
        # ----------------------------------------------------

        for column in display_df.columns:

            lower_name = (
                str(column)
                .strip()
                .lower()
            )

            if column == PRIMARY_KEY:
                continue

            if (
                lower_name
                in {
                    "team",
                    "visit_no",
                    "visitno",
                    "sr_no",
                    "srno",
                }
            ):

                gb.configure_column(
                    column,
                    minWidth=90,
                    width=110,
                )

            elif lower_name in {
                "tsp",
                "approach",
                "case",
            }:

                gb.configure_column(
                    column,
                    minWidth=130,
                    width=160,
                )

            elif (
                "ward" in lower_name
                or "village" in lower_name
            ):

                gb.configure_column(
                    column,
                    minWidth=160,
                    width=200,
                )

            elif (
                "date" in lower_name
            ):

                gb.configure_column(
                    column,
                    minWidth=130,
                    width=150,
                )

            elif (
                "name" in lower_name
                or "address" in lower_name
                or "remark" in lower_name
                or "reason" in lower_name
            ):

                gb.configure_column(
                    column,
                    minWidth=180,
                    width=220,
                )

            else:

                gb.configure_column(
                    column,
                    minWidth=120,
                    width=150,
                )

        grid_options = gb.build()

        # ----------------------------------------------------
        # Stable row ID
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

        # ----------------------------------------------------
        # AG Grid
        # ----------------------------------------------------

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

            allow_unsafe_jscode=True,

            enable_enterprise_modules=False,

            fit_columns_on_grid_load=False,

            reload_data=False,

            height=700,

            width="100%",

            key=(
                "database_grid_"
                f"{st.session_state.grid_version}_"
                f"{st.session_state.filter_version}_"
                f"{current_page}"
            ),
        )


        # ====================================================
        # CAPTURE EDITS
        # ====================================================

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
                st.session_state
                .editor_source_df
            )

            if source_df is not None:

                capture_grid_changes(
                    source_df,
                    edited_grid_df,
                )


        # ====================================================
        # SELECTED ROWS
        # ====================================================

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
                .to_dict(
                    "records"
                )
            )

        if selected_rows is None:
            selected_rows = []


        # ====================================================
        # DELETE SELECTED
        # ====================================================

        if can_delete:

            if st.button(
                "Delete Selected",
                type="secondary",
                disabled=not bool(
                    selected_rows
                ),
                use_container_width=True,
            ):

                deleted = (
                    delete_selected_rows(
                        selected_rows
                    )
                )

                if deleted:

                    st.success(
                        f"{deleted} record(s) "
                        "added to pending deletion."
                    )

                    st.session_state.grid_version += 1

                    st.rerun()


        # ====================================================
        # PAGINATION
        # ====================================================

        st.divider()

        page1, page2, page3 = st.columns(
            [1, 2, 1]
        )

        with page1:

            if st.button(
                "← Previous",

                disabled=(
                    current_page <= 1
                ),

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


        with page2:

            if has_next_page:

                page_text = (
                    f"Page {current_page} "
                    "• More records available"
                )

            else:

                page_text = (
                    f"Page {current_page} "
                    "• Last page"
                )

            st.markdown(
                (
                    "<div style="
                    "'text-align:center;"
                    "padding-top:8px'>"
                    f"{page_text}"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


        with page3:

            if st.button(
                "Next →",

                disabled=(
                    not has_next_page
                ),

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
# PENDING CHANGES
# ============================================================

st.divider()

st.header(
    "Pending Changes"
)

pending_updates = (
    st.session_state.pending_updates
)

pending_inserts = (
    st.session_state.pending_inserts
)

pending_deletes = (
    st.session_state.pending_deletes
)

pending_total = (
    pending_changes_count()
)


if pending_total == 0:

    st.info(
        "No pending changes."
    )

else:

    count1, count2, count3 = st.columns(
        3
    )

    with count1:

        st.metric(
            "Updates",
            len(pending_updates),
        )

    with count2:

        st.metric(
            "Inserts",
            len(pending_inserts),
        )

    with count3:

        st.metric(
            "Deletes",
            len(pending_deletes),
        )


    # ========================================================
    # UPDATE PREVIEW
    # ========================================================

    if pending_updates:

        st.subheader(
            "Pending Updates"
        )

        update_rows = []

        for primary_id, changes in (
            pending_updates.items()
        ):

            for column, value in (
                changes.items()
            ):

                update_rows.append(
                    {
                        PRIMARY_KEY: primary_id,
                        "Change Type": "UPDATE",
                        "Column": column,
                        "New Value": value,
                    }
                )

        if update_rows:

            st.dataframe(
                pd.DataFrame(
                    update_rows
                ),
                use_container_width=True,
                hide_index=True,
            )


    # ========================================================
    # INSERT PREVIEW
    # ========================================================

    if pending_inserts:

        st.subheader(
            "Pending Inserts"
        )

        st.dataframe(
            pd.DataFrame(
                pending_inserts
            ),
            use_container_width=True,
            hide_index=True,
        )


    # ========================================================
    # DELETE PREVIEW
    # ========================================================

    if pending_deletes:

        st.subheader(
            "Pending Deletes"
        )

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


    # ========================================================
    # SYNC / DISCARD
    # ========================================================

    sync_col, discard_col = st.columns(
        2
    )

    with sync_col:

        if st.button(
            "Sync Changes to Supabase",
            type="primary",
            disabled=not can_edit,
            use_container_width=True,
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

        st.subheader(
            "New Record"
        )

        new_record = {}

        editable_columns = [
            column
            for column in columns
            if column
            != db_columns.get(
                "updated_at"
            )
        ]

        form_columns = st.columns(
            3
        )

        for index, column in enumerate(
            editable_columns
        ):

            with form_columns[
                index % 3
            ]:

                new_record[column] = (
                    st.text_input(
                        column,
                        key=(
                            "new_record_"
                            f"{column}"
                        ),
                    )
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

            success, message = (
                add_new_record(
                    cleaned_record
                )
            )

            if success:

                st.success(
                    message
                )

                st.rerun()

            else:

                st.error(
                    message
                )


# ============================================================
# EXPLORER
# ============================================================

st.divider()

st.header(
    "Explorer"
)

st.caption(
    "Search database records by keyword. "
    "Known numeric and date columns are excluded "
    "from ILIKE text searching."
)


# ============================================================
# EXPLORER SEARCHABLE COLUMNS
# ============================================================

# IMPORTANT:
# Do not dynamically guess PostgreSQL data types from a
# small sample. The following known numeric/date fields
# are explicitly excluded from ILIKE searches.

EXPLORER_EXCLUDED_COLUMNS = {
    db_columns.get("team"),
    db_columns.get("visit_no"),
    db_columns.get("sr_no"),
    db_columns.get("date"),
}

EXPLORER_EXCLUDED_COLUMNS = {
    column
    for column in EXPLORER_EXCLUDED_COLUMNS
    if column
}


searchable_columns = [
    column
    for column in columns
    if column
    not in EXPLORER_EXCLUDED_COLUMNS
]


# PatientID is intentionally searchable as text.
if PRIMARY_KEY not in searchable_columns:

    searchable_columns.insert(
        0,
        PRIMARY_KEY,
    )


searchable_columns = list(
    dict.fromkeys(
        searchable_columns
    )
)


# ============================================================
# EXPLORER CONTROLS
# ============================================================

explorer_search_value = st.text_input(
    "Search keyword",

    value=st.session_state.get(
        "explorer_search",
        "",
    ),

    placeholder=(
        "Search Patient ID, TSP, Approach, Case, "
        "Ward/Village, symptoms, etc."
    ),
)


saved_explorer_columns = (
    st.session_state.get(
        "explorer_columns",
        [],
    )
)

valid_saved_columns = [
    column
    for column in saved_explorer_columns
    if column in searchable_columns
]


if valid_saved_columns:

    default_explorer_columns = (
        valid_saved_columns
    )

else:

    default_explorer_columns = (
        searchable_columns
    )


explorer_search_columns = st.multiselect(

    "Columns to search",

    options=searchable_columns,

    default=default_explorer_columns,

    help=(
        "Select the columns in which the keyword "
        "should be searched."
    ),
)


explorer_action1, explorer_action2 = st.columns(
    2
)


with explorer_action1:

    search_explorer = st.button(
        "🔎 Search Explorer",
        type="primary",
        use_container_width=True,
    )


with explorer_action2:

    clear_explorer = st.button(
        "Clear Explorer",
        use_container_width=True,
    )


# ============================================================
# CLEAR EXPLORER
# ============================================================

if clear_explorer:

    st.session_state.explorer_search = ""
    st.session_state.explorer_columns = []
    st.session_state.explorer_loaded = False

    st.rerun()


# ============================================================
# SAVE EXPLORER SEARCH
# ============================================================

if search_explorer:

    st.session_state.explorer_search = (
        explorer_search_value.strip()
    )

    st.session_state.explorer_columns = list(
        explorer_search_columns
    )

    st.session_state.explorer_loaded = True

    st.rerun()


# ============================================================
# EXPLORER QUERY
# ============================================================

if st.session_state.get(
    "explorer_loaded",
    False,
):

    search_text = (
        st.session_state.get(
            "explorer_search",
            "",
        )
        .strip()
    )

    search_columns = (
        st.session_state.get(
            "explorer_columns",
            [],
        )
    )

    # Ensure saved columns still exist.
    search_columns = [
        column
        for column in search_columns
        if column in searchable_columns
    ]

    try:

        query = (
            client
            .table(TABLE_NAME)
            .select("*")
        )


        # ====================================================
        # KEYWORD SEARCH
        # ====================================================

        if search_text and search_columns:

            # Escape characters that can interfere with
            # PostgREST pattern matching.
            safe_search = (
                search_text
                .replace(
                    "\\",
                    "\\\\",
                )
                .replace(
                    "%",
                    "\\%",
                )
                .replace(
                    "_",
                    "\\_",
                )
            )

            or_conditions = []

            for column in search_columns:

                # Defensive check:
                # only columns from the explicitly safe list
                # can reach ILIKE.
                if column not in searchable_columns:
                    continue

                or_conditions.append(
                    f"{column}.ilike.*"
                    f"{safe_search}"
                    f"*"
                )

            if or_conditions:

                query = query.or_(
                    ",".join(
                        or_conditions
                    )
                )


        # ====================================================
        # FIRST 1001 RECORDS
        # ====================================================

        response = (
            query
            .range(
                0,
                EXPLORER_DISPLAY_LIMIT,
            )
            .execute()
        )

        rows = (
            response.data
            or []
        )

        has_more = (
            len(rows)
            > EXPLORER_DISPLAY_LIMIT
        )

        if has_more:

            rows = rows[
                :EXPLORER_DISPLAY_LIMIT
            ]

        explorer_df = pd.DataFrame(
            rows
        )


        # ====================================================
        # RESULTS
        # ====================================================

        if explorer_df.empty:

            st.info(
                "No matching records found."
            )

        else:

            if has_more:

                st.info(
                    "Showing the first "
                    f"{EXPLORER_DISPLAY_LIMIT:,} "
                    "matching records."
                )

            else:

                st.success(
                    f"{len(explorer_df):,} "
                    "matching record(s)."
                )


            # ------------------------------------------------
            # Display
            # ------------------------------------------------

            st.dataframe(

                explorer_df,

                use_container_width=True,

                hide_index=True,

                height=600,
            )


            # ------------------------------------------------
            # Data information
            # ------------------------------------------------

            with st.expander(
                "Data Information"
            ):

                information = []

                for column in (
                    explorer_df.columns
                ):

                    information.append(
                        {
                            "Column": column,

                            "Data Type": str(
                                explorer_df[
                                    column
                                ].dtype
                            ),

                            "Non-null": int(
                                explorer_df[
                                    column
                                ]
                                .notna()
                                .sum()
                            ),

                            "Null": int(
                                explorer_df[
                                    column
                                ]
                                .isna()
                                .sum()
                            ),
                        }
                    )

                st.dataframe(

                    pd.DataFrame(
                        information
                    ),

                    use_container_width=True,

                    hide_index=True,
                )


            # =================================================
            # EXPORT ALL MATCHING RECORDS
            # =================================================

            if st.button(
                "Prepare CSV of All Matching Records",
                use_container_width=True,
            ):

                with st.spinner(
                    "Loading all matching records..."
                ):

                    export_query = (
                        client
                        .table(TABLE_NAME)
                        .select("*")
                    )


                    if search_text and search_columns:

                        safe_search = (
                            search_text
                            .replace(
                                "\\",
                                "\\\\",
                            )
                            .replace(
                                "%",
                                "\\%",
                            )
                            .replace(
                                "_",
                                "\\_",
                            )
                        )

                        export_conditions = []

                        for column in (
                            search_columns
                        ):

                            if (
                                column
                                not in searchable_columns
                            ):
                                continue

                            export_conditions.append(
                                f"{column}.ilike.*"
                                f"{safe_search}"
                                f"*"
                            )

                        if export_conditions:

                            export_query = (
                                export_query
                                .or_(
                                    ",".join(
                                        export_conditions
                                    )
                                )
                            )


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
                    .to_csv(
                        index=False
                    )
                    .encode(
                        "utf-8-sig"
                    )
                )


                st.success(
                    f"{len(export_df):,} "
                    "record(s) prepared for download."
                )


                st.download_button(

                    "⬇️ Download CSV",

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