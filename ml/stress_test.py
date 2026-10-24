import sqlite3
import time
from typing import List, Dict, Any
import numpy as np
import pandas as pd

from common.contracts import PlanResult, TargetDemand, ShipmentLine

DB_PATH = "optiroute_data.sqlite"


def evaluate_plan_risk(
    plan_result: PlanResult,
    target_demands: List[TargetDemand],
    num_scenarios: int = 500,
    random_seed: int = 42,
) -> Dict[str, Any]:
    """
    Vectorized Monte Carlo stress-tester evaluating plan resilience across 500 scenarios.
    
    Perturbs P50 store-SKU demand by regional lognormal shocks beta ~ LogNormal(0, 0.15),
    samples Poisson demand, computes inventory coverage, stockout costs, fill rates,
    and CVaR-95 tail risk.
    """
    if not target_demands:
        return {
            "median_fill_rate": 0.0,
            "p_stockout": 0.0,
            "cvar_95_unmet_cost": 0.0,
            "scenarios_evaluated": 0,
        }

    # 1. Connect to SQLite and fetch SKU margins
    conn = sqlite3.connect(DB_PATH)
    df_skus = pd.read_sql_query("SELECT sku_id, margin FROM skus", conn)
    conn.close()
    margin_map = dict(zip(df_skus["sku_id"], df_skus["margin"]))

    # 2. Extract arrays for all (store_id, sku_id) pairs
    unique_stores = sorted(list(set(td.store_id for td in target_demands)))
    store_to_region = {}
    for s in unique_stores:
        try:
            # Stores are formatted like 'STORE_01' -> (index - 1) % 3
            s_idx = int(s.replace("STORE_", "")) - 1
            store_to_region[s] = s_idx % 3
        except (ValueError, TypeError):
            store_to_region[s] = 0

    pair_regions = np.array([store_to_region.get(td.store_id, 0) for td in target_demands])
    p50_array = np.array([td.p50_demand for td in target_demands], dtype=np.float64)
    proj_stock_array = np.array([td.projected_stock_delivery for td in target_demands], dtype=np.float64)
    margins_array = np.array([margin_map.get(td.sku_id, 0.0) for td in target_demands], dtype=np.float64)

    # 3. Compute available inventory = projected_stock_delivery + shipped units from plan_result
    shipped_map = {}
    if plan_result and plan_result.shipments:
        for s in plan_result.shipments:
            key = (s.store_id, s.sku_id)
            shipped_map[key] = shipped_map.get(key, 0) + s.units_shipped

    shipped_array = np.array(
        [shipped_map.get((td.store_id, td.sku_id), 0) for td in target_demands],
        dtype=np.float64,
    )
    available_inventory = proj_stock_array + shipped_array  # Shape: (num_pairs,)

    # 4. Vectorized demand simulation for 500 scenarios
    rng = np.random.default_rng(random_seed)
    num_regions = 3
    # Regional shock factor beta ~ LogNormal(mean=0.0, sigma=0.15)
    beta_regions = rng.lognormal(mean=0.0, sigma=0.15, size=(num_scenarios, num_regions))
    beta = beta_regions[:, pair_regions]  # Shape: (num_scenarios, num_pairs)

    lam = np.maximum(0.01, p50_array * beta)
    simulated_demand = rng.poisson(lam)  # Shape: (num_scenarios, num_pairs)

    # 5. Calculate delivered, unmet, stockout costs, and fill rate
    delivered_units = np.minimum(simulated_demand, available_inventory)
    unmet_units = np.maximum(0, simulated_demand - available_inventory)

    # Stockout cost = sum(unmet_units * sku_margin)
    scenario_stockout_costs = np.sum(unmet_units * margins_array, axis=1)  # Shape: (num_scenarios,)

    # Scenario fill rate = sum(delivered_units) / sum(simulated_demand)
    total_delivered = np.sum(delivered_units, axis=1)
    total_demand = np.sum(simulated_demand, axis=1)
    scenario_fill_rates = total_delivered / np.maximum(total_demand, 1e-9)

    # 6. Compute summary metrics across scenarios
    # median_fill_rate: 50th percentile of scenario fill rates (float percentage)
    median_fill_rate = float(round(float(np.percentile(scenario_fill_rates * 100.0, 50)), 2))

    # p_stockout: percentage of scenarios where unmet demand > 0
    total_unmet = np.sum(unmet_units, axis=1)
    p_stockout = float(round(float(np.mean(total_unmet > 0) * 100.0), 2))

    # cvar_95_unmet_cost: average stockout financial loss in worst 5% of simulated scenarios
    worst_n = max(1, int(round(num_scenarios * 0.05)))
    sorted_costs = np.sort(scenario_stockout_costs)
    cvar_95_unmet_cost = float(round(float(np.mean(sorted_costs[-worst_n:])), 2))

    return {
        "median_fill_rate": median_fill_rate,
        "p_stockout": p_stockout,
        "cvar_95_unmet_cost": cvar_95_unmet_cost,
        "scenarios_evaluated": int(num_scenarios),
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
