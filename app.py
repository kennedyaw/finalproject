"""Near-Earth Asteroid Tracker: Flask app.

Routes (matching the feature table in the proposal):

    GET /                          the dashboard page (HTML)
    GET /asteroids                 Feature 1: this week's close approaches, sorted by date
    GET /asteroids?hazardous=true  Feature 2: only potentially hazardous asteroids
    GET /asteroids/<id>            Feature 2: details for one asteroid
    GET /asteroids/chart-data      Feature 3: top 5 largest this week + landmarks, for Plotly
    GET /health                    quick check for Render
"""

import os
from datetime import date

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()  # reads NASA_API_KEY / DATABASE_URL from a local .env file

import nasa  # noqa: E402  (must come after load_dotenv)
from db import connect, init_db  # noqa: E402

# Heights of well-known landmarks, in meters, for the size comparison chart.
LANDMARKS = [
    {"name": "Statue of Liberty (ground to torch)", "meters": 93},
    {"name": "Football field (end zone to end zone)", "meters": 110},
    {"name": "Eiffel Tower", "meters": 330},
    {"name": "Empire State Building (to tip)", "meters": 443},
]


def create_app():
    app = Flask(__name__)
    init_db()

    def current_week():
        """Monday..Sunday of this week. ?today=YYYY-MM-DD overrides "today" for demos/tests."""
        override = request.args.get("today")
        try:
            today = date.fromisoformat(override) if override else None
        except ValueError:
            today = None
        return nasa.week_bounds(today)

    def ensure_fresh(start):
        """Refresh from NASA if the cache is old. Returns a warning string or None."""
        result = nasa.refresh_week(start)
        return result.get("error")

    def meta(start, end, warning):
        status = nasa.cache_status(start)
        return {
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "last_updated": status["last_updated"],
            "warning": warning,
        }

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------------- Feature 1 + 2: list, with optional hazard filter ----------------
    @app.get("/asteroids")
    def list_asteroids():
        start, end = current_week()
        warning = ensure_fresh(start)

        sql = (
            "SELECT * FROM asteroids WHERE close_approach_date BETWEEN ? AND ?"
        )
        params = [start.isoformat(), end.isoformat()]
        if request.args.get("hazardous", "").lower() in ("true", "1", "yes"):
            sql += " AND is_potentially_hazardous = 1"
        sql += " ORDER BY close_approach_date, close_approach_epoch_ms"

        with connect() as conn:
            rows = conn.query(sql, params)

        body = {"meta": meta(start, end, warning), "asteroids": [to_json(r) for r in rows]}
        if not rows and warning and body["meta"]["last_updated"] is None:
            # NASA is down AND we have nothing cached: tell the page clearly.
            return jsonify(body), 503
        return jsonify(body)

    # ---------------- Feature 3: chart data ----------------
    # Defined before /asteroids/<neo_id> for readability; Flask matches the
    # fixed path "chart-data" ahead of the variable one either way.
    @app.get("/asteroids/chart-data")
    def chart_data():
        start, end = current_week()
        warning = ensure_fresh(start)
        with connect() as conn:
            # Same asteroid can pass twice in a week; count it once.
            rows = conn.query(
                "SELECT id, name, MAX(estimated_diameter_min_m) AS dmin, "
                "MAX(estimated_diameter_max_m) AS dmax, MAX(is_potentially_hazardous) AS hz "
                "FROM asteroids WHERE close_approach_date BETWEEN ? AND ? "
                "GROUP BY id, name ORDER BY dmax DESC LIMIT 5",
                (start.isoformat(), end.isoformat()),
            )
            # The detail page asks for its asteroid to be included even if it isn't top 5.
            extra_id = request.args.get("include")
            if extra_id and extra_id not in {r["id"] for r in rows}:
                extra = conn.query_one(
                    "SELECT id, name, estimated_diameter_min_m AS dmin, "
                    "estimated_diameter_max_m AS dmax, is_potentially_hazardous AS hz "
                    "FROM asteroids WHERE id = ? ORDER BY close_approach_date LIMIT 1",
                    (extra_id,),
                )
                if extra:
                    rows.append(extra)

        asteroids = [
            {
                "id": r["id"],
                "name": r["name"],
                "min_m": round(r["dmin"] or 0, 1),
                "max_m": round(r["dmax"] or 0, 1),
                "avg_m": round(((r["dmin"] or 0) + (r["dmax"] or 0)) / 2, 1),
                "hazardous": bool(r["hz"]),
            }
            for r in rows
        ]
        return jsonify({
            "meta": meta(start, end, warning),
            "asteroids": asteroids,
            "landmarks": LANDMARKS,
        })

    # ---------------- Feature 2: detail ----------------
    @app.get("/asteroids/<neo_id>")
    def asteroid_detail(neo_id):
        if not neo_id.isdigit():
            return jsonify({"error": "Asteroid id should be a number."}), 400
        start, end = current_week()
        with connect() as conn:
            approaches = conn.query(
                "SELECT * FROM asteroids WHERE id = ? ORDER BY close_approach_date",
                (neo_id,),
            )
        if not approaches:
            return jsonify({"error": "We don't have that asteroid in this week's data."}), 404

        # Prefer an approach inside the current week if there are several.
        this_week = [a for a in approaches
                     if start.isoformat() <= a["close_approach_date"] <= end.isoformat()]
        record = to_json((this_week or approaches)[0])

        orbit_class, orbit_desc = nasa.get_orbit_class(neo_id)
        record["orbit_class"] = orbit_class
        record["orbit_class_description"] = orbit_desc
        record["approaches"] = [
            {"date": a["close_approach_date"], "miss_distance_miles": a["miss_distance_miles"]}
            for a in approaches
        ]
        return jsonify(record)

    return app


def to_json(row):
    """Database row -> JSON the front end uses (booleans instead of 0/1)."""
    out = dict(row)
    out["is_potentially_hazardous"] = bool(out.get("is_potentially_hazardous"))
    out["is_sentry_object"] = bool(out.get("is_sentry_object"))
    return out


app = create_app()


@app.cli.command("refresh")
def refresh_command():
    """flask --app app refresh : pull this week from NASA right now."""
    print(nasa.refresh_week(force=True))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
