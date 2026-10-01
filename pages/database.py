# ============================================================
# database.py
# YgnTBPro - Supabase Database Editor
# ============================================================

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
EDITOR_PAGE_SIZE_DEFAULT = 100
EXPLORER_PAGE_SIZE_DEFAULT = 100

PRIMARY_KEY_CANDIDATES = [
    "PatientID",
    "patientid",
    "patient_id",
]

DATE_COLUMN_CANDIDATES = [
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
        "VisitNo",
    ],
    "sr_no": [
        "srno",
        "Sr_No",
        "sr_no",
        "SrNo",
    ],
    "ward_village": [
        "WardVillage",
        "Ward_Village",
        "ward_village",
        "Ward/Village",
    ],
}


# Columns suitable for text search in Explorer.
# Numeric/date PostgreSQL columns are deliberately excluded.
EXPLORER_TEXT_CANDIDATES = [
    "PatientID",
    "patientid",
    "patient_id",
    "TSP",
    "Tsp",
    "tsp",
    "Approach",
    "approach",
    "Case",
    "case",
    "WardVillage",
    "Ward_Village",
    "ward_village",
    "Reasonforexamination",
    "Treatmentreferral",
    "CXRresult",
    "CXRResult",
    "GeneXpertresult",
    "GeneXpertResult",
    "MonthDiagnosis11",
    "Gender",
    "Name",
    "name",
    "Address",
    "address",
    "Remark",
    "remark",
]


# ============================================================
# SESSION STATE
# ============================================================

DEFAULTS = {
    # Authentication
    "session": None,
    "user_role": None,

    # Editor
    "editor_loaded": False,
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

    # Raw DB snapshot for currently displayed editor page
    "editor_db_df": None,
    "editor_source_key": None,

    # Pending changes
    "pending_updates": {},
    "pending_inserts": [],
    "pending_deletes": set(),

    # Grid
    "grid_version": 0,

    # Explorer
    "explorer_all_df": None,
    "explorer_loaded": False,
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
            st.session_state[key] = set()
        else:
            st.session_state[key] = value


# ============================================================
# BASIC HELPERS
# ============================================================

def resolve_column(columns, *candidates):
    """Return the first matching column from candidates."""
    columns = list(columns)

    for candidate in candidates:
        if candidate in columns:
            return candidate

    return None


def values_equal(left, right):
    """Safe comparison for pandas/numpy/NaN values."""

    if left is None and right is None:
        return True

    try:
        if pd.isna(left) and pd.isna(right):
            return True
    except Exception:
        pass

    try:
        result = left == right

        if isinstance(result, (bool, np.bool_)):
            return bool(result)

    except Exception:
        pass

    return str(left) == str(right)


def clean_value(value):
    """Convert values to JSON/AG Grid safe Python values."""

    if value is None:
        return None

    # Pandas / NumPy missing values
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    # NumPy scalar
    if isinstance(value, np.generic):
        value = value.item()

    # Decimal
    if isinstance(value, Decimal):
        return float(value)

    # Pandas timestamp
    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    # datetime
    if isinstance(value, datetime):
        return value.isoformat()

    # date
    if isinstance(value, date):
        return value.isoformat()

    # Objects such as URL / UUID / other custom objects
    # should be converted to ordinary strings.
    if not isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
            list,
            tuple,
            dict,
        ),
    ):
        return str(value)

    return value

def make_dataframe_aggrid_safe(df):
    """
    Convert every DataFrame value to a normal Python value
    that Streamlit/AG Grid can serialize safely.
    """

    if df is None:
        return pd.DataFrame()

    result = df.copy()

    for column in result.columns:

        result[column] = result[column].map(
            clean_value
        )

    return result

# def make_json_safe(data):
#     """Recursively make data safe for Supabase JSON payloads."""

#     if isinstance(data, dict):
#         return {
#             str(k): make_json_safe(v)
#             for k, v in data.items()
#         }

#     if isinstance(data, (list, tuple)):
#         return [
#             make_json_safe(v)
#             for v in data
#         ]

#     if isinstance(data, set):
#         return [
#             make_json_safe(v)
#             for v in data
#         ]

#     return clean_value(data)

def make_json_safe(data):
    """Recursively convert values to JSON-safe Python values."""

    if isinstance(data, dict):

        return {
            str(k): make_json_safe(v)
            for k, v in data.items()
        }

    if isinstance(data, (list, tuple)):

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

def normalize_key(value):
    """Normalize primary-key values for session-state dictionaries."""

    value = clean_value(value)

    if value is None:
        return None

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return value


# ============================================================
# SUPABASE CLIENT
# ============================================================

@st.cache_resource
def get_base_client() -> Client:
    url = (
        st.secrets.get(
            "SUPABASE_URL_ygntbpro",
            os.getenv("SUPABASE_URL_ygntbpro"),
        )
        or st.secrets.get(
            "SUPABASE_URL",
            os.getenv("SUPABASE_URL"),
        )
    )

    key = (
        st.secrets.get(
            "SUPABASE_KEY_ygntbpro",
            os.getenv("SUPABASE_KEY_ygntbpro"),
        )
        or st.secrets.get(
            "SUPABASE_KEY",
            os.getenv("SUPABASE_KEY"),
        )
    )

    if not url or not key:
        raise RuntimeError(
            "Supabase URL/key not found. "
            "Please configure SUPABASE_URL_ygntbpro and "
            "SUPABASE_KEY_ygntbpro in Streamlit secrets."
        )

    return create_client(url, key)


base_supabase = get_base_client()


def get_user_client() -> Client:
    """
    Create a Supabase client authenticated with the
    current user's access token.

    This client is used for database operations so that
    Supabase RLS policies are respected.
    """

    session = st.session_state.get("session")

    if not session:
        raise RuntimeError("No authenticated Supabase session.")

    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)

    if not access_token:
        raise RuntimeError("Authenticated session has no access token.")

    client = create_client(
        base_supabase.supabase_url,
        base_supabase.supabase_key,
    )

    try:
        if refresh_token:
            client.auth.set_session(
                access_token,
                refresh_token,
            )
        else:
            try:
                client.postgrest.auth(access_token)
            except Exception:
                pass

    except Exception:
        try:
            client.postgrest.auth(access_token)
        except Exception:
            pass

    return client


# ============================================================
# AUTHENTICATION
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
            return (
                False,
                "Login failed: no authenticated session was returned.",
            )

        st.session_state.session = response.session

        user_client = get_user_client()

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
            role = (
                role_result.data[0]
                .get("role", "viewer")
            )
        else:
            role = "viewer"

        role = str(role).strip().lower()

        if role not in {"viewer", "editor", "admin"}:
            role = "viewer"

        st.session_state.user_role = role

        return True, "Login successful."

    except Exception as exc:
        return False, str(exc)


def logout_user():
    try:
        base_supabase.auth.sign_out()
    except Exception:
        pass

    for key, value in DEFAULTS.items():
        if isinstance(value, dict):
            st.session_state[key] = value.copy()
        elif isinstance(value, set):
            st.session_state[key] = set()
        else:
            st.session_state[key] = value

    st.rerun()


# ============================================================
# LOGIN SCREEN
# ============================================================

if not st.session_state.session:

    st.title("🗄️ YgnTBPro Database")

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
            st.error("Please enter email and password.")

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

can_edit = user_role in {
    "editor",
    "admin",
}

can_add = user_role == "admin"
can_delete = user_role == "admin"


# ============================================================
# TABLE SCHEMA
# ============================================================

@st.cache_data(ttl=300)
def get_table_columns_cached():

    response = (
        base_supabase
        .table(TABLE_NAME)
        .select("*")
        .limit(1)
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            f"No schema/sample row could be read from {TABLE_NAME}."
        )

    return list(response.data[0].keys())


try:
    TABLE_COLUMNS = get_table_columns_cached()
except Exception as exc:
    st.error(f"Unable to read table schema: {exc}")
    st.stop()


PRIMARY_KEY = resolve_column(
    TABLE_COLUMNS,
    *PRIMARY_KEY_CANDIDATES,
)

DATE_COLUMN = resolve_column(
    TABLE_COLUMNS,
    *DATE_COLUMN_CANDIDATES,
)

if not PRIMARY_KEY:
    st.error(
        "Primary key column was not found. "
        f"Checked: {PRIMARY_KEY_CANDIDATES}"
    )
    st.stop()


# Resolve filter columns
FILTER_COLUMNS = {}

for filter_name, candidates in FILTER_CANDIDATES.items():

    FILTER_COLUMNS[filter_name] = resolve_column(
        TABLE_COLUMNS,
        *candidates,
    )


# ============================================================
# NUMERIC FILTER DETECTION
# ============================================================

NUMERIC_FILTER_NAMES = {
    "team",
    "visit_no",
    "sr_no",
}


def convert_filter_values(
    filter_name,
    values,
):
    """
    Convert numeric filter values to numeric Python values.

    This prevents PostgreSQL errors caused by passing strings
    to numeric columns.
    """

    if filter_name not in NUMERIC_FILTER_NAMES:
        return values

    converted = []

    for value in values:

        if value is None:
            continue

        text_value = str(value).strip()

        if not text_value:
            continue

        try:
            number = float(text_value)

            if number.is_integer():
                number = int(number)

            converted.append(number)

        except Exception:
            continue

    return converted


# ============================================================
# BATCH DATA LOADING
# ============================================================

def fetch_all_rows(
    table_name,
    client,
    select_columns="*",
    order_column=None,
    descending=False,
):
    """
    Load ALL RLS-visible rows in batches.

    BATCH_SIZE is only the request size.
    It is NOT a maximum-record limit.
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
    """
    Paginate an already-built query.

    query_builder must be a function receiving
    start and end.
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
# PENDING CHANGE MANAGEMENT
# ============================================================

def add_pending_update(
    primary_id,
    changes,
):
    primary_id = normalize_key(primary_id)

    if primary_id is None:
        return

    cleaned = {
        column: clean_value(value)
        for column, value in changes.items()
    }

    cleaned = {
        column: value
        for column, value in cleaned.items()
        if column != PRIMARY_KEY
    }

    if cleaned:
        existing = (
            st.session_state
            .pending_updates
            .get(primary_id, {})
            .copy()
        )

        existing.update(cleaned)

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
    primary_key,
):
    """
    Compare AG Grid values against the RAW database
    snapshot, while preserving previous pending changes.
    """

    if source_df is None or edited_df is None:
        return

    if source_df.empty or edited_df.empty:
        return

    if primary_key not in source_df.columns:
        return

    if primary_key not in edited_df.columns:
        return

    source_lookup = {}

    for _, row in source_df.iterrows():

        key = normalize_key(
            row.get(primary_key)
        )

        if key is not None:
            source_lookup[key] = row

    for _, edited_row in edited_df.iterrows():

        primary_id = normalize_key(
            edited_row.get(primary_key)
        )

        if primary_id is None:
            continue

        # ----------------------------------------------------
        # New temporary row
        # ----------------------------------------------------
        if primary_id not in source_lookup:
            continue

        original_row = source_lookup[primary_id]

        existing = (
            st.session_state
            .pending_updates
            .get(primary_id, {})
            .copy()
        )

        for column in edited_df.columns:

            if column == primary_key:
                continue

            if column not in source_df.columns:
                continue

            original_value = original_row.get(
                column
            )

            new_value = edited_row.get(
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
                # If user changes a value back to
                # the database value, remove that
                # field from pending UPDATE.
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


def update_pending_insert(
    temp_id,
    record,
):
    """
    Update an existing pending INSERT record.
    """

    for index, existing in enumerate(
        st.session_state.pending_inserts
    ):

        if existing.get("_temp_id") == temp_id:

            new_record = {
                column: clean_value(value)
                for column, value in record.items()
            }

            new_record["_temp_id"] = temp_id

            st.session_state.pending_inserts[
                index
            ] = new_record

            return


def apply_pending_changes_to_df(
    df,
):
    """
    Create display dataframe by applying pending
    UPDATE / INSERT / DELETE to a RAW database dataframe.

    Does not modify the raw dataframe.
    """

    if df is None:
        return pd.DataFrame()

    result = df.copy()

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    if PRIMARY_KEY in result.columns:

        for primary_id, changes in (
            st.session_state
            .pending_updates
            .items()
        ):

            mask = (
                result[PRIMARY_KEY]
                .apply(
                    lambda value:
                    normalize_key(value)
                    == normalize_key(primary_id)
                )
            )

            if mask.any():

                for column, value in changes.items():

                    if column in result.columns:
                        result.loc[
                            mask,
                            column,
                        ] = value

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    if (
        PRIMARY_KEY in result.columns
        and st.session_state.pending_deletes
    ):

        delete_ids = {
            normalize_key(value)
            for value in
            st.session_state.pending_deletes
        }

        mask = result[PRIMARY_KEY].apply(
            lambda value:
            normalize_key(value) in delete_ids
        )

        result = result.loc[
            ~mask
        ].copy()

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    inserts = (
        st.session_state.pending_inserts
    )

    if inserts:

        insert_rows = []

        for record in inserts:

            row = {
                column: record.get(column)
                for column in TABLE_COLUMNS
            }

            row["_temp_id"] = record.get(
                "_temp_id"
            )

            insert_rows.append(row)

        insert_df = pd.DataFrame(
            insert_rows
        )

        for column in TABLE_COLUMNS:

            if column not in insert_df.columns:
                insert_df[column] = None

        insert_df = insert_df[
            TABLE_COLUMNS + ["_temp_id"]
        ]

        if "_temp_id" not in result.columns:
            result["_temp_id"] = None

        result = pd.concat(
            [
                result,
                insert_df,
            ],
            ignore_index=True,
        )

    return result


def pending_changes_count():

    return (
        len(st.session_state.pending_inserts)
        + len(st.session_state.pending_updates)
        + len(st.session_state.pending_deletes)
    )


def clear_pending_changes():

    st.session_state.pending_updates = {}
    st.session_state.pending_inserts = []
    st.session_state.pending_deletes = set()

    st.session_state.editor_db_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


# ============================================================
# PENDING CHANGES - GROUPED UI
# ============================================================

def show_pending_changes():

    inserts = st.session_state.pending_inserts
    updates = st.session_state.pending_updates
    deletes = st.session_state.pending_deletes

    total = (
        len(inserts)
        + len(updates)
        + len(deletes)
    )

    st.subheader("Pending Changes")

    if total == 0:

        st.caption("No pending changes.")

        return

    # ========================================================
    # ADD
    # ========================================================

    with st.expander(
        f"➕ Add New Records ({len(inserts)})",
        expanded=True,
    ):

        if inserts:

            for index, record in enumerate(
                inserts,
                start=1,
            ):

                temp_id = record.get(
                    "_temp_id",
                    f"new-{index}",
                )

                # Show patient ID when available
                patient_value = record.get(
                    PRIMARY_KEY
                )

                if (
                    patient_value is None
                    or str(patient_value).strip() == ""
                ):
                    patient_value = "New record"

                st.write(
                    f"**{index}.** "
                    f"`{patient_value}`"
                )

                visible_fields = []

                for column in TABLE_COLUMNS:

                    if column == PRIMARY_KEY:
                        continue

                    value = record.get(column)

                    if value is not None and str(value) != "":
                        visible_fields.append(
                            f"{column}={value}"
                        )

                if visible_fields:

                    st.caption(
                        " | ".join(
                            visible_fields[:6]
                        )
                    )

        else:

            st.caption("No new records.")

    # ========================================================
    # UPDATE
    # ========================================================

    with st.expander(
        f"✏️ Updated Records ({len(updates)})",
        expanded=True,
    ):

        if updates:

            for primary_id, changes in (
                updates.items()
            ):

                st.markdown(
                    f"**Patient ID:** `{primary_id}`"
                )

                for column, value in changes.items():

                    st.write(
                        f"↳ `{column}` → `{value}`"
                    )

        else:

            st.caption("No updated records.")

    # ========================================================
    # DELETE
    # ========================================================

    with st.expander(
        f"🗑️ Deleted Records ({len(deletes)})",
        expanded=True,
    ):

        if deletes:

            for primary_id in sorted(
                deletes,
                key=lambda x: str(x),
            ):

                st.write(
                    f"• `{primary_id}`"
                )

        else:

            st.caption("No deleted records.")

    st.divider()

    st.caption(
        f"Total pending changes: **{total}**  "
        f"(Add: {len(inserts)}, "
        f"Update: {len(updates)}, "
        f"Delete: {len(deletes)})"
    )

    col1, col2 = st.columns(2)

    with col1:

        if st.button(
            "🔄 Sync Changes",
            type="primary",
            use_container_width=True,
            key="sync_pending_changes_button",
        ):

            success, message = (
                sync_pending_changes()
            )

            if success:

                st.success(message)
                st.rerun()

            else:

                st.error(message)

    with col2:

        if st.button(
            "✖ Discard Changes",
            use_container_width=True,
            key="discard_pending_changes_button",
        ):

            clear_pending_changes()

            st.success(
                "All pending changes were discarded."
            )

            st.rerun()


# ============================================================
# SYNC PENDING CHANGES
# ============================================================

def sync_pending_changes():

    client = get_user_client()

    errors = []

    update_count = 0
    insert_count = 0
    delete_count = 0

    # ========================================================
    # 1. UPDATE
    # ========================================================

    if can_edit:

        for primary_id, changes in list(
            st.session_state.pending_updates.items()
        ):

            if (
                primary_id
                in st.session_state.pending_deletes
            ):
                continue

            payload = make_json_safe(
                changes
            )

            if not payload:
                continue

            try:

                response = (
                    client
                    .table(TABLE_NAME)
                    .update(payload)
                    .eq(
                        PRIMARY_KEY,
                        primary_id,
                    )
                    .execute()
                )

                update_count += 1

            except Exception as exc:

                errors.append(
                    f"UPDATE {primary_id}: {exc}"
                )

    # ========================================================
    # 2. INSERT
    # ========================================================

    if can_add:

        successful_insert_ids = []

        for record in list(
            st.session_state.pending_inserts
        ):

            temp_id = record.get(
                "_temp_id"
            )

            payload = {
                column: record.get(column)
                for column in TABLE_COLUMNS
                if column != "_temp_id"
            }

            # Remove empty generated values
            # but retain legitimate None values.
            payload = make_json_safe(
                payload
            )

            try:

                response = (
                    client
                    .table(TABLE_NAME)
                    .insert(payload)
                    .execute()
                )

                insert_count += 1

                successful_insert_ids.append(
                    temp_id
                )

            except Exception as exc:

                errors.append(
                    f"INSERT {temp_id}: {exc}"
                )

        if successful_insert_ids:

            st.session_state.pending_inserts = [
                record
                for record in
                st.session_state.pending_inserts
                if record.get("_temp_id")
                not in successful_insert_ids
            ]

    # ========================================================
    # 3. DELETE
    # ========================================================

    if can_delete:

        successful_delete_ids = set()

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

                delete_count += 1

                successful_delete_ids.add(
                    primary_id
                )

            except Exception as exc:

                errors.append(
                    f"DELETE {primary_id}: {exc}"
                )

        for primary_id in successful_delete_ids:

            st.session_state.pending_deletes.discard(
                primary_id
            )

            # Remove stale UPDATE for deleted record.
            st.session_state.pending_updates.pop(
                primary_id,
                None,
            )

    # ========================================================
    # RESULT
    # ========================================================

    if errors:

        return (
            False,
            "Some changes could not be synchronized:\n"
            + "\n".join(errors),
        )

    # All successfully synced updates can be cleared.
    if can_edit:

        st.session_state.pending_updates = {}

    st.session_state.editor_db_df = None
    st.session_state.editor_source_key = None
    st.session_state.grid_version += 1

    return (
        True,
        f"Sync completed. "
        f"Updated: {update_count}, "
        f"Added: {insert_count}, "
        f"Deleted: {delete_count}.",
    )


# ============================================================
# FILTER OPTION LOADING
# ============================================================

def load_editor_filter_options():

    client = get_user_client()

    columns = []

    for filter_name, column in FILTER_COLUMNS.items():

        if column and column not in columns:
            columns.append(column)

    if not columns:
        return {}

    rows = []
    start = 0

    while True:

        end = start + BATCH_SIZE - 1

        response = (
            client
            .table(TABLE_NAME)
            .select(",".join(columns))
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
            name: []
            for name in FILTER_COLUMNS
        }

    df = pd.DataFrame(rows)

    options = {}

    for filter_name, column in (
        FILTER_COLUMNS.items()
    ):

        if not column or column not in df.columns:

            options[filter_name] = []

            continue

        values = []

        for value in df[column].dropna():

            value = clean_value(value)

            if value is None:
                continue

            values.append(value)

        # Deduplicate while retaining usable values.
        unique_values = list(
            dict.fromkeys(
                str(value)
                for value in values
            )
        )

        # Numeric fields sorted numerically.
        if filter_name in NUMERIC_FILTER_NAMES:

            def numeric_sort(value):
                try:
                    return float(value)
                except Exception:
                    return float("inf")

            unique_values.sort(
                key=numeric_sort
            )

        else:

            unique_values.sort(
                key=lambda value:
                str(value).lower()
            )

        options[filter_name] = unique_values

    return options


# ============================================================
# EDITOR FILTER CALLBACKS
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

    # Reset widget keys before the widgets are created
    # on the next Streamlit run.
    for key in [
        "filter_patient_id",
        "filter_team",
        "filter_tsp",
        "filter_approach",
        "filter_case",
        "filter_visit_no",
        "filter_sr_no",
        "filter_ward_village",
        "filter_date_from",
        "filter_date_to",
    ]:

        if key in st.session_state:
            del st.session_state[key]

    st.session_state.editor_page = 1

    st.session_state.editor_db_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


def apply_editor_filters():

    st.session_state.editor_filters = {
        "patient_id": st.session_state.get(
            "filter_patient_id",
            [],
        ),
        "team": st.session_state.get(
            "filter_team",
            [],
        ),
        "tsp": st.session_state.get(
            "filter_tsp",
            [],
        ),
        "approach": st.session_state.get(
            "filter_approach",
            [],
        ),
        "case": st.session_state.get(
            "filter_case",
            [],
        ),
        "visit_no": st.session_state.get(
            "filter_visit_no",
            [],
        ),
        "sr_no": st.session_state.get(
            "filter_sr_no",
            [],
        ),
        "ward_village": st.session_state.get(
            "filter_ward_village",
            [],
        ),
        "date_from": st.session_state.get(
            "filter_date_from"
        ),
        "date_to": st.session_state.get(
            "filter_date_to"
        ),
    }

    st.session_state.editor_page_size = (
        st.session_state.get(
            "editor_page_size_selector",
            EDITOR_PAGE_SIZE_DEFAULT,
        )
    )

    st.session_state.editor_page = 1

    st.session_state.editor_db_df = None
    st.session_state.editor_source_key = None

    st.session_state.grid_version += 1


# ============================================================
# BUILD EDITOR QUERY
# ============================================================

def build_editor_query(client):

    query = (
        client
        .table(TABLE_NAME)
        .select("*")
    )

    filters = st.session_state.editor_filters

    for filter_name, values in filters.items():

        if filter_name in {
            "date_from",
            "date_to",
        }:
            continue

        column = FILTER_COLUMNS.get(
            filter_name
        )

        if not column or not values:
            continue

        converted_values = (
            convert_filter_values(
                filter_name,
                values,
            )
        )

        if not converted_values:
            continue

        query = query.in_(
            column,
            converted_values,
        )

    if DATE_COLUMN:

        date_from = filters.get(
            "date_from"
        )

        date_to = filters.get(
            "date_to"
        )

        if date_from:

            query = query.gte(
                DATE_COLUMN,
                date_from.isoformat(),
            )

        if date_to:

            date_after = (
                date_to
                + timedelta(days=1)
            )

            query = query.lt(
                DATE_COLUMN,
                date_after.isoformat(),
            )

    query = query.order(
        PRIMARY_KEY,
        desc=True,
    )

    return query


# ============================================================
# LOAD EDITOR PAGE
# ============================================================

def load_editor_page():

    client = get_user_client()

    page = max(
        1,
        int(
            st.session_state.editor_page
        ),
    )

    page_size = max(
        1,
        int(
            st.session_state.editor_page_size
        ),
    )

    start = (
        page - 1
    ) * page_size

    # One extra row tells us whether another page exists.
    end = start + page_size

    query = build_editor_query(
        client
    )

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

    raw_df = pd.DataFrame(
        rows,
        columns=TABLE_COLUMNS,
    )

    return raw_df, has_next


# ============================================================
# ADD NEW ROW
# ============================================================

def add_new_row():

    if not can_add:
        return

    temp_id = (
        f"new_{datetime.now().timestamp()}"
    )

    record = {
        column: None
        for column in TABLE_COLUMNS
    }

    record["_temp_id"] = temp_id

    # Do not automatically invent a PatientID.
    # User can enter it in the grid.
    st.session_state.pending_inserts.append(
        record
    )

    st.session_state.grid_version += 1


# ============================================================
# DELETE SELECTED GRID ROWS
# ============================================================

def get_selected_rows(grid_response):

    selected = getattr(
        grid_response,
        "selected_rows",
        None,
    )

    if selected is None:
        return []

    if isinstance(selected, pd.DataFrame):
        return selected.to_dict(
            orient="records"
        )

    if isinstance(selected, list):
        return selected

    return []


def delete_selected_rows(
    selected_rows
):

    if not can_delete:
        return

    if not selected_rows:
        return

    for row in selected_rows:

        # ----------------------------------------------------
        # Pending INSERT
        # ----------------------------------------------------

        temp_id = row.get(
            "_temp_id"
        )

        if temp_id:

            st.session_state.pending_inserts = [
                record
                for record in
                st.session_state.pending_inserts
                if record.get("_temp_id")
                != temp_id
            ]

            continue

        # ----------------------------------------------------
        # Existing DB record
        # ----------------------------------------------------

        primary_id = normalize_key(
            row.get(PRIMARY_KEY)
        )

        if primary_id is None:
            continue

        st.session_state.pending_deletes.add(
            primary_id
        )

        # If it was previously edited,
        # deletion supersedes the update.
        st.session_state.pending_updates.pop(
            primary_id,
            None,
        )

    st.session_state.grid_version += 1


# ============================================================
# AG GRID JAVASCRIPT
# ============================================================

EDITABLE_CELL_STYLE = JsCode(
    """
    function(params) {
        if (params.node && params.node.data &&
            params.node.data._temp_id) {
            return {
                'fontStyle': 'italic'
            };
        }
        return {};
    }
    """
)


# ============================================================
# BUILD GRID OPTIONS
# ============================================================

def build_grid_options(
    df,
    editable=False,
    selectable=False,
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

    if selectable:

        gb.configure_selection(
            "multiple",
            use_checkbox=True,
            header_checkbox=True,
            groupSelectsChildren=True,
        )

    # --------------------------------------------------------
    # Column widths
    # --------------------------------------------------------

    for column in df.columns:

        if column == "_temp_id":
            continue

        width = 150
        min_width = 120

        lower = column.lower()

        if column == PRIMARY_KEY:

            width = 190
            min_width = 170

        elif lower in {
            "updated_at",
            "created_at",
        }:

            width = 200
            min_width = 180

        elif lower in {
            "team",
            "visitno",
            "visit_no",
            "visit no",
            "srno",
            "sr_no",
            "sr no",
        }:

            width = 110
            min_width = 90

        elif lower in {
            "tsp",
            "approach",
            "case",
        }:

            width = 160
            min_width = 130

        elif (
            "ward" in lower
            or "village" in lower
        ):

            width = 200
            min_width = 160

        elif (
            lower == "date"
            or lower.endswith("_date")
        ):

            width = 150
            min_width = 130

        elif any(
            term in lower
            for term in [
                "name",
                "address",
                "remark",
                "reason",
            ]
        ):

            width = 220
            min_width = 180

        gb.configure_column(
            column,
            width=width,
            minWidth=min_width,
            flex=0,
        )

    if "_temp_id" in df.columns:

        gb.configure_column(
            "_temp_id",
            hide=True,
        )

    grid_options = gb.build()

    grid_options.update(
        {
            "domLayout": "normal",
            "suppressHorizontalScroll": False,
            "suppressSizeToFit": True,
            "suppressColumnVirtualisation": False,
            "enableCellTextSelection": True,
            "ensureDomOrder": True,
            "rowSelection": (
                "multiple"
                if selectable
                else None
            ),
            "getRowStyle": EDITABLE_CELL_STYLE,
        }
    )

    return grid_options


# ============================================================
# RENDER EDITOR GRID
# ============================================================

def render_editor_grid(
    raw_df,
):

    display_df = (apply_pending_changes_to_df(raw_df))

    display_df = make_dataframe_aggrid_safe(display_df)

    # Always ensure expected columns exist.
    for column in TABLE_COLUMNS:

        if column not in display_df.columns:
            display_df[column] = None

    if "_temp_id" not in display_df.columns:
        display_df["_temp_id"] = None

    display_df = display_df[
        TABLE_COLUMNS + ["_temp_id"]
    ]

    grid_options = build_grid_options(
        display_df,
        editable=can_edit,
        selectable=can_delete,
    )

    grid_key = (
        "database_editor_grid_"
        f"{st.session_state.grid_version}"
    )

    response = AgGrid(
        display_df,
        gridOptions=grid_options,
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=(
            GridUpdateMode.VALUE_CHANGED
            | GridUpdateMode.SELECTION_CHANGED
        ),
        fit_columns_on_grid_load=False,
        allow_unsafe_jscode=True,
        theme="streamlit",
        height=600,
        width="100%",
        key=grid_key,
    )

    edited_df = response.get(
        "data",
        display_df,
    )

    if isinstance(
        edited_df,
        list,
    ):
        edited_df = pd.DataFrame(
            edited_df
        )

    if not isinstance(
        edited_df,
        pd.DataFrame,
    ):
        edited_df = display_df.copy()

    # --------------------------------------------------------
    # Capture existing-row changes
    # --------------------------------------------------------

    capture_editor_changes(
        raw_df,
        edited_df,
        PRIMARY_KEY,
    )

    # --------------------------------------------------------
    # Capture pending INSERT edits
    # --------------------------------------------------------

    if "_temp_id" in edited_df.columns:

        for _, row in edited_df.iterrows():

            temp_id = row.get(
                "_temp_id"
            )

            if not temp_id:
                continue

            record = {
                column: clean_value(
                    row.get(column)
                )
                for column in TABLE_COLUMNS
            }

            update_pending_insert(
                temp_id,
                record,
            )

    return response


# ============================================================
# EDITOR FILTER UI
# ============================================================

def render_editor_filters():

    options = (
        st.session_state
        .editor_filter_options
    )

    current = (
        st.session_state.editor_filters
    )

    # --------------------------------------------------------
    # Row 1
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        patient_options = options.get(
            "patient_id",
            [],
        )

        st.multiselect(
            "Patient ID",
            patient_options,
            default=[
                str(v)
                for v in current.get(
                    "patient_id",
                    [],
                )
            ],
            key="filter_patient_id",
        )

    with c2:

        team_options = options.get(
            "team",
            [],
        )

        st.multiselect(
            "Team",
            team_options,
            default=[
                str(v)
                for v in current.get(
                    "team",
                    [],
                )
            ],
            key="filter_team",
        )

    with c3:

        tsp_options = options.get(
            "tsp",
            [],
        )

        st.multiselect(
            "TSP",
            tsp_options,
            default=[
                str(v)
                for v in current.get(
                    "tsp",
                    [],
                )
            ],
            key="filter_tsp",
        )

    with c4:

        approach_options = options.get(
            "approach",
            [],
        )

        st.multiselect(
            "Approach",
            approach_options,
            default=[
                str(v)
                for v in current.get(
                    "approach",
                    [],
                )
            ],
            key="filter_approach",
        )

    # --------------------------------------------------------
    # Row 2
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        case_options = options.get(
            "case",
            [],
        )

        st.multiselect(
            "Case",
            case_options,
            default=[
                str(v)
                for v in current.get(
                    "case",
                    [],
                )
            ],
            key="filter_case",
        )

    with c2:

        visit_options = options.get(
            "visit_no",
            [],
        )

        st.multiselect(
            "Visit No",
            visit_options,
            default=[
                str(v)
                for v in current.get(
                    "visit_no",
                    [],
                )
            ],
            key="filter_visit_no",
        )

    with c3:

        sr_options = options.get(
            "sr_no",
            [],
        )

        st.multiselect(
            "SR No",
            sr_options,
            default=[
                str(v)
                for v in current.get(
                    "sr_no",
                    [],
                )
            ],
            key="filter_sr_no",
        )

    with c4:

        ward_options = options.get(
            "ward_village",
            [],
        )

        st.multiselect(
            "Ward / Village",
            ward_options,
            default=[
                str(v)
                for v in current.get(
                    "ward_village",
                    [],
                )
            ],
            key="filter_ward_village",
        )

    # --------------------------------------------------------
    # Row 3
    # --------------------------------------------------------

    c1, c2, c3 = st.columns(
        [1, 1, 1]
    )

    with c1:

        st.date_input(
            "Date From",
            value=current.get(
                "date_from"
            ),
            key="filter_date_from",
        )

    with c2:

        st.date_input(
            "Date To",
            value=current.get(
                "date_to"
            ),
            key="filter_date_to",
        )

    with c3:

        st.selectbox(
            "Rows per page",
            [
                50,
                100,
                200,
                500,
                1000,
            ],
            index=[
                50,
                100,
                200,
                500,
                1000,
            ].index(
                st.session_state.editor_page_size
            )
            if st.session_state.editor_page_size
            in [
                50,
                100,
                200,
                500,
                1000,
            ]
            else 1,
            key="editor_page_size_selector",
        )

    # --------------------------------------------------------
    # Buttons
    # --------------------------------------------------------

    b1, b2 = st.columns(2)

    with b1:

        st.button(
            "Apply Filters",
            type="primary",
            use_container_width=True,
            key="apply_editor_filters_button",
            on_click=apply_editor_filters,
        )

    with b2:

        st.button(
            "Reset Filters",
            use_container_width=True,
            key="reset_editor_filters_button",
            on_click=reset_editor_filters,
        )


# ============================================================
# EDITOR PAGE
# ============================================================

def render_database_editor():

    st.header("Database Editor")

    if not can_edit:

        st.info(
            "You have Viewer permission. "
            "The database is read-only."
        )

    # --------------------------------------------------------
    # Load filter values
    # --------------------------------------------------------

    if not (
        st.session_state
        .editor_filter_options_loaded
    ):

        try:

            with st.spinner(
                "Loading filter values..."
            ):

                options = (
                    load_editor_filter_options()
                )

                st.session_state.editor_filter_options = (
                    options
                )

                st.session_state.editor_filter_options_loaded = (
                    True
                )

        except Exception as exc:

            st.error(
                f"Unable to load filter values: {exc}"
            )

            return

    # --------------------------------------------------------
    # Filters
    # --------------------------------------------------------

    render_editor_filters()

    st.divider()

    # --------------------------------------------------------
    # Load current page
    # --------------------------------------------------------

    filters_key = repr(
        st.session_state.editor_filters
    )

    source_key = (
        filters_key,
        st.session_state.editor_page,
        st.session_state.editor_page_size,
    )

    if (
        st.session_state.editor_db_df is None
        or st.session_state.editor_source_key
        != source_key
    ):

        try:

            with st.spinner(
                "Loading database records..."
            ):

                raw_df, has_next = (
                    load_editor_page()
                )

                st.session_state.editor_db_df = (
                    raw_df.copy()
                )

                st.session_state.editor_source_key = (
                    source_key
                )

        except Exception as exc:

            st.error(
                f"Unable to load database records: {exc}"
            )

            return

    else:

        raw_df = (
            st.session_state.editor_db_df.copy()
        )

        # Recheck whether next page exists only when
        # the page is actually loaded.
        try:

            _, has_next = load_editor_page()

        except Exception:

            has_next = False

    # --------------------------------------------------------
    # Toolbar
    # --------------------------------------------------------

    t1, t2, t3 = st.columns(
        [1.5, 1.5, 5]
    )

    with t1:

        if can_add:

            if st.button(
                "➕ Add Row",
                use_container_width=True,
                key="editor_add_row_button",
            ):

                add_new_row()

                st.rerun()

    with t2:

        if can_delete:

            # Selection is handled below after grid rendering.
            pass

    with t3:

        st.caption(
            f"Page "
            f"{st.session_state.editor_page:,}"
        )

    # --------------------------------------------------------
    # Grid
    # --------------------------------------------------------

    grid_response = render_editor_grid(
        raw_df
    )

    # --------------------------------------------------------
    # Delete selected
    # --------------------------------------------------------

    if can_delete:

        selected_rows = get_selected_rows(
            grid_response
        )

        if selected_rows:

            if st.button(
                f"🗑️ Delete Selected "
                f"({len(selected_rows)})",
                type="secondary",
                use_container_width=True,
                key="editor_delete_selected_button",
            ):

                delete_selected_rows(
                    selected_rows
                )

                st.rerun()

    # --------------------------------------------------------
    # Pending Changes
    # --------------------------------------------------------

    st.divider()

    show_pending_changes()

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

    st.divider()

    p1, p2, p3 = st.columns(
        [1, 1, 4]
    )

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
            f"Page "
            f"{st.session_state.editor_page:,}"
        )

    if (
        previous_clicked
        and st.session_state.editor_page > 1
    ):

        st.session_state.editor_page -= 1

        st.session_state.editor_db_df = None
        st.session_state.editor_source_key = None

        st.session_state.grid_version += 1

        st.rerun()

    if next_clicked and has_next:

        st.session_state.editor_page += 1

        st.session_state.editor_db_df = None
        st.session_state.editor_source_key = None

        st.session_state.grid_version += 1

        st.rerun()


# ============================================================
# EXPLORER - LOAD ALL DATA
# ============================================================

def load_explorer_all_data():

    client = get_user_client()

    with st.spinner(
        "Loading all database records..."
    ):

        df = fetch_all_rows(
            TABLE_NAME,
            client,
            select_columns="*",
            order_column=PRIMARY_KEY,
            descending=True,
        )

    return df


def refresh_explorer():

    try:

        df = load_explorer_all_data()

        st.session_state.explorer_all_df = df
        st.session_state.explorer_loaded = True
        st.session_state.explorer_page = 1

    except Exception as exc:

        st.error(
            f"Unable to load Explorer data: {exc}"
        )


# ============================================================
# EXPLORER LOCAL SEARCH
# ============================================================

def explorer_search_dataframe(
    df,
    search_text,
    search_columns,
):

    if df is None or df.empty:
        return pd.DataFrame()

    if not search_text.strip():
        return df.copy()

    search_text = (
        search_text
        .strip()
        .lower()
    )

    valid_columns = [
        column
        for column in search_columns
        if column in df.columns
    ]

    if not valid_columns:
        return df.copy()

    mask = pd.Series(
        False,
        index=df.index,
    )

    for column in valid_columns:

        values = (
            df[column]
            .astype(str)
            .str.lower()
        )

        mask |= values.str.contains(
            search_text,
            regex=False,
            na=False,
        )

    return df.loc[mask].copy()


# ============================================================
# EXPLORER
# ============================================================

def render_explorer():

    st.header("Explorer")

    st.caption(
        "Explorer loads all RLS-visible records into the "
        "application and provides local search and pagination."
    )

    # --------------------------------------------------------
    # Toolbar
    # --------------------------------------------------------

    c1, c2 = st.columns(
        [2, 5]
    )

    with c1:

        if st.button(
            "🔄 Load / Refresh All Data",
            type="primary",
            use_container_width=True,
            key="explorer_refresh_button",
        ):

            refresh_explorer()

    with c2:

        if st.session_state.explorer_loaded:

            total_records = len(
                st.session_state.explorer_all_df
            )

            st.caption(
                f"Loaded {total_records:,} records."
            )

    if not st.session_state.explorer_loaded:

        st.info(
            "Click 'Load / Refresh All Data' "
            "to load all records."
        )

        return

    df = (
        st.session_state
        .explorer_all_df
    )

    if df is None:
        st.info("No data loaded.")
        return

    # --------------------------------------------------------
    # Searchable columns
    # --------------------------------------------------------

    searchable_columns = [
        column
        for column in EXPLORER_TEXT_CANDIDATES
        if column in df.columns
    ]

    if not searchable_columns:
        searchable_columns = list(
            df.columns
        )

    if not st.session_state.explorer_search_columns:

        st.session_state.explorer_search_columns = (
            searchable_columns.copy()
        )

    selected_columns = st.multiselect(
        "Search columns",
        searchable_columns,
        default=[
            column
            for column in
            st.session_state.explorer_search_columns
            if column in searchable_columns
        ],
        key="explorer_search_columns_widget",
    )

    search_text = st.text_input(
        "Search",
        value=st.session_state.explorer_search,
        placeholder="Search loaded records...",
        key="explorer_search_widget",
    )

    st.session_state.explorer_search = (
        search_text
    )

    st.session_state.explorer_search_columns = (
        selected_columns
    )

    # --------------------------------------------------------
    # Local filtering
    # --------------------------------------------------------

    filtered_df = explorer_search_dataframe(
        df,
        search_text,
        selected_columns,
    )

    total_matching = len(
        filtered_df
    )

    # --------------------------------------------------------
    # Page size
    # --------------------------------------------------------

    size_options = [
        50,
        100,
        200,
        500,
        1000,
    ]

    current_size = (
        st.session_state.explorer_page_size
    )

    if current_size not in size_options:
        current_size = 100

    st.session_state.explorer_page_size_selector = (
        current_size
    )

    page_size = st.selectbox(
        "Rows per page",
        size_options,
        index=size_options.index(
            current_size
        ),
        key="explorer_page_size_selector",
    )

    if page_size != st.session_state.explorer_page_size:

        st.session_state.explorer_page_size = (
            page_size
        )

        st.session_state.explorer_page = 1

    # --------------------------------------------------------
    # Pagination calculation
    # --------------------------------------------------------

    if total_matching == 0:

        st.info(
            "No matching records."
        )

        return

    total_pages = max(
        1,
        (
            total_matching
            + page_size
            - 1
        )
        // page_size,
    )

    page = st.session_state.explorer_page

    if page > total_pages:

        page = total_pages

        st.session_state.explorer_page = (
            page
        )

    start = (
        page - 1
    ) * page_size

    end = start + page_size

    page_df = filtered_df.iloc[start:end].copy()

    page_df = make_dataframe_aggrid_safe(page_df)

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    st.caption(
        f"Showing "
        f"{start + 1:,}–"
        f"{min(end, total_matching):,} "
        f"of {total_matching:,} matching records "
        f"({len(df):,} loaded records)."
    )

    # --------------------------------------------------------
    # Explorer Grid
    # --------------------------------------------------------

    grid_options = build_grid_options(
        page_df,
        editable=False,
        selectable=False,
    )

    explorer_grid_key = (
        "explorer_grid_"
        f"{page}_"
        f"{total_matching}_"
        f"{len(page_df)}"
    )

    AgGrid(
        page_df,
        gridOptions=grid_options,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        update_mode=GridUpdateMode.NO_UPDATE,
        fit_columns_on_grid_load=False,
        allow_unsafe_jscode=True,
        theme="streamlit",
        height=600,
        width="100%",
        key=explorer_grid_key,
    )

    # --------------------------------------------------------
    # Export
    # --------------------------------------------------------

    csv_all = filtered_df.to_csv(
        index=False
    ).encode("utf-8-sig")

    csv_page = page_df.to_csv(
        index=False
    ).encode("utf-8-sig")

    e1, e2 = st.columns(2)

    with e1:

        st.download_button(
            "⬇️ Download Current Page",
            csv_page,
            file_name="ygntbpro_current_page.csv",
            mime="text/csv",
            use_container_width=True,
            key="explorer_download_page",
        )

    with e2:

        st.download_button(
            "⬇️ Download All Matching",
            csv_all,
            file_name="ygntbpro_all_matching.csv",
            mime="text/csv",
            use_container_width=True,
            key="explorer_download_all",
        )

    # --------------------------------------------------------
    # Pagination
    # --------------------------------------------------------

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
            f"Page {page:,} of "
            f"{total_pages:,}"
        )

    if previous_clicked and page > 1:

        st.session_state.explorer_page = (
            page - 1
        )

        st.rerun()

    if next_clicked and page < total_pages:

        st.session_state.explorer_page = (
            page + 1
        )

        st.rerun()


# ============================================================
# MAIN APPLICATION
# ============================================================

st.title("🗄️ YgnTBPro Database")

top1, top2, top3 = st.columns(
    [2, 2, 1]
)

with top1:

    st.caption(
        f"User role: **{user_role.upper()}**"
    )

with top2:

    pending_total = (
        pending_changes_count()
    )

    if pending_total:

        st.caption(
            f"Pending changes: "
            f"**{pending_total}**"
        )

    else:

        st.caption(
            "No pending changes"
        )

with top3:

    if st.button(
        "Logout",
        use_container_width=True,
        key="logout_button",
    ):

        logout_user()


tab_editor, tab_explorer = st.tabs(
    [
        "📝 Database Editor",
        "🔎 Explorer",
    ]
)


# ============================================================
# DATABASE EDITOR TAB
# ============================================================

with tab_editor:

    render_database_editor()


# ============================================================
# EXPLORER TAB
# ============================================================

with tab_explorer:

    render_explorer()