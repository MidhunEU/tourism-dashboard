"""Tourism Data Analysis and Visualization - single-page Streamlit dashboard.

Historical layer : data/clean_tourism.csv (World Bank, 2010-2023)
Live layer       : tourism.db -> live_signals (written by fetcher.py)

With the default ``LIVE_MODE=db`` the dashboard only reads local files and never
calls an upstream API. With ``LIVE_MODE=ondemand`` (used on shared hosting, where
no long-running collector exists) it collects one round itself when the stored row
is older than ``LIVE_MAX_AGE_SECONDS``, reusing :mod:`fetcher`. Every read is
guarded, so a missing database or a broken CSV produces a warning instead of a
traceback, and the historical charts always render.

Run:
    source .venv/bin/activate
    streamlit run app.py --server.port 8501

Shared hosting:
    LIVE_MODE=ondemand streamlit run app.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


def _apply_streamlit_secrets() -> None:
    """Expose ``st.secrets`` entries as environment variables.

    Shared hosting (Streamlit Community Cloud) configures an app through a
    Secrets field rather than a shell, while :mod:`config` reads the environment.
    Bridging the two keeps ``LIVE_MODE`` and friends settable in one place without
    duplicating the settings logic.
    """
    try:
        secrets = dict(st.secrets)
    except Exception:  # noqa: BLE001 - no secrets configured, use the environment
        return
    for key, value in secrets.items():
        os.environ.setdefault(str(key).upper(), str(value))


_apply_streamlit_secrets()

import config  # noqa: E402 - must follow the secrets bridge above

ALL_CITIES = "All cities"
CITY_POINTS = {
    city["city"]: {
        "country": city["country"],
        "lat": city["lat"],
        "lon": city["lon"],
    }
    for city in config.CITIES
}
CITY_ORDER = list(config.CITY_ORDER)

WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Violent showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm + hail",
    99: "Thunderstorm + hail",
}

STATUS_COLOURS = {"LIVE": "#1b7f3b", "STALE": "#b25e09", "OFFLINE": "#b3261e"}

st.set_page_config(
    page_title="Tourism Data Analysis and Visualization",
    page_icon="🌍",
    layout="wide",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def refresh_every(seconds: int):
    """Timed auto-refresh without a third-party package.

    ``st.autorefresh`` does not exist in Streamlit 1.x, so the view is wrapped in
    a timed fragment. On a version without fragments the decorator degrades to a
    normal render.
    """

    def decorator(func):
        fragment = getattr(st, "fragment", None) or getattr(
            st, "experimental_fragment", None
        )
        return fragment(run_every=seconds)(func) if fragment else func

    return decorator


def humanize(value: float | None) -> str:
    """Format a large number as K/M/B, or n/a."""
    if value is None or pd.isna(value):
        return "n/a"
    value = float(value)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return f"{value / size:,.2f}{unit}"
    return f"{value:,.0f}"


def status_for(last_updated: pd.Timestamp | None) -> tuple[str, str]:
    """Return ``(LIVE|STALE|OFFLINE, colour)`` for the freshness pill.

    LIVE under 5 minutes, STALE between 5 and 30 minutes, OFFLINE beyond that or
    when no live row exists at all.
    """
    if last_updated is None:
        return "OFFLINE", STATUS_COLOURS["OFFLINE"]
    age = (pd.Timestamp.now(tz="UTC") - last_updated).total_seconds()
    if age <= config.LIVE_MAX_AGE_SECONDS:
        return "LIVE", STATUS_COLOURS["LIVE"]
    if age <= config.STALE_MAX_AGE_SECONDS:
        return "STALE", STATUS_COLOURS["STALE"]
    return "OFFLINE", STATUS_COLOURS["OFFLINE"]


def newest_live_timestamp(db_path: Path) -> pd.Timestamp | None:
    """Newest stored live timestamp, read without loading the whole table."""
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute(
                f"SELECT MAX(timestamp) FROM {config.TABLE_NAME}"
            ).fetchone()
    except (sqlite3.Error, pd.errors.DatabaseError):
        return None
    if not row or not row[0]:
        return None
    stamp = pd.to_datetime(row[0], utc=True, errors="coerce")
    return None if pd.isna(stamp) else stamp


@st.cache_data(ttl=config.ON_DEMAND_TTL, show_spinner=False)
def collect_live_now() -> str | None:
    """Collect one round of live signals inside the dashboard process.

    Only used when ``LIVE_MODE=ondemand``. Returns ``None`` on success or a short
    error string. The result is cached for ``ON_DEMAND_TTL`` seconds, so every
    visitor within that window shares a single round of upstream calls instead
    of each page view hitting OpenSky.
    """
    import fetcher  # imported lazily: the default "db" mode never needs it

    try:
        session = fetcher.create_session()
        conn = fetcher.connect()
    except Exception as exc:  # noqa: BLE001 - surface the reason, never crash
        return f"{type(exc).__name__}: {exc}"
    try:
        try:
            eur_rate = fetcher.fetch_fx(session)
        except Exception:  # noqa: BLE001 - FX is optional, reuse the stored rate
            eur_rate = fetcher.last_eur_rate(conn)

        batch: list[tuple[object, ...]] = []
        collected = 0
        for city in config.CITIES:
            timestamp = fetcher.utc_now()
            signals, ok = fetcher.collect_city(session, conn, city)
            collected += int(ok)
            batch.append(
                (
                    timestamp,
                    city["city"],
                    signals["planes_in_box"],
                    signals["temp_c"],
                    signals["precipitation"],
                    signals["weather_code"],
                    eur_rate,
                )
            )
        if collected:
            fetcher.insert_rows(conn, batch)
        else:
            # Every upstream call failed: no row is written, so the pill stays
            # OFFLINE instead of claiming fresh data made entirely of nulls.
            return "every upstream API failed; no live row stored"
    except Exception as exc:  # noqa: BLE001 - the caller reports it as OFFLINE
        return f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def load_historical(path: str) -> pd.DataFrame:
    """Load and type the historical panel; cached for one hour."""
    df = pd.read_csv(path)
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for col in ("arrivals", "receipts", "receipts_per_arrival", "recovery_vs_2019"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=config.DASHBOARD_REFRESH_SECONDS, show_spinner=False)
def load_live() -> tuple[pd.DataFrame, pd.Timestamp | None, str | None]:
    """Read the latest row per city.

    Returns ``(frame, last_timestamp, error)``. ``error`` is a short string when
    SQLite could not be read, so the caller can warn instead of crashing.
    """
    db_path = Path(config.DB_PATH)
    fetch_error: str | None = None
    if config.on_demand():
        newest = newest_live_timestamp(db_path)
        age = (
            None
            if newest is None
            else (pd.Timestamp.now(tz="UTC") - newest).total_seconds()
        )
        if age is None or age > config.LIVE_MAX_AGE_SECONDS:
            fetch_error = collect_live_now()
    if not db_path.exists():
        return pd.DataFrame(), None, fetch_error
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as conn:
            frame = pd.read_sql_query(f"SELECT * FROM {config.TABLE_NAME}", conn)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        return pd.DataFrame(), None, f"{type(exc).__name__}: {exc}"

    if frame.empty:
        return frame, None, fetch_error

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"])
    if frame.empty:
        return frame, None, fetch_error

    latest = frame.sort_values("timestamp").groupby("city", as_index=False).tail(1)
    order = {city: index for index, city in enumerate(CITY_ORDER)}
    latest = latest.assign(_rank=latest["city"].map(order).fillna(len(order)))
    latest = latest.sort_values(["_rank", "city"]).drop(columns="_rank")
    return latest.reset_index(drop=True), frame["timestamp"].max(), fetch_error


def live_state() -> tuple[pd.DataFrame, pd.Timestamp | None, str | None]:
    """Cached live read with a session-level fallback to the last good frame."""
    frame, last_updated, error = load_live()
    if error is None:
        st.session_state["live_backup"] = (frame, last_updated)
        return frame, last_updated, None

    backup_frame, backup_updated = st.session_state.get(
        "live_backup", (pd.DataFrame(), None)
    )
    return backup_frame, backup_updated, error


def render_status() -> None:
    """Render the LIVE / STALE / OFFLINE pill plus the last-updated time."""
    _frame, last_updated, error = live_state()
    label, colour = status_for(last_updated)
    if last_updated is None:
        detail = f"no live rows yet - start fetcher.py (refreshes every {config.DASHBOARD_REFRESH_SECONDS} s)"
    else:
        detail = (
            f"{last_updated.tz_convert('UTC').strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"(refreshes every {config.DASHBOARD_REFRESH_SECONDS} s)"
        )
    st.markdown(
        '<div style="display:flex;align-items:center;gap:.6rem;margin:0 0 .7rem 0">'
        f'<span style="background:{colour};color:#fff;border-radius:999px;'
        "padding:.15rem .65rem;font-size:.78rem;font-weight:700;"
        f'letter-spacing:.05em">{label}</span>'
        f'<span style="color:#555;font-size:.85rem">Live data last updated: {detail}</span>'
        "</div>",
        unsafe_allow_html=True,
    )
    if error:
        st.warning("Live data unavailable, showing last cached data")


def styled(fig: go.Figure, height: int = 380) -> go.Figure:
    """Apply the shared chart layout."""
    fig.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
    )
    return fig


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.title("🌍 Tourism Data Analysis and Visualization")
st.markdown(
    "Historical international tourism **2010–2023** (World Bank) alongside "
    "**live travel signals** for six cities, collected every 2 minutes."
)


@refresh_every(config.DASHBOARD_REFRESH_SECONDS)
def render_header_status() -> None:
    """Auto-refreshing status pill."""
    render_status()


render_header_status()

try:
    historical = load_historical(config.CSV_PATH)
except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
    st.error(
        f"Historical dataset could not be loaded from `{config.CSV_PATH}`: {exc}. "
        "Restore data/clean_tourism.csv to see the charts."
    )
    st.stop()

if historical.empty:
    st.error(
        f"Historical dataset `{config.CSV_PATH}` contains no rows. "
        "Restore the file to see the charts."
    )
    st.stop()


# --------------------------------------------------------------------------- #
# Sidebar filters
# --------------------------------------------------------------------------- #
st.sidebar.header("Filters")
city_choice = st.sidebar.selectbox("City", [ALL_CITIES] + CITY_ORDER)
metric_label = st.sidebar.selectbox("Metric", ["Arrivals", "Receipts"])
metric = "arrivals" if metric_label == "Arrivals" else "receipts"

years = sorted(int(y) for y in historical["year"].dropna().unique())
selected_year = st.sidebar.selectbox("Year", years, index=len(years) - 1)

focus_city = None if city_choice == ALL_CITIES else city_choice
focus_country = CITY_POINTS[focus_city]["country"] if focus_city else None
scope_label = focus_country if focus_country else "All countries"


# --------------------------------------------------------------------------- #
# KPI cards
# --------------------------------------------------------------------------- #
def kpi_values() -> tuple[float, float, float]:
    """Totals for the selected year and scope."""
    year_df = historical[historical["year"] == selected_year]
    if focus_country:
        year_df = year_df[year_df["country"] == focus_country]
        if year_df.empty:
            base = historical[historical["country"] == focus_country]
            if not base.empty:
                year_df = base[base["year"] == base["year"].max()]
    arrivals = year_df["arrivals"].sum(min_count=1)
    receipts = year_df["receipts"].sum(min_count=1)
    per_arrival = (
        receipts / arrivals if pd.notna(arrivals) and arrivals else float("nan")
    )
    return arrivals, receipts, per_arrival


arrivals_total, receipts_total, per_arrival = kpi_values()
st.markdown(f"#### {selected_year} snapshot — {scope_label}")
k1, k2, k3 = st.columns(3)
k1.metric(f"Total arrivals ({selected_year})", humanize(arrivals_total))
k2.metric(f"Total receipts ({selected_year})", f"US$ {humanize(receipts_total)}")
k3.metric(
    "Receipts per arrival",
    f"US$ {per_arrival:,.0f}" if pd.notna(per_arrival) else "n/a",
)
st.caption(
    "Receipts are World Bank current US$. When a city is selected, the historical "
    "figures describe its country; otherwise they cover all reporting economies."
)

st.divider()


# --------------------------------------------------------------------------- #
# Live section
# --------------------------------------------------------------------------- #
@refresh_every(config.DASHBOARD_REFRESH_SECONDS)
def render_live() -> None:
    """Six auto-refreshing city cards, with explicit empty/error states."""
    live, _, error = live_state()
    st.subheader("Live travel signals")
    if error:
        st.warning("Live data unavailable, showing last cached data")
    if live.empty:
        st.warning("Live data not yet fetched, run fetcher.py")
        return

    rows = live.set_index("city")
    for start in range(0, len(CITY_ORDER), 3):
        cols = st.columns(3)
        for col, city in zip(cols, CITY_ORDER[start : start + 3]):
            with col, st.container(border=True):
                st.markdown(f"**{city}**")
                if city not in rows.index:
                    st.info("No live data yet, start fetcher.py")
                    continue
                row = rows.loc[city]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
                code = row.get("weather_code")
                weather = (
                    WEATHER_CODES.get(int(code), "Unknown") if pd.notna(code) else "n/a"
                )
                left, right = st.columns(2)
                left.metric(
                    "Planes in box",
                    "n/a"
                    if pd.isna(row.get("planes_in_box"))
                    else f"{int(row['planes_in_box'])}",
                )
                right.metric(
                    "Temp",
                    "n/a" if pd.isna(row.get("temp_c")) else f"{row['temp_c']:.1f} °C",
                )
                rain = row.get("precipitation")
                rain_text = f"{rain:.1f} mm" if pd.notna(rain) else "n/a"
                st.caption(f"{weather} · rain {rain_text}")
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
    """Human label for the selected metric."""
    return "arrivals" if metric == "arrivals" else "receipts (US$)"


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
        line={"color": "#1f77b4", "width": 3},
        fill="tozeroy",
        fillcolor="rgba(31,119,180,0.12)",
    )
)
if focus_country:
    focus_trend = (
        historical[historical["country"] == focus_country]
        .groupby("year", as_index=False)[metric]
        .sum(min_count=1)
        .dropna()
    )
    trend_fig.add_trace(
        go.Scatter(
            x=focus_trend["year"],
            y=focus_trend[metric],
            mode="lines+markers",
            name=focus_country,
            line={"color": "#d62728", "width": 2, "dash": "dot"},
            yaxis="y2",
        )
    )
    trend_fig.update_layout(
        yaxis2={
            "title": f"{focus_country} {metric_title()}",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        }
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
            marker={"size": 14, "color": "#d62728", "symbol": "star"},
            name=focus_city,
            showlegend=False,
        )
    map_fig.update_layout(
        geo={
            "showframe": False,
            "showcoastlines": False,
            "projection_type": "natural earth",
        },
        coloraxis_colorbar={"title": metric_label},
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
scatter_data = scatter_data[
    (scatter_data["arrivals"] > 0) & (scatter_data["receipts"] > 0)
]
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
                    marker={"size": 16, "color": "#d62728", "symbol": "star"},
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
