"""Shared data-processing and Plotly functions for the YgnTBPro Streamlit app."""

import os
import requests
import numpy as np
import pandas as pd
import plotly.io as pio
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import requests.packages.urllib3.util.connection as urllib3_conn

ALT_CLOUDFLARE_IPS = ["104.16.132.229", "104.18.32.7", "172.67.74.135"]

MISSING_STRINGS = {
    "", "none", "nan", "null", "n/a", "na", "<na>", "nat",
    "#n/a", "-", "None", "NONE", "NaN", "NULL", "<NA>", "N/A", "NaT"
}

COLUMN_DISABILITY = [
    "Seeing1", "Hearing1", "Walking1", "Cognition1",
    "Selfcare1", "Communication1"
]

UNCODE_DISABILITY = {
    "1": "No - No Difficulty",
    "2": "Yes - Some Difficulty",
    "3": "Yes - A lot of Difficulty",
    "4": "Yes - Can not do it at all",
}

UNCODE_DEFAULT = {"1": "Yes", "2": "No"}

def _create_censorship_resistant_session(host_domain: str, alt_ip: str) -> requests.Session:
  orig_create_connection = urllib3_conn.create_connection
  def patched_create_connection(address, *args, **kwargs):
    host, port = address
    if host == host_domain:
      host = alt_ip
    return orig_create_connection((host, port), *args, **kwargs)
  session = requests.Session()
  urllib3_conn.create_connection = patched_create_connection
  return session

def functionGetDataFromTable(tableName: str, url: str, key: str, page_size: int = 1000) -> pd.DataFrame:
  endpoint = f"{url.rstrip('/')}/rest/v1/{tableName}?select=*"
  host_domain = url.replace("https://", "").replace("http://", "").split("/")[0]
  headers = {
      "apikey": key.strip(),
      "Authorization": f"Bearer {key.strip()}",
      "Content-Type": "application/json",
  }
  session = None
  for alt_ip in [None] + ALT_CLOUDFLARE_IPS:
    try:
      if alt_ip is None:
        test_session = requests.Session()
      else:
        test_session = _create_censorship_resistant_session(
            host_domain, alt_ip
        )
      check_res = test_session.get(f"{url}/rest/v1/", headers=headers, timeout=5)
      if check_res.status_code < 500:
        session = test_session
        if alt_ip:
          print(f"⚡ Connected via unblocked Cloudflare route: {alt_ip}")
        break
    except requests.exceptions.RequestException:
      continue

  if session is None:
    print("❌ Failed to reach Supabase across all routes. ISP block may require a Cloudflare Worker Relay.")
    return None

  all_data = []
  start_index = 0

  try:
    while True:
      page_headers = headers.copy()
      page_headers["Range"] = f"{start_index}-{start_index + page_size - 1}"
      response = session.get(endpoint, headers=page_headers, timeout=15)
      response.raise_for_status()
      chunk = response.json()
      if not chunk:
        break
      all_data.extend(chunk)
      if len(chunk) < page_size:
        break
      start_index += page_size

    print(f"✅ Successfully retrieved ALL {len(all_data)} rows from '{tableName}'")
    return pd.DataFrame(all_data)

  except Exception as err:
    print(f"❌ An error occurred during data retrieval: {err}")
    return None

def normalize(val):
  if pd.isna(val):
    return ""
  s = str(val).strip()
  if s.lower() in MISSING_STRINGS:
    return ""
  try:
    f = float(s)
    return str(int(f)) if f.is_integer() else str(f)
  except (ValueError, TypeError):
    return s
      
def clean_missing(df: pd.DataFrame) -> pd.DataFrame:
  df = df.copy()
  non_datetime_cols = [col for col in df.columns if not pd.api.types.is_datetime64_any_dtype(df[col])]
  for col in non_datetime_cols:
    df[col] = df[col].astype(str).str.strip()
  df[non_datetime_cols] = df[non_datetime_cols].replace({s: "" for s in MISSING_STRINGS})
  return df

def function_uncode(df: pd.DataFrame, colName=None, mapping=None) -> pd.DataFrame:
  df = clean_missing(df)
  mapping = mapping or {}
  if colName is None:
    print("No columns specified for uncode. Please provide a column name or list of column names.")
    return df
  elif isinstance(colName, str):
    columns = [colName]
  else:
    columns = list(colName)
  for col in columns:
    if (col not in df.columns or pd.api.types.is_datetime64_any_dtype(df[col])):
      continue
    if col in mapping:
      mp = {normalize(k): v for k, v in mapping[col].items()}
    elif col in COLUMN_DISABILITY:
      mp = UNCODE_DISABILITY
    else:
      mp = UNCODE_DEFAULT
    df[col] = df[col].apply(lambda x: mp.get(normalize(x), x if x else ""))
  return df

def switchingRowToColumn(df, column_name, preserved_column_list, value_col=None):
    if value_col:
        df_reshaped = df.pivot_table(
            index=preserved_column_list,
            columns=column_name,
            values=value_col,
            aggfunc="first",
        ).reset_index()
    else:
        df_reshaped = (
            df.groupby(preserved_column_list + [column_name])
            .size()
            .unstack(fill_value=0)
            .reset_index()
        )
    df_reshaped.columns.name = None
    return df_reshaped

def function_reporting_period(df,date_col="Date",cutoff=25):
    df = df.copy()
    d = pd.to_datetime(df[date_col])
    df["ReportingDate"] = np.where(d.dt.day > cutoff,(d + pd.DateOffset(months=1)).dt.to_period("M").dt.to_timestamp(),d.dt.to_period("M").dt.to_timestamp())
    return df

def create_category(df,source_col,criteria_mapping,output_col="COLUMN_NEW",default=""):
    df = df.copy()
    source = df[source_col]
    conditions = [source.isin(source_values) for source_values in criteria_mapping.values()]
    choices = list(criteria_mapping.keys())
    df[output_col] = np.select(conditions,choices,default=default)
    return df

def create_category_combined(df, criteria_dict, new_column_name='PHC Category', sep=', '):
    df_copy = df.copy()
    mapped_columns = [
        df_copy[col].map(mapping) 
        for col, mapping in criteria_dict.items() 
        if col in df_copy.columns]
    if mapped_columns:
        combined_df = pd.concat(mapped_columns, axis=1)
        df_copy[new_column_name] = combined_df.apply(
            lambda row: sep.join(dict.fromkeys(row.dropna().astype(str))), axis=1
        ).replace('', np.nan)
    else:
        df_copy[new_column_name] = np.nan
    return df_copy

def ci_entitled(df: pd.DataFrame) -> pd.DataFrame:

    df = df.copy()
    base_filter = (df["Case"] == "TB") & (df["Treatmentreferral"] == "Registered")
    age_numeric = pd.to_numeric(df["Age"], errors="coerce")
    cond_dr_tb = base_filter & (df["TypeofTBTreatment"] == "DR-TB")
    cond_tb_hiv = base_filter & (df["HIVStatus"] == "P")
    cond_under5 = base_filter & (age_numeric < 5)
    cond_dstb_bc = base_filter & (df["Bact_status"] == "BC")
    conditions = [cond_dr_tb, cond_tb_hiv, cond_under5, cond_dstb_bc]
    choices = ["DR-TB", "TB-HIV", "Under5", "DS-TB_BC"]
    df["ECI"] = np.select(conditions, choices, default=None)
    return df

def export_chart(fig: go.Figure,fig_name: str,width: int = 1200,height: int = 700,scale: float = 3.0):
    if not isinstance(fig, go.Figure):
        raise TypeError(
            "The 'fig' parameter must be a Plotly go.Figure object."
        )

    _, ext = os.path.splitext(fig_name)
    ext = ext.lower()

    if not ext:
        raise ValueError(
            "fig_name must include a valid file extension (e.g., '.png', '.svg', '.pdf')."
        )

    try:
        if ext in [".svg", ".pdf"]:
            pio.write_image(fig, fig_name, width=width, height=height)
            print(f"Vector graphic exported successfully: {fig_name}")
        else:
            pio.write_image(
                fig, fig_name, width=width, height=height, scale=scale
            )
            print(
                f"High-res image exported successfully ({int(width*scale)}x{int(height*scale)} px): {fig_name}"
            )
    except ValueError as e:
        if "kaleido" in str(e).lower():
            print(
                "\n[ERROR] Kaleido library is missing. Install it by running:\n"
                "  !pip install -U kaleido\n"
                "Then restart your Jupyter kernel and try again.\n"
            )
        else:
            raise e

def function_indicator_achievement(dataframe, criteria_indicator, group_columns=None):
  df = dataframe.copy()
  if group_columns is None:
    group_columns = ["ReportingDate", "Team", "TargetCategory", "Tsp", "Clinic"]

  # 1. Clean group_columns robustly, handling blanks and NaNs (especially for 'Clinic')
  for col in group_columns:
    if col not in df.columns:
      raise KeyError(
          f"Group column '{col}' specified in group_columns was not found."
      )

    if col == "ReportingDate":
      df[col] = pd.to_datetime(df[col], errors="coerce")
    else:
      # Safely strip strings, keeping true blanks/NaNs as pd.NA instead of "nan"
      df[col] = df[col].apply(
          lambda x: str(x).strip()
          if pd.notna(x) and str(x).strip() != ""
          else pd.NA
      )

  # 2. Evaluate criteria rules for each indicator
  for indicator, rules in criteria_indicator.items():
    flag = pd.Series(True, index=df.index)
    for column, value in rules.items():
      if column not in df.columns:
        raise KeyError(
            f"Column '{column}' required for indicator '{indicator}' was not found."
        )
      flag &= (
          df[column].fillna("").astype(str).str.strip().eq(str(value).strip())
      )
    df[indicator] = flag.astype(int)

  # 3. Group and aggregate
  indicator_columns = list(criteria_indicator.keys())

  # dropna=False ensures rows with blank/NaN clinics are preserved in the summary
  summary = (
      df.groupby(group_columns, as_index=False, dropna=False)[indicator_columns]
      .sum()
      .copy()
  )
  return summary

def function_merge_target(achievement,target,indicators=("Examined Cases", "Notified Cases", "BC Cases")):
  keys = ["ReportingDate", "Team", "TargetCategory", "Tsp", "Clinic"]
  indicators = list(indicators)

  # Check for missing columns
  missing_ach = [c for c in keys + indicators if c not in achievement.columns]
  missing_tar = [c for c in keys + indicators if c not in target.columns]
  if missing_ach:
    raise KeyError(f"Missing columns in achievement: {missing_ach}")
  if missing_tar:
    raise KeyError(f"Missing columns in target: {missing_tar}")

  ach = achievement[keys + indicators].copy()
  tar = target[keys + indicators].copy()

  # Robustly clean string keys: normalize blanks, NaNs, \r, and text representations to ""
  string_keys = ["Team", "TargetCategory", "Tsp", "Clinic"]
  for col in string_keys:
    ach[col] = (
        ach[col]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace(["nan", "None", "NAT", "NaT", "<NA>"], "")
    )
    tar[col] = (
        tar[col]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace(["nan", "None", "NAT", "NaT", "<NA>"], "")
    )

  # Parse dates safely
  ach["ReportingDate"] = pd.to_datetime(
      ach["ReportingDate"], errors="coerce", dayfirst=False
  )
  tar["ReportingDate"] = pd.to_datetime(
      tar["ReportingDate"], errors="coerce", dayfirst=False
  )

  # Filter years safely (guarding against empty dates)
  valid_ach_years = ach["ReportingDate"].dt.year
  if valid_ach_years.dropna().empty:
    raise ValueError("No valid ReportingDate found in achievement data.")

  min_year = valid_ach_years.min()
  max_year = valid_ach_years.max()
  tar = tar[tar["ReportingDate"].dt.year.between(min_year, max_year)].copy()

  # Convert indicators to numeric
  for col in indicators:
    tar[col] = pd.to_numeric(tar[col], errors="coerce")
    ach[col] = pd.to_numeric(ach[col], errors="coerce")

  # Rename columns
  tar = tar.rename(columns={c: f"{c} Target" for c in indicators})
  ach = ach.rename(columns={c: f"{c} Achievement" for c in indicators})

  return tar.merge(ach, on=keys, how="left")

def plotly_achievement_target_dropdown(dataframe: pd.DataFrame,achievement_columnList: list,target_columnList: list,period: str = "Monthly",date_col: str = "Date") -> go.Figure:
    df_clean = dataframe.copy()
    df_clean[date_col] = pd.to_datetime(df_clean[date_col])
    df_clean["Year"] = df_clean[date_col].dt.year

    periods_config = {
        "Monthly": {
            "label": "Monthly",
            "freq": "MS",
            "date_fmt": "%b %Y",
            "divisor": 12,
        },
        "Quarterly": {
            "label": "Quarterly",
            "freq": "QS",
            "date_fmt": "Q%q %Y",
            "divisor": 4,
        },
        "Semiannually": {
            "label": "Semiannually",
            "freq": "6MS",
            "date_fmt": "%b %Y",
            "divisor": 2,
        },
        "Annually": {
            "label": "Annually",
            "freq": "YS",
            "date_fmt": "%Y",
            "divisor": 1,
        },
    }

    # Color palette shared between bars and horizontal target lines
    shared_colors = ["#2ca02c", "#ff7f0e", "#d9381e", "#9467bd", "#17becf"]

    annual_targets = df_clean.groupby("Year")[target_columnList].sum()
    frames_data = {}

    for p_key, p_cfg in periods_config.items():
        agg_df = (
            df_clean.set_index(date_col)
            .resample(p_cfg["freq"])[achievement_columnList]
            .sum()
            .reset_index()
        )
        agg_df["Year"] = agg_df[date_col].dt.year

        if p_key == "Quarterly":
            agg_df["Period_Label"] = agg_df[date_col].dt.to_period(
                "Q"
            ).astype(str)
        elif p_key == "Semiannually":
            # Inside periods_config for Semiannually
            agg_df["Period_Label"] = agg_df[date_col].dt.year.astype(str) + "S" + (agg_df[date_col].dt.month.gt(6).astype(int) + 1).astype(str)
        else:
            agg_df["Period_Label"] = agg_df[date_col].dt.strftime(
                p_cfg["date_fmt"]
            )

        period_targets = {}
        for t_col in target_columnList:
            yearly_t = agg_df["Year"].map(annual_targets[t_col])
            period_targets[t_col] = (yearly_t / p_cfg["divisor"]).mean()

        frames_data[p_key] = {
            "agg_df": agg_df,
            "period_targets": period_targets,
        }

    def get_log_axis_config(agg_df, period_targets):
        target_vals = list(period_targets.values())
        bar_vals = agg_df[achievement_columnList].values.flatten()
        all_vals = [v for v in np.append(bar_vals, target_vals) if v > 0]

        if not all_vals:
            min_exp, max_exp = 0, 4
        else:
            min_val, max_val = min(all_vals), max(all_vals)
            min_exp = int(np.floor(np.log10(min_val)))
            max_exp = int(np.ceil(np.log10(max_val * 1.3)))

        power_ticks = [10**i for i in range(min_exp, max_exp + 1)]
        power_texts = [f"{v:,.0f}" for v in power_ticks]

        return dict(
            type="log",
            tickmode="array",
            tickvals=power_ticks,
            ticktext=power_texts,
            range=[min_exp - 0.2, max_exp],
            gridcolor="#e5e5e5",
            side="left",
        )

    def build_chart_elements(selected_period):
        p_data = frames_data[selected_period]
        agg_df = p_data["agg_df"]
        p_targets = p_data["period_targets"]

        traces = []
        for i, ach_col in enumerate(achievement_columnList):
            color = shared_colors[i % len(shared_colors)]
            traces.append(
                go.Bar(
                    x=agg_df["Period_Label"],
                    y=agg_df[ach_col],
                    name=ach_col,
                    marker_color=color,
                    text=agg_df[ach_col],
                    texttemplate="%{text:,.0f}",
                    textposition="inside", # option :"auto", "inside", "outside"
                    insidetextanchor="middle", # DELETE OR ADD (option : "end" , "start" , "middle"
                    hovertemplate=f"<b>%{{x}}</b><br>{ach_col}: %{{y:,.2f}}<extra></extra>",
                )
            )

        shapes = []
        annotations = []
        for j, t_col in enumerate(target_columnList):
            t_val = p_targets[t_col]
            color = shared_colors[j % len(shared_colors)]

            # Native line shape across plot frame
            shapes.append(
                dict(
                    type="line",
                    xref="paper",
                    x0=0,
                    x1=1,
                    yref="y",
                    y0=t_val,
                    y1=t_val,
                    line=dict(color=color, width=2.5, dash="dash"),
                )
            )

            # Target annotation placed inside top-right of the plot line
            annotations.append(
                dict(
                    xref="paper",
                    x=0.99,  # Best fit position: flush right inside plot area
                    y=np.log10(t_val),  # Explicit log conversion for Y placement
                    yref="y",
                    text=f"<b>🎯 {t_col} ({t_val:,.0f})</b>",
                    #text=f"<b>{"Target"}({t_val:,.0f})</b>",
                    #text=f"<b>🎯 {t_col}</b><br>({t_val:,.0f})",
                    showarrow=False,
                    font=dict(color=color, size=11),
                    xanchor="right",
                    yanchor="bottom",
                    bgcolor="rgba(255, 255, 255, 0.8)",  # Soft background so text stays legible over bars
                )
            )

        yaxis_config = get_log_axis_config(agg_df, p_targets)

        return traces, shapes, annotations, yaxis_config

    initial_traces, initial_shapes, initial_annotations, initial_yaxis = (
        build_chart_elements(period)
    )
    fig = go.Figure(data=initial_traces)

    dropdown_buttons = []
    for p_key, p_cfg in periods_config.items():
        p_traces, p_shapes, p_annotations, p_yaxis = build_chart_elements(p_key)

        dropdown_buttons.append(
            dict(
                label=p_cfg["label"],
                method="update",
                args=[
                    {
                        "x": [t.x for t in p_traces],
                        "y": [t.y for t in p_traces],
                        "text": [t.text for t in p_traces],
                    },
                    {
                        # "title.text": f"Achievement vs Target ({p_key})",
                        "title.text": f"<b>Target vs Achievement<b>",
                        "shapes": p_shapes,
                        "annotations": p_annotations,
                        "yaxis": p_yaxis,
                    },
                ],
            )
        )

    fig.update_layout(
        title=dict(
            # text=f"Achievement vs Target ({period})",
            text=f"<b>Target vs Achievement<b>",
            font=dict(size=18),
            x=0.50,
            y=0.95,
        ),
        xaxis_title="Reporting Period",
        yaxis_title="Number of Cases",
        template="plotly_white",
        hovermode="x unified",
        barmode="group",
        bargap=0.2,
        bargroupgap=0.1,
        shapes=initial_shapes,
        annotations=initial_annotations,
        yaxis=initial_yaxis,
        margin=dict(t=80, b=40, l=80, r=80),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0
        ),
        updatemenus=[
            dict(
                type="dropdown",
                active=list(periods_config.keys()).index(period),
                x=0.9,
                xanchor="right",
                y=1.1,
                yanchor="top",
                showactive=True,
                buttons=dropdown_buttons,
            )
        ],
    )

    return fig

def plotly_variance_heatmap(df: pd.DataFrame,indicators: list = ["Examined Cases", "Notified Cases", "BC Cases"],date_column: str = "ReportingDate",color_scale_range: tuple = None):
  data = df.copy()

  # 1. Normalize string columns to avoid Windows \r, trailing spaces, and NaN issues
  string_cols = ["Team", "Tsp", "TargetCategory", "Clinic"]
  for col in string_cols:
    if col in data.columns:
      data[col] = (
          data[col]
          .fillna("")
          .astype(str)
          .str.strip()
          .replace(["nan", "None", "NAT", "NaT", "<NA>"], "")
      )

  # Create Tsp combination safely
  data["Tsp"] = (
      data["Team"] + " - " + data["Tsp"] + " - " + data["TargetCategory"]
  )
  data[date_column] = pd.to_datetime(data[date_column], errors="coerce")

  records = []

  for ind in indicators:
    t_col, a_col = f"{ind} Target", f"{ind} Achievement"

    if t_col in data.columns and a_col in data.columns:
      # Isolate rows where achievement data is reported (> 0)
      achieve_data = data[data[a_col] > 0]

      if achieve_data.empty:
        continue

      # 2. Determine location-specific active date range (dropna=False preserves blank clinics on Windows)
      date_bounds = (
          achieve_data.groupby(["Tsp", "Clinic"], dropna=False)[date_column]
          .agg(min_date="min", max_date="max")
          .reset_index()
      )

      # 3. Merge date bounds back to slice target/achievement data within active period
      ind_df = data[["Tsp", "Clinic", date_column, t_col, a_col]].merge(
          date_bounds, on=["Tsp", "Clinic"], how="inner"
      )

      # Filter rows within each location's active reporting window
      filtered_df = ind_df[
          (ind_df[date_column] >= ind_df["min_date"])
          & (ind_df[date_column] <= ind_df["max_date"])
      ]

      # 4. Aggregate within active reporting period using dropna=False
      summary = (
          filtered_df.groupby(
              ["Tsp", "Clinic", "min_date", "max_date"],
              as_index=False,
              dropna=False,
          )
          .agg({t_col: "sum", a_col: "sum"})
      )

      # Handle Location string formatting cleanly when Clinic is blank
      summary["Clinic_Clean"] = summary["Clinic"].apply(
          lambda x: f" | {x}" if x and x != "" else ""
      )
      summary["Location"] = summary["Tsp"] + summary["Clinic_Clean"]
      summary.drop(columns=["Clinic_Clean"], inplace=True)

      summary["Indicator"] = ind
      summary["Target"] = summary[t_col]
      summary["Achievement"] = summary[a_col]

      # Calculate Progress %
      summary["Progress_Pct"] = np.where(
          summary["Target"] > 0,
          (summary["Achievement"] / summary["Target"]) * 100,
          0.0,
      )

      # Date range display string
      summary["Period"] = (
          summary["min_date"].dt.strftime("%b %d, %Y")
          + " - "
          + summary["max_date"].dt.strftime("%b %d, %Y")
      )

      records.append(
          summary[
              [
                  "Location",
                  "Indicator",
                  "Target",
                  "Achievement",
                  "Progress_Pct",
                  "Period",
              ]
          ]
      )

  if not records:
    raise ValueError(
        "No valid achievement data found across the specified indicators."
    )

  combined_df = pd.concat(records, ignore_index=True)

  # 5. Pivot matrices for heatmap layout
  pct_matrix = combined_df.pivot(
      index="Location", columns="Indicator", values="Progress_Pct"
  )
  target_matrix = combined_df.pivot(
      index="Location", columns="Indicator", values="Target"
  )
  achieve_matrix = combined_df.pivot(
      index="Location", columns="Indicator", values="Achievement"
  )
  period_matrix = combined_df.pivot(
      index="Location", columns="Indicator", values="Period"
  )

  # Enforce original 'indicators' list ordering on the x-axis
  ordered_cols = [ind for ind in indicators if ind in pct_matrix.columns]

  pct_matrix = pct_matrix.reindex(columns=ordered_cols).fillna(0)
  target_matrix = target_matrix.reindex(columns=ordered_cols).fillna(0)
  achieve_matrix = achieve_matrix.reindex(columns=ordered_cols).fillna(0)
  period_matrix = period_matrix.reindex(columns=ordered_cols).fillna("N/A")

  # 6. Determine dynamic vs user-defined color scale range (zmin, zmax)
  if (
      color_scale_range is not None
      and isinstance(color_scale_range, (tuple, list))
      and len(color_scale_range) == 2
  ):
    z_min, z_max = color_scale_range
  else:
    z_min = float(pct_matrix.values.min())
    z_max = float(pct_matrix.values.max())

  # 7. Build multi-line text labels for cells
  text_matrix = []
  hover_matrix = []

  for loc in pct_matrix.index:
    row_text = []
    row_hover = []
    for ind in pct_matrix.columns:
      tgt = (
          int(target_matrix.loc[loc, ind])
          if loc in target_matrix.index and ind in target_matrix.columns
          else 0
      )
      ach = (
          int(achieve_matrix.loc[loc, ind])
          if loc in achieve_matrix.index and ind in achieve_matrix.columns
          else 0
      )
      pct = (
          pct_matrix.loc[loc, ind]
          if loc in pct_matrix.index and ind in pct_matrix.columns
          else 0.0
      )
      prd = (
          period_matrix.loc[loc, ind]
          if loc in period_matrix.index and ind in period_matrix.columns
          else "N/A"
      )

      cell_str = f"{ach:,} (Targeted {tgt:,}) <br><b>{pct:.1f}%</b>"
      row_text.append(cell_str)

      hover_str = (
          f"<b>Location:</b> {loc}<br>"
          f"<b>Indicator:</b> {ind}<br>"
          f"<b>Active Window:</b> {prd}<br>"
          f"<b>Achievement:</b> {ach:,}<br>"
          f"<b>Target:</b> {tgt:,}<br>"
          f"<b>Progress Rate:</b> {pct:.1f}%"
      )
      row_hover.append(hover_str)

    text_matrix.append(row_text)
    hover_matrix.append(row_hover)

  red_white_green = [
      [0.0, "#D9381E"],
      [0.5, "#FFFFFF"],
      [1.0, "#2E7D32"],
  ]

  # 8. Render Plotly Heatmap
  fig = go.Figure(
      data=go.Heatmap(
          z=pct_matrix.values,
          x=list(pct_matrix.columns),
          y=list(pct_matrix.index),
          text=text_matrix,
          texttemplate="%{text}",
          textfont={"size": 11},
          hoverinfo="text",
          hovertext=hover_matrix,
          colorscale=red_white_green,
          zmin=z_min,
          zmax=z_max,
          colorbar=dict(title="% Target Met"),
      )
  )

  overall_min = (
      pd.to_datetime(combined_df["Period"].str.split(" - ").str[0])
      .min()
      .strftime("%b-%Y")
  )
  overall_max = (
      pd.to_datetime(combined_df["Period"].str.split(" - ").str[1])
      .max()
      .strftime("%b-%Y")
  )

  fig.update_layout(
      title=dict(
          text=f"<b>Performance on Progress (From: {overall_min} to {overall_max})</b>",
          font=dict(size=18),
          x=0.50,
          y=0.93,
          xanchor="center",
      ),
      template="plotly_white",
      xaxis_title="Indicators",
      yaxis_title="Tsp | Clinic",
      xaxis=dict(categoryorder="array", categoryarray=ordered_cols),
      height=max(450, len(pct_matrix) * 50),
      margin=dict(l=150, r=40, t=90, b=40),
  )

  return fig

def plotly_target_achievement_allcharts(dataframe,date_config,bar_configs,optional_percentage=True,percentage_calc=None,freq="Month",):
  df = dataframe.copy()
  date_col = list(date_config)[0]
  date_label = date_config[date_col]

  # 1. Clean string/object date columns to remove Windows \r and hidden whitespaces
  if df[date_col].dtype == object or pd.api.types.is_string_dtype(df[date_col]):
    df[date_col] = (
        df[date_col]
        .astype(str)
        .str.strip()
        .replace(["nan", "None", "NAT", "NaT", "<NA>", ""], pd.NA)
    )

  # Parse dates safely
  df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

  # Drop rows with invalid or missing dates to prevent crash
  df = df.dropna(subset=[date_col]).copy()
  if df.empty:
    raise ValueError(
        f"No valid datetime values found in date column '{date_col}'."
    )

  f = freq.lower()

  if f in ["month", "m"]:
    period = df[date_col].dt.to_period("M")
    label_format = "%Y-%b"

  elif f in ["quarter", "q"]:
    period = df[date_col].dt.to_period("Q")
    label_format = "%Y-Q%q"

  elif f in ["semi-annual", "semi_annual", "sa"]:
    period = (
        df[date_col].dt.year.astype(str)
        + "-"
        + df[date_col].dt.month.map(lambda x: "S1" if x <= 6 else "S2")
    )
    label_format = None

  elif f in ["annual", "year", "a", "y"]:
    period = df[date_col].dt.to_period("Y")
    label_format = "%Y"

  else:
    raise ValueError("freq must be Month, Quarter, Semi-Annual or Annual")

  # 2. Assign period to a temporary helper column for stable grouping
  df["_period_key"] = period

  df_grouped = df.groupby("_period_key", as_index=False).sum(numeric_only=True)

  # Format the date label consistently across platforms
  if label_format and f not in ["semi-annual", "semi_annual", "sa"]:
    df_grouped[date_col] = df_grouped["_period_key"].dt.strftime(label_format)
  else:
    df_grouped[date_col] = df_grouped["_period_key"].astype(str)

  df_grouped = df_grouped.drop(columns=["_period_key"])

  charts = {}

  for config in bar_configs:
    target_col = next(
        (
            col
            for col, label in config.items()
            if "target" in col.lower() or "target" in label.lower()
        ),
        None,
    )

    achievement_col = next(
        (col for col in config if col != target_col), None
    )

    if not target_col or not achievement_col:
      raise ValueError(
          f"Could not identify Target/Achievement columns: {config}"
      )

    indicator = (
        achievement_col.replace(" Achievement", "")
        .replace("_Achievement", "")
        .replace("Achievement", "")
        .strip()
    )

    target = (
        pd.to_numeric(df_grouped[target_col], errors="coerce").fillna(0)
        if target_col in df_grouped.columns
        else pd.Series(0, index=df_grouped.index)
    )

    achievement = (
        pd.to_numeric(df_grouped[achievement_col], errors="coerce").fillna(0)
        if achievement_col in df_grouped.columns
        else pd.Series(0, index=df_grouped.index)
    )

    target_total = target.sum()
    achievement_total = achievement.sum()

    progress_total = (
        (achievement_total / target_total * 100)
        if target_total > 0
        else None
    )
    progress_label = (
        f"{progress_total:.0f}%" if progress_total is not None else "N/A"
    )

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=df_grouped[date_col].astype(str),
            y=achievement,
            name=f"Achievement ({achievement_total:,.0f})",
            text=achievement.map(lambda x: f"{x:,.0f}"),
            textposition="inside",
            insidetextanchor="middle",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df_grouped[date_col].astype(str),
            y=target,
            name=f"Target ({target_total:,.0f})",
            mode="lines+markers+text",
            text=target.map(lambda x: f"{x:,.0f}"),
            textposition="top center",
            line=dict(width=2),
            marker=dict(size=7),
        )
    )

    if (
        optional_percentage
        and percentage_calc
        and indicator in percentage_calc
    ):
      num_col, den_col = percentage_calc[indicator]

      if num_col in df_grouped.columns and den_col in df_grouped.columns:
        num = pd.to_numeric(df_grouped[num_col], errors="coerce")
        den = pd.to_numeric(df_grouped[den_col], errors="coerce")

        pct = pd.Series(pd.NA, index=df_grouped.index, dtype="Float64")

        valid = num.notna() & den.notna() & (num > 0) & (den > 0)
        pct.loc[valid] = (num.loc[valid] / den.loc[valid] * 100).round(0)

        fig.add_trace(
            go.Scatter(
                x=df_grouped[date_col].astype(str),
                y=pct,
                name=f"Progress ({progress_label})",
                mode="lines+markers+text",
                text=pct.map(lambda x: f"{x:.0f}%" if pd.notna(x) else ""),
                textposition="top center",
                line=dict(dash="dash", width=2),
                marker=dict(size=7),
                connectgaps=False,
                yaxis="y2",
            )
        )

    fig.update_layout(
        title=dict(text=f"<b>{indicator}</b>", x=0.5, xanchor="center"),
        height=450,
        barmode="group",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
        ),
        template="plotly_white",
        margin=dict(t=100, b=70, l=60, r=60),
    )

    fig.update_xaxes(title_text=date_label)
    fig.update_yaxes(title_text="# of Cases", rangemode="tozero")

    if optional_percentage:
      fig.update_layout(
          yaxis2=dict(
              title="Progress %",
              overlaying="y",
              side="right",
              rangemode="tozero",
              ticksuffix="%",
          )
      )

    charts[indicator] = fig

  return charts

def plotly_gender_agegroup(dataframe, sex, age, xaxis_interval=200):
  df = dataframe.copy()
  if xaxis_interval <= 0:
    raise ValueError("xaxis_interval must be greater than 0")

  # 1. Clean and validate age column safely
  df[age] = pd.to_numeric(df[age], errors="coerce")

  # 2. Robustly clean sex column: handle Windows \r, spaces, case, and blanks
  df[sex] = (
      df[sex]
      .fillna("")
      .astype(str)
      .str.strip()
      .replace(["nan", "None", "NAT", "NaT", "<NA>"], "")
      .str.upper()
  )

  # Map common variations to standard "M" and "F"
  sex_mapping = {
      "MALE": "M",
      "M": "M",
      "FEMALE": "F",
      "F": "F",
  }
  df[sex] = df[sex].map(sex_mapping).fillna("")

  # Filter out rows where age or sex is missing/invalid to ensure consistent crosstab results
  df = df.dropna(subset=[age]).copy()
  df = df[df[sex].isin(["M", "F"])].copy()

  if df.empty:
    raise ValueError(
        "No valid records found with valid Age and Sex after cleaning."
    )

  bins = [-1, 4, 9, 14, 24, 34, 44, 54, 64, float("inf")]

  labels = [
      "0-4",
      "5-9",
      "10-14",
      "15-24",
      "25-34",
      "35-44",
      "45-54",
      "55-64",
      "≥ 65",
  ]

  df["AgeGroup"] = pd.cut(df[age], bins=bins, labels=labels)

  tab = pd.crosstab(df["AgeGroup"], df[sex]).reindex(labels, fill_value=0)

  male = -tab.get("M", pd.Series(0, index=labels))
  female = tab.get("F", pd.Series(0, index=labels))

  male_total = abs(male).sum()
  female_total = female.sum()

  ratio = male_total / female_total if female_total > 0 else 0

  max_value = max(abs(male).max(), female.max())

  axis_max = (
      int((max_value + xaxis_interval - 1) // xaxis_interval) * xaxis_interval
  )
  # Prevent axis_max from being 0 if all values are 0
  if axis_max == 0:
    axis_max = xaxis_interval

  tickvals = list(range(-axis_max, axis_max + xaxis_interval, xaxis_interval))

  ticktext = [f"{abs(x):,}" for x in tickvals]

  fig = go.Figure()

  fig.add_bar(
      y=labels,
      x=male,
      orientation="h",
      name=f"Male ({male_total:,})",
      text=abs(male),
      textposition="outside",
      cliponaxis=False,
  )

  fig.add_bar(
      y=labels,
      x=female,
      orientation="h",
      name=f"Female ({female_total:,})",
      text=female,
      textposition="outside",
      cliponaxis=False,
  )

  fig.update_layout(
      title=dict(
          text="<b>Disaggregation by Sex and Age Group</b>",
          font=dict(size=18),
          x=0.50,
          y=0.95,
          xanchor="center",
      ),
      barmode="relative",
      template="plotly_white",
      xaxis=dict(
          title=f"Ratio - Male ({ratio:.2f} : 1) Female",
          range=[-axis_max * 1.10, axis_max * 1.10],
          tickmode="array",
          tickvals=tickvals,
          ticktext=ticktext,
          zeroline=True,
          zerolinewidth=2,
          showgrid=True,
      ),
      yaxis=dict(title="Age Group", categoryorder="array", categoryarray=labels),
      legend=dict(orientation="h", y=1.2, x=0.5, xanchor="center"),
      margin=dict(l=70, r=70, t=100, b=70),
  )

  return fig

def plotly_stack_bar(dataframe,columns,rename_dict=None,exclude_blank=True,orientation="v",title="Disaggregation by Category"):
  df = dataframe.copy()

  # Apply renaming early & update columns list
  if rename_dict:
    df = df.rename(columns=rename_dict)
    if isinstance(columns, str):
      columns = [rename_dict.get(columns, columns)]
    else:
      columns = [rename_dict.get(col, col) for col in columns]
  elif isinstance(columns, str):
    columns = [columns]

  missing = [col for col in columns if col not in df.columns]

  if missing:
    raise KeyError(f"Missing columns: {missing}")

  orientation = orientation.lower()

  if orientation not in ["v", "h"]:
    raise ValueError("orientation must be 'v' or 'h'")

  fig = go.Figure()

  for col in columns:
    # Clean string data safely and remove hidden Windows carriage returns (\r)
    data = (
        df[col]
        .astype("string")
        .str.replace("\r", "", regex=False)
        .str.strip()
    )

    if exclude_blank:
      data = data[data.notna() & data.ne("")]
    else:
      data = data.fillna("Blank")
      data = data.replace("", "Blank")

    if data.empty:
      continue

    # Get value counts
    counts = data.value_counts()

    # --- CROSS-PLATFORM STABILITY FIX ---
    # Convert to DataFrame to sort deterministically:
    # 1st by count descending, 2nd by category name ascending (prevents tie-sorting discrepancies between Mac and Windows)
    counts_df = counts.reset_index()
    counts_df.columns = ["category", "count"]
    counts_df = counts_df.sort_values(
        by=["count", "category"], ascending=[False, True]
    )

    counts = counts_df.set_index("category")["count"]
    total = counts.sum()

    if total == 0:
      continue

    percentages = counts / total * 100

    for category in counts.index:
      count = counts[category]
      percent = percentages[category]

      label = f"{category}<br>{count:,}<br>{percent:.1f}%"

      hover = (
          f"<b>{col}</b>"
          f"<br>Category: {category}"
          f"<br>Total: {count:,}"
          f"<br>Percent: {percent:.1f}%"
          f"<br>Column Total: {total:,}"
          "<extra></extra>"
      )

      text_angle = 0 if percent >= 12 else -90

      if orientation == "v":
        fig.add_trace(
            go.Bar(
                x=[col],
                y=[percent],
                name=str(category),
                text=[label],
                textposition="inside",
                insidetextanchor="middle",
                textangle=text_angle,
                hovertemplate=hover,
                showlegend=False,
            )
        )
      else:
        fig.add_trace(
            go.Bar(
                y=[col],
                x=[percent],
                orientation="h",
                name=str(category),
                text=[label],
                textposition="inside",
                insidetextanchor="middle",
                textangle=text_angle,
                hovertemplate=hover,
                showlegend=False,
            )
        )

  fig.update_layout(
      title=dict(
          text=f"<b>{title}</b>", font=dict(size=18), x=0.5, y=0.95, xanchor="center"
      ),
      barmode="stack",
      template="plotly_white",
      hovermode="closest",
      showlegend=False,
      margin=dict(l=50, r=50, t=80, b=50),
  )

  if orientation == "v":
    fig.update_yaxes(title=None, range=[0, 100], showticklabels=False)
    fig.update_xaxes(title=None, categoryorder="array", categoryarray=columns)
  else:
    fig.update_xaxes(title=None, range=[0, 100], showticklabels=False)
    fig.update_yaxes(title=None, categoryorder="array", categoryarray=columns)

  return fig

def function_sankey_cascade_log(dataframe,criteria_dict,title="TB Cascade of Care",width=1000,height=500,log_base=10):
  df = dataframe.copy()
  # 1. Clean all stage columns and conditional columns upfront to remove Windows \r and whitespaces
  all_cols_to_clean = set(list(criteria_dict.keys()) + ["Reasonforexamination", "Referralfor"])
  for col in all_cols_to_clean:
    if col in df.columns:
      df[col] = (
          df[col]
          .astype("string")
          .str.replace("\r", "", regex=False)
          .str.strip()
          .replace(["nan", "None", "NAT", "NaT", "<NA>"], "")
      )

  # Apply conditional logic safely after cleaning
  if "Reasonforexamination" in df.columns and "Referralfor" in df.columns:
    mask = (df["Reasonforexamination"] == "Diagnosis") & (
        df["Referralfor"].isna() | (df["Referralfor"] == "")
    )
    df.loc[mask, "Referralfor"] = "Presumptive"

  stages = list(criteria_dict.keys())
  node_map = {}

  labels = []
  node_counts = []
  node_colors = []

  colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2"]
  node_id = 0

  for i, stage in enumerate(stages):
    valid_values = criteria_dict[stage]
    for value in valid_values:
      # Convert both to string for robust matching across platforms
      str_val = str(value)
      count = df[stage].astype(str).eq(str_val).sum()
      
      node_map[(stage, value)] = node_id
      labels.append(
          f"<b>{value}</b><br>{count:,} "
          f"({count / len(df) * 100:.1f}%)" if len(df) > 0 else f"<b>{value}</b><br>0 (0.0%)"
      )
      node_counts.append(count)
      node_colors.append(colors[i % len(colors)])
      node_id += 1

  source = []
  target = []
  values = []
  original = []
  percentages = []

  for i in range(len(stages) - 1):
    stage1 = stages[i]
    stage2 = stages[i + 1]

    # Convert criteria values to string for safe filtering
    valid1 = [str(v) for v in criteria_dict[stage1]]
    valid2 = [str(v) for v in criteria_dict[stage2]]

    temp = (
        df[df[stage1].isin(valid1) & df[stage2].isin(valid2)]
        .groupby([stage1, stage2], dropna=False)
        .size()
        .reset_index(name="Count")
    )

    # Enforce deterministic sort order across Mac and Windows
    temp = temp.sort_values(by=[stage1, stage2]).reset_index(drop=True)

    for _, row in temp.iterrows():
      count = row["Count"]
      source_count = df[df[stage1].eq(row[stage1])].shape[0]
      retention = (
          (count / source_count * 100) if source_count > 0 else 0
      )
      
      source.append(node_map[(stage1, row[stage1])])
      target.append(node_map[(stage2, row[stage2])])
      values.append(np.log(count + 1) / np.log(log_base))
      original.append(count)
      percentages.append(retention)

  fig = go.Figure(
      go.Sankey(
          arrangement="snap",
          node=dict(
              pad=15,
              thickness=60,
              align="center",
              label=labels,
              color=node_colors,
              line=dict(color="black", width=1),
          ),
          link=dict(
              source=source,
              target=target,
              value=values,
              customdata=np.column_stack((original, percentages)),
              hovertemplate=(
                  "<b>%{source.label}</b>"
                  "<br>↓<br>"
                  "<b>%{target.label}</b>"
                  "<br><br>"
                  "Patients: <b>%{customdata[0]:,}</b>"
                  "<br>"
                  "Retention: <b>%{customdata[1]:.1f}%</b>"
                  "<extra></extra>"
              ),
          ),
      )
  )

  fig.update_layout(
      title=dict(
          text=f"<b>{title}</b>",
          font=dict(size=18),
          x=0.5,
          y=0.95,
          xanchor="center",
      ),
      width=width,
      height=height,
      template="plotly_white",
      dragmode="zoom",
      margin=dict(l=30, r=30, t=70, b=30),
  )

  return fig

def function_heatmap(dataframe: pd.DataFrame,Xaxis: str,Yaxis: str,exclude_blank: bool = True,colorscale: str = "Blues") -> go.Figure:
  df = dataframe.copy()
  # 1. Normalize empty strings, strip spaces, and remove Windows \r carriage returns safely
  for col in [Xaxis, Yaxis]:
    if col in df.columns:
      if (
          df[col].dtype == "object"
          or isinstance(df[col].dtype, pd.CategoricalDtype)
          or pd.api.types.is_string_dtype(df[col])
      ):
        df[col] = (df[col].astype(str).str.replace("\r", "", regex=False).str.strip().replace(r"^\s*$", np.nan, regex=True))

  if exclude_blank:
    # Drop rows ONLY if BOTH columns are missing/blank
    df = df.dropna(subset=[Xaxis, Yaxis], how="all")

  # Fill missing values with "Not Done"
  df[Xaxis] = df[Xaxis].fillna("Not Done")
  df[Yaxis] = df[Yaxis].fillna("Not Done")

  # 2. Compute Crosstab including margins (Totals)
  counts = pd.crosstab(
      df[Yaxis],
      df[Xaxis],
      dropna=False,
      margins=True,
      margins_name="Total",
  )

  # Deterministic row ordering
  other_rows = sorted(
      [str(idx) for idx in counts.index if str(idx) not in ["Total", "Not Done"]]
  )
  row_order = other_rows.copy()
  if "Not Done" in counts.index:
    row_order.insert(0, "Not Done")
  row_order.insert(0, "Total")
  counts = counts.loc[row_order]

  # Deterministic column ordering
  other_cols = sorted(
      [str(col) for col in counts.columns if str(col) not in ["Total", "Not Done"]]
  )
  col_order = other_cols.copy()
  if "Not Done" in counts.columns:
    col_order.append("Not Done")
  col_order.append("Total")
  counts = counts[col_order]

  grand_total = counts.loc["Total", "Total"]

  # 3. Build text, hover matrices, and log-transformed z-matrix
  text_matrix = []
  hover_matrix = []

  z_values = np.log10(counts.values.astype(float) + 1)

  for i, row_label in enumerate(counts.index):
    text_row = []
    hover_row = []
    for j, col_label in enumerate(counts.columns):
      c_val = counts.iloc[i, j]

      is_total_row = str(row_label) == "Total"
      is_total_col = str(col_label) == "Total"

      # Set total cells to NaN so they stay uncolored
      if is_total_row or is_total_col:
        z_values[i, j] = np.nan

      # Positional column total lookup (row 0 is 'Total')
      col_total = counts.iloc[0, j]

      if is_total_row and is_total_col:
        cell_text = f"<b>{c_val}</b><br>(100.0%)"
        hover_text = f"<b>Grand Total</b>: {c_val}"
      elif is_total_row:
        pct = (c_val / grand_total * 100) if grand_total > 0 else 0
        cell_text = f"<b>{c_val}</b><br>({pct:.1f}%)"
        hover_text = (
            f"<b>Column Total ({col_label})</b>: {c_val} ({pct:.1f}% of total)"
        )
      elif is_total_col:
        pct = (c_val / grand_total * 100) if grand_total > 0 else 0
        cell_text = f"<b>{c_val}</b><br>({pct:.1f}%)"
        hover_text = (
            f"<b>Row Total ({row_label})</b>: {c_val} ({pct:.1f}% of total)"
        )
      else:
        pct = (c_val / col_total * 100) if col_total > 0 else 0
        cell_text = f"<b>{c_val}</b><br>({pct:.1f}%)"
        hover_text = (
            f"<b>{Yaxis}</b>: {row_label}<br>"
            f"<b>{Xaxis}</b>: {col_label}<br>"
            f"<b>Count</b>: {c_val}<br>"
            f"<b>Col %</b>: {pct:.1f}%"
        )

      text_row.append(cell_text)
      hover_row.append(hover_text)

    text_matrix.append(text_row)
    hover_matrix.append(hover_row)

  # 4. Build Plotly Heatmap
  fig = go.Figure(
      data=go.Heatmap(
          z=z_values,
          x=[str(col) for col in counts.columns],
          y=[str(idx) for idx in counts.index],
          text=text_matrix,
          texttemplate="%{text}",
          hoverinfo="text",
          hovertext=hover_matrix,
          colorscale=colorscale,
          showscale=False,
      )
  )

  # 5. Layout configuration
  fig.update_layout(
      title=dict(
          text="<b>Gene Xpert Result on Chest X-ray Findings</b>",
          font=dict(size=18),
          x=0.5,
          y=0.95,
          xanchor="center",
      ),
      xaxis_title=f"{Xaxis}",
      yaxis_title=f"{Yaxis}",
      template="plotly_white",
      xaxis=dict(side="bottom"),
      yaxis=dict(autorange="reversed"),
      margin=dict(l=40, r=40, t=60, b=40),
  )

  return fig

def plotly_table_count_percent(df: pd.DataFrame, column_list: list,optional_exclude_blank: bool = True,optional_include_total: bool = True):
  table_rows = []

  for col in column_list:
    if col not in df.columns:
      continue

    data = df[col].copy()

    # 1. Clean string data safely: remove Windows \r and normalize types
    if (
        data.dtype == "object"
        or isinstance(data.dtype, pd.CategoricalDtype)
        or pd.api.types.is_string_dtype(data)
    ):
      data = (
          data.astype("string")
          .str.replace("\r", "", regex=False)
          .str.strip()
          .replace(["nan", "None", "NaN", "<NA>", ""], pd.NA)
      )

    # Handle blank/null values based on flag
    if optional_exclude_blank:
      data = data.dropna()
    else:
      data = data.fillna("Blank")

    if data.empty:
      total_count = 0
      counts_df = pd.DataFrame(columns=["Category", "Count"])
    else:
      # Compute value counts
      counts = data.value_counts(dropna=False).reset_index()
      counts.columns = ["Category", "Count"]

      # --- CROSS-PLATFORM STABILITY FIX ---
      # Sort deterministically: 1st by Count descending, 2nd by Category name ascending (prevents tie-sorting discrepancies)
      counts = counts.sort_values(
          by=["Count", "Category"], ascending=[False, True]
      ).reset_index(drop=True)

      total_count = counts["Count"].sum()
      counts["Percent"] = (
          (counts["Count"] / total_count * 100) if total_count > 0 else 0.0
      )

    # 2. Add Total row FIRST if requested
    if optional_include_total:
      table_rows.append({
          "Column Name": col,
          "Category": "Total",
          "Count": total_count,
          "Percent": 100.0 if total_count > 0 else 0.0,
          "Is_Total": True,
      })

    # 3. Add individual Category rows
    for i, row in counts.iterrows():
      col_display = col if (i == 0 and not optional_include_total) else ""

      table_rows.append({
          "Column Name": col_display,
          "Category": str(row["Category"]),
          "Count": row["Count"],
          "Percent": row["Percent"],
          "Is_Total": False,
      })

  if not table_rows:
    raise ValueError(
        "No valid data available to render after processing columns."
    )

  result_df = pd.DataFrame(table_rows)

  # Formatted cell content
  formatted_col_name = [
      f"<b>{r['Column Name']}</b>" for _, r in result_df.iterrows()
  ]
  formatted_category = [
      f"<b>{r['Category']}</b>" if r["Is_Total"] else r["Category"]
      for _, r in result_df.iterrows()
  ]
  formatted_counts = [
      f"<b>{r['Count']:,}</b>" if r["Is_Total"] else f"{r['Count']:,}"
      for _, r in result_df.iterrows()
  ]
  formatted_percent = [
      f"<b>{r['Percent']:.1f}%</b>" if r["Is_Total"] else f"{r['Percent']:.1f}%"
      for _, r in result_df.iterrows()
  ]

  # Background color formatting
  fill_colors = []
  for _, r in result_df.iterrows():
    if r["Is_Total"]:
      fill_colors.append(
          "#E1EBF5"
      )  # Light blue/gray highlight for Total header row
    else:
      fill_colors.append("#FFFFFF")  # White background for category rows

  # Dynamic height calculation to ensure all rows display properly
  n_rows = len(result_df)
  header_height = 30
  row_height = 26
  total_height = 60 + header_height + (n_rows * row_height)

  # Create Plotly Table
  fig = go.Figure(
      data=[
          go.Table(
              header=dict(
                  values=[
                      "<b>Column Name</b>",
                      "<b>Category</b>",
                      "<b>Count</b>",
                      "<b>Percent</b>",
                  ],
                  fill_color="#1F77B4",
                  font=dict(color="white", size=13),
                  align=["left", "left", "right", "right"],
                  height=header_height,
              ),
              cells=dict(
                  values=[
                      formatted_col_name,
                      formatted_category,
                      formatted_counts,
                      formatted_percent,
                  ],
                  fill_color=[fill_colors] * 4,
                  font=dict(color="black", size=12),
                  align=["left", "left", "right", "right"],
                  height=row_height,
              ),
          )
      ]
  )

  fig.update_layout(
      title="Summary Table: Count and Percent Breakdown",
      margin=dict(l=20, r=20, t=50, b=20),
      height=total_height,
  )
  return fig

def plotly_funnel(df: pd.DataFrame,funnel_column_criteria: dict,rename_column: list,column_funnel: str,):
  df = df.copy()
  # 1. Clean grouping and criteria columns upfront to remove Windows \r and whitespace issues
  all_cols_to_clean = list(funnel_column_criteria.keys()) + [column_funnel]
  for col in all_cols_to_clean:
    if col in df.columns:
      if (
          df[col].dtype == "object"
          or isinstance(df[col].dtype, pd.CategoricalDtype)
          or pd.api.types.is_string_dtype(df[col])
      ):
        df[col] = (
            df[col]
            .astype("string")
            .str.replace("\r", "", regex=False)
            .str.strip()
            .replace(["nan", "None", "NaN", "<NA>"], pd.NA)
        )

  raw_step_names = list(funnel_column_criteria.keys())

  # Validation check for rename_column list length
  if len(rename_column) != len(raw_step_names):
    raise ValueError(
        f"Length of `rename_column` ({len(rename_column)}) must match "
        f"the number of stages in `funnel_column_criteria` ({len(raw_step_names)})."
    )

  # 2. Get unique segments and sort deterministically across platforms
  segments = df[column_funnel].dropna().unique().tolist()
  if not segments:
    raise ValueError(
        f"No unique values found in group column: '{column_funnel}'"
    )

  # Convert segments to string and sort alphabetically for cross-platform consistency
  segments = sorted([str(seg) for seg in segments])
  num_segments = len(segments)

  # 3. Setup subplot grid (1 row, N columns)
  fig = make_subplots(
      rows=1,
      cols=num_segments,
      subplot_titles=[f"<b>{seg}</b>" for seg in segments],
      shared_yaxes=True,
  )

  # 4. Iterate through segments and build individual funnels
  for i, seg in enumerate(segments, start=1):
    seg_df = df[df[column_funnel].astype(str) == seg]
    raw_counts = []

    # Calculate raw counts per funnel stage safely
    for col, criteria in funnel_column_criteria.items():
      criteria_list = criteria if isinstance(criteria, list) else [criteria]
      criteria_list_str = [str(c) for c in criteria_list]
      count = seg_df[col].astype(str).isin(criteria_list_str).sum()
      raw_counts.append(count)

    # Log10 transformation for bar widths (log10(count + 1) prevents log(0))
    log_counts = np.log10(np.array(raw_counts) + 1).tolist()

    # Calculate percentages based on raw counts
    initial_count = (
        raw_counts[0] if len(raw_counts) > 0 and raw_counts[0] > 0 else 1
    )
    pct_initial = [(cnt / initial_count) * 100 for cnt in raw_counts]

    # Format display text and hover text using custom labels from rename_column
    display_text = [
        f"{cnt:,} ({pct:.1f}%)" for cnt, pct in zip(raw_counts, pct_initial)
    ]
    hover_text = [
        (
            f"<b>Stage:</b> {label}<br><b>Raw Count:</b>"
            f" {cnt:,}<br><b>% of Initial:</b> {pct:.1f}%"
        )
        for label, cnt, pct in zip(rename_column, raw_counts, pct_initial)
    ]

    # Add funnel trace to subplot using renamed stages for y-axis
    fig.add_trace(
        go.Funnel(
            name=str(seg),
            y=rename_column,  # Custom labels shown on y-axis
            x=log_counts,  # Log values control bar widths
            text=display_text,  # Actual counts & percentages on bars
            textinfo="text",
            hoverinfo="text",
            hovertext=hover_text,
        ),
        row=1,
        col=i,
    )

  # 5. Layout configuration
  fig.update_layout(
      title=dict(
          text=f"<b>Cascade of Care Analysis by {column_funnel}</b>",
          font=dict(size=18),
          x=0.5,
          y=0.95,
          xanchor="center",
      ),
      showlegend=False,
      template="plotly_white",
      margin=dict(l=40, r=40, t=80, b=40),
  )

  # Hide log tick values along the bottom x-axes
  fig.update_xaxes(showticklabels=False, title_text="")

  return fig

def plot_clinic_sankey(df: pd.DataFrame) -> go.Figure:
  data = df.copy()
  # 1. Clean all relevant columns upfront to remove Windows \r and trailing whitespaces
  cols_to_clean = [
      "TypeofPatient1",
      "HT1",
      "DM1",
      "RTIAVI1",
      "Generalweakness1",
      "Other1",
  ]
  for col in cols_to_clean:
    if col in data.columns:
      data[col] = (data[col].astype("string").str.replace("\r", "", regex=False).str.strip().replace(["nan", "None", "NaN", "<NA>"], pd.NA))

  # 2. Categorize Attendant Type
  def get_attendant_type(val):
    if pd.isna(val):
      return "Unspecified Attendant"
    val_str = str(val).strip().lower()
    if val_str in ["new", "yes", "1", "1.0"]:
      return "New Attendant"
    elif val_str in ["old", "no", "2", "2.0"]:
      return "Old Attendant"
    return "Unspecified Attendant"

  data["Attendant_Category"] = data["TypeofPatient1"].apply(get_attendant_type)

  # 3. Categorize Consultation Types
  valid_ht_dm = {"1", "3", "yes", "1.0", "3.0"}
  valid_binary = {"1", "yes", "1.0"}

  def parse_flag(val, valid_set):
    if pd.isna(val):
      return False
    return str(val).strip().lower() in valid_set

  def get_consultation_categories(row):
    categories = []
    is_ht = parse_flag(row.get("HT1"), valid_ht_dm)
    is_dm = parse_flag(row.get("DM1"), valid_ht_dm)
    is_avi = parse_flag(row.get("RTIAVI1"), valid_binary)
    is_general_weakness = parse_flag(row.get("Generalweakness1"), valid_binary)
    is_others = parse_flag(row.get("Other1"), valid_binary)

    if is_ht and is_dm:
      categories.append("HT+DM")
    elif is_ht:
      categories.append("Hypertension")
    elif is_dm:
      categories.append("Diabetes")

    if is_avi:
      categories.append("AVI")
    if is_general_weakness:
      categories.append("General Weakness")
    if is_others:
      categories.append("Others")

    return categories if categories else ["Unspecified/Blank"]

  data["Consultation_List"] = data.apply(
      get_consultation_categories, axis=1
  )
  exploded = data.explode("Consultation_List")

  # 4. Aggregate Link Flows (Source -> Target) with deterministic sorting
  flow_counts = (
      exploded.groupby(["Attendant_Category", "Consultation_List"], dropna=False)
      .size()
      .reset_index(name="Patient_Count")
  )

  # Enforce deterministic row order across Mac and Windows
  flow_counts = flow_counts.sort_values(
      by=["Attendant_Category", "Consultation_List"]
  ).reset_index(drop=True)

  # 5. Map Nodes to Index Positions deterministically
  sources = flow_counts["Attendant_Category"].tolist()
  targets = flow_counts["Consultation_List"].tolist()

  # Sort unique sources and targets separately or use a consistent sorted set order
  unique_sources = sorted(list(set(sources)))
  unique_targets = sorted(list(set(targets)))
  unique_nodes = unique_sources + unique_targets

  node_indices = {name: i for i, name in enumerate(unique_nodes)}

  source_idx = [node_indices[s] for s in sources]
  target_idx = [node_indices[t] for t in targets]
  values = flow_counts["Patient_Count"].tolist()

  # 6. Define Custom Node Colors
  color_palette = {
      "New Attendant": "#2b5c8f",
      "Old Attendant": "#d95f02",
      "Unspecified Attendant": "#8c8c8c",
      "Hypertension": "#1f77b4",
      "Diabetes": "#ff7f0e",
      "HT+DM": "#d62728",
      "AVI": "#9467bd",
      "Others": "#2ca02c",
      "Unspecified/Blank": "#7f7f7f",
  }
  node_colors = [
      color_palette.get(node, "#333333") for node in unique_nodes
  ]

  # 7. Construct Sankey Figure
  fig = go.Figure(
      data=[
          go.Sankey(
              node=dict(
                  pad=20,
                  thickness=20,
                  line=dict(color="black", width=0.5),
                  label=unique_nodes,
                  color=node_colors,
              ),
              link=dict(
                  source=source_idx,
                  target=target_idx,
                  value=values,
                  color="rgba(180, 180, 180, 0.3)",
              ),
          )
      ]
  )

  fig.update_layout(
      title_text="<b>Patient Flow: Clinic Attendant Type → Consultation Category</b>",
      font_size=12,
      template="plotly_white",
      margin=dict(t=60, b=40, l=40, r=40),
  )

  return fig

def plot_phc_category_bubble(df, category_col="PHC Category", sep=", ", base_size=8, log_factor=10):

  data = df.copy()

  if category_col not in data.columns:
    raise KeyError(f"Column '{category_col}' not found in dataframe.")

  # 1. Clean string data upfront to remove Windows \r and normalize null tokens
  data[category_col] = (
      data[category_col]
      .astype("string")
      .str.replace("\r", "", regex=False)
      .str.strip()
      .replace(["nan", "None", "NaN", "<NA>", ""], pd.NA)
  )

  valid_data = data[category_col].dropna()
  if valid_data.empty:
    raise ValueError(
        f"No valid data available in column '{category_col}' after filtering."
    )

  # 2. Clean individual delimited items to strip stray whitespaces around the separator
  def clean_delimited_string(val):
    if pd.isna(val):
      return val
    parts = [p.strip() for p in str(val).split(sep) if p.strip()]
    return sep.join(parts)

  valid_data_cleaned = valid_data.apply(clean_delimited_string)

  # Generate dummy matrix
  dummies = valid_data_cleaned.str.get_dummies(sep=sep)

  if dummies.empty:
    raise ValueError(
        "Dummies matrix is empty. Check category column values and separator."
    )

  # Enforce deterministic alphabetical sorting of categories across platforms
  categories = sorted(dummies.columns)
  dummies = dummies[categories]

  co_matrix = dummies.T.dot(dummies)

  # 3. Reshape co-occurrence matrix to long format
  co_df = co_matrix.reset_index().melt(id_vars="index")
  co_df.columns = ["Cat_X", "Cat_Y", "Count"]
  co_df = co_df[co_df["Count"] > 0].copy()

  # Enforce deterministic sort order for scatter points across operating systems
  co_df = co_df.sort_values(by=["Cat_X", "Cat_Y"]).reset_index(drop=True)

  # 4. Apply logarithmic scaling to size smaller bubbles (np.log1p prevents log(0) errors)
  co_df["log_size"] = base_size + (np.log1p(co_df["Count"]) * log_factor)

  # 5. Extract total count per category for the top X-axis
  totals = [co_matrix.loc[cat, cat] for cat in categories]
  top_totals_text = [str(t) for t in totals]

  # 6. Create Scatter Plot
  fig = go.Figure()

  fig.add_trace(
      go.Scatter(
          x=co_df["Cat_X"],
          y=co_df["Cat_Y"],
          mode="markers+text",
          text=co_df["Count"].astype(str),
          textposition="middle center",
          textfont=dict(color="black", size=8, family="Arial Bold"),
          marker=dict(
              size=co_df["log_size"],
              color=co_df["Count"],
              colorscale="YlGnBu",
              showscale=True,
              colorbar=dict(title="Intersection Count"),
              line=dict(width=1, color="DarkBlue"),
          ),
          hovertemplate=(
              "<b>X Category:</b> %{x}<br><b>Y Category:</b>"
              " %{y}<br><b>Intersection Count:</b> %{text}<extra></extra>"
          ),
      )
  )

  # 7. Configure Layout with Secondary Top X-Axis for Total Counts
  fig.update_layout(
      title=dict(
          text="<b>PHC Category Intersection Matrix</b>",
          x=0.5,
          y=0.98,
          font=dict(size=16),
      ),
      xaxis=dict(
          title="PHC Category",
          tickangle=-45,
          categoryorder="array",
          categoryarray=categories,
      ),
      xaxis2=dict(
          title=dict(text="Total Category Count", font=dict(size=12, color="black")),
          overlaying="x",
          side="top",
          tickmode="array",
          tickvals=categories,
          ticktext=top_totals_text,
          tickangle=0,
          tickfont=dict(size=11, color="black", family="Arial Bold"),
      ),
      yaxis=dict(
          title="PHC Category", categoryorder="array", categoryarray=categories
      ),
      width=850,
      height=750,
      template="plotly_white",
      margin=dict(t=120, b=80, l=90, r=80),
  )

  return fig

def plotly_waterfall(df: pd.DataFrame,start_dict: dict,subtract_dict1: dict,add_dict: dict,subtract_dict2: dict,chart_title: str = "Waterfall Analysis"):
  data = df.copy()

  # 1. Collect all unique columns referenced in all criteria dictionaries
  all_dicts = [start_dict, subtract_dict1, add_dict, subtract_dict2]
  all_cols_to_clean = set()
  for d in all_dicts:
    if d:
      for col in d.keys():
        all_cols_to_clean.add(col)

  # 2. Clean all referenced columns upfront to remove Windows \r and normalize null tokens
  for col in all_cols_to_clean:
    if col in data.columns:
      if (
          data[col].dtype == "object"
          or isinstance(data[col].dtype, pd.CategoricalDtype)
          or pd.api.types.is_string_dtype(data[col])
      ):
        data[col] = (
            data[col]
            .astype("string")
            .str.replace("\r", "", regex=False)
            .str.strip()
            .replace(["nan", "None", "NaN", "<NA>", ""], pd.NA)
        )

  def apply_filter(data_subset, criteria_dict):
    mask = pd.Series(True, index=data_subset.index)
    for col, values in criteria_dict.items():
      if col in data_subset.columns:
        val_list = values if isinstance(values, list) else [values]
        # Convert filter values to string representation for robust matching
        val_list_str = [str(v) for v in val_list]
        mask &= data_subset[col].astype(str).isin(val_list_str)
    return mask

  x_labels = []
  y_values = []
  measure_types = []
  text_labels = []
  raw_counts = []

  running_total = 0

  # Helper function to append steps
  def process_dict(dict_obj, is_subtraction=False):
    nonlocal running_total
    if not dict_obj:
      return
    for step_label, criteria in dict_obj.items():
      count = apply_filter(data, criteria).sum()
      if is_subtraction:
        running_total -= count
        val = -count
        txt = f"-{count:,}"
      else:
        running_total += count
        val = count
        txt = f"+{count:,}"

      x_labels.append(step_label)
      y_values.append(val)
      measure_types.append("relative")
      text_labels.append(txt)
      raw_counts.append(count)

  # 3. Process Dictionaries sequentially
  process_dict(start_dict, is_subtraction=False)
  process_dict(subtract_dict1, is_subtraction=True)
  process_dict(add_dict, is_subtraction=False)
  process_dict(subtract_dict2, is_subtraction=True)

  # 4. Process Final Total
  x_labels.append("Remaining")
  y_values.append(0)  # Plotly auto-calculates span for total
  measure_types.append("total")
  text_labels.append(f"{running_total:,}")
  raw_counts.append(running_total)

  # 5. Best Fit Logic for Text Positioning
  max_val = max([abs(c) for c in raw_counts] + [1])

  text_positions = []
  text_colors = []

  for count in raw_counts:
    if (abs(count) / max_val) >= 0.15:
      text_positions.append("inside")
      text_colors.append("white")
    else:
      text_positions.append("outside")
      text_colors.append("black")

  # 6. Build Waterfall Chart
  fig = go.Figure(
      go.Waterfall(
          orientation="v",
          measure=measure_types,
          x=x_labels,
          y=y_values,
          text=text_labels,
          textposition=text_positions,
          textfont=dict(color=text_colors, size=11),
          connector={"line": {"color": "rgb(63, 63, 63)", "width": 1.5}},
          decreasing={"marker": {"color": "#EF553B"}},
          increasing={"marker": {"color": "#00CC96"}},
          totals={"marker": {"color": "#2B5C8F"}},
      )
  )

  # 7. Layout Adjustments
  fig.update_layout(
      title=dict(
          text=f"<b>{chart_title}</b>",
          font=dict(size=18),
          x=0.5,
          xanchor="center",
      ),
      showlegend=False,
      template="plotly_white",
      yaxis_title="Count",
      margin=dict(t=80, b=50, l=50, r=50),
  )

  return fig

def plotly_scatter_bubble(df: pd.DataFrame,x_col: str,yaxis: str,chartTitle: str,exclude_blank: bool = True,):
  df_clean = df.copy()
  # 1. Clean and normalize target string columns using nullable string dtype
  for col in [x_col, yaxis]:
    if col in df_clean.columns:
      df_clean[col] = (
          df_clean[col]
          .astype("string")
          .str.replace("\r", "", regex=False)
          .str.strip()
          .replace(
              {
                  "": pd.NA,
                  "None": pd.NA,
                  "nan": pd.NA,
                  "NaN": pd.NA,
                  "<NA>": pd.NA,
              }
          )
      )

  # 2. Filter blanks if required
  if exclude_blank:
    df_clean = df_clean.dropna(subset=[x_col, yaxis])

  if df_clean.empty:
    raise ValueError("No valid data remaining after filtering.")

  # 3. Split comma-separated values and explode into individual category rows
  df_clean["X_Category"] = df_clean[x_col].str.split(",")
  exploded = df_clean.explode("X_Category")
  exploded["X_Category"] = exploded["X_Category"].str.strip()

  # Drop any blank categories produced after splitting
  exploded = exploded[
      ~exploded["X_Category"].isin(["", "nan", "NaN", "None", "<NA>"])
      & exploded["X_Category"].notna()
  ]

  if exploded.empty:
    raise ValueError(
        "No valid categories found after exploding delimited column."
    )

  # 4. Group and aggregate counts deterministically
  grouped = (
      exploded.groupby(["X_Category", yaxis], dropna=False)
      .size()
      .reset_index(name="Count")
  )

  # Enforce deterministic sorting across platforms
  grouped = grouped.sort_values(by=["X_Category", yaxis]).reset_index(drop=True)

  # 5. Calculate total counts and create concatenated X-axis tick labels
  total_counts = (
      grouped.groupby("X_Category")["Count"]
      .sum()
      .reset_index(name="TotalCount")
  )

  # Sort categories alphabetically for consistent cross-platform sequencing
  total_counts = total_counts.sort_values(by="X_Category").reset_index(
      drop=True
  )

  total_counts["X_Label"] = total_counts.apply(
      lambda r: f"{r['X_Category']} ({r['TotalCount']})", axis=1
  )

  # Map formatted label back to the grouped dataframe
  category_label_map = dict(
      zip(total_counts["X_Category"], total_counts["X_Label"])
  )
  grouped["X_Label"] = grouped["X_Category"].map(category_label_map)

  # Extract sorted label arrays for explicit Plotly category ordering
  sorted_x_labels = total_counts["X_Label"].tolist()
  sorted_y_labels = sorted(grouped[yaxis].dropna().unique().astype(str).tolist())

  # 6. Apply log transformation for bubble sizing
  grouped["LogCount"] = np.log1p(grouped["Count"])

  # 7. Create Bubble Plot using LogCount for size and raw Count for text inside bubbles
  fig = px.scatter(
      grouped,
      x="X_Label",
      y=yaxis,
      size="LogCount",
      color=yaxis,
      size_max=45,
      text="Count",
      title=chartTitle,
      labels={"X_Label": "Category", yaxis: yaxis},
      hover_data={"LogCount": False, "Count": True, "X_Label": True},
  )

  # Display raw count inside bubbles
  fig.update_traces(
      textposition="middle center", textfont=dict(color="white", size=11)
  )

  # 8. Layout adjustments with explicit category ordering
  fig.update_layout(
      title_x=0.5,
      margin=dict(t=80, b=80, l=80, r=80),
      xaxis=dict(
          title="Primary Healthcare Category",
          type="category",
          categoryorder="array",
          categoryarray=sorted_x_labels,
          tickangle=-45,
      ),
      yaxis=dict(
          type="category",
          categoryorder="array",
          categoryarray=sorted_y_labels,
      ),
      showlegend=False,
      template="plotly_white",
  )

  return fig

def plot_nested_donut_chart(df: pd.DataFrame,column_name: str = "PrimaryHealthcare",chart_title: str = "Primary Healthcare Category & Overlap Breakdown"):
  df_clean = df.copy()

  if column_name not in df_clean.columns:
    raise KeyError(f"Column '{column_name}' not found in dataframe.")

  # 1. Clean missing/null values and Windows carriage returns upfront
  df_clean[column_name] = (
      df_clean[column_name]
      .astype("string")
      .str.replace("\r", "", regex=False)
      .str.strip()
      .replace(
          {
              "": pd.NA,
              "None": pd.NA,
              "nan": pd.NA,
              "NaN": pd.NA,
              "<NA>": pd.NA,
          }
      )
  )
  df_clean = df_clean.dropna(subset=[column_name])

  if df_clean.empty:
    raise ValueError(f"No valid data remaining in column '{column_name}'.")

  # 2. Preserve full profile (e.g., "DM, HT")
  df_clean["Full_Profile"] = df_clean[column_name]

  # 3. Split comma-separated string and explode into individual primary categories
  df_clean["Primary_Category"] = df_clean[column_name].str.split(",")
  exploded = df_clean.explode("Primary_Category")

  # Clean whitespace around individual categories
  exploded["Primary_Category"] = exploded["Primary_Category"].str.strip()

  # Drop any blank entries produced after splitting
  exploded = exploded[
      ~exploded["Primary_Category"].isin(["", "nan", "NaN", "None", "<NA>"])
      & exploded["Primary_Category"].notna()
  ]

  if exploded.empty:
    raise ValueError(
        "No valid categories found after exploding delimited column."
    )

  # 4. Aggregate counts across all primary categories and full profiles deterministically
  grouped = (
      exploded.groupby(["Primary_Category", "Full_Profile"], dropna=False)
      .size()
      .reset_index(name="Count")
  )

  # Enforce deterministic sort order to keep sector arrangements identical across OSes
  grouped = grouped.sort_values(
      by=["Primary_Category", "Full_Profile"]
  ).reset_index(drop=True)

  # 5. Create Sunburst (Nested Donut)
  fig = px.sunburst(
      grouped,
      path=["Primary_Category", "Full_Profile"],
      values="Count",
      title=chart_title,
      color="Primary_Category",
      color_discrete_sequence=px.colors.qualitative.Pastel,
  )

  fig.update_traces(
      textinfo="label+value+percent entry",
      insidetextorientation="horizontal",
  )

  fig.update_layout(
      title_x=0.5,
      margin=dict(t=80, l=40, r=40, b=40),
      template="plotly_white",
  )

  return fig

def plot_scatter_sunburst(df: pd.DataFrame,x_col: str = "PrimaryHealthcare", yaxis: str = "Disease",main_title: str = "Primary Healthcare Dashboard",exclude_blank: bool = True) -> go.Figure:
  df_clean = df.copy()

  cols_to_clean = list({x_col, yaxis})
  for col in cols_to_clean:
    if col in df_clean.columns:
      df_clean[col] = (
          df_clean[col]
          .astype("string")
          .str.replace("\r", "", regex=False)
          .str.strip()
          .replace(
              {
                  "": pd.NA,
                  "None": pd.NA,
                  "nan": pd.NA,
                  "NaN": pd.NA,
                  "<NA>": pd.NA,
              }
          )
      )

  if exclude_blank:
    df_clean = df_clean.dropna(subset=cols_to_clean)

  if df_clean.empty:
    raise ValueError("No valid data remaining after filtering.")

  # Preserve full profile string before exploding
  df_clean["Full_Profile"] = df_clean[x_col]

  # --- 1. Explode Comma-Separated Categories ---
  df_clean["X_Category"] = df_clean[x_col].str.split(",")
  exploded = df_clean.explode("X_Category")
  exploded["X_Category"] = exploded["X_Category"].str.strip()
  exploded = exploded[
      ~exploded["X_Category"].isin(["", "nan", "NaN", "None", "<NA>"])
      & exploded["X_Category"].notna()
  ]

  if exploded.empty:
    raise ValueError(
        "No valid categories found after exploding delimited column."
    )

  # --- 2. Process Data for Scatter Bubble Plot ---
  grouped_scatter = (
      exploded.groupby(["X_Category", yaxis], dropna=False)
      .size()
      .reset_index(name="Count")
  )
  grouped_scatter = grouped_scatter.sort_values(
      by=["X_Category", yaxis]
  ).reset_index(drop=True)

  total_counts = (
      grouped_scatter.groupby("X_Category")["Count"]
      .sum()
      .reset_index(name="TotalCount")
  )
  total_counts = total_counts.sort_values(by="X_Category").reset_index(
      drop=True
  )

  total_counts["X_Label"] = total_counts.apply(
      lambda r: f"{r['X_Category']} ({r['TotalCount']})", axis=1
  )

  category_label_map = dict(
      zip(total_counts["X_Category"], total_counts["X_Label"])
  )
  grouped_scatter["X_Label"] = grouped_scatter["X_Category"].map(
      category_label_map
  )
  grouped_scatter["LogCount"] = np.log1p(grouped_scatter["Count"])

  max_log = grouped_scatter["LogCount"].max()
  grouped_scatter["MarkerSize"] = (
      grouped_scatter["LogCount"] / (max_log if max_log else 1)
  ) * 45

  # Extract sorted unique labels for explicit cross-platform category ordering
  sorted_x_labels = total_counts["X_Label"].tolist()
  sorted_y_labels = sorted(
      grouped_scatter[yaxis].dropna().unique().astype(str).tolist()
  )

  # --- 3. Process Data for Sunburst Chart ---
  grouped_sunburst = (
      exploded.groupby(["X_Category", "Full_Profile"], dropna=False)
      .size()
      .reset_index(name="Count")
  )
  grouped_sunburst = grouped_sunburst.sort_values(
      by=["X_Category", "Full_Profile"]
  ).reset_index(drop=True)

  # --- 4. Build Subplot Canvas ---
  fig = make_subplots(
      rows=1,
      cols=2,
      subplot_titles=(
          f"PHC Consultation by Examined {yaxis}",
          f"Category & Comorbidity Among Examined {yaxis}",
      ),
      specs=[[{"type": "xy"}, {"type": "sunburst"}]],
      horizontal_spacing=0.12,
  )

  # Add Bubble Scatter Traces (grouped by category for distinct colors)
  categories = sorted_y_labels
  colors = px.colors.qualitative.Plotly

  for idx, cat in enumerate(categories):
    sub_df = grouped_scatter[grouped_scatter[yaxis] == cat]
    if sub_df.empty:
      continue
    fig.add_trace(
        go.Scatter(
            x=sub_df["X_Label"],
            y=sub_df[yaxis],
            mode="markers+text",
            marker=dict(
                size=sub_df["MarkerSize"],
                color=colors[idx % len(colors)],
            ),
            text=sub_df["Count"],
            textposition="middle center",
            textfont=dict(color="white", size=11),
            name=str(cat),
            hovertemplate=(
                "Category: %{x}<br>Y-Axis: %{y}<br>Count:"
                " %{text}<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )

  # Temporary figure generated via Plotly Express to extract Sunburst structure
  temp_sunburst = px.sunburst(
      grouped_sunburst,
      path=["X_Category", "Full_Profile"],
      values="Count",
      color="X_Category",
      color_discrete_sequence=px.colors.qualitative.Pastel,
  )

  for trace in temp_sunburst.data:
    trace.update(
        textinfo="label+value+percent entry",
        insidetextorientation="horizontal",
    )
    fig.add_trace(trace, row=1, col=2)

  # --- 5. Adjust Layout & Formatting ---
  fig.update_layout(
      title=dict(
          text=f"<b>{main_title}</b>",
          font=dict(size=18),
          x=0.5,
          xanchor="center",
      ),
      margin=dict(t=90, l=50, r=50, b=60),
      xaxis=dict(
          title="Primary Healthcare Category",
          type="category",
          categoryorder="array",
          categoryarray=sorted_x_labels,
          tickangle=-45,
      ),
      yaxis=dict(
          title=yaxis,
          type="category",
          categoryorder="array",
          categoryarray=sorted_y_labels,
      ),
      showlegend=False,
      template="plotly_white",
  )

  return fig

def plotly_combo_bar_percent(df: pd.DataFrame,xaxis_str: str,bar_dict: dict,optional_percent_line_list: list = None):
  df_clean = df.copy()

  if xaxis_str not in df_clean.columns:
    raise KeyError(f"X-axis column '{xaxis_str}' not found in dataframe.")

  # 1. Collect all columns to clean (X-axis + all bar keys + optional percent line columns)
  cols_to_clean = {xaxis_str}
  for col_name in bar_dict.keys():
    cols_to_clean.add(col_name)
  if optional_percent_line_list and len(optional_percent_line_list) == 2:
    cols_to_clean.add(optional_percent_line_list[0])
    cols_to_clean.add(optional_percent_line_list[1])

  # 2. Normalize string columns upfront to eliminate Windows \r and inconsistent null tokens
  for col in cols_to_clean:
    if col in df_clean.columns:
      df_clean[col] = (
          df_clean[col]
          .astype("string")
          .str.replace("\r", "", regex=False)
          .str.strip()
          .replace(
              {
                  "": pd.NA,
                  "None": pd.NA,
                  "nan": pd.NA,
                  "NaN": pd.NA,
                  "<NA>": pd.NA,
              }
          )
      )

  # Drop rows where the X-axis category is null
  df_clean = df_clean.dropna(subset=[xaxis_str])
  if df_clean.empty:
    raise ValueError(
        f"No valid data remaining in X-axis column '{xaxis_str}'."
    )

  # Determine unique X-axis categories deterministically (sorted as strings)
  x_categories = sorted(df_clean[xaxis_str].unique().astype(str).tolist())
  aggregated_counts = {}

  # 3. Filter DataFrame separately per bar criteria & aggregate counts
  for col_name, criteria in bar_dict.items():
    if col_name not in df_clean.columns:
      raise KeyError(f"Bar criteria column '{col_name}' not found in dataframe.")

    if isinstance(criteria, (list, tuple, set)):
      crit_list = [str(c) for c in criteria]
      filtered_df = df_clean[df_clean[col_name].astype(str).isin(crit_list)]
    else:
      filtered_df = df_clean[
          df_clean[col_name].astype(str) == str(criteria)
      ]

    counts = filtered_df.groupby(xaxis_str).size()
    aggregated_counts[col_name] = counts.reindex(x_categories, fill_value=0)

  # 4. Setup Figure Layout
  has_secondary = bool(
      optional_percent_line_list and len(optional_percent_line_list) == 2
  )

  if has_secondary:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
  else:
    fig = go.Figure()

  # 5. Add Bar Traces (Transformed Y-values via log1p)
  max_count = 0
  for col_name, counts_series in aggregated_counts.items():
    raw_vals = counts_series.values
    max_count = max(max_count, np.max(raw_vals) if len(raw_vals) > 0 else 0)

    # Apply log1p transformation to Y values so 0 stays at 0
    transformed_y = np.log1p(raw_vals)

    criteria_display = bar_dict[col_name]
    if isinstance(criteria_display, (list, tuple, set)):
      crit_str = ", ".join(map(str, criteria_display))
    else:
      crit_str = str(criteria_display)

    trace = go.Bar(
        x=x_categories,
        y=transformed_y,
        name=f"{col_name} ({crit_str})",
        text=raw_vals,  # Display true raw count as text label on bars
        textposition="inside",
        hovertemplate="<b>%{x}</b><br>Count: %{text}<extra></extra>",
    )

    if has_secondary:
      fig.add_trace(trace, secondary_y=False)
    else:
      fig.add_trace(trace)

  # 6. Compute & Add Optional Percentage Line on Secondary Y-axis
  pct_max = 100
  if has_secondary:
    num_col, den_col = optional_percent_line_list

    if num_col in aggregated_counts and den_col in aggregated_counts:
      num_series = aggregated_counts[num_col]
      den_series = aggregated_counts[den_col]

      pct_series = np.where(den_series > 0, (num_series / den_series) * 100, 0)
      pct_max = (
          max(np.max(pct_series), 1) if len(pct_series) > 0 else 100
      )

      fig.add_trace(
          go.Scatter(
              x=x_categories,
              y=pct_series,
              name=f"% ({num_col} / {den_col})",
              mode="lines+markers+text",
              text=[f"<b>{p:.1f}%</b>" for p in pct_series],
              textposition="top center",
              textfont=dict(size=12, color="#0C0C0C"),
              line=dict(
                  dash="4px 4px", width=0.5, color="#0C0C0C"
              ),  # Custom sharp 4px dots
              marker=dict(
                  size=8,
                  color="#0C0C0C",
                  symbol="diamond",  # Unique marker shape
                  line=dict(width=1.5, color="black"),
              ),
          ),
          secondary_y=True,
      )

  # 7. Build Tick Array for 0, 10, 100, 1000, etc.
  max_power = (
      int(np.ceil(np.log10(max_count))) if max_count > 0 else 1
  )  # Power of 10 bounds
  raw_ticks = [0] + [10**i for i in range(1, max_power + 1)]
  transformed_ticks = [np.log1p(t) for t in raw_ticks]
  tick_labels = [str(t) for t in raw_ticks]

  # 8. Apply Layout Configurations with Explicit Category Ordering
  layout_args = dict(
      title=dict(
          text=f"<b>Number of Cases & Percentage by {xaxis_str}</b>",
          font=dict(size=18),
          x=0.5,
          xanchor="center",
      ),
      xaxis=dict(
          title=xaxis_str,
          type="category",
          categoryorder="array",
          categoryarray=x_categories,
          tickangle=-45,
      ),
      yaxis=dict(
          title="# of Cases",
          type="linear",  # Linear type mapping transformed log values
          tickmode="array",
          tickvals=transformed_ticks,
          ticktext=tick_labels,
          range=[0, np.log1p(max_count * 1.25) if max_count > 0 else 1],
      ),
      barmode="group",
      template="plotly_white",
      legend=dict(
          orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5
      ),
      margin=dict(t=100, b=80, l=60, r=60),
  )

  if has_secondary:
    fig.update_layout(**layout_args)
    fig.update_yaxes(
        title_text="Percentage (%)",
        secondary_y=True,
        range=[0, max(10, pct_max * 1.15)],
        showgrid=False,
        ticksuffix="%",
    )
  else:
    fig.update_layout(**layout_args)

  return fig

def plotly_table_pivot(dataframe: pd.DataFrame,row: list,count_unique: str,count_or_sum_all: str,agg_type: str = "count",optional_percent: bool = False,title: str = "Pivot Summary Table") -> go.Figure:
  df = dataframe.copy()
  # 1. Clean string grouping columns to handle Windows \r, trailing spaces, and blanks safely
  for col in row:
    if col in df.columns:
      df[col] = (
          df[col]
          .fillna("")
          .astype(str)
          .str.strip()
          .replace(["nan", "None", "NAT", "NaT", "<NA>"], "")
      )

  unique_col_name = "# of Day"
  agg_col_name = "# of Consultation"
  pct_col_name = "Average Per Day"

  # --- 2. Aggregation Strategy ---
  agg_dict = {count_unique: pd.Series.nunique}
  if agg_type.lower() == "sum":
    agg_dict[count_or_sum_all] = "sum"
  else:
    agg_dict[count_or_sum_all] = "count"

  # Group by specified row hierarchy with dropna=False to keep blank/missing categories
  pivot_df = df.groupby(row, as_index=False, dropna=False).agg(agg_dict)
  pivot_df = pivot_df.rename(
      columns={
          count_unique: unique_col_name,
          count_or_sum_all: agg_col_name,
      }
  )

  # --- 3. Compute Total Row ---
  total_row = {}
  for i, col_name in enumerate(row):
    total_row[col_name] = "Total" if i == 0 else ""

  total_unique = df[count_unique].nunique()

  if agg_type.lower() == "sum":
    total_agg = df[count_or_sum_all].sum()
  else:
    total_agg = df[count_or_sum_all].count()

  total_row[unique_col_name] = total_unique
  total_row[agg_col_name] = total_agg

  # Append summary row to table dataframe
  total_df = pd.DataFrame([total_row])
  display_df = pd.concat([pivot_df, total_df], ignore_index=True)

  # --- 4. Calculate Row-Level Percentage ---
  if optional_percent:
    display_df[pct_col_name] = display_df[agg_col_name].div(
        display_df[unique_col_name].where(display_df[unique_col_name] != 0, 1)
    )

  # --- 5. Formatting Values for Display ---
  formatted_df = display_df.copy()

  formatted_df[unique_col_name] = formatted_df[unique_col_name].map(
      "{:,.0f}".format
  )
  formatted_df[agg_col_name] = formatted_df[agg_col_name].map(
      "{:,.0f}".format if agg_type == "count" else "{:,.2f}".format
  )

  if optional_percent:
    formatted_df[pct_col_name] = formatted_df[pct_col_name].map(
        lambda x: "{:.1f}".format(x) if pd.notna(x) else "0.0"
    )

  # Convert cells to string & add bold styling to the Total row
  n_rows = len(formatted_df)
  for col in formatted_df.columns:
    formatted_df[col] = [
        f"<b>{val}</b>" if idx == n_rows - 1 else str(val)
        for idx, val in enumerate(formatted_df[col])
    ]

  # --- 6. Styling: Banded Rows & Colors ---
  headers = list(formatted_df.columns)
  columns_data = [formatted_df[col].tolist() for col in headers]

  fill_colors = []
  for i in range(n_rows):
    if i == n_rows - 1:
      fill_colors.append("#E2E8F0")  # Highlight background for Total
    elif i % 2 == 0:
      fill_colors.append("#FFFFFF")  # White
    else:
      fill_colors.append("#F8FAFC")  # Light gray band

  cell_fill_matrix = [fill_colors] * len(headers)

  # --- 7. Dynamic Sizing Logic ---
  header_height = 36
  row_height = 28
  top_margin = 60
  bottom_margin = 30

  total_height = top_margin + bottom_margin + header_height + (n_rows * row_height)

  # --- 8. Constructing Plotly Table ---
  fig = go.Figure(
      data=[
          go.Table(
              header=dict(
                  values=[f"<b>{col}</b>" for col in headers],
                  fill_color="#1E293B",
                  align="center",
                  font=dict(color="white", size=12),
                  height=header_height,
              ),
              cells=dict(
                  values=columns_data,
                  fill_color=cell_fill_matrix,
                  align=["left"] * len(row)
                  + ["right"] * (len(headers) - len(row)),
                  font=dict(color="#0F172A", size=11),
                  height=row_height,
              ),
          )
      ]
  )

  fig.update_layout(
      title=dict(text=f"<b>{title}</b>", x=0.5, xanchor="center"),
      margin=dict(l=20, r=20, t=top_margin, b=bottom_margin),
      height=total_height,
      autosize=True,
  )

  return fig