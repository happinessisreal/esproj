"""
Build network.json — a live, MULTI-POLLUTANT snapshot from several deployed IoT
nodes across Dhaka (not a single point, and not a single pollutant).

For every node it records the area, coordinates, and the latest real-time reading
for *every* parameter the node exposes — PM2.5, PM10, PM1, and (where the station
carries them) O3, NO2, SO2, CO, plus the temperature/humidity environment
channels. For each pollutant with a US-EPA breakpoint table it computes the AQI
sub-index; the node's headline AQI is the *worst* sub-index across pollutants
(this is exactly how the EPA defines AQI — the dominant pollutant wins), and the
dominant pollutant is recorded. It also keeps a 30-day daily-mean series per
pollutant so the dashboard can compare any pollutant across areas. Re-runnable.
Nothing is simulated — if a station doesn't measure a pollutant, it simply isn't
listed for that node.

Usage:
    OPENAQ_API_KEY=... python gen_network.py
"""
import os, time, json, datetime as dt
import numpy as np
import pandas as pd
import requests

H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
BASE = "https://api.openaq.org/v3"

# (location_id, area name it covers, is_primary)  — the node the ML model uses.
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

CATEGORIES = [
    (0, 50, "Good", "#00897b"), (51, 100, "Moderate", "#f9a825"),
    (101, 150, "Unhealthy for Sensitive Groups", "#ef6c00"),
    (151, 200, "Unhealthy", "#d32f2f"), (201, 300, "Very Unhealthy", "#7b1fa2"),
    (301, 500, "Hazardous", "#6d1b2e"),
]

# --- US-EPA AQI breakpoints per pollutant (concentration -> sub-index) --------
# Each entry: parameter -> (expected_unit, truncation_decimals, [(Clo,Chi,Ilo,Ihi)..])
# PM2.5 uses the 2024-updated breakpoints (matches the rest of the project).
# Gaseous sub-indices are only computed when the station reports the pollutant in
# the unit the EPA table expects (ppb/ppm) — we never guess a µg/m³<->ppb
# conversion, so a gas reported in µg/m³ is shown raw with no (possibly wrong) AQI.
AQI_BP = {
    "pm25": ("µg/m³", 1, [(0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
                          (55.5, 125.4, 151, 200), (125.5, 225.4, 201, 300), (225.5, 325.4, 301, 500)]),
    "pm10": ("µg/m³", 0, [(0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
                          (255, 354, 151, 200), (355, 424, 201, 300), (425, 604, 301, 500)]),
    "o3":   ("ppb",   0, [(0, 54, 0, 50), (55, 70, 51, 100), (71, 85, 101, 150),
                          (86, 105, 151, 200), (106, 200, 201, 300)]),               # 8-hour
    "no2":  ("ppb",   0, [(0, 53, 0, 50), (54, 100, 51, 100), (101, 360, 101, 150),
                          (361, 649, 151, 200), (650, 1249, 201, 300), (1250, 2049, 301, 500)]),
    "so2":  ("ppb",   0, [(0, 35, 0, 50), (36, 75, 51, 100), (76, 185, 101, 150),
                          (186, 304, 151, 200), (305, 604, 201, 300), (605, 1004, 301, 500)]),
    "co":   ("ppm",   1, [(0.0, 4.4, 0, 50), (4.5, 9.4, 51, 100), (9.5, 12.4, 101, 150),
                          (12.5, 15.4, 151, 200), (15.5, 30.4, 201, 300), (30.5, 50.4, 301, 500)]),
}

# OpenAQ parameter name -> (output key, label, kind). "pollutant" channels are
# shown in the AQI strip; "environment" channels (temp/humidity) are shown apart.
PARAMS = {
    "pm25":            ("pm25",        "PM2.5", "pollutant"),
    "pm10":            ("pm10",        "PM10",  "pollutant"),
    "pm1":             ("pm1",         "PM1",   "pollutant"),
    "o3":              ("o3",          "O₃",    "pollutant"),
    "no2":             ("no2",         "NO₂",   "pollutant"),
    "so2":             ("so2",         "SO₂",   "pollutant"),
    "co":              ("co",          "CO",    "pollutant"),
    "temperature":     ("temperature", "Temp",  "environment"),
    "relativehumidity":("humidity",    "Humidity", "environment"),
}
# stable display order for a node's readings
ORDER = ["pm25", "pm10", "pm1", "o3", "no2", "so2", "co", "temperature", "humidity"]
# tidy a few raw OpenAQ unit strings for display
UNIT_FIX = {"c": "°C", "ug/m3": "µg/m³", "f": "°F"}


def sub_index(key, value, unit):
    """US-EPA AQI sub-index for one pollutant reading, or None if not applicable."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    spec = AQI_BP.get(key)
    if not spec:
        return None
    exp_unit, dec, bp = spec
    if unit and unit.replace("ug/m3", "µg/m³") != exp_unit:
        return None  # reported in a unit our breakpoints don't cover — don't guess
    c = np.floor(value * (10 ** dec)) / (10 ** dec)
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


def get(path, params=None):
    for a in range(6):
        r = requests.get(BASE + path, headers=H, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 2 ** a))); continue
        r.raise_for_status(); return r.json()
    raise SystemExit("rate-limited")


def download_series(sensor_id, since):
    """Hourly series for one sensor over the window [since, now]."""
    rows, page = [], 1
    while True:
        data = get(f"/sensors/{sensor_id}/hours",
                   {"datetime_from": since, "limit": 1000, "page": page}).get("results", [])
        for it in data:
            ts = ((it.get("period") or {}).get("datetimeFrom") or {}).get("utc")
            rows.append({"datetime": ts, "value": it.get("value")})
        if len(data) < 1000:
            break
        page += 1; time.sleep(0.2)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.Series(dtype=float)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    return df.dropna(subset=["datetime"]).set_index("datetime")["value"].sort_index()


since = (dt.date.today() - dt.timedelta(days=30)).isoformat()
nodes = []
# comparisons[param]["Area"] = daily-mean Series  (built per pollutant)
comparisons = {}

for (lid, area, primary), color in zip(NODES, PALETTE):
    loc = get(f"/locations/{lid}")["results"][0]
    co = loc.get("coordinates") or {}

    readings, latest_time, n_points = [], None, 0
    for s in loc.get("sensors", []):
        pname = (s.get("parameter") or {}).get("name")
        if pname not in PARAMS:
            continue
        key, label, kind = PARAMS[pname]
        unit = (s.get("parameter") or {}).get("units")

        ser = download_series(s["id"], since)
        if kind == "pollutant":  # clip implausible particulate/gas readings
            ser = ser[(ser >= 0) & (ser < 100000)]
        ser = ser.dropna()
        if ser.empty:
            continue

        last = ser.iloc[-1]
        ts = ser.index[-1]
        latest_time = max(latest_time, ts) if latest_time is not None else ts
        n_points = max(n_points, len(ser))

        readings.append({
            "key": key, "label": label, "kind": kind,
            "unit": UNIT_FIX.get(unit, unit),
            "value": round(float(last), 1),
            "aqi": sub_index(key, float(last), unit),
        })

        # keep a daily-mean series for the cross-area comparison chart
        if kind == "pollutant":
            comparisons.setdefault(key, {})[area] = ser.resample("1D").mean()

    pm = next((r for r in readings if r["key"] == "pm25"), None)
    if not readings or pm is None:
        print(f"  skip {area}: no usable pollutant data"); continue

    # headline AQI = worst pollutant sub-index (EPA dominant-pollutant rule)
    scored = [r for r in readings if r["aqi"] is not None]
    if scored:
        worst = max(scored, key=lambda r: r["aqi"])
        node_aqi, dominant = worst["aqi"], worst["label"]
    else:
        node_aqi, dominant = None, None

    readings.sort(key=lambda r: ORDER.index(r["key"]) if r["key"] in ORDER else 99)

    nodes.append({
        "id": lid, "area": area, "name": loc.get("name"),
        "primary": primary, "color": color,
        "lat": co.get("latitude"), "lon": co.get("longitude"),
        "latest_time": latest_time.strftime("%Y-%m-%d %H:%M UTC"),
        "pm25": pm["value"],                 # back-compat headline particulate
        "aqi": node_aqi, "category": category(node_aqi),
        "dominant": dominant,
        "readings": readings,
        "n_points": n_points,
    })
    chips = "  ".join(f"{r['label']}={r['value']}" for r in readings)
    print(f"  {area:11s} AQI={str(node_aqi):>3} ({dominant})  {chips}")

# --- aligned daily matrices for the comparison chart, one block per pollutant -
comp_out = {}
for key, by_area in comparisons.items():
    all_days = sorted({d.date() for ser in by_area.values() for d in ser.index})
    labels = [d.isoformat() for d in all_days]
    series = []
    for (lid, area, primary), color in zip(NODES, PALETTE):
        if area not in by_area:
            continue
        m = {d.date(): v for d, v in by_area[area].items()}
        series.append({"area": area, "color": color,
                       "values": [None if (m.get(d) is None or pd.isna(m.get(d)))
                                  else round(float(m[d]), 1) for d in all_days]})
    label = next((lab for (k, lab, _) in PARAMS.values() if k == key), key)
    comp_out[key] = {"label": label, "labels": labels, "series": series}

out = {
    "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "city": "Dhaka",
    "node_count": len(nodes),
    "pollutants": sorted({r["key"] for n in nodes for r in n["readings"]
                          if r["kind"] == "pollutant"},
                         key=lambda k: ORDER.index(k) if k in ORDER else 99),
    "nodes": sorted(nodes, key=lambda n: (n["aqi"] is None, -(n["aqi"] or 0))),
    "comparisons": comp_out,
    # back-compat: old frontend read `comparison` (PM2.5 only)
    "comparison": comp_out.get("pm25", {"labels": [], "series": []}),
}
json.dump(out, open("network.json", "w"), indent=2)
print(f"\nwrote network.json — {len(nodes)} live nodes, "
      f"pollutants: {', '.join(out['pollutants'])}")
