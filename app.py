"""Tourism Data Analysis and Visualization - single-page Streamlit dashboard.

Historical layer : data/clean_tourism.csv (World Bank, 2010-2023)
Live layer       : tourism.db -> live_signals (written by fetcher.py)

Run:
    source .venv/bin/activate
    streamlit run app.py --server.port 8501
"""

from __future__ import annotations

import os
import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

CSV_PATH = "data/clean_tourism.csv"
DB_PATH = "tourism.db"
REFRESH_SECONDS = 60

CITY_POINTS = {
    "Paris": {"country": "France", "lat": 48.85, "lon": 2.35},
    "London": {"country": "United Kingdom", "lat": 51.50, "lon": -0.12},
    "New York": {"country": "United States", "lat": 40.71, "lon": -74.00},
    "Dubai": {"country": "United Arab Emirates", "lat": 25.20, "lon": 55.27},
    "Singapore": {"country": "Singapore", "lat": 1.35, "lon": 103.82},
    "Tokyo": {"country": "Japan", "lat": 35.68, "lon": 139.69},
}
CITY_ORDER = list(CITY_POINTS)
ALL_CITIES = "All cities"

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain", 71: "Light snow", 73: "Snow",
    75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
    82: "Violent showers", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Thunderstorm + hail",
}

st.set_page_config(
    page_title="Tourism Data Analysis and Visualization",
    page_icon="🌍",
    layout="wide",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def refresh_every(seconds: int):
    """st.autorefresh does not exist in Streamlit 1.x; use a timed fragment.

    Falls back to a normal function on versions without st.fragment.
    """
    def decorator(func):
        fragment = getattr(st, "fragment", None) or getattr(
            st, "experimental_fragment", None
        )
        return fragment(run_every=seconds)(func) if fragment else func
    return decorator


def humanize(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    value = float(value)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return f"{value / size:,.2f}{unit}"
    return f"{value:,.0f}"


@st.cache_data(show_spinner=False)
def load_historical(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for col in ("arrivals", "receipts", "receipts_per_arrival", "recovery_vs_2019"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_live() -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Latest row per city. Empty frame (and None) when no live data exists."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(), None
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5) as conn:
            frame = pd.read_sql_query("SELECT * FROM live_signals", conn)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame(), None

    if frame.empty:
        return frame, None

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"])
    if frame.empty:
        return frame, None

    latest = frame.sort_values("timestamp").groupby("city", as_index=False).tail(1)
    latest = latest.sort_values(
        "city", key=lambda s: s.map({c: i for i, c in enumerate(CITY_ORDER)})
    )
    return latest, frame["timestamp"].max()


def styled(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


# --------------------------------------------------------------------------- #
# Sidebar filters
# --------------------------------------------------------------------------- #
st.sidebar.header("Filters")
city_choice = st.sidebar.selectbox("City", [ALL_CITIES] + CITY_ORDER)
metric_label = st.sidebar.selectbox("Metric", ["Arrivals", "Receipts"])
metric = "arrivals" if metric_label == "Arrivals" else "receipts"

historical = load_historical(CSV_PATH)
years = sorted(int(y) for y in historical["year"].dropna().unique())
selected_year = st.sidebar.selectbox(
    "Year", years, index=len(years) - 1
)

focus_city = None if city_choice == ALL_CITIES else city_choice
focus_country = CITY_POINTS[focus_city]["country"] if focus_city else None
scope_label = focus_country if focus_country else "All countries"


# --------------------------------------------------------------------------- #
# Header (auto-refreshing last_updated)
# --------------------------------------------------------------------------- #
@refresh_every(REFRESH_SECONDS)
def render_last_updated() -> None:
    _, last_updated = load_live()
    if last_updated is None:
        st.caption("Live data: not yet fetched")
    else:
        st.caption(
            "Live data last updated: "
            f"{last_updated.tz_convert('UTC').strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"(refreshes every {REFRESH_SECONDS} s)"
        )


st.title("🌍 Tourism Data Analysis and Visualization")
st.markdown(
    "Historical international tourism **2010–2023** (World Bank) alongside "
    "**live travel signals** for six cities, collected every 2 minutes."
)
render_last_updated()


# --------------------------------------------------------------------------- #
# KPI cards
# --------------------------------------------------------------------------- #
def kpi_values() -> tuple[float, float, float]:
    year_df = historical[historical["year"] == selected_year]
    if focus_country:
        year_df = year_df[year_df["country"] == focus_country]
        if year_df.empty:
            base = historical[historical["country"] == focus_country]
            if not base.empty:
                year_df = base[base["year"] == base["year"].max()]
    arrivals = year_df["arrivals"].sum(min_count=1)
    receipts = year_df["receipts"].sum(min_count=1)
    per_arrival = receipts / arrivals if pd.notna(arrivals) and arrivals else float("nan")
    return arrivals, receipts, per_arrival


arrivals_total, receipts_total, per_arrival = kpi_values()
st.markdown(f"#### {selected_year} snapshot — {scope_label}")
k1, k2, k3 = st.columns(3)
k1.metric(f"Total arrivals ({selected_year})", humanize(arrivals_total))
k2.metric(f"Total receipts ({selected_year})", f"US$ {humanize(receipts_total)}")
k3.metric("Receipts per arrival", f"US$ {per_arrival:,.0f}" if pd.notna(per_arrival) else "n/a")
st.caption(
    "Receipts are World Bank current US$. When a city is selected, the historical "
    "figures describe its country; otherwise they cover all reporting economies."
)

st.divider()


# --------------------------------------------------------------------------- #
# Live section
# --------------------------------------------------------------------------- #
@refresh_every(REFRESH_SECONDS)
def render_live() -> None:
    live, _ = load_live()
    st.subheader("Live travel signals")
    if live.empty:
        st.warning("Live data not yet fetched, run fetcher.py")
        return

    rows = live.set_index("city")
    for start in range(0, len(CITY_ORDER), 3):
        cols = st.columns(3)
        for col, city in zip(cols, CITY_ORDER[start:start + 3]):
            with col:
                if city not in rows.index:
                    st.info(f"{city}: no live row yet")
                    continue
                row = rows.loc[city]
                code = row.get("weather_code")
                weather = WEATHER_CODES.get(int(code), "Unknown") if pd.notna(code) else "n/a"
                with st.container(border=True):
                    st.markdown(f"**{city}**")
                    left, right = st.columns(2)
                    left.metric(
                        "Planes in box",
                        "n/a" if pd.isna(row.get("planes_in_box")) else f"{int(row['planes_in_box'])}",
                    )
                    right.metric(
                        "Temp",
                        "n/a" if pd.isna(row.get("temp_c")) else f"{row['temp_c']:.1f} °C",
                    )
                    st.caption(f"{weather} · rain {row.get('precipitation', 0):.1f} mm")
                eur = row.get("eur_rate")
                if pd.notna(eur):
                    st.caption(f"1 USD = {eur:.4f} EUR")


render_live()
st.caption(
    "planes_in_box counts aircraft inside a ±0.5° box around the city. "
    "It is a **proxy for air traffic activity**, not a tourist count."
)
st.divider()


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def metric_title() -> str:
    return "arrivals" if metric == "arrivals" else "receipts (US$)"


def focus_row() -> pd.Series | None:
    if not focus_country:
        return None
    year_df = historical[
        (historical["year"] == selected_year) & (historical["country"] == focus_country)
    ]
    return None if year_df.empty else year_df.iloc[0]


# 1. Global trend ------------------------------------------------------------
st.subheader("1 · Global trend 2010–2023")
trend = historical.groupby("year", as_index=False)[metric].sum(min_count=1).dropna()
trend_fig = go.Figure()
trend_fig.add_trace(
    go.Scatter(
        x=trend["year"],
        y=trend[metric],
        mode="lines+markers",
        name="World total",
        line=dict(color="#1f77b4", width=3),
        fill="tozeroy",
        fillcolor="rgba(31,119,180,0.12)",
    )
)
if focus_country:
    focus_trend = (
        historical[historical["country"] == focus_country]
        .groupby("year", as_index=False)[metric].sum(min_count=1).dropna()
    )
    trend_fig.add_trace(
        go.Scatter(
            x=focus_trend["year"],
            y=focus_trend[metric],
            mode="lines+markers",
            name=focus_country,
            line=dict(color="#d62728", width=2, dash="dot"),
            yaxis="y2",
        )
    )
    trend_fig.update_layout(
        yaxis2=dict(
            title=f"{focus_country} {metric_title()}",
            overlaying="y",
            side="right",
            showgrid=False,
        )
    )
trend_fig.update_layout(
    xaxis_title="Year",
    yaxis_title=f"World {metric_title()}",
    hovermode="x unified",
)
trend_fig.update_xaxes(range=[2010, 2023], dtick=1)
st.plotly_chart(styled(trend_fig, 400), width="stretch")

# 2. Choropleth --------------------------------------------------------------
st.subheader(f"2 · {metric_label} by country in {selected_year}")
year_data = historical[historical["year"] == selected_year].dropna(subset=[metric])
if year_data.empty:
    st.info(f"No {metric_label.lower()} data reported for {selected_year}.")
else:
    map_fig = px.choropleth(
        year_data,
        locations="country",
        locationmode="country names",
        color=metric,
        hover_name="country",
        color_continuous_scale="Blues",
        labels={metric: metric_label},
    )
    if focus_city:
        info = CITY_POINTS[focus_city]
        map_fig.add_scattergeo(
            lon=[info["lon"]],
            lat=[info["lat"]],
            text=[focus_city],
            mode="markers+text",
            textposition="top center",
            marker=dict(size=14, color="#d62728", symbol="star"),
            name=focus_city,
            showlegend=False,
        )
    map_fig.update_layout(
        geo=dict(showframe=False, showcoastlines=False, projection_type="natural earth"),
        coloraxis_colorbar=dict(title=metric_label),
    )
    st.plotly_chart(styled(map_fig, 460), width="stretch")

# 3. Top 10 ------------------------------------------------------------------
st.subheader(f"3 · Top 10 countries by {metric_label.lower()} in {selected_year}")
if year_data.empty:
    st.info(f"No {metric_label.lower()} data reported for {selected_year}.")
else:
    top = year_data.nlargest(10, metric).copy()
    if focus_country:
        focus_entry = year_data[year_data["country"] == focus_country]
        if not focus_entry.empty and focus_country not in set(top["country"]):
            rank = int((year_data[metric] > focus_entry[metric].iloc[0]).sum()) + 1
            top = pd.concat([top, focus_entry])
            st.caption(
                f"{focus_country} ranks #{rank} in {selected_year} and is shown "
                "alongside the top 10."
            )
    top = top.sort_values(metric)
    top_fig = px.bar(
        top,
        x=metric,
        y="country",
        orientation="h",
        text=metric,
        color=top["country"].eq(focus_country).map({True: "Selected", False: "Other"}),
        color_discrete_map={"Selected": "#d62728", "Other": "#1f77b4"},
        labels={metric: metric_label, "color": ""},
        category_orders={"color": ["Other", "Selected"]},
    )
    top_fig.update_traces(texttemplate="%{text:,.3s}", textposition="outside")
    top_fig.update_layout(showlegend=False, xaxis_title=metric_label, yaxis_title="")
    st.plotly_chart(styled(top_fig, 420), width="stretch")

# 4. Scatter -----------------------------------------------------------------
st.subheader(f"4 · Arrivals vs receipts in {selected_year}")
scatter_data = historical[historical["year"] == selected_year].dropna(
    subset=["arrivals", "receipts"]
)
scatter_data = scatter_data[(scatter_data["arrivals"] > 0) & (scatter_data["receipts"] > 0)]
if scatter_data.empty:
    st.info(f"Not enough paired arrivals/receipts data for {selected_year}.")
else:
    scatter_fig = px.scatter(
        scatter_data,
        x="arrivals",
        y="receipts",
        hover_name="country",
        color="region",
        log_x=True,
        log_y=True,
        labels={
            "arrivals": "International arrivals (log)",
            "receipts": "Receipts in US$ (log)",
            "region": "Region",
        },
        opacity=0.7,
    )
    if focus_country:
        point = scatter_data[scatter_data["country"] == focus_country]
        if not point.empty:
            scatter_fig.add_trace(
                go.Scatter(
                    x=point["arrivals"],
                    y=point["receipts"],
                    mode="markers+text",
                    text=point["country"],
                    textposition="top center",
                    marker=dict(size=16, color="#d62728", symbol="star"),
                    name="Selected",
                    showlegend=True,
                )
            )
    scatter_fig.update_layout(legend_title_text="")
    st.plotly_chart(styled(scatter_fig, 460), width="stretch")

st.caption(
    "Sources: World Bank ST.INT.ARVL / ST.INT.RCPT.CD · OpenSky Network (aircraft) · "
    "Open-Meteo (weather) · Frankfurter (FX). No API keys are used."
)
