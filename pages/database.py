# ============================================================
# database.py
# YgnTBPro Supabase Database Editor / Explorer
# ============================================================

import os
from datetime import date, datetime
from decimal import Decimal

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
# CONFIGURATION
# ============================================================

TABLE_NAME = "ygntbpro"
USER_ROLE_TABLE = "user_roles"

# Supabase request batch size.
# This is NOT the maximum number of database records.
BATCH_SIZE = 1000

EDITOR_PAGE_SIZE_DEFAULT = 300
EXPLORER_PAGE_SIZE = 1000

ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"


# ============================================================
# SESSION DEFAULTS
# ============================================================

DEFAULTS = {
    # Authentication
    "session": None,
    "user_role": None,

    # General versions
    "grid_version": 0,
    "filter_version": 0,

    # Pending database changes
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

    "editor_filter_options": {},
    "editor_filter_options_loaded": False,

    # Explorer
    "explorer_search": "",
    "explorer_columns": [],
    "explorer_loaded": False,
    "explorer_page": 1,
    "explorer_has_next": False,
}


def initialize_session_state():
    for key, default_value in DEFAULTS.items():

        if key not in st.session_state:

            if isinstance(default_value, dict):
                st.session_state[key] = default_value.copy()

            elif isinstance(default_value, list):
                st.session_state[key] = list(default_value)

            elif isinstance(default_value, set):
                st.session_state[key] = set(default_value)

            else:
                st.session_state[key] = default_value


initialize_session_state()


# ============================================================
# VALUE UTILITIES
# ============================================================

def is_null_like(value):
    if value is None:
        return True

    try:
        result = pd.isna(value)

        if isinstance(result, (bool, np.bool_)):
            return bool(result)

    except Exception:
        pass

    return False


def clean_value(value):
    """
    Convert Pandas / NumPy / Decimal / datetime values into
    values that Supabase/PostgREST can safely serialize.
    """

    if is_null_like(value):
        return None

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.to_pydatetime().isoformat()

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, np.ndarray):
        return value.tolist()

    return value


def make_json_safe(data):
    if isinstance(data, dict):
        return {
            str(k): make_json_safe(v)
            for k, v in data.items()
        }

    if isinstance(data, list):
        return [
            make_json_safe(v)
            for v in data
        ]

    if isinstance(data, tuple):
        return [
            make_json_safe(v)
            for v in data
        ]

    if isinstance(data, set):
        return [
            make_json_safe(v)
            for v in data
        ]

    return clean_value(data)


def values_equal(left, right):

    left_null = is_null_like(left)
    right_null = is_null_like(right)

    if left_null and right_null:
        return True

    if left_null != right_null:
        return False

    try:
        return bool(left == right)
    except Exception:
        return str(left) == str(right)


def resolve_column(columns, *candidates):

    if not columns:
        return None

    exact = {
        str(column).lower(): column
        for column in columns
    }

    for candidate in candidates:
        if not candidate:
            continue

        found = exact.get(str(candidate).lower())

        if found is not None:
            return found

    return None


# ============================================================
# SUPABASE CONFIGURATION
# ============================================================

def get_secret(name, default=None):

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


@st.cache_resource(show_spinner=False)
def get_base_client():

    url, key = get_supabase_config()

    return create_client(url, key)


def get_user_client() -> Client:

    session = st.session_state.get("session")

    if not session:
        raise RuntimeError(
            "No authenticated session is available."
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

    if not access_token:
        raise RuntimeError(
            "Authenticated access token is missing."
        )

    # Preferred Supabase-Python method.
    try:

        if refresh_token:
            client.auth.set_session(
                access_token,
                refresh_token,
            )

        else:
            client.postgrest.auth(
                access_token
            )

    except Exception:

        # Fallback for versions where set_session
        # behaves differently.
        try:
            client.postgrest.auth(
                access_token
            )

        except Exception as exc:
            raise RuntimeError(
                f"Unable to authenticate Supabase client: {exc}"
            )

    return client


# ============================================================
# AUTHENTICATION
# ============================================================

def login_user(email: str, password: str):

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

        if role_result.data:

            role = role_result.data[0].get(
                "role",
                ROLE_VIEWER,
            )

            role = str(role).strip().lower()

            if role not in {
                ROLE_VIEWER,
                ROLE_EDITOR,
                ROLE_ADMIN,
            }:
                role = ROLE_VIEWER

        else:
            role = ROLE_VIEWER

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
            st.session_state[key] = list(value)

        elif isinstance(value, set):
            st.session_state[key] = set(value)

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
            type="primary",
            use_container_width=True,
        )

    if submitted:

        if not email.strip() or not password:

            st.error(
                "Please enter your email and password."
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
    st.session_state.get("user_role")
    or ROLE_VIEWER
)

user_role = str(user_role).lower()

can_edit = user_role in {
    ROLE_EDITOR,
    ROLE_ADMIN,
}

can_add = user_role == ROLE_ADMIN
can_delete = user_role == ROLE_ADMIN


# ============================================================
# DATABASE SCHEMA
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def get_table_columns_cached(
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

    return list(rows[0].keys())


try:

    supabase_url, _ = get_supabase_config()

    columns = get_table_columns_cached(
        supabase_url,
        TABLE_NAME,
    )

except Exception as exc:

    st.error(
        f"Unable to read database schema: {exc}"
    )

    st.stop()


if not columns:

    st.error(
        f"No columns were found in table `{TABLE_NAME}`."
    )

    st.stop()


# ============================================================
# IMPORTANT DATABASE COLUMNS
# ============================================================

db_columns = {

    "primary_key": resolve_column(
        columns,
        "PatientID",
        "patientid",
        "patient_id",
        "KEY",
        "key",
    ),

    "date": resolve_column(
        columns,
        "Date",
        "date",
        "DiagnosisDate",
        "diagnosis_date",
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
        "visitno",
        "Visit_no",
        "VisitNo",
        "visit_no",
    ),

    "sr_no": resolve_column(
        columns,
        "srno",
        "Sr_No",
        "SrNo",
        "sr_no",
    ),

    "ward_village": resolve_column(
        columns,
        "WardVillage",
        "Ward_Village",
        "WardVillageName",
        "ward_village",
    ),
}


PRIMARY_KEY = db_columns["primary_key"]


if not PRIMARY_KEY:

    st.error(
        "PatientID / primary key column could not be identified."
    )

    st.stop()


# ============================================================
# DATABASE FETCH HELPERS
# ============================================================

def fetch_all_rows(
    table_name,
    client,
    select_columns="*",
    order_column=None,
    descending=False,
):
    """
    Load all records in batches.

    BATCH_SIZE controls request size only.
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
# EDITOR FILTER VALUES
# ============================================================

FILTER_DEFINITIONS = {
    "patient_id": db_columns["primary_key"],
    "team": db_columns["team"],
    "tsp": db_columns["tsp"],
    "approach": db_columns["approach"],
    "case": db_columns["case"],
    "visit_no": db_columns["visit_no"],
    "sr_no": db_columns["sr_no"],
    "ward_village": db_columns["ward_village"],
}


def get_unique_filter_values(client):

    actual_columns = []

    for column in FILTER_DEFINITIONS.values():

        if column and column not in actual_columns:
            actual_columns.append(column)

    if not actual_columns:
        return {}

    rows = []
    start = 0

    select_string = ",".join(
        actual_columns
    )

    while True:

        end = start + BATCH_SIZE - 1

        response = (
            client
            .table(TABLE_NAME)
            .select(select_string)
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
        return {
            key: []
            for key in FILTER_DEFINITIONS
        }

    temp_df = pd.DataFrame(rows)

    result = {}

    for logical_name, actual_column in FILTER_DEFINITIONS.items():

        if not actual_column or actual_column not in temp_df.columns:

            result[logical_name] = []
            continue

        values = []

        for value in temp_df[actual_column].tolist():

            if is_null_like(value):
                continue

            value = clean_value(value)

            if value not in values:
                values.append(value)

        try:
            values.sort(
                key=lambda x: str(x).lower()
            )
        except Exception:
            pass

        result[logical_name] = values

    return result


def load_editor_filter_options():

    client = get_user_client()

    options = get_unique_filter_values(
        client
    )

    st.session_state.editor_filter_options = options
    st.session_state.editor_filter_options_loaded = True


# ============================================================
# EDITOR QUERY
# ============================================================

def build_editor_query(
    client,
    filters,
):

    query = (
        client
        .table(TABLE_NAME)
        .select("*")
    )

    # --------------------------------------------------------
    # Multiselect filters
    # --------------------------------------------------------

    for logical_name, actual_column in FILTER_DEFINITIONS.items():

        if not actual_column:
            continue

        selected_values = filters.get(
            logical_name,
            [],
        )

        if not selected_values:
            continue

        cleaned_values = [
            clean_value(value)
            for value in selected_values
            if not is_null_like(value)
        ]

        if cleaned_values:

            query = query.in_(
                actual_column,
                cleaned_values,
            )

    # --------------------------------------------------------
    # Date filter
    # --------------------------------------------------------

    date_column = db_columns["date"]

    date_from = filters.get("date_from")
    date_to = filters.get("date_to")

    if date_column:

        if date_from:

            query = query.gte(
                date_column,
                date_from.isoformat(),
            )

        if date_to:

            # Exclusive upper bound.
            next_day = (
                date_to
                + pd.Timedelta(days=1)
            ).date()

            query = query.lt(
                date_column,
                next_day.isoformat(),
            )

    # --------------------------------------------------------
    # Stable ordering
    # --------------------------------------------------------

    query = query.order(
        PRIMARY_KEY,
        desc=True,
    )

    return query


def load_editor_page():

    client = get_user_client()

    page = max(
        1,
        int(
            st.session_state.editor_page
        ),
    )

    page_size = int(
        st.session_state.editor_page_size
    )

    start = (
        page - 1
    ) * page_size

    # Request one extra record.
    end = start + page_size

    query = build_editor_query(
        client,
        st.session_state.editor_filters,
    )

    response = (
        query
        .range(start, end)
        .execute()
    )

    rows = response.data or []

    has_next = len(rows) > page_size

    if has_next:
        rows = rows[:page_size]

    df = pd.DataFrame(
        rows,
        columns=columns,
    )

    return df, has_next


# ============================================================
# PENDING CHANGES
# ============================================================

def add_pending_update(
    primary_id,
    changes,
):

    if is_null_like(primary_id):
        return

    primary_id = clean_value(primary_id)

    existing = st.session_state.pending_updates.get(
        primary_id,
        {},
    )

    existing.update(
        make_json_safe(changes)
    )

    st.session_state.pending_updates[
        primary_id
    ] = existing


def capture_editor_changes(
    source_df,
    edited_df,
    primary_key,
):

    if source_df is None or edited_df is None:
        return

    if primary_key not in source_df.columns:
        return

    if primary_key not in edited_df.columns:
        return

    source_lookup = {}

    for _, row in source_df.iterrows():

        primary_id = row.get(
            primary_key
        )

        if is_null_like(primary_id):
            continue

        source_lookup[
            clean_value(primary_id)
        ] = row

    for _, edited_row in edited_df.iterrows():

        primary_id = edited_row.get(
            primary_key
        )

        if is_null_like(primary_id):
            continue

        primary_id = clean_value(
            primary_id
        )

        if primary_id not in source_lookup:
            continue

        source_row = source_lookup[
            primary_id
        ]

        existing = st.session_state.pending_updates.get(
            primary_id,
            {},
        ).copy()

        # Compare every editable column.
        for column in edited_df.columns:

            if column == primary_key:
                continue

            if column not in source_df.columns:
                continue

            original_value = source_row.get(
                column
            )

            new_value = edited_row.get(
                column
            )

            # ------------------------------------------------
            # User changed value.
            # ------------------------------------------------

            if not values_equal(
                original_value,
                new_value,
            ):

                existing[column] = clean_value(
                    new_value
                )

            # ------------------------------------------------
            # User changed it back to original value.
            # Remove pending change.
            # ------------------------------------------------

            else:

                existing.pop(
                    column,
                    None,
                )

        if existing:

            st.session_state.pending_updates[
                primary_id
            ] = make_json_safe(existing)

        else:

            st.session_state.pending_updates.pop(
                primary_id,
                None,
            )


def capture_new_rows(
    edited_df,
    primary_key,
):

    if edited_df is None:
        return

    if primary_key not in edited_df.columns:
        return

    for _, row in edited_df.iterrows():

        primary_id = row.get(
            primary_key
        )

        # Blank primary key indicates a new row.
        if is_null_like(primary_id):
            continue

        primary_id = clean_value(
            primary_id
        )

        # If this is already a pending insert,
        # update its values.
        for index, pending_row in enumerate(
            st.session_state.pending_inserts
        ):

            existing_id = pending_row.get(
                primary_key
            )

            if values_equal(
                existing_id,
                primary_id,
            ):

                st.session_state.pending_inserts[
                    index
                ] = make_json_safe(
                    row.to_dict()
                )

                break


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
# SYNC PENDING CHANGES
# ============================================================

def sync_pending_changes():

    client = get_user_client()

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

        # Do not update records that are also pending deletion.
        if primary_id in st.session_state.pending_deletes:
            continue

        try:

            payload = make_json_safe(
                changes
            )

            (
                client
                .table(TABLE_NAME)
                .update(payload)
                .eq(
                    PRIMARY_KEY,
                    primary_id,
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

    for primary_id in successful_updates:

        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    for index, record in enumerate(
        list(
            st.session_state.pending_inserts
        )
    ):

        try:

            payload = make_json_safe(
                record
            )

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

    if successful_inserts:

        remaining_inserts = []

        for record in (
            st.session_state.pending_inserts
        ):

            if record not in successful_inserts:
                remaining_inserts.append(
                    record
                )

        st.session_state.pending_inserts = (
            remaining_inserts
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
                    PRIMARY_KEY,
                    primary_id,
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

    for primary_id in successful_deletes:

        st.session_state.pending_deletes.discard(
            primary_id
        )

        # IMPORTANT:
        # A record successfully deleted should
        # no longer have an UPDATE pending.
        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    return {
        "updates": len(successful_updates),
        "inserts": len(successful_inserts),
        "deletes": len(successful_deletes),
        "update_errors": update_errors,
        "insert_errors": insert_errors,
        "delete_errors": delete_errors,
    }


# ============================================================
# DELETE SELECTED ROWS
# ============================================================

def delete_selected_rows(
    selected_rows,
):

    if not can_delete:
        return

    if not selected_rows:
        return

    for row in selected_rows:

        primary_id = row.get(
            PRIMARY_KEY
        )

        if is_null_like(primary_id):
            continue

        primary_id = clean_value(
            primary_id
        )

        # Remove pending UPDATE.
        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

        # If this was a pending INSERT,
        # remove it instead of creating DELETE.
        removed_insert = False

        remaining_inserts = []

        for record in (
            st.session_state.pending_inserts
        ):

            record_id = record.get(
                PRIMARY_KEY
            )

            if values_equal(
                clean_value(record_id),
                primary_id,
            ):

                removed_insert = True

            else:

                remaining_inserts.append(
                    record
                )

        st.session_state.pending_inserts = (
            remaining_inserts
        )

        if not removed_insert:

            st.session_state.pending_deletes.add(
                primary_id
            )

    st.session_state.grid_version += 1


# ============================================================
# RESET EDITOR FILTERS
# ============================================================

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
        st.session_state[key] = []

    st.session_state.filter_date_from = None
    st.session_state.filter_date_to = None

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

    st.session_state.editor_page = 1

    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.filter_version += 1


# ============================================================
# APPLY EDITOR FILTERS
# ============================================================

def apply_editor_filters():

    st.session_state.editor_filters = {

        "patient_id": list(
            st.session_state.get(
                "filter_patient_id",
                [],
            )
        ),

        "team": list(
            st.session_state.get(
                "filter_team",
                [],
            )
        ),

        "tsp": list(
            st.session_state.get(
                "filter_tsp",
                [],
            )
        ),

        "approach": list(
            st.session_state.get(
                "filter_approach",
                [],
            )
        ),

        "case": list(
            st.session_state.get(
                "filter_case",
                [],
            )
        ),

        "visit_no": list(
            st.session_state.get(
                "filter_visit_no",
                [],
            )
        ),

        "sr_no": list(
            st.session_state.get(
                "filter_sr_no",
                [],
            )
        ),

        "ward_village": list(
            st.session_state.get(
                "filter_ward_village",
                [],
            )
        ),

        "date_from": st.session_state.get(
            "filter_date_from"
        ),

        "date_to": st.session_state.get(
            "filter_date_to"
        ),
    }

    # IMPORTANT:
    # This is a separate widget key.
    st.session_state.editor_page_size = int(
        st.session_state.get(
            "editor_page_size_selector",
            EDITOR_PAGE_SIZE_DEFAULT,
        )
    )

    st.session_state.editor_page = 1

    # Force a fresh DB snapshot.
    # Pending changes are deliberately NOT cleared.
    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.filter_version += 1


# ============================================================
# PAGE HEADER
# ============================================================

st.title("YgnTBPro Database")

header_col1, header_col2, header_col3 = st.columns(
    [5, 2, 1]
)

with header_col1:

    st.caption(
        f"Table: `{TABLE_NAME}`"
    )

with header_col2:

    role_label = user_role.upper()

    st.caption(
        f"Role: **{role_label}**"
    )

with header_col3:

    if st.button(
        "Logout",
        key="logout_button",
    ):
        logout_user()


# ============================================================
# PENDING CHANGES SUMMARY
# ============================================================

pending_count = pending_changes_count()

if pending_count:

    st.warning(
        f"Pending changes: {pending_count}"
    )


# ============================================================
# TABS
# ============================================================

tab_editor, tab_explorer = st.tabs(
    [
        "Database Editor",
        "Explorer",
    ]
)


# ============================================================
# DATABASE EDITOR
# ============================================================

with tab_editor:

    st.subheader("Database Editor")

    if not can_edit:

        st.info(
            "Viewer access: records can be viewed but cannot be edited."
        )

    # --------------------------------------------------------
    # FILTER LIST CONTROLS
    # --------------------------------------------------------

    list_col1, list_col2 = st.columns(2)

    with list_col1:

        if st.button(
            "Load Filter Lists",
            key="load_filter_lists",
        ):

            try:

                with st.spinner(
                    "Loading unique filter values..."
                ):

                    load_editor_filter_options()

                st.success(
                    "Filter lists loaded."
                )

            except Exception as exc:

                st.error(
                    f"Unable to load filter lists: {exc}"
                )

    with list_col2:

        if st.button(
            "Refresh Lists",
            key="refresh_filter_lists",
        ):

            try:

                with st.spinner(
                    "Refreshing unique values..."
                ):

                    load_editor_filter_options()

                st.success(
                    "Filter lists refreshed."
                )

            except Exception as exc:

                st.error(
                    f"Unable to refresh filter lists: {exc}"
                )

    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------

    options = (
        st.session_state.editor_filter_options
    )

    if (
        st.session_state.editor_filter_options_loaded
        and options
    ):

        st.markdown("### Filters")

        filter_col1, filter_col2 = st.columns(2)

        with filter_col1:

            st.multiselect(
                "Patient ID",
                options.get(
                    "patient_id",
                    [],
                ),
                key="filter_patient_id",
            )

            st.multiselect(
                "Team",
                options.get(
                    "team",
                    [],
                ),
                key="filter_team",
            )

            st.multiselect(
                "TSP",
                options.get(
                    "tsp",
                    [],
                ),
                key="filter_tsp",
            )

            st.multiselect(
                "Approach",
                options.get(
                    "approach",
                    [],
                ),
                key="filter_approach",
            )

        with filter_col2:

            st.multiselect(
                "Case",
                options.get(
                    "case",
                    [],
                ),
                key="filter_case",
            )

            st.multiselect(
                "Visit No",
                options.get(
                    "visit_no",
                    [],
                ),
                key="filter_visit_no",
            )

            st.multiselect(
                "SR No",
                options.get(
                    "sr_no",
                    [],
                ),
                key="filter_sr_no",
            )

            st.multiselect(
                "Ward / Village",
                options.get(
                    "ward_village",
                    [],
                ),
                key="filter_ward_village",
            )

        date_col1, date_col2 = st.columns(2)

        with date_col1:

            st.date_input(
                "Date From",
                value=None,
                key="filter_date_from",
            )

        with date_col2:

            st.date_input(
                "Date To",
                value=None,
                key="filter_date_to",
            )

        # ----------------------------------------------------
        # PAGE SIZE
        # ----------------------------------------------------

        page_size_options = [
            100,
            300,
            500,
            1000,
        ]

        current_page_size = st.session_state.get(
            "editor_page_size",
            EDITOR_PAGE_SIZE_DEFAULT,
        )

        if current_page_size not in page_size_options:
            current_page_size = (
                EDITOR_PAGE_SIZE_DEFAULT
            )

        # IMPORTANT:
        # This widget has a DIFFERENT key from
        # editor_page_size.
        st.selectbox(
            "Rows per page",
            page_size_options,
            index=page_size_options.index(
                current_page_size
            ),
            key="editor_page_size_selector",
        )

        button_col1, button_col2 = st.columns(2)

        with button_col1:

            st.button(
                "Apply Filters",
                type="primary",
                key="apply_editor_filters_button",
                on_click=apply_editor_filters,
            )

        with button_col2:

            st.button(
                "Reset Filters",
                key="reset_editor_filters_button",
                on_click=reset_editor_filters,
            )

    else:

        st.info(
            "Click 'Load Filter Lists' to load the unique filter values."
        )

    # --------------------------------------------------------
    # LOAD EDITOR DATA
    # --------------------------------------------------------

    if not st.session_state.editor_filter_options_loaded:

        st.stop()

    try:

        editor_df, editor_has_next = load_editor_page()

    except Exception as exc:

        st.error(
            f"Database Editor query failed: {exc}"
        )

        st.stop()

    # --------------------------------------------------------
    # Apply pending updates to displayed records.
    # --------------------------------------------------------

    if (
        not editor_df.empty
        and PRIMARY_KEY in editor_df.columns
    ):

        for row_index in editor_df.index:

            primary_id = editor_df.at[
                row_index,
                PRIMARY_KEY,
            ]

            if is_null_like(primary_id):
                continue

            primary_id = clean_value(
                primary_id
            )

            pending = (
                st.session_state
                .pending_updates
                .get(primary_id)
            )

            if pending:

                for column, value in pending.items():

                    if column in editor_df.columns:

                        editor_df.at[
                            row_index,
                            column,
                        ] = value

    # --------------------------------------------------------
    # Remove records pending deletion from grid.
    # --------------------------------------------------------

    if (
        not editor_df.empty
        and PRIMARY_KEY in editor_df.columns
        and st.session_state.pending_deletes
    ):

        delete_ids = (
            st.session_state.pending_deletes
        )

        editor_df = editor_df[
            ~editor_df[
                PRIMARY_KEY
            ].apply(
                lambda x: clean_value(x)
                in delete_ids
            )
        ].reset_index(
            drop=True
        )

    # --------------------------------------------------------
    # Source snapshot
    # --------------------------------------------------------

    filter_signature = repr(
        (
            st.session_state.editor_filters,
            st.session_state.editor_page,
            st.session_state.editor_page_size,
        )
    )

    if (
        st.session_state.editor_source_df is None
        or st.session_state.editor_source_key
        != filter_signature
    ):

        # Source must represent DB values BEFORE pending edits.
        source_df, _ = load_editor_page()

        st.session_state.editor_source_df = (
            source_df.copy()
        )

        st.session_state.editor_source_key = (
            filter_signature
        )

    # --------------------------------------------------------
    # Store displayed editor dataframe.
    # --------------------------------------------------------

    st.session_state.editor_df = (
        editor_df.copy()
    )

    # --------------------------------------------------------
    # Page information
    # --------------------------------------------------------

    current_page = int(
        st.session_state.editor_page
    )

    if editor_df.empty:

        st.info(
            "No records found for the selected filters."
        )

    else:

        start_record = (
            (current_page - 1)
            * st.session_state.editor_page_size
        ) + 1

        end_record = (
            start_record
            + len(editor_df)
            - 1
        )

        if editor_has_next:

            st.caption(
                f"Showing records "
                f"{start_record:,}–{end_record:,}. "
                f"More records are available."
            )

        else:

            st.caption(
                f"Showing records "
                f"{start_record:,}–{end_record:,}. "
                f"Last page."
            )

        # ----------------------------------------------------
        # AG GRID
        # ----------------------------------------------------

        gb = GridOptionsBuilder.from_dataframe(
            editor_df
        )

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
        # Column widths
        # ----------------------------------------------------

        for column in editor_df.columns:

            column_lower = str(
                column
            ).lower()

            width = 150
            min_width = 120

            if column == PRIMARY_KEY:

                width = 190
                min_width = 170

            elif column_lower in {
                "updated_at",
                "created_at",
                "timestamp",
            }:

                width = 200
                min_width = 180

            elif column_lower in {
                "team",
                "visitno",
                "visit_no",
                "visitno",
                "srno",
                "sr_no",
            }:

                width = 110
                min_width = 90

            elif column_lower in {
                "tsp",
                "approach",
                "case",
            }:

                width = 160
                min_width = 130

            elif (
                "ward" in column_lower
                or "village" in column_lower
            ):

                width = 200
                min_width = 160

            elif column_lower in {
                "date",
                "diagnosisdate",
                "diagnosis_date",
            }:

                width = 150
                min_width = 130

            elif (
                "name" in column_lower
                or "address" in column_lower
                or "remark" in column_lower
                or "reason" in column_lower
            ):

                width = 220
                min_width = 180

            gb.configure_column(
                column,
                width=width,
                minWidth=min_width,
                resizable=True,
            )

        # ----------------------------------------------------
        # Selection
        # ----------------------------------------------------

        gb.configure_selection(
            "multiple",
            use_checkbox=True,
        )

        gb.configure_grid_options(
            suppressRowClickSelection=True,
            enableRangeSelection=True,
            rowSelection="multiple",
            suppressColumnVirtualisation=False,
            suppressSizeToFit=True,
            enterNavigatesVertically=True,
            enterNavigatesVerticallyAfterEdit=True,
            getRowId=JsCode(
                f"""
                function(params) {{
                    return String(params.data["{PRIMARY_KEY}"]);
                }}
                """
            ),
        )

        grid_options = gb.build()

        grid_response = AgGrid(
            editor_df,
            gridOptions=grid_options,
            height=700,
            width="100%",
            data_return_mode=DataReturnMode.AS_INPUT,
            update_mode=(
                GridUpdateMode.VALUE_CHANGED
                | GridUpdateMode.SELECTION_CHANGED
            ),
            fit_columns_on_grid_load=False,
            reload_data=False,
            allow_unsafe_jscode=True,
            key=(
                f"ygntbpro_editor_"
                f"{st.session_state.grid_version}_"
                f"{st.session_state.filter_version}_"
                f"{current_page}"
            ),
        )

        # ----------------------------------------------------
        # Capture edits
        # ----------------------------------------------------

        returned_df = grid_response.get(
            "data"
        )

        if returned_df is not None:

            if not isinstance(
                returned_df,
                pd.DataFrame,
            ):

                returned_df = pd.DataFrame(
                    returned_df
                )

            capture_editor_changes(
                st.session_state.editor_source_df,
                returned_df,
                PRIMARY_KEY,
            )

            st.session_state.editor_df = (
                returned_df.copy()
            )

        # ----------------------------------------------------
        # Selected rows
        # ----------------------------------------------------

        selected_rows = grid_response.get(
            "selected_rows",
            [],
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
        # Delete Selected
        # ----------------------------------------------------

        if can_delete:

            if st.button(
                "Delete Selected",
                type="secondary",
                key=(
                    f"delete_selected_"
                    f"{current_page}"
                ),
            ):

                if not selected_rows:

                    st.warning(
                        "Please select at least one record."
                    )

                else:

                    delete_selected_rows(
                        selected_rows
                    )

                    st.success(
                        f"{len(selected_rows)} "
                        f"record(s) marked for deletion."
                    )

                    st.rerun()

    # --------------------------------------------------------
    # Editor pagination
    # --------------------------------------------------------

    st.markdown("### Editor Pagination")

    previous_col, page_col, next_col = st.columns(
        [1, 2, 1]
    )

    with previous_col:

        if st.button(
            "← Previous",
            disabled=current_page <= 1,
            key="editor_previous_page",
            use_container_width=True,
        ):

            st.session_state.editor_page = max(
                1,
                current_page - 1,
            )

            # Pending changes remain untouched.
            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1

            st.rerun()

    with page_col:

        if editor_has_next:

            st.markdown(
                f"""
                <div style="text-align:center;">
                    <b>Page {current_page}</b>
                </div>
                """,
                unsafe_allow_html=True,
            )

        else:

            st.markdown(
                f"""
                <div style="text-align:center;">
                    <b>Page {current_page}</b>
                    &nbsp; (last page)
                </div>
                """,
                unsafe_allow_html=True,
            )

    with next_col:

        if st.button(
            "Next →",
            disabled=not editor_has_next,
            key="editor_next_page",
            use_container_width=True,
        ):

            st.session_state.editor_page = (
                current_page + 1
            )

            # Pending changes remain untouched.
            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1

            st.rerun()


# ============================================================
# PENDING CHANGES
# ============================================================

if pending_changes_count():

    st.divider()

    st.subheader("Pending Changes")

    update_count = len(
        st.session_state.pending_updates
    )

    insert_count = len(
        st.session_state.pending_inserts
    )

    delete_count = len(
        st.session_state.pending_deletes
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Updates",
            update_count,
        )

    with col2:
        st.metric(
            "Inserts",
            insert_count,
        )

    with col3:
        st.metric(
            "Deletes",
            delete_count,
        )

    # --------------------------------------------------------
    # UPDATE PREVIEW
    # --------------------------------------------------------

    if update_count:

        with st.expander(
            "Pending Updates",
            expanded=False,
        ):

            update_rows = []

            for primary_id, changes in (
                st.session_state
                .pending_updates
                .items()
            ):

                for column, value in (
                    changes.items()
                ):

                    update_rows.append(
                        {
                            PRIMARY_KEY: primary_id,
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

    # --------------------------------------------------------
    # INSERT PREVIEW
    # --------------------------------------------------------

    if insert_count:

        with st.expander(
            "Pending Inserts",
            expanded=False,
        ):

            st.dataframe(
                pd.DataFrame(
                    st.session_state.pending_inserts
                ),
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # DELETE PREVIEW
    # --------------------------------------------------------

    if delete_count:

        with st.expander(
            "Pending Deletes",
            expanded=False,
        ):

            st.write(
                list(
                    st.session_state.pending_deletes
                )
            )

    sync_col, discard_col = st.columns(2)

    with sync_col:

        if st.button(
            "Sync Changes",
            type="primary",
            key="sync_pending_changes",
            use_container_width=True,
        ):

            with st.spinner(
                "Synchronizing changes..."
            ):

                result = (
                    sync_pending_changes()
                )

            total_success = (
                result["updates"]
                + result["inserts"]
                + result["deletes"]
            )

            if total_success:

                st.success(
                    "Sync completed: "
                    f"{result['updates']} update(s), "
                    f"{result['inserts']} insert(s), "
                    f"{result['deletes']} delete(s)."
                )

            all_errors = (
                result["update_errors"]
                + result["insert_errors"]
                + result["delete_errors"]
            )

            if all_errors:

                st.error(
                    "Some changes could not be synchronized."
                )

                for error in all_errors:
                    st.write(
                        f"- {error}"
                    )

            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1

            st.rerun()

    with discard_col:

        if st.button(
            "Discard All Pending Changes",
            key="discard_pending_changes",
            use_container_width=True,
        ):

            clear_pending_changes()

            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1

            st.success(
                "All pending changes were discarded."
            )

            st.rerun()


# ============================================================
# ADMIN: ADD NEW RECORD
# ============================================================

if can_add:

    st.divider()

    with st.expander(
        "Admin: Add New Record",
        expanded=False,
    ):

        st.caption(
            "Enter values for the new record. "
            "Blank fields are sent as NULL."
        )

        with st.form(
            "add_new_record_form"
        ):

            new_record = {}

            # Do not create dozens of unnecessary
            # widgets for columns that are normally
            # database-generated.
            skip_columns = {
                "created_at",
                "updated_at",
            }

            for column in columns:

                if column in skip_columns:
                    continue

                new_record[column] = st.text_input(
                    column,
                    key=f"new_record_{column}",
                )

            submitted = st.form_submit_button(
                "Add Record to Pending Changes",
                type="primary",
                use_container_width=True,
            )

        if submitted:

            cleaned_record = {}

            for column, value in (
                new_record.items()
            ):

                if (
                    isinstance(value, str)
                    and not value.strip()
                ):

                    cleaned_record[column] = None

                else:

                    cleaned_record[column] = (
                        clean_value(value)
                    )

            cleaned_record = make_json_safe(
                cleaned_record
            )

            st.session_state.pending_inserts.append(
                cleaned_record
            )

            st.success(
                "New record added to pending changes. "
                "Click 'Sync Changes' to insert it into Supabase."
            )

            st.rerun()


# ============================================================
# EXPLORER
# ============================================================

with tab_explorer:

    st.subheader("Database Explorer")

    # --------------------------------------------------------
    # Safe searchable columns
    #
    # ILIKE should NOT be applied blindly to numeric
    # PostgreSQL columns.
    # --------------------------------------------------------

    EXPLORER_TEXT_CANDIDATES = [

        db_columns["primary_key"],

        db_columns["tsp"],

        db_columns["approach"],

        db_columns["case"],

        db_columns["ward_village"],

        resolve_column(
            columns,
            "Reasonforexamination",
            "ReasonForExamination",
            "reasonforexamination",
        ),

        resolve_column(
            columns,
            "Treatmentreferral",
            "TreatmentReferral",
            "treatmentreferral",
        ),

        resolve_column(
            columns,
            "CXRresult",
            "Cxrresult",
            "cxrresult",
        ),

        resolve_column(
            columns,
            "GeneXpertresult",
            "Genexpertresult",
            "geneXpertresult",
        ),

        resolve_column(
            columns,
            "MonthDiagnosis11",
            "monthdiagnosis11",
        ),

        resolve_column(
            columns,
            "Gender",
            "gender",
            "Sex",
            "sex",
        ),

        resolve_column(
            columns,
            "Name",
            "name",
            "PatientName",
            "patient_name",
        ),

        resolve_column(
            columns,
            "Address",
            "address",
        ),

        resolve_column(
            columns,
            "Remark",
            "remark",
            "Remarks",
            "remarks",
        ),
    ]

    searchable_columns = []

    for column in EXPLORER_TEXT_CANDIDATES:

        if column and column in columns:

            if column not in searchable_columns:

                searchable_columns.append(
                    column
                )

    # --------------------------------------------------------
    # Explorer search controls
    # --------------------------------------------------------

    explorer_search_input = st.text_input(
        "Search",
        value=st.session_state.get(
            "explorer_search",
            "",
        ),
        key="explorer_search_input",
        placeholder="Enter keyword...",
    )

    explorer_default_columns = (
        st.session_state.get(
            "explorer_columns"
        )
        or searchable_columns
    )

    # Remove columns that no longer exist.
    explorer_default_columns = [
        column
        for column in explorer_default_columns
        if column in searchable_columns
    ]

    explorer_search_columns = st.multiselect(
        "Search in columns",
        options=searchable_columns,
        default=explorer_default_columns,
        key="explorer_search_columns",
    )

    explorer_button_col1, explorer_button_col2 = (
        st.columns(2)
    )

    def run_explorer_search():

        st.session_state.explorer_search = (
            st.session_state
            .explorer_search_input
            .strip()
        )

        st.session_state.explorer_columns = list(
            st.session_state
            .explorer_search_columns
        )

        # New search starts at page 1.
        st.session_state.explorer_page = 1
        st.session_state.explorer_loaded = True
        st.session_state.explorer_has_next = False


    def clear_explorer():

        st.session_state.explorer_search_input = ""
        st.session_state.explorer_search_columns = []

        st.session_state.explorer_search = ""
        st.session_state.explorer_columns = []

        st.session_state.explorer_page = 1
        st.session_state.explorer_loaded = False
        st.session_state.explorer_has_next = False


    with explorer_button_col1:

        st.button(
            "Search Explorer",
            type="primary",
            key="search_explorer_button",
            on_click=run_explorer_search,
            use_container_width=True,
        )

    with explorer_button_col2:

        st.button(
            "Clear Explorer",
            key="clear_explorer_button",
            on_click=clear_explorer,
            use_container_width=True,
        )

    # --------------------------------------------------------
    # Explorer query
    # --------------------------------------------------------

    def fetch_explorer_page(
        client,
        search_text,
        selected_columns,
        page,
        page_size,
    ):

        query = (
            client
            .table(TABLE_NAME)
            .select("*")
        )

        search_text = (
            str(search_text)
            .strip()
        )

        selected_columns = [
            column
            for column in selected_columns
            if column in searchable_columns
        ]

        if search_text and selected_columns:

            # PostgREST OR conditions cannot safely
            # contain commas. Replace them with spaces.
            safe_search = (
                search_text
                .replace("\\", " ")
                .replace(",", " ")
            )

            # Do not use numeric columns here.
            conditions = []

            for column in selected_columns:

                conditions.append(
                    f"{column}.ilike.*"
                    f"{safe_search}"
                    f"*"
                )

            if conditions:

                query = query.or_(
                    ",".join(conditions)
                )

        start = (
            max(1, page) - 1
        ) * page_size

        # Range is inclusive, so request page_size + 1.
        end = start + page_size

        response = (
            query
            .range(start, end)
            .execute()
        )

        rows = response.data or []

        has_next = (
            len(rows) > page_size
        )

        if has_next:
            rows = rows[:page_size]

        result_df = pd.DataFrame(
            rows,
            columns=columns,
        )

        return result_df, has_next


    # --------------------------------------------------------
    # Run Explorer
    # --------------------------------------------------------

    if st.session_state.explorer_loaded:

        search_text = (
            st.session_state.explorer_search
        )

        selected_columns = (
            st.session_state.explorer_columns
        )

        explorer_page = max(
            1,
            int(
                st.session_state.explorer_page
            ),
        )

        try:

            client = get_user_client()

            with st.spinner(
                "Loading Explorer data..."
            ):

                explorer_df, explorer_has_next = (
                    fetch_explorer_page(
                        client=client,
                        search_text=search_text,
                        selected_columns=selected_columns,
                        page=explorer_page,
                        page_size=EXPLORER_PAGE_SIZE,
                    )
                )

            st.session_state.explorer_has_next = (
                explorer_has_next
            )

            # ------------------------------------------------
            # Search description
            # ------------------------------------------------

            if search_text:

                st.caption(
                    f"Search: `{search_text}`"
                )

            if selected_columns:

                st.caption(
                    "Search columns: "
                    + ", ".join(
                        selected_columns
                    )
                )

            # ------------------------------------------------
            # Results
            # ------------------------------------------------

            if explorer_df.empty:

                st.info(
                    "No matching records found."
                )

            else:

                first_record = (
                    (
                        explorer_page - 1
                    )
                    * EXPLORER_PAGE_SIZE
                ) + 1

                last_record = (
                    first_record
                    + len(explorer_df)
                    - 1
                )

                if explorer_has_next:

                    st.caption(
                        f"Showing records "
                        f"{first_record:,}–"
                        f"{last_record:,}. "
                        f"More records are available."
                    )

                else:

                    st.caption(
                        f"Showing records "
                        f"{first_record:,}–"
                        f"{last_record:,}. "
                        f"Last page."
                    )

                st.dataframe(
                    explorer_df,
                    use_container_width=True,
                    height=650,
                    hide_index=True,
                )

                # ------------------------------------------------
                # Explorer pagination
                # ------------------------------------------------

                st.markdown(
                    "### Explorer Pagination"
                )

                prev_col, page_col, next_col = (
                    st.columns(
                        [1, 2, 1]
                    )
                )

                with prev_col:

                    st.button(
                        "← Previous",
                        disabled=(
                            explorer_page <= 1
                        ),
                        key=(
                            "explorer_previous_page"
                        ),
                        use_container_width=True,
                        on_click=lambda: (
                            st.session_state.update(
                                {
                                    "explorer_page":
                                        max(
                                            1,
                                            explorer_page
                                            - 1,
                                        )
                                }
                            )
                        ),
                    )

                with page_col:

                    if explorer_has_next:

                        st.markdown(
                            f"""
                            <div style="text-align:center;">
                                <b>Page {explorer_page}</b>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    else:

                        st.markdown(
                            f"""
                            <div style="text-align:center;">
                                <b>Page {explorer_page}</b>
                                &nbsp; (last page)
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                with next_col:

                    st.button(
                        "Next →",
                        disabled=(
                            not explorer_has_next
                        ),
                        key=(
                            "explorer_next_page"
                        ),
                        use_container_width=True,
                        on_click=lambda: (
                            st.session_state.update(
                                {
                                    "explorer_page":
                                        explorer_page
                                        + 1
                                }
                            )
                        ),
                    )

                # ------------------------------------------------
                # CSV export - current Explorer page
                # ------------------------------------------------

                csv_data = explorer_df.to_csv(
                    index=False
                ).encode(
                    "utf-8-sig"
                )

                st.download_button(
                    "Download Current Explorer Page",
                    data=csv_data,
                    file_name=(
                        f"{TABLE_NAME}_"
                        f"explorer_page_"
                        f"{explorer_page}.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                )

            # ------------------------------------------------
            # Explorer next-page status even when empty
            # ------------------------------------------------

            if (
                explorer_df.empty
                and explorer_page > 1
            ):

                st.info(
                    "There are no records on this page."
                )

        except Exception as exc:

            st.error(
                f"Explorer query failed: {exc}"
            )

    else:

        st.info(
            "Enter search criteria and click "
            "'Search Explorer'."
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

footer_col1, footer_col2, footer_col3 = st.columns(
    3
)

with footer_col1:

    st.caption(
        f"Table: {TABLE_NAME}"
    )

with footer_col2:

    st.caption(
        f"Role: {user_role.upper()}"
    )

with footer_col3:

    st.caption(
        f"Pending changes: "
        f"{pending_changes_count()}"
    )