# Near-Earth Asteroid Tracker

A simple web dashboard that shows which asteroids are passing close to Earth **this week**: how big they are, how close they get, and whether NASA classifies them as potentially hazardous. It's for students, teachers, and space fans who don't want to dig through raw NASA JSON.

Data comes from NASA's [NeoWs (Near Earth Object Web Service)](https://api.nasa.gov) API.

## Features

| # | Feature | What the user does | Request | Backend | Data |
|---|---------|--------------------|---------|---------|------|
| 1 | Asteroid list | Opens the home page | `GET /asteroids` | Refreshes this week from NASA if the cache is older than 6 h, then reads the week sorted by date | reads/writes `asteroids`, `fetch_log` |
| 2 | Hazard filter + details | Flips **Hazardous only**, or clicks **View details** | `GET /asteroids?hazardous=true`, `GET /asteroids/<id>` | Adds `is_potentially_hazardous = 1` to the query; or loads one asteroid and its orbit class | reads `asteroids`; reads/writes `asteroid_details` |
| 3 | Size comparison chart | Views the chart on the home page or detail page | `GET /asteroids/chart-data` | Top 5 largest asteroids this week + landmark heights, as JSON for Plotly | reads `asteroids` |

Potentially hazardous asteroids are highlighted in coral **and** labeled with a badge, so the meaning isn't carried by color alone.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your NASA key in .env
python app.py                      # open http://127.0.0.1:5000
```

Get a free NASA key at https://api.nasa.gov. `DEMO_KEY` works for testing but allows only about 30 requests per hour.

Useful extras:

- `flask --app app refresh`: pull this week from NASA right now.
- `http://localhost:5000/?today=2026-11-02`: show a different week (handy for demos).
- `python -m pytest -q`: run the tests. They use fake NASA data, so they work offline.

## How caching works (and what happens when NASA is down)

1. Every page reads from the database, never straight from NASA.
2. When the cached week is older than `CACHE_TTL_HOURS` (default 6), the next request fetches the week from NASA (one request, Monday–Sunday) and replaces that week's rows in a single transaction.
3. If NASA times out, rate-limits (HTTP 429), or errors, the app **keeps serving the last good copy** and shows a banner saying the data couldn't be refreshed. It waits 10 minutes before trying NASA again.
4. If there's no cached data at all *and* NASA is down, `/asteroids` returns HTTP 503 and the page shows a friendly message instead of crashing.

The tests in `tests/test_app.py` simulate an outage, a rate limit, and an empty cache.

## Project layout

```
app.py              Flask routes (the feature table above)
nasa.py             Calls NASA, parses the feed, manages the cache
db.py               SQLite locally / Postgres in production, plus the schema
templates/index.html  The page (Screen 1 list + Screen 2 detail)
static/app.js       Front end: fetches JSON, draws cards and the Plotly chart
static/style.css    Styles, including the mobile layout
tests/              Pytest tests with fake NASA data
render.yaml         Render deployment config
```

## Database

`asteroids`: one row per close approach. Key is `(id, close_approach_date)` because the same asteroid can pass more than once.
Columns: `id, name, estimated_diameter_min_m, estimated_diameter_max_m, is_potentially_hazardous, is_sentry_object, absolute_magnitude_h, close_approach_date, close_approach_epoch_ms, miss_distance_miles, miss_distance_lunar, relative_velocity_mph, orbiting_body, nasa_jpl_url, created_at`

`asteroid_details`: orbit class per asteroid. It isn't in NASA's weekly feed, so it's fetched from the lookup endpoint the first time someone opens that asteroid, then cached.

`fetch_log`: one row per week recording the last refresh attempt (time, success or failure, message).

No user accounts or personal data are collected.

## Deploy (Render + Supabase, free tiers)

1. **Supabase:** create a project, then copy the **Session pooler** connection string (Project → Connect). The app creates its tables on first start.
2. **Render:** New → Blueprint → select this repo (it reads `render.yaml`). Set `NASA_API_KEY` and `DATABASE_URL` when prompted.
3. Visit `/health` to confirm it's up.

Free-tier caveats: Render's free web services spin down after 15 minutes without traffic, so the first visit after a quiet period is slow. Supabase pauses free projects after a week of low activity; you can resume them from the dashboard.

## Roadmap (nice-to-haves)

- Interactive 3D orbit preview
- Export the approach schedule to Google Calendar (an `.ics` download needs no accounts)
- Email alerts for hazardous passes (would require collecting emails, which changes the "no personal data" promise)
