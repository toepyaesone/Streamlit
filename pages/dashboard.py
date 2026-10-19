import os
import pandas as pd
import numpy as np
import streamlit as st

from functions import (
    functionGetDataFromTable,
    switchingRowToColumn,
    function_uncode,
    function_reporting_period,
    create_category,
    create_category_combined,
    function_indicator_achievement,
    function_merge_target,
    ci_entitled,
    plotly_achievement_target_dropdown,
    plotly_variance_heatmap,
    plotly_combo_bar_percent,
    plotly_gender_agegroup,
    plotly_stack_bar,
    function_heatmap,
    function_sankey_cascade_log,
    plotly_waterfall,
    plotly_scatter_bubble,
    plot_nested_donut_chart,
    plot_scatter_sunburst,
    plotly_table_pivot,
    plotly_target_achievement_allcharts,
    plotly_funnel,
    plotly_table_count_percent,
    plotly_table_builder,
    plotly_excel_pivot_builder,
    dataframe_table_builder, 
    dataframe_excel_pivot_builder
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
SUPABASE_URL = st.secrets.get(
    "SUPABASE_URL_ygntbpro",
    os.getenv("SUPABASE_URL_ygntbpro", os.getenv("SUPABASE_URL", "")),
)
SUPABASE_KEY = st.secrets.get(
    "SUPABASE_KEY_ygntbpro",
    os.getenv("SUPABASE_KEY_ygntbpro", os.getenv("SUPABASE_KEY", "")),
)

COLUMN_UNCODE = [
    "Team", "Sex", "VOL", "Referralfor", "Cough", "Fever", "Wtloss",
    "Nightsweat", "Haemoptysis", "Chestpain", "Fatigue", "Neckglands",
    "TBcontact", "MDRTBcontact", "TBTreatmenthistory", "Smoking",
    "Reasonforexamination", "TypeofPatient", "PublicHealthCare1",
    "TypeofPatient1", "DM1", "HT1", "DMHT1", "RTIAVI1", "Generalweakness1",
    "Other1", "Cxrr", "CXRresult", "Sputum_request", "Micror",
    "Sputummicroscopyresult", "Genexpertrequested", "GeneXpertresult",
    "Bact_status", "Case", "Treatmentreferral", "TreatmentRegimen",
    "Placeforreferral", "TreatmentOutcome1211", "ContactInvestigation111",
    "DOTSupervision111", "DOTsupervisiontillTreatmentComp111", "Seeing1",
    "Hearing1", "Walking1", "Cognition1", "Selfcare1", "Communication1",
    "Disability1", "Xray2ndReading11", "CXRresult211", "TypeofTBTreatment",
]

COLUMN_SYMPTOM = [
    "Cough", "Fever", "Wtloss", "Nightsweat", "Haemoptysis", "Chestpain",
    "Fatigue", "Neckglands",
]

COLUMN_PRESERVED_FOR_TARGET = [
    "ReportingDate", "Team", "Tsp", "TargetCategory", "Group"
]

UNCODE_MAPPING = {
    "CXRresult": {"1": "Normal", "2": "TB Active", "3": "TB Suspect", "4": "TB Healed", "5": "Other Abnormal"},
    "CXRresult211": {"1": "Normal", "2": "TB Active", "3": "TB Suspect", "4": "TB Healed", "5": "Other Abnormal"},
    "GeneXpertresult": {"0": "N", "1": "I", "2": "T", "3": "RR", "4": "TI", "5": "Denied", "6": "Missing", "7": "TT"},
    "Placeforreferral": {"1": "NTP", "2": "MMA", "3": "PSI", "4": "MATA", "5": "Other"},
    "TreatmentRegimen": {"1": "IR", "2": "RR", "3": "CR", "4": "MDR", "5": "MR"},
    "TypeofTBTreatment": {"1": "DS-TB", "2": "DR-TB", "3": "TPT"},
    "Sex": {"1": "Male", "2": "Female"},
    "Cxrr": {"1": "Requested", "2": "Not Requested"},
    "Reasonforexamination": {"1": "Diagnosis", "2": "Follow-Up"},
    "VOL": {"1": "Volunteer Referral", "2": "Walk-In"},
    "Referralfor": {"1": "Presumptive", "2": "CI"},
    "Case": {"1": "TB", "2": "No TB"},
    "DM1": {"1": "DM-New", "2": "No DM", "3": "DM-Old"},
    "HT1": {"1": "HT-New", "2": "No DM", "3": "HT-Old"},
    "HIVStatus": {"N": "Negative", "P": "Positive", "U": "Unknown"},
    "Genexpertrequested": {"1": "Requested", "2": "Not Requested"},
    "Bact_status": {"1": "BC", "2": "CD"},
    "Treatmentreferral": {"1": "Registered", "2": "Not Registered"},
    "TypeofPatient1": {"1": "New", "2": "Old"},
    "Team": {"1": "MMA", "5": "MATA"},
}

CRITERIA_INDICATORS = {
    "Examined Cases": {"Reasonforexamination": "Diagnosis"},
    "Notified Cases": {"Reasonforexamination": "Diagnosis", "Case": "TB"},
    "BC Cases": {"Reasonforexamination": "Diagnosis", "Case": "TB", "Bact_status": "BC"},
}

CATEGORY_PHC_CRITERIA = {
    "DM1": {"DM-New": "DM", "DM-Old": "DM"},
    "HT1": {"HT-New": "HT", "HT-Old": "HT"},
    "RTIAVI1": {"Yes": "AVI"},
    "Generalweakness1": {"Yes": "General Weakness"},
    "Other1": {"Yes": "Others"},
}

COLUMNS_SLICER = [
    "Team", "Tsp", "Approach", "Clinic", "Reasonforexamination", "Case",
    "Bact_status", "Treatmentreferral","VOL","Referralfor", "MonthDiagnosis11", "Cxrr", "CXRresult",
    "CXRresult211", "Genexpertrequested", "GeneXpertresult", "TypeofTBTreatment",
    "TargetCategory",
]

COLUMN_CI_DOTS = [
    "Case", "Bact_status", "Treatmentreferral", "TypeofTBTreatment", "Age",
    "HIVStatus", "ContactInvestigation111", "DOTSupervision111",
    "DOTStartedDate111", "DOTsupervisiontillTreatmentComp111", "Tsp", "Ptstsp",
    "VOL", "Referralfor", "VolunteerName", "Organization", "TreatmentOutcome1211",
    "Tx_Outcome_Date", "DOTvolName111", "VolunteerGender111", "VolunteerOrganization111",
]

MAPPING_TARGET_CATEGORY = {
    "PPM": ["PPM", "Diagnostic Center"],
    "Mobile": ["Mobile Visit", "Elderly Care", "Touring"],
}


def classify_symptomatic(df: pd.DataFrame, symptom_cols, target_vals=("yes", "1")) -> pd.Series:
    cols = [symptom_cols] if isinstance(symptom_cols, str) else list(symptom_cols)
    valid_cols = [c for c in cols if c in df.columns]
    if not valid_cols:
        return pd.Series("Asymptomatic", index=df.index)
    cleaned = df[valid_cols].fillna("").astype(str).apply(lambda c: c.str.strip().str.lower())
    is_symptomatic = cleaned.isin([v.lower() for v in target_vals]).any(axis=1)
    return pd.Series("Asymptomatic", index=df.index).mask(is_symptomatic, "Symptomatic")


@st.cache_data(ttl=900, show_spinner=False)
def load_table(table_name: str) -> pd.DataFrame:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Supabase credentials are missing. Configure SUPABASE_URL_ygntbpro "
            "and SUPABASE_KEY_ygntbpro in Streamlit Secrets or environment variables."
        )
    df = functionGetDataFromTable(table_name, SUPABASE_URL, SUPABASE_KEY, page_size=1000)
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        raise RuntimeError(f"Could not retrieve '{table_name}' from Supabase.")
    return df


@st.cache_data(ttl=900, show_spinner=False)
def prepare_data(raw_dashboard: pd.DataFrame, raw_target: pd.DataFrame):
    dashboard = raw_dashboard.copy()
    target = raw_target.copy()

    valid_target_preserved = [c for c in COLUMN_PRESERVED_FOR_TARGET if c in target.columns]
    target = switchingRowToColumn(
        df=target,
        column_name="Indicator",
        preserved_column_list=valid_target_preserved,
        value_col="Target",
    )
    if "Team" in target.columns:
        target = function_uncode(target, colName=["Team"], mapping=UNCODE_MAPPING)
    if "ReportingDate" in target.columns:
        target = function_reporting_period(target, date_col="ReportingDate")
    if "Group" in target.columns:
        target = target.rename(columns={"Group": "Clinic"})

    if "Approach" in dashboard.columns:
        dashboard = create_category(
            dashboard,
            source_col="Approach",
            criteria_mapping=MAPPING_TARGET_CATEGORY,
            output_col="TargetCategory",
            default="",
        )
    if "EPI11" in dashboard.columns:
        dashboard = dashboard.rename(columns={"EPI11": "Clinic"})
        
    valid_uncode_cols = [c for c in COLUMN_UNCODE if c in dashboard.columns]
    dashboard = function_uncode(dashboard, colName=valid_uncode_cols, mapping=UNCODE_MAPPING)
    dashboard = function_reporting_period(dashboard)
    dashboard = create_category_combined(
        dashboard, CATEGORY_PHC_CRITERIA, "PrimaryHealthcare"
    )
    
    if "Date" in dashboard.columns:
        dashboard["Date"] = pd.to_datetime(dashboard["Date"], errors="coerce")
        dashboard = dashboard.dropna(subset=["Date"]).copy()
    if "ReportingDate" in target.columns:
        target["ReportingDate"] = pd.to_datetime(target["ReportingDate"], errors="coerce")
        target = target.dropna(subset=["ReportingDate"]).copy()
        
    return dashboard, target


def options_for(df: pd.DataFrame, col: str):
    if col not in df.columns:
        return []
    values = (
        df[col].dropna().astype(str).str.strip()
    )
    # Drop entries that are textual representations of missing values
    values = values[~values.str.lower().isin(["", "nan", "none", "<na>", "nat"])]
    return sorted(values.unique().tolist())


def safe_plotly(fig):
    if fig is None:
        return
    st.plotly_chart(fig, use_container_width=True, theme=None, config={"displaylogo": False})


def safe_section(title, fn):
    st.subheader(title)
    try:
        fn()
    except Exception as exc:
        st.error(f"Unable to render this section: {exc}")

# def safe_table(styled_df):
#     """Renders a Pandas DataFrame or Styler safely as a native Streamlit table."""
#     if styled_df is None:
#         st.info("No data available for the current selection.")
#         return

#     # Dynamically calculate height so all rows fit cleanly without unnecessary scrollbars
#     n_rows = len(styled_df.data) if hasattr(styled_df, "data") else len(styled_df)
#     dynamic_height = min(max((n_rows + 1) * 35 + 3, 110), 750)

#     st.dataframe(
#         styled_df,
#         use_container_width=True,
#         hide_index=True,
#         height=dynamic_height,
#     )

def safe_table(styled_df):
    """Renders a Pandas DataFrame or Styler safely as a native Streamlit table."""
    if styled_df is None:
        st.info("No data available for the current selection.")
        return

    # Extract underlying DataFrame to check row count
    raw_df = styled_df.data if hasattr(styled_df, "data") else styled_df
    if raw_df.empty:
        st.info("No data available for the current selection.")
        return

    # Render Styler to HTML and wrap in a responsive horizontal/vertical scroll container
    table_html = styled_df.to_html()
    scrollable_container = f"""
    <div style="
        width: 100%;
        max-height: 600px;
        overflow-x: auto;
        overflow-y: auto;
        border: 1px solid #CBD5E1;
        border-radius: 6px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        margin-bottom: 1rem;
    ">
        {table_html}
    </div>
    """
    st.markdown(scrollable_container, unsafe_allow_html=True)
# -----------------------------------------------------------------------------
# Main View Setup
# -----------------------------------------------------------------------------
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error(
        "Supabase credentials are missing. Configure "
        "`SUPABASE_URL_ygntbpro` and `SUPABASE_KEY_ygntbpro` in Streamlit Secrets."
    )
    st.stop()

st.title("YgnTBPro Data Analysis Dashboard")
st.caption("Yangon TB Project • Supabase + Streamlit + Plotly")

try:
    with st.spinner("Loading YgnTBPro data from Supabase…"):
        raw_dashboard = load_table("ygntbpro")
        raw_target = load_table("target")
        df_slicer, df_target = prepare_data(raw_dashboard, raw_target)
except Exception as exc:
    st.error(f"Data loading failed: {exc}")
    st.stop()

if "Date" not in df_slicer.columns or df_slicer["Date"].dropna().empty:
    st.error("The `ygntbpro` table does not contain usable Date values for the dashboard.")
    st.stop()

min_date = df_slicer["Date"].min().date()
max_date = df_slicer["Date"].max().date()

# -----------------------------------------------------------------------------
# Sidebar filters  — generation-counter pattern avoids session_state conflicts
# -----------------------------------------------------------------------------

if "filter_gen" not in st.session_state:
    st.session_state["filter_gen"] = 0

_gen = st.session_state["filter_gen"]

with st.sidebar:
    st.header("Dashboard Filters")

    date_from = st.date_input(
        "From",
        value=min_date,
        min_value=min_date,
        max_value=max_date,
        key=f"filter_date_from_{_gen}",
    )
    date_to = st.date_input(
        "To",
        value=max_date,
        min_value=min_date,
        max_value=max_date,
        key=f"filter_date_to_{_gen}",
    )

    if date_from > date_to:
        st.error("The From date must not be later than the To date.")
        st.stop()

    selections = {}
    for col in COLUMNS_SLICER:
        opts = options_for(df_slicer, col)
        selections[col] = st.multiselect(col, opts, default=[], key=f"filter_{col}_{_gen}")

    if st.button("Clear all filters", use_container_width=True):
        # Incrementing the generation invalidates ALL widget keys — no
        # session_state writes to rendered widget keys, so no error.
        st.session_state["filter_gen"] += 1
        st.rerun()

# Apply filters
filtered_df = df_slicer[
    (df_slicer["Date"].dt.date >= date_from)
    & (df_slicer["Date"].dt.date <= date_to)
].copy()

from_month = pd.Timestamp(date_from).to_period("M").to_timestamp()
to_month = pd.Timestamp(date_to).to_period("M").to_timestamp()
target_df = df_target[
    (df_target["ReportingDate"] >= from_month)
    & (df_target["ReportingDate"] <= to_month)
].copy()

for col, selected in selections.items():
    if selected and col in filtered_df.columns:
        filtered_df = filtered_df[
            filtered_df[col].astype(str).str.strip().isin(selected)
        ]
    if selected and col in target_df.columns:
        target_df = target_df[
            target_df[col].astype(str).str.strip().isin(selected)
        ]

if filtered_df.empty:
    st.warning(
        "No records match the current filters. "
        "Please adjust the date range or filter selections."
    )
    st.stop()

filtered_df["Symptom"] = classify_symptomatic(filtered_df, COLUMN_SYMPTOM)

try:
    achievement = function_indicator_achievement(filtered_df, CRITERIA_INDICATORS)
    progress = function_merge_target(
        achievement,
        target_df,
        indicators=tuple(CRITERIA_INDICATORS.keys()),
    )
except Exception as exc:
    st.error(f"Unable to calculate target/achievement metrics: {exc}")
    st.stop()

# KPI Calculations
total_attendant = len(filtered_df)
presumptive_count = int(achievement["Examined Cases"].sum())
notified_count = int(achievement["Notified Cases"].sum())
bc_count = int(achievement["BC Cases"].sum())

presumptive_target = float(pd.to_numeric(
    progress.get("Examined Cases Target", pd.Series(dtype=float)), errors="coerce"
).fillna(0).sum())
notified_target = float(pd.to_numeric(
    progress.get("Notified Cases Target", pd.Series(dtype=float)), errors="coerce"
).fillna(0).sum())
bc_target = float(pd.to_numeric(
    progress.get("BC Cases Target", pd.Series(dtype=float)), errors="coerce"
).fillna(0).sum())


def achievement_pct(actual, target):
    if target is None or target == 0:
        return 0.0
    return (actual / target) * 100


def achievement_text(actual, target):
    ach = achievement_pct(actual, target)
    if ach >= 100:
        return f"↑ {ach:.1f}% of {target:,.0f}", "green"
    else:
        return f"↓ {ach:.1f}% of {target:,.0f}", "red"


k1, k2, k3, k4 = st.columns(4)

with k1:
    st.metric("Total Attendant", f"{total_attendant:,}")

with k2:
    st.metric("Examined Cases", f"{presumptive_count:,}")
    text, color = achievement_text(presumptive_count, presumptive_target)
    st.markdown(f"<span style='color:{color}; font-weight:600;'>{text}</span>", unsafe_allow_html=True)

with k3:
    st.metric("Notified Cases", f"{notified_count:,}")
    text, color = achievement_text(notified_count, notified_target)
    st.markdown(f"<span style='color:{color}; font-weight:600;'>{text}</span>", unsafe_allow_html=True)

with k4:
    st.metric("BC Cases", f"{bc_count:,}")
    text, color = achievement_text(bc_count, bc_target)
    st.markdown(f"<span style='color:{color}; font-weight:600;'>{text}</span>", unsafe_allow_html=True)

st.caption(
    f"SELECTED PERIOD: From {date_from:%d %b %Y} To {date_to:%d %b %Y} | "
    f"Records: {len(filtered_df):,}"
)

# Render sections
section = st.radio(
    "Dashboard section",
    ["📊 Overview", "🫁 Tuberculosis", "🏥 Primary Healthcare", "🔎 Analysis","Enrollment"],
    horizontal=True,
)

if section == "📊 Overview":
    safe_section("Target vs Achievement", lambda: safe_plotly(
        plotly_achievement_target_dropdown(
            dataframe=progress,
            achievement_columnList=[
                "Examined Cases Achievement",
                "Notified Cases Achievement",
                "BC Cases Achievement",
            ],
            target_columnList=[
                "Examined Cases Target",
                "Notified Cases Target",
                "BC Cases Target",
            ],
            period="Monthly",
            date_col="ReportingDate",
        )
    ))

    safe_section("Performance Heatmap", lambda: safe_plotly(
        plotly_variance_heatmap(progress, color_scale_range=(0, 200))
    ))

    c1, c2 = st.columns(2)
    with c1:
        safe_section("Diagnosis and TB by Township", lambda: safe_plotly(
            plotly_combo_bar_percent(
                df=filtered_df,
                xaxis_str="Tsp",
                bar_dict={"Reasonforexamination": ["Diagnosis"], "Case": ["TB"]},
                optional_percent_line_list=["Case", "Reasonforexamination"],
            )
        ))
    with c2:
        safe_section("Bacteriological Confirmation by Township", lambda: safe_plotly(
            plotly_combo_bar_percent(
                df=filtered_df,
                xaxis_str="Tsp",
                bar_dict={"Bact_status": ["BC"], "Case": ["TB"]},
                optional_percent_line_list=["Bact_status", "Case"],
            )
        ))

    c1, c2 = st.columns(2)
    with c1:
        safe_section("Diagnosis and TB by Approach", lambda: safe_plotly(
            plotly_combo_bar_percent(
                df=filtered_df,
                xaxis_str="Approach",
                bar_dict={"Reasonforexamination": ["Diagnosis"], "Case": ["TB"]},
                optional_percent_line_list=["Case", "Reasonforexamination"],
            )
        ))
    with c2:
        safe_section("Bacteriological Confirmation by Approach", lambda: safe_plotly(
            plotly_combo_bar_percent(
                df=filtered_df,
                xaxis_str="Approach",
                bar_dict={"Bact_status": ["BC"], "Case": ["TB"]},
                optional_percent_line_list=["Bact_status", "Case"],
            )
        ))

    safe_section("Age and Sex Distribution", lambda: safe_plotly(
        plotly_gender_agegroup(filtered_df, "Sex", "Age", 500)
    ))

elif section == "🫁 Tuberculosis":
    if "Case" not in filtered_df.columns:
        st.warning("The `Case` column is unavailable; no TB-specific breakdown can be shown.")
        st.stop()
    df_tb = filtered_df[filtered_df["Case"] == "TB"].copy()
    df_tb["HIVStatus"] = df_tb["HIVStatus"].replace({"P": "Positive", "N": "Negative", "Y": "Positive", "U": "Unknown", "": "Unknown"})
    df_tb["DM1"] = df_tb["DM1"].replace({"No DM": "DM - No", "DM-New": "DM - Yes", "DM-Old": "DM - Yes", "": "Unknown"})

    rename_mapping = {
        "HIVStatus": "HIV Status",
        "DM1": "DM Status",
        "TreatmentRegimen": "Treatment Regimen",
        "Treatmentreferral": "Treatment Registration",
        "TypeofTBTreatment": "Type of TB Treatment",
        "Bact_status": "Bacteriological Status",
    }
    safe_section("Categorical Breakdown", lambda: safe_plotly(
        plotly_stack_bar(
            df_tb,
            columns=list(rename_mapping.keys()),
            rename_dict=rename_mapping,
            exclude_blank=True,
            orientation="h",
            title="Categorical Breakdown",
        )
    ))

    safe_section("Distribution of Cases", lambda: safe_plotly(
        plotly_stack_bar(
            filtered_df,
            columns=["Treatmentreferral", "Tsp", "Approach", "Case", "Sex"],
            exclude_blank=True,
            orientation="h",
            title="Distribution of Cases",
        )
    ))

    c1, c2 = st.columns(2)
    with c1:
        safe_section("Chest X-ray vs GeneXpert", lambda: safe_plotly(function_heatmap(filtered_df, "CXRresult", "GeneXpertresult")))
    with c2:
        colSankey = {
            "Team": ["MATA", "MMA"],
            "VOL": ["Volunteer Referral", "Walk-In"],
            "Referralfor": ["CI", "Presumptive"],
            "Case": ["TB"],
            "Bact_status": ["BC", "CD"],
            "Treatmentreferral": ["Registered"],
        }
        df_sankey = filtered_df[filtered_df["Reasonforexamination"] == "Diagnosis"]
        safe_section("Service Provision Pathway", lambda: safe_plotly(
            function_sankey_cascade_log(
                dataframe=df_sankey,
                criteria_dict=colSankey,
                title="Service Provision Pathway",
                log_base=10,
            )
        ))

    DF_CIDOTS = filtered_df[[c for c in COLUMN_CI_DOTS if c in filtered_df.columns]].copy()
    if not DF_CIDOTS.empty:
        DF_CIDOTS = DF_CIDOTS[DF_CIDOTS["Case"] == "TB"]
        DF_CIDOTS = ci_entitled(DF_CIDOTS)

        c1, c2 = st.columns(2)
        with c1:
            safe_section("Contact Investigation", lambda: safe_plotly(
                plotly_waterfall(
                    df=DF_CIDOTS,
                    start_dict={"Notified": {"Case": ["TB"]}},
                    subtract_dict1={"Registered": {"Treatmentreferral": ["Registered"]}, "Not Registered": {"Treatmentreferral": ["Not Registered"]}},
                    add_dict={"DS-TB_BC": {"ECI": ["DS-TB_BC"]}, "DR-TB": {"ECI": ["DR-TB"]}, "TB-HIV": {"ECI": ["TB-HIV"]}, "Under5": {"ECI": ["Under5"]}},
                    subtract_dict2={"CI Done": {"ContactInvestigation111": ["Y"]}},
                    chart_title="Contact Investigation",
                )
            ))
        with c2:
            safe_section("DOTS Provision", lambda: safe_plotly(
                plotly_waterfall(
                    df=DF_CIDOTS,
                    start_dict={"Notified": {"Case": ["TB"]}},
                    subtract_dict1={"Volunteer": {"VOL": ["Volunteer Referral"]}, "Self": {"VOL": ["Walk-In"]}},
                    add_dict={"Registered": {"Treatmentreferral": ["Registered"]}},
                    subtract_dict2={"DR-TB": {"TypeofTBTreatment": ["DR-TB"]}, "DOTS": {"DOTSupervision111": ["Y"]}},
                    chart_title="DOTS Provision",
                )
            ))

elif section == "🏥 Primary Healthcare":
    c1, c2 = st.columns(2)
    with c1:
        safe_section("Primary Healthcare Consultation", lambda: safe_plotly(
            plotly_scatter_bubble(
                df=filtered_df, x_col="PrimaryHealthcare", yaxis="Approach",
                chartTitle="Primary Healthcare Consultation", exclude_blank=False,
            )
        ))
    with c2:
        safe_section("Primary Healthcare Category", lambda: safe_plotly(
            plot_nested_donut_chart(filtered_df, column_name="PrimaryHealthcare")
        ))

    safe_section("Primary Healthcare Among Examined Cases", lambda: safe_plotly(
        plot_scatter_sunburst(
            df=filtered_df,
            x_col="PrimaryHealthcare",
            yaxis="Case",
            main_title="Primary Healthcare Among Examined Cases",
            exclude_blank=True,
        )
    ))

    safe_section("Average Consultation Per Day", lambda: safe_plotly(
        plotly_table_pivot(
            dataframe=filtered_df,
            row=["Team", "Tsp", "Approach", "Clinic"],
            count_unique="Date",
            count_or_sum_all="Name",
            agg_type="count",
            optional_percent=True,
            title="Average Consultation Per Day",
        )
    ))

elif section == "🔎 Analysis":
    charts = plotly_target_achievement_allcharts(
        dataframe=progress,
        date_config={"ReportingDate": "Reporting Period"},
        bar_configs=[
            {"Examined Cases Target": "Examined Cases Target", "Examined Cases Achievement": "Examined Cases Achievement"},
            {"Notified Cases Target": "Notified Cases Target", "Notified Cases Achievement": "Notified Cases Achievement"},
            {"BC Cases Target": "BC Cases Target", "BC Cases Achievement": "BC Cases Achievement"},
        ],
        optional_percentage=True,
        percentage_calc={
            "Examined Cases": ("Examined Cases Achievement", "Examined Cases Target"),
            "Notified Cases": ("Notified Cases Achievement", "Notified Cases Target"),
            "BC Cases": ("BC Cases Achievement", "BC Cases Target"),
        },
        freq="Month",
    )
    for name, fig in charts.items():
        safe_section(name, lambda fig=fig: safe_plotly(fig))

    funnel_column_criteria = {
        "Reasonforexamination": ["Diagnosis"],
        "Cxrr": ["Requested"],
        "CXRresult": ["TB Suspect", "TB Healed", "TB Active"],
        "Genexpertrequested": ["Requested"],
        "GeneXpertresult": ["N", "T", "TT", "TI", "RR"],
        "Bact_status": ["BC"],
        "Case": ["TB"],
        "Treatmentreferral": ["Registered"],
    }
    funnel_column_rename = [
        "Screening", "CXR Request", "CXR Abnormality", "Gene Request",
        "Gene Result", "Bact Confirmed", "Notified TB", "Treatment Registered",
    ]

    c1, c2 = st.columns(2)
    with c1:
        safe_section("TB Cascade by Symptom", lambda: safe_plotly(
            plotly_funnel(filtered_df, funnel_column_criteria, funnel_column_rename, "Symptom")
        ))
    with c2:
        safe_section("TB Cascade by Approach", lambda: safe_plotly(
            plotly_funnel(filtered_df, funnel_column_criteria, funnel_column_rename, "Approach")
        ))

    safe_section("Categorical Summary", lambda: safe_plotly(
        plotly_table_count_percent(
            df=filtered_df,
            column_list=["TypeofPatient1", "Reasonforexamination", "TypeofDisease", "Transferin", "Placeforreferral"],
            optional_exclude_blank=True,
            optional_include_total=True,
        )
    ))
elif section == "Enrollment":
    safe_section("Enrollment Data Overview", lambda: safe_plotly(
        plotly_table_count_percent(
            df=filtered_df,
            column_list=["Treatmentreferral", "TreatmentRegimen", "TypeofTBTreatment", "Placeforreferral"],
            optional_exclude_blank=True,
            optional_include_total=True,
        )
    ))

    safe_section("All Registered DS TB case for selected month", lambda: safe_plotly(
        plotly_table_builder(
            df=filtered_df,
            row=["ReportingTsp11"],
            column="TreatmentRegimen",
            filters={"Treatmentreferral": "Registered", "TypeofTBTreatment": "DS-TB"},
            optional_include_total=True,
            optional_exclude_blank=True,
        )
    ))

    # safe_section("All Registered DS TB case for selected month", lambda: safe_table(
    #     dataframe_table_builder(
    #         df=filtered_df,
    #         row=["ReportingTsp11"],
    #         column="TreatmentRegimen",
    #         filters={"Treatmentreferral": "Registered", "TypeofTBTreatment": "DS-TB"},
    #         optional_include_total=True,
    #         optional_exclude_blank=True,
    #     )
    # ))


    safe_section("TB Diagnosis for selected month", lambda: safe_plotly(
        plotly_table_builder(
            df=filtered_df,
            row=["ReportingTsp11"],
            column={
                "Registered DS-TB Cases": {"TypeofTBTreatment": ["DS-TB"]},
                "Gene Xpert Tested": {"GeneXpertresult": ["N", "I", "T", "RR", "TI", "TT"]},
                "Gene Xpert RR": {"GeneXpertresult": ["RR"]},
            },
            filters={"Treatmentreferral": "Registered", "TypeofTBTreatment": "DS-TB"},
            pct_numerator="Gene Xpert Tested",
            pct_denominator="Registered DS-TB Cases",
            pct_col_name="Gene Xpert Tested (%)",
            optional_include_total=True,
            optional_exclude_blank=True,
        )
    ))

    # safe_section("TB Diagnosis for selected month", lambda: safe_table(
    #     dataframe_table_builder(
    #         df=filtered_df,
    #         row=["ReportingTsp11"],
    #         column={
    #             "Registered DS-TB Cases": {"TypeofTBTreatment": ["DS-TB"]},
    #             "Gene Xpert Tested": {"GeneXpertresult": ["N", "I", "T", "RR", "TI", "TT"]},
    #             "Gene Xpert RR": {"GeneXpertresult": ["RR"]},
    #         },
    #         filters={"Treatmentreferral": "Registered", "TypeofTBTreatment": "DS-TB"},
    #         pct_numerator="Gene Xpert Tested",
    #         pct_denominator="Registered DS-TB Cases",
    #         pct_col_name="Gene Xpert Tested (%)",
    #         optional_include_total=True,
    #         optional_exclude_blank=True,
    #     )
    # ))

# def render_custom_pivot_section(df):
#     if df is None or df.empty:
#         st.info("No data available to build a pivot table.")
#         return

#     all_cols = list(df.columns)
#     default_row = ["ReportingTsp11"] if "ReportingTsp11" in all_cols else [all_cols[0]]
#     default_col = ["TreatmentRegimen"] if "TreatmentRegimen" in all_cols else []

#     with st.expander("⚙️ Configure Custom Pivot Table (Excel-Style)", expanded=True):
#         # Row 1: Field Selections (Rows, Columns, Values, Aggregation)
#         p_col1, p_col2, p_col3, p_col4 = st.columns(4)
#         with p_col1:
#             selected_rows = st.multiselect(
#                 "Rows (Hierarchy)",
#                 options=all_cols,
#                 default=default_row,
#                 key="piv_rows"
#             )
#         with p_col2:
#             avail_cols = [c for c in all_cols if c not in selected_rows]
#             selected_cols = st.multiselect(
#                 "Columns",
#                 options=avail_cols,
#                 default=[c for c in default_col if c in avail_cols],
#                 key="piv_cols"
#             )
#         with p_col3:
#             agg_method = st.selectbox(
#                 "Calculation Method",
#                 options=["Count", "Sum", "Count Unique", "Average"],
#                 index=0,
#                 key="piv_agg"
#             )
#         with p_col4:
#             val_options = ["(Record Count)"] + all_cols if agg_method == "Count" else all_cols
#             selected_val = st.selectbox(
#                 "Value Field",
#                 options=val_options,
#                 index=0,
#                 key="piv_val"
#             )

#         # Row 2: Dynamic Filters
#         filter_cols = st.multiselect(
#             "Filter Fields (Optional)",
#             options=all_cols,
#             default=[c for c in ["Treatmentreferral", "TypeofTBTreatment"] if c in all_cols],
#             key="piv_filter_cols"
#         )
#         active_filters = {}
#         if filter_cols:
#             f_ui_cols = st.columns(min(len(filter_cols), 4))
#             for idx, f_col in enumerate(filter_cols):
#                 with f_ui_cols[idx % len(f_ui_cols)]:
#                     unique_vals = sorted(
#                         df[f_col].dropna().astype(str).str.strip().unique().tolist()
#                     )
#                     chosen_vals = st.multiselect(
#                         f"Filter: {f_col}",
#                         options=unique_vals,
#                         default=unique_vals,
#                         key=f"piv_fval_{f_col}"
#                     )
#                     active_filters[f_col] = chosen_vals

#         st.markdown("---")

#         # Row 3: Layout & Total Options
#         opt1, opt2, opt3, opt4 = st.columns(4)
#         with opt1:
#             layout_style = st.radio(
#                 "Report Layout",
#                 options=["Tabular", "Compact"],
#                 horizontal=True,
#                 key="piv_layout"
#             )
#         with opt2:
#             show_subtotals = st.checkbox("Show Subtotals", value=True, key="piv_sub")
#         with opt3:
#             show_grand_total = st.checkbox("Show Grand Total", value=True, key="piv_gt")
#         with opt4:
#             exclude_blank = st.checkbox("Exclude Blanks", value=True, key="piv_blank")

#     if not selected_rows:
#         st.warning("Please select at least one field in **Rows (Hierarchy)** to generate the pivot table.")
#         return

#     # Map UI selection to function arguments
#     agg_map = {
#         "Count": "count",
#         "Sum": "sum",
#         "Count Unique": "nunique",
#         "Average": "mean"
#     }
#     val_field = None if selected_val == "(Record Count)" else selected_val

#     fig = plotly_excel_pivot_builder(
#         df=df,
#         rows=selected_rows,
#         columns=selected_cols,
#         values=val_field,
#         agg_func=agg_map[agg_method],
#         filters=active_filters,
#         show_subtotals=show_subtotals,
#         show_grand_total=show_grand_total,
#         exclude_blank=exclude_blank,
#         layout_style=layout_style,
#     )

#     safe_plotly(fig)

# # Invoke inside your dashboard flow:
# safe_section("Custom Interactive Pivot Table", lambda: render_custom_pivot_section(filtered_df))


# ==============================================================================
# INTERACTIVE EXCEL-STYLE PIVOT TABLE BUILDER (Native Table)
# ==============================================================================
    # def render_custom_pivot_table_section(df):
    #     if df is None or df.empty:
    #         st.info("No data available to build a pivot table.")
    #         return

    #     all_cols = list(df.columns)
    #     default_row = ["ReportingTsp11"] if "ReportingTsp11" in all_cols else [all_cols[0]]
    #     default_col = ["TreatmentRegimen"] if "TreatmentRegimen" in all_cols else []

    #     with st.expander("⚙️ Configure Custom Pivot Table (Excel-Style)", expanded=True):
    #         # Row 1: Field Selections (Rows, Columns, Values, Aggregation)
    #         p_col1, p_col2, p_col3, p_col4 = st.columns(4)
    #         with p_col1:
    #             selected_rows = st.multiselect(
    #                 "Rows (Hierarchy)",
    #                 options=all_cols,
    #                 default=default_row,
    #                 key="piv_rows_tbl"
    #             )
    #         with p_col2:
    #             avail_cols = [c for c in all_cols if c not in selected_rows]
    #             selected_cols = st.multiselect(
    #                 "Columns",
    #                 options=avail_cols,
    #                 default=[c for c in default_col if c in avail_cols],
    #                 key="piv_cols_tbl"
    #             )
    #         with p_col3:
    #             agg_method = st.selectbox(
    #                 "Calculation Method",
    #                 options=["Count", "Sum", "Count Unique", "Average"],
    #                 index=0,
    #                 key="piv_agg_tbl"
    #             )
    #         with p_col4:
    #             val_options = ["(Record Count)"] + all_cols if agg_method == "Count" else all_cols
    #             selected_val = st.selectbox(
    #                 "Value Field",
    #                 options=val_options,
    #                 index=0,
    #                 key="piv_val_tbl"
    #             )

    #         # Row 2: Dynamic Filters
    #         filter_cols = st.multiselect(
    #             "Filter Fields (Optional)",
    #             options=all_cols,
    #             default=[c for c in ["Treatmentreferral", "TypeofTBTreatment"] if c in all_cols],
    #             key="piv_filter_cols_tbl"
    #         )
    #         active_filters = {}
    #         if filter_cols:
    #             f_ui_cols = st.columns(min(len(filter_cols), 4))
    #             for idx, f_col in enumerate(filter_cols):
    #                 with f_ui_cols[idx % len(f_ui_cols)]:
    #                     unique_vals = sorted(
    #                         df[f_col].dropna().astype(str).str.strip().unique().tolist()
    #                     )
    #                     chosen_vals = st.multiselect(
    #                         f"Filter: {f_col}",
    #                         options=unique_vals,
    #                         default=unique_vals,
    #                         key=f"piv_fval_tbl_{f_col}"
    #                     )
    #                     active_filters[f_col] = chosen_vals

    #         st.markdown("---")

    #         # Row 3: Layout & Total Options
    #         opt1, opt2, opt3, opt4 = st.columns(4)
    #         with opt1:
    #             layout_style = st.radio(
    #                 "Report Layout",
    #                 options=["Tabular", "Compact"],
    #                 horizontal=True,
    #                 key="piv_layout_tbl"
    #             )
    #         with opt2:
    #             show_subtotals = st.checkbox("Show Subtotals", value=True, key="piv_sub_tbl")
    #         with opt3:
    #             show_grand_total = st.checkbox("Show Grand Total", value=True, key="piv_gt_tbl")
    #         with opt4:
    #             exclude_blank = st.checkbox("Exclude Blanks", value=True, key="piv_blank_tbl")

    #     if not selected_rows:
    #         st.warning("Please select at least one field in **Rows (Hierarchy)** to generate the pivot table.")
    #         return

    #     agg_map = {
    #         "Count": "count",
    #         "Sum": "sum",
    #         "Count Unique": "nunique",
    #         "Average": "mean"
    #     }
    #     val_field = None if selected_val == "(Record Count)" else selected_val

    #     styled_pivot = dataframe_excel_pivot_builder(
    #         df=df,
    #         rows=selected_rows,
    #         columns=selected_cols,
    #         values=val_field,
    #         agg_func=agg_map[agg_method],
    #         filters=active_filters,
    #         show_subtotals=show_subtotals,
    #         show_grand_total=show_grand_total,
    #         exclude_blank=exclude_blank,
    #         layout_style=layout_style,
    #     )

    #     safe_table(styled_pivot)


    # safe_section("Custom Interactive Pivot Table", lambda: render_custom_pivot_table_section(filtered_df))


    def render_custom_pivot_table_section(df):
        if df is None or df.empty:
            st.info("No data available to build a pivot table.")
            return

        working_df = df.copy()
        base_cols = list(working_df.columns)

        # Detect likely Age column automatically for default selection
        # Set default age column to 'Age' specifically (avoiding partial matches like 'WardVillage')
        if "Age" in base_cols:
            default_age_col = "Age"
        else:
            age_candidates = [c for c in base_cols if c.lower() in ["age", "ageyear", "age_year"]]
            default_age_col = age_candidates[0] if age_candidates else base_cols[0]

        with st.expander("⚙️ Configure Custom Pivot Table (Excel-Style)", expanded=True):
            # ------------------------------------------------------------------
            # Row 0: Dynamic Age Grouping Configuration (Slider Bar)
            # ------------------------------------------------------------------
            st.markdown("##### 👶 Age Grouping Configuration")
            age_c1, age_c2, age_c3 = st.columns([1.2, 1.3, 2.5])
            with age_c1:
                enable_age_group = st.checkbox(
                    "Enable Age Grouping",
                    value=True,
                    key="piv_enable_age_tbl",
                    help="Creates a dynamic 'Age Group' field based on your slider cutoff."
                )
            with age_c2:
                age_source_col = st.selectbox(
                    "Age Column",
                    options=base_cols,
                    index=base_cols.index(default_age_col) if default_age_col in base_cols else 0,
                    disabled=not enable_age_group,
                    key="piv_age_col_tbl"
                )
            with age_c3:
                age_cutoff = st.slider(
                    "Age Cutoff (Under X vs. X and Above)",
                    min_value=1,
                    max_value=100,
                    value=5,  # Default: Under 5 and Above 5 (slide to 8 for Under 8 and Above 8)
                    step=1,
                    disabled=not enable_age_group,
                    key="piv_age_slider_tbl"
                )

            if enable_age_group and age_source_col in working_df.columns:
                numeric_age = pd.to_numeric(working_df[age_source_col], errors="coerce")
                under_label = f"Under {age_cutoff}" #f"Under {age_cutoff} (<{age_cutoff})"
                above_label = f"{age_cutoff} and Above" #f"{age_cutoff} & Above (≥{age_cutoff})"
                working_df["Age Group"] = np.where(
                    numeric_age.isna(),
                    "",
                    np.where(numeric_age < age_cutoff, under_label, above_label)
                )
                st.caption(
                    f"✨ Dynamic field **`Age Group`** created: **{under_label}** and **{above_label}**. "
                    f"You can now add **`Age Group`** to Rows, Columns (Subcategory), or Filters below."
                )

            st.markdown("---")

            # Refresh available columns (now includes "Age Group" if enabled)
            all_cols = list(working_df.columns)
            default_row = ["ReportingTsp11"] if "ReportingTsp11" in all_cols else [all_cols[0]]
            default_col = ["TreatmentRegimen"] if "TreatmentRegimen" in all_cols else []
            if enable_age_group and "Age Group" in all_cols and "Age Group" not in default_col:
                default_col = default_col + ["Age Group"]

            # ------------------------------------------------------------------
            # Row 1: Field Selections (Rows, Column Hierarchy, Aggregation, Values)
            # ------------------------------------------------------------------
            p_col1, p_col2, p_col3, p_col4 = st.columns(4)
            with p_col1:
                selected_rows = st.multiselect(
                    "Rows (Hierarchy)",
                    options=all_cols,
                    default=default_row,
                    help="Select 1 or more fields. Multiple fields create parent/child row hierarchies.",
                    key="piv_rows_tbl"
                )
            with p_col2:
                avail_cols = [c for c in all_cols if c not in selected_rows]
                selected_cols = st.multiselect(
                    "Columns (Hierarchy / Subcategory)",
                    options=avail_cols,
                    default=[c for c in default_col if c in avail_cols],
                    help="Select multiple fields (e.g., TreatmentRegimen then Age Group) to create nested column subcategories.",
                    key="piv_cols_tbl"
                )
            with p_col3:
                agg_method = st.selectbox(
                    "Calculation Method",
                    options=["Count", "Sum", "Count Unique", "Average"],
                    index=0,
                    key="piv_agg_tbl"
                )
            with p_col4:
                val_options = ["(Record Count)"] + all_cols if agg_method == "Count" else all_cols
                selected_val = st.selectbox(
                    "Value Field",
                    options=val_options,
                    index=0,
                    key="piv_val_tbl"
                )

            # ------------------------------------------------------------------
            # Row 2: Dynamic Filters
            # ------------------------------------------------------------------
            filter_cols = st.multiselect(
                "Filter Fields (Optional)",
                options=all_cols,
                default=[c for c in ["Treatmentreferral", "TypeofTBTreatment"] if c in all_cols],
                key="piv_filter_cols_tbl"
            )
            active_filters = {}
            if filter_cols:
                f_ui_cols = st.columns(min(len(filter_cols), 4))
                for idx, f_col in enumerate(filter_cols):
                    with f_ui_cols[idx % len(f_ui_cols)]:
                        unique_vals = sorted(
                            working_df[f_col].dropna().astype(str).str.strip().unique().tolist()
                        )
                        chosen_vals = st.multiselect(
                            f"Filter: {f_col}",
                            options=unique_vals,
                            default=unique_vals,
                            key=f"piv_fval_tbl_{f_col}"
                        )
                        active_filters[f_col] = chosen_vals

            st.markdown("---")

            # ------------------------------------------------------------------
            # Row 3: Layout & Subtotal/Total Options
            # ------------------------------------------------------------------
            opt1, opt2, opt3, opt4, opt5 = st.columns(5)
            with opt1:
                layout_style = st.radio(
                    "Report Layout",
                    options=["Tabular", "Compact"],
                    horizontal=True,
                    key="piv_layout_tbl"
                )
            with opt2:
                show_subtotals = st.checkbox("Row Subtotals", value=True, key="piv_sub_tbl")
            with opt3:
                show_col_subtotals = st.checkbox(
                    "Column Subtotals",
                    value=True,
                    disabled=len(selected_cols) < 2,
                    help="Shows subtotals for each parent column when 2+ column hierarchy fields are selected.",
                    key="piv_col_sub_tbl"
                )
            with opt4:
                show_grand_total = st.checkbox("Show Grand Total", value=True, key="piv_gt_tbl")
            with opt5:
                exclude_blank = st.checkbox("Exclude Blanks", value=True, key="piv_blank_tbl")

        if not selected_rows:
            st.warning("Please select at least one field in **Rows (Hierarchy)** to generate the pivot table.")
            return

        agg_map = {
            "Count": "count",
            "Sum": "sum",
            "Count Unique": "nunique",
            "Average": "mean"
        }
        val_field = None if selected_val == "(Record Count)" else selected_val

        styled_pivot = dataframe_excel_pivot_builder(
            df=working_df,
            rows=selected_rows,
            columns=selected_cols,
            values=val_field,
            agg_func=agg_map[agg_method],
            filters=active_filters,
            show_subtotals=show_subtotals,
            show_col_subtotals=show_col_subtotals,
            show_grand_total=show_grand_total,
            exclude_blank=exclude_blank,
            layout_style=layout_style,
        )

        safe_table(styled_pivot)


    safe_section("Custom Interactive Pivot Table", lambda: render_custom_pivot_table_section(filtered_df))

st.divider()
st.caption("YgnTBPro • Streamlit Community Cloud • Data source: Supabase")