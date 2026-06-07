# Dhaka Air Quality — Monitoring & Prediction

An end-to-end Air Quality Monitoring & Prediction project for Dhaka: it pulls real
hourly readings from deployed IoT monitors (via the OpenAQ v3 API), trains a model
to nowcast PM2.5 / AQI, and serves a Flask + Chart.js dashboard that shows the live
multi-pollutant city network, the model's actual-vs-predicted performance, and a
short forward forecast.

## Repository layout
```
.
├── dataset/        the data the model is trained on (pm25/aqi only)
│   └── training_data.csv         hourly PM2.5 + AQI series
├── ml_model/       the model, its notebooks, and the code that builds the data
│   ├── AQI_Prediction_Model.ipynb            clean (unrun) notebook — for diffs/version control
│   ├── AQI_Prediction_Model_run.ipynb        executed copy — all charts + outputs embedded
│   ├── add_pollutants.py         training_data.csv → backend/dashboard_data.csv (+ PM1/temp/humidity)
│   ├── aqi_model.joblib          trained model + feature list
│   ├── metrics.json              headline metrics
│   ├── predictions_actual_vs_predicted.csv   test set: actual vs predicted PM2.5/AQI
│   ├── gen_predictions.py        (re)build the test-set predictions CSV
│   ├── model_card.json           provenance / training metadata
│   └── gen_model_card.py         (re)build model_card.json
├── backend/        Flask API + live data
│   ├── app.py
│   ├── gen_network.py            build the live multi-pollutant city snapshot
│   ├── dashboard_data.csv        enriched runtime series (PM2.5 + PM1/temp/humidity)
│   └── network.json              live snapshot written by gen_network.py
├── frontend/       the dashboard UI (served by the backend)
│   ├── templates/index.html
│   └── static/{app.js, style.css}
├── documentation/  project docs + extracted charts
│   ├── STRUCTURE.md
│   └── figures/    notebook charts as PNG (EDA, actual-vs-predicted, scatter, feature importance)
├── requirements.txt
└── README.md
```
> `hardware/`, `firmware/` and `presentation/` are intentionally omitted — this
> project uses commercial AirGradient / Smart Air monitors (no custom hardware or
> firmware in-repo) and ships no slides. Add them if/when you have that content.

## What it shows
- **Current AQI** — big colour-coded indicator (Good → Hazardous) with latest PM2.5.
- **City sensor network** — multiple IoT nodes across Dhaka, each showing **every
  pollutant/channel it measures** (PM2.5, PM10, PM1, gases where available, plus
  temperature/humidity). Each node's headline AQI is its *worst* pollutant
  sub-index (the EPA dominant-pollutant rule), with the driving pollutant labelled.
- **Area comparison** — 30-day daily means across neighbourhoods, switchable per
  pollutant (any pollutant measured at more than one node).
- **Model metrics** — best model, MAE, RMSE, R² (from `metrics.json`).
- **Pollutant & environment trends** — 7-day / 30-day / 1-year view, per channel.
- **Actual vs Predicted** — the model on its held-out test set.
- **Forecast** — next 12/24/48 h, predicted recursively from the latest readings.

## Artifacts the backend reads
The backend resolves each file from its folder above (no copying needed):

| File | Home | Produced by |
|------|------|-------------|
| `aqi_model.joblib` | `ml_model/` | notebook Section 11 |
| `metrics.json` | `ml_model/` | notebook Section 11 |
| `predictions_actual_vs_predicted.csv` | `ml_model/` | notebook Section 10 / `ml_model/gen_predictions.py` |
| `model_card.json` | `ml_model/` | `ml_model/gen_model_card.py` |
| `training_data.csv` | `dataset/` | notebook Section 11 (pm25/aqi — the training data) |
| `dashboard_data.csv` | `backend/` | `ml_model/add_pollutants.py` (enriched runtime copy) |
| `network.json` | `backend/` | `backend/gen_network.py` |

(The copies included here were built from real data so the app runs immediately.
Re-run the scripts/notebook to refresh them with your own station's data.)

## Run it
```bash
pip install -r requirements.txt
python backend/app.py
# open http://127.0.0.1:5000
```

To read every artifact from one flat folder instead (e.g. the notebook's raw export dir):
```bash
AQI_DATA_DIR=/path/to/your/outputs python backend/app.py
```

## Notes
- The **city sensor network** reads `backend/network.json`, built by
  `backend/gen_network.py`. That script pulls *every* parameter each node exposes
  (not just PM2.5) and computes a per-pollutant AQI sub-index. Re-run it to refresh:
  ```bash
  OPENAQ_API_KEY=... python backend/gen_network.py
  ```
  The bundled `network.json` was generated live, so every node carries its full real
  channel set (PM2.5, PM1, temperature, humidity). These low-cost Smart Air / AirGradient
  monitors report particulates + environment only — for true gases (NO₂/SO₂/O₃/CO) add
  reference-station location IDs to the `NODES` list and the AQI math handles the rest.
- The forecast works when the model uses the standard time + lag features (the usual
  PM2.5-only case). If you trained with temperature/humidity features, live forecasting
  is disabled (it would need future weather), and the card shows a short message instead.
- This is a development server. For a class demo that's fine; don't expose it publicly.
