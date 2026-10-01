# database.py
# ============================================================
# YgnTBPro - Supabase Database Editor / Explorer
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
USER_ROLES_TABLE = "user_roles"

BATCH_SIZE = 1000

EDITOR_PAGE_SIZE_OPTIONS = [100, 300, 500, 1000]
EXPLORER_PAGE_SIZE_OPTIONS = [100, 300, 500, 1000]

EDITOR_PAGE_SIZE_DEFAULT = 100
EXPLORER_PAGE_SIZE_DEFAULT = 100

PRIMARY_KEY_CANDIDATES = [
    "PatientID",
    "patientid",
    "patient_id",
]

DATE_CANDIDATES = [
    "Date",
    "date",
]

FILTER_CANDIDATES = {
    "patient_id": [
        "PatientID",
        "patientid",
        "patient_id",
    ],
    "team": [
        "Team",
        "team",
    ],
    "tsp": [
        "TSP",
        "tsp",
        "Tsp",
    ],
    "approach": [
        "Approach",
        "approach",
    ],
    "case": [
        "Case",
        "case",
    ],
    "visit_no": [
        "Visit_no",
        "VisitNo",
        "visit_no",
        "visitno",
    ],
    "sr_no": [
        "Sr_No",
        "SR_No",
        "SrNo",
        "sr_no",
        "srno",
    ],
    "ward_village": [
        "WardVillage",
        "Ward_Village",
        "ward_village",
        "wardvillage",
    ],
}


EXPLORER_TEXT_CANDIDATES = [
    "PatientID",
    "patientid",
    "patient_id",
    "TSP",
    "tsp",
    "Approach",
    "approach",
    "Case",
    "case",
    "WardVillage",
    "Ward_Village",
    "Reasonforexamination",
    "Treatmentreferral",
    "CXRresult",
    "GeneXpertresult",
    "MonthDiagnosis11",
    "Gender",
    "Name",
    "Address",
    "Remark",
]


# ============================================================
# SESSION DEFAULTS
# ============================================================

DEFAULTS = {
    "session": None,
    "user_role": None,

    # Editor
    "editor_page": 1,
    "editor_page_size": EDITOR_PAGE_SIZE_DEFAULT,
    "editor_page_size_selector": EDITOR_PAGE_SIZE_DEFAULT,

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

    "editor_source_df": None,
    "editor_source_key": None,

    # Persistent pending changes
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Grid
    "grid_version": 0,

    # Explorer
    "explorer_all_df": None,
    "explorer_page": 1,
    "explorer_page_size": EXPLORER_PAGE_SIZE_DEFAULT,
    "explorer_page_size_selector": EXPLORER_PAGE_SIZE_DEFAULT,
    "explorer_search": "",
    "explorer_search_columns": [],
}


for key, value in DEFAULTS.items():

    if key not in st.session_state:

        if isinstance(value, dict):
            st.session_state[key] = value.copy()

        elif isinstance(value, set):
            st.session_state[key] = set(value)

        else:
            st.session_state[key] = value


# ============================================================
# BASIC UTILITIES
# ============================================================

def resolve_column(columns, *candidates):
    """Return the first candidate that exists in columns."""

    column_set = set(columns)

    for candidate in candidates:

        if candidate in column_set:
            return candidate

    return None


def values_equal(left, right):
    """Safe comparison for None, NaN, timestamps, etc."""

    if left is None and right is None:
        return True

    try:

        if pd.isna(left) and pd.isna(right):
            return True

    except Exception:
        pass

    try:

        if isinstance(left, pd.Timestamp):
            left = left.to_pydatetime()

        if isinstance(right, pd.Timestamp):
            right = right.to_pydatetime()

    except Exception:
        pass

    try:
        return bool(left == right)

    except Exception:
        return str(left) == str(right)


def clean_value(value):
    """
    Convert Pandas / NumPy / Decimal / datetime values
    into JSON-safe Supabase values.
    """

    if value is None:
        return None

    if isinstance(value, pd.NA.__class__):
        return None

    try:

        if pd.isna(value):
            return None

    except Exception:
        pass

    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat()

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


def make_json_safe(data):
    """Convert a dict/list/DataFrame row into JSON-safe data."""

    if isinstance(data, pd.Series):
        data = data.to_dict()

    if isinstance(data, dict):

        return {
            str(key): clean_value(value)
            for key, value in data.items()
        }

    if isinstance(data, list):

        return [
            make_json_safe(item)
            for item in data
        ]

    return clean_value(data)


# ============================================================
# SUPABASE CLIENT
# ============================================================

def get_supabase_config():
    """
    Streamlit secrets first, then environment variables.
    """

    try:

        url = st.secrets.get(
            "SUPABASE_URL_ygntbpro",
            st.secrets.get("SUPABASE_URL", ""),
        )

        key = st.secrets.get(
            "SUPABASE_KEY_ygntbpro",
            st.secrets.get("SUPABASE_KEY", ""),
        )

    except Exception:

        url = os.getenv(
            "SUPABASE_URL_ygntbpro",
            os.getenv("SUPABASE_URL", ""),
        )

        key = os.getenv(
            "SUPABASE_KEY_ygntbpro",
            os.getenv("SUPABASE_KEY", ""),
        )

    return str(url).strip(), str(key).strip()


@st.cache_resource
def get_base_client() -> Client:

    url, key = get_supabase_config()

    if not url:
        raise RuntimeError(
            "SUPABASE_URL_ygntbpro / SUPABASE_URL is not configured."
        )

    if not key:
        raise RuntimeError(
            "SUPABASE_KEY_ygntbpro / SUPABASE_KEY is not configured."
        )

    return create_client(url, key)


def get_user_client() -> Client:
    """
    Create an authenticated Supabase client for the currently
    logged-in user.

    All database operations therefore use the user's JWT and
    Supabase RLS policies.
    """

    session = st.session_state.get("session")

    if not session:
        raise RuntimeError("No authenticated session.")

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

            if refresh_token:
                client.auth.set_session(
                    access_token,
                    refresh_token,
                )

            else:

                # Fallback for environments where refresh token
                # is unavailable.
                client.postgrest.auth(access_token)

        except Exception:

            try:
                client.postgrest.auth(access_token)

            except Exception:
                pass

    return client


# ============================================================
# LOGIN / LOGOUT
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

        role_result = (
            user_client
            .table(USER_ROLES_TABLE)
            .select("role")
            .eq(
                "user_id",
                response.session.user.id,
            )
            .limit(1)
            .execute()
        )

        if role_result.data:

            role = role_result.data[0].get(
                "role",
                "viewer",
            )

        else:

            role = "viewer"

        role = str(role).lower().strip()

        if role not in {
            "viewer",
            "editor",
            "admin",
        }:
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

        elif isinstance(value, set):
            st.session_state[key] = set(value)

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
            placeholder="Enter your email",
        )

        password = st.text_input(
            "Password",
            type="password",
        )

        login_clicked = st.form_submit_button(
            "Login",
            type="primary",
            use_container_width=True,
        )

    if login_clicked:

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
).lower()

can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"
can_delete = user_role == "admin"


# ============================================================
# HEADER
# ============================================================

header_col1, header_col2, header_col3 = st.columns(
    [5, 2, 1]
)

with header_col1:

    st.title("🗄️ YgnTBPro Database")

with header_col2:

    st.info(
        f"Role: **{user_role.upper()}**"
    )

with header_col3:

    st.button(
        "Logout",
        on_click=logout_user,
        use_container_width=True,
    )


# ============================================================
# TABLE INFORMATION
# ============================================================

client = get_user_client()


@st.cache_data(ttl=600, show_spinner=False)
def get_table_columns_cached():

    base_client = get_base_client()

    try:

        result = (
            base_client
            .table(TABLE_NAME)
            .select("*")
            .limit(1)
            .execute()
        )

        rows = result.data or []

        if rows:
            return list(rows[0].keys())

    except Exception:
        pass

    return []


TABLE_COLUMNS = get_table_columns_cached()

if not TABLE_COLUMNS:

    st.error(
        f"Unable to determine columns for `{TABLE_NAME}`."
    )

    st.stop()


PRIMARY_KEY = resolve_column(
    TABLE_COLUMNS,
    *PRIMARY_KEY_CANDIDATES,
)

DATE_COLUMN = resolve_column(
    TABLE_COLUMNS,
    *DATE_CANDIDATES,
)


if not PRIMARY_KEY:

    st.error(
        "Primary key column could not be found. "
        "Expected one of: "
        + ", ".join(PRIMARY_KEY_CANDIDATES)
    )

    st.stop()


# ============================================================
# COLUMN MAP
# ============================================================

FILTER_COLUMNS = {}

for filter_name, candidates in FILTER_CANDIDATES.items():

    FILTER_COLUMNS[filter_name] = resolve_column(
        TABLE_COLUMNS,
        *candidates,
    )


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
    Fetch ALL RLS-visible rows in batches.

    BATCH_SIZE=1000 is only the request size.
    It is NOT a maximum record limit.
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
    batch_size=BATCH_SIZE,
):

    rows = []
    start = 0

    while True:

        end = start + batch_size - 1

        response = (
            query_builder(
                start,
                end,
            )
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
# PENDING CHANGES
# ============================================================

def add_pending_update(
    primary_id,
    changes,
):

    primary_id = clean_value(primary_id)

    if primary_id is None:
        return

    existing = (
        st.session_state.pending_updates
        .get(primary_id, {})
        .copy()
    )

    for column, value in changes.items():

        if column == PRIMARY_KEY:
            continue

        existing[column] = clean_value(value)

    if existing:

        st.session_state.pending_updates[
            primary_id
        ] = existing

    else:

        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )


def capture_editor_changes(
    source_df,
    edited_df,
):

    if source_df is None or edited_df is None:
        return

    if source_df.empty or edited_df.empty:
        return

    if PRIMARY_KEY not in source_df.columns:
        return

    if PRIMARY_KEY not in edited_df.columns:
        return

    source_by_id = {}

    for _, row in source_df.iterrows():

        primary_id = clean_value(
            row.get(PRIMARY_KEY)
        )

        if primary_id is not None:

            source_by_id[primary_id] = row

    for _, new_row in edited_df.iterrows():

        primary_id = clean_value(
            new_row.get(PRIMARY_KEY)
        )

        if primary_id not in source_by_id:
            continue

        original = source_by_id[primary_id]

        existing = (
            st.session_state.pending_updates
            .get(primary_id, {})
            .copy()
        )

        for column in edited_df.columns:

            if column == PRIMARY_KEY:
                continue

            if column not in source_df.columns:
                continue

            original_value = original.get(
                column
            )

            new_value = new_row.get(
                column
            )

            if not values_equal(
                original_value,
                new_value,
            ):

                existing[column] = clean_value(
                    new_value
                )

            else:

                # Important:
                # if user changes a value back to
                # the original value, remove it
                # from pending changes.
                existing.pop(
                    column,
                    None,
                )

        if existing:

            st.session_state.pending_updates[
                primary_id
            ] = existing

        else:

            st.session_state.pending_updates.pop(
                primary_id,
                None,
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
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


# ============================================================
# APPLY PENDING UPDATES TO DATAFRAME
# ============================================================

def apply_pending_changes_to_df(df):

    if df is None or df.empty:
        return df

    result = df.copy()

    if PRIMARY_KEY not in result.columns:
        return result

    # --------------------------------------------------------
    # Pending deletes
    # --------------------------------------------------------

    deleted_ids = {
        clean_value(value)
        for value in st.session_state.pending_deletes
    }

    if deleted_ids:

        keep_mask = ~result[
            PRIMARY_KEY
        ].map(
            lambda value:
            clean_value(value) in deleted_ids
        )

        result = result.loc[
            keep_mask
        ].copy()

    # --------------------------------------------------------
    # Pending updates
    # --------------------------------------------------------

    for primary_id, changes in (
        st.session_state.pending_updates.items()
    ):

        mask = result[
            PRIMARY_KEY
        ].map(
            lambda value:
            values_equal(
                clean_value(value),
                clean_value(primary_id),
            )
        )

        for column, value in changes.items():

            if column in result.columns:

                result.loc[
                    mask,
                    column
                ] = value

    return result


# ============================================================
# EDITOR FILTER OPTIONS
# ============================================================

def load_editor_filter_options():

    columns_to_select = []

    for column in FILTER_COLUMNS.values():

        if column and column not in columns_to_select:

            columns_to_select.append(column)

    if not columns_to_select:
        return {}

    select_string = ",".join(
        columns_to_select
    )

    query = (
        client
        .table(TABLE_NAME)
        .select(select_string)
    )

    df = fetch_all_from_query(
        lambda start, end:
        query.range(start, end)
    )

    options = {}

    for filter_name, column in FILTER_COLUMNS.items():

        if not column or column not in df.columns:

            options[filter_name] = []
            continue

        values = []

        for value in df[column].tolist():

            value = clean_value(value)

            if value is not None:

                values.append(value)

        # Convert values to strings for
        # Streamlit multiselect stability.
        unique_values = sorted(
            {
                str(value)
                for value in values
            },
            key=lambda value: value.lower(),
        )

        options[filter_name] = unique_values

    return options


def ensure_editor_filter_options():

    if not st.session_state.editor_filter_options_loaded:

        with st.spinner(
            "Loading filter values..."
        ):

            options = load_editor_filter_options()

        st.session_state.editor_filter_options = options

        st.session_state.editor_filter_options_loaded = True


# ============================================================
# EDITOR FILTER CALLBACKS
# ============================================================

def apply_editor_filters():

    st.session_state.editor_filters = {

        "patient_id":
            st.session_state.get(
                "editor_patient_id_filter",
                [],
            ),

        "team":
            st.session_state.get(
                "editor_team_filter",
                [],
            ),

        "tsp":
            st.session_state.get(
                "editor_tsp_filter",
                [],
            ),

        "approach":
            st.session_state.get(
                "editor_approach_filter",
                [],
            ),

        "case":
            st.session_state.get(
                "editor_case_filter",
                [],
            ),

        "visit_no":
            st.session_state.get(
                "editor_visit_no_filter",
                [],
            ),

        "sr_no":
            st.session_state.get(
                "editor_sr_no_filter",
                [],
            ),

        "ward_village":
            st.session_state.get(
                "editor_ward_village_filter",
                [],
            ),

        "date_from":
            st.session_state.get(
                "editor_date_from_filter"
            ),

        "date_to":
            st.session_state.get(
                "editor_date_to_filter"
            ),
    }

    st.session_state.editor_page_size = (
        st.session_state.get(
            "editor_page_size_selector",
            EDITOR_PAGE_SIZE_DEFAULT,
        )
    )

    st.session_state.editor_page = 1

    # Force new page snapshot.
    # IMPORTANT: pending changes are NOT cleared.
    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


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

    # Reset widget values through callback.
    st.session_state.editor_patient_id_filter = []
    st.session_state.editor_team_filter = []
    st.session_state.editor_tsp_filter = []
    st.session_state.editor_approach_filter = []
    st.session_state.editor_case_filter = []
    st.session_state.editor_visit_no_filter = []
    st.session_state.editor_sr_no_filter = []
    st.session_state.editor_ward_village_filter = []

    st.session_state.editor_date_from_filter = None
    st.session_state.editor_date_to_filter = None

    st.session_state.editor_page = 1

    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


def editor_previous_page():

    st.session_state.editor_page = max(
        1,
        st.session_state.editor_page - 1,
    )

    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


def editor_next_page():

    st.session_state.editor_page += 1

    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


# ============================================================
# BUILD EDITOR QUERY
# ============================================================

def build_editor_query():

    query = (
        client
        .table(TABLE_NAME)
        .select("*")
    )

    filters = (
        st.session_state.editor_filters
    )

    # --------------------------------------------------------
    # Patient ID
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "patient_id"
    )

    values = filters.get(
        "patient_id",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # Team
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "team"
    )

    values = filters.get(
        "team",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # TSP
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "tsp"
    )

    values = filters.get(
        "tsp",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # Approach
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "approach"
    )

    values = filters.get(
        "approach",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # Case
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "case"
    )

    values = filters.get(
        "case",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # Visit No
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "visit_no"
    )

    values = filters.get(
        "visit_no",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # SR No
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "sr_no"
    )

    values = filters.get(
        "sr_no",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # Ward/Village
    # --------------------------------------------------------

    column = FILTER_COLUMNS.get(
        "ward_village"
    )

    values = filters.get(
        "ward_village",
        [],
    )

    if column and values:

        query = query.in_(
            column,
            values,
        )

    # --------------------------------------------------------
    # Date From
    # --------------------------------------------------------

    date_from = filters.get(
        "date_from"
    )

    if DATE_COLUMN and date_from:

        query = query.gte(
            DATE_COLUMN,
            date_from.isoformat(),
        )

    # --------------------------------------------------------
    # Date To
    # --------------------------------------------------------

    date_to = filters.get(
        "date_to"
    )

    if DATE_COLUMN and date_to:

        next_day = (
            date_to + timedelta(days=1)
        )

        query = query.lt(
            DATE_COLUMN,
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


# ============================================================
# LOAD CURRENT EDITOR PAGE
# ============================================================

def load_editor_page():

    page = st.session_state.editor_page

    page_size = (
        st.session_state.editor_page_size
    )

    start = (
        page - 1
    ) * page_size

    end = (
        start + page_size
    )

    # Inclusive Supabase range.
    # Request one extra record to determine
    # whether another page exists.
    query = build_editor_query()

    response = (
        query
        .range(
            start,
            end,
        )
        .execute()
    )

    rows = response.data or []

    has_next = len(rows) > page_size

    rows = rows[:page_size]

    df = pd.DataFrame(rows)

    if df.empty:
        return df, False

    df = apply_pending_changes_to_df(
        df
    )

    return df, has_next


# ============================================================
# GRID OPTIONS
# ============================================================

def build_grid_options(
    df,
    editable=False,
    allow_selection=False,
):

    gb = GridOptionsBuilder.from_dataframe(
        df
    )

    gb.configure_default_column(
        editable=editable,
        resizable=True,
        sortable=True,
        filter=True,
        minWidth=120,
        flex=0,
        wrapText=False,
        autoHeight=False,
    )

    for column in df.columns:

        lower = str(column).lower()

        # ----------------------------------------------------
        # Patient ID
        # ----------------------------------------------------

        if lower in {
            "patientid",
            "patient_id",
        }:

            gb.configure_column(
                column,
                width=190,
                minWidth=170,
            )

        # ----------------------------------------------------
        # Updated / Created
        # ----------------------------------------------------

        elif lower in {
            "updated_at",
            "created_at",
        }:

            gb.configure_column(
                column,
                width=200,
                minWidth=180,
            )

        # ----------------------------------------------------
        # Numeric identifiers
        # ----------------------------------------------------

        elif lower in {
            "team",
            "visit_no",
            "visitno",
            "sr_no",
            "srno",
        }:

            gb.configure_column(
                column,
                width=110,
                minWidth=90,
            )

        # ----------------------------------------------------
        # Common categorical columns
        # ----------------------------------------------------

        elif lower in {
            "tsp",
            "approach",
            "case",
        }:

            gb.configure_column(
                column,
                width=160,
                minWidth=130,
            )

        # ----------------------------------------------------
        # Ward/Village
        # ----------------------------------------------------

        elif lower in {
            "wardvillage",
            "ward_village",
        }:

            gb.configure_column(
                column,
                width=200,
                minWidth=160,
            )

        # ----------------------------------------------------
        # Date
        # ----------------------------------------------------

        elif lower == "date":

            gb.configure_column(
                column,
                width=150,
                minWidth=130,
            )

        # ----------------------------------------------------
        # Text fields
        # ----------------------------------------------------

        elif any(
            term in lower
            for term in [
                "name",
                "address",
                "remark",
                "reason",
            ]
        ):

            gb.configure_column(
                column,
                width=220,
                minWidth=180,
            )

        else:

            gb.configure_column(
                column,
                width=150,
                minWidth=120,
            )

    if allow_selection:

        gb.configure_selection(
            selection_mode="multiple",
            use_checkbox=True,
            suppressRowClickSelection=False,
        )

    gb.configure_grid_options(
        suppressSizeToFit=True,
        pagination=False,
        animateRows=False,
    )

    return gb.build()


# ============================================================
# EDITOR GRID CALLBACK
# ============================================================

def capture_editor_grid_response(
    grid_response,
    source_df,
):

    if not grid_response:
        return

    edited_df = grid_response.get(
        "data"
    )

    if edited_df is None:
        return

    if not isinstance(
        edited_df,
        pd.DataFrame,
    ):

        try:

            edited_df = pd.DataFrame(
                edited_df
            )

        except Exception:
            return

    capture_editor_changes(
        source_df,
        edited_df,
    )


# ============================================================
# DELETE SELECTED
# ============================================================

def get_selected_rows(
    grid_response,
):

    if not grid_response:
        return []

    selected = grid_response.get(
        "selected_rows",
        [],
    )

    if selected is None:
        return []

    if isinstance(
        selected,
        pd.DataFrame,
    ):

        return selected.to_dict(
            orient="records"
        )

    if isinstance(
        selected,
        list,
    ):

        return selected

    return []


def mark_selected_for_delete(
    grid_response,
):

    if not can_delete:
        return

    selected_rows = get_selected_rows(
        grid_response
    )

    if not selected_rows:
        return

    for row in selected_rows:

        primary_id = clean_value(
            row.get(PRIMARY_KEY)
        )

        if primary_id is None:
            continue

        st.session_state.pending_deletes.add(
            primary_id
        )

        # If a row is deleted, its pending
        # update must no longer be applied.
        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


# ============================================================
# INSERT NEW RECORD
# ============================================================

def show_add_record_form():

    if not can_add:
        return

    st.markdown("---")

    st.subheader("➕ Add New Record")

    editable_columns = [
        column
        for column in TABLE_COLUMNS
        if column not in {
            "created_at",
            "updated_at",
        }
    ]

    with st.form("add_new_record_form"):

        form_values = {}

        # Four columns for a more compact form.
        form_columns = st.columns(4)

        for index, column in enumerate(
            editable_columns
        ):

            with form_columns[
                index % 4
            ]:

                form_values[column] = (
                    st.text_input(
                        column,
                        key=f"new_record_{column}",
                    )
                )

        add_clicked = st.form_submit_button(
            "Add to Pending INSERT",
            type="primary",
            use_container_width=True,
        )

    if add_clicked:

        new_record = {}

        for column, value in form_values.items():

            if value is None:
                continue

            value = str(value)

            if value == "":
                continue

            new_record[column] = value

        if not new_record:

            st.warning(
                "Please enter at least one value."
            )

        else:

            st.session_state.pending_inserts.append(
                make_json_safe(
                    new_record
                )
            )

            st.success(
                "New record added to pending INSERT."
            )

            st.rerun()


# ============================================================
# PENDING CHANGES DISPLAY
# ============================================================

def build_pending_changes_df():

    rows = []

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for primary_id, changes in (
        st.session_state.pending_updates.items()
    ):

        row = {
            "Action": "UPDATE",
            PRIMARY_KEY: primary_id,
        }

        row.update(changes)

        rows.append(row)

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    for record in (
        st.session_state.pending_inserts
    ):

        row = {
            "Action": "INSERT",
        }

        row.update(record)

        rows.append(row)

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    for primary_id in (
        st.session_state.pending_deletes
    ):

        rows.append(
            {
                "Action": "DELETE",
                PRIMARY_KEY: primary_id,
            }
        )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


# ============================================================
# SYNC CHANGES
# ============================================================

def sync_pending_changes():

    sync_client = get_user_client()

    errors = []

    success_updates = 0
    success_inserts = 0
    success_deletes = 0

    # ========================================================
    # UPDATE
    # ========================================================

    for primary_id, changes in list(
        st.session_state.pending_updates.items()
    ):

        if not changes:
            continue

        update_data = {}

        for column, value in changes.items():

            if column == PRIMARY_KEY:
                continue

            # Let database handle timestamp.
            if column == "updated_at":
                continue

            update_data[column] = clean_value(
                value
            )

        if not update_data:
            continue

        try:

            response = (
                sync_client
                .table(TABLE_NAME)
                .update(update_data)
                .eq(
                    PRIMARY_KEY,
                    clean_value(primary_id),
                )
                .execute()
            )

            if response.data is not None:

                success_updates += 1

                st.session_state.pending_updates.pop(
                    primary_id,
                    None,
                )

        except Exception as exc:

            errors.append(
                f"UPDATE {primary_id}: {exc}"
            )

    # ========================================================
    # INSERT
    # ========================================================

    remaining_inserts = []

    for record in list(
        st.session_state.pending_inserts
    ):

        insert_data = {}

        for column, value in record.items():

            if column in {
                "created_at",
                "updated_at",
            }:
                continue

            insert_data[column] = clean_value(
                value
            )

        if not insert_data:
            continue

        try:

            response = (
                sync_client
                .table(TABLE_NAME)
                .insert(insert_data)
                .execute()
            )

            if response.data is not None:

                success_inserts += 1

            else:

                remaining_inserts.append(
                    record
                )

        except Exception as exc:

            remaining_inserts.append(
                record
            )

            errors.append(
                f"INSERT: {exc}"
            )

    st.session_state.pending_inserts = (
        remaining_inserts
    )

    # ========================================================
    # DELETE
    # ========================================================

    remaining_deletes = set()

    for primary_id in list(
        st.session_state.pending_deletes
    ):

        try:

            response = (
                sync_client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    PRIMARY_KEY,
                    clean_value(primary_id),
                )
                .execute()
            )

            if response.data is not None:

                success_deletes += 1

            else:

                # Keep pending if Supabase
                # returned no successful data.
                remaining_deletes.add(
                    primary_id
                )

        except Exception as exc:

            remaining_deletes.add(
                primary_id
            )

            errors.append(
                f"DELETE {primary_id}: {exc}"
            )

    st.session_state.pending_deletes = (
        remaining_deletes
    )

    # Force data reload after successful sync.
    st.session_state.editor_source_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1

    return (
        success_updates,
        success_inserts,
        success_deletes,
        errors,
    )


# ============================================================
# EXPLORER
# ============================================================

def load_all_explorer_data():

    explorer_client = get_user_client()

    return fetch_all_rows(
        TABLE_NAME,
        explorer_client,
        select_columns="*",
        order_column=PRIMARY_KEY,
        descending=True,
    )


# def explorer_previous_page():

#     st.session_state.explorer_page = max(
#         1,
#         st.session_state.explorer_page - 1,
#     )


# def explorer_next_page():

#     st.session_state.explorer_page += 1


def filter_explorer_locally(
    df,
    search_text,
    search_columns,
):

    if df is None or df.empty:
        return df

    if not search_text:
        return df

    if not search_columns:
        return df.iloc[0:0].copy()

    mask = pd.Series(
        False,
        index=df.index,
    )

    search_value = str(
        search_text
    ).strip()

    if not search_value:
        return df

    for column in search_columns:

        if column not in df.columns:
            continue

        values = (
            df[column]
            .astype("string")
            .fillna("")
        )

        mask |= values.str.contains(
            search_value,
            case=False,
            regex=False,
            na=False,
        )

    return df.loc[
        mask
    ].copy()


# ============================================================
# TABS
# ============================================================

tab_editor, tab_explorer = st.tabs(
    [
        "✏️ Database Editor",
        "🔎 Explorer",
    ]
)


# ============================================================
# DATABASE EDITOR
# ============================================================

with tab_editor:

    st.subheader("Database Editor")

    # --------------------------------------------------------
    # Load filter options
    # --------------------------------------------------------

    ensure_editor_filter_options()

    filter_options = (
        st.session_state.editor_filter_options
    )

    # --------------------------------------------------------
    # ROW 1
    # Patient ID FIRST
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        st.multiselect(
            "Patient ID",
            options=filter_options.get(
                "patient_id",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "patient_id",
                [],
            ),
            key="editor_patient_id_filter",
        )

    with c2:

        st.multiselect(
            "Team",
            options=filter_options.get(
                "team",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "team",
                [],
            ),
            key="editor_team_filter",
        )

    with c3:

        st.multiselect(
            "TSP",
            options=filter_options.get(
                "tsp",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "tsp",
                [],
            ),
            key="editor_tsp_filter",
        )

    with c4:

        st.multiselect(
            "Approach",
            options=filter_options.get(
                "approach",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "approach",
                [],
            ),
            key="editor_approach_filter",
        )

    # --------------------------------------------------------
    # ROW 2
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        st.multiselect(
            "Case",
            options=filter_options.get(
                "case",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "case",
                [],
            ),
            key="editor_case_filter",
        )

    with c2:

        st.multiselect(
            "Visit No",
            options=filter_options.get(
                "visit_no",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "visit_no",
                [],
            ),
            key="editor_visit_no_filter",
        )

    with c3:

        st.multiselect(
            "SR No",
            options=filter_options.get(
                "sr_no",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "sr_no",
                [],
            ),
            key="editor_sr_no_filter",
        )

    with c4:

        st.multiselect(
            "Ward/Village",
            options=filter_options.get(
                "ward_village",
                [],
            ),
            default=st.session_state.editor_filters.get(
                "ward_village",
                [],
            ),
            key="editor_ward_village_filter",
        )

    # --------------------------------------------------------
    # ROW 3
    # Date + Rows per page
    # --------------------------------------------------------

    c1, c2, c3 = st.columns(
        [1, 1, 1]
    )

    with c1:

        st.date_input(
            "Date From",
            value=st.session_state.editor_filters.get(
                "date_from"
            ),
            key="editor_date_from_filter",
        )

    with c2:

        st.date_input(
            "Date To",
            value=st.session_state.editor_filters.get(
                "date_to"
            ),
            key="editor_date_to_filter",
        )

    with c3:

        st.selectbox(
            "Rows per page",
            options=EDITOR_PAGE_SIZE_OPTIONS,
            index=EDITOR_PAGE_SIZE_OPTIONS.index(
                st.session_state.editor_page_size
            ),
            key="editor_page_size_selector",
        )

    # --------------------------------------------------------
    # ROW 4 — Buttons
    # --------------------------------------------------------

    b1, b2 = st.columns(
        2
    )

    with b1:

        st.button(
            "Apply Filters",
            type="primary",
            use_container_width=True,
            on_click=apply_editor_filters,
        )

    with b2:

        st.button(
            "Reset Filters",
            use_container_width=True,
            on_click=reset_editor_filters,
        )

    st.markdown("---")

    # --------------------------------------------------------
    # Load current page
    # --------------------------------------------------------

    current_filter_key = (
        repr(
            st.session_state.editor_filters
        )
        + f"|page={st.session_state.editor_page}"
        + f"|size={st.session_state.editor_page_size}"
    )

    if (
        st.session_state.editor_source_df is None
        or st.session_state.editor_source_key
        != current_filter_key
    ):

        with st.spinner(
            "Loading database records..."
        ):

            page_df, has_next = (
                load_editor_page()
            )

        st.session_state.editor_source_df = (
            page_df.copy()
        )

        st.session_state.editor_source_key = (
            current_filter_key
        )

    else:

        page_df = (
            st.session_state.editor_source_df
            .copy()
        )

        # Determine next-page status again.
        page_number = (
            st.session_state.editor_page
        )

        page_size = (
            st.session_state.editor_page_size
        )

        query = build_editor_query()

        response = (
            query
            .range(
                page_number * page_size,
                page_number * page_size,
            )
            .execute()
        )

        has_next = bool(
            response.data
        )

    # --------------------------------------------------------
    # Page information
    # --------------------------------------------------------

    if page_df.empty:

        st.info(
            "No records match the selected filters."
        )

    else:

        page_number = (
            st.session_state.editor_page
        )

        page_size = (
            st.session_state.editor_page_size
        )

        start_number = (
            (page_number - 1)
            * page_size
            + 1
        )

        end_number = (
            start_number
            + len(page_df)
            - 1
        )

        pending_count = (
            pending_changes_count()
        )

        st.caption(
            f"Showing records "
            f"{start_number:,}–{end_number:,}"
            f"  •  Page {page_number:,}"
            f"  •  Pending changes: "
            f"{pending_count:,}"
        )

        # ----------------------------------------------------
        # Database Editor AG Grid
        # ----------------------------------------------------

        grid_options = build_grid_options(
            page_df,
            editable=can_edit,
            allow_selection=can_delete,
        )

        grid_response = AgGrid(
            page_df,
            gridOptions=grid_options,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            update_mode=(
                GridUpdateMode.VALUE_CHANGED
                | GridUpdateMode.SELECTION_CHANGED
            ),
            fit_columns_on_grid_load=False,
            allow_unsafe_jscode=True,
            theme="streamlit",
            height=650,
            key=(
                f"editor_grid_"
                f"{st.session_state.grid_version}_"
                f"{page_number}_"
                f"{len(page_df)}"
            ),
        )

        # ----------------------------------------------------
        # Capture edits
        # ----------------------------------------------------

        if can_edit:

            capture_editor_grid_response(
                grid_response,
                page_df,
            )

        # ----------------------------------------------------
        # Delete Selected button
        # ----------------------------------------------------

        if can_delete:

            selected_rows = (
                get_selected_rows(
                    grid_response
                )
            )

            delete_col1, delete_col2 = (
                st.columns([1, 4])
            )

            with delete_col1:

                delete_clicked = st.button(
                    "🗑️ Delete Selected",
                    disabled=not bool(
                        selected_rows
                    ),
                    use_container_width=True,
                )

            with delete_col2:

                if selected_rows:

                    st.caption(
                        f"{len(selected_rows):,} "
                        "row(s) selected."
                    )

            if delete_clicked:

                mark_selected_for_delete(
                    grid_response
                )

                st.rerun()

        # ----------------------------------------------------
        # Pagination
        # ----------------------------------------------------

        st.markdown("")

        # p1, p2, p3 = st.columns(
        #     [1, 1, 4]
        # )

        # with p1:

        #     st.button(
        #         "← Previous",
        #         disabled=(
        #             st.session_state.editor_page
        #             <= 1
        #         ),
        #         use_container_width=True,
        #         on_click=editor_previous_page,
        #     )

        # with p2:

        #     st.button(
        #         "Next →",
        #         disabled=not has_next,
        #         use_container_width=True,
        #         on_click=editor_next_page,
        #     )

        # with p3:

        #     if has_next:

        #         st.caption(
        #             f"Page "
        #             f"{st.session_state.editor_page:,}"
        #             " — more records available"
        #         )

        #     else:

        #         st.caption(
        #             f"Page "
        #             f"{st.session_state.editor_page:,}"
        #             " — last page"
        #         )

        p1, p2, p3 = st.columns([1, 1, 4])

        with p1:

            previous_clicked = st.button(
                "← Previous",
                disabled=(
                    st.session_state.editor_page <= 1
                ),
                use_container_width=True,
                key="editor_previous_button",
            )

        with p2:

            next_clicked = st.button(
                "Next →",
                disabled=not has_next,
                use_container_width=True,
                key="editor_next_button",
            )

        with p3:

            st.caption(
                f"Page {st.session_state.editor_page:,}"
            )

        if previous_clicked and st.session_state.editor_page > 1:

            st.session_state.editor_page -= 1
            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1
            st.rerun()

        if next_clicked and has_next:

            st.session_state.editor_page += 1
            st.session_state.editor_source_df = None
            st.session_state.editor_source_key = None
            st.session_state.grid_version += 1
            st.rerun()

    # ========================================================
    # ADD RECORD
    # ========================================================

    show_add_record_form()

    # ========================================================
    # PENDING CHANGES
    # ========================================================

    pending_df = (
        build_pending_changes_df()
    )

    if not pending_df.empty:

        st.markdown("---")

        st.subheader(
            f"📝 Pending Changes "
            f"({len(pending_df):,})"
        )

        st.dataframe(
            pending_df,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.caption(
            "No pending changes."
        )

    # ========================================================
    # SYNC / DISCARD
    # ========================================================

    pending_exists = (
        pending_changes_count() > 0
    )

    st.markdown("")

    sync_col, discard_col = (
        st.columns(2)
    )

    with sync_col:

        sync_clicked = st.button(
            "💾 Sync Changes",
            type="primary",
            disabled=(
                not pending_exists
                or not can_edit
            ),
            use_container_width=True,
        )

    with discard_col:

        discard_clicked = st.button(
            "↩️ Discard Changes",
            disabled=not pending_exists,
            use_container_width=True,
        )

    # --------------------------------------------------------
    # Discard
    # --------------------------------------------------------

    if discard_clicked:

        clear_pending_changes()

        st.success(
            "All pending changes have been discarded."
        )

        st.rerun()

    # --------------------------------------------------------
    # Sync
    # --------------------------------------------------------

    if sync_clicked:

        with st.spinner(
            "Synchronizing changes..."
        ):

            (
                update_count,
                insert_count,
                delete_count,
                errors,
            ) = sync_pending_changes()

        if update_count:

            st.success(
                f"Updated: {update_count:,}"
            )

        if insert_count:

            st.success(
                f"Inserted: {insert_count:,}"
            )

        if delete_count:

            st.success(
                f"Deleted: {delete_count:,}"
            )

        if errors:

            st.error(
                "Some changes could not be synchronized."
            )

            for error in errors:

                st.warning(error)

        elif (
            update_count
            or insert_count
            or delete_count
        ):

            st.success(
                "Synchronization completed."
            )

        st.rerun()


# ============================================================
# EXPLORER
# ============================================================

with tab_explorer:

    st.subheader("Explorer")

    st.caption(
        "Explorer loads all RLS-visible records into memory "
        "in batches of 1,000. The table displays only the "
        "current page for better performance."
    )

    # --------------------------------------------------------
    # Load / Refresh ALL data
    # --------------------------------------------------------

    load_col, status_col = st.columns(
        [1, 3]
    )

    with load_col:

        load_all_clicked = st.button(
            "🔄 Load / Refresh All Data",
            type="primary",
            use_container_width=True,
        )

    if load_all_clicked:

        with st.spinner(
            "Loading all RLS-visible data from Supabase..."
        ):

            explorer_df = (
                load_all_explorer_data()
            )

        st.session_state.explorer_all_df = (
            explorer_df
        )

        st.session_state.explorer_page = 1

        st.success(
            f"Loaded "
            f"{len(explorer_df):,} records."
        )

    # --------------------------------------------------------
    # Loaded data
    # --------------------------------------------------------

    if (
        st.session_state.explorer_all_df
        is None
    ):

        st.info(
            "Click **Load / Refresh All Data** "
            "to load all records."
        )

    else:

        explorer_all_df = (
            st.session_state.explorer_all_df
            .copy()
        )

        # ----------------------------------------------------
        # Searchable columns
        # ----------------------------------------------------

        searchable_columns = [
            column
            for column
            in EXPLORER_TEXT_CANDIDATES
            if column in explorer_all_df.columns
        ]

        if not searchable_columns:

            searchable_columns = list(
                explorer_all_df.columns
            )

        # Set default search columns once.
        if not st.session_state.explorer_search_columns:

            st.session_state.explorer_search_columns = (
                searchable_columns.copy()
            )

        # Remove columns that no longer exist.
        valid_current_columns = [
            column
            for column
            in st.session_state.explorer_search_columns
            if column in searchable_columns
        ]

        if not valid_current_columns:

            valid_current_columns = (
                searchable_columns.copy()
            )

        # ----------------------------------------------------
        # Search
        # ----------------------------------------------------

        search_col1, search_col2 = (
            st.columns([1, 2])
        )

        with search_col1:

            st.text_input(
                "Search",
                key="explorer_search",
                placeholder=(
                    "Search loaded data..."
                ),
            )

        with search_col2:

            st.multiselect(
                "Search in columns",
                options=searchable_columns,
                default=valid_current_columns,
                key="explorer_search_columns",
            )

        # ----------------------------------------------------
        # Local search
        # ----------------------------------------------------

        filtered_explorer_df = (
            filter_explorer_locally(
                explorer_all_df,
                st.session_state.explorer_search,
                st.session_state.explorer_search_columns,
            )
        )

        # ----------------------------------------------------
        # Page size
        # ----------------------------------------------------

        size_col1, size_col2 = (
            st.columns([1, 5])
        )

        with size_col1:

            explorer_page_size = st.selectbox(
                "Rows per page",
                options=EXPLORER_PAGE_SIZE_OPTIONS,
                index=EXPLORER_PAGE_SIZE_OPTIONS.index(
                    st.session_state.explorer_page_size
                ),
                key="explorer_page_size_selector",
            )

            st.session_state.explorer_page_size = (
                explorer_page_size
            )

        # ----------------------------------------------------
        # Exact local pagination
        # ----------------------------------------------------

        total_records = len(
            filtered_explorer_df
        )

        page_size = (
            st.session_state.explorer_page_size
        )

        total_pages = max(
            1,
            (
                total_records
                + page_size
                - 1
            )
            // page_size,
        )

        if (
            st.session_state.explorer_page
            > total_pages
        ):

            st.session_state.explorer_page = (
                total_pages
            )

        page = (
            st.session_state.explorer_page
        )

        start = (
            page - 1
        ) * page_size

        end = (
            start + page_size
        )

        explorer_page_df = (
            filtered_explorer_df
            .iloc[start:end]
            .copy()
        )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        st.caption(
            f"Loaded total: "
            f"{len(explorer_all_df):,} records"
            f"  •  Matching: "
            f"{total_records:,} records"
        )

        if total_records:

            shown_from = start + 1

            shown_to = min(
                end,
                total_records,
            )

            st.caption(
                f"Showing "
                f"{shown_from:,}–{shown_to:,}"
                f" of "
                f"{total_records:,} matching records"
                f"  •  Page "
                f"{page:,} of "
                f"{total_pages:,}"
            )

        # ----------------------------------------------------
        # Explorer AG Grid
        # ----------------------------------------------------

        if explorer_page_df.empty:

            st.info(
                "No records match the search."
            )

        else:

            explorer_grid_options = (
                build_grid_options(
                    explorer_page_df,
                    editable=False,
                    allow_selection=False,
                )
            )

            AgGrid(
                explorer_page_df,
                gridOptions=(
                    explorer_grid_options
                ),
                data_return_mode=(
                    DataReturnMode.FILTERED_AND_SORTED
                ),
                update_mode=(
                    GridUpdateMode.NO_UPDATE
                ),
                fit_columns_on_grid_load=False,
                allow_unsafe_jscode=True,
                theme="streamlit",
                height=650,
                key=(
                    f"explorer_grid_"
                    f"{page}_"
                    f"{total_records}_"
                    f"{len(explorer_page_df)}"
                ),
            )

        # ----------------------------------------------------
        # Export
        # ----------------------------------------------------

        export_col1, export_col2 = (
            st.columns(2)
        )

        with export_col1:

            current_csv = (
                explorer_page_df
                .to_csv(
                    index=False
                )
                .encode("utf-8-sig")
            )

            st.download_button(
                "⬇️ Download Current Page CSV",
                data=current_csv,
                file_name=(
                    "ygntbpro_explorer_page.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )

        with export_col2:

            filtered_csv = (
                filtered_explorer_df
                .to_csv(
                    index=False
                )
                .encode("utf-8-sig")
            )

            st.download_button(
                "⬇️ Download All Matching CSV",
                data=filtered_csv,
                file_name=(
                    "ygntbpro_explorer_filtered.csv"
                ),
                mime="text/csv",
                use_container_width=True,
            )

        # ----------------------------------------------------
        # Explorer pagination
        # ----------------------------------------------------

        st.markdown("")

        p1, p2, p3 = st.columns(
            [1, 1, 4]
        )

        with p1:

            previous_clicked = st.button(
                "← Previous",
                disabled=(page <= 1),
                use_container_width=True,
                key="explorer_previous_button",
            )

        with p2:

            next_clicked = st.button(
                "Next →",
                disabled=(page >= total_pages),
                use_container_width=True,
                key="explorer_next_button",
            )

        with p3:

            st.caption(
                f"Page {page:,} of {total_pages:,}"
            )

        # Change page AFTER the widgets have been created.
        # This avoids Streamlit widget-state conflicts.

        if previous_clicked and page > 1:

            st.session_state.explorer_page = page - 1
            st.rerun()

        if next_clicked and page < total_pages:

            st.session_state.explorer_page = page + 1
            st.rerun()