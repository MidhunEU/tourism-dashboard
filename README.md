# Tourism Data Analysis and Visualization

A single-page Streamlit dashboard for the *Data Analytics with Python* course
end project. It shows World Bank historical tourism data for 2010–2023 next to
live travel signals for six cities, refreshed every two minutes.

---

## 1. What it does

| Layer | Content | Source |
|---|---|---|
| **Historical** | International arrivals and receipts per country-year, 2010–2023 | World Bank `ST.INT.ARVL` / `ST.INT.RCPT.CD` → `data/clean_tourism.csv` |
| **Live** | Aircraft in a ±0.5° box, temperature, precipitation, weather code and the USD→EUR rate for Paris, London, New York, Dubai, Singapore, Tokyo | OpenSky, Open-Meteo, Frankfurter → `tourism.db` (`live_signals`) |

The **fetcher** collects one row per city every `FETCH_INTERVAL_SECONDS`
(default 120 s). The **dashboard** re-reads SQLite every
`DASHBOARD_REFRESH_SECONDS` (default 60 s) and never calls an upstream API
itself, so the historical charts always render even when every API is down.

Dashboard contents (single page, unchanged feature set):

- a **LIVE / STALE / OFFLINE** status pill with the last fetch timestamp;
- sidebar filters: **City** (All cities + 6), **Metric** (Arrivals / Receipts), **Year**;
- three KPI cards for the selected year and scope;
- six live city cards (planes, temperature, weather, rain, USD→EUR);
- four Plotly charts: global trend, choropleth map, top-10 bars, arrivals-vs-receipts scatter.

`planes_in_box` is **a proxy for air-traffic activity, not a tourist count**:
it counts transponder states inside the box, including overflights, cargo and
private traffic, and only aircraft visible to volunteer ADS-B receivers. The
historical layer is the only place real reported tourist counts appear.

### Project layout

```
.
├── app.py                      # Streamlit dashboard (single page)
├── fetcher.py                  # collector loop → SQLite (logging, retries, health)
├── config.py                   # every setting, env-var overridable
├── cleaning.py                 # historical cleaning rules (covered by tests)
├── data/clean_tourism.csv      # cleaned World Bank panel, 2010–2023
├── tourism.db                  # SQLite live_signals table (WAL mode)
├── requirements.txt            # pinned environment (pip freeze)
├── start.sh / stop.sh          # local Linux process control
├── systemd/tourism-fetcher.service
├── tests/                      # pytest suite
├── logs/                       # app.log (rotating), fetcher.log, streamlit.log
├── .env.example                # configuration template (copy to .env)
└── README.md
```

---

## 2. How to run locally

```bash
cd <project-folder>

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # optional: change ports, paths, intervals, endpoints

# terminal 1 — collector (runs forever)
python3 fetcher.py

# terminal 2 — dashboard
streamlit run app.py --server.port 8501
```

Open <http://localhost:8501>.

Or run both with the helper script (fetcher in the background with `nohup`,
dashboard in the background, both logging to `logs/`):

```bash
./start.sh          # starts fetcher + dashboard, waits for the health endpoint
./stop.sh           # stops both (PID files, then pkill as a safety net)
PORT=8600 ./start.sh
```

Useful commands:

```bash
python3 fetcher.py --loops 2        # two loops then exit (smoke test)
python3 fetcher.py --health         # last fetch time + success/failure counters as JSON
sqlite3 tourism.db "SELECT COUNT(*) FROM live_signals;"
python3 -m py_compile fetcher.py app.py config.py cleaning.py
```

### Configuration

All values live in `config.py` and are read from the environment (a local
`.env` file is loaded when `python-dotenv` is installed; real environment
variables win). See `.env.example`. Key settings:

| Variable | Default | Meaning |
|---|---|---|
| `FETCH_INTERVAL_SECONDS` | `120` | seconds between collection loops |
| `REQUEST_TIMEOUT` | `10` | HTTP timeout for every request |
| `RETRY_COUNT` | `3` | retries per request (backoff 2 s, 4 s, 8 s) |
| `DB_PATH` | `tourism.db` | SQLite file |
| `CSV_PATH` | `data/clean_tourism.csv` | historical panel |
| `LOG_FILE` | `logs/app.log` | rotating log file (1 MB × 5) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `OPENSKY_URL`, `OPENMETEO_URL`, `FRANKFURTER_URL` | see `.env.example` | upstream endpoints |
| `DASHBOARD_REFRESH_SECONDS` | `60` | dashboard auto-refresh |
| `LIVE_MAX_AGE_SECONDS` | `300` | LIVE → STALE threshold (5 min) |
| `STALE_MAX_AGE_SECONDS` | `1800` | STALE → OFFLINE threshold (30 min) |

`.env` is git-ignored. No secrets or API keys are used anywhere in the project.

---

## 3. How to run in production with systemd

`systemd/tourism-fetcher.service` is a **user** unit. It uses `%h` (the user's
home directory) rather than a hardcoded `/home/user`:

```ini
[Unit]
Description=Tourism live signal fetcher (OpenSky, Open-Meteo, Frankfurter)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/<project-path>
Environment=PYTHONUNBUFFERED=1
ExecStart=/bin/bash -c 'source %h/<project-path>/.venv/bin/activate && exec python3 fetcher.py'
Restart=always
RestartSec=10
KillSignal=SIGINT
```

Install and start it:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/tourism-fetcher.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tourism-fetcher

systemctl --user cat tourism-fetcher.service     # verify the installed unit
systemctl --user status tourism-fetcher          # is it running?
```

The unit is written for this project's absolute path. If you move the folder,
update `WorkingDirectory` and the `source` path in `ExecStart`, then
`systemctl --user daemon-reload`.

The dashboard is a normal long-running web server; run it with `start.sh`, or add
a second unit whose `ExecStart` is
`/bin/bash -c 'source .../bin/activate && exec streamlit run app.py --server.port 8501 --server.headless true'`.

To keep the fetcher running after logout: `loginctl enable-linger "$USER"`.

---

## 4. How to check logs

```bash
# Under systemd (journal)
journalctl --user -u tourism-fetcher -f          # follow live
journalctl --user -u tourism-fetcher --since "10 min ago"
journalctl --user -u tourism-fetcher -p warning  # warnings and errors only

# Without systemd
tail -f logs/app.log             # rotating log: timestamp, level, city, message
tail -f logs/fetcher.log         # collector stdout/stderr from start.sh
tail -f logs/streamlit.log       # dashboard output from start.sh

# Is data actually arriving?
python3 fetcher.py --health
sqlite3 tourism.db "SELECT city, MAX(timestamp) FROM live_signals GROUP BY city;"
```

Every log line has the shape
`2026-11-23T09:01:05+0000 INFO     [Paris] fetched at [2026-11-23T09:01:05Z]`
(timestamp, level, city, message).

---

## 5. How to run tests

```bash
source .venv/bin/activate
python -m pytest -q
ruff check .
```

| Test file | Covers |
|---|---|
| `tests/test_fetcher.py` | OpenSky parsing with 0/1/5 states, null and missing `states`, ±0.5° box params, retry on timeout (2 s/4 s backoff), give-up after 3 retries, 429/5xx retried but 4xx not, malformed/non-object JSON rejection, missing `current` → None values, missing `states` skips only the aircraft field, batch insert + `get_health`, WAL mode |
| `tests/test_cleaning.py` | `receipts_per_arrival` (incl. zero arrivals → NaN), `recovery_vs_2019` (incl. missing 2019 base → NaN), aggregates dropped, NaN preserved (never zero-filled), both-missing rows dropped, year bounds and column order |
| `tests/test_config.py` | defaults with no env vars, fixed city list and coordinates, env overrides, invalid numbers fall back to defaults |

The suite uses no network: every HTTP call is replaced by a fake session.

---

## 6. Failure modes and what happens

| Failure | Fetcher | Dashboard |
|---|---|---|
| OpenSky timeout / 5xx | retried after 2 s, 4 s, 8 s; if still failing the city keeps its last stored `planes_in_box` and a **warning** is logged | unchanged; pill shows LIVE/STALE from the last stored row |
| OpenSky 429 (anonymous rate limit) | retried (429 is treated as retryable), then falls back to the last value | unchanged |
| OpenSky returns JSON without `states` | warning `skipping planes for this city`, only the aircraft count is skipped; weather still collected | card shows the last known plane count or `n/a` |
| Open-Meteo down or payload without `current` | weather fields are reused from the last row (or `None`); temperature/rain show `n/a` | card shows `n/a` for the affected fields |
| Frankfurter down | last stored `eur_rate` reused | card shows the last rate |
| OpenSky 4xx other than 429 | **not** retried; error logged, city falls back | unchanged |
| Database locked / busy | WAL mode + 10 s busy timeout; insert retried by the next loop | read is `mode=ro`, so the dashboard never blocks the writer |
| SQLite file missing or unreadable | fetcher creates it on start | warning `Live data unavailable, showing last cached data`, historical charts still render |
| No rows yet | first loop inserts within seconds | pill **OFFLINE**, `Live data not yet fetched, run fetcher.py` |
| Fetcher stopped for > 5 min / > 30 min | — | pill flips to **STALE** / **OFFLINE**; last-updated time shows the gap |
| CSV missing or corrupt | — | error message naming the file; no traceback, no blank page |

The collector never exits because of an API failure: each city is collected in
its own `try/except`, a failed loop still writes rows, and `Restart=always` in
the unit covers crashes or reboots. A loop that fails entirely is logged and the
next one starts `FETCH_INTERVAL_SECONDS` later.

If OpenSky goes down during a demo: the fetcher keeps retrying and reuses the
last aircraft counts (so cards stay populated with slightly stale numbers and
the API calls are logged as warnings), the loop never stops, weather and FX keep
updating, and the dashboard pill turns **STALE** after five minutes — the
historical charts and KPIs are unaffected.

---

## 7. Limitations

1. **Python version.** The brief asks for 3.11; this machine has 3.12.3 and no
   `python3.11`. The code uses no version-specific syntax and all dependencies
   support 3.12.
2. **No `st.autorefresh`.** Streamlit 1.x has no `st.autorefresh`, so the
   dashboard uses a timed fragment (`st.fragment(run_every=60)`) through the
   small `refresh_every()` decorator, degrading to a normal render on versions
   without fragments.
3. **World Bank coverage gap.** In the current World Bank snapshot both
   indicators return `null` for every economy for 2021–2023, so the panel spans
   2010–2023 but only 2010–2020 carry values. Missing stays NaN — nothing is
   imputed, and the trend chart keeps a fixed 2010–2023 axis.
4. **Frankfurter has no AED.** The endpoint is called exactly as specified, but
   Frankfurter serves the ECB reference set (EUR/GBP/INR/JPY/SGD). Only the EUR
   rate is stored, so no column is missing.
5. **OpenSky anonymous limits.** Six box queries every two minutes can exceed the
   free allowance (HTTP 429). The retry/fallback path keeps rows flowing, but the
   aircraft count may be a few minutes old.
6. **`planes_in_box` is a proxy, not tourist arrivals.** See section 1.
7. **City → country mapping** for the historical focus: Paris→France,
   London→United Kingdom, New York→United States, Dubai→United Arab Emirates,
   Singapore→Singapore, Tokyo→Japan.
8. **Choropleth name matching.** Plotly matches World Bank names by country name,
   so a few long forms (e.g. `Congo, Dem. Rep.`) may stay uncoloured.
9. **Time zone.** All live timestamps are stored and displayed in UTC.
10. **One writer, one reader.** SQLite is the right fit for a single fetcher and a
    single dashboard; it is not a multi-node store.
11. **The CSV is a snapshot** and is not rebuilt by the fetcher; the cleaning
    rules live in `cleaning.py`.

---

## 8. APIs used (all free, no keys)

| API | Endpoint | Cadence |
|---|---|---|
| World Bank | `.../country/all/indicator/ST.INT.ARVL?format=json&date=2010:2023&per_page=20000` | one-off (CSV build) |
| World Bank | `.../country/all/indicator/ST.INT.RCPT.CD?format=json&date=2010:2023&per_page=20000` | one-off (CSV build) |
| OpenSky Network | `https://opensky-network.org/api/states/all?lamin=…&lamax=…&lomin=…&lomax=…` | every loop, per city |
| Open-Meteo | `https://api.open-meteo.com/v1/forecast?latitude=…&longitude=…&current=temperature_2m,precipitation,weather_code` | every loop, per city |
| Frankfurter | `https://api.frankfurter.app/latest?from=USD&to=EUR,INR,JPY,GBP,SGD,AED` | once per loop |

---

## 9. Viva / defence answers

**Why is `planes_in_box` a proxy?** OpenSky reports aircraft transponder
positions, not people. The box includes overflights, cargo, private and military
aircraft, and only aircraft visible to volunteer ADS-B receivers; tourists
arriving by train, car or ship are invisible. It signals air-traffic intensity,
which correlates with travel activity, but it cannot be converted into a tourist
count without assumptions.

**Why 120 seconds?** OpenSky refreshes state vectors roughly every 10–60 s and
anonymous access is rate-limited, so two minutes is near-live while staying
inside courtesy limits and producing 30 rows per hour per city. It is also
coarser than the 60 s dashboard refresh, so the UI shows genuinely new data
rather than re-reading the same row.

**Why SQLite?** It is in the Python standard library, needs no server, user or
password, and keeps the live history in one portable file. One writer and one
reader at this volume is exactly what SQLite handles best, and the file can be
inspected from the shell (`sqlite3 tourism.db …`), which keeps the project
reproducible on any Linux machine without extra infrastructure.
