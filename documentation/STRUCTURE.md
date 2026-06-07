# Project structure & data flow

This project is organised by stage of the pipeline. Each folder owns one concern.

```
ml_model/        dataset/            ml_model/          backend/        frontend/
notebook    →    training_data.csv  →  add_pollutants  →  app.py serves  ←  dashboard
(train)          (the training data)   (enrich→runtime)   dashboard_data     (display)
```

## Folders

| Folder | Owns | Key files |
|--------|------|-----------|
| `dataset/` | The data the model is trained on — pm25/aqi only, data only (no code). | `training_data.csv` |
| `ml_model/` | The model, its notebooks (clean + executed), the data-prep + regenerator scripts, and everything it exports. | `AQI_Prediction_Model.ipynb` (unrun), `AQI_Prediction_Model_run.ipynb` (executed), `add_pollutants.py`, `aqi_model.joblib`, `metrics.json`, `predictions_actual_vs_predicted.csv`, `gen_predictions.py`, `model_card.json`, `gen_model_card.py` |
| `backend/` | Flask API + the runtime data it serves (the enriched series and live snapshot) + the snapshot generator. | `app.py`, `gen_network.py`, `dashboard_data.csv`, `network.json` |
| `frontend/` | The dashboard the backend renders. | `templates/index.html`, `static/app.js`, `static/style.css` |
| `documentation/` | Project docs + the charts extracted from the notebook. | `STRUCTURE.md`, `figures/*.png` |

Intentionally omitted (no in-repo content): `hardware/`, `firmware/` — the IoT
nodes are commercial AirGradient / Smart Air monitors read through OpenAQ, so there
is no custom board or firmware — and `presentation/` (no slides yet). Add any of
these when you have the material.

## Notebooks & figures

Two copies of the analysis notebook are kept:

- **`AQI_Prediction_Model.ipynb`** — *unrun* (outputs stripped). Clean for diffs and
  version control; cell 5 is set to download live from the OpenAQ API.
- **`AQI_Prediction_Model_run.ipynb`** — *executed*, with every chart and output
  embedded. It was run offline against `dataset/training_data.csv`
  (`DOWNLOAD_FROM_API = False`) so it reproduces the committed model exactly
  (identical metrics; predictions match to 0.0) without hitting the API.

Running it also writes the charts to **`documentation/figures/`**:

| Figure | What it shows |
|--------|----------------|
| `eda_overview.png` | PM2.5 over time + average PM2.5 by hour of day |
| `actual_vs_pred_pm25.png` | test set — actual vs predicted PM2.5 |
| `actual_vs_pred_aqi.png` | test set — actual vs predicted AQI |
| `scatter_pred_vs_actual.png` | predicted-vs-actual scatter (PM2.5 + AQI) |
| `feature_importance.png` | Random-Forest feature importance |

## Data flow

1. **Train** — `ml_model/AQI_Prediction_Model.ipynb` downloads/cleans the series,
   trains the nowcaster, and exports the model bundle, metrics, predictions, and the
   cleaned pm25/aqi series — the **training data**, `dataset/training_data.csv`.
   `ml_model/gen_model_card.py` builds `model_card.json` from those. The held-out
   **test case** (`predictions_actual_vs_predicted.csv` — actual vs predicted PM2.5/AQI
   on the unseen tail) comes from the notebook's Section 10 and is regenerable from the
   saved model + training data with `ml_model/gen_predictions.py`.
   > Note: the notebook's Section 11 writes its exports next to the notebook
   > (`ml_model/`) under the name `dashboard_data.csv`. In this layout that pm25/aqi
   > export is the training data, so rename + move it: `mv ml_model/dashboard_data.csv
   > dataset/training_data.csv`. The model artifacts (`aqi_model.joblib`,
   > `metrics.json`, `predictions_actual_vs_predicted.csv`) already belong in
   > `ml_model/` and need no move.
2. **Enrich** — `ml_model/add_pollutants.py` reads `dataset/training_data.csv` and
   adds the primary monitor's extra channels (PM1, temperature, humidity) from OpenAQ
   v3, writing the runtime copy `backend/dashboard_data.csv` that the dashboard serves.
   `backend/gen_network.py` separately pulls the latest multi-pollutant readings from
   every monitor and writes `backend/network.json` (the live city view).
3. **Serve** — `backend/app.py` resolves each artifact from its home folder
   (override with `AQI_DATA_DIR` to read a single flat folder), exposes the
   `/api/*` endpoints, and renders the `frontend/` templates + static assets.
4. **Display** — `frontend/static/app.js` calls the API and draws the AQI orb, the
   live multi-pollutant city network + map, the area comparison, trends, validation,
   and the forecast.

## Cross-folder paths

The scripts and backend resolve paths relative to their own location (via
`__file__`), so they work regardless of the current working directory:

- `backend/app.py` → reads `../ml_model` (model artifacts) and local
  `dashboard_data.csv` + `network.json`; renders `../frontend`.
- `backend/gen_network.py` → writes local `network.json`.
- `ml_model/add_pollutants.py` → reads `../dataset/training_data.csv`, writes
  `../backend/dashboard_data.csv`.
- `ml_model/gen_model_card.py` → reads `../dataset/training_data.csv` + local model
  files, writes `./model_card.json`.
