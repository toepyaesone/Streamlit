
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

DATE_COLUMNS = [
    "DiagnosisDate",
    "DateDiagnosis",
    "Diagnosis_Date",
    "DateofDiagnosis",
    "DateOfDiagnosis",
    "Diagnosisdate",
]


# ============================================================
# SESSION DEFAULTS
# ============================================================

DEFAULTS = {
    "session": None,
    "authenticated": False,

    "user_email": None,
    "user_id": None,

    "user_role": "viewer",
    "role": "viewer",

    "db_df": None,

    # Pending database operations
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Grid
    "selected_grid_rows": [],

    # Changes to this number force a clean grid rebuild
    "grid_version": 0,

    # Filter reset counter
    "filter_reset_version": 0,

    # Prevent processing the same grid state repeatedly
    "last_processed_grid_version": None,
}


def initialize_session_state():

    for key, value in DEFAULTS.items():

        if key not in st.session_state:

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
# SUPABASE CREDENTIALS
# ============================================================

def get_supabase_credentials():

    try:

        return (
            st.secrets["supabase"]["url"],
            st.secrets["supabase"]["key"],
        )

    except Exception:

        try:

            return (
                st.secrets["SUPABASE_URL"],
                st.secrets["SUPABASE_KEY"],
            )

        except Exception as exc:

            st.error(
                "Supabase credentials were not found "
                "in Streamlit secrets."
            )

            st.exception(exc)
            st.stop()


SUPABASE_URL, SUPABASE_KEY = (
    get_supabase_credentials()
)


# ============================================================
# BASE SUPABASE CLIENT
# ============================================================

@st.cache_resource
def get_base_client():

    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )


base_supabase = get_base_client()


# ============================================================
# SUPABASE VALUE CONVERSION
# ============================================================

def json_safe(value):

    if value is None:
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

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, np.ndarray):
        return [
            json_safe(x)
            for x in value.tolist()
        ]

    if isinstance(value, dict):

        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):

        return [
            json_safe(x)
            for x in value
        ]

    return value


def clean_record(record):

    result = {}

    for key, value in record.items():

        if key == "_temp_id":
            continue

        result[key] = json_safe(value)

    return result


# ============================================================
# AUTHENTICATED USER CLIENT
# ============================================================

def get_user_client():

    session = st.session_state.get(
        "session"
    )

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

def login_user(
    email: str,
    password: str,
):

    try:

        email = email.strip()

        if not email:
            return False, "Please enter your email."

        if not password:
            return False, "Please enter your password."

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
                "Login failed: no authenticated "
                "session was returned.",
            )

        # ----------------------------------------------------
        # Store authentication
        # ----------------------------------------------------

        st.session_state.session = (
            response.session
        )

        st.session_state.user_email = (
            email.lower()
        )

        st.session_state.user_id = (
            response.session.user.id
        )

        st.session_state.authenticated = True

        # ----------------------------------------------------
        # Authenticated client
        # ----------------------------------------------------

        user_client = get_user_client()

        # ----------------------------------------------------
        # Get role from user_roles using user UUID
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

        else:

            user_role = "viewer"

        user_role = str(
            user_role
        ).strip().lower()

        if user_role not in {
            "viewer",
            "editor",
            "admin",
        }:

            user_role = "viewer"

        st.session_state.user_role = (
            user_role
        )

        st.session_state.role = (
            user_role
        )

        return True, "Login successful."

    except Exception as exc:

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
# CURRENT USER / ROLE
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

user_role = (
    st.session_state.get(
        "user_role"
    )
    or st.session_state.get(
        "role"
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

st.session_state.user_role = (
    user_role
)

st.session_state.role = (
    user_role
)

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
# AUTHENTICATED DATABASE CLIENT
# ============================================================

client = get_user_client()


# ============================================================
# LOAD ALL DATABASE ROWS
# ============================================================

def load_all_rows():

    rows = []

    start = 0

    while True:

        end = (
            start
            + BATCH_SIZE
            - 1
        )

        response = (
            client
            .table(TABLE_NAME)
            .select("*")
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


def refresh_database():

    try:

        df = load_all_rows()

        st.session_state.db_df = df

        return True

    except Exception as exc:

        st.error(
            f"Unable to load database: {exc}"
        )

        return False


# ============================================================
# INITIAL LOAD
# ============================================================

if st.session_state.db_df is None:

    with st.spinner(
        "Loading YgnTBPro data..."
    ):

        refresh_database()


db_df = (
    st.session_state.db_df
)

if db_df is None:

    db_df = pd.DataFrame()


# ============================================================
# PENDING CHANGE FUNCTIONS
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

    st.session_state.selected_grid_rows = []

    st.session_state.grid_version += 1


def existing_record(
    primary_value
):

    if db_df.empty:
        return None

    if PRIMARY_KEY not in db_df.columns:
        return None

    matches = db_df[
        db_df[PRIMARY_KEY]
        .astype(str)
        == str(primary_value)
    ]

    if matches.empty:
        return None

    return matches.iloc[0].to_dict()


def pending_insert_by_temp_id(
    temp_id
):

    for row in (
        st.session_state.pending_inserts
    ):

        if (
            row.get("_temp_id")
            == temp_id
        ):

            return row

    return None


# ============================================================
# BUILD DISPLAY DATA
# ============================================================

def build_display_dataframe():

    if db_df is None:

        result = pd.DataFrame()

    else:

        result = db_df.copy()

    # --------------------------------------------------------
    # Apply pending updates
    # --------------------------------------------------------

    if (
        not result.empty
        and PRIMARY_KEY in result.columns
    ):

        for primary_value, changes in (
            st.session_state.pending_updates.items()
        ):

            mask = (
                result[PRIMARY_KEY]
                .astype(str)
                == str(primary_value)
            )

            for column, value in changes.items():

                if column in result.columns:

                    result.loc[
                        mask,
                        column
                    ] = value

    # --------------------------------------------------------
    # Apply pending deletes
    # --------------------------------------------------------

    if (
        not result.empty
        and PRIMARY_KEY in result.columns
        and st.session_state.pending_deletes
    ):

        delete_ids = {
            str(x)
            for x in (
                st.session_state
                .pending_deletes
            )
        }

        result = result[
            ~result[PRIMARY_KEY]
            .astype(str)
            .isin(delete_ids)
        ]

    # --------------------------------------------------------
    # Add pending new rows
    # --------------------------------------------------------

    if st.session_state.pending_inserts:

        insert_df = pd.DataFrame(
            st.session_state.pending_inserts
        )

        if not insert_df.empty:

            for column in result.columns:

                if column not in insert_df.columns:

                    insert_df[column] = None

            for column in insert_df.columns:

                if column not in result.columns:

                    result[column] = None

            insert_df = insert_df[
                result.columns.tolist()
            ]

            result = pd.concat(
                [
                    result,
                    insert_df,
                ],
                ignore_index=True,
            )

    # --------------------------------------------------------
    # Internal row identifier
    # --------------------------------------------------------

    if "_temp_id" not in result.columns:

        result["_temp_id"] = [
            str(uuid.uuid4())
            for _ in range(
                len(result)
            )
        ]

    return result.reset_index(
        drop=True
    )


# ============================================================
# CREATE NEW ROW
# ============================================================

def add_new_row():

    if not can_add:
        return False

    if db_df.empty:
        return False

    new_row = {}

    for column in db_df.columns:

        new_row[column] = None

    # Internal identifier
    new_row["_temp_id"] = str(
        uuid.uuid4()
    )

    st.session_state.pending_inserts.append(
        new_row
    )

    st.session_state.grid_version += 1

    return True


# ============================================================
# DELETE SELECTED ROWS
# ============================================================

def delete_selected_rows():

    if not can_delete:
        return 0

    selected = (
        st.session_state.selected_grid_rows
    )

    if not selected:
        return 0

    deleted_count = 0

    selected_temp_ids = {
        row.get("_temp_id")
        for row in selected
        if row.get("_temp_id")
    }

    # --------------------------------------------------------
    # Remove newly inserted rows
    # --------------------------------------------------------

    original_insert_count = len(
        st.session_state.pending_inserts
    )

    st.session_state.pending_inserts = [
        row
        for row in (
            st.session_state.pending_inserts
        )
        if row.get("_temp_id")
        not in selected_temp_ids
    ]

    deleted_count += (
        original_insert_count
        - len(
            st.session_state.pending_inserts
        )
    )

    # --------------------------------------------------------
    # Existing database rows
    # --------------------------------------------------------

    for row in selected:

        primary_value = row.get(
            PRIMARY_KEY
        )

        temp_id = row.get(
            "_temp_id"
        )

        # New row already handled above.
        if temp_id in selected_temp_ids:
            continue

        if primary_value is None:
            continue

        primary_string = str(
            primary_value
        )

        # Mark for deletion.
        st.session_state.pending_deletes.add(
            primary_string
        )

        # No need to update a row that will be deleted.
        st.session_state.pending_updates.pop(
            primary_string,
            None,
        )

        deleted_count += 1

    st.session_state.selected_grid_rows = []

    if deleted_count:

        st.session_state.grid_version += 1

    return deleted_count


# ============================================================
# DISCARD CHANGES
# ============================================================

def discard_changes():

    st.session_state.pending_updates = {}

    st.session_state.pending_inserts = []

    st.session_state.pending_deletes = set()

    st.session_state.selected_grid_rows = []

    st.session_state.grid_version += 1


# ============================================================
# SYNC CHANGES
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

    total = (
        len(updates)
        + len(inserts)
        + len(deletes)
    )

    if total == 0:

        st.info(
            "There are no pending changes."
        )

        return False

    successful_updates = set()
    successful_inserts = []
    successful_deletes = set()

    errors = []

    completed = 0

    progress = st.progress(
        0,
        text="Synchronizing changes...",
    )

    # ========================================================
    # INSERT
    # ========================================================

    for row in inserts:

        payload = clean_record(row)

        # PatientID is allowed to be generated by DB
        # when empty.
        if PRIMARY_KEY in payload:

            value = payload.get(
                PRIMARY_KEY
            )

            if (
                value is None
                or str(value).strip() == ""
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

            successful_inserts.append(
                row
            )

        except Exception as exc:

            errors.append(
                f"INSERT failed: {exc}"
            )

        completed += 1

        progress.progress(
            completed / total
        )

    # ========================================================
    # UPDATE
    # ========================================================

    for primary_value, changes in updates.items():

        if not changes:

            successful_updates.add(
                str(primary_value)
            )

            completed += 1

            progress.progress(
                completed / total
            )

            continue

        # Never update primary key.
        changes = {
            key: value
            for key, value in changes.items()
            if key != PRIMARY_KEY
        }

        payload = clean_record(
            changes
        )

        if not payload:

            successful_updates.add(
                str(primary_value)
            )

            completed += 1

            progress.progress(
                completed / total
            )

            continue

        try:

            (
                client
                .table(TABLE_NAME)
                .update(payload)
                .eq(
                    PRIMARY_KEY,
                    json_safe(
                        primary_value
                    ),
                )
                .execute()
            )

            successful_updates.add(
                str(primary_value)
            )

        except Exception as exc:

            errors.append(
                f"UPDATE {primary_value} failed: "
                f"{exc}"
            )

        completed += 1

        progress.progress(
            completed / total
        )

    # ========================================================
    # DELETE
    # ========================================================

    for primary_value in deletes:

        try:

            (
                client
                .table(TABLE_NAME)
                .delete()
                .eq(
                    PRIMARY_KEY,
                    json_safe(
                        primary_value
                    ),
                )
                .execute()
            )

            successful_deletes.add(
                str(primary_value)
            )

        except Exception as exc:

            errors.append(
                f"DELETE {primary_value} failed: "
                f"{exc}"
            )

        completed += 1

        progress.progress(
            completed / total
        )

    progress.empty()

    # ========================================================
    # REMOVE ONLY SUCCESSFUL UPDATES
    # ========================================================

    for primary_value in successful_updates:

        st.session_state.pending_updates.pop(
            primary_value,
            None,
        )

    # ========================================================
    # REMOVE ONLY SUCCESSFUL INSERTS
    # ========================================================

    successful_temp_ids = {
        row.get("_temp_id")
        for row in successful_inserts
    }

    if successful_temp_ids:

        st.session_state.pending_inserts = [
            row
            for row in (
                st.session_state
                .pending_inserts
            )
            if row.get("_temp_id")
            not in successful_temp_ids
        ]

    # ========================================================
    # REMOVE ONLY SUCCESSFUL DELETES
    # ========================================================

    for primary_value in successful_deletes:

        st.session_state.pending_deletes.discard(
            primary_value
        )

    # ========================================================
    # RESULT
    # ========================================================

    if errors:

        st.warning(
            f"Sync completed with "
            f"{len(errors)} error(s)."
        )

        for error in errors:
            st.error(error)

    else:

        st.success(
            f"Successfully synchronized "
            f"{total} operation(s)."
        )

    # ========================================================
    # REFRESH DATABASE AFTER FULL SUCCESS
    # ========================================================

    if not errors:

        if refresh_database():

            st.session_state.selected_grid_rows = []

            st.session_state.grid_version += 1

    return True


# ============================================================
# FIND DATE COLUMN
# ============================================================

def find_date_column(df):

    if df is None or df.empty:
        return None

    for column in DATE_COLUMNS:

        if column in df.columns:
            return column

    return None


date_column = find_date_column(
    db_df
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "YgnTBPro System"
)

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
# SIDEBAR FILTER
# ============================================================

st.sidebar.divider()

st.sidebar.subheader(
    "Filters"
)

filter_key = (
    f"date_filter_"
    f"{st.session_state.filter_reset_version}"
)

date_from = None
date_to = None

if date_column:

    date_from = st.sidebar.date_input(
        "Date From",
        value=None,
        key=f"{filter_key}_from",
    )

    date_to = st.sidebar.date_input(
        "Date To",
        value=None,
        key=f"{filter_key}_to",
    )

    if st.sidebar.button(
        "🔄 Reset Filter",
        use_container_width=True,
    ):

        st.session_state.filter_reset_version += 1

        st.rerun()

else:

    st.sidebar.caption(
        "No recognized date column found."
    )


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

    parsed_dates = pd.to_datetime(
        display_df[date_column],
        errors="coerce",
    )

    if date_from:

        display_df = display_df[
            parsed_dates
            >= pd.Timestamp(date_from)
        ]

    if date_to:

        end_date = (
            pd.Timestamp(date_to)
            + pd.Timedelta(days=1)
        )

        display_df = display_df[
            parsed_dates
            < end_date
        ]


# ============================================================
# HEADER
# ============================================================

st.title(
    "🗄️ YgnTBPro Database"
)

h1, h2, h3 = st.columns(
    [2, 1, 1]
)

with h1:

    st.caption(
        f"Logged in as: {user_email}"
    )

with h2:

    st.metric(
        "Role",
        user_role.upper(),
    )

with h3:

    st.metric(
        "Pending",
        pending_changes_count(),
    )


# ============================================================
# PERMISSION MESSAGE
# ============================================================

if user_role == "viewer":

    st.info(
        "Viewer mode: you can view data but "
        "cannot modify it."
    )

elif user_role == "editor":

    st.info(
        "Editor mode: you can edit existing records. "
        "Add and Delete are disabled."
    )

elif user_role == "admin":

    st.info(
        "Admin mode: Edit, Add and Delete are enabled."
    )


# ============================================================
# ACTION BUTTONS
# ============================================================

c1, c2, c3, c4 = st.columns(
    4
)


# ------------------------------------------------------------
# ADD ROW
# ------------------------------------------------------------

with c1:

    add_clicked = st.button(
        "➕ Add Row",
        disabled=not can_add,
        use_container_width=True,
    )

    if add_clicked:

        if add_new_row():

            st.rerun()


# ------------------------------------------------------------
# DELETE SELECTED
# ------------------------------------------------------------

with c2:

    delete_clicked = st.button(
        "🗑️ Delete Selected",
        disabled=not can_delete,
        use_container_width=True,
    )

    if delete_clicked:

        count = delete_selected_rows()

        if count:

            st.success(
                f"{count} row(s) marked for deletion."
            )

            st.rerun()

        else:

            st.warning(
                "Please select at least one row."
            )


# ------------------------------------------------------------
# SYNC
# ------------------------------------------------------------

with c3:

    sync_clicked = st.button(
        "💾 Sync Changes",
        disabled=(
            pending_changes_count() == 0
        ),
        type="primary",
        use_container_width=True,
    )

    if sync_clicked:

        sync_changes()

        st.rerun()


# ------------------------------------------------------------
# DISCARD
# ------------------------------------------------------------

with c4:

    discard_clicked = st.button(
        "↩️ Discard Changes",
        disabled=(
            pending_changes_count() == 0
        ),
        use_container_width=True,
    )

    if discard_clicked:

        discard_changes()

        st.rerun()


# ============================================================
# CURRENT PENDING SUMMARY
# ============================================================

if pending_changes_count() > 0:

    st.warning(
        "There are unsynchronized changes. "
        "Changing filters will NOT discard them."
    )


# ============================================================
# AG GRID DATA
# ============================================================

grid_df = display_df.copy()


if "_temp_id" not in grid_df.columns:

    grid_df["_temp_id"] = [
        str(uuid.uuid4())
        for _ in range(
            len(grid_df)
        )
    ]


# ============================================================
# AG GRID OPTIONS
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
# Primary key cannot be edited
# ------------------------------------------------------------

if PRIMARY_KEY in grid_df.columns:

    gb.configure_column(
        PRIMARY_KEY,
        editable=False,
    )


# ------------------------------------------------------------
# Internal ID hidden
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


gb.configure_grid_options(
    stopEditingWhenCellsLoseFocus=True,
    suppressRowClickSelection=False,
)


# ------------------------------------------------------------
# Date columns
# ------------------------------------------------------------

for column in grid_df.columns:

    if column == "_temp_id":
        continue

    if "date" in column.lower():

        gb.configure_column(
            column,
            filter="agDateColumnFilter",
        )


grid_options = gb.build()


# ============================================================
# AG GRID
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

    key=(
        f"ygn_tb_grid_"
        f"{st.session_state.grid_version}"
    ),
)


# ============================================================
# SAVE SELECTION
# ============================================================

selected_rows = grid_response.get(
    "selected_rows"
)

if selected_rows is not None:

    if isinstance(
        selected_rows,
        pd.DataFrame,
    ):

        st.session_state.selected_grid_rows = (
            selected_rows
            .to_dict(
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
# PROCESS GRID DATA
# ============================================================

returned_data = grid_response.get(
    "data"
)

if (
    returned_data is not None
    and can_edit
):

    if isinstance(
        returned_data,
        pd.DataFrame,
    ):

        edited_df = returned_data.copy()

    else:

        edited_df = pd.DataFrame(
            returned_data
        )

    # --------------------------------------------------------
    # Compare grid with database snapshot
    # --------------------------------------------------------

    for _, row in edited_df.iterrows():

        row_data = row.to_dict()

        temp_id = row_data.get(
            "_temp_id"
        )

        # ====================================================
        # NEW PENDING ROW
        # ====================================================

        new_row = pending_insert_by_temp_id(
            temp_id
        )

        if new_row is not None:

            # Update the pending new row.
            for column, value in row_data.items():

                if column == "_temp_id":
                    continue

                new_row[column] = json_safe(
                    value
                )

            continue

        # ====================================================
        # EXISTING DATABASE ROW
        # ====================================================

        primary_value = row_data.get(
            PRIMARY_KEY
        )

        if (
            primary_value is None
            or (
                isinstance(
                    primary_value,
                    float,
                )
                and pd.isna(
                    primary_value
                )
            )
        ):

            continue

        primary_string = str(
            primary_value
        )

        original = existing_record(
            primary_value
        )

        if original is None:
            continue

        row_changes = {}

        for column, current_value in row_data.items():

            if column == "_temp_id":
                continue

            if column == PRIMARY_KEY:
                continue

            if column not in original:
                continue

            original_value = (
                original.get(column)
            )

            current_safe = json_safe(
                current_value
            )

            original_safe = json_safe(
                original_value
            )

            # Both empty.
            if (
                current_safe is None
                and original_safe is None
            ):

                continue

            # One empty, one not empty.
            if (
                current_safe is None
                or original_safe is None
            ):

                row_changes[column] = (
                    current_safe
                )

                continue

            # Normalize strings for comparison.
            if (
                str(current_safe)
                != str(original_safe)
            ):

                row_changes[column] = (
                    current_safe
                )

        # ----------------------------------------------------
        # Preserve/merge pending updates
        # ----------------------------------------------------

        if row_changes:

            current_pending = (
                st.session_state
                .pending_updates
                .get(
                    primary_string,
                    {},
                )
                .copy()
            )

            current_pending.update(
                row_changes
            )

            st.session_state.pending_updates[
                primary_string
            ] = current_pending

        else:

            # Only remove pending update if the entire
            # row has returned to its original state.
            st.session_state.pending_updates.pop(
                primary_string,
                None,
            )


# ============================================================
# PENDING CHANGES DISPLAY
# ============================================================

update_count = len(
    st.session_state.pending_updates
)

insert_count = len(
    st.session_state.pending_inserts
)

delete_count = len(
    st.session_state.pending_deletes
)

if (
    update_count
    or insert_count
    or delete_count
):

    st.divider()

    st.subheader(
        "Pending Changes"
    )

    p1, p2, p3 = st.columns(3)

    with p1:

        st.metric(
            "Updates",
            update_count,
        )

    with p2:

        st.metric(
            "New Rows",
            insert_count,
        )

    with p3:

        st.metric(
            "Deletes",
            delete_count,
        )


# ============================================================
# DATABASE STATUS
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
            "Table": TABLE_NAME,
            "Primary Key": PRIMARY_KEY,
            "Database Rows": len(db_df),
            "Displayed Rows": len(display_df),
            "Pending Updates": update_count,
            "Pending Inserts": insert_count,
            "Pending Deletes": delete_count,
        }
    )