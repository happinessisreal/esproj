"""
Re-extract predictions_actual_vs_predicted.csv — the notebook's Section-10 test
case: the model's predicted PM2.5 / AQI vs the actual values on the held-out test
set. Reproduces it exactly from the saved model bundle + the training data, so the
test result is regenerable without re-running the whole notebook.

    columns: datetime, actual_pm25, predicted_pm25, actual_aqi, predicted_aqi

Usage:
    python gen_predictions.py
"""
import os
from math import ceil

import numpy as np
import pandas as pd
import joblib

HERE = os.path.dirname(os.path.abspath(__file__))            # ml_model/
ROOT = os.path.dirname(HERE)
TRAIN = os.path.join(ROOT, "dataset", "training_data.csv")   # the data the model trained on
OUT = os.path.join(HERE, "predictions_actual_vs_predicted.csv")

TEST_SIZE = 0.20   # time-ordered 80/20, no shuffle — matches the notebook's split

# EPA 2024 PM2.5 -> AQI (identical to the notebook's pm_to_aqi)
BREAKPOINTS = [(0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
               (55.5, 125.4, 151, 200), (125.5, 225.4, 201, 300), (225.5, 325.4, 301, 500)]


def pm_to_aqi(c):
    if pd.isna(c):
        return np.nan
    c = np.floor(c * 10) / 10            # EPA truncates PM2.5 to 0.1 µg/m³
    for clo, chi, ilo, ihi in BREAKPOINTS:
        if clo <= c <= chi:
            return round((ihi - ilo) / (chi - clo) * (c - clo) + ilo)
    return 500


# --- rebuild the modelling features exactly as the notebook does -------------
d = pd.read_csv(TRAIN)
d["datetime"] = pd.to_datetime(d["datetime"], utc=True)
d = d.sort_values("datetime").set_index("datetime")
d["hour"] = d.index.hour
d["dow"] = d.index.dayofweek
d["month"] = d.index.month
for lag in (1, 2, 3, 24):
    d[f"pm25_lag{lag}"] = d["pm25"].shift(lag)
d["pm25_roll6"] = d["pm25"].shift(1).rolling(6).mean()
d["pm25_roll24"] = d["pm25"].shift(1).rolling(24).mean()

bundle = joblib.load(os.path.join(HERE, "aqi_model.joblib"))
FEATURES, model = bundle["features"], bundle["model"]

md = d.dropna(subset=[c for c in FEATURES if c in d.columns] + ["pm25"])
X, y = md[FEATURES], md["pm25"]

# the held-out tail (no shuffle) is the test set the model never saw
n_test = ceil(TEST_SIZE * len(md))
X_test, y_test = X.iloc[-n_test:], y.iloc[-n_test:]
pred = model.predict(X_test)

out = pd.DataFrame({
    "datetime": y_test.index,
    "actual_pm25": y_test.values,
    "predicted_pm25": pred,
    "actual_aqi": y_test.apply(pm_to_aqi).values,
    "predicted_aqi": pd.Series(pred, index=y_test.index).apply(pm_to_aqi).values,
})
out.to_csv(OUT, index=False)

mae = float(np.abs(out["actual_pm25"] - out["predicted_pm25"]).mean())
print(f"wrote {OUT}")
print(f"  {len(out)} test rows | PM2.5 MAE={mae:.2f} | "
      f"{out['datetime'].min():%Y-%m-%d %H:%M} -> {out['datetime'].max():%Y-%m-%d %H:%M} UTC")
