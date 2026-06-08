"""
AQI Dashboard — Flask backend.

Serves a web dashboard for the Air Quality project. It reads the files exported
by AQI_Prediction_Model.ipynb (Section 11):

    aqi_model.joblib                      trained model + feature list
    dashboard_data.csv                    full hourly PM2.5 + AQI series
    predictions_actual_vs_predicted.csv   model test-set predictions
    metrics.json                          headline metrics

Real-time data:
    /api/summary and /api/network overlay the *current* reading fetched live from
    OpenAQ v3 (see live.py), falling back to the file snapshot if the API is
    unreachable. Exported files are also hot-reloaded when they change on disk, so
    re-running the notebook export / gen_network.py needs no server restart.
    Set AQI_LIVE=0 to disable live fetching; AQI_LIVE_TTL tunes the cache (seconds).

Run:
    pip install -r requirements.txt
    python app.py
    open http://127.0.0.1:5000
"""

import os
import json
import threading
import datetime as dt

import joblib
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

import live

# Folder holding the notebook's exported files (default: this folder).
DATA_DIR = os.environ.get("AQI_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)

# --- US EPA 2024 AQI categories (matches the notebook) -----------------------
CATEGORIES = [
    (0,   50,  "Good",                           "#00897b"),
    (51,  100, "Moderate",                        "#f9a825"),
    (101, 150, "Unhealthy for Sensitive Groups",  "#ef6c00"),
    (151, 200, "Unhealthy",                        "#d32f2f"),
    (201, 300, "Very Unhealthy",                   "#7b1fa2"),
    (301, 500, "Hazardous",                        "#6d1b2e"),
]


# --- Multi-pollutant channels shown on the dashboard -------------------------
# All measured by the primary public monitoring station (RAJUK Uttara), read
# live through the OpenAQ v3 API.
CHANNELS = [
    {"key": "pm25",        "label": "PM2.5",       "unit": "µg/m³", "source": "real", "color": "#45c4b0"},
    {"key": "pm1",         "label": "PM1",         "unit": "µg/m³", "source": "real", "color": "#7aa2c4"},
    {"key": "temperature", "label": "Temperature", "unit": "°C",    "source": "real", "color": "#f4a259"},
    {"key": "humidity",    "label": "Humidity",    "unit": "%",     "source": "real", "color": "#5aa9e6"},
]


def aqi_category(aqi):
    if aqi is None or (isinstance(aqi, float) and np.isnan(aqi)):
        return {"label": "Unknown", "color": "#888"}
    for lo, hi, label, color in CATEGORIES:
        if lo <= aqi <= hi:
            return {"label": label, "color": color}
    return {"label": "Hazardous", "color": "#6d1b2e"}


def _path(name):
    return os.path.join(DATA_DIR, name)


def load_state():
    """Load all artifacts once at startup (and expose for reload)."""
    state = {"data": None, "preds": None, "metrics": {}, "bundle": None,
             "card": None, "network": None}

    if os.path.exists(_path("dashboard_data.csv")):
        d = pd.read_csv(_path("dashboard_data.csv"))
        d["datetime"] = pd.to_datetime(d["datetime"], utc=True, errors="coerce")
        state["data"] = d.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    if os.path.exists(_path("predictions_actual_vs_predicted.csv")):
        p = pd.read_csv(_path("predictions_actual_vs_predicted.csv"))
        p["datetime"] = pd.to_datetime(p["datetime"], utc=True, errors="coerce")
        state["preds"] = p.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    if os.path.exists(_path("metrics.json")):
        state["metrics"] = json.load(open(_path("metrics.json")))

    if os.path.exists(_path("aqi_model.joblib")):
        state["bundle"] = joblib.load(_path("aqi_model.joblib"))

    if os.path.exists(_path("model_card.json")):
        state["card"] = json.load(open(_path("model_card.json")))

    if os.path.exists(_path("network.json")):
        state["network"] = json.load(open(_path("network.json")))

    return state


STATE = load_state()

# --- Hot-reload exported files when they change on disk -----------------------
# Lets a re-run of the notebook export / gen_network.py show up without a server
# restart (the live OpenAQ fetch handles the rest — see live.py).
SOURCE_FILES = ["dashboard_data.csv", "predictions_actual_vs_predicted.csv",
                "metrics.json", "aqi_model.joblib", "model_card.json", "network.json"]
_reload_lock = threading.Lock()


def _mtimes():
    return {f: os.path.getmtime(_path(f)) for f in SOURCE_FILES if os.path.exists(_path(f))}


STATE_MTIMES = _mtimes()


def maybe_reload():
    """Reload STATE if any source file's mtime changed since we last loaded."""
    global STATE, STATE_MTIMES
    cur = _mtimes()
    if cur != STATE_MTIMES:
        with _reload_lock:
            if cur != STATE_MTIMES:  # re-check under lock
                STATE = load_state()
                STATE_MTIMES = cur


# Feature set the forecaster knows how to reconstruct without future weather data.
BASE_FEATURES = {"hour", "dow", "month", "pm25_lag1", "pm25_lag2", "pm25_lag3",
                 "pm25_lag24", "pm25_roll6", "pm25_roll24"}


def pm_features(series_values, ts):
    """Build one feature row from recent pm25 values and the target timestamp."""
    s = series_values
    return {
        "hour": ts.hour, "dow": ts.dayofweek, "month": ts.month,
        "pm25_lag1": s[-1], "pm25_lag2": s[-2], "pm25_lag3": s[-3],
        "pm25_lag24": s[-24],
        "pm25_roll6": float(np.mean(s[-6:])),
        "pm25_roll24": float(np.mean(s[-24:])),
    }


def recursive_forecast(series, last_ts, model, features, hours):
    """Roll the PM2.5 model forward `hours` steps, feeding each prediction back in.

    `series` is a list of recent consecutive hourly PM2.5 values (mutated here);
    `last_ts` is its final (UTC, tz-aware) timestamp.
    """
    labels, pm_fore, aqi_fore = [], [], []
    for h in range(hours):
        ts = last_ts + pd.Timedelta(hours=h + 1)
        row = pm_features(series, ts)  # time features stay in UTC to match training
        x = pd.DataFrame([[row[f] for f in features]], columns=features)
        pred = max(float(model.predict(x)[0]), 0.0)
        series.append(pred)
        labels.append(ts.tz_convert(live.BD_TZ).strftime("%Y-%m-%d %H:%M"))
        pm_fore.append(round(pred, 1))
        aqi_fore.append(live.sub_index("pm25", pred, "µg/m³"))
    return labels, pm_fore, aqi_fore


def area_pm25_series(node):
    """Clean recent hourly PM2.5 series for an area, fetched live from OpenAQ.

    Returns (values, last_ts) or (None, None) if unavailable / too short.
    """
    try:
        rows = live.fetch_pm25_hours(node[0])
    except Exception:
        rows = None
    if not rows:
        return None, None
    s = pd.DataFrame(rows)
    s["datetime"] = pd.to_datetime(s["datetime"], utc=True, errors="coerce")
    s = s.dropna(subset=["datetime"]).set_index("datetime")["value"].sort_index()
    s = s[(s >= 0) & (s < 100000)].dropna()
    s = s.resample("1h").mean().interpolate(limit=6).dropna()  # consecutive hourly steps
    if len(s) < 24:
        return None, None
    return s.tolist(), s.index[-1]


# ----------------------------- ROUTES --------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/summary")
def api_summary():
    maybe_reload()
    if STATE["data"] is None or STATE["data"].empty:
        return jsonify({"error": "No data. Run the notebook's Section 11 export first."}), 404
    d = STATE["data"]
    last = d.iloc[-1]

    # Baseline: latest hourly reading from the exported series.
    aqi = int(last["aqi"]) if pd.notna(last["aqi"]) else None
    pm25 = round(float(last["pm25"]), 1)
    latest_time = live.fmt_bd(last["datetime"])
    now = {}
    for ch in CHANNELS:
        k = ch["key"]
        if k in d.columns and pd.notna(last[k]):
            now[k] = round(float(last[k]), 1)

    # Overlay the live OpenAQ reading for the primary node when available.
    is_live = False
    live_read = live.fetch_primary()
    if live_read:
        is_live = True
        latest_time = live_read.get("latest_time") or latest_time
        if live_read.get("pm25") is not None:
            pm25 = live_read["pm25"]
        if live_read.get("aqi") is not None:
            aqi = live_read["aqi"]
        for k, v in (live_read.get("now") or {}).items():
            now[k] = v

    channels = [ch for ch in CHANNELS if ch["key"] in d.columns]
    return jsonify({
        "latest_time": latest_time,
        "pm25": pm25,
        "aqi": aqi,
        "category": aqi_category(aqi),
        "now": now,
        "channels": channels,
        "metrics": STATE["metrics"],
        "n_hours": int(len(d)),
        "live": is_live,
    })


@app.route("/api/meta")
def api_meta():
    """Provenance / training metadata + the EPA AQI scale for the legend."""
    cats = [{"lo": lo, "hi": hi, "label": label, "color": color}
            for lo, hi, label, color in CATEGORIES]
    return jsonify({"card": STATE.get("card"), "categories": cats})


@app.route("/api/network")
def api_network():
    """Live snapshot from all IoT nodes across the city (multi-sensor view).

    Refreshes each node's latest reading from OpenAQ v3 (cached, background) and
    merges it onto network.json; falls back to the file snapshot if the live
    fetch hasn't populated yet or the API is unreachable.
    """
    maybe_reload()
    base = STATE.get("network")
    live_net = live.fetch_network(base)
    if live_net:
        return jsonify(live_net)
    if not base:
        return jsonify({"error": "No network.json. Run gen_network.py."}), 404
    snapshot = dict(base)
    snapshot["live"] = False  # serving the cached file, not a fresh fetch
    return jsonify(snapshot)


@app.route("/api/history")
def api_history():
    maybe_reload()
    if STATE["data"] is None:
        return jsonify({"error": "No data"}), 404
    days = int(request.args.get("days", 30))
    d = STATE["data"]
    cutoff = d["datetime"].max() - pd.Timedelta(days=days)
    d = d[d["datetime"] >= cutoff]
    out = {
        "labels": d["datetime"].dt.tz_convert(live.BD_TZ).dt.strftime("%Y-%m-%d %H:%M").tolist(),
        "aqi": d["aqi"].astype("Int64").tolist(),
    }
    # every available channel series (pm25, pm1, temperature, humidity)
    for ch in CHANNELS:
        k = ch["key"]
        if k in d.columns:
            out[k] = d[k].round(1).tolist()
    return jsonify(out)


@app.route("/api/predictions")
def api_predictions():
    maybe_reload()
    if STATE["preds"] is None:
        return jsonify({"error": "No predictions"}), 404
    p = STATE["preds"]
    # cap points so the chart stays snappy
    if len(p) > 1500:
        p = p.iloc[:: int(np.ceil(len(p) / 1500))]
    return jsonify({
        "labels": p["datetime"].dt.tz_convert(live.BD_TZ).dt.strftime("%Y-%m-%d %H:%M").tolist(),
        "actual": p["actual_pm25"].round(1).tolist(),
        "predicted": p["predicted_pm25"].round(1).tolist(),
    })


@app.route("/api/forecast")
def api_forecast():
    """Forecast PM2.5/AQI for an area using the trained model.

    ?area=<name>  picks which IoT node to forecast (default: the primary node).
    The primary node uses the rich local CSV history; other areas are forecast
    from their recent hourly readings fetched live from OpenAQ. Same model either
    way — it only needs recent PM2.5 + time features.
    """
    maybe_reload()
    if STATE["bundle"] is None:
        return jsonify({"error": "Need aqi_model.joblib"}), 404
    hours = min(int(request.args.get("hours", 24)), 72)
    model = STATE["bundle"]["model"]
    features = STATE["bundle"]["features"]

    if not set(features).issubset(BASE_FEATURES):
        return jsonify({"error": "Model uses weather features; live forecast needs "
                                 "future weather and isn't supported here."}), 200

    area = (request.args.get("area") or "").strip()
    node = live.node_by_area(area) if area else None

    if node is None or node[2]:  # primary node (or unspecified/unknown) → local history
        d = STATE["data"]
        if d is None or len(d) < 24:
            return jsonify({"error": "Not enough history to forecast."}), 200
        series, last_ts = d["pm25"].tolist(), d["datetime"].iloc[-1]
        area_label = live.PRIMARY_AREA
    else:  # another area → forecast from its live OpenAQ readings
        series, last_ts = area_pm25_series(node)
        if series is None:
            return jsonify({"error": f"Not enough recent OpenAQ data for {node[1]} "
                                     "to forecast."}), 200
        area_label = node[1]

    labels, pm_fore, aqi_fore = recursive_forecast(series, last_ts, model, features, hours)
    return jsonify({"labels": labels, "pm25": pm_fore, "aqi": aqi_fore,
                    "area": area_label, "from": live.fmt_bd(last_ts)})


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
