"""
AQI Dashboard — Flask backend.

Serves a web dashboard for the Air Quality project. It reads the files exported
by AQI_Prediction_Model.ipynb (Section 11):

    aqi_model.joblib                      trained model + feature list
    dashboard_data.csv                    full hourly PM2.5 + AQI series
    predictions_actual_vs_predicted.csv   model test-set predictions
    metrics.json                          headline metrics

Run:
    pip install -r requirements.txt
    python app.py
    open http://127.0.0.1:5000
"""

import os
import json
import datetime as dt

import joblib
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

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
# All measured by the deployed WiFi air-quality monitor (AirGradient node at
# RAJUK Uttara), read live through the OpenAQ v3 API — treated as our IoT node.
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


# ----------------------------- ROUTES --------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/summary")
def api_summary():
    if STATE["data"] is None or STATE["data"].empty:
        return jsonify({"error": "No data. Run the notebook's Section 11 export first."}), 404
    d = STATE["data"]
    last = d.iloc[-1]
    aqi = int(last["aqi"]) if pd.notna(last["aqi"]) else None
    # current reading for every channel that's present in the data
    now = {}
    for ch in CHANNELS:
        k = ch["key"]
        if k in d.columns and pd.notna(last[k]):
            now[k] = round(float(last[k]), 1)
    channels = [ch for ch in CHANNELS if ch["key"] in d.columns]
    return jsonify({
        "latest_time": last["datetime"].strftime("%Y-%m-%d %H:%M UTC"),
        "pm25": round(float(last["pm25"]), 1),
        "aqi": aqi,
        "category": aqi_category(aqi),
        "now": now,
        "channels": channels,
        "metrics": STATE["metrics"],
        "n_hours": int(len(d)),
    })


@app.route("/api/meta")
def api_meta():
    """Provenance / training metadata + the EPA AQI scale for the legend."""
    cats = [{"lo": lo, "hi": hi, "label": label, "color": color}
            for lo, hi, label, color in CATEGORIES]
    return jsonify({"card": STATE.get("card"), "categories": cats})


@app.route("/api/network")
def api_network():
    """Live snapshot from all IoT nodes across the city (multi-sensor view)."""
    if not STATE.get("network"):
        return jsonify({"error": "No network.json. Run gen_network.py."}), 404
    return jsonify(STATE["network"])


@app.route("/api/history")
def api_history():
    if STATE["data"] is None:
        return jsonify({"error": "No data"}), 404
    days = int(request.args.get("days", 30))
    d = STATE["data"]
    cutoff = d["datetime"].max() - pd.Timedelta(days=days)
    d = d[d["datetime"] >= cutoff]
    out = {
        "labels": d["datetime"].dt.strftime("%Y-%m-%d %H:%M").tolist(),
        "aqi": d["aqi"].astype("Int64").tolist(),
    }
    # every available channel series (pm25, pm1, co2, temperature, humidity)
    for ch in CHANNELS:
        k = ch["key"]
        if k in d.columns:
            out[k] = d[k].round(1).tolist()
    return jsonify(out)


@app.route("/api/predictions")
def api_predictions():
    if STATE["preds"] is None:
        return jsonify({"error": "No predictions"}), 404
    p = STATE["preds"]
    # cap points so the chart stays snappy
    if len(p) > 1500:
        p = p.iloc[:: int(np.ceil(len(p) / 1500))]
    return jsonify({
        "labels": p["datetime"].dt.strftime("%Y-%m-%d %H:%M").tolist(),
        "actual": p["actual_pm25"].round(1).tolist(),
        "predicted": p["predicted_pm25"].round(1).tolist(),
    })


@app.route("/api/forecast")
def api_forecast():
    if STATE["data"] is None or STATE["bundle"] is None:
        return jsonify({"error": "Need dashboard_data.csv and aqi_model.joblib"}), 404
    hours = min(int(request.args.get("hours", 24)), 72)
    model = STATE["bundle"]["model"]
    features = STATE["bundle"]["features"]

    if not set(features).issubset(BASE_FEATURES):
        return jsonify({"error": "Model uses weather features; live forecast needs "
                                 "future weather and isn't supported here."}), 200

    d = STATE["data"]
    if len(d) < 24:
        return jsonify({"error": "Not enough history to forecast."}), 200

    series = d["pm25"].tolist()
    last_ts = d["datetime"].iloc[-1]

    # convert AQI helper inline (mirror of notebook breakpoints)
    bp = [(0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
          (55.5, 125.4, 151, 200), (125.5, 225.4, 201, 300), (225.5, 325.4, 301, 500)]

    def to_aqi(c):
        c = np.floor(c * 10) / 10
        for clo, chi, ilo, ihi in bp:
            if clo <= c <= chi:
                return int(round((ihi - ilo) / (chi - clo) * (c - clo) + ilo))
        return 500

    labels, pm_fore, aqi_fore = [], [], []
    for h in range(hours):
        ts = last_ts + pd.Timedelta(hours=h + 1)
        row = pm_features(series, ts)
        x = pd.DataFrame([[row[f] for f in features]], columns=features)
        pred = float(model.predict(x)[0])
        pred = max(pred, 0.0)
        series.append(pred)
        labels.append(ts.strftime("%Y-%m-%d %H:%M"))
        pm_fore.append(round(pred, 1))
        aqi_fore.append(to_aqi(pred))

    return jsonify({"labels": labels, "pm25": pm_fore, "aqi": aqi_fore})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
