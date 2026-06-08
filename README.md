# Dhaka Air Quality — Web Dashboard

A Flask + Chart.js dashboard for the Air Quality Monitoring & Prediction project.
It visualises the output of `AQI_Prediction_Model.ipynb`: the current AQI (colour-coded
to the US EPA 2024 categories), the PM2.5 trend, the model's actual-vs-predicted
performance, and a short forward forecast produced by the trained model.

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

## Files it reads
Place these (exported by the notebook's Section 11 + Section 10) next to `app.py`:

| File | Produced by |
|------|-------------|
| `aqi_model.joblib` | notebook Section 11 |
| `dashboard_data.csv` | notebook Section 11 |
| `metrics.json` | notebook Section 11 |
| `predictions_actual_vs_predicted.csv` | notebook Section 10 |

(The copy included here was built from the sample data so the app runs immediately.
Replace them with your own exports to show your real station's data.)

## Run it
```bash
cd webapp
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

To point the app at files in another folder:
```bash
AQI_DATA_DIR=/path/to/your/outputs python app.py
```

## Real-time data
The **Current AQI** hero and the **City sensor network** map read the *current*
reading straight from the OpenAQ v3 feed at request time, not just the exported
files (`live.py`). The fetch is cached and runs in a background thread, so requests
stay fast; if OpenAQ is unreachable the app transparently falls back to the file
snapshot (the UI then shows `snapshot · …` instead of `live · …`). The exported
files are also hot-reloaded when they change on disk, so re-running the notebook
export or `gen_network.py` shows up without restarting the server. All timestamps
are displayed in Bangladesh Standard Time (BST, UTC+6).

```bash
OPENAQ_API_KEY=...    # API key (a project key is bundled so it works out-of-box)
AQI_LIVE=0            # disable live fetching, serve files only
AQI_LIVE_TTL=600      # cache lifetime in seconds (default 10 min)
```

## Notes
- The **city sensor network** reads `network.json`, built by `gen_network.py`. That
  script now pulls *every* parameter each node exposes (not just PM2.5) and computes
  a per-pollutant AQI sub-index. Re-run it to refresh and to fill in every node's
  full channel set:
  ```bash
  OPENAQ_API_KEY=... python gen_network.py
  ```
  The bundled `network.json` was generated live, so every node carries its full real
  channel set (PM2.5, PM1, temperature, humidity). These low-cost Smart Air / AirGradient
  monitors report particulates + environment only — for true gases (NO₂/SO₂/O₃/CO) add
  reference-station location IDs to the `NODES` list and the AQI math handles the rest.
- The forecast works when the model uses the standard time + lag features (the usual
  PM2.5-only case). If you trained with temperature/humidity features, live forecasting
  is disabled (it would need future weather), and the card shows a short message instead.
- This is a development server. For a class demo that's fine; don't expose it publicly.
