# Tourism Data Analysis and Visualization — Project Plan

**Course:** Data Analytics with Python — End Project

## 1. Objective / User Story

A viewer opens a single-page Streamlit dashboard and sees:

1. **Historical global tourism (2010–2023)** — World Bank arrivals and receipts, cleaned and stored as CSV.
2. **Live travel signals** for 6 tourist cities — planes in the city's bounding box, current temperature, precipitation, weather code, and the USD→EUR rate — refreshed by a background fetcher every 120 seconds and stored in SQLite.

The dashboard auto-refreshes its data view every 60 seconds, so the viewer sees the historical baseline plus near-live conditions side by side.

## 2. Files

| File | Purpose |
|---|---|
| `PLAN.md` | This plan: architecture, APIs, schemas. |
| `fetcher.py` | Long-running collector loop (120 s). Writes `live_signals` rows to `tourism.db`. |
| `data/clean_tourism.csv` | Cleaned historical panel: one row per country-year (2010–2023). |
| `tourism.db` | SQLite database (`live_signals` table). Created by `fetcher.py`. |
| `app.py` | Single-page Streamlit dashboard (historical + live). |
| `requirements.txt` | Pinned environment from `pip freeze`. |
| `README.md` | Linux run instructions, API descriptions, proxy-vs-real explanation, assumptions. |

No other files. No Docker, Kafka, Airflow, Postgres, Jupyter, BI tools. Libraries: streamlit, pandas, plotly, requests, sqlite3 (stdlib).

## 3. Architecture

```
World Bank API ──(one-off build script inside csv build step)──> data/clean_tourism.csv
                                                                        │
OpenSky (planes) ─┐                                                     ▼
Open-Meteo (wx) ──┼──> fetcher.py (every 120 s) ──> tourism.db (live_signals) ──> app.py (Streamlit, refresh 60 s)
Frankfurter (FX) ─┘                                                             └─> Plotly charts
```

The dashboard never calls a live API itself: it reads SQLite + CSV. If a live API fails or the table is empty, the historical charts still render.

## 4. APIs (only these; no keys)

| # | API | Endpoint | Use | Cadence |
|---|---|---|---|---|
| 1 | World Bank | `https://api.worldbank.org/v2/country/all/indicator/ST.INT.ARVL?format=json&date=2010:2023&per_page=20000` | International arrivals by country-year | One-off |
| 2 | World Bank | `https://api.worldbank.org/v2/country/all/indicator/ST.INT.RCPT.CD?format=json&date=2010:2023&per_page=20000` | International tourism receipts (current US$) | One-off |
| 3 | OpenSky Network | `https://opensky-network.org/api/states/all?lamin=..&lamax=..&lomin=..&lomax=..` | Aircraft positions in a ±0.5° box around each city; `len(states)` = `planes_in_box` | Every loop, per city |
| 4 | Open-Meteo | `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,precipitation,weather_code` | Current weather at each city | Every loop, per city |
| 5 | Frankfurter | `https://api.frankfurter.app/latest?from=USD&to=EUR,INR,JPY,GBP,SGD,AED` | USD→EUR rate stored with every row | Once per loop |

### Cities (fixed)

| City | Country | lat | lon |
|---|---|---|---|
| Paris | France | 48.85 | 2.35 |
| London | United Kingdom | 51.50 | -0.12 |
| New York | United States | 40.71 | -74.00 |
| Dubai | United Arab Emirates | 25.20 | 55.27 |
| Singapore | Singapore | 1.35 | 103.82 |
| Tokyo | Japan | 35.68 | 139.69 |

OpenSky box = lat ± 0.5, lon ± 0.5.

## 5. Schemas

### SQLite `tourism.db`

```sql
CREATE TABLE IF NOT EXISTS live_signals (
    timestamp    TEXT,     -- UTC ISO-8601, e.g. 2026-11-23T08:45:12Z
    city         TEXT,     -- one of the 6 cities
    planes_in_box INTEGER, -- len(states) from OpenSky (NULL on failure, last good row reused)
    temp_c       REAL,     -- Open-Meteo temperature_2m
    precipitation REAL,    -- Open-Meteo precipitation (mm)
    weather_code INTEGER,  -- WMO weather code
    eur_rate     REAL      -- Frankfurter USD->EUR
);
```

### `data/clean_tourism.csv`

| Column | Type | Meaning |
|---|---|---|
| `country` | str | World Bank economy name |
| `region` | str | Region label (from the country metadata endpoint) |
| `year` | int | 2010–2023 |
| `arrivals` | float | ST.INT.ARVL (NaN if missing) |
| `receipts` | float | ST.INT.RCPT.CD, current US$ (NaN if missing) |
| `receipts_per_arrival` | float | `receipts / arrivals` (NaN if either missing / zero) |
| `recovery_vs_2019` | float | `arrivals / arrivals_2019 * 100` per country (NaN if no 2019 base) |

Rules: aggregates (World, High income, Euro area, regions, etc.) dropped; only 2010–2023; missing values stay NaN — never filled with 0.

## 6. Build order

- [x] STEP 0 — Plan + git init/commit.
- [ ] STEP 1 — `fetcher.py`: per-city try/except, Frankfurter once per loop, never crash, log failures and reuse last good row, print `fetched [city] at [timestamp]`.
- [ ] STEP 2 — `data/clean_tourism.csv` from World Bank (fallback: deterministic realistic structure, documented).
- [ ] STEP 3 — `app.py`: last_updated, filters, 3 KPIs, 6 live cards, 4 Plotly charts, 60 s autorefresh, empty-live warning.
- [ ] STEP 4 — `README.md`.
- [ ] STEP 5 — `pip freeze > requirements.txt`.
- [ ] Verification — compile, 2 fetch loops, `COUNT(*)`, streamlit boot.
