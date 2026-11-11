"""Database access for the asteroid tracker.

Uses SQLite locally and Postgres (e.g. Supabase) in production. Which one is
chosen depends on the DATABASE_URL environment variable:

    (unset)                         -> SQLite file at ./asteroids.db
    sqlite:///path/to/file.db       -> SQLite file at that path
    postgresql://user:pass@host/db  -> Postgres via psycopg 3

All SQL in this project is written with "?" placeholders and in a dialect that
both SQLite and Postgres understand (including INSERT ... ON CONFLICT), so the
only thing this module has to translate is the placeholder style.
"""

import os
import sqlite3
from contextlib import contextmanager

SCHEMA = [
    # One row per close approach. The same asteroid can appear on more than
    # one date, so the key is (id, close_approach_date), not id alone.
    """
    CREATE TABLE IF NOT EXISTS asteroids (
        id                         TEXT NOT NULL,
        name                       TEXT NOT NULL,
        estimated_diameter_min_m   REAL,
        estimated_diameter_max_m   REAL,
        is_potentially_hazardous   INTEGER NOT NULL DEFAULT 0,
        is_sentry_object           INTEGER NOT NULL DEFAULT 0,
        absolute_magnitude_h       REAL,
        close_approach_date        TEXT NOT NULL,
        close_approach_epoch_ms    BIGINT,
        miss_distance_miles        REAL,
        miss_distance_lunar        REAL,
        relative_velocity_mph      REAL,
        orbiting_body              TEXT,
        nasa_jpl_url               TEXT,
        created_at                 TEXT NOT NULL,
        PRIMARY KEY (id, close_approach_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_asteroids_date ON asteroids (close_approach_date)",
    # Orbit class is not in the weekly feed; it comes from the per-asteroid
    # lookup endpoint, fetched the first time someone opens the detail view.
    """
    CREATE TABLE IF NOT EXISTS asteroid_details (
        id               TEXT PRIMARY KEY,
        orbit_class      TEXT,
        orbit_class_desc TEXT,
        fetched_at       TEXT NOT NULL
    )
    """,
    # Records every attempt to refresh a week from NASA, so the app knows how
    # fresh its cache is and can show "last updated" to users.
    """
    CREATE TABLE IF NOT EXISTS fetch_log (
        week_start  TEXT PRIMARY KEY,
        fetched_at  TEXT NOT NULL,
        ok          INTEGER NOT NULL,
        message     TEXT
    )
    """,
]


def _database_url():
    return os.environ.get("DATABASE_URL", "").strip()


def is_postgres(url=None):
    url = _database_url() if url is None else url
    return url.startswith(("postgres://", "postgresql://"))


def _sqlite_path(url):
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    return os.environ.get("SQLITE_PATH", "asteroids.db")


class _Conn:
    """Small wrapper so the rest of the app can use "?" placeholders and
    get rows back as dicts, whichever database is underneath."""

    def __init__(self, raw, pg):
        self.raw = raw
        self.pg = pg

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), params)
        return cur

    def executemany(self, sql, seq):
        cur = self.raw.cursor()
        cur.executemany(self._sql(sql), seq)
        return cur

    def query(self, sql, params=()):
        cur = self.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


@contextmanager
def connect():
    """Open a connection, commit on success, roll back on error."""
    url = _database_url()
    if is_postgres(url):
        import psycopg  # only needed in production

        raw = psycopg.connect(url, connect_timeout=10)
        conn = _Conn(raw, pg=True)
    else:
        raw = sqlite3.connect(_sqlite_path(url), timeout=10)
        conn = _Conn(raw, pg=False)
    try:
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def init_db():
    with connect() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
