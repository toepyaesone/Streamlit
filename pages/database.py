# database.py

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
# Configuration
# ============================================================

TABLE_NAME = "ygntbpro"
PRIMARY_KEY = "PatientID"
BATCH_SIZE = 1000

SUPABASE_URL_ygntbpro = "https://kocihpxevlowqbguhstf.supabase.co"
SUPABASE_KEY_ygntbpro = "sb_publishable_JtrNLjMNSvZ5LzvXKbv2xw_mj-hl5MD"

SUPABASE_URL = st.secrets.get("SUPABASE_URL_ygntbpro", os.getenv("SUPABASE_URL", SUPABASE_URL_ygntbpro))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY_ygntbpro", os.getenv("SUPABASE_KEY", SUPABASE_KEY_ygntbpro))


#SUPABASE_URL = st.secrets.get("SUPABASE_URL_ygntbpro")
#SUPABASE_KEY = st.secrets.get("SUPABASE_KEY_ygntbpro")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error(
        "Supabase configuration is missing. "
        "Please check SUPABASE_URL_ygntbpro and SUPABASE_KEY_ygntbpro."
    )
    st.stop()


# ============================================================
# Supabase clients
# ============================================================

@st.cache_resource
def get_base_client() -> Client:
    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )


base_supabase = get_base_client()


def get_user_client() -> Client:
    """
    Create a Supabase client using the current authenticated
    session whenever possible.
    """

    client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    try:
        session = base_supabase.auth.get_session()

        if session and session.session:
            access_token = session.session.access_token
            refresh_token = session.session.refresh_token

            if access_token:
                client.auth.set_session(
                    access_token,
                    refresh_token,
                )

    except Exception:
        pass

    return client


# ============================================================
# Authentication
# ============================================================

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "user_email" not in st.session_state:
    st.session_state.user_email = None


def login_user(email: str, password: str):
    try:
        email = email.strip().lower()

        response = base_supabase.auth.sign_in_with_password(
            {
                "email": email,
                "password": password,
            }
        )

        if response.user:

            authenticated_email = (
                response.user.email or email
            ).strip().lower()

            st.session_state.authenticated = True
            st.session_state.user_email = authenticated_email
            st.session_state.user_id = response.user.id

            return True, None

        return False, "Authentication failed."

    except Exception as e:
        return False, str(e)


def logout_user():
    try:
        base_supabase.auth.sign_out()
    except Exception:
        pass

    for key in [
        "authenticated",
        "user_email",
        "user_id",
        "role",
        "db_df",
        "pending_updates",
        "pending_inserts",
        "pending_deletes",
        "grid_version",
    ]:
        st.session_state.pop(key, None)


# ============================================================
# Login screen
# ============================================================

if not st.session_state.authenticated:

    st.title("YgnTBPro Database")

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

        success, error = login_user(
            email,
            password,
        )

        if success:
            st.rerun()
        else:
            st.error(error)

    st.stop()


# ============================================================
# Authenticated Supabase client
# ============================================================

client = get_user_client()


# ============================================================
# User role / permissions
# ============================================================

def get_user_role(
    supabase_client: Client,
    user_email: str,
) -> str:
    """
    Get the application role from the user_role table
    using the authenticated user's email.
    """

    if not user_email:
        return "viewer"

    try:
        response = (
            supabase_client
            .table("user_role")
            .select("role")
            .eq("email", user_email.strip().lower())
            .limit(1)
            .execute()
        )

        rows = response.data or []

        if rows:
            role_value = rows[0].get("role")

            if role_value:
                role_value = str(role_value).strip().lower()

                # Only allow known application roles
                if role_value in {
                    "viewer",
                    "editor",
                    "admin",
                }:
                    return role_value

    except Exception as e:
        st.warning(
            f"Could not read user role: {e}"
        )

    # Safe default
    return "viewer"


# ------------------------------------------------------------
# Get role using the authenticated login email
# ------------------------------------------------------------

user_email = (
    st.session_state.get("user_email")
)

role = get_user_role(
    client,
    user_email,
)

st.session_state.role = role


# ------------------------------------------------------------
# Permissions
# ------------------------------------------------------------

can_edit = role in {
    "editor",
    "admin",
}

can_add = role == "admin"

can_delete = role == "admin"


# ============================================================
# Session-state initialization
# ============================================================

if "db_df" not in st.session_state:
    st.session_state.db_df = None

if "pending_updates" not in st.session_state:
    st.session_state.pending_updates = {}

if "pending_inserts" not in st.session_state:
    st.session_state.pending_inserts = []

if "pending_deletes" not in st.session_state:
    st.session_state.pending_deletes = set()

if "grid_version" not in st.session_state:
    st.session_state.grid_version = 0


# ============================================================
# JSON-safe value conversion
# ============================================================

def is_missing(value):

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

    if value is None:
        return None

    if is_missing(value):
        return None

    if isinstance(value, np.generic):

        try:
            return value.item()

        except Exception:
            pass

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def make_json_safe(value):

    if isinstance(value, dict):

        return {
            str(key): make_json_safe(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple)):

        return [
            make_json_safe(item)
            for item in value
        ]

    if isinstance(value, set):

        return [
            make_json_safe(item)
            for item in value
        ]

    return clean_value(value)


# ============================================================
# Load all rows from Supabase
# ============================================================

@st.cache_data(ttl=60, show_spinner=False)
def load_all_rows_cached(
    table_name: str,
    batch_size: int,
):

    rows = []
    start = 0

    while True:

        response = (
            base_supabase
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
        len(st.session_state.pending_updates)
        + len(st.session_state.pending_inserts)
        + len(st.session_state.pending_deletes)
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
            ) + 1
        )
    )


# ============================================================
# Build display dataframe
# ============================================================

def build_display_dataframe():

    if st.session_state.db_df is None:

        return pd.DataFrame()

    df = st.session_state.db_df.copy()

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

        df["_temp_id"] = df[PRIMARY_KEY].astype(str)

    # --------------------------------------------------------
    # Apply pending updates
    # --------------------------------------------------------

    for pk, changes in (
        st.session_state.pending_updates.items()
    ):

        mask = (
            df[PRIMARY_KEY].astype(str)
            == str(pk)
        )

        for column, value in changes.items():

            if column in df.columns:

                df.loc[mask, column] = value

    # --------------------------------------------------------
    # Mark pending deletes
    # --------------------------------------------------------

    if st.session_state.pending_deletes:

        delete_mask = (
            df[PRIMARY_KEY]
            .astype(str)
            .isin(
                {
                    str(x)
                    for x in st.session_state.pending_deletes
                }
            )
        )

        df = df.loc[
            ~delete_mask
        ].copy()

    # --------------------------------------------------------
    # Add pending inserts
    # --------------------------------------------------------

    if st.session_state.pending_inserts:

        insert_df = pd.DataFrame(
            st.session_state.pending_inserts
        )

        if not insert_df.empty:

            for column in df.columns:

                if column not in insert_df.columns:

                    insert_df[column] = None

            for column in insert_df.columns:

                if column not in df.columns:

                    df[column] = None

            insert_df = insert_df[
                df.columns
            ]

            df = pd.concat(
                [
                    df,
                    insert_df,
                ],
                ignore_index=True,
            )

    return df


# ============================================================
# Capture AG Grid edits
# ============================================================

def capture_grid_changes(grid_df):

    if grid_df is None:
        return

    if grid_df.empty:
        return

    if PRIMARY_KEY not in grid_df.columns:
        return

    original_df = st.session_state.db_df

    if original_df is None:
        return

    original_lookup = {}

    for _, row in original_df.iterrows():

        pk = row.get(PRIMARY_KEY)

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

        # ----------------------------------------------------
        # New row
        # ----------------------------------------------------

        if (
            temp_id is not None
            and str(temp_id).startswith("__NEW__")
        ):

            found_index = None

            for index, existing in enumerate(
                st.session_state.pending_inserts
            ):

                if (
                    str(
                        existing.get("_temp_id")
                    )
                    == str(temp_id)
                ):

                    found_index = index
                    break

            cleaned_row = {}

            for key, value in row_data.items():

                if key == "_temp_id":
                    cleaned_row[key] = temp_id
                else:
                    cleaned_row[key] = clean_value(value)

            if found_index is None:

                st.session_state.pending_inserts.append(
                    cleaned_row
                )

            else:

                st.session_state.pending_inserts[
                    found_index
                ] = cleaned_row

            continue

        # ----------------------------------------------------
        # Existing row
        # ----------------------------------------------------

        pk = row_data.get(
            PRIMARY_KEY
        )

        if is_missing(pk):
            continue

        pk_string = str(pk)

        if pk_string not in original_lookup:
            continue

        original_row = original_lookup[
            pk_string
        ]

        changes = {}

        for column, new_value in row_data.items():

            if column == "_temp_id":
                continue

            old_value = original_row.get(
                column
            )

            old_missing = is_missing(
                old_value
            )

            new_missing = is_missing(
                new_value
            )

            if old_missing and new_missing:
                continue

            if (
                old_missing
                and not new_missing
            ):

                changes[column] = clean_value(
                    new_value
                )
                continue

            if (
                not old_missing
                and new_missing
            ):

                changes[column] = None
                continue

            if str(old_value) != str(new_value):

                changes[column] = clean_value(
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

            # If all values have returned
            # to their original state,
            # remove the pending update.
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

    columns = list(
        st.session_state.db_df.columns
    )

    new_row = {
        column: None
        for column in columns
    }

    new_row["_temp_id"] = make_temp_id()

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
    selected_rows
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
        # Delete unsynced new row
        # ----------------------------------------------------

        if (
            temp_id is not None
            and str(temp_id).startswith("__NEW__")
        ):

            st.session_state.pending_inserts = [
                item
                for item in (
                    st.session_state
                    .pending_inserts
                )
                if str(
                    item.get("_temp_id")
                ) != str(temp_id)
            ]

            deleted_count += 1
            continue

        # ----------------------------------------------------
        # Delete existing row
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

        # If the row had a pending update,
        # deletion takes priority.
        st.session_state.pending_updates.pop(
            pk_string,
            None,
        )

        deleted_count += 1

    if deleted_count:

        st.session_state.grid_version += 1

        st.success(
            f"{deleted_count} row(s) marked for deletion."
        )


# ============================================================
# Sync changes to Supabase
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

    progress = st.progress(0)

    total_operations = (
        len(updates)
        + len(inserts)
        + len(deletes)
    )

    completed = 0

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for pk, update_data in updates.items():

        try:

            safe_data = make_json_safe(
                update_data
            )

            if PRIMARY_KEY in safe_data:
                safe_data.pop(
                    PRIMARY_KEY,
                    None,
                )

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

        except Exception as e:

            errors.append(
                f"UPDATE {pk}: {e}"
            )

        completed += 1

        progress.progress(
            min(
                completed
                / max(total_operations, 1),
                1.0,
            )
        )

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    for insert_row in inserts:

        try:

            data = {
                key: value
                for key, value in insert_row.items()
                if key != "_temp_id"
            }

            data = make_json_safe(
                data
            )

            response = (
                client
                .table(TABLE_NAME)
                .insert(data)
                .execute()
            )

            if response.data:

                successful_inserts.append(
                    insert_row.get("_temp_id")
                )

            else:

                errors.append(
                    "INSERT: No row returned."
                )

        except Exception as e:

            errors.append(
                f"INSERT: {e}"
            )

        completed += 1

        progress.progress(
            min(
                completed
                / max(total_operations, 1),
                1.0,
            )
        )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

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

        except Exception as e:

            errors.append(
                f"DELETE {pk}: {e}"
            )

        completed += 1

        progress.progress(
            min(
                completed
                / max(total_operations, 1),
                1.0,
            )
        )

    progress.empty()

    # --------------------------------------------------------
    # Remove successful updates
    # --------------------------------------------------------

    for pk in successful_updates:

        st.session_state.pending_updates.pop(
            pk,
            None,
        )

    # --------------------------------------------------------
    # Remove successful inserts
    # --------------------------------------------------------

    successful_insert_ids = {
        str(x)
        for x in successful_inserts
        if x is not None
    }

    st.session_state.pending_inserts = [
        row
        for row in (
            st.session_state.pending_inserts
        )
        if str(
            row.get("_temp_id")
        )
        not in successful_insert_ids
    ]

    # --------------------------------------------------------
    # Remove successful deletes
    # --------------------------------------------------------

    for pk in successful_deletes:

        st.session_state.pending_deletes.discard(
            pk
        )

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    if successful_updates:
        st.success(
            f"Updated {len(successful_updates)} row(s)."
        )

    if successful_inserts:
        st.success(
            f"Inserted {len(successful_inserts)} row(s)."
        )

    if successful_deletes:
        st.success(
            f"Deleted {len(successful_deletes)} row(s)."
        )

    if errors:

        st.error(
            f"{len(errors)} operation(s) failed."
        )

        with st.expander(
            "Show errors"
        ):

            for error in errors:
                st.write(error)

    # --------------------------------------------------------
    # Reload database only when there are
    # no remaining pending changes
    # --------------------------------------------------------

    if pending_changes_count() == 0:

        try:

            st.cache_data.clear()

            st.session_state.db_df = (
                load_all_rows(
                    client,
                    TABLE_NAME,
                    BATCH_SIZE,
                )
            )

            st.session_state.grid_version += 1

            st.success(
                "Database synchronized successfully."
            )

        except Exception as e:

            st.error(
                f"Database reload failed: {e}"
            )


# ============================================================
# Main UI
# ============================================================

st.set_page_config(
    page_title="YgnTBPro Database",
    page_icon="🗄️",
    layout="wide",
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
        f"{st.session_state.user_email} "
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
# Load database snapshot
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

        except Exception as e:

            st.error(
                f"Could not load database: {e}"
            )

            st.stop()


db_df = st.session_state.db_df


# ============================================================
# Database summary
# ============================================================

total_rows = len(db_df)
total_columns = len(db_df.columns)
pending_count = pending_changes_count()

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

# IMPORTANT:
# Keep the calculation outside the f-string.
# This avoids the Python 3.14 parsing problem.
memory_mb = (
    db_df
    .memory_usage(deep=True)
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

    explorer_df = build_display_dataframe()

    if explorer_df.empty:

        st.info(
            "No records available."
        )

    else:

        # ----------------------------------------------------
        # Search
        # ----------------------------------------------------

        search_text = st.text_input(
            "Search all columns",
            placeholder="Enter keyword...",
            key="global_search",
        )

        filtered_df = explorer_df.copy()

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

            for column in filtered_df.columns:

                try:

                    mask = (
                        mask
                        | filtered_df[column]
                        .astype(str)
                        .str
                        .lower()
                        .str
                        .contains(
                            search_value,
                            na=False,
                        )
                    )

                except Exception:
                    pass

            filtered_df = filtered_df.loc[
                mask
            ]

        # ----------------------------------------------------
        # Explorer dataframe
        # ----------------------------------------------------

        st.caption(
            f"Showing "
            f"{len(filtered_df):,} "
            f"of "
            f"{len(explorer_df):,} "
            f"rows"
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
            "You can view the database but cannot edit it."
        )

    # --------------------------------------------------------
    # Editor toolbar
    # --------------------------------------------------------

    if can_edit:

        col1, col2, col3, col4 = st.columns(
            [
                1.2,
                1.2,
                1.2,
                4,
            ]
        )

        with col1:

            if st.button(
                "➕ Add Row",
                disabled=not can_add,
                use_container_width=True,
            ):

                add_new_row()
                st.rerun()

        with col2:

            if st.button(
                "🗑️ Delete Selected",
                disabled=not can_delete,
                use_container_width=True,
            ):

                selected = st.session_state.get(
                    "selected_grid_rows",
                    [],
                )

                process_selected_deletes(
                    selected
                )

                st.rerun()

        with col3:

            if st.button(
                "💾 Sync",
                disabled=pending_count == 0,
                use_container_width=True,
            ):

                sync_changes()
                st.rerun()

        with col4:

            if st.button(
                "↩️ Discard",
                disabled=pending_count == 0,
                use_container_width=True,
            ):

                clear_pending_changes()

                st.session_state.grid_version += 1

                st.success(
                    "All pending changes discarded."
                )

                st.rerun()

    # --------------------------------------------------------
    # Pending status
    # --------------------------------------------------------

    pending_count = pending_changes_count()

    if pending_count:

        st.warning(
            f"You have {pending_count:,} "
            "pending change(s). "
            "Changes are not written to Supabase "
            "until you click Sync."
        )

    # --------------------------------------------------------
    # Display dataframe
    # --------------------------------------------------------

    editor_df = build_display_dataframe()

    if editor_df.empty:

        st.info(
            "No data available."
        )

    else:

        # ----------------------------------------------------
        # AG Grid
        # ----------------------------------------------------

        gb = GridOptionsBuilder.from_dataframe(
            editor_df
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
        # Hide internal column
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
        # Grid height
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
            data_return_mode=DataReturnMode.AS_INPUT,
            update_mode=(
                GridUpdateMode.VALUE_CHANGED
                | GridUpdateMode.SELECTION_CHANGED
            ),
            fit_columns_on_grid_load=False,
            allow_unsafe_jscode=True,
            enable_enterprise_modules=False,
            height=650,
            key=f"database_grid_{st.session_state.grid_version}",
        )

        # ----------------------------------------------------
        # Capture edited data
        # ----------------------------------------------------

        returned_df = grid_response.get(
            "data"
        )

        if returned_df is not None:

            try:

                returned_df = pd.DataFrame(
                    returned_df
                )

                capture_grid_changes(
                    returned_df
                )

            except Exception as e:

                st.warning(
                    f"Could not capture grid changes: {e}"
                )

        # ----------------------------------------------------
        # Capture selected rows
        # ----------------------------------------------------

        selected_rows = grid_response.get(
            "selected_rows",
            [],
        )

        if selected_rows is None:
            selected_rows = []

        if isinstance(
            selected_rows,
            pd.DataFrame,
        ):

            selected_rows = (
                selected_rows
                .to_dict("records")
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