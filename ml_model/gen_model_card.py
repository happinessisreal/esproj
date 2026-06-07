"""
Build model_card.json — the provenance/training metadata the dashboard shows
("how & where this was trained"). Re-runnable.

Usage:
    OPENAQ_API_KEY=... python gen_model_card.py <location_id>

Derives counts/ranges/features/metrics from the exported artifacts, and pulls
the station's identity (name, provider, coordinates, distance) from OpenAQ.
Station lookup is best-effort: if it fails, the card is still written without it.
"""
import os, sys, json, datetime as dt
import numpy as np
import pandas as pd
import joblib

DHAKA_LAT, DHAKA_LON = 23.8103, 90.4125
loc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0

# this script lives in ml_model/; the training data lives in ../dataset/
HERE = os.path.dirname(os.path.abspath(__file__))            # ml_model/
DATASET = os.path.join(os.path.dirname(HERE), "dataset")

# --- derive everything we can from the exported files ------------------------
d = pd.read_csv(os.path.join(DATASET, "training_data.csv"))
d["datetime"] = pd.to_datetime(d["datetime"], utc=True)
d = d.sort_values("datetime").reset_index(drop=True)

p = pd.read_csv(os.path.join(HERE, "predictions_actual_vs_predicted.csv"))
p["datetime"] = pd.to_datetime(p["datetime"], utc=True)

bundle = joblib.load(os.path.join(HERE, "aqi_model.joblib"))
features = list(bundle["features"])
metrics = json.load(open(os.path.join(HERE, "metrics.json")))

# reconstruct the modellable set exactly as the notebook does (lag/roll warmup)
f = d.set_index("datetime").copy()
for lag in (1, 2, 3, 24):
    f[f"pm25_lag{lag}"] = f["pm25"].shift(lag)
f["pm25_roll6"] = f["pm25"].shift(1).rolling(6).mean()
f["pm25_roll24"] = f["pm25"].shift(1).rolling(24).mean()
f = f.dropna(subset=[c for c in features if c in f.columns])

n_test = len(p)
n_model = len(f)
n_train = n_model - n_test
test_start, test_end = p["datetime"].min(), p["datetime"].max()
train_start = f.index[0]
train_end = f.index[n_train - 1] if n_train > 0 else train_start

FEATURE_DOC = {
    "hour": "hour of day (0–23)", "dow": "day of week", "month": "month",
    "pm25_lag1": "PM2.5 one hour ago", "pm25_lag2": "PM2.5 two hours ago",
    "pm25_lag3": "PM2.5 three hours ago", "pm25_lag24": "PM2.5 24 hours ago",
    "pm25_roll6": "mean PM2.5 over last 6 h", "pm25_roll24": "mean PM2.5 over last 24 h",
}

card = {
    "data_source": "OpenAQ v3 API",
    "parameter": "PM2.5",
    "parameter_units": "µg/m³",
    "cadence": "hourly",
    "target": "next-hour PM2.5 (µg/m³)",
    "model": metrics.get("best_model", type(bundle["model"]).__name__),
    "model_note": "selected over the alternative by lowest test-set RMSE",
    "features": [{"name": k, "desc": FEATURE_DOC.get(k, k)} for k in features],
    "n_features": len(features),
    "coverage": {
        "start": d["datetime"].min().strftime("%Y-%m-%d %H:%M UTC"),
        "end":   d["datetime"].max().strftime("%Y-%m-%d %H:%M UTC"),
        "total_hours": int(len(d)),
    },
    "split": {
        "scheme": "time-ordered 80 / 20 (no shuffle)",
        "train_hours": int(n_train),
        "test_hours": int(n_test),
        "train_start": train_start.strftime("%Y-%m-%d"),
        "train_end":   train_end.strftime("%Y-%m-%d"),
        "test_start":  test_start.strftime("%Y-%m-%d"),
        "test_end":    test_end.strftime("%Y-%m-%d"),
    },
    "metrics": metrics,
    "forecast": {"method": "recursive (feeds each prediction back as the next lag)",
                 "max_hours": 72},
    "trained_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "station": None,
}

# --- station identity from OpenAQ (best effort) ------------------------------
key = os.environ.get("OPENAQ_API_KEY", "17803b50a1dac87cafdb7056be122a9e9ea8d1e8abef8df48f3183c8fb2a8d1c")
if loc_id and key:
    try:
        import requests
        r = requests.get(f"https://api.openaq.org/v3/locations/{loc_id}",
                         headers={"X-API-Key": key}, timeout=30)
        loc = r.json()["results"][0]
        c = loc.get("coordinates") or {}
        lat, lon = c.get("latitude"), c.get("longitude")
        dist = None
        if lat is not None and lon is not None:
            R = 6371.0
            from math import radians, sin, cos, asin, sqrt
            dlat, dlon = radians(lat - DHAKA_LAT), radians(lon - DHAKA_LON)
            a = sin(dlat/2)**2 + cos(radians(DHAKA_LAT))*cos(radians(lat))*sin(dlon/2)**2
            dist = round(2*R*asin(sqrt(a)), 1)
        card["station"] = {
            "name": loc.get("name"),
            "location_id": loc_id,
            "provider": (loc.get("provider") or {}).get("name"),
            "locality": loc.get("locality"),
            "timezone": loc.get("timezone"),
            "coordinates": {"lat": lat, "lon": lon},
            "distance_km_from_dhaka_centre": dist,
            "is_monitor": loc.get("isMonitor"),
        }
        print("station:", card["station"]["name"], "| provider:", card["station"]["provider"])
    except Exception as e:
        print("station lookup failed (card written without it):", e)

json.dump(card, open(os.path.join(HERE, "model_card.json"), "w"), indent=2)
print(f"wrote model_card.json | train={n_train}h test={n_test}h total={len(d)}h "
      f"features={len(features)} model={card['model']}")
