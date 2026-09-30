import os
from datetime import date

import pandas as pd
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
)

st.set_page_config(
    page_title="YgnTBPro Data Analysis Dashboard",
    page_icon="🫁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Put these in Streamlit Community Cloud -> App -> Settings -> Secrets:

SUPABASE_URL_ygntbpro = "https://kocihpxevlowqbguhstf.supabase.co"
SUPABASE_KEY_ygntbpro = "sb_publishable_JtrNLjMNSvZ5LzvXKbv2xw_mj-hl5MD"

SUPABASE_URL = st.secrets.get("SUPABASE_URL_ygntbpro", os.getenv("SUPABASE_URL", ""))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY_ygntbpro", os.getenv("SUPABASE_KEY", ""))

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
    "Bact_status", "Treatmentreferral", "MonthDiagnosis11", "Cxrr", "CXRresult",
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


def classify_symptomatic(df: pd.DataFrame, symptom_cols, target_val: str = "yes") -> pd.Series:
    cols = [symptom_cols] if isinstance(symptom_cols, str) else list(symptom_cols)
    valid_cols = [c for c in cols if c in df.columns]
    if not valid_cols:
        return pd.Series("Asymptomatic", index=df.index)
    cleaned = (
        df[valid_cols]
        .fillna("")
        .astype(str)
        .apply(lambda col: col.str.strip().str.lower())
    )
    return pd.Series(
        "Symptomatic",
        index=df.index,
    ).where(cleaned.eq(target_val.lower()).any(axis=1), "Asymptomatic")


@st.cache_data(ttl=900, show_spinner=False)
def load_table(table_name: str) -> pd.DataFrame:
    df = functionGetDataFromTable(table_name, SUPABASE_URL, SUPABASE_KEY, page_size=1000)
    if df is None:
        raise RuntimeError(f"Could not retrieve '{table_name}' from Supabase.")
    return df


@st.cache_data(ttl=900, show_spinner=False)
def prepare_data(raw_dashboard: pd.DataFrame, raw_target: pd.DataFrame):
    dashboard = raw_dashboard.copy()
    target = raw_target.copy()

    target = switchingRowToColumn(
        df=target,
        column_name="Indicator",
        preserved_column_list=COLUMN_PRESERVED_FOR_TARGET,
        value_col="Target",
    )
    target = function_uncode(target, colName=["Team"], mapping=UNCODE_MAPPING)
    target = function_reporting_period(target, date_col="ReportingDate")
    target = target.rename(columns={"Group": "Clinic"})

    dashboard = create_category(
        dashboard,
        source_col="Approach",
        criteria_mapping=MAPPING_TARGET_CATEGORY,
        output_col="TargetCategory",
        default="",
    )
    dashboard = dashboard.rename(columns={"EPI11": "Clinic"})
    dashboard = function_uncode(dashboard, colName=COLUMN_UNCODE, mapping=UNCODE_MAPPING)
    dashboard = function_reporting_period(dashboard)
    dashboard = create_category_combined(
        dashboard, CATEGORY_PHC_CRITERIA, "PrimaryHealthcare"
    )
    dashboard["Date"] = pd.to_datetime(dashboard["Date"], errors="coerce")
    target["ReportingDate"] = pd.to_datetime(target["ReportingDate"], errors="coerce")
    dashboard = dashboard.dropna(subset=["Date"]).copy()
    target = target.dropna(subset=["ReportingDate"]).copy()
    return dashboard, target


def options_for(df: pd.DataFrame, col: str):
    if col not in df.columns:
        return []
    values = (
        df[col].dropna().astype(str).str.strip()
        .replace({"nan": "blank", "None": "blank", "": "blank"})
        .unique().tolist()
    )
    return sorted(values)


def safe_plotly(fig, height=None):
    if fig is None:
        return
    st.plotly_chart(fig, use_container_width=True, theme=None, config={"displaylogo": False})


def safe_section(title, fn):
    st.subheader(title)
    try:
        fn()
    except Exception as exc:
        st.error(f"Unable to render this section: {exc}")


# -----------------------------------------------------------------------------
# App startup
# -----------------------------------------------------------------------------
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Supabase credentials are missing. Add SUPABASE_URL and SUPABASE_KEY to Streamlit Secrets.")
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

min_date = df_slicer["Date"].min().date()
max_date = df_slicer["Date"].max().date()

# -----------------------------------------------------------------------------
# Sidebar filters
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    date_from = st.date_input("From", value=min_date, min_value=min_date, max_value=max_date)
    date_to = st.date_input("To", value=max_date, min_value=min_date, max_value=max_date)

    if date_from > date_to:
        st.error("The From date must not be later than the To date.")
        st.stop()

    selections = {}
    for col in COLUMNS_SLICER:
        opts = options_for(df_slicer, col)
        selections[col] = st.multiselect(col, opts, default=[], key=f"filter_{col}")

    if st.button("Clear all filters", use_container_width=True):
        for col in COLUMNS_SLICER:
            st.session_state[f"filter_{col}"] = []
        st.rerun()

# -----------------------------------------------------------------------------
# Apply filters once. Everything below uses these cached-in-memory filtered frames.
# -----------------------------------------------------------------------------
filtered_df = df_slicer[
    (df_slicer["Date"].dt.date >= date_from)
    & (df_slicer["Date"].dt.date <= date_to)
].copy()

# Targets are monthly reporting periods; use the months covered by the selected dates.
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

filtered_df["Symptom"] = classify_symptomatic(filtered_df, COLUMN_SYMPTOM)

achievement = function_indicator_achievement(filtered_df, CRITERIA_INDICATORS)
progress = function_merge_target(
    achievement,
    target_df,
    indicators=tuple(CRITERIA_INDICATORS.keys()),
)

# -----------------------------------------------------------------------------
# KPI calculations
# -----------------------------------------------------------------------------
total_attendant = len(filtered_df)
presumptive_count = int(achievement["Examined Cases"].sum())
notified_count = int(achievement["Notified Cases"].sum())
bc_count = int(achievement["BC Cases"].sum())

presumptive_target = float(progress.get("Examined Cases Target", pd.Series(dtype=float)).sum())
notified_target = float(progress.get("Notified Cases Target", pd.Series(dtype=float)).sum())
bc_target = float(progress.get("BC Cases Target", pd.Series(dtype=float)).sum())

# ---------- KPI ACHIEVEMENT ----------
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
    st.metric("Total Attendant",f"{total_attendant:,}")

with k2:
    st.metric("Examined Cases",f"{presumptive_count:,}")
    text, color = achievement_text(presumptive_count,presumptive_target)
    st.markdown(f"<span style='color:{color}; font-weight:600;'>{text}</span>",unsafe_allow_html=True)

with k3:
    st.metric("Notified Cases",f"{notified_count:,}")
    text, color = achievement_text(notified_count,notified_target)
    st.markdown(f"<span style='color:{color}; font-weight:600;'>{text}</span>",unsafe_allow_html=True)

with k4:
    st.metric("BC Cases",f"{bc_count:,}")
    text, color = achievement_text(bc_count,bc_target)
    st.markdown(f"<span style='color:{color}; font-weight:600;'>{text}</span>",unsafe_allow_html=True)


# def pct(n, d):
#     return f"{(n / d * 100):.0f}%" if d > 0 else "N/A"
# k1, k2, k3, k4 = st.columns(4)
# k1.metric("Total Attendant", f"{total_attendant:,}")
# k2.metric("Examined Cases", f"{presumptive_count:,}", f"{pct(presumptive_count, presumptive_target)} of {presumptive_target:,.0f}")
# k3.metric("Notified Cases", f"{notified_count:,}", f"{pct(notified_count, notified_target)} of {notified_target:,.0f}")
# k4.metric("BC Cases", f"{bc_count:,}", f"{pct(bc_count, bc_target)} of {bc_target:,.0f}")

st.caption(
    f"SELECTED PERIOD: From {date_from:%d %b %Y} To {date_to:%d %b %Y} | "
    f"Records: {len(filtered_df):,}"
)

# -----------------------------------------------------------------------------
# Render only the selected section. This is substantially lighter than rerendering
# every chart on every Streamlit interaction.
# -----------------------------------------------------------------------------

# tab1, tab2, tab3, tab4 = st.tabs([
#     "📊 Overview",
#     "🫁 TB Care Cascade",
#     "🏥 Primary Healthcare",
#     "🔎 Detailed Analysis"
# ])

section = st.radio(
    "Dashboard section",
    ["📊 Overview", "🫁 Tuberculosis", "🏥 Primary Healthcare", "🔎 Analysis"],
    horizontal=True,
)

# with tab1:
#     st.subheader("Overview")
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

# with tab2:
#     st.subheader("TB Care Cascade")
elif section == "🫁 Tuberculosis":
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
# with tab3:
#     st.subheader("Primary Healthcare")
elif section == "🏥 Primary Healthcare":
    c1, c2 = st.columns(2)
    with c1:
        safe_section("Primary Healthcare Distribution", lambda: safe_plotly(
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
# with tab4:
#     st.subheader("Detailed Analysis")
else:
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

st.divider()
st.caption("YgnTBPro • Streamlit Community Cloud • Data source: Supabase")
