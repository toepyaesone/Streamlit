import os
from datetime import date, datetime, timedelta
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

SUPABASE_URL = st.secrets.get(
    "SUPABASE_URL_ygntbpro",
    os.getenv(
        "SUPABASE_URL_ygntbpro",
        os.getenv("SUPABASE_URL", ""),
    ),
)

SUPABASE_KEY = st.secrets.get(
    "SUPABASE_KEY_ygntbpro",
    os.getenv(
        "SUPABASE_KEY_ygntbpro",
        os.getenv("SUPABASE_KEY", ""),
    ),
)

TABLE_NAME = "ygntbpro"
MAX_ROWS = 1000


if not SUPABASE_URL or not SUPABASE_KEY:
    st.error(
        "Supabase credentials are missing. Configure "
        "`SUPABASE_URL_ygntbpro` and `SUPABASE_KEY_ygntbpro`."
    )
    st.stop()


# ============================================================
# Supabase clients
# ============================================================

@st.cache_resource
def get_base_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


base_supabase = get_base_client()


def get_user_client() -> Client:
    """
    Return Supabase client using the current authenticated
    user's access token so that RLS is respected.
    """

    session = st.session_state.get("session")

    if not session:
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

    if access_token:
        try:
            client.postgrest.auth(access_token)
        except Exception:
            pass

    return client


# ============================================================
# Session state
# ============================================================

DEFAULTS = {
    "session": None,
    "user_role": None,

    # AG Grid version.
    # Changing this rebuilds the grid but DOES NOT clear pending changes.
    "grid_version": 0,

    # Database snapshot.
    "db_df": None,

    # Pending database operations.
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Temporary ID counter for new rows.
    "insert_counter": 0,
}


for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# Authentication
# ============================================================

def login_user(email: str, password: str):

    try:

        response = base_supabase.auth.sign_in_with_password(
            {
                "email": email.strip(),
                "password": password,
            }
        )

        if not response.session:
            return False, (
                "Login failed: no authenticated session "
                "was returned."
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

        if role_result.data:

            st.session_state.user_role = (
                role_result.data[0].get(
                    "role",
                    "viewer",
                )
            )

        else:

            st.session_state.user_role = "viewer"

        return True, "Login successful."

    except Exception as exc:

        return False, str(exc)


def logout_user():

    try:
        base_supabase.auth.sign_out()
    except Exception:
        pass

    for key, value in DEFAULTS.items():
        st.session_state[key] = value

    st.rerun()


# ============================================================
# Login page
# ============================================================

if not st.session_state.session:

    st.title("🔑 YgnTBPro Database Login")

    with st.form("login_form"):

        email = st.text_input("Email")

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
# Permissions
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
# Sidebar
# ============================================================

st.sidebar.title("YgnTBPro System")

st.sidebar.write(
    f"**User Role:** `{user_role}`"
)

if st.sidebar.button(
    "Logout",
    use_container_width=True,
):
    logout_user()


# ============================================================
# Utility functions
# ============================================================

def resolve_column(
    columns,
    *candidates,
):
    """
    Resolve PostgreSQL column names without changing
    the database schema.
    """

    lookup = {
        str(column).lower(): column
        for column in columns
    }

    for candidate in candidates:

        if candidate in columns:
            return candidate

        found = lookup.get(
            str(candidate).lower()
        )

        if found is not None:
            return found

    return None


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
    """
    Convert Pandas / NumPy values to ordinary Python values.

    This prevents errors such as:

        Object of type int64 is not JSON serializable
    """

    if value is None:
        return None

    if is_missing(value):
        return None

    # NumPy scalar:
    # np.int64, np.float64, np.bool_, etc.
    if isinstance(
        value,
        np.generic,
    ):
        try:
            return value.item()
        except Exception:
            pass

    # Pandas Timestamp
    if isinstance(
        value,
        pd.Timestamp,
    ):
        return value.isoformat()

    # datetime/date
    if isinstance(
        value,
        (datetime, date),
    ):
        return value.isoformat()

    # Decimal
    if isinstance(
        value,
        Decimal,
    ):
        return float(value)

    return value


def make_json_safe(value):
    """
    Recursively convert a payload into values accepted
    by the Supabase JSON encoder.
    """

    if isinstance(value, dict):

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

    if isinstance(value, set):

        return [
            make_json_safe(item)
            for item in value
        ]

    return clean_value(value)


def values_equal(
    left,
    right,
):
    """
    Safe comparison for None, NaN, NumPy and Pandas values.
    """

    left = clean_value(left)
    right = clean_value(right)

    if left is None and right is None:
        return True

    if left is None or right is None:
        return False

    try:
        result = left == right

        if isinstance(
            result,
            (bool, np.bool_),
        ):
            return bool(result)

    except Exception:
        pass

    return str(left) == str(right)


def normalize_dataframe(
    df: pd.DataFrame,
):
    """
    Convert DataFrame values into stable Python values
    before displaying / comparing.
    """

    if df is None:
        return None

    result = df.copy()

    for column in result.columns:

        result[column] = result[column].map(
            clean_value
        )

    return result


# ============================================================
# Pending change helpers
# ============================================================

def add_pending_update(
    primary_id,
    changes,
):

    primary_id = str(primary_id)

    current = (
        st.session_state
        .pending_updates
        .get(
            primary_id,
            {},
        )
        .copy()
    )

    for column, value in changes.items():

        if column == "updated_at":
            continue

        current[column] = clean_value(value)

    if current:

        st.session_state.pending_updates[
            primary_id
        ] = current

        st.session_state.pending_deletes.discard(
            primary_id
        )

    else:

        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )


def remove_pending_update_if_empty(
    primary_id,
):

    changes = (
        st.session_state
        .pending_updates
        .get(
            str(primary_id),
            {},
        )
    )

    if not changes:

        st.session_state.pending_updates.pop(
            str(primary_id),
            None,
        )


# ============================================================
# Capture AG Grid changes
# ============================================================

def capture_grid_changes(
    edited_df: pd.DataFrame,
    db_df: pd.DataFrame,
    primary_key: str,
):

    if edited_df is None:
        return

    if db_df is None:
        return

    edited = normalize_dataframe(
        edited_df
    )

    database = normalize_dataframe(
        db_df
    )

    if primary_key not in edited.columns:
        return

    if primary_key not in database.columns:
        return

    # --------------------------------------------------------
    # Database records indexed by PatientID
    # --------------------------------------------------------

    db_records = {}

    for _, row in database.iterrows():

        pid = row.get(primary_key)

        if is_missing(pid):
            continue

        pid = str(pid).strip()

        if not pid:
            continue

        db_records[pid] = row.to_dict()

    # --------------------------------------------------------
    # Existing rows
    # --------------------------------------------------------

    existing_ids = set()

    for _, row in edited.iterrows():

        pid = row.get(primary_key)

        if is_missing(pid):
            continue

        pid = str(pid).strip()

        # Temporary insert rows are handled separately.
        if pid.startswith("__NEW__"):
            continue

        if pid not in db_records:
            continue

        existing_ids.add(pid)

        original = db_records[pid]

        changes = {}

        for column in edited.columns:

            if column in {
                primary_key,
                "updated_at",
            }:
                continue

            if column not in original:
                continue

            old_value = original.get(column)
            new_value = row.get(column)

            if not values_equal(
                old_value,
                new_value,
            ):
                changes[column] = clean_value(
                    new_value
                )

        if changes:

            add_pending_update(
                pid,
                changes,
            )

        else:

            st.session_state.pending_updates.pop(
                pid,
                None,
            )

    # --------------------------------------------------------
    # Do NOT infer deletes from missing grid rows.
    #
    # AG Grid filters can hide rows.
    # Therefore a missing row is NOT a deletion.
    #
    # Deletes are generated only by the explicit Delete
    # action below.
    # --------------------------------------------------------


# ============================================================
# Pending counts
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


# ============================================================
# Load database
# ============================================================

def load_database():

    client = get_user_client()

    response = (
        client
        .table(TABLE_NAME)
        .select("*")
        .limit(MAX_ROWS)
        .execute()
    )

    df = pd.DataFrame(
        response.data or []
    )

    return normalize_dataframe(df)


# ============================================================
# Apply pending updates to display
# ============================================================

def build_display_dataframe(
    db_df: pd.DataFrame,
    primary_key: str,
):

    if db_df is None:
        return pd.DataFrame()

    display_df = db_df.copy()

    # --------------------------------------------------------
    # Apply pending UPDATEs
    # --------------------------------------------------------

    for pid, changes in (
        st.session_state
        .pending_updates
        .items()
    ):

        mask = (
            display_df[
                primary_key
            ].astype(str)
            == str(pid)
        )

        if not mask.any():
            continue

        for column, value in changes.items():

            if column in display_df.columns:

                display_df.loc[
                    mask,
                    column,
                ] = clean_value(value)

    # --------------------------------------------------------
    # Mark pending DELETEs
    # --------------------------------------------------------

    display_df["_pending_delete"] = (
        display_df[
            primary_key
        ]
        .astype(str)
        .isin(
            {
                str(x)
                for x in (
                    st.session_state
                    .pending_deletes
                )
            }
        )
    )

    # --------------------------------------------------------
    # Add pending INSERT rows
    # --------------------------------------------------------

    insert_rows = []

    for row in (
        st.session_state
        .pending_inserts
    ):

        display_row = {
            column: row.get(column)
            for column in display_df.columns
            if column != "_pending_delete"
        }

        display_row[
            "_pending_delete"
        ] = False

        insert_rows.append(
            display_row
        )

    if insert_rows:

        insert_df = pd.DataFrame(
            insert_rows
        )

        for column in display_df.columns:

            if column not in insert_df.columns:
                insert_df[column] = None

        insert_df = insert_df[
            display_df.columns
        ]

        display_df = pd.concat(
            [
                display_df,
                insert_df,
            ],
            ignore_index=True,
        )

    return normalize_dataframe(
        display_df
    )


# ============================================================
# Clear pending changes
# ============================================================

def clear_pending_changes():

    st.session_state.pending_updates = {}

    st.session_state.pending_inserts = []

    st.session_state.pending_deletes = set()

    st.session_state.grid_version += 1


# ============================================================
# Create temporary INSERT row
# ============================================================

def create_new_row(columns):

    st.session_state.insert_counter += 1

    temp_id = (
        "__NEW__"
        + str(
            st.session_state.insert_counter
        )
    )

    row = {
        column: None
        for column in columns
        if column != "updated_at"
    }

    # Internal temporary identifier.
    # This is replaced/removed before INSERT.
    row["_temp_id"] = temp_id

    # PatientID is intentionally blank initially.
    # Admin must enter the real PatientID.
    return row


# ============================================================
# Build AG Grid options
# ============================================================

def build_grid_options(
    df,
    primary_key,
    date_column,
    editable,
):

    builder = GridOptionsBuilder.from_dataframe(
        df
    )

    # --------------------------------------------------------
    # General grid settings
    # --------------------------------------------------------

    builder.configure_default_column(
        editable=editable,
        sortable=True,
        filter=True,
        resizable=True,
        floatingFilter=True,
        minWidth=110,
    )

    builder.configure_grid_options(
        rowSelection="multiple",
        suppressRowClickSelection=True,
        animateRows=False,
        pagination=False,
        enableRangeSelection=True,
        copyHeadersToClipboard=True,
    )

    # --------------------------------------------------------
    # Primary key
    # --------------------------------------------------------

    if primary_key in df.columns:

        builder.configure_column(
            primary_key,
            editable=False,
            pinned="left",
            filter="agTextColumnFilter",
            minWidth=180,
        )

    # --------------------------------------------------------
    # Internal status columns
    # --------------------------------------------------------

    if "_pending_delete" in df.columns:

        builder.configure_column(
            "_pending_delete",
            headerName="Pending Delete",
            editable=False,
            filter="agSetColumnFilter",
            width=130,
        )

    if "_temp_id" in df.columns:

        builder.configure_column(
            "_temp_id",
            hide=True,
        )

    # --------------------------------------------------------
    # Date filter
    # --------------------------------------------------------

    if date_column and date_column in df.columns:

        date_comparator = JsCode(
            """
            function(filterLocalDateAtMidnight, cellValue) {

                if (cellValue == null || cellValue === '') {
                    return -1;
                }

                var cellDate = new Date(cellValue);

                if (isNaN(cellDate.getTime())) {
                    return -1;
                }

                cellDate.setHours(0, 0, 0, 0);

                if (cellDate < filterLocalDateAtMidnight) {
                    return -1;
                }

                if (cellDate > filterLocalDateAtMidnight) {
                    return 1;
                }

                return 0;
            }
            """
        )

        builder.configure_column(
            date_column,
            filter="agDateColumnFilter",
            filterParams={
                "comparator": date_comparator
            },
        )

    # --------------------------------------------------------
    # Row appearance
    # --------------------------------------------------------

    row_class_rules = JsCode(
        """
        {
            'pending-delete-row':
                function(params) {
                    return params.data &&
                           params.data._pending_delete === true;
                }
        }
        """
    )

    builder.configure_grid_options(
        rowClassRules=row_class_rules,
    )

    return builder.build()


# ============================================================
# Explorer
# ============================================================

explorer_tab, editor_tab = st.tabs(
    [
        "💾 Explorer & Search",
        "✏️ Data Editor & Sync",
    ]
)


with explorer_tab:

    st.title(
        "💾 Database Explorer"
    )

    st.caption(
        "Inspect and export records from Supabase."
    )

    with st.spinner(
        "Loading data..."
    ):

        try:

            explorer_df = load_database()

        except Exception as exc:

            st.error(
                f"Error loading data: {exc}"
            )

            explorer_df = pd.DataFrame()

    if explorer_df.empty:

        st.warning(
            "No records returned."
        )

    else:

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Records",
            f"{len(explorer_df):,}",
        )

        c2.metric(
            "Columns",
            f"{len(explorer_df.columns):,}",
        )

        c3.metric(
            "Memory",
            f"{explorer_df.memory_usage(deep=True).sum() / 1024 / 1024:.2f} MB",
        )

        st.divider()

        search = st.text_input(
            "🔍 Global Search",
            placeholder="Search all columns...",
        )

        display_df = explorer_df.copy()

        if search:

            mask = (
                display_df
                .astype(str)
                .apply(
                    lambda row:
                    row.str.contains(
                        search,
                        case=False,
                        na=False,
                        regex=False,
                    ).any(),
                    axis=1,
                )
            )

            display_df = display_df[
                mask
            ]

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "📥 Download CSV",
            data=display_df.to_csv(
                index=False
            ).encode("utf-8"),
            file_name=(
                f"{TABLE_NAME}_export.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )


# ============================================================
# AG GRID EDITOR
# ============================================================

with editor_tab:

    st.title(
        "🩺 YgnTBPro AG Grid Editor"
    )

    st.caption(
        "Edit records locally. Changes remain pending until Sync."
    )

    # --------------------------------------------------------
    # Detect schema
    # --------------------------------------------------------

    try:

        schema_response = (
            get_user_client()
            .table(TABLE_NAME)
            .select("*")
            .limit(1)
            .execute()
        )

        schema_df = pd.DataFrame(
            schema_response.data or []
        )

    except Exception as exc:

        st.error(
            f"Unable to inspect schema: {exc}"
        )

        st.stop()

    if schema_df.empty:

        st.warning(
            "The table is empty or could not be read."
        )

        st.stop()

    columns = list(
        schema_df.columns
    )

    # --------------------------------------------------------
    # Resolve important columns
    # --------------------------------------------------------

    primary_key = resolve_column(
        columns,
        "PatientID",
        "patientid",
        "patient_id",
    )

    date_column = resolve_column(
        columns,
        "Date",
        "date",
    )

    if not primary_key:

        st.error(
            "PatientID column was not found."
        )

        st.stop()

    # --------------------------------------------------------
    # Load database snapshot only when necessary
    #
    # IMPORTANT:
    # We do NOT reload the snapshot merely because the
    # AG Grid filter changes.
    # --------------------------------------------------------

    if (
        st.session_state.db_df is None
        or pending_changes_count() == 0
    ):

        with st.spinner(
            "Loading database records..."
        ):

            try:

                st.session_state.db_df = (
                    load_database()
                )

            except Exception as exc:

                st.error(
                    f"Error loading data: {exc}"
                )

                st.stop()

    db_df = st.session_state.db_df

    # --------------------------------------------------------
    # Header/status
    # --------------------------------------------------------

    pending_count = (
        pending_changes_count()
    )

    if pending_count:

        st.warning(
            f"📝 {pending_count} pending change(s) "
            "not yet synchronized."
        )

    else:

        st.info(
            "No pending changes."
        )

    # --------------------------------------------------------
    # Controls
    # --------------------------------------------------------

    control1, control2, control3 = st.columns(
        [1, 1, 2]
    )

    with control1:

        reset_filters = st.button(
            "🔄 Reset Filters",
            use_container_width=True,
        )

    with control2:

        if can_add:

            add_row = st.button(
                "➕ New Row",
                use_container_width=True,
            )

        else:

            add_row = False

    with control3:

        st.caption(
            "Use the filter boxes in each AG Grid column. "
            "Filtering does not discard pending changes."
        )

    # --------------------------------------------------------
    # Reset AG Grid filters
    #
    # This only rebuilds the grid.
    # It does NOT clear pending changes.
    # --------------------------------------------------------

    if reset_filters:

        st.session_state.grid_version += 1

        st.rerun()

    # --------------------------------------------------------
    # Add new row
    # --------------------------------------------------------

    if add_row:

        new_row = create_new_row(
            columns
        )

        st.session_state.pending_inserts.append(
            new_row
        )

        st.session_state.grid_version += 1

        st.rerun()

    # --------------------------------------------------------
    # Build grid dataframe
    # --------------------------------------------------------

    display_df = build_display_dataframe(
        db_df,
        primary_key,
    )

    # --------------------------------------------------------
    # Remove internal fields from visible grid
    # --------------------------------------------------------

    visible_columns = [
        column
        for column in display_df.columns
        if column not in {
            "_temp_id",
        }
    ]

    display_df = display_df[
        visible_columns
    ]

    # --------------------------------------------------------
    # AG Grid
    # --------------------------------------------------------

    editable = can_edit

    grid_options = build_grid_options(
        display_df,
        primary_key,
        date_column,
        editable,
    )

    grid_key = (
        "ygntbpro_aggrid_"
        + str(
            st.session_state.grid_version
        )
    )

    grid_response = AgGrid(
        display_df,
        gridOptions=grid_options,
        height=650,
        width="100%",
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        fit_columns_on_grid_load=False,
        allow_unsafe_jscode=True,
        key=grid_key,
        theme="streamlit",
    )

    edited_df = pd.DataFrame(
        grid_response.get(
            "data",
            display_df.to_dict(
                "records"
            ),
        )
    )

    # --------------------------------------------------------
    # Capture UPDATE changes
    # --------------------------------------------------------

    if can_edit:

        capture_grid_changes(
            edited_df,
            db_df,
            primary_key,
        )

    elif not edited_df.equals(
        display_df
    ):

        st.warning(
            "Your role is read-only. "
            "Changes cannot be synchronized."
        )

    # ========================================================
    # Pending INSERT validation
    # ========================================================

    invalid_inserts = []

    for index, row in enumerate(
        st.session_state.pending_inserts
    ):

        patient_id = row.get(
            primary_key
        )

        if (
            patient_id is None
            or str(patient_id).strip() == ""
        ):

            invalid_inserts.append(
                index
            )

    # ========================================================
    # Pending changes preview
    # ========================================================

    pending_rows = []

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    for (
        pid,
        changes,
    ) in (
        st.session_state
        .pending_updates
        .items()
    ):

        row = {
            "Action": "UPDATE",
            primary_key: pid,
        }

        row.update(
            changes
        )

        pending_rows.append(
            row
        )

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    for index, row in enumerate(
        st.session_state.pending_inserts
    ):

        display_row = {
            "Action": "INSERT"
        }

        display_row.update(
            {
                key: value
                for key, value in row.items()
                if key != "_temp_id"
            }
        )

        if index in invalid_inserts:

            display_row[
                "_Validation"
            ] = (
                "PatientID is required"
            )

        pending_rows.append(
            display_row
        )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    for pid in sorted(
        st.session_state.pending_deletes,
        key=str,
    ):

        pending_rows.append(
            {
                "Action": "DELETE",
                primary_key: pid,
            }
        )

    # ========================================================
    # Pending change panel
    # ========================================================

    if pending_rows:

        st.divider()

        st.subheader(
            f"📝 Pending Changes "
            f"({len(pending_rows)})"
        )

        pending_df = pd.DataFrame(
            pending_rows
        )

        st.dataframe(
            pending_df,
            use_container_width=True,
            hide_index=True,
        )

        sync_col, discard_col = st.columns(
            2
        )

        with sync_col:

            sync_clicked = st.button(
                "💾 Sync Changes",
                type="primary",
                disabled=not can_edit,
                use_container_width=True,
            )

        with discard_col:

            discard_clicked = st.button(
                "↩️ Discard Changes",
                use_container_width=True,
            )

        # ====================================================
        # DISCARD
        # ====================================================

        if discard_clicked:

            clear_pending_changes()

            st.success(
                "Pending changes discarded."
            )

            st.rerun()

        # ====================================================
        # SYNC
        # ====================================================

        if sync_clicked:

            sync_client = (
                get_user_client()
            )

            success_count = 0
            errors = []

            successful_updates = []
            successful_inserts = []
            successful_deletes = []

            # =================================================
            # UPDATE
            # =================================================

            for (
                pid,
                update_data,
            ) in list(
                st.session_state
                .pending_updates
                .items()
            ):

                if not update_data:
                    continue

                try:

                    # -----------------------------------------
                    # CRITICAL:
                    # Convert np.int64, np.float64, pd.NA,
                    # Timestamp, etc. before Supabase.
                    # -----------------------------------------

                    safe_update = (
                        make_json_safe(
                            update_data
                        )
                    )

                    response = (
                        sync_client
                        .table(TABLE_NAME)
                        .update(
                            safe_update
                        )
                        .eq(
                            primary_key,
                            str(pid),
                        )
                        .execute()
                    )

                    if response.data:

                        success_count += 1

                        successful_updates.append(
                            pid
                        )

                    else:

                        errors.append(
                            f"UPDATE affected no row: {pid}"
                        )

                except Exception as exc:

                    errors.append(
                        f"UPDATE {pid}: {exc}"
                    )

            # =================================================
            # INSERT
            # =================================================

            if st.session_state.pending_inserts:

                if not can_add:

                    errors.append(
                        "INSERT requires admin permissions."
                    )

                else:

                    for index, row_data in enumerate(
                        list(
                            st.session_state
                            .pending_inserts
                        )
                    ):

                        # -------------------------------------
                        # Validate PatientID
                        # -------------------------------------

                        patient_id = row_data.get(
                            primary_key
                        )

                        if (
                            patient_id is None
                            or str(
                                patient_id
                            ).strip() == ""
                        ):

                            errors.append(
                                f"INSERT row {index}: "
                                f"{primary_key} is required."
                            )

                            continue

                        # -------------------------------------
                        # Remove internal temporary fields
                        # -------------------------------------

                        insert_data = {
                            key: value
                            for key, value
                            in row_data.items()
                            if key not in {
                                "_temp_id"
                            }
                        }

                        # -------------------------------------
                        # JSON-safe conversion
                        # -------------------------------------

                        safe_insert = (
                            make_json_safe(
                                insert_data
                            )
                        )

                        try:

                            response = (
                                sync_client
                                .table(TABLE_NAME)
                                .insert(
                                    safe_insert
                                )
                                .execute()
                            )

                            if response.data:

                                success_count += 1

                                successful_inserts.append(
                                    index
                                )

                            else:

                                errors.append(
                                    f"INSERT returned no row: "
                                    f"{index}"
                                )

                        except Exception as exc:

                            errors.append(
                                f"INSERT row {index}: "
                                f"{exc}"
                            )

            # =================================================
            # DELETE
            # =================================================

            if st.session_state.pending_deletes:

                if not can_delete:

                    errors.append(
                        "DELETE requires admin permissions."
                    )

                else:

                    for pid in list(
                        st.session_state
                        .pending_deletes
                    ):

                        try:

                            response = (
                                sync_client
                                .table(TABLE_NAME)
                                .delete()
                                .eq(
                                    primary_key,
                                    str(pid),
                                )
                                .execute()
                            )

                            if response.data:

                                success_count += 1

                                successful_deletes.append(
                                    pid
                                )

                            else:

                                errors.append(
                                    f"DELETE affected no row: "
                                    f"{pid}"
                                )

                        except Exception as exc:

                            errors.append(
                                f"DELETE {pid}: {exc}"
                            )

            # =================================================
            # Remove only successful operations
            # =================================================

            for pid in successful_updates:

                st.session_state.pending_updates.pop(
                    pid,
                    None,
                )

            for index in reversed(
                successful_inserts
            ):

                del (
                    st.session_state
                    .pending_inserts[index]
                )

            for pid in successful_deletes:

                st.session_state.pending_deletes.discard(
                    pid
                )

            # =================================================
            # Results
            # =================================================

            if success_count:

                st.success(
                    f"✅ {success_count} change(s) "
                    "synchronized successfully."
                )

            if errors:

                st.error(
                    f"❌ {len(errors)} operation(s) "
                    "failed."
                )

                for error in errors:

                    st.warning(error)

            # =================================================
            # Refresh DB snapshot only after successful sync
            # =================================================

            if success_count:

                try:

                    st.session_state.db_df = (
                        load_database()
                    )

                except Exception as exc:

                    st.warning(
                        "Changes were synchronized, "
                        "but the refreshed database snapshot "
                        f"could not be loaded: {exc}"
                    )

                st.session_state.grid_version += 1

                st.rerun()

    else:

        st.caption(
            "No pending changes."
        )


# ============================================================
# Footer
# ============================================================

st.divider()

st.caption(
    "YgnTBPro • Supabase + Streamlit + AG Grid"
)