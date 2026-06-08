"""
Live OpenAQ v3 client for the dashboard.

The notebook + gen_network.py pull from OpenAQ offline and write static files
(dashboard_data.csv, network.json). This module lets the running Flask app read
the *current* reading straight from OpenAQ v3 instead of only serving that frozen
snapshot, so the "Current AQI" hero and the "City sensor network" map reflect
real-time conditions.

Design notes:
  * Stale-while-revalidate cache. A request never blocks on the network: it gets
    the last good value immediately (or None on a cold start) and a background
    thread refreshes the cache when it's older than AQI_LIVE_TTL. If OpenAQ is
    unreachable, the cache simply keeps its last value and app.py falls back to
    the file snapshot — so the dashboard never breaks, it just goes stale.
  * The EPA breakpoint tables / NODES / PARAMS mirror gen_network.py on purpose:
    this module is import-safe (no work at import time), whereas importing
    gen_network would run its batch fetch. Keep the two in sync if you edit one.

Env:
  OPENAQ_API_KEY   API key (falls back to the project key so it runs out-of-box).
  AQI_LIVE         set to "0" to disable live fetching (serve files only).
  AQI_LIVE_TTL     cache lifetime in seconds (default 600 = 10 min).
"""

import os
import time
import math
import threading
import datetime as dt

import requests

BASE = "https://api.openaq.org/v3"
API_KEY = os.environ.get(
    "OPENAQ_API_KEY",
    "17803b50a1dac87cafdb7056be122a9e9ea8d1e8abef8df48f3183c8fb2a8d1c",
)
HEADERS = {"X-API-Key": API_KEY}

ENABLED = os.environ.get("AQI_LIVE", "1") != "0"
TTL = int(os.environ.get("AQI_LIVE_TTL", "600"))
TIMEOUT = int(os.environ.get("AQI_LIVE_TIMEOUT", "12"))

# Bangladesh Standard Time (UTC+6, no DST) — all timestamps are shown in BST.
BD_TZ = dt.timezone(dt.timedelta(hours=6))
BD_LABEL = "BST"


def fmt_bd(when, suffix=True):
    """Format a tz-aware datetime/Timestamp in Bangladesh Standard Time."""
    s = when.astimezone(BD_TZ).strftime("%Y-%m-%d %H:%M")
    return f"{s} {BD_LABEL}" if suffix else s

# (location_id, area, is_primary) — mirrors gen_network.py.
NODES = [
    (6157905, "Uttara",     True),
    (6234363, "Gulshan",    False),
    (6242232, "Baridhara",  False),
    (6240773, "Badda",      False),
    (6251395, "Mirpur",     False),
    (6242079, "Moghbazar",  False),
    (6240023, "Dhanmondi",  False),
    (6236590, "Hazaribagh", False),
]
PALETTE = ["#45c4b0", "#f4a259", "#5aa9e6", "#c98bdb",
           "#e6688a", "#8ad17d", "#e0c341", "#7aa2c4"]
PRIMARY_LID, PRIMARY_AREA, PRIMARY_COLOR = 6157905, "Uttara", PALETTE[0]

CATEGORIES = [
    (0, 50, "Good", "#00897b"), (51, 100, "Moderate", "#f9a825"),
    (101, 150, "Unhealthy for Sensitive Groups", "#ef6c00"),
    (151, 200, "Unhealthy", "#d32f2f"), (201, 300, "Very Unhealthy", "#7b1fa2"),
    (301, 500, "Hazardous", "#6d1b2e"),
]

# US-EPA AQI breakpoints per pollutant: key -> (expected_unit, decimals, [(Clo,Chi,Ilo,Ihi)..])
AQI_BP = {
    "pm25": ("µg/m³", 1, [(0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
                          (55.5, 125.4, 151, 200), (125.5, 225.4, 201, 300), (225.5, 325.4, 301, 500)]),
    "pm10": ("µg/m³", 0, [(0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
                          (255, 354, 151, 200), (355, 424, 201, 300), (425, 604, 301, 500)]),
    "o3":   ("ppb",   0, [(0, 54, 0, 50), (55, 70, 51, 100), (71, 85, 101, 150),
                          (86, 105, 151, 200), (106, 200, 201, 300)]),
    "no2":  ("ppb",   0, [(0, 53, 0, 50), (54, 100, 51, 100), (101, 360, 101, 150),
                          (361, 649, 151, 200), (650, 1249, 201, 300), (1250, 2049, 301, 500)]),
    "so2":  ("ppb",   0, [(0, 35, 0, 50), (36, 75, 51, 100), (76, 185, 101, 150),
                          (186, 304, 151, 200), (305, 604, 201, 300), (605, 1004, 301, 500)]),
    "co":   ("ppm",   1, [(0.0, 4.4, 0, 50), (4.5, 9.4, 51, 100), (9.5, 12.4, 101, 150),
                          (12.5, 15.4, 151, 200), (15.5, 30.4, 201, 300), (30.5, 50.4, 301, 500)]),
}

# OpenAQ parameter name -> (output key, label, kind)
PARAMS = {
    "pm25":             ("pm25",        "PM2.5",    "pollutant"),
    "pm10":             ("pm10",        "PM10",     "pollutant"),
    "pm1":              ("pm1",         "PM1",      "pollutant"),
    "o3":               ("o3",          "O₃",       "pollutant"),
    "no2":              ("no2",         "NO₂",      "pollutant"),
    "so2":              ("so2",         "SO₂",      "pollutant"),
    "co":               ("co",          "CO",       "pollutant"),
    "temperature":      ("temperature", "Temp",     "environment"),
    "relativehumidity": ("humidity",    "Humidity", "environment"),
}
ORDER = ["pm25", "pm10", "pm1", "o3", "no2", "so2", "co", "temperature", "humidity"]
UNIT_FIX = {"c": "°C", "ug/m3": "µg/m³", "f": "°F"}


def sub_index(key, value, unit):
    """US-EPA AQI sub-index for one reading, or None if not applicable."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    spec = AQI_BP.get(key)
    if not spec:
        return None
    exp_unit, dec, bp = spec
    if unit and unit.replace("ug/m3", "µg/m³") != exp_unit:
        return None  # reported in a unit our breakpoints don't cover — don't guess
    c = math.floor(value * (10 ** dec)) / (10 ** dec)
    for clo, chi, ilo, ihi in bp:
        if clo <= c <= chi:
            return int(round((ihi - ilo) / (chi - clo) * (c - clo) + ilo))
    return 500


def category(aqi):
    if aqi is None:
        return {"label": "Unknown", "color": "#888"}
    for lo, hi, lab, col in CATEGORIES:
        if lo <= aqi <= hi:
            return {"label": lab, "color": col}
    return {"label": "Hazardous", "color": "#6d1b2e"}


# --------------------------- OpenAQ HTTP ------------------------------------
def _get(path, params=None):
    for a in range(4):
        r = requests.get(BASE + path, headers=HEADERS, params=params, timeout=TIMEOUT)
        if r.status_code == 429:
            time.sleep(min(int(r.headers.get("Retry-After", 2 ** a)), 8))
            continue
        r.raise_for_status()
        return r.json()
    return {}


def _parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# location metadata (name, coords, sensor->parameter map) changes rarely → cache a day
_loc_cache = {}  # lid -> (ts, meta)


def _loc_meta(lid):
    ent = _loc_cache.get(lid)
    if ent and time.time() - ent[0] < 86400:
        return ent[1]
    results = _get(f"/locations/{lid}").get("results", [])
    if not results:
        return None
    loc = results[0]
    co = loc.get("coordinates") or {}
    sensors = {}
    for s in loc.get("sensors", []):
        pname = (s.get("parameter") or {}).get("name")
        if pname not in PARAMS:
            continue
        key, label, kind = PARAMS[pname]
        unit = (s.get("parameter") or {}).get("units")
        sensors[s["id"]] = (key, label, kind, unit)
    meta = {"name": loc.get("name"), "lat": co.get("latitude"),
            "lon": co.get("longitude"), "sensors": sensors}
    _loc_cache[lid] = (time.time(), meta)
    return meta


def node_latest(lid, area, primary, color):
    """Current reading for one location, shaped exactly like a gen_network node."""
    meta = _loc_meta(lid)
    if not meta:
        return None
    sensors = meta["sensors"]
    results = _get(f"/locations/{lid}/latest").get("results", [])
    if not results:
        return None

    readings, latest_time = [], None
    for it in results:
        sm = sensors.get(it.get("sensorsId"))
        if not sm:
            continue
        key, label, kind, unit = sm
        val = it.get("value")
        if val is None:
            continue
        val = float(val)
        if kind == "pollutant" and not (0 <= val < 100000):
            continue  # clip implausible particulate/gas readings
        ts = _parse_ts((it.get("datetime") or {}).get("utc"))
        if ts is not None:
            latest_time = ts if latest_time is None else max(latest_time, ts)
        readings.append({
            "key": key, "label": label, "kind": kind,
            "unit": UNIT_FIX.get(unit, unit),
            "value": round(val, 1),
            "aqi": sub_index(key, val, unit),
        })

    pm = next((r for r in readings if r["key"] == "pm25"), None)
    if not readings or pm is None:
        return None

    scored = [r for r in readings if r["aqi"] is not None]
    if scored:
        worst = max(scored, key=lambda r: r["aqi"])
        node_aqi, dominant = worst["aqi"], worst["label"]
    else:
        node_aqi, dominant = None, None

    readings.sort(key=lambda r: ORDER.index(r["key"]) if r["key"] in ORDER else 99)
    return {
        "id": lid, "area": area, "name": meta.get("name"),
        "primary": primary, "color": color,
        "lat": meta.get("lat"), "lon": meta.get("lon"),
        "latest_time": fmt_bd(latest_time) if latest_time else "",
        "pm25": pm["value"], "aqi": node_aqi, "category": category(node_aqi),
        "dominant": dominant, "readings": readings, "n_points": len(readings),
    }


# ----------------------- producers (the slow work) --------------------------
def _producer_primary():
    n = node_latest(PRIMARY_LID, PRIMARY_AREA, True, PRIMARY_COLOR)
    if not n:
        return None
    return {
        "latest_time": n["latest_time"],
        "pm25": n.get("pm25"),
        "aqi": n.get("aqi"),
        "category": n.get("category"),
        "now": {r["key"]: r["value"] for r in n["readings"]},
    }


def _producer_network(base):
    base_nodes = (base or {}).get("nodes") or []
    if base_nodes:
        targets = [(n["id"], n["area"], n.get("primary", False), n.get("color"))
                   for n in base_nodes]
    else:
        targets = [(lid, area, pri, col)
                   for (lid, area, pri), col in zip(NODES, PALETTE)]

    fresh, got_live = [], False
    for lid, area, primary, color in targets:
        node = None
        try:
            node = node_latest(lid, area, primary, color)
        except requests.RequestException:
            node = None
        if node:
            got_live = True
            fresh.append(node)
        else:  # keep the cached node for this location if we have one
            cached = next((x for x in base_nodes if x.get("id") == lid), None)
            if cached:
                fresh.append(cached)

    if not got_live:
        return None  # nothing refreshed → let app.py serve the file snapshot

    out = dict(base) if base else {"city": "Dhaka"}
    out["nodes"] = sorted(fresh, key=lambda n: (n.get("aqi") is None, -(n.get("aqi") or 0)))
    out["node_count"] = len(fresh)
    out["pollutants"] = sorted(
        {r["key"] for n in fresh for r in n.get("readings", []) if r.get("kind") == "pollutant"},
        key=lambda k: ORDER.index(k) if k in ORDER else 99)
    out["generated_at"] = fmt_bd(dt.datetime.now(dt.timezone.utc))
    out["live"] = True
    return out  # comparisons/comparison (if present in base) are carried through


# ------------------- stale-while-revalidate cache ---------------------------
_cache = {
    "primary": {"value": None, "ts": 0.0, "busy": False},
    "network": {"value": None, "ts": 0.0, "busy": False},
}
_locks = {"primary": threading.Lock(), "network": threading.Lock()}


def _refresh(name, producer, args):
    c = _cache[name]
    try:
        val = producer(*args)
        if val is not None:
            c["value"], c["ts"] = val, time.time()
    except Exception:  # network/parse error → keep last good value
        pass
    finally:
        c["busy"] = False


def _swr(name, producer, *args):
    """Return the last good value now; kick off a background refresh if stale."""
    if not ENABLED:
        return None
    c = _cache[name]
    if c["value"] is not None and (time.time() - c["ts"]) < TTL:
        return c["value"]
    with _locks[name]:
        if not c["busy"]:
            c["busy"] = True
            threading.Thread(target=_refresh, args=(name, producer, args),
                             daemon=True).start()
    return c["value"]  # last good value, or None on a cold start


def fetch_primary():
    """Live current reading for the primary node, or None if unavailable yet."""
    return _swr("primary", _producer_primary)


def fetch_network(base):
    """Live city snapshot (latest per node) merged onto `base`, or None."""
    return _swr("network", _producer_network, base)


# ------------------- per-area history (for forecasting) ---------------------
def node_by_area(area):
    """Resolve an area name to (location_id, name, is_primary, color), or None."""
    a = (area or "").strip().lower()
    for (lid, name, primary), color in zip(NODES, PALETTE):
        if name.lower() == a:
            return (lid, name, primary, color)
    return None


def pm25_sensor_id(lid):
    meta = _loc_meta(lid)
    if not meta:
        return None
    for sid, (key, _label, _kind, _unit) in meta["sensors"].items():
        if key == "pm25":
            return sid
    return None


_series_cache = {}  # lid -> (ts, rows)


def fetch_pm25_hours(lid, hours=96):
    """Recent hourly PM2.5 rows [{datetime, value}, ...] for a location, or None.

    Cached per location for AQI_LIVE_TTL so changing the forecast horizon doesn't
    re-hit OpenAQ. Used to forecast areas other than the primary node.
    """
    if not ENABLED:
        return None
    ent = _series_cache.get(lid)
    if ent and (time.time() - ent[0]) < TTL:
        return ent[1]
    sid = pm25_sensor_id(lid)
    if not sid:
        return None
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, page = [], 1
    while True:
        data = _get(f"/sensors/{sid}/hours",
                    {"datetime_from": since, "limit": 1000, "page": page}).get("results", [])
        for it in data:
            ts = ((it.get("period") or {}).get("datetimeFrom") or {}).get("utc")
            rows.append({"datetime": ts, "value": it.get("value")})
        if len(data) < 1000:
            break
        page += 1
    if not rows:
        return None
    _series_cache[lid] = (time.time(), rows)
    return rows
