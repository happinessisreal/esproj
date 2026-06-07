"""
Augment dashboard_data.csv with the deployed monitor's other REAL channels
(temperature, humidity, PM1). The AirGradient WiFi monitor at RAJUK Uttara is
treated as the project's IoT sensor node; its readings are pulled live from the
OpenAQ v3 API. Every channel here is measured — nothing is simulated.

Usage:
    OPENAQ_API_KEY=... python add_pollutants.py <location_id>
"""
import os, sys, time, requests
import pandas as pd

loc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 6157905
H = {"X-API-Key": "17803b50a1dac87cafdb7056be122a9e9ea8d1e8abef8df48f3183c8fb2a8d1c"}
BASE = "https://api.openaq.org/v3"

# parameter name -> output column name
WANT = {"temperature": "temperature", "relativehumidity": "humidity", "pm1": "pm1"}


def get(path, params=None):
    for a in range(6):
        r = requests.get(BASE + path, headers=H, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 2 ** a))); continue
        r.raise_for_status(); return r.json()
    raise SystemExit("rate-limited")


def month_chunks(start, end):
    cur = pd.Timestamp(start).date(); end = pd.Timestamp(end).date()
    import datetime as dt
    while cur < end:
        nxt = min((cur.replace(day=1) + dt.timedelta(days=32)).replace(day=1), end)
        yield cur.isoformat(), nxt.isoformat(); cur = nxt


def download(sensor_id, start, end):
    rows = []
    for dfrom, dto in month_chunks(start, end):
        page = 1
        while True:
            data = get(f"/sensors/{sensor_id}/hours",
                       {"datetime_from": dfrom, "datetime_to": dto, "limit": 1000, "page": page})
            res = data.get("results", [])
            for it in res:
                ts = ((it.get("period") or {}).get("datetimeFrom") or {}).get("utc")
                rows.append({"datetime": ts, "value": it.get("value")})
            if len(res) < 1000: break
            page += 1; time.sleep(0.2)
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    return df.dropna(subset=["datetime"]).set_index("datetime")["value"].sort_index()


# --- base series (keep pm25/aqi exactly as the model pipeline produced them) --
base = pd.read_csv("dashboard_data.csv")
base["datetime"] = pd.to_datetime(base["datetime"], utc=True)
base = base[["datetime", "pm25", "aqi"]].set_index("datetime").sort_index()
start, end = base.index.min().date().isoformat(), (base.index.max() + pd.Timedelta(days=1)).date().isoformat()

# --- discover sensors and pull the real extra channels -----------------------
sensors = get(f"/locations/{loc_id}/sensors", {"limit": 100}).get("results", [])
for s in sensors:
    pname = (s.get("parameter") or {}).get("name")
    if pname in WANT:
        col = WANT[pname]
        ser = download(s["id"], start, end)
        # align to base hourly grid, clean lightly, fill short gaps
        ser = ser.reindex(base.index.union(ser.index)).interpolate(limit=6)
        base[col] = ser.reindex(base.index)
        print(f"  added {col:12s} from sensor {s['id']}  ({base[col].notna().sum()} pts)")

# sane bounds + fill any residual gaps
if "temperature" in base: base["temperature"] = base["temperature"].clip(-10, 55)
if "humidity" in base:    base["humidity"] = base["humidity"].clip(0, 100)
if "pm1" in base:         base["pm1"] = base["pm1"].clip(0, 1000)
for c in ("temperature", "humidity", "pm1"):
    if c in base: base[c] = base[c].interpolate(limit=12).ffill().bfill().round(1)

base.reset_index().to_csv("dashboard_data.csv", index=False)
cols = [c for c in base.columns]
print("wrote dashboard_data.csv with columns:", ["datetime"] + cols)
print("latest:", base.iloc[-1][cols].to_dict())
