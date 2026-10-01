# ============================================================
# database.py
# YgnTBPro Supabase Database Editor
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
)


# ============================================================
# Page configuration
# ============================================================

st.set_page_config(
    page_title="YgnTBPro Database",
    page_icon="🗄️",
    layout="wide",
)


# ============================================================
# Configuration
# ============================================================

TABLE_NAME = "ygntbpro"
USER_ROLE_TABLE = "user_role"

PRIMARY_KEY = "PatientID"

BATCH_SIZE = 1000


# ------------------------------------------------------------
# Supabase configuration
# ------------------------------------------------------------

SUPABASE_URL_DEFAULT = (
    "https://kocihpxevlowqbguhstf.supabase.co"
)

SUPABASE_KEY_DEFAULT = (
    "sb_publishable_JtrNLjMNSvZ5LzvXKbv2xw_mj-hl5MD"
)


SUPABASE_URL = st.secrets.get(
    "SUPABASE_URL_ygntbpro",
    os.getenv(
        "SUPABASE_URL",
        SUPABASE_URL_DEFAULT,
    ),
)


SUPABASE_KEY = st.secrets.get(
    "SUPABASE_KEY_ygntbpro",
    os.getenv(
        "SUPABASE_KEY",
        SUPABASE_KEY_DEFAULT,
    ),
)


if not SUPABASE_URL or not SUPABASE_KEY:

    st.error(
        "Supabase configuration is missing. "
        "Please check SUPABASE_URL_ygntbpro "
        "and SUPABASE_KEY_ygntbpro."
    )

    st.stop()


# ============================================================
# Supabase base client
# ============================================================

@st.cache_resource
def get_base_client() -> Client:

    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )


base_supabase = get_base_client()


# ============================================================
# Session-state defaults
# ============================================================

DEFAULTS = {
    "session": None,
    "user_email": None,
    "user_id": None,
    "authenticated": False,
    "role": "viewer",
    "user_role": "viewer",

    "db_df": None,

    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    "selected_grid_rows": [],

    "grid_version": 0,
    "filter_version": 0,
}


for key, default_value in DEFAULTS.items():

    if key not in st.session_state:

        st.session_state[key] = default_value


# ============================================================
# Authenticated Supabase client
# ============================================================

def get_user_client() -> Client:
    """
    Create a Supabase client using the currently authenticated
    user's access token.

    This client should be used for database operations so that
    Supabase RLS policies continue to apply.
    """

    session = st.session_state.get("session")

    if session is None:
        return base_supabase

    client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
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

    if access_token and refresh_token:

        try:

            client.auth.set_session(
                access_token,
                refresh_token,
            )

            return client

        except Exception:

            pass

    # Fallback for access-token-only situations
    if access_token:

        try:

            client.postgrest.auth(
                access_token
            )

        except Exception:

            pass

    return client


# ============================================================
# Get user role
# ============================================================

def get_user_role(
    supabase_client: Client,
    user_email: str,
) -> str:
    """
    Get application role from user_role table
    using the authenticated user's email.

    Expected table structure:

        user_role
        ----------------------
        email
        role

    Supported roles:

        viewer
        editor
        admin
    """

    if not user_email:

        return "viewer"

    email = (
        str(user_email)
        .strip()
        .lower()
    )

    try:

        response = (
            supabase_client
            .table(USER_ROLE_TABLE)
            .select("role")
            .eq(
                "email",
                email,
            )
            .limit(1)
            .execute()
        )

        rows = response.data or []

        if rows:

            role_value = rows[0].get(
                "role"
            )

            if role_value:

                role_value = (
                    str(role_value)
                    .strip()
                    .lower()
                )

                if role_value in {
                    "viewer",
                    "editor",
                    "admin",
                }:

                    return role_value

    except Exception as exc:

        st.warning(
            "Could not read user role: "
            f"{exc}"
        )

    # Safe default
    return "viewer"


# ============================================================
# Login
# ============================================================

def login_user(
    email: str,
    password: str,
):

    try:

        email = (
            email
            .strip()
            .lower()
        )

        if not email:

            return False, "Please enter your email."

        if not password:

            return False, "Please enter your password."


        # ----------------------------------------------------
        # Authenticate with Supabase
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
                "Login failed: "
                "no authenticated session was returned.",
            )


        # ----------------------------------------------------
        # Save authentication session
        # ----------------------------------------------------

        session = response.session

        authenticated_user = (
            session.user
        )

        authenticated_email = (
            getattr(
                authenticated_user,
                "email",
                None,
            )
            or email
        )

        authenticated_email = (
            str(authenticated_email)
            .strip()
            .lower()
        )


        user_id = getattr(
            authenticated_user,
            "id",
            None,
        )


        st.session_state.session = session

        st.session_state.user_email = (
            authenticated_email
        )

        st.session_state.user_id = (
            user_id
        )

        st.session_state.authenticated = True


        # ----------------------------------------------------
        # Create authenticated client
        # ----------------------------------------------------

        user_client = get_user_client()


        # ----------------------------------------------------
        # Get application role by EMAIL
        # ----------------------------------------------------

        role = get_user_role(
            user_client,
            authenticated_email,
        )


        st.session_state.role = role
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
# Logout
# ============================================================

def logout_user():

    try:

        base_supabase.auth.sign_out()

    except Exception:

        pass


    for key, default_value in DEFAULTS.items():

        st.session_state[key] = default_value


# ============================================================
# Login screen
# ============================================================

if not st.session_state.authenticated:

    st.title(
        "🔑 YgnTBPro Database Login"
    )

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

        login_clicked = (
            st.form_submit_button(
                "Login",
                use_container_width=True,
                type="primary",
            )
        )


    if login_clicked:

        success, message = login_user(
            email,
            password,
        )

        if success:

            st.rerun()

        else:

            st.error(message)


    st.stop()


# ============================================================
# Authenticated client
# ============================================================

client = get_user_client()


# ============================================================
# Make sure authentication information exists
# ============================================================

user_email = (
    st.session_state.get(
        "user_email"
    )
    or ""
)


user_id = (
    st.session_state.get(
        "user_id"
    )
)


# ------------------------------------------------------------
# Re-read role if necessary
# ------------------------------------------------------------

role = (
    st.session_state.get(
        "role"
    )
    or st.session_state.get(
        "user_role"
    )
    or "viewer"
)


role = (
    str(role)
    .strip()
    .lower()
)


# Only allow valid roles
if role not in {
    "viewer",
    "editor",
    "admin",
}:

    role = "viewer"


st.session_state.role = role
st.session_state.user_role = role


# ============================================================
# Permissions
# ============================================================

can_edit = role in {
    "editor",
    "admin",
}

can_add = role == "admin"

can_delete = role == "admin"


# ============================================================
# JSON-safe value conversion
# ============================================================

def is_missing(value):

    if value is None:

        return True

    try:

        result = pd.isna(value)

        if isinstance(
            result,
            (bool, np.bool_),
        ):

            return bool(result)

    except Exception:

        pass

    return False


def clean_value(value):

    if value is None:

        return None

    if is_missing(value):

        return None


    if isinstance(
        value,
        np.generic,
    ):

        try:

            return value.item()

        except Exception:

            pass


    if isinstance(
        value,
        pd.Timestamp,
    ):

        return value.isoformat()


    if isinstance(
        value,
        (datetime, date),
    ):

        return value.isoformat()


    if isinstance(
        value,
        Decimal,
    ):

        return float(value)


    return value


def make_json_safe(value):

    if isinstance(
        value,
        dict,
    ):

        return {
            str(key): make_json_safe(val)
            for key, val in value.items()
        }


    if isinstance(
        value,
        (list, tuple),
    ):

        return [
            make_json_safe(item)
            for item in value
        ]


    if isinstance(
        value,
        set,
    ):

        return [
            make_json_safe(item)
            for item in value
        ]


    return clean_value(value)


# ============================================================
# Load all rows
# ============================================================

def load_all_rows(
    supabase_client: Client,
    table_name: str,
    batch_size: int = 1000,
):

    rows = []

    start = 0


    while True:

        response = (
            supabase_client
            .table(table_name)
            .select("*")
            .range(
                start,
                start + batch_size - 1,
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
# Pending-change helpers
# ============================================================

def pending_changes_count():

    return (
        len(
            st.session_state.pending_updates
        )
        +
        len(
            st.session_state.pending_inserts
        )
        +
        len(
            st.session_state.pending_deletes
        )
    )


def clear_pending_changes():

    st.session_state.pending_updates = {}

    st.session_state.pending_inserts = []

    st.session_state.pending_deletes = set()


def make_temp_id():

    return (
        "__NEW__"
        + str(
            st.session_state.grid_version
        )
        + "_"
        + str(
            len(
                st.session_state.pending_inserts
            )
            + 1
        )
    )


# ============================================================
# Build display dataframe
# ============================================================

def build_display_dataframe():

    if (
        st.session_state.db_df
        is None
    ):

        return pd.DataFrame()


    df = (
        st.session_state.db_df
        .copy()
    )


    if PRIMARY_KEY not in df.columns:

        st.error(
            f"Primary key '{PRIMARY_KEY}' "
            "was not found in the database table."
        )

        return pd.DataFrame()


    # --------------------------------------------------------
    # Temporary ID
    # --------------------------------------------------------

    if "_temp_id" not in df.columns:

        df["_temp_id"] = (
            df[PRIMARY_KEY]
            .astype(str)
        )


    # --------------------------------------------------------
    # Apply pending updates
    # --------------------------------------------------------

    for (
        pk,
        changes,
    ) in (
        st.session_state
        .pending_updates
        .items()
    ):

        mask = (
            df[PRIMARY_KEY]
            .astype(str)
            == str(pk)
        )


        for (
            column,
            value,
        ) in changes.items():

            if column in df.columns:

                df.loc[
                    mask,
                    column,
                ] = value


    # --------------------------------------------------------
    # Remove pending deletes
    # --------------------------------------------------------

    if st.session_state.pending_deletes:

        delete_keys = {
            str(x)
            for x in (
                st.session_state
                .pending_deletes
            )
        }


        delete_mask = (
            df[PRIMARY_KEY]
            .astype(str)
            .isin(delete_keys)
        )


        df = (
            df.loc[
                ~delete_mask
            ]
            .copy()
        )


    # --------------------------------------------------------
    # Add pending inserts
    # --------------------------------------------------------

    if st.session_state.pending_inserts:

        insert_df = pd.DataFrame(
            st.session_state
            .pending_inserts
        )


        if not insert_df.empty:

            # Add missing columns
            for column in df.columns:

                if column not in insert_df.columns:

                    insert_df[column] = None


            # Add new columns
            for column in insert_df.columns:

                if column not in df.columns:

                    df[column] = None


            insert_df = (
                insert_df[
                    df.columns
                ]
            )


            df = pd.concat(
                [
                    df,
                    insert_df,
                ],
                ignore_index=True,
            )


    return df


# ============================================================
# Capture AG Grid changes
# ============================================================

def capture_grid_changes(
    grid_df,
):

    if grid_df is None:

        return


    if grid_df.empty:

        return


    if PRIMARY_KEY not in grid_df.columns:

        return


    original_df = (
        st.session_state.db_df
    )


    if original_df is None:

        return


    # --------------------------------------------------------
    # Original row lookup
    # --------------------------------------------------------

    original_lookup = {}


    for _, row in original_df.iterrows():

        pk = row.get(
            PRIMARY_KEY
        )


        if not is_missing(pk):

            original_lookup[
                str(pk)
            ] = row.to_dict()


    # --------------------------------------------------------
    # Process rows
    # --------------------------------------------------------

    for _, row in grid_df.iterrows():

        row_data = row.to_dict()


        temp_id = row_data.get(
            "_temp_id"
        )


        # ====================================================
        # New row
        # ====================================================

        if (
            temp_id is not None
            and str(
                temp_id
            ).startswith(
                "__NEW__"
            )
        ):

            found_index = None


            for (
                index,
                existing,
            ) in enumerate(
                st.session_state
                .pending_inserts
            ):

                if (
                    str(
                        existing.get(
                            "_temp_id"
                        )
                    )
                    == str(temp_id)
                ):

                    found_index = index

                    break


            cleaned_row = {}


            for (
                key,
                value,
            ) in row_data.items():

                if key == "_temp_id":

                    cleaned_row[
                        key
                    ] = temp_id

                else:

                    cleaned_row[
                        key
                    ] = clean_value(value)


            if found_index is None:

                st.session_state.pending_inserts.append(
                    cleaned_row
                )

            else:

                st.session_state.pending_inserts[
                    found_index
                ] = cleaned_row


            continue


        # ====================================================
        # Existing row
        # ====================================================

        pk = row_data.get(
            PRIMARY_KEY
        )


        if is_missing(pk):

            continue


        pk_string = str(pk)


        if pk_string not in original_lookup:

            continue


        original_row = (
            original_lookup[
                pk_string
            ]
        )


        changes = {}


        for (
            column,
            new_value,
        ) in row_data.items():

            if column == "_temp_id":

                continue


            old_value = (
                original_row.get(
                    column
                )
            )


            old_missing = is_missing(
                old_value
            )

            new_missing = is_missing(
                new_value
            )


            if (
                old_missing
                and new_missing
            ):

                continue


            if (
                old_missing
                and not new_missing
            ):

                changes[
                    column
                ] = clean_value(
                    new_value
                )

                continue


            if (
                not old_missing
                and new_missing
            ):

                changes[
                    column
                ] = None

                continue


            if str(
                old_value
            ) != str(
                new_value
            ):

                changes[
                    column
                ] = clean_value(
                    new_value
                )


        if changes:

            existing_changes = (
                st.session_state
                .pending_updates
                .get(
                    pk_string,
                    {},
                )
            )


            existing_changes.update(
                changes
            )


            st.session_state.pending_updates[
                pk_string
            ] = existing_changes


        else:

            st.session_state.pending_updates.pop(
                pk_string,
                None,
            )


# ============================================================
# Add new row
# ============================================================

def add_new_row():

    if not can_add:

        return


    if st.session_state.db_df is None:

        return


    columns = list(
        st.session_state
        .db_df
        .columns
    )


    new_row = {
        column: None
        for column in columns
    }


    new_row[
        "_temp_id"
    ] = make_temp_id()


    if PRIMARY_KEY in columns:

        new_row[
            PRIMARY_KEY
        ] = None


    st.session_state.pending_inserts.append(
        new_row
    )


    st.session_state.grid_version += 1


# ============================================================
# Delete selected rows
# ============================================================

def process_selected_deletes(
    selected_rows,
):

    if not can_delete:

        return


    if not selected_rows:

        return


    deleted_count = 0


    for row in selected_rows:

        temp_id = row.get(
            "_temp_id"
        )


        # ----------------------------------------------------
        # Unsaved new row
        # ----------------------------------------------------

        if (
            temp_id is not None
            and str(
                temp_id
            ).startswith(
                "__NEW__"
            )
        ):

            st.session_state.pending_inserts = [
                item
                for item in (
                    st.session_state
                    .pending_inserts
                )
                if str(
                    item.get(
                        "_temp_id"
                    )
                )
                != str(temp_id)
            ]


            deleted_count += 1

            continue


        # ----------------------------------------------------
        # Existing row
        # ----------------------------------------------------

        pk = row.get(
            PRIMARY_KEY
        )


        if is_missing(pk):

            continue


        pk_string = str(pk)


        st.session_state.pending_deletes.add(
            pk_string
        )


        # Delete takes priority over update
        st.session_state.pending_updates.pop(
            pk_string,
            None,
        )


        deleted_count += 1


    if deleted_count:

        st.session_state.grid_version += 1


# ============================================================
# Sync changes
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


    if not (
        updates
        or inserts
        or deletes
    ):

        st.info(
            "There are no pending changes."
        )

        return


    successful_updates = []

    successful_inserts = []

    successful_deletes = []

    errors = []


    total_operations = (
        len(updates)
        +
        len(inserts)
        +
        len(deletes)
    )


    completed = 0


    progress = st.progress(0)


    def update_progress():

        nonlocal completed

        completed += 1

        progress.progress(
            min(
                completed
                / max(
                    total_operations,
                    1,
                ),
                1.0,
            )
        )


    # ========================================================
    # UPDATE
    # ========================================================

    for (
        pk,
        update_data,
    ) in updates.items():

        try:

            safe_data = make_json_safe(
                update_data
            )


            safe_data.pop(
                PRIMARY_KEY,
                None,
            )


            if not safe_data:

                successful_updates.append(
                    pk
                )

                update_progress()

                continue


            response = (
                client
                .table(TABLE_NAME)
                .update(safe_data)
                .eq(
                    PRIMARY_KEY,
                    pk,
                )
                .execute()
            )


            if response.data:

                successful_updates.append(
                    pk
                )

            else:

                errors.append(
                    f"UPDATE {pk}: "
                    "No row returned."
                )


        except Exception as exc:

            errors.append(
                f"UPDATE {pk}: {exc}"
            )


        update_progress()


    # ========================================================
    # INSERT
    # ========================================================

    for insert_row in inserts:

        temp_id = insert_row.get(
            "_temp_id"
        )


        try:

            data = {
                key: value
                for key, value
                in insert_row.items()
                if key != "_temp_id"
            }


            data = make_json_safe(
                data
            )


            # Do not send temporary primary key
            # when it is None.
            if (
                PRIMARY_KEY in data
                and data[PRIMARY_KEY] is None
            ):

                data.pop(
                    PRIMARY_KEY,
                    None,
                )


            response = (
                client
                .table(TABLE_NAME)
                .insert(data)
                .execute()
            )


            if response.data:

                successful_inserts.append(
                    temp_id
                )

            else:

                errors.append(
                    "INSERT: "
                    "No row returned."
                )


        except Exception as exc:

            errors.append(
                f"INSERT: {exc}"
            )


        update_progress()


    # ========================================================
    # DELETE
    # ========================================================

    for pk in deletes:

        try:

            response = (
                client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    PRIMARY_KEY,
                    pk,
                )
                .execute()
            )


            if response.data:

                successful_deletes.append(
                    pk
                )

            else:

                errors.append(
                    f"DELETE {pk}: "
                    "No row returned."
                )


        except Exception as exc:

            errors.append(
                f"DELETE {pk}: {exc}"
            )


        update_progress()


    progress.empty()


    # ========================================================
    # Remove successful updates
    # ========================================================

    for pk in successful_updates:

        st.session_state.pending_updates.pop(
            pk,
            None,
        )


    # ========================================================
    # Remove successful inserts
    # ========================================================

    successful_insert_ids = {
        str(x)
        for x in successful_inserts
        if x is not None
    }


    st.session_state.pending_inserts = [
        row
        for row in (
            st.session_state
            .pending_inserts
        )
        if str(
            row.get(
                "_temp_id"
            )
        )
        not in successful_insert_ids
    ]


    # ========================================================
    # Remove successful deletes
    # ========================================================

    for pk in successful_deletes:

        st.session_state.pending_deletes.discard(
            pk
        )


    # ========================================================
    # Results
    # ========================================================

    if successful_updates:

        st.success(
            f"Updated "
            f"{len(successful_updates):,} "
            "row(s)."
        )


    if successful_inserts:

        st.success(
            f"Inserted "
            f"{len(successful_inserts):,} "
            "row(s)."
        )


    if successful_deletes:

        st.success(
            f"Deleted "
            f"{len(successful_deletes):,} "
            "row(s)."
        )


    if errors:

        st.error(
            f"{len(errors):,} "
            "operation(s) failed."
        )


        with st.expander(
            "Show errors"
        ):

            for error in errors:

                st.write(error)


    # ========================================================
    # Reload after complete successful sync
    # ========================================================

    if pending_changes_count() == 0:

        try:

            st.session_state.db_df = (
                load_all_rows(
                    client,
                    TABLE_NAME,
                    BATCH_SIZE,
                )
            )


            st.session_state.grid_version += 1


        except Exception as exc:

            st.error(
                "Database reload failed: "
                f"{exc}"
            )


# ============================================================
# Header
# ============================================================

header_col1, header_col2 = st.columns(
    [5, 1]
)


with header_col1:

    st.title(
        "YgnTBPro Database"
    )


    st.caption(
        f"Logged in as: "
        f"{user_email or 'Unknown'} "
        f"| Role: {role}"
    )


with header_col2:

    if st.button(
        "Logout",
        use_container_width=True,
    ):

        logout_user()

        st.rerun()


# ============================================================
# Load database
# ============================================================

if st.session_state.db_df is None:

    with st.spinner(
        "Loading all database records..."
    ):

        try:

            st.session_state.db_df = (
                load_all_rows(
                    client,
                    TABLE_NAME,
                    BATCH_SIZE,
                )
            )

        except Exception as exc:

            st.error(
                "Could not load database: "
                f"{exc}"
            )

            st.stop()


db_df = (
    st.session_state.db_df
)


# ============================================================
# Database summary
# ============================================================

total_rows = len(db_df)

total_columns = len(
    db_df.columns
)

pending_count = (
    pending_changes_count()
)


c1, c2, c3, c4 = st.columns(4)


with c1:

    st.metric(
        "Database Rows",
        f"{total_rows:,}",
    )


with c2:

    st.metric(
        "Columns",
        f"{total_columns:,}",
    )


with c3:

    st.metric(
        "Pending Changes",
        f"{pending_count:,}",
    )


memory_mb = (
    db_df
    .memory_usage(
        deep=True
    )
    .sum()
    / 1024
    / 1024
)


with c4:

    st.metric(
        "Memory",
        f"{memory_mb:.2f} MB",
    )


# ============================================================
# Tabs
# ============================================================

tab_explorer, tab_editor = st.tabs(
    [
        "🔎 Explorer & Search",
        "✏️ Database Editor",
    ]
)


# ============================================================
# Explorer
# ============================================================

with tab_explorer:

    st.subheader(
        "Explorer & Search"
    )


    explorer_df = (
        build_display_dataframe()
    )


    if explorer_df.empty:

        st.info(
            "No records available."
        )

    else:

        search_text = st.text_input(
            "Search all columns",
            placeholder="Enter keyword...",
            key="global_search",
        )


        filtered_df = (
            explorer_df.copy()
        )


        if search_text.strip():

            search_value = (
                search_text
                .strip()
                .lower()
            )


            mask = pd.Series(
                False,
                index=filtered_df.index,
            )


            for column in (
                filtered_df.columns
            ):

                try:

                    mask = (
                        mask
                        |
                        filtered_df[
                            column
                        ]
                        .astype(str)
                        .str.lower()
                        .str.contains(
                            search_value,
                            na=False,
                        )
                    )

                except Exception:

                    pass


            filtered_df = (
                filtered_df.loc[
                    mask
                ]
            )


        st.caption(
            f"Showing "
            f"{len(filtered_df):,} "
            f"of "
            f"{len(explorer_df):,} "
            "rows"
        )


        st.dataframe(
            filtered_df,
            use_container_width=True,
            height=600,
            hide_index=True,
        )


# ============================================================
# Database Editor
# ============================================================

with tab_editor:

    st.subheader(
        "Database Editor"
    )


    if not can_edit:

        st.info(
            "You have Viewer access. "
            "You can view the database but "
            "cannot edit it."
        )


    # ========================================================
    # Editor toolbar
    # ========================================================

    if can_edit:

        col1, col2, col3, col4 = (
            st.columns(
                [
                    1.2,
                    1.2,
                    1.2,
                    4,
                ]
            )
        )


        # ----------------------------------------------------
        # Add
        # ----------------------------------------------------

        with col1:

            if st.button(
                "➕ Add Row",
                disabled=not can_add,
                use_container_width=True,
            ):

                add_new_row()

                st.rerun()


        # ----------------------------------------------------
        # Delete
        # ----------------------------------------------------

        with col2:

            if st.button(
                "🗑️ Delete Selected",
                disabled=not can_delete,
                use_container_width=True,
            ):

                selected = (
                    st.session_state
                    .get(
                        "selected_grid_rows",
                        [],
                    )
                )


                process_selected_deletes(
                    selected
                )


                st.rerun()


        # ----------------------------------------------------
        # Sync
        # ----------------------------------------------------

        with col3:

            if st.button(
                "💾 Sync",
                disabled=(
                    pending_changes_count()
                    == 0
                ),
                use_container_width=True,
            ):

                sync_changes()

                st.rerun()


        # ----------------------------------------------------
        # Discard
        # ----------------------------------------------------

        with col4:

            if st.button(
                "↩️ Discard",
                disabled=(
                    pending_changes_count()
                    == 0
                ),
                use_container_width=True,
            ):

                clear_pending_changes()

                st.session_state.grid_version += 1

                st.success(
                    "All pending changes discarded."
                )

                st.rerun()


    # ========================================================
    # Pending status
    # ========================================================

    pending_count = (
        pending_changes_count()
    )


    if pending_count:

        st.warning(
            f"You have "
            f"{pending_count:,} "
            "pending change(s). "
            "Changes are not written to "
            "Supabase until you click Sync."
        )


    # ========================================================
    # Editor dataframe
    # ========================================================

    editor_df = (
        build_display_dataframe()
    )


    if editor_df.empty:

        st.info(
            "No data available."
        )

    else:

        # ====================================================
        # AG Grid
        # ====================================================

        gb = (
            GridOptionsBuilder
            .from_dataframe(
                editor_df
            )
        )


        gb.configure_default_column(
            editable=can_edit,
            sortable=True,
            filter=True,
            resizable=True,
            minWidth=120,
        )


        # ----------------------------------------------------
        # Selection
        # ----------------------------------------------------

        gb.configure_selection(
            selection_mode="multiple",
            use_checkbox=can_delete,
        )


        # ----------------------------------------------------
        # Temporary ID
        # ----------------------------------------------------

        if "_temp_id" in editor_df.columns:

            gb.configure_column(
                "_temp_id",
                hide=True,
                editable=False,
            )


        # ----------------------------------------------------
        # Primary key
        # ----------------------------------------------------

        if PRIMARY_KEY in editor_df.columns:

            gb.configure_column(
                PRIMARY_KEY,
                editable=False,
            )


        # ----------------------------------------------------
        # Grid options
        # ----------------------------------------------------

        gb.configure_grid_options(
            rowSelection="multiple",
            suppressRowClickSelection=False,
            pagination=True,
            paginationPageSize=100,
            domLayout="normal",
        )


        grid_options = gb.build()


        grid_response = AgGrid(
            editor_df,
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
            allow_unsafe_jscode=True,
            enable_enterprise_modules=False,
            height=650,
            key=(
                "database_grid_"
                f"{st.session_state.grid_version}"
            ),
        )


        # ====================================================
        # Capture edits
        # ====================================================

        returned_df = (
            grid_response.get(
                "data"
            )
        )


        if returned_df is not None:

            try:

                returned_df = pd.DataFrame(
                    returned_df
                )


                capture_grid_changes(
                    returned_df
                )


            except Exception as exc:

                st.warning(
                    "Could not capture "
                    f"grid changes: {exc}"
                )


        # ====================================================
        # Capture selection
        # ====================================================

        selected_rows = (
            grid_response.get(
                "selected_rows",
                [],
            )
        )


        if selected_rows is None:

            selected_rows = []


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


        st.session_state.selected_grid_rows = (
            selected_rows
        )


# ============================================================
# Footer
# ============================================================

st.divider()

st.caption(
    "YgnTBPro • Supabase • Streamlit • AG Grid"
)