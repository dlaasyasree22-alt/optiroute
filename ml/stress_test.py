import sqlite3
import time
from typing import List, Dict, Any
import numpy as np
import pandas as pd

from common.contracts import PlanResult, TargetDemand, ShipmentLine

from common.db import DB_PATH


NB_DISPERSION = 8.0  # Negative Binomial size parameter: variance = mean + mean^2 / NB_DISPERSION


def evaluate_plan_risk(
    plan_result: PlanResult,
    target_demands: List[TargetDemand],
    num_scenarios: int = 500,
    random_seed: int = 42,
    shock_sigma: float = 0.15,
) -> Dict[str, Any]:
    """
    Vectorized Monte Carlo stress-tester evaluating plan resilience across 500 scenarios.

    Each scenario draws a regional shock beta ~ LogNormal(0, shock_sigma) shared by all stores
    in a region, then samples Negative Binomial horizon demand around beta * P50 per store-SKU.
    Reports the fill-rate distribution, its CVaR95 (mean of the worst 5% of scenarios),
    the stockout cost in those worst scenarios, and the store-SKU pairs most likely to run out.
    """
    if not target_demands:
        return {
            "median_fill_rate": 0.0,
            "p_stockout": 0.0,
            "cvar_95_unmet_cost": 0.0,
            "scenarios_evaluated": 0,
        }

    conn = sqlite3.connect(DB_PATH)
    df_skus = pd.read_sql_query("SELECT sku_id, margin, unit_cost FROM skus", conn)
    conn.close()
    penalty_map = dict(zip(df_skus["sku_id"], df_skus["margin"] + 0.5 * df_skus["unit_cost"]))

    def region_of(store_id: str) -> int:
        try:
            return (int(store_id.replace("STORE_", "")) - 1) % 3
        except (ValueError, TypeError):
            return 0

    pair_regions = np.array([region_of(td.store_id) for td in target_demands])
    p50_array = np.array([td.p50_demand for td in target_demands], dtype=np.float64)
    proj_stock_array = np.array([td.projected_stock_delivery for td in target_demands], dtype=np.float64)
    penalty_array = np.array([penalty_map.get(td.sku_id, 0.0) for td in target_demands], dtype=np.float64)

    shipped_map: Dict[tuple, int] = {}
    for s in (plan_result.shipments if plan_result else []):
        shipped_map[(s.store_id, s.sku_id)] = shipped_map.get((s.store_id, s.sku_id), 0) + s.units_shipped
    shipped_array = np.array([shipped_map.get((td.store_id, td.sku_id), 0) for td in target_demands], dtype=np.float64)
    available_inventory = proj_stock_array + shipped_array

    rng = np.random.default_rng(random_seed)
    beta_regions = rng.lognormal(mean=0.0, sigma=shock_sigma, size=(num_scenarios, 3))
    mean = np.maximum(0.01, p50_array * beta_regions[:, pair_regions])
    # numpy parameterises NB by (n, p) with mean n(1-p)/p
    p = NB_DISPERSION / (NB_DISPERSION + mean)
    simulated_demand = rng.negative_binomial(NB_DISPERSION, p)

    delivered = np.minimum(simulated_demand, available_inventory)
    unmet = np.maximum(0, simulated_demand - available_inventory)
    scenario_fill = delivered.sum(axis=1) / np.maximum(simulated_demand.sum(axis=1), 1e-9)
    scenario_cost = (unmet * penalty_array).sum(axis=1)

    worst_n = max(1, int(round(num_scenarios * 0.05)))
    worst_idx = np.argsort(scenario_fill)[:worst_n]
    pair_stockout_prob = (unmet > 0).mean(axis=0)

    at_risk = []
    for i in np.argsort(-pair_stockout_prob)[:8]:
        td = target_demands[i]
        at_risk.append({
            "sku_id": td.sku_id,
            "store_id": td.store_id,
            "stockout_probability": round(float(pair_stockout_prob[i]), 3),
            "available_units": round(float(available_inventory[i]), 1),
            "median_demand_units": round(float(p50_array[i]), 1),
        })

    return {
        "scenarios_evaluated": int(num_scenarios),
        "median_fill_rate": round(float(np.median(scenario_fill) * 100.0), 2),
        "cvar95_fill_rate": round(float(scenario_fill[worst_idx].mean() * 100.0), 2),
        # Share of store-SKU pairs that run short, averaged over scenarios
        "p_stockout": round(float((unmet > 0).mean() * 100.0), 2),
        "cvar_95_unmet_cost": round(float(scenario_cost[worst_idx].mean()), 2),
        "expected_unmet_cost": round(float(scenario_cost.mean()), 2),
        "fill_rate_samples": [round(float(x) * 100.0, 2) for x in scenario_fill],
        "top_at_risk": at_risk,
    }


if __name__ == "__main__":
    from ml.forecaster import generate_target_demands

    print("=== Running OptiRoute Stress-Test Benchmark ===")
    
    # 1. Generate baseline target demands
    t_start_forecast = time.time()
    demands = generate_target_demands("2026-10-26")
    print(f"Loaded {len(demands)} target demands in {time.time() - t_start_forecast:.2f}s")

    # 2. Mock a PlanResult dispatching shipments
    mock_shipments = []
    for td in demands:
        # Simulate shipping ~80% of target demand
        cases = int(np.ceil((td.target_q_demand * 0.8) / 10))
        mock_shipments.append(
            ShipmentLine(
                sku_id=td.sku_id,
                batch_id="BAT_WH1_0001",
                warehouse_id="WH1",
                store_id=td.store_id,
                cases_shipped=cases,
                units_shipped=cases * 10,
                truck_id="TRUCK_WH1_S01",
                days_to_expiry_at_arrival=18,
                reason_code="REGULAR_REPLENISHMENT",
            )
        )

    mock_plan = PlanResult(
        plan_id="MOCK_PLAN_20261026_01",
        solver_status="OPTIMAL",
        solve_time_seconds=1.15,
        total_freight_cost=425000.0,
        unmet_demand_units=120.0,
        wastage_units=0.0,
        truck_utilization_pct=86.5,
        shipments=mock_shipments,
    )

    # 3. Evaluate Plan Risk across 500 Monte Carlo scenarios
    t_eval_start = time.time()
    risk_dict = evaluate_plan_risk(mock_plan, demands, num_scenarios=500, random_seed=42)
    elapsed_time = time.time() - t_eval_start

    print(f"\nExecution Time: {elapsed_time:.4f} seconds (Target: < 4.0 seconds)")
    print("Risk Dictionary Output:")
    print(risk_dict)
