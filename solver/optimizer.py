"""
OptiRoute MILP Optimizer (Dev B - solver/optimizer.py)
Implements joint multi-echelon FMCG replenishment allocation using scipy.optimize.milp (HiGHS).
"""

import os
import sys
import time
import sqlite3
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import dok_matrix

# Ensure repo root is on sys.path for direct script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.contracts import TargetDemand, ShipmentLine, PlanResult
try:
    from common.db import DB_PATH, get_connection, load_table
except ImportError:
    DB_PATH = "optiroute_data.sqlite"
    get_connection = None
    load_table = None


def _normalize_columns(df: pd.DataFrame, mappings: Dict[str, List[str]]) -> pd.DataFrame:
    """Normalizes DataFrame column names to standard keys based on candidate lists."""
    df = df.copy()
    col_map = {}
    lower_cols = {c.lower(): c for c in df.columns}
    for standard_key, candidates in mappings.items():
        for cand in candidates:
            if cand.lower() in lower_cols:
                col_map[lower_cols[cand.lower()]] = standard_key
                break
    return df.rename(columns=col_map)


def load_optimizer_data(db_path: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Loads and standardizes skus, lanes, and inventory_batches tables."""
    path = db_path or DB_PATH
    if not os.path.exists(path):
        alt_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "optiroute_data.sqlite")
        if os.path.exists(alt_path):
            path = alt_path

    conn = sqlite3.connect(path)
    try:
        skus_df = pd.read_sql_query("SELECT * FROM skus", conn)
        lanes_df = pd.read_sql_query("SELECT * FROM lanes", conn)
        batches_df = pd.read_sql_query("SELECT * FROM inventory_batches", conn)
    finally:
        conn.close()

    # Normalize skus
    sku_mappings = {
        "sku_id": ["sku_id", "id", "sku", "product_id"],
        "case_pack_size": ["case_pack_size", "pack_size", "case_pack", "case_size", "pack", "k_s"],
        "volume": ["volume", "unit_volume", "volume_m3", "vol", "v_s"],
        "margin": ["margin", "unit_margin", "profit_margin", "selling_price", "price", "m_s"],
        "holding_cost": ["holding_cost", "holding_rate", "h_s"],
        "is_perishable": ["is_perishable", "perishable", "is_fresh"],
        "min_shelf_life": ["min_shelf_life", "min_delivery_shelf_life", "shelf_life_min", "mu_s"],
    }
    skus_df = _normalize_columns(skus_df, sku_mappings)
    if "case_pack_size" not in skus_df.columns:
        skus_df["case_pack_size"] = 1
    if "volume" not in skus_df.columns:
        skus_df["volume"] = 1.0
    if "margin" not in skus_df.columns:
        skus_df["margin"] = 50.0

    # Normalize lanes
    lane_mappings = {
        "warehouse_id": ["warehouse_id", "source_warehouse", "origin_dc", "origin", "warehouse", "from_dc", "w"],
        "store_id": ["store_id", "destination_store", "destination", "dest_store", "store", "to_store", "l"],
        "fixed_cost": ["fixed_cost", "fixed_truck_cost", "fixed_freight_cost", "truck_cost", "cost_fixed", "fixed_freight", "f_wl"],
        "variable_cost": ["variable_cost", "variable_freight", "variable_freight_cost", "cost_per_unit", "variable_cost_per_unit", "cost_var", "c_wl"],
        "lead_time": ["lead_time", "lead_time_days", "transit_days", "transit_time", "tau_wl"],
    }
    lanes_df = _normalize_columns(lanes_df, lane_mappings)
    if "fixed_cost" not in lanes_df.columns:
        lanes_df["fixed_cost"] = 5000.0
    if "variable_cost" not in lanes_df.columns:
        lanes_df["variable_cost"] = 2.0
    if "lead_time" not in lanes_df.columns:
        lanes_df["lead_time"] = 1

    # Normalize inventory_batches
    batch_mappings = {
        "batch_id": ["batch_id", "id", "batch", "lot_id", "lot"],
        "sku_id": ["sku_id", "sku", "product_id"],
        "warehouse_id": ["warehouse_id", "warehouse", "source_warehouse", "dc_id", "dc"],
        "quantity": ["quantity", "batch_quantity", "units", "stock", "inventory_qty", "qty", "q_b"],
        "days_to_expiry": ["days_to_expiry", "expiry_days", "shelf_life_remaining", "days_left", "shelf_life_days", "d_b"],
    }
    batches_df = _normalize_columns(batches_df, batch_mappings)
    if "quantity" not in batches_df.columns:
        batches_df["quantity"] = 0
    if "days_to_expiry" not in batches_df.columns:
        batches_df["days_to_expiry"] = 30

    return skus_df, lanes_df, batches_df


def solve_allocation(
    target_demands: List[TargetDemand],
    db_path: Optional[str] = None,
    truck_capacity: float = 500.0,
    tie_breaker_weight: float = 0.001,
    forced_shipments: Optional[List[Dict[str, Any]]] = None,
    enable_post_solve_repair: bool = True,
) -> PlanResult:
    """
    Solves the joint multi-echelon replenishment allocation using HiGHS MILP.

    Decision Variables:
      x[b, l] : integer number of case packs of batch b shipped to store l.
      y[w, l] : integer number of 500-unit trucks on lane (w, l).
      u[s, l] : continuous shortfall slack (unmet demand) for SKU s at store l.

    Constraints:
      1. Batch supply: sum_l (case_pack_size * x[b, l]) <= batch_quantity
      2. Truck volume: sum_{b in w} (volume * case_pack_size * x[b, l]) <= truck_capacity * y[w, l]
      3. Fulfillment: stock_on_hand + sum_b (case_pack_size * x[b, l]) + u[s, l] >= target_q_demand
      4. Forced shipments (optional): sum_{b in w, s(b)=s} (case_pack_size * x[b, l]) >= min_units

    Objective:
      min sum fixed_truck_costs + sum variable_freight + sum (margin * u[s, l]) + sum (0.001 * days_to_expiry * x[b, l])
    """
    start_time = time.time()

    # Fast path: Empty demands
    if not target_demands and not forced_shipments:
        return PlanResult(
            plan_id=f"plan_{int(time.time())}",
            solver_status="OPTIMAL",
            solve_time_seconds=0.0,
            total_freight_cost=0.0,
            unmet_demand_units=0.0,
            wastage_units=0.0,
            truck_utilization_pct=0.0,
            shipments=[],
        )

    # 1. Load Data
    skus_df, lanes_df, batches_df = load_optimizer_data(db_path)

    sku_info = {
        str(r["sku_id"]): {
            "case_pack_size": int(r.get("case_pack_size", 1)),
            "volume": float(r.get("volume", 1.0)),
            "margin": float(r.get("margin", 50.0)),
        } for _, r in skus_df.iterrows()
    }

    lane_info = {
        (str(r["warehouse_id"]), str(r["store_id"])): {
            "fixed_cost": float(r.get("fixed_cost", 5000.0)),
            "variable_cost": float(r.get("variable_cost", 2.0)),
            "lead_time": int(r.get("lead_time", 1)),
        } for _, r in lanes_df.iterrows()
    }

    batches = []
    for _, row in batches_df.iterrows():
        b_id, s_id, w_id = str(row["batch_id"]), str(row["sku_id"]), str(row["warehouse_id"])
        qty, d_exp = float(row.get("quantity", 0)), float(row.get("days_to_expiry", 30))
        if qty > 0 and s_id in sku_info:
            batches.append({
                "batch_id": b_id,
                "sku_id": s_id,
                "warehouse_id": w_id,
                "quantity": qty,
                "days_to_expiry": d_exp,
                "case_pack_size": sku_info[s_id]["case_pack_size"],
                "volume": sku_info[s_id]["volume"],
            })

    demand_map = {(str(td.sku_id), str(td.store_id)): td for td in target_demands}
    all_stores = {str(td.store_id) for td in target_demands}
    if forced_shipments:
        for fc in forced_shipments:
            all_stores.add(str(fc["store_id"]))

    valid_lanes = [(w, l) for (w, l) in lane_info.keys() if (not all_stores or l in all_stores)]

    # 2. Variable Indexing
    x_keys, x_index = [], {}
    for b_idx, b in enumerate(batches):
        w = b["warehouse_id"]
        for (lw, l) in valid_lanes:
            if lw == w:
                key = (b["batch_id"], l)
                x_index[key] = len(x_keys)
                x_keys.append((b_idx, l))

    y_keys, y_index = [], {}
    for (w, l) in valid_lanes:
        key = (w, l)
        y_index[key] = len(x_keys) + len(y_keys)
        y_keys.append(key)

    u_keys, u_index = [], {}
    for (s, l) in demand_map.keys():
        key = (s, l)
        u_index[key] = len(x_keys) + len(y_keys) + len(u_keys)
        u_keys.append(key)

    n_vars = len(x_keys) + len(y_keys) + len(u_keys)

    # 3. Objective Vector c
    c = np.zeros(n_vars, dtype=float)
    for key, var_idx in x_index.items():
        b_id, l = key
        b_idx, _ = x_keys[var_idx]
        b = batches[b_idx]
        w = b["warehouse_id"]
        var_freight_rate = lane_info[(w, l)]["variable_cost"]
        k_s, d_b = b["case_pack_size"], b["days_to_expiry"]
        c[var_idx] = (var_freight_rate * k_s) + (tie_breaker_weight * d_b)

    for key, var_idx in y_index.items():
        w, l = key
        c[var_idx] = lane_info[(w, l)]["fixed_cost"]

    for key, var_idx in u_index.items():
        s, l = key
        c[var_idx] = sku_info.get(s, {}).get("margin", 50.0)

    # 4. Integrality and Bounds
    integrality = np.zeros(n_vars, dtype=int)
    lb, ub = np.zeros(n_vars, dtype=float), np.full(n_vars, np.inf, dtype=float)

    for key, var_idx in x_index.items():
        integrality[var_idx] = 1
        b_idx, _ = x_keys[var_idx]
        ub[var_idx] = max(0, int(batches[b_idx]["quantity"] // batches[b_idx]["case_pack_size"]))

    for key, var_idx in y_index.items():
        integrality[var_idx] = 1
        ub[var_idx] = 1000.0

    for key, var_idx in u_index.items():
        integrality[var_idx] = 0
        ub[var_idx] = np.inf

    bounds = Bounds(lb=lb, ub=ub)

    # 5. Constraints Assembly
    n_forced = len(forced_shipments) if forced_shipments else 0
    total_constraints = len(batches) + len(valid_lanes) + len(demand_map) + n_forced

    A = dok_matrix((total_constraints, n_vars), dtype=float)
    lhs = np.zeros(total_constraints, dtype=float)
    rhs = np.zeros(total_constraints, dtype=float)

    row_idx = 0

    # Constraint 1: Batch supply
    for b_idx, b in enumerate(batches):
        b_id, k_s, w = b["batch_id"], b["case_pack_size"], b["warehouse_id"]
        for (lw, l) in valid_lanes:
            if lw == w and (b_id, l) in x_index:
                A[row_idx, x_index[(b_id, l)]] = k_s
        lhs[row_idx] = -np.inf
        rhs[row_idx] = float(b["quantity"])
        row_idx += 1

    # Constraint 2: Truck volume
    for (w, l) in valid_lanes:
        A[row_idx, y_index[(w, l)]] = -truck_capacity
        for b_idx, b in enumerate(batches):
            if b["warehouse_id"] == w and (b["batch_id"], l) in x_index:
                A[row_idx, x_index[(b["batch_id"], l)]] = b["volume"] * b["case_pack_size"]
        lhs[row_idx] = -np.inf
        rhs[row_idx] = 0.0
        row_idx += 1

    # Constraint 3: Fulfillment balance
    for (s, l), td in demand_map.items():
        A[row_idx, u_index[(s, l)]] = 1.0
        for b_idx, b in enumerate(batches):
            if b["sku_id"] == s and (b["batch_id"], l) in x_index:
                A[row_idx, x_index[(b["batch_id"], l)]] = float(b["case_pack_size"])
        lhs[row_idx] = max(0.0, td.target_q_demand - td.projected_stock_delivery)
        rhs[row_idx] = np.inf
        row_idx += 1

    # Constraint 4: Forced shipments (Counterfactual lower-bound)
    if forced_shipments:
        for fc in forced_shipments:
            f_sku, f_wh, f_store, min_u = str(fc["sku_id"]), str(fc["warehouse_id"]), str(fc["store_id"]), float(fc["min_units"])
            for b_idx, b in enumerate(batches):
                if b["sku_id"] == f_sku and b["warehouse_id"] == f_wh:
                    key = (b["batch_id"], f_store)
                    if key in x_index:
                        A[row_idx, x_index[key]] = float(b["case_pack_size"])
            lhs[row_idx] = min_u
            rhs[row_idx] = np.inf
            row_idx += 1

    A_csc = A.tocsc()
    constraints = LinearConstraint(A_csc, lhs, rhs)

    # 6. Solve MILP using HiGHS
    res = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": 10.0, "mip_rel_gap": 0.01}
    )

    solve_time = time.time() - start_time
    solver_status = "OPTIMAL" if res.success else "INFEASIBLE"

    shipments: List[ShipmentLine] = []
    total_freight, total_vol, total_cap, total_unmet = 0.0, 0.0, 0.0, 0.0

    if res.x is not None:
        sol = res.x
        for (w, l), var_idx in y_index.items():
            n_t = int(round(sol[var_idx]))
            if n_t > 0:
                total_freight += n_t * lane_info[(w, l)]["fixed_cost"]
                total_cap += n_t * truck_capacity

        truck_counters = {(w, l): 1 for (w, l) in valid_lanes}
        for key, var_idx in x_index.items():
            cases = int(round(sol[var_idx]))
            if cases > 0:
                b_id, l = key
                b = batches[x_keys[var_idx][0]]
                w, s = b["warehouse_id"], b["sku_id"]
                units = cases * b["case_pack_size"]
                total_vol += units * b["volume"]
                total_freight += units * lane_info[(w, l)]["variable_cost"]
                t_id = f"TRUCK_{w}_{l}_{truck_counters[(w, l)]}"
                arr_days = max(0, int(b["days_to_expiry"] - lane_info[(w, l)]["lead_time"]))
                shipments.append(
                    ShipmentLine(
                        sku_id=s,
                        batch_id=b_id,
                        warehouse_id=w,
                        store_id=l,
                        cases_shipped=cases,
                        units_shipped=units,
                        truck_id=t_id,
                        days_to_expiry_at_arrival=arr_days,
                        reason_code="STOCKOUT_PROTECTION",
                    )
                )

        for key, var_idx in u_index.items():
            val = float(sol[var_idx])
            if val > 1e-4:
                total_unmet += val

    truck_util = (total_vol / total_cap * 100.0) if total_cap > 0 else 0.0
    plan_id = f"plan_{int(time.time())}"

    plan_result = PlanResult(
        plan_id=plan_id,
        solver_status=solver_status,
        solve_time_seconds=round(solve_time, 3),
        total_freight_cost=round(total_freight, 2),
        unmet_demand_units=round(total_unmet, 2),
        wastage_units=0.0,
        truck_utilization_pct=round(truck_util, 2),
        shipments=shipments,
    )

    if enable_post_solve_repair and solver_status == "OPTIMAL":
        from solver.fefo_repair import repair_fefo_violations, assert_zero_fefo_violations
        plan_result = repair_fefo_violations(plan_result, db_path=db_path)
        assert_zero_fefo_violations(plan_result, db_path=db_path)

    return plan_result


if __name__ == "__main__":
    skus_df, lanes_df, _ = load_optimizer_data()
    test_skus = skus_df["sku_id"].unique()[:5]
    test_stores = lanes_df["store_id"].unique()[:4]

    mock_demands = []
    for s in test_skus:
        for st in test_stores:
            mock_demands.append(
                TargetDemand(
                    sku_id=str(s),
                    store_id=str(st),
                    target_q_demand=120.0,
                    p90_demand=150.0,
                    p50_demand=100.0,
                    projected_stock_delivery=20.0,
                )
            )

    print(f"Running test solve with {len(mock_demands)} target demands...")
    result = solve_allocation(mock_demands)
    print(f"Solver Status: {result.solver_status}")
    print(f"Solve Time: {result.solve_time_seconds}s")
    print(f"Total Freight Cost: {result.total_freight_cost}")
    print(f"Truck Utilization: {result.truck_utilization_pct}%")
    print(f"Number of Shipments: {len(result.shipments)}")
    assert result.solver_status == "OPTIMAL", f"Expected OPTIMAL, got {result.solver_status}"
    assert result.solve_time_seconds < 5.0, f"Solve took too long: {result.solve_time_seconds}s"
    print("ALL TESTS PASSED SUCCESSFULLY!")
