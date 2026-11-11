"""Talking to NASA's NeoWs API and keeping the local cache fresh.

The site never waits on NASA if it doesn't have to: every page is served from
the database, and NASA is only called when the cached week is older than
CACHE_TTL_HOURS. If NASA is slow, rate-limiting, or down, the app keeps
serving the last good copy and tells the user how old it is.
"""

import os
import threading
from datetime import date, datetime, timedelta, timezone

import requests

from db import connect

FEED_URL = "https://api.nasa.gov/neo/rest/v1/feed"
LOOKUP_URL = "https://api.nasa.gov/neo/rest/v1/neo/{id}"
TIMEOUT_SECONDS = 15

_refresh_lock = threading.Lock()


class NasaError(Exception):
    """NASA could not be reached or returned something unusable."""


def api_key():
    return os.environ.get("NASA_API_KEY") or "DEMO_KEY"


def cache_ttl():
    return timedelta(hours=float(os.environ.get("CACHE_TTL_HOURS", "6")))


def now_utc():
    return datetime.now(timezone.utc)


def week_bounds(today=None):
    """Monday..Sunday of the week containing `today`.

    NASA's feed allows at most 7 days per request, which is exactly one week.
    """
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _get(url, params):
    params = {**params, "api_key": api_key()}
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise NasaError(f"Could not reach NASA: {exc.__class__.__name__}") from exc
    if resp.status_code == 429:
        raise NasaError("NASA rate limit reached (HTTP 429). Using cached data.")
    if resp.status_code == 403:
        raise NasaError("NASA rejected the API key (HTTP 403). Check NASA_API_KEY.")
    if resp.status_code != 200:
        raise NasaError(f"NASA returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise NasaError("NASA returned a response that was not JSON.") from exc


def fetch_feed(start, end):
    return _get(FEED_URL, {"start_date": start.isoformat(), "end_date": end.isoformat()})


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_feed(payload, created_at=None):
    """Turn NASA's nested feed JSON into flat rows for the asteroids table."""
    created_at = created_at or now_utc().isoformat()
    rows = []
    for day, neos in (payload.get("near_earth_objects") or {}).items():
        for neo in neos:
            approaches = neo.get("close_approach_data") or []
            # The feed lists the approach for the requested date first; fall
            # back to matching by date just in case.
            approach = next(
                (a for a in approaches if a.get("close_approach_date") == day),
                approaches[0] if approaches else {},
            )
            diameter = (neo.get("estimated_diameter") or {}).get("meters") or {}
            rows.append({
                "id": str(neo["id"]),
                "name": (neo.get("name") or "").strip(),
                "estimated_diameter_min_m": _float(diameter.get("estimated_diameter_min")),
                "estimated_diameter_max_m": _float(diameter.get("estimated_diameter_max")),
                "is_potentially_hazardous": 1 if neo.get("is_potentially_hazardous_asteroid") else 0,
                "is_sentry_object": 1 if neo.get("is_sentry_object") else 0,
                "absolute_magnitude_h": _float(neo.get("absolute_magnitude_h")),
                "close_approach_date": approach.get("close_approach_date", day),
                "close_approach_epoch_ms": approach.get("epoch_date_close_approach"),
                "miss_distance_miles": _float((approach.get("miss_distance") or {}).get("miles")),
                "miss_distance_lunar": _float((approach.get("miss_distance") or {}).get("lunar")),
                "relative_velocity_mph": _float(
                    (approach.get("relative_velocity") or {}).get("miles_per_hour")
                ),
                "orbiting_body": approach.get("orbiting_body"),
                "nasa_jpl_url": neo.get("nasa_jpl_url"),
                "created_at": created_at,
            })
    return rows


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

COLUMNS = [
    "id", "name", "estimated_diameter_min_m", "estimated_diameter_max_m",
    "is_potentially_hazardous", "is_sentry_object", "absolute_magnitude_h",
    "close_approach_date", "close_approach_epoch_ms", "miss_distance_miles",
    "miss_distance_lunar", "relative_velocity_mph", "orbiting_body",
    "nasa_jpl_url", "created_at",
]


def store_week(conn, start, end, rows):
    """Replace the cached rows for one week in a single transaction."""
    conn.execute(
        "DELETE FROM asteroids WHERE close_approach_date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    )
    placeholders = ", ".join("?" for _ in COLUMNS)
    conn.executemany(
        f"INSERT INTO asteroids ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
        "ON CONFLICT (id, close_approach_date) DO NOTHING",
        [tuple(r[c] for c in COLUMNS) for r in rows],
    )


def _log_fetch(conn, start, ok, message):
    conn.execute(
        "INSERT INTO fetch_log (week_start, fetched_at, ok, message) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (week_start) DO UPDATE SET fetched_at = excluded.fetched_at, "
        "ok = excluded.ok, message = excluded.message",
        (start.isoformat(), now_utc().isoformat(), 1 if ok else 0, message),
    )


def last_success(conn, start):
    """When this week was last fetched successfully, or None."""
    row = conn.query_one(
        "SELECT fetched_at, ok, message FROM fetch_log WHERE week_start = ?",
        (start.isoformat(),),
    )
    return row


def cache_status(start):
    """Describe how fresh the cache is for the week starting `start`."""
    with connect() as conn:
        log = last_success(conn, start)
        count = conn.query_one(
            "SELECT COUNT(*) AS n FROM asteroids WHERE close_approach_date BETWEEN ? AND ?",
            (start.isoformat(), (start + timedelta(days=6)).isoformat()),
        )["n"]
        good = conn.query_one(
            "SELECT MAX(created_at) AS t FROM asteroids WHERE close_approach_date BETWEEN ? AND ?",
            (start.isoformat(), (start + timedelta(days=6)).isoformat()),
        )["t"]
    return {"rows": count, "last_updated": good, "last_attempt": log}


def refresh_week(start=None, force=False):
    """Make sure the cache holds a fresh copy of the week.

    Returns a dict describing what happened. Never raises for NASA problems:
    a failed refresh just leaves the previous data in place.
    """
    start = start or week_bounds()[0]
    end = start + timedelta(days=6)

    # Only one refresh runs at a time. If two requests arrive together, the
    # second waits here, then sees the fresh cache below and skips the fetch.
    if not _refresh_lock.acquire(timeout=TIMEOUT_SECONDS * 2):
        return {"refreshed": False, "reason": "refresh already in progress"}
    try:
        with connect() as conn:
            log = last_success(conn, start)
        if log and not force:
            age = now_utc() - datetime.fromisoformat(log["fetched_at"])
            if log["ok"] and age < cache_ttl():
                return {"refreshed": False, "reason": "cache is fresh"}
            if not log["ok"] and age < timedelta(minutes=10):
                # After a failure, wait a few minutes before trying NASA again.
                return {"refreshed": False, "reason": "recent failure, backing off",
                        "error": log["message"]}

        try:
            payload = fetch_feed(start, end)
            rows = parse_feed(payload)
        except NasaError as exc:
            with connect() as conn:
                _log_fetch(conn, start, False, str(exc))
            return {"refreshed": False, "reason": "nasa error", "error": str(exc)}
        except (KeyError, TypeError, AttributeError) as exc:
            msg = f"NASA response had an unexpected shape ({exc.__class__.__name__})."
            with connect() as conn:
                _log_fetch(conn, start, False, msg)
            return {"refreshed": False, "reason": "nasa error", "error": msg}
        with connect() as conn:
            store_week(conn, start, end, rows)
            _log_fetch(conn, start, True, f"{len(rows)} close approaches")
        return {"refreshed": True, "rows": len(rows)}
    finally:
        _refresh_lock.release()


# --------------------------------------------------------------------------
# Per-asteroid details (orbit class)
# --------------------------------------------------------------------------

def get_orbit_class(neo_id):
    """Orbit class for one asteroid, from cache or NASA's lookup endpoint.

    Returns (orbit_class, description) or (None, None) if unavailable.
    """
    with connect() as conn:
        row = conn.query_one(
            "SELECT orbit_class, orbit_class_desc FROM asteroid_details WHERE id = ?",
            (neo_id,),
        )
    if row:
        return row["orbit_class"], row["orbit_class_desc"]
    try:
        payload = _get(LOOKUP_URL.format(id=neo_id), {})
    except NasaError:
        return None, None
    orbit = (payload.get("orbital_data") or {}).get("orbit_class") or {}
    cls, desc = orbit.get("orbit_class_type"), orbit.get("orbit_class_description")
    with connect() as conn:
        conn.execute(
            "INSERT INTO asteroid_details (id, orbit_class, orbit_class_desc, fetched_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
            "orbit_class = excluded.orbit_class, orbit_class_desc = excluded.orbit_class_desc, "
            "fetched_at = excluded.fetched_at",
            (neo_id, cls, desc, now_utc().isoformat()),
        )
    return cls, desc
