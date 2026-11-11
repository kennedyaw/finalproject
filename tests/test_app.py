"""Tests run without internet: NASA's API is replaced by a fake that returns
sample data shaped exactly like the real NeoWs feed.

Run with:  python -m pytest -q
"""

import importlib

import pytest
import requests

TODAY = "2026-11-11"  # a Wednesday; its week is Mon 2026-11-09 .. Sun 2026-11-15


def neo(id_, name, dmin, dmax, hazardous, day, miles, mph, epoch):
    return {
        "id": id_,
        "name": name,
        "nasa_jpl_url": f"https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={id_}",
        "absolute_magnitude_h": 21.3,
        "estimated_diameter": {"meters": {"estimated_diameter_min": dmin, "estimated_diameter_max": dmax}},
        "is_potentially_hazardous_asteroid": hazardous,
        "is_sentry_object": False,
        "close_approach_data": [{
            "close_approach_date": day,
            "epoch_date_close_approach": epoch,
            "relative_velocity": {"miles_per_hour": str(mph)},
            "miss_distance": {"miles": str(miles), "lunar": str(miles / 238900)},
            "orbiting_body": "Earth",
        }],
    }


SAMPLE_FEED = {
    "element_count": 4,
    "near_earth_objects": {
        "2026-11-12": [
            neo("3542519", "(2010 PK9)", 120.0, 270.0, True, "2026-11-12", 4_500_000, 30_000, 1794500000000),
        ],
        "2026-11-10": [
            neo("2000433", "433 Eros (A898 PA)", 16000.0, 35000.0, False, "2026-11-10", 20_000_000, 12_000, 1794330000000),
            neo("3726710", "(2015 RC)", 30.0, 60.0, False, "2026-11-10", 900_000, 25_000, 1794300000000),
        ],
        "2026-11-14": [
            neo("54016742", "(2020 XY1)", 400.0, 900.0, True, "2026-11-14", 7_000_000, 41_000, 1794670000000),
        ],
    },
}

LOOKUP = {"orbital_data": {"orbit_class": {"orbit_class_type": "APO", "orbit_class_description": "Near-Earth asteroid orbits which cross the Earth's orbit"}}}


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def nasa_calls():
    return []


@pytest.fixture
def client(tmp_path, monkeypatch, nasa_calls):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    state = {"mode": "ok"}

    def fake_get(url, params=None, timeout=None):
        nasa_calls.append(url)
        if state["mode"] == "down":
            raise requests.ConnectionError("simulated outage")
        if state["mode"] == "429":
            return FakeResp(429, {})
        if "/feed" in url:
            return FakeResp(200, SAMPLE_FEED)
        return FakeResp(200, LOOKUP)

    monkeypatch.setattr(requests, "get", fake_get)
    import app as app_module
    importlib.reload(app_module)  # fresh app + fresh DB per test
    c = app_module.app.test_client()
    c.nasa_state = state
    return c


def get(client, path):
    sep = "&" if "?" in path else "?"
    return client.get(f"{path}{sep}today={TODAY}")


# ---------------- Feature 1 ----------------
def test_list_is_sorted_by_date(client):
    r = get(client, "/asteroids")
    assert r.status_code == 200
    data = r.get_json()
    dates = [a["close_approach_date"] for a in data["asteroids"]]
    assert dates == sorted(dates)
    assert len(dates) == 4
    assert data["meta"]["week_start"] == "2026-11-09"
    assert data["meta"]["week_end"] == "2026-11-15"
    # Within the same day, earlier approach time first
    assert data["asteroids"][0]["name"] == "(2015 RC)"


def test_second_request_uses_cache(client, nasa_calls):
    get(client, "/asteroids")
    get(client, "/asteroids")
    get(client, "/asteroids/chart-data")
    assert len([u for u in nasa_calls if "/feed" in u]) == 1


# ---------------- Feature 2 ----------------
def test_hazardous_filter(client):
    data = get(client, "/asteroids?hazardous=true").get_json()
    assert {a["name"] for a in data["asteroids"]} == {"(2010 PK9)", "(2020 XY1)"}
    assert all(a["is_potentially_hazardous"] is True for a in data["asteroids"])


def test_detail(client):
    get(client, "/asteroids")
    r = get(client, "/asteroids/3542519")
    assert r.status_code == 200
    a = r.get_json()
    assert a["name"] == "(2010 PK9)"
    assert a["relative_velocity_mph"] == 30000
    assert a["orbit_class"] == "APO"


def test_detail_unknown_and_bad_ids(client):
    get(client, "/asteroids")
    assert get(client, "/asteroids/999").status_code == 404
    assert get(client, "/asteroids/abc").status_code == 400


# ---------------- Feature 3 ----------------
def test_chart_data(client):
    data = get(client, "/asteroids/chart-data").get_json()
    names = [a["name"] for a in data["asteroids"]]
    assert names[0] == "433 Eros (A898 PA)"  # largest first
    assert len(names) == 4  # only 4 in sample week (limit is 5)
    assert any("Statue of Liberty" in l["name"] for l in data["landmarks"])
    eros = data["asteroids"][0]
    assert eros["min_m"] <= eros["avg_m"] <= eros["max_m"]


# ---------------- Resilience (the "scariest part") ----------------
def test_nasa_down_with_cache_serves_stale_data(client, monkeypatch):
    get(client, "/asteroids")                      # fill the cache
    monkeypatch.setenv("CACHE_TTL_HOURS", "0")     # force the cache to look old
    client.nasa_state["mode"] = "down"
    r = get(client, "/asteroids")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["asteroids"]) == 4             # still have data
    assert "Could not reach NASA" in data["meta"]["warning"]


def test_nasa_down_with_empty_cache_returns_503(client):
    client.nasa_state["mode"] = "down"
    r = get(client, "/asteroids")
    assert r.status_code == 503
    assert r.get_json()["asteroids"] == []


def test_rate_limited(client):
    client.nasa_state["mode"] = "429"
    r = get(client, "/asteroids")
    assert "rate limit" in r.get_json()["meta"]["warning"]


def test_backs_off_after_failure(client, nasa_calls):
    client.nasa_state["mode"] = "down"
    get(client, "/asteroids")
    get(client, "/asteroids")
    assert len(nasa_calls) == 1  # didn't retry immediately


def test_homepage_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Near-Earth Asteroid Tracker" in r.data
