"""
Forecast accuracy on 8 held-out weeks, with the same features and models as ml/forecaster.py.
For each cutoff, train only on days up to it, predict the next 7 days per store and product,
and compare with true demand (units_demanded, which includes demand lost to stockouts).

Run:  python checks/forecast_holdout.py
"""
import sqlite3
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FEATURES = ["r7", "r14", "dow", "is_promo"]

df = pd.read_sql("select * from sales_history", sqlite3.connect(ROOT / "optiroute_data.sqlite"))
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values(["store_id", "sku_id", "date"]).reset_index(drop=True)
censored = (df.is_stockout == 1) & (df.units_sold == 0)
median7 = df["units_sold"].where(~censored).groupby([df.store_id, df.sku_id]).transform(
    lambda x: x.rolling(7, min_periods=1).median().bfill().ffill())
df["y"] = np.where(censored, median7, df.units_sold).astype(float)

last = df["date"].max()
rows = []
for c in [last - pd.Timedelta(days=7 * k) for k in range(8, 0, -1)]:
    train = df[df.date <= c].copy()
    g = train.groupby(["store_id", "sku_id"])["y"]
    train["r7"] = g.transform(lambda x: x.shift(1).rolling(7, min_periods=1).mean())
    train["r14"] = g.transform(lambda x: x.shift(1).rolling(14, min_periods=1).mean())
    train["dow"] = train["date"].dt.dayofweek
    train = train.dropna(subset=["r7"])
    m50 = lgb.LGBMRegressor(objective="quantile", alpha=0.5, n_estimators=100, random_state=42, verbose=-1).fit(train[FEATURES], train.y)
    m90 = lgb.LGBMRegressor(objective="quantile", alpha=0.9, n_estimators=100, random_state=42, verbose=-1).fit(train[FEATURES], train.y)

    hist = df[df.date <= c].groupby(["store_id", "sku_id"])["y"]
    state = hist.apply(lambda x: x.iloc[-7:].mean()).rename("r7").to_frame()
    state["r14"] = hist.apply(lambda x: x.iloc[-14:].mean())
    state["is_promo"] = 0
    p50 = np.zeros(len(state))
    p90 = np.zeros(len(state))
    for d in range(1, 8):
        state["dow"] = (c + pd.Timedelta(days=d)).dayofweek
        a = np.maximum(m50.predict(state[FEATURES]), 0)
        p50 += a
        p90 += np.maximum(m90.predict(state[FEATURES]), a)

    future = df[(df.date > c) & (df.date <= c + pd.Timedelta(days=7))].groupby(["store_id", "sku_id"]).units_demanded.sum()
    lastweek = df[(df.date > c - pd.Timedelta(days=7)) & (df.date <= c)].groupby(["store_id", "sku_id"]).units_demanded.sum()
    actual = future.loc[state.index].values
    naive = lastweek.loc[state.index].values
    rows.append((
        np.abs(actual - p50).sum() / actual.sum(),
        np.abs(actual - naive).sum() / actual.sum(),
        (actual <= p90).mean(),
        (np.abs(actual - p50) < np.abs(actual - naive)).mean(),
    ))

r = np.array(rows)
print(f"Weekly forecast error (WAPE) of P50 : {r[:, 0].mean():.1%}  (weeks range {r[:, 0].min():.1%} to {r[:, 0].max():.1%}; PRD target <= 20%)")
print(f"Error of 'same as last week'        : {r[:, 1].mean():.1%}")
print(f"P90 covers actual demand            : {r[:, 2].mean():.1%} of store-products (a 90% estimate should be about 90%)")
print(f"Beats 'same as last week' on        : {r[:, 3].mean():.1%} of store-products")
