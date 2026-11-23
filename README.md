# Tourism Data Analysis and Visualization

A single-page, real-time tourism analytics dashboard for the *Data Analytics with
Python* course project.

It combines two layers:

| Layer | Content | Source |
|---|---|---|
| **Historical** | International tourist arrivals and receipts, 2010–2023, by country | World Bank API → `data/clean_tourism.csv` |
| **Live** | Aircraft count, temperature, precipitation, weather code and the USD→EUR rate for Paris, London, New York, Dubai, Singapore, Tokyo | OpenSky, Open-Meteo, Frankfurter → `tourism.db` (`live_signals`) |

The fetcher refreshes the live layer every **120 seconds**; the dashboard
re-reads it every **60 seconds**. If an API fails, the fetcher logs the error,
keeps the last good value for that city, and the loop continues. The dashboard
never depends on a live API call, so historical charts always load.

---

## 1. Run it on Linux

```bash
cd <project-folder>

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # or: pip install streamlit pandas plotly requests
```

Terminal 1 — collector (runs forever, one loop every 120 s):

```bash
source .venv/bin/activate
python3 fetcher.py
```

Terminal 2 — dashboard:

```bash
source .venv/bin/activate
streamlit run app.py --server.port 8501
```

Then open <http://localhost:8501>.

Useful extra commands:

```bash
python3 fetcher.py --loops 2                                   # two loops then stop
sqlite3 tourism.db "SELECT COUNT(*) FROM live_signals;"         # row count
sqlite3 tourism.db "SELECT * FROM live_signals ORDER BY timestamp DESC LIMIT 6;"
python3 -m py_compile fetcher.py app.py                        # syntax check
```

## 2. Files

```
.
├── PLAN.md                  # 1-page plan: architecture, APIs, schemas
├── fetcher.py               # 120 s collection loop → SQLite
├── app.py                   # Streamlit dashboard (one page)
├── data/clean_tourism.csv   # cleaned World Bank panel, 2010–2023
├── tourism.db               # SQLite live_signals table (created by fetcher.py)
├── requirements.txt         # pip freeze of the environment
└── README.md                # this file
```

## 3. APIs used (all free, no keys)

| API | Endpoint | What it gives us | Called |
|---|---|---|---|
| World Bank | `ST.INT.ARVL` — `.../country/all/indicator/ST.INT.ARVL?format=json&date=2010:2023&per_page=20000` | International tourist arrivals per country-year | Once, while building the CSV |
| World Bank | `ST.INT.RCPT.CD` — `.../country/all/indicator/ST.INT.RCPT.CD?format=json&date=2010:2023&per_page=20000` | International tourism receipts, current US$ | Once, while building the CSV |
| OpenSky Network | `https://opensky-network.org/api/states/all?lamin={lat-0.5}&lamax={lat+0.5}&lomin={lon-0.5}&lomax={lon+0.5}` | `len(states)` = `planes_in_box` | Every loop, per city |
| Open-Meteo | `https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,precipitation,weather_code` | `temp_c`, `precipitation`, `weather_code` | Every loop, per city |
| Frankfurter | `https://api.frankfurter.app/latest?from=USD&to=EUR,INR,JPY,GBP,SGD,AED` | USD→EUR rate (`eur_rate`) | Once per loop |

World Bank region labels come from the companion metadata endpoint
`https://api.worldbank.org/v2/country/all?format=json&per_page=400`, used only
when the CSV was built.

No API keys, no accounts, no paid tiers.

## 4. Proxy vs real count — read this before quoting numbers

- `planes_in_box` is **the number of aircraft transponder states inside a
  ±0.5° latitude/longitude box around the city**. It is a **proxy for air
  traffic activity near that city**, not a count of tourists, not a count of
  passengers, and not a count of arrivals at the city.
- Aircraft in the box include overflights, cargo, private and military traffic.
  A plane at cruise altitude on the way to another continent can be inside the
  box. OpenSky also only sees aircraft that are broadcasting ADS-B and are
  received by volunteer ground stations, so coverage is incomplete and differs
  by region.
- Likewise, `temp_c`, `precipitation` and `weather_code` describe **current
  conditions**, not tourism demand. `eur_rate` is a single global exchange rate
  (identical for all six cities in a loop), included for context.
- The historical layer is a **real reported count**: World Bank arrivals are
  internationally reported tourist arrivals per country-year, and receipts are
  current US$ values. Use this layer for any quantitative claim about tourism
  volume.

Only the historical layer supports statements like "country X received Y
tourists". The live layer supports statements like "at 09:01 UTC there were 29
aircraft in the Paris box and it was 3.1 °C".

## 5. Data model

`tourism.db`:

```sql
CREATE TABLE live_signals (
    timestamp      TEXT,     -- UTC ISO-8601, e.g. 2026-11-23T09:01:05Z
    city           TEXT,     -- Paris | London | New York | Dubai | Singapore | Tokyo
    planes_in_box  INTEGER,  -- len(states) from OpenSky
    temp_c         REAL,
    precipitation  REAL,     -- mm
    weather_code   INTEGER,  -- WMO code
    eur_rate       REAL      -- USD -> EUR
);
```

`data/clean_tourism.csv`:

| Column | Meaning |
|---|---|
| `country` | World Bank economy name |
| `region` | World Bank region label |
| `year` | 2010–2023 |
| `arrivals` | International tourist arrivals (NaN if not reported) |
| `receipts` | International tourism receipts, current US$ (NaN if not reported) |
| `receipts_per_arrival` | `receipts / arrivals` (NaN when either is missing or arrivals is 0) |
| `recovery_vs_2019` | `arrivals / arrivals_2019 * 100` per country (NaN without a 2019 base) |

Cleaning rules applied when the CSV was built:

1. Only real economies kept — the World Bank `country/all` feed's aggregates
   (World, High income, Euro area, income groups, regional blocs) were dropped
   by keeping economies with a 2-letter ISO code and a non-aggregate region.
2. Only 2010–2023 kept.
3. Rows where both indicators are missing were dropped; a single missing
   indicator stays **NaN**, never 0.

## 6. Dashboard contents (single page)

- Header: *Live data last updated … UTC* from SQLite (`n/a` until the fetcher runs).
- Sidebar filters: **City** (All cities + 6), **Metric** (Arrivals / Receipts),
  **Year** (2010–2020, the years with reported values).
- Three KPI cards for the selected year and scope: total arrivals, total
  receipts, receipts per arrival.
- Live section: six city cards with planes in box, temperature, weather,
  precipitation and the USD→EUR rate, auto-refreshed every 60 s.
- Four Plotly charts:
  1. Global line chart of the selected metric, 2010–2023 axis (the focus
     country's line is added on a secondary axis when a city is selected).
  2. Choropleth map by country for the selected year (the selected city is
     marked with a star).
  3. Top 10 countries bar chart for the selected year (selected country
     highlighted).
  4. Arrivals vs receipts scatter (log–log) for the selected year, with the
     selected country highlighted.
- If `live_signals` is empty or the database is missing, the dashboard warns
  **"Live data not yet fetched, run fetcher.py"** and still shows the historical
  charts and KPIs.

Changing the city filter changes the KPI scope, adds the focus-country trend
line, marks the city on the map, highlights its bar and its scatter point.
Changing the metric switches charts 1–3 between arrivals and receipts.

## 7. Assumptions and known limitations

1. **Python version.** The brief asks for Python 3.11, but this Linux machine
   only has Python 3.12.3 installed (`/usr/bin/python3.12`, no `python3.11`).
   The project uses `python3` from the venv, i.e. 3.12.3. All listed libraries
   support 3.12; no 3.11-only syntax is used.
2. **`st.autorefresh` does not exist in Streamlit 1.x.** The dashboard instead
   uses Streamlit's built-in timed fragment (`st.fragment(run_every=60)`) via a
   small `refresh_every(60)` decorator, which produces the same 60-second
   auto-refresh without adding a third-party package. On a Streamlit version
   without fragments the decorator degrades to a normal render.
3. **World Bank coverage gap.** In the current World Bank snapshot (indicator
   feed last modified 2026-11-11), `ST.INT.ARVL` and `ST.INT.RCPT.CD` return
   `null` for every economy for 2021–2023. The panel is therefore built for
   2010–2023 but only 2010–2020 carry values; 2021–2023 remain missing (NaN),
   not zero, and are not shown as data points. The line chart keeps a fixed
   2010–2023 x-axis. Nothing was imputed.
   No synthetic fallback data was needed: the API responded successfully.
4. **Frankfurter has no AED.** The endpoint is called exactly as specified
   (`to=EUR,INR,JPY,GBP,SGD,AED`), but Frankfurter serves the ECB reference set,
   which does not include AED, so the response contains EUR/GBP/INR/JPY/SGD only.
   Only `eur_rate` is stored, so nothing is missing in the database.
5. **OpenSky anonymous rate limit.** OpenSky limits anonymous clients, and the
   dashboard's 6 requests every 2 minutes (4,320/day) can exceed the free
   allowance, producing HTTP 429. The fetcher then logs the error and reuses the
   city's last good `planes_in_box`, so rows keep arriving with a slightly stale
   proxy value. Running the fetcher for short demo sessions is fine.
6. **City → country mapping** used for the historical focus:
   Paris→France, London→United Kingdom, New York→United States,
   Dubai→United Arab Emirates, Singapore→Singapore, Tokyo→Japan.
7. **Choropleth name matching.** Plotly matches World Bank names by country
   name; a few long forms (for example `Congo, Dem. Rep.`) may not colour on the
   map even though they are present in the data.
8. **Time zone.** All live timestamps are stored and displayed in UTC.
9. The CSV is a snapshot; it is not refreshed by the fetcher (rebuilding it
   requires re-running the World Bank step described above).

## 8. Viva / defence answers

**Why is `planes_in_box` a proxy?** Because OpenSky reports aircraft
transponder positions, not people. The box count includes overflights, cargo,
private and military aircraft, and only aircraft visible to volunteer ADS-B
receivers; it also ignores tourists who arrive by train, car or ship. It is
therefore a *signal of air-traffic intensity* near a city, correlated with
travel activity, but it cannot be converted into a tourist count without
assumptions.

**Why 120 seconds?** It is a deliberate middle ground: OpenSky updates its state
vectors roughly every 10–60 seconds and free anonymous access is rate-limited,
so polling every 2 minutes keeps the dashboard near-live while staying inside
courtesy limits and producing only 30 rows per hour per city (a manageable
SQLite table). It is also coarser than the 60-second dashboard refresh, so the
UI shows genuinely new data on every other refresh rather than re-reading the
same row repeatedly.

**Why SQLite?** It is part of the Python standard library, requires no server,
user or password, and stores the whole project's live history in one portable
file (`tourism.db`). For a single-writer fetcher and a single-reader dashboard
at this volume it is more than sufficient, and it can be queried directly from
the shell (`sqlite3 tourism.db ...`) for verification, which keeps the project
reproducible on any Linux machine without extra infrastructure.
