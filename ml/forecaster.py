import sqlite3
import pandas as pd
import numpy as np
import lightgbm as lgb
from typing import List

from common.contracts import TargetDemand

from common.db import DB_PATH


_MODEL_CACHE = {}


def generate_target_demands(cycle_date: str = "2026-10-26", horizon_days: int = 7) -> List[TargetDemand]:
    """
    Load sales history and SKUs from SQLite, correct for censored demand,
    train LightGBM quantile regression models (P50 and P90), calculate
    the overage-adjusted newsvendor critical ratio, interpolate target demand,
    and return a list of TargetDemand contract objects.
    """
    if "trained" in _MODEL_CACHE:
        df_sales, df_skus, model_p50, model_p90, feature_cols = _MODEL_CACHE["trained"]
        return _build_targets(df_sales, df_skus, model_p50, model_p90, feature_cols, cycle_date, horizon_days)

    # 1. Connect to SQLite and load tables
    conn = sqlite3.connect(DB_PATH)
    df_sales = pd.read_sql_query(
        "SELECT * FROM sales_history ORDER BY store_id, sku_id, date", conn
    )
    df_skus = pd.read_sql_query("SELECT * FROM skus", conn)
    conn.close()

    df_sales["date"] = pd.to_datetime(df_sales["date"])
    df_sales = df_sales.sort_values(["store_id", "sku_id", "date"]).reset_index(drop=True)

    # 2. Implement censored demand correction:
    # Where is_stockout == 1 and units_sold == 0, impute units_sold with the
    # 7-day rolling median of non-stockout days for that specific (store_id, sku_id)
    df_sales["clean_sales"] = np.where(
        (df_sales["is_stockout"] == 1) & (df_sales["units_sold"] == 0),
        np.nan,
        df_sales["units_sold"],
    )

    df_sales["rolling_7d_median"] = df_sales.groupby(["store_id", "sku_id"])[
        "clean_sales"
    ].transform(lambda x: x.rolling(window=7, min_periods=1).median())
    
    df_sales["rolling_7d_median"] = df_sales.groupby(["store_id", "sku_id"])[
        "rolling_7d_median"
    ].transform(lambda x: x.bfill().ffill())

    df_sales["imputed_units_sold"] = df_sales["units_sold"].astype(float)
    mask_stockout = (df_sales["is_stockout"] == 1) & (df_sales["units_sold"] == 0)
    df_sales.loc[mask_stockout, "imputed_units_sold"] = df_sales.loc[
        mask_stockout, "rolling_7d_median"
    ]

    # 3. Create feature columns: 7-day rolling mean, 14-day rolling mean, dayofweek, and is_promo
    df_sales["rolling_7d_mean"] = df_sales.groupby(["store_id", "sku_id"])[
        "imputed_units_sold"
    ].transform(lambda x: x.rolling(window=7, min_periods=1).mean())

    df_sales["rolling_14d_mean"] = df_sales.groupby(["store_id", "sku_id"])[
        "imputed_units_sold"
    ].transform(lambda x: x.rolling(window=14, min_periods=1).mean())

    df_sales["dayofweek"] = df_sales["date"].dt.dayofweek

    feature_cols = ["rolling_7d_mean", "rolling_14d_mean", "dayofweek", "is_promo"]
    X_train = df_sales[feature_cols]
    y_train = df_sales["imputed_units_sold"]

    # 4. Train two LightGBM quantile regression models: P50 (alpha=0.5) and P90 (alpha=0.9)
    model_p50 = lgb.LGBMRegressor(
        objective="quantile",
        alpha=0.5,
        n_estimators=100,
        random_state=42,
        verbose=-1,
    )
    model_p50.fit(X_train, y_train)

    model_p90 = lgb.LGBMRegressor(
        objective="quantile",
        alpha=0.9,
        n_estimators=100,
        random_state=42,
        verbose=-1,
    )
    model_p90.fit(X_train, y_train)

    _MODEL_CACHE["trained"] = (df_sales, df_skus, model_p50, model_p90, feature_cols)
    return _build_targets(df_sales, df_skus, model_p50, model_p90, feature_cols, cycle_date, horizon_days)


def _build_targets(df_sales, df_skus, model_p50, model_p90, feature_cols, cycle_date, horizon_days):
    # 5. Predict each day of the horizon from the latest rolling state per (store_id, sku_id)
    cycle_dt = pd.to_datetime(cycle_date)
    latest_features = df_sales.groupby(["store_id", "sku_id"]).last().reset_index()
    latest_features["is_promo"] = 0

    p50_total = np.zeros(len(latest_features))
    p90_total = np.zeros(len(latest_features))
    for d in range(horizon_days):
        latest_features["dayofweek"] = (cycle_dt + pd.Timedelta(days=d)).dayofweek
        X_pred = latest_features[feature_cols]
        p50 = np.maximum(model_p50.predict(X_pred), 0.0)
        # Ensure quantile monotonicity
        p50_total += p50
        p90_total += np.maximum(model_p90.predict(X_pred), p50)

    latest_features["p50_demand"] = p50_total
    latest_features["p90_demand"] = p90_total

    # 6. Calculate overage-adjusted newsvendor critical ratio:
    # q_s = clip(margin / (margin + holding_cost + unit_cost * is_perishable), 0.50, 0.90)
    merged = latest_features.merge(df_skus, on="sku_id", how="left")
    merged["q_s"] = (
        merged["margin"]
        / (
            merged["margin"]
            + merged["holding_cost"]
            + merged["unit_cost"] * merged["is_perishable"]
        )
    ).clip(0.50, 0.90)

    # 7. Interpolate target demand D_hat between P50 and P90
    weight = (merged["q_s"] - 0.50) / (0.90 - 0.50)
    merged["d_hat"] = merged["p50_demand"] + weight * (
        merged["p90_demand"] - merged["p50_demand"]
    )

    # 8. Projected stock at delivery: store on-hand minus median demand during the
    # fastest lane's transit time (sales that happen before the truck arrives)
    conn = sqlite3.connect(DB_PATH)
    on_hand = pd.read_sql_query("SELECT * FROM store_inventory", conn)
    lead = pd.read_sql_query(
        "SELECT store_id, MIN(lead_time_days) AS lead_days FROM lanes GROUP BY store_id", conn
    )
    conn.close()
    merged = merged.merge(on_hand, on=["store_id", "sku_id"], how="left").merge(lead, on="store_id", how="left")
    merged["on_hand_units"] = merged["on_hand_units"].fillna(0.0)
    daily_p50 = merged["p50_demand"] / horizon_days
    merged["projected"] = (merged["on_hand_units"] - daily_p50 * merged["lead_days"].fillna(1)).clip(lower=0.0)

    target_demands = []
    for _, row in merged.iterrows():
        target_demands.append(
            TargetDemand(
                sku_id=str(row["sku_id"]),
                store_id=str(row["store_id"]),
                target_q_demand=float(round(row["d_hat"], 2)),
                p90_demand=float(round(row["p90_demand"], 2)),
                p50_demand=float(round(row["p50_demand"], 2)),
                projected_stock_delivery=float(round(row["projected"], 2)),
                horizon_days=int(horizon_days),
                on_hand_units=float(row["on_hand_units"]),
            )
        )

    return target_demands


if __name__ == "__main__":
    demands = generate_target_demands("2026-10-26")
    for item in demands[:5]:
        print(item)
