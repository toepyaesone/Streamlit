# database.py
# ============================================================
# YgnTBPro - Supabase Database Editor
# Streamlit + Supabase + AG Grid
# ============================================================

import os
import math
from datetime import date, datetime, time
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

# Supabase/PostgREST request batch size.
#
# IMPORTANT:
# This is NOT the maximum number of records in the
# application. It is only the size of each server request.
BATCH_SIZE = 1000

EDITOR_PAGE_SIZE_DEFAULT = 300

EDITOR_PAGE_SIZE_OPTIONS = [
    100,
    300,
    500,
    1000,
]

EXPLORER_DISPLAY_LIMIT = 1000

CSV_BATCH_SIZE = 1000


# ============================================================
# COLUMN CANDIDATES
# ============================================================

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


# ============================================================
# FILTER TYPES
# ============================================================

# IMPORTANT:
# PostgreSQL ILIKE cannot be used against integer columns.
#
# These filters therefore use exact .eq() matching.
NUMERIC_FILTERS = {
    "team",
    "visit_no",
    "sr_no",
}


# These filters use ILIKE.
TEXT_FILTERS = {
    "tsp",
    "approach",
    "case",
    "ward_village",
}


# ============================================================
# SESSION DEFAULTS
# ============================================================

DEFAULTS = {
    "session": None,
    "user_role": None,

    "editor_page": 1,
    "editor_page_size": EDITOR_PAGE_SIZE_DEFAULT,
    "editor_filters": {},
    "editor_loaded": False,

    "editor_source_df": None,

    # Primary-key -> changed columns
    "pending_updates": {},

    # List of new records
    "pending_inserts": [],

    # Set of primary-key values
    "pending_deletes": set(),

    "grid_version": 0,
    "filter_version": 0,

    "show_add_form": False,

    "explorer_loaded": False,
    "explorer_result": pd.DataFrame(),
}


for key, default_value in DEFAULTS.items():

    if key not in st.session_state:

        st.session_state[key] = default_value


# ============================================================
# SECRET / ENVIRONMENT HELPERS
# ============================================================

def get_secret(
    name: str,
    default: str = "",
) -> str:
    """
    Read a value from Streamlit secrets first,
    then environment variables.

    Always return a normal Python string.

    This is important because some newer versions of
    httpx/supabase can expose URLs as URL objects.
    """

    value = None

    try:

        if name in st.secrets:

            value = st.secrets[name]

    except Exception:

        value = None

    if value is None:

        value = os.getenv(
            name
        )

    if value is None:

        return default

    # --------------------------------------------------------
    # IMPORTANT URL FIX
    # --------------------------------------------------------

    return str(
        value
    ).strip()


def get_supabase_config():
    """
    Get Supabase URL and key.

    Supports both project-specific and generic secret names.
    """

    url = get_secret(
        "SUPABASE_URL_ygntbpro"
    )

    if not url:

        url = get_secret(
            "SUPABASE_URL"
        )

    key = get_secret(
        "SUPABASE_KEY_ygntbpro"
    )

    if not key:

        key = get_secret(
            "SUPABASE_KEY"
        )

    # --------------------------------------------------------
    # IMPORTANT URL FIX
    # --------------------------------------------------------

    url = str(
        url
    ).strip()

    key = str(
        key
    ).strip()

    if not url:

        raise RuntimeError(
            "SUPABASE_URL_ygntbpro is missing."
        )

    if not key:

        raise RuntimeError(
            "SUPABASE_KEY_ygntbpro is missing."
        )

    return (
        url,
        key,
    )


# ============================================================
# BASE SUPABASE CLIENT
# ============================================================

@st.cache_resource
def get_base_client() -> Client:

    url, key = (
        get_supabase_config()
    )

    # Explicitly convert to plain strings.
    url = str(url)
    key = str(key)

    return create_client(
        url,
        key,
    )


# ============================================================
# AUTHENTICATED USER CLIENT
# ============================================================

def get_user_client() -> Client:
    """
    Create a Supabase client authenticated as the
    currently logged-in user.

    All RLS-sensitive operations should use this client.
    """

    session = (
        st.session_state.get(
            "session"
        )
    )

    if session is None:

        raise RuntimeError(
            "No authenticated session."
        )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT use:
    #
    #     base_client.supabase_url
    #
    # because in some library versions this can be an
    # httpx.URL object.
    #
    # Read the original configuration and explicitly
    # convert it to str.
    # --------------------------------------------------------

    url, key = (
        get_supabase_config()
    )

    url = str(
        url
    ).strip()

    key = str(
        key
    ).strip()

    # --------------------------------------------------------
    # Create a completely new client.
    # --------------------------------------------------------

    user_client = create_client(
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

    access_token = str(
        access_token
    ).strip()

    if refresh_token:

        refresh_token = str(
            refresh_token
        ).strip()

    # --------------------------------------------------------
    # Authenticate the PostgREST client.
    # --------------------------------------------------------

    authenticated = False

    # Preferred method.
    if refresh_token:

        try:

            user_client.auth.set_session(
                access_token,
                refresh_token,
            )

            authenticated = True

        except Exception:

            authenticated = False

    # Fallback for different supabase-py versions.
    if not authenticated:

        try:

            user_client.postgrest.auth(
                access_token
            )

            authenticated = True

        except Exception as exc:

            raise RuntimeError(
                "Unable to authenticate Supabase client: "
                f"{exc}"
            )

    return user_client


# ============================================================
# LOGIN
# ============================================================

def login_user(
    email: str,
    password: str,
):
    """
    Authenticate with Supabase Auth.

    Role is retrieved by user UUID from user_roles.
    """

    try:

        base_supabase = (
            get_base_client()
        )

        email = str(
            email
        ).strip()

        password = str(
            password
        )

        # ----------------------------------------------------
        # Supabase authentication
        # ----------------------------------------------------

        response = (
            base_supabase
            .auth
            .sign_in_with_password(
                {
                    "email": email,
                    "password": password,
                }
            )
        )

        if not response.session:

            return (
                False,
                "Login failed: no authenticated session was returned.",
            )

        # ----------------------------------------------------
        # Save session FIRST.
        # ----------------------------------------------------

        st.session_state.session = (
            response.session
        )

        # ----------------------------------------------------
        # Now create authenticated client.
        # ----------------------------------------------------

        user_client = (
            get_user_client()
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Role is looked up by Supabase Auth user UUID,
        # NOT by email.
        # ----------------------------------------------------

        user_id = (
            response.session
            .user
            .id
        )

        role_result = (
            user_client
            .table("user_roles")
            .select("role")
            .eq(
                "user_id",
                user_id,
            )
            .limit(1)
            .execute()
        )

        if role_result.data:

            role = (
                role_result
                .data[0]
                .get(
                    "role",
                    "viewer",
                )
            )

        else:

            role = "viewer"

        role = str(
            role
        ).strip().lower()

        if role not in {
            "viewer",
            "editor",
            "admin",
        }:

            role = "viewer"

        st.session_state.user_role = (
            role
        )

        return (
            True,
            "Login successful.",
        )

    except Exception as exc:

        # Clear partial authentication state.
        st.session_state.session = None
        st.session_state.user_role = None

        return (
            False,
            str(exc),
        )


# ============================================================
# LOGOUT
# ============================================================

def logout_user():

    try:

        base_supabase = (
            get_base_client()
        )

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

    st.title(
        "🗄️ YgnTBPro Database"
    )

    st.subheader(
        "Login"
    )

    with st.form(
        "login_form"
    ):

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

        if (
            not email.strip()
            or not password
        ):

            st.error(
                "Please enter both email and password."
            )

        else:

            success, message = (
                login_user(
                    email,
                    password,
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

    st.stop()


# ============================================================
# USER ROLE / PERMISSIONS
# ============================================================

user_role = (
    st.session_state.get(
        "user_role"
    )
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


can_edit = user_role in {
    "editor",
    "admin",
}

can_add = (
    user_role == "admin"
)

can_delete = (
    user_role == "admin"
)


# ============================================================
# GENERAL UTILITIES
# ============================================================

def resolve_column(
    columns,
    *candidates,
):

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


def is_null_like(
    value,
):

    if value is None:

        return True

    try:

        result = pd.isna(
            value
        )

        if isinstance(
            result,
            (
                bool,
                np.bool_,
            ),
        ):

            return bool(
                result
            )

    except Exception:

        pass

    return False


def values_equal(
    left,
    right,
):

    if (
        is_null_like(left)
        and is_null_like(right)
    ):

        return True

    if (
        is_null_like(left)
        != is_null_like(right)
    ):

        return False

    try:

        result = (
            left == right
        )

        if isinstance(
            result,
            (
                bool,
                np.bool_,
            ),
        ):

            return bool(
                result
            )

    except Exception:

        pass

    return str(left) == str(right)


def clean_value(
    value,
):

    if value is None:

        return None

    if is_null_like(
        value
    ):

        return None

    if isinstance(
        value,
        np.integer,
    ):

        return int(
            value
        )

    if isinstance(
        value,
        np.floating,
    ):

        if (
            np.isnan(value)
            or np.isinf(value)
        ):

            return None

        return float(
            value
        )

    if isinstance(
        value,
        np.bool_,
    ):

        return bool(
            value
        )

    if isinstance(
        value,
        Decimal,
    ):

        return float(
            value
        )

    if isinstance(
        value,
        pd.Timestamp,
    ):

        return value.to_pydatetime()

    if isinstance(
        value,
        np.datetime64,
    ):

        return pd.Timestamp(
            value
        ).to_pydatetime()

    return value


def make_json_safe(
    value,
):

    if value is None:

        return None

    if isinstance(
        value,
        dict,
    ):

        return {
            str(key): make_json_safe(
                val
            )
            for key, val
            in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):

        return [
            make_json_safe(
                item
            )
            for item
            in value
        ]

    if isinstance(
        value,
        np.integer,
    ):

        return int(
            value
        )

    if isinstance(
        value,
        np.floating,
    ):

        if (
            np.isnan(value)
            or np.isinf(value)
        ):

            return None

        return float(
            value
        )

    if isinstance(
        value,
        np.bool_,
    ):

        return bool(
            value
        )

    if isinstance(
        value,
        Decimal,
    ):

        return float(
            value
        )

    if isinstance(
        value,
        pd.Timestamp,
    ):

        return value.isoformat()

    if isinstance(
        value,
        (
            datetime,
            date,
            time,
        ),
    ):

        return value.isoformat()

    try:

        if pd.isna(
            value
        ):

            return None

    except Exception:

        pass

    return value


def clean_record(
    record: dict,
) -> dict:

    return make_json_safe(
        {
            key: clean_value(
                value
            )
            for key, value
            in record.items()
        }
    )


# ============================================================
# SCHEMA
# ============================================================

def get_table_columns():

    # Use authenticated client so that schema/sample retrieval
    # follows the current user's RLS permissions.
    client = get_user_client()

    response = (
        client
        .table(TABLE_NAME)
        .select("*")
        .limit(1)
        .execute()
    )

    rows = (
        response.data
        or []
    )

    if not rows:

        return []

    return list(
        rows[0].keys()
    )


def resolve_database_columns(
    columns,
):

    return {

        "primary_key":
            resolve_column(
                columns,
                *PRIMARY_KEY_CANDIDATES,
            ),

        "date":
            resolve_column(
                columns,
                *DATE_COLUMN_CANDIDATES,
            ),

        "updated_at":
            resolve_column(
                columns,
                *UPDATED_AT_CANDIDATES,
            ),

        "team":
            resolve_column(
                columns,
                "team",
                "Team",
            ),

        "tsp":
            resolve_column(
                columns,
                "tsp",
                "Tsp",
                "TSP",
            ),

        "approach":
            resolve_column(
                columns,
                "approach",
                "Approach",
            ),

        "case":
            resolve_column(
                columns,
                "case",
                "Case",
            ),

        "visit_no":
            resolve_column(
                columns,
                "visit_no",
                "Visit_no",
                "visitno",
                "VisitNo",
            ),

        "sr_no":
            resolve_column(
                columns,
                "sr_no",
                "Sr_No",
                "srno",
                "SrNo",
            ),

        "ward_village":
            resolve_column(
                columns,
                "ward_village",
                "WardVillage",
                "Ward_Village",
                "wardvillage",
            ),
    }


# ============================================================
# PAGINATED FETCH
# ============================================================

def fetch_all_from_query(
    query_builder,
    batch_size=BATCH_SIZE,
):
    """
    Fetch all rows using repeated PostgREST range requests.

    There is no 1,000-row application limit.

    Example:
        0-999
        1000-1999
        2000-2999
        ...
    """

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

        rows.extend(
            batch
        )

        if len(batch) < batch_size:

            break

        start += batch_size

    return pd.DataFrame(
        rows
    )


# ============================================================
# FILTER HELPERS
# ============================================================

def parse_integer_filter(
    value,
):

    if value is None:

        return None

    text = str(
        value
    ).strip()

    if not text:

        return None

    try:

        return int(
            text
        )

    except (
        ValueError,
        TypeError,
    ):

        return None


def escape_ilike_value(
    value: str,
):

    return (
        str(value)
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


def apply_editor_filter(
    query,
    column,
    value,
    filter_name,
):
    """
    Apply a filter using the correct PostgreSQL operator.

    Numeric:
        Team
        Visit No
        SR No

    Text:
        TSP
        Approach
        Case
        Ward/Village

    PatientID:
        exact match
    """

    if not column:

        return query

    if value is None:

        return query

    value = str(
        value
    ).strip()

    if not value:

        return query

    # --------------------------------------------------------
    # Patient ID
    # --------------------------------------------------------

    if filter_name == "patient_id":

        return query.eq(
            column,
            value,
        )

    # --------------------------------------------------------
    # Numeric columns
    #
    # IMPORTANT FIX:
    # Do NOT use ilike() here.
    # --------------------------------------------------------

    if filter_name in NUMERIC_FILTERS:

        numeric_value = (
            parse_integer_filter(
                value
            )
        )

        # Invalid numeric input:
        # return no matching records rather than generating
        # an invalid PostgreSQL query.
        if numeric_value is None:

            return query.eq(
                column,
                -999999999,
            )

        return query.eq(
            column,
            numeric_value,
        )

    # --------------------------------------------------------
    # Text columns
    # --------------------------------------------------------

    if filter_name in TEXT_FILTERS:

        safe_value = (
            escape_ilike_value(
                value
            )
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
    client,
    columns,
    db_columns,
    filters,
):

    query = (
        client
        .table(TABLE_NAME)
        .select("*")
    )

    # --------------------------------------------------------
    # Filters
    # --------------------------------------------------------

    mapping = {

        "patient_id":
            db_columns.get(
                "primary_key"
            ),

        "team":
            db_columns.get(
                "team"
            ),

        "tsp":
            db_columns.get(
                "tsp"
            ),

        "approach":
            db_columns.get(
                "approach"
            ),

        "case":
            db_columns.get(
                "case"
            ),

        "visit_no":
            db_columns.get(
                "visit_no"
            ),

        "sr_no":
            db_columns.get(
                "sr_no"
            ),

        "ward_village":
            db_columns.get(
                "ward_village"
            ),
    }

    for filter_name, column in mapping.items():

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
    # Date range
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

            # Use < next day rather than <= 23:59:59.
            next_day = (
                date_to
                + pd.Timedelta(
                    days=1
                )
            )

            query = query.lt(
                date_column,
                next_day.date().isoformat(),
            )

    # --------------------------------------------------------
    # Stable ordering
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
    client,
    columns,
    db_columns,
    filters,
    page,
    page_size,
):
    """
    Load one editor page.

    IMPORTANT:
    Does NOT use:
        select(count="exact")

    This avoids compatibility problems with older
    supabase-py/PostgREST versions.

    Instead, request one extra row.

    Example:
        page size = 300

        request = 301 rows

        301 rows returned -> Next page exists
        <=300 rows       -> Last page
    """

    query = build_editor_query(
        client,
        columns,
        db_columns,
        filters,
    )

    start = max(
        0,
        (page - 1) * page_size,
    )

    # Request one additional record.
    end = (
        start
        + page_size
    )

    response = (
        query
        .range(
            start,
            end,
        )
        .execute()
    )

    rows = (
        response.data
        or []
    )

    has_next_page = (
        len(rows) > page_size
    )

    if has_next_page:

        rows = rows[
            :page_size
        ]

    df = pd.DataFrame(
        rows
    )

    # This is only a display/navigation estimate.
    # It is NOT an exact database count.
    if has_next_page:

        records_loaded = (
            start
            + page_size
        )

    else:

        records_loaded = (
            start
            + len(rows)
        )

    return (
        df,
        records_loaded,
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

    key = str(
        primary_id
    )

    if key not in (
        st.session_state
        .pending_updates
    ):

        st.session_state.pending_updates[
            key
        ] = {}

    st.session_state.pending_updates[
        key
    ].update(
        changes
    )

    # If user edits a record after marking it for deletion,
    # cancel the pending deletion.
    st.session_state.pending_deletes.discard(
        key
    )


def add_pending_insert(
    record,
):

    record = clean_record(
        record
    )

    st.session_state.pending_inserts.append(
        record
    )


def capture_grid_changes(
    source_df,
    edited_df,
    primary_key,
):
    """
    Compare the current grid against its database snapshot.

    IMPORTANT:
    Missing rows are NOT considered deleted.

    Deletion must be explicit through Delete Selected.
    """

    if source_df is None:

        return

    if edited_df is None:

        return

    if primary_key not in source_df.columns:

        return

    if primary_key not in edited_df.columns:

        return

    source = (
        source_df
        .copy()
        .reset_index(drop=True)
    )

    edited = (
        edited_df
        .copy()
        .reset_index(drop=True)
    )

    source_rows = {}

    for _, row in source.iterrows():

        primary_id = row.get(
            primary_key
        )

        if not is_null_like(
            primary_id
        ):

            source_rows[
                str(primary_id)
            ] = row

    for _, edited_row in edited.iterrows():

        primary_id = edited_row.get(
            primary_key
        )

        if is_null_like(
            primary_id
        ):

            continue

        key = str(
            primary_id
        )

        if key not in source_rows:

            continue

        source_row = (
            source_rows[key]
        )

        changes = {}

        for column in edited.columns:

            if column == primary_key:

                continue

            old_value = (
                source_row.get(
                    column
                )
            )

            new_value = (
                edited_row.get(
                    column
                )
            )

            if not values_equal(
                old_value,
                new_value,
            ):

                changes[column] = (
                    clean_value(
                        new_value
                    )
                )

        if changes:

            add_pending_update(
                primary_id,
                changes,
            )


def pending_changes_count():

    return (
        len(
            st.session_state
            .pending_updates
        )
        +
        len(
            st.session_state
            .pending_inserts
        )
        +
        len(
            st.session_state
            .pending_deletes
        )
    )


def clear_pending_changes():

    st.session_state.pending_updates = {}

    st.session_state.pending_inserts = []

    st.session_state.pending_deletes = set()

    st.session_state.editor_source_df = None


def mark_selected_for_delete(
    selected_rows,
    primary_key,
):

    if not selected_rows:

        return

    for row in selected_rows:

        primary_id = row.get(
            primary_key
        )

        if is_null_like(
            primary_id
        ):

            continue

        key = str(
            primary_id
        )

        st.session_state.pending_deletes.add(
            key
        )

        st.session_state.pending_updates.pop(
            key,
            None,
        )


def merge_pending_changes_into_page(
    df,
    primary_key,
):
    """
    Overlay pending changes on the currently loaded page.

    This makes pending changes remain visible when the
    user navigates between pages or changes filters.
    """

    if df is None:

        return df

    if df.empty:

        return df

    result = df.copy()

    # --------------------------------------------------------
    # Remove pending deletes
    # --------------------------------------------------------

    delete_ids = {
        str(value)
        for value
        in st.session_state.pending_deletes
    }

    if (
        delete_ids
        and primary_key in result.columns
    ):

        result = result[
            ~result[
                primary_key
            ]
            .astype(str)
            .isin(delete_ids)
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

            if is_null_like(
                primary_id
            ):

                continue

            key = str(
                primary_id
            )

            changes = (
                st.session_state
                .pending_updates
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
# AG GRID
# ============================================================

def build_grid_options(
    df,
    primary_key,
    updated_at_column,
    editable,
):

    gb = (
        GridOptionsBuilder
        .from_dataframe(df)
    )

    gb.configure_default_column(
        editable=editable,
        sortable=True,
        filter=True,
        resizable=True,
        minWidth=100,
    )

    if primary_key in df.columns:

        gb.configure_column(
            primary_key,
            editable=False,
            pinned="left",
        )

    if (
        updated_at_column
        and updated_at_column in df.columns
    ):

        gb.configure_column(
            updated_at_column,
            editable=False,
        )

    # --------------------------------------------------------
    # Selection only for Admin.
    # --------------------------------------------------------

    if can_delete:

        gb.configure_selection(
            selection_mode="multiple",
            use_checkbox=True,
            header_checkbox=True,
        )

    gb.configure_grid_options(
        rowHeight=32,
        headerHeight=38,
        suppressRowClickSelection=False,
        animateRows=False,
    )

    return gb.build()


# ============================================================
# EXPLORER QUERY
# ============================================================

def build_explorer_query(
    client,
    columns,
    db_columns,
    keyword,
    selected_columns,
):

    if selected_columns:

        select_string = ",".join(
            selected_columns
        )

    else:

        select_string = "*"

    query = (
        client
        .table(TABLE_NAME)
        .select(
            select_string
        )
    )

    keyword = str(
        keyword or ""
    ).strip()

    if keyword:

        safe_keyword = (
            escape_ilike_value(
                keyword
            )
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Only text columns are included in global keyword
        # search.
        #
        # We deliberately do NOT call:
        #
        #     Team.ilike(...)
        #     VisitNo.ilike(...)
        #     SRNo.ilike(...)
        #
        # because those may be INTEGER columns.
        # ----------------------------------------------------

        text_columns = []

        for name in [
            "tsp",
            "approach",
            "case",
            "ward_village",
        ]:

            column = (
                db_columns.get(
                    name
                )
            )

            if (
                column
                and column in columns
            ):

                text_columns.append(
                    column
                )

        if text_columns:

            expressions = [
                (
                    f"{column}.ilike."
                    f"%{safe_keyword}%"
                )
                for column
                in text_columns
            ]

            query = query.or_(
                ",".join(
                    expressions
                )
            )

    primary_key = (
        db_columns.get(
            "primary_key"
        )
    )

    if primary_key:

        query = query.order(
            primary_key,
            desc=True,
        )

    return query


# ============================================================
# EXPLORER CSV EXPORT
# ============================================================

def export_filtered_csv(
    client,
    columns,
    db_columns,
    keyword,
    selected_columns,
):

    def query_builder(
        start,
        end,
    ):

        query = (
            build_explorer_query(
                client,
                columns,
                db_columns,
                keyword,
                selected_columns,
            )
        )

        return query.range(
            start,
            end,
        )

    df = fetch_all_from_query(
        query_builder,
        CSV_BATCH_SIZE,
    )

    if df.empty:

        return b""

    return (
        df
        .to_csv(
            index=False
        )
        .encode(
            "utf-8-sig"
        )
    )


# ============================================================
# SYNCHRONIZATION
# ============================================================

def sync_pending_changes(
    client,
    primary_key,
):
    """
    Synchronize pending UPDATE / INSERT / DELETE operations.

    Failed operations remain pending.
    """

    update_errors = []
    insert_errors = []
    delete_errors = []

    successful_updates = []
    successful_inserts = []
    successful_deletes = []

    # ========================================================
    # UPDATE
    # ========================================================

    for (
        primary_id,
        changes,
    ) in list(
        st.session_state
        .pending_updates
        .items()
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
                    clean_value(
                        primary_id
                    ),
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

    # ========================================================
    # INSERT
    # ========================================================

    remaining_inserts = []

    for record in list(
        st.session_state
        .pending_inserts
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
                f"INSERT {record.get(primary_key, '')}: {exc}"
            )

            remaining_inserts.append(
                record
            )

    # ========================================================
    # DELETE
    # ========================================================

    for primary_id in list(
        st.session_state
        .pending_deletes
    ):

        try:

            (
                client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    primary_key,
                    clean_value(
                        primary_id
                    ),
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

    # ========================================================
    # Remove successful UPDATE operations
    # ========================================================

    for primary_id in successful_updates:

        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    # ========================================================
    # Keep failed INSERT operations
    # ========================================================

    st.session_state.pending_inserts = (
        remaining_inserts
    )

    # ========================================================
    # Remove successful DELETE operations
    # ========================================================

    for primary_id in successful_deletes:

        st.session_state.pending_deletes.discard(
            primary_id
        )

    return {

        "successful_updates":
            len(
                successful_updates
            ),

        "successful_inserts":
            len(
                successful_inserts
            ),

        "successful_deletes":
            len(
                successful_deletes
            ),

        "errors":
            (
                update_errors
                + insert_errors
                + delete_errors
            ),
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
# DATABASE INITIALIZATION
# ============================================================

try:

    # Authenticated client.
    client = get_user_client()

    columns = (
        get_table_columns()
    )

    if not columns:

        st.error(
            f"No columns were returned from `{TABLE_NAME}`."
        )

        st.stop()

    db_columns = (
        resolve_database_columns(
            columns
        )
    )

    primary_key = (
        db_columns.get(
            "primary_key"
        )
    )

    if not primary_key:

        st.error(
            "Could not identify PatientID "
            "as the primary-key column."
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

    # ========================================================
    # FILTER RESET CALLBACK
    # ========================================================

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

        st.session_state.editor_loaded = True

        st.session_state.filter_version += 1

        # IMPORTANT:
        #
        # Pending UPDATE / INSERT / DELETE changes
        # are intentionally NOT cleared.

    # ========================================================
    # FILTERS
    # ========================================================

    with st.expander(
        "🔍 Filters",
        expanded=True,
    ):

        row1 = st.columns(
            4
        )

        with row1[0]:

            patient_id_filter = (
                st.text_input(
                    "Patient ID",
                    key="filter_patient_id",
                )
            )

        with row1[1]:

            team_filter = (
                st.text_input(
                    "Team",
                    key="filter_team",
                    help="Exact numeric match.",
                )
            )

        with row1[2]:

            tsp_filter = (
                st.text_input(
                    "TSP",
                    key="filter_tsp",
                )
            )

        with row1[3]:

            approach_filter = (
                st.text_input(
                    "Approach",
                    key="filter_approach",
                )
            )

        row2 = st.columns(
            4
        )

        with row2[0]:

            case_filter = (
                st.text_input(
                    "Case",
                    key="filter_case",
                )
            )

        with row2[1]:

            visit_no_filter = (
                st.text_input(
                    "Visit No",
                    key="filter_visit_no",
                    help="Exact numeric match.",
                )
            )

        with row2[2]:

            sr_no_filter = (
                st.text_input(
                    "SR No",
                    key="filter_sr_no",
                    help="Exact numeric match.",
                )
            )

        with row2[3]:

            ward_village_filter = (
                st.text_input(
                    "Ward / Village",
                    key="filter_ward_village",
                )
            )

        row3 = st.columns(
            3
        )

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

            current_size = (
                st.session_state
                .editor_page_size
            )

            if (
                current_size
                not in EDITOR_PAGE_SIZE_OPTIONS
            ):

                current_size = (
                    EDITOR_PAGE_SIZE_DEFAULT
                )

            page_size = st.selectbox(
                "Rows per page",
                EDITOR_PAGE_SIZE_OPTIONS,
                index=(
                    EDITOR_PAGE_SIZE_OPTIONS
                    .index(
                        current_size
                    )
                ),
            )

        button1, button2 = st.columns(
            2
        )

        with button1:

            apply_filters = st.button(
                "🔄 Load / Apply Filters",
                type="primary",
                use_container_width=True,
            )

        with button2:

            st.button(
                "↩️ Reset Filters",
                use_container_width=True,
                on_click=reset_editor_filters,
            )

    # ========================================================
    # APPLY FILTERS
    # ========================================================

    if apply_filters:

        st.session_state.editor_filters = {

            "patient_id":
                patient_id_filter,

            "team":
                team_filter,

            "tsp":
                tsp_filter,

            "approach":
                approach_filter,

            "case":
                case_filter,

            "visit_no":
                visit_no_filter,

            "sr_no":
                sr_no_filter,

            "ward_village":
                ward_village_filter,

            "date_from":
                date_from,

            "date_to":
                date_to,
        }

        st.session_state.editor_page = 1

        st.session_state.editor_page_size = (
            page_size
        )

        st.session_state.editor_loaded = True

        # Reset only the database snapshot.
        #
        # Pending changes remain untouched.
        st.session_state.editor_source_df = None

        st.session_state.filter_version += 1

        st.rerun()

    # ========================================================
    # INITIAL FILTER STATE
    # ========================================================

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

    # ========================================================
    # LOAD CURRENT PAGE
    # ========================================================

    try:

        current_page = (
            st.session_state
            .editor_page
        )

        current_page_size = (
            st.session_state
            .editor_page_size
        )

        filters = (
            st.session_state
            .editor_filters
        )

        (
            df,
            records_loaded,
            has_next_page,
        ) = load_editor_page(
            client,
            columns,
            db_columns,
            filters,
            current_page,
            current_page_size,
        )

        # ----------------------------------------------------
        # Keep database snapshot WITHOUT pending changes.
        # ----------------------------------------------------

        st.session_state.editor_source_df = (
            df.copy()
        )

        # ----------------------------------------------------
        # Overlay pending updates/deletes for display.
        # ----------------------------------------------------

        display_df = (
            merge_pending_changes_into_page(
                df,
                primary_key,
            )
        )

    except Exception as exc:

        st.error(
            f"Unable to load records: {exc}"
        )

        st.stop()

    # ========================================================
    # PAGE INFORMATION
    # ========================================================

    info1, info2, info3 = st.columns(
        3
    )

    with info1:

        if has_next_page:

            record_text = (
                f"{records_loaded:,}+"
            )

        else:

            record_text = (
                f"{records_loaded:,}"
            )

        st.metric(
            "Records Loaded",
            record_text,
        )

    with info2:

        page_text = (
            f"Page **{current_page}**"
        )

        if has_next_page:

            page_text += " / more"

        else:

            page_text += " / last"

        st.write(
            page_text
        )

        st.caption(
            f"Showing {len(display_df):,} "
            "records on this page"
        )

    with info3:

        pending = (
            pending_changes_count()
        )

        if pending:

            st.warning(
                f"Pending changes: {pending}"
            )

        else:

            st.success(
                "No pending changes"
            )

    # ========================================================
    # AG GRID
    # ========================================================

    grid_response = None

    if display_df.empty:

        st.info(
            "No records found."
        )

    else:

        grid_options = (
            build_grid_options(
                display_df,
                primary_key,
                db_columns.get(
                    "updated_at"
                ),
                can_edit,
            )
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
                |
                GridUpdateMode.SELECTION_CHANGED
            ),
            fit_columns_on_grid_load=False,
            allow_unsafe_jscode=False,
            enable_enterprise_modules=False,
            height=600,
            width="100%",
            reload_data=False,
            key=grid_key,
        )

        # ----------------------------------------------------
        # Capture edits
        # ----------------------------------------------------

        edited_rows = (
            grid_response.get(
                "data"
            )
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
                st.session_state.editor_source_df,
                edited_df,
                primary_key,
            )

    # ========================================================
    # ACTION BUTTONS
    # ========================================================

    action1, action2, action3, action4 = (
        st.columns(4)
    )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    with action1:

        if can_delete:

            if st.button(
                "🗑️ Delete Selected",
                use_container_width=True,
            ):

                selected_rows = []

                if grid_response:

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

                if selected_rows:

                    mark_selected_for_delete(
                        selected_rows,
                        primary_key,
                    )

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
    # ADD
    # --------------------------------------------------------

    with action2:

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
    # PREVIOUS
    # --------------------------------------------------------

    with action3:

        if st.button(
            "⬅️ Previous",
            disabled=(
                current_page <= 1
            ),
            use_container_width=True,
        ):

            st.session_state.editor_page = (
                current_page - 1
            )

            st.session_state.editor_source_df = None

            st.rerun()

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    with action4:

        if st.button(
            "Next ➡️",
            disabled=not has_next_page,
            use_container_width=True,
        ):

            st.session_state.editor_page = (
                current_page + 1
            )

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
            "The new record is kept as a pending INSERT "
            "until Sync Changes is pressed."
        )

        with st.form(
            "add_new_record_form"
        ):

            add_values = {}

            form_columns = st.columns(
                4
            )

            for index, column in enumerate(
                columns
            ):

                with form_columns[
                    index % 4
                ]:

                    add_values[column] = (
                        st.text_input(
                            column
                        )
                    )

            submit_new = (
                st.form_submit_button(
                    "Add to Pending Changes",
                    type="primary",
                    use_container_width=True,
                )
            )

        if submit_new:

            record = {}

            for column, value in (
                add_values.items()
            ):

                value = str(
                    value
                ).strip()

                if value:

                    record[column] = value

            if not record.get(
                primary_key
            ):

                st.error(
                    f"{primary_key} is required."
                )

            else:

                # ------------------------------------------------
                # Convert known numeric fields.
                # ------------------------------------------------

                numeric_columns = [
                    db_columns.get(
                        "team"
                    ),
                    db_columns.get(
                        "visit_no"
                    ),
                    db_columns.get(
                        "sr_no"
                    ),
                ]

                for column in numeric_columns:

                    if (
                        column
                        and column in record
                    ):

                        parsed = (
                            parse_integer_filter(
                                record[column]
                            )
                        )

                        if parsed is not None:

                            record[column] = (
                                parsed
                            )

                add_pending_insert(
                    record
                )

                st.session_state.show_add_form = (
                    False
                )

                st.success(
                    "New record added to pending changes."
                )

                st.rerun()

    # ========================================================
    # PENDING CHANGES
    # ========================================================

    if pending_changes_count():

        st.divider()

        st.subheader(
            f"⏳ Pending Changes "
            f"({pending_changes_count()})"
        )

        p1, p2, p3 = st.columns(
            3
        )

        with p1:

            st.metric(
                "Updates",
                len(
                    st.session_state
                    .pending_updates
                ),
            )

        with p2:

            st.metric(
                "Inserts",
                len(
                    st.session_state
                    .pending_inserts
                ),
            )

        with p3:

            st.metric(
                "Deletes",
                len(
                    st.session_state
                    .pending_deletes
                ),
            )

        # ----------------------------------------------------
        # UPDATE PREVIEW
        # ----------------------------------------------------

        if (
            st.session_state
            .pending_updates
        ):

            st.markdown(
                "**UPDATE**"
            )

            update_preview = []

            for (
                primary_id,
                changes,
            ) in (
                st.session_state
                .pending_updates
                .items()
            ):

                for (
                    column,
                    value,
                ) in changes.items():

                    update_preview.append(
                        {
                            primary_key:
                                primary_id,

                            "Column":
                                column,

                            "New Value":
                                value,
                        }
                    )

            st.dataframe(
                pd.DataFrame(
                    update_preview
                ),
                use_container_width=True,
                hide_index=True,
            )

        # ----------------------------------------------------
        # INSERT PREVIEW
        # ----------------------------------------------------

        if (
            st.session_state
            .pending_inserts
        ):

            st.markdown(
                "**INSERT**"
            )

            st.dataframe(
                pd.DataFrame(
                    st.session_state
                    .pending_inserts
                ),
                use_container_width=True,
                hide_index=True,
            )

        # ----------------------------------------------------
        # DELETE PREVIEW
        # ----------------------------------------------------

        if (
            st.session_state
            .pending_deletes
        ):

            st.markdown(
                "**DELETE**"
            )

            st.write(
                sorted(
                    list(
                        st.session_state
                        .pending_deletes
                    )
                )
            )

        # ----------------------------------------------------
        # SYNC / DISCARD
        # ----------------------------------------------------

        sync_col, discard_col = (
            st.columns(2)
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

        # ----------------------------------------------------
        # DISCARD
        # ----------------------------------------------------

        if discard_clicked:

            clear_pending_changes()

            st.session_state.grid_version += 1

            st.success(
                "All pending changes discarded."
            )

            st.rerun()

        # ----------------------------------------------------
        # SYNC
        # ----------------------------------------------------

        if sync_clicked:

            if not (
                st.session_state.pending_updates
                or st.session_state.pending_inserts
                or st.session_state.pending_deletes
            ):

                st.info(
                    "There are no pending changes."
                )

            else:

                with st.spinner(
                    "Synchronizing changes..."
                ):

                    result = (
                        sync_pending_changes(
                            client,
                            primary_key,
                        )
                    )

                if result["errors"]:

                    st.error(
                        "Some changes could not be synchronized."
                    )

                    for error in result["errors"]:

                        st.error(
                            error
                        )

                else:

                    st.success(
                        "All pending changes synchronized successfully."
                    )

                st.info(
                    " | ".join(
                        [
                            (
                                "Updated: "
                                f"{result['successful_updates']}"
                            ),
                            (
                                "Inserted: "
                                f"{result['successful_inserts']}"
                            ),
                            (
                                "Deleted: "
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
        "Global keyword search covers text fields such as "
        "TSP, Approach, Case and Ward/Village. "
        "Numeric fields are intentionally excluded from "
        "global ILIKE search."
    )

    col1, col2 = st.columns(
        [3, 2]
    )

    with col1:

        explorer_keyword = (
            st.text_input(
                "Keyword",
                key="explorer_keyword",
                placeholder=(
                    "Search text columns..."
                ),
            )
        )

    with col2:

        explorer_columns = (
            st.multiselect(
                "Columns",
                options=columns,
                default=[],
                key="explorer_columns",
            )
        )

    b1, b2 = st.columns(
        2
    )

    with b1:

        load_explorer = st.button(
            "🔎 Search / Load",
            type="primary",
            use_container_width=True,
        )

    with b2:

        export_csv = st.button(
            "⬇️ Export ALL Matching CSV",
            use_container_width=True,
        )

    # ========================================================
    # EXPLORER SEARCH
    # ========================================================

    if load_explorer:

        try:

            query = (
                build_explorer_query(
                    client,
                    columns,
                    db_columns,
                    explorer_keyword,
                    explorer_columns,
                )
            )

            response = (
                query
                .range(
                    0,
                    EXPLORER_DISPLAY_LIMIT - 1,
                )
                .execute()
            )

            rows = (
                response.data
                or []
            )

            st.session_state.explorer_loaded = (
                True
            )

            st.session_state.explorer_result = (
                pd.DataFrame(
                    rows
                )
            )

        except Exception as exc:

            st.error(
                f"Unable to search Explorer: {exc}"
            )

    # ========================================================
    # CSV EXPORT
    # ========================================================

    if export_csv:

        try:

            with st.spinner(
                "Preparing complete CSV..."
            ):

                csv_bytes = (
                    export_filtered_csv(
                        client,
                        columns,
                        db_columns,
                        explorer_keyword,
                        explorer_columns,
                    )
                )

            if csv_bytes:

                st.download_button(
                    "⬇️ Download CSV",
                    data=csv_bytes,
                    file_name=(
                        "ygntbpro_export.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                )

            else:

                st.warning(
                    "No matching records found."
                )

        except Exception as exc:

            st.error(
                f"Unable to export CSV: {exc}"
            )

    # ========================================================
    # EXPLORER RESULT
    # ========================================================

    if st.session_state.explorer_loaded:

        explorer_df = (
            st.session_state
            .explorer_result
        )

        if explorer_df.empty:

            st.info(
                "No records found."
            )

        else:

            st.success(
                f"Displaying up to "
                f"{EXPLORER_DISPLAY_LIMIT:,} records."
            )

            st.dataframe(
                explorer_df,
                use_container_width=True,
                height=600,
                hide_index=True,
            )

            # ------------------------------------------------
            # DATA INFORMATION
            # ------------------------------------------------

            with st.expander(
                "📊 Data Information"
            ):

                c1, c2, c3 = st.columns(
                    3
                )

                with c1:

                    st.metric(
                        "Rows",
                        f"{len(explorer_df):,}",
                    )

                with c2:

                    st.metric(
                        "Columns",
                        f"{len(explorer_df.columns):,}",
                    )

                with c3:

                    null_count = int(
                        explorer_df
                        .isna()
                        .sum()
                        .sum()
                    )

                    st.metric(
                        "Missing Values",
                        f"{null_count:,}",
                    )

                dtype_df = pd.DataFrame(
                    {
                        "Column":
                            explorer_df.columns,

                        "Data Type":
                            [
                                str(dtype)
                                for dtype
                                in explorer_df.dtypes
                            ],

                        "Null Count":
                            [
                                int(
                                    explorer_df[
                                        column
                                    ]
                                    .isna()
                                    .sum()
                                )
                                for column
                                in explorer_df.columns
                            ],
                    }
                )

                st.dataframe(
                    dtype_df,
                    use_container_width=True,
                    hide_index=True,
                )