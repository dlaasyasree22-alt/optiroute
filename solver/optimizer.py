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


STOCKOUT_GOODWILL_FACTOR = 0.5


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
        "volume": ["volume_footprint", "volume", "unit_volume", "volume_m3", "vol", "v_s"],
        "margin": ["margin", "unit_margin", "profit_margin", "selling_price", "price", "m_s"],
        "holding_cost": ["holding_cost", "holding_rate", "h_s"],
        "is_perishable": ["is_perishable", "perishable", "is_fresh"],
        "min_shelf_life": ["min_life_at_delivery", "min_shelf_life", "min_delivery_shelf_life", "shelf_life_min", "mu_s"],
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
        "variable_cost": ["variable_unit_cost", "variable_cost", "variable_freight", "variable_freight_cost", "cost_per_unit", "variable_cost_per_unit", "cost_var", "c_wl"],
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
        "quantity": ["quantity_units", "quantity", "batch_quantity", "units", "stock", "inventory_qty", "qty", "q_b"],
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
    use_shelf_life: bool = True,
    mip_gap: float = 0.01,
    time_limit: float = 20.0,
    closed_lanes: Optional[List[Tuple[str, str]]] = None,
    relax_cases: bool = True,
) -> PlanResult:
    """
    Solves the joint multi-echelon replenishment allocation using HiGHS MILP.

    Decision Variables:
      x[b, l] : integer number of case packs of batch b shipped to store l.
      y[w, l] : integer number of 500-unit trucks on lane (w, l).
      u[s, l] : continuous shortfall slack (unmet demand) for SKU s at store l.
      e[b, l] : continuous expected store expiry of batch b at store l (perishables only).

    Constraints:
      1. Batch supply: sum_l (case_pack_size * x[b, l]) <= batch_quantity
      2. Truck volume: sum_{b in w} (volume * case_pack_size * x[b, l]) <= truck_capacity * y[w, l]
      3. Fulfillment: projected_stock + sum_b (case_pack_size * x[b, l]) + u[s, l] >= target_q_demand
      4. Expected expiry: e[b, l] >= case_pack_size * x[b, l] - daily_p50 * life_at_arrival
      5. Forced shipments (optional): sum_{b in w, s(b)=s} (case_pack_size * x[b, l]) >= min_units
      Shelf-life pruning: x[b, l] = 0 when life at arrival < min_life_at_delivery.

    With relax_cases=True, x is solved as continuous (trucks y stay integer) so HiGHS
    proves optimality in seconds; cases are then rounded down and topped up greedily
    into spare truck space, which keeps every constraint satisfied.

    Objective:
      min fixed truck costs + variable freight + margin * u + unit_cost * e + tie_breaker * days_to_expiry * x
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
    sku_info = _sku_info(skus_df)
    lane_info = _lane_info(lanes_df)
    closed = {(str(w), str(l)) for (w, l) in (closed_lanes or [])}

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

    valid_lanes = [
        (w, l) for (w, l) in lane_info.keys()
        if (not all_stores or l in all_stores) and (w, l) not in closed
    ]
    forced_pairs = {(str(fc["sku_id"]), str(fc["store_id"])) for fc in (forced_shipments or [])}

    # 2. Variable Indexing (x only where the SKU is wanted at the store and shelf life allows)
    x_keys, x_index = [], {}
    for b_idx, b in enumerate(batches):
        w, s = b["warehouse_id"], b["sku_id"]
        for (lw, l) in valid_lanes:
            if lw != w:
                continue
            if (s, l) not in demand_map and (s, l) not in forced_pairs:
                continue
            arrival_life = b["days_to_expiry"] - lane_info[(w, l)]["lead_time"]
            if use_shelf_life and arrival_life < sku_info[s]["min_life"]:
                continue
            x_index[(b["batch_id"], l)] = len(x_keys)
            x_keys.append((b_idx, l))

    n_x = len(x_keys)
    y_keys, y_index = [], {}
    for (w, l) in valid_lanes:
        y_index[(w, l)] = n_x + len(y_keys)
        y_keys.append((w, l))

    u_keys, u_index = [], {}
    for (s, l) in demand_map.keys():
        u_index[(s, l)] = n_x + len(y_keys) + len(u_keys)
        u_keys.append((s, l))

    # Expected expiry variables for perishable batch-store pairs that could over-ship
    e_index, e_life_demand = {}, {}
    if use_shelf_life:
        base = n_x + len(y_keys) + len(u_keys)
        for var_idx, (b_idx, l) in enumerate(x_keys):
            b = batches[b_idx]
            s = b["sku_id"]
            if not sku_info[s]["is_perishable"] or (s, l) not in demand_map:
                continue
            td = demand_map[(s, l)]
            daily = td.p50_demand / max(1, td.horizon_days)
            arrival_life = b["days_to_expiry"] - lane_info[(b["warehouse_id"], l)]["lead_time"]
            sellable = daily * max(0.0, arrival_life)
            if sellable < b["quantity"]:
                e_index[var_idx] = base + len(e_index)
                e_life_demand[var_idx] = sellable

    n_vars = n_x + len(y_keys) + len(u_keys) + len(e_index)

    # 3. Objective Vector c
    c = np.zeros(n_vars, dtype=float)
    for var_idx, (b_idx, l) in enumerate(x_keys):
        b = batches[b_idx]
        var_freight_rate = lane_info[(b["warehouse_id"], l)]["variable_cost"]
        c[var_idx] = (var_freight_rate * b["case_pack_size"]) + (tie_breaker_weight * b["days_to_expiry"])
    for (w, l), var_idx in y_index.items():
        c[var_idx] = lane_info[(w, l)]["fixed_cost"]
    for (s, l), var_idx in u_index.items():
        c[var_idx] = sku_info[s]["stockout_penalty"]
    for x_idx, var_idx in e_index.items():
        c[var_idx] = sku_info[batches[x_keys[x_idx][0]]["sku_id"]]["unit_cost"]

    # 4. Integrality and Bounds
    integrality = np.zeros(n_vars, dtype=int)
    lb, ub = np.zeros(n_vars, dtype=float), np.full(n_vars, np.inf, dtype=float)
    for var_idx, (b_idx, _) in enumerate(x_keys):
        integrality[var_idx] = 0 if relax_cases else 1
        ub[var_idx] = max(0, int(batches[b_idx]["quantity"] // batches[b_idx]["case_pack_size"]))
    for var_idx in y_index.values():
        integrality[var_idx] = 1
        ub[var_idx] = 1000.0
    bounds = Bounds(lb=lb, ub=ub)

    # Index x variables by batch, lane and (sku, store) once to keep assembly linear
    x_by_batch: Dict[int, List[int]] = {}
    x_by_lane: Dict[Tuple[str, str], List[int]] = {}
    x_by_pair: Dict[Tuple[str, str], List[int]] = {}
    for var_idx, (b_idx, l) in enumerate(x_keys):
        b = batches[b_idx]
        x_by_batch.setdefault(b_idx, []).append(var_idx)
        x_by_lane.setdefault((b["warehouse_id"], l), []).append(var_idx)
        x_by_pair.setdefault((b["sku_id"], l), []).append(var_idx)

    # 5. Constraints Assembly
    n_forced = len(forced_shipments) if forced_shipments else 0
    total_constraints = len(batches) + len(valid_lanes) + len(demand_map) + len(e_index) + n_forced
    A = dok_matrix((total_constraints, n_vars), dtype=float)
    lhs = np.zeros(total_constraints, dtype=float)
    rhs = np.zeros(total_constraints, dtype=float)
    row_idx = 0

    # Constraint 1: Batch supply
    for b_idx, b in enumerate(batches):
        for var_idx in x_by_batch.get(b_idx, []):
            A[row_idx, var_idx] = b["case_pack_size"]
        lhs[row_idx], rhs[row_idx] = -np.inf, float(b["quantity"])
        row_idx += 1

    # Constraint 2: Truck volume
    for (w, l) in valid_lanes:
        A[row_idx, y_index[(w, l)]] = -truck_capacity
        for var_idx in x_by_lane.get((w, l), []):
            b = batches[x_keys[var_idx][0]]
            A[row_idx, var_idx] = b["volume"] * b["case_pack_size"]
        lhs[row_idx], rhs[row_idx] = -np.inf, 0.0
        row_idx += 1

    # Constraint 3: Fulfillment balance
    for (s, l), td in demand_map.items():
        A[row_idx, u_index[(s, l)]] = 1.0
        for var_idx in x_by_pair.get((s, l), []):
            A[row_idx, var_idx] = float(batches[x_keys[var_idx][0]]["case_pack_size"])
        lhs[row_idx] = max(0.0, td.target_q_demand - td.projected_stock_delivery)
        rhs[row_idx] = np.inf
        row_idx += 1

    # Constraint 4: Expected store expiry  e - k*x >= -sellable
    for x_idx, var_idx in e_index.items():
        A[row_idx, var_idx] = 1.0
        A[row_idx, x_idx] = -float(batches[x_keys[x_idx][0]]["case_pack_size"])
        lhs[row_idx], rhs[row_idx] = -e_life_demand[x_idx], np.inf
        row_idx += 1

    # Constraint 5: Forced shipments (Counterfactual lower-bound)
    for fc in (forced_shipments or []):
        f_sku, f_wh, f_store = str(fc["sku_id"]), str(fc["warehouse_id"]), str(fc["store_id"])
        for var_idx in x_by_pair.get((f_sku, f_store), []):
            b = batches[x_keys[var_idx][0]]
            if b["warehouse_id"] == f_wh:
                A[row_idx, var_idx] = float(b["case_pack_size"])
        lhs[row_idx], rhs[row_idx] = float(fc["min_units"]), np.inf
        row_idx += 1

    constraints = LinearConstraint(A.tocsc(), lhs, rhs)

    # 6. Solve MILP using HiGHS
    res = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": time_limit, "mip_rel_gap": mip_gap},
    )

    solve_time = time.time() - start_time
    if res.status == 0:
        solver_status = "OPTIMAL"
    elif res.x is not None:
        solver_status = "TIME_LIMIT"
    else:
        solver_status = "INFEASIBLE"

    shipments: List[ShipmentLine] = []
    if res.x is not None:
        sol = res.x
        int_cases = np.round(sol[:n_x]).astype(int) if not relax_cases else _round_and_top_up(
            sol, x_keys, x_by_pair, x_by_lane, batches, demand_map, y_index, truck_capacity, forced_shipments
        )
        for var_idx, (b_idx, l) in enumerate(x_keys):
            cases = int(int_cases[var_idx])
            if cases > 0:
                b = batches[b_idx]
                w = b["warehouse_id"]
                shipments.append(
                    ShipmentLine(
                        sku_id=b["sku_id"],
                        batch_id=b["batch_id"],
                        warehouse_id=w,
                        store_id=l,
                        cases_shipped=cases,
                        units_shipped=cases * b["case_pack_size"],
                        truck_id="",
                        days_to_expiry_at_arrival=max(0, int(b["days_to_expiry"] - lane_info[(w, l)]["lead_time"])),
                        reason_code="",
                    )
                )

    plan_result = PlanResult(
        plan_id=f"plan_{int(time.time() * 1000)}",
        solver_status=solver_status,
        solve_time_seconds=round(solve_time, 3),
        total_freight_cost=0.0,
        unmet_demand_units=0.0,
        wastage_units=0.0,
        truck_utilization_pct=0.0,
        shipments=shipments,
        mip_gap=float(getattr(res, "mip_gap", 0.0) or 0.0),
    )

    if enable_post_solve_repair and solver_status != "INFEASIBLE":
        from solver.fefo_repair import repair_fefo_violations, assert_zero_fefo_violations
        plan_result = repair_fefo_violations(plan_result, db_path=db_path)
        assert_zero_fefo_violations(plan_result, db_path=db_path)

    return finalize_plan(plan_result, target_demands, db_path, truck_capacity, forced_shipments)


def _round_and_top_up(sol, x_keys, x_by_pair, x_by_lane, batches, demand_map, y_index, truck_capacity, forced_shipments):
    """Rounds relaxed case counts down, then adds whole cases where trucks and batches have room."""
    n_x = len(x_keys)
    cases = np.floor(sol[:n_x] + 1e-6).astype(int)
    # Planner-forced lines round up so the forced minimum is always honoured
    for fc in (forced_shipments or []):
        for v in x_by_pair.get((str(fc["sku_id"]), str(fc["store_id"])), []):
            if batches[x_keys[v][0]]["warehouse_id"] == str(fc["warehouse_id"]):
                cases[v] = int(np.ceil(sol[v] - 1e-6))
    batch_left = {i: int(b["quantity"] // b["case_pack_size"]) for i, b in enumerate(batches)}
    for var_idx, (b_idx, _) in enumerate(x_keys):
        batch_left[b_idx] -= cases[var_idx]
    lane_room = {}
    for lane, var_list in x_by_lane.items():
        used = sum(cases[v] * batches[x_keys[v][0]]["volume"] * batches[x_keys[v][0]]["case_pack_size"] for v in var_list)
        lane_room[lane] = round(sol[y_index[lane]]) * truck_capacity - used

    # Shortfall per pair in units: demand targets plus any forced minimums
    needs = {}
    for (s, l), td in demand_map.items():
        needs[(s, l)] = max(0.0, td.target_q_demand - td.projected_stock_delivery)
    for fc in (forced_shipments or []):
        key = (str(fc["sku_id"]), str(fc["store_id"]))
        needs[key] = max(needs.get(key, 0.0), float(fc["min_units"]))
    for key in needs:
        for v in x_by_pair.get(key, []):
            needs[key] -= cases[v] * batches[x_keys[v][0]]["case_pack_size"]

    for key, short in needs.items():
        if short <= 0:
            continue
        # Oldest eligible batch first, using lanes whose trucks already have room
        for v in sorted(x_by_pair.get(key, []), key=lambda v: batches[x_keys[v][0]]["days_to_expiry"]):
            b_idx, l = x_keys[v]
            b = batches[b_idx]
            lane = (b["warehouse_id"], l)
            case_vol = b["volume"] * b["case_pack_size"]
            while short > 0 and batch_left[b_idx] > 0 and lane_room.get(lane, 0) >= case_vol:
                cases[v] += 1
                batch_left[b_idx] -= 1
                lane_room[lane] -= case_vol
                short -= b["case_pack_size"]
            if short <= 0:
                break
    return cases


def _sku_info(skus_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    return {
        str(r["sku_id"]): {
            "case_pack_size": int(r.get("case_pack_size", 1)),
            "volume": float(r.get("volume", 1.0)),
            "margin": float(r.get("margin", 50.0)),
            "unit_cost": float(r.get("unit_cost", 50.0)),
            # A lost sale costs the margin plus customer goodwill, valued at half the unit cost
            "stockout_penalty": float(r.get("margin", 50.0)) + STOCKOUT_GOODWILL_FACTOR * float(r.get("unit_cost", 50.0)),
            "min_life": int(r.get("min_shelf_life", 0)),
            "is_perishable": bool(r.get("is_perishable", 0)),
        } for _, r in skus_df.iterrows()
    }


def _lane_info(lanes_df: pd.DataFrame) -> Dict[Tuple[str, str], Dict[str, float]]:
    return {
        (str(r["warehouse_id"]), str(r["store_id"])): {
            "fixed_cost": float(r.get("fixed_cost", 5000.0)),
            "variable_cost": float(r.get("variable_cost", 2.0)),
            "lead_time": int(r.get("lead_time", 1)),
        } for _, r in lanes_df.iterrows()
    }


def finalize_plan(
    plan: PlanResult,
    target_demands: List[TargetDemand],
    db_path: Optional[str] = None,
    truck_capacity: float = 500.0,
    forced_shipments: Optional[List[Dict[str, Any]]] = None,
) -> PlanResult:
    """
    Packs shipment lines onto trucks, assigns plain reason codes and computes plan KPIs.
    Runs after FEFO repair so every number reflects the batches that actually ship.
    """
    skus_df, lanes_df, _ = load_optimizer_data(db_path)
    sku_info = _sku_info(skus_df)
    lane_info = _lane_info(lanes_df)
    demand_map = {(td.sku_id, td.store_id): td for td in target_demands}
    forced = {(str(f["sku_id"]), str(f["warehouse_id"]), str(f["store_id"])) for f in (forced_shipments or [])}

    # Truck packing per lane: oldest stock loaded first, lines split across trucks by case
    by_lane: Dict[Tuple[str, str], List[ShipmentLine]] = {}
    for sh in plan.shipments:
        by_lane.setdefault((sh.warehouse_id, sh.store_id), []).append(sh)

    packed: List[ShipmentLine] = []
    freight, total_vol, trucks_used = 0.0, 0.0, 0
    for (w, l), lines in sorted(by_lane.items()):
        lines.sort(key=lambda x: (x.days_to_expiry_at_arrival, x.sku_id))
        store_no = l.replace("STORE_", "S")
        truck_no, room = 0, 0.0
        for sh in lines:
            vol_per_case = sku_info[sh.sku_id]["volume"] * sku_info[sh.sku_id]["case_pack_size"]
            remaining = sh.cases_shipped
            while remaining > 0:
                fit = int(room // vol_per_case) if vol_per_case > 0 else remaining
                if fit <= 0:
                    truck_no += 1
                    room = truck_capacity
                    continue
                take = min(fit, remaining)
                room -= take * vol_per_case
                remaining -= take
                packed.append(ShipmentLine(
                    sku_id=sh.sku_id, batch_id=sh.batch_id, warehouse_id=w, store_id=l,
                    cases_shipped=take, units_shipped=take * sku_info[sh.sku_id]["case_pack_size"],
                    truck_id=f"TRUCK_{w}_{store_no}_{truck_no}",
                    days_to_expiry_at_arrival=sh.days_to_expiry_at_arrival,
                    reason_code=sh.reason_code,
                ))
        trucks_used += truck_no
        lane_units = sum(x.units_shipped for x in lines)
        total_vol += sum(x.units_shipped * sku_info[x.sku_id]["volume"] for x in lines)
        freight += truck_no * lane_info[(w, l)]["fixed_cost"] + lane_units * lane_info[(w, l)]["variable_cost"]

    # Per (sku, store): shipped units, unmet demand and expected store expiry
    shipped: Dict[Tuple[str, str], List[ShipmentLine]] = {}
    for sh in packed:
        shipped.setdefault((sh.sku_id, sh.store_id), []).append(sh)

    total_demand = unmet = waste = stockout_cost = writeoff_cost = 0.0
    for key, td in demand_map.items():
        lines = shipped.get(key, [])
        units = sum(x.units_shipped for x in lines)
        short = max(0.0, td.target_q_demand - td.projected_stock_delivery - units)
        total_demand += td.target_q_demand
        unmet += short
        stockout_cost += short * sku_info[td.sku_id]["stockout_penalty"]
        if sku_info[td.sku_id]["is_perishable"] and lines:
            # Existing store stock sells first, then shipped batches in expiry order
            daily = td.p50_demand / max(1, td.horizon_days)
            ahead = td.projected_stock_delivery
            for x in sorted(lines, key=lambda x: x.days_to_expiry_at_arrival):
                sellable = max(0.0, daily * x.days_to_expiry_at_arrival - ahead)
                exp = max(0.0, x.units_shipped - sellable)
                waste += exp
                writeoff_cost += exp * sku_info[td.sku_id]["unit_cost"]
                ahead += x.units_shipped

    # Plain reason codes, most specific first
    for sh in packed:
        td = demand_map.get((sh.sku_id, sh.store_id))
        info = sku_info[sh.sku_id]
        if (sh.sku_id, sh.warehouse_id, sh.store_id) in forced:
            sh.reason_code = "PLANNER_FORCED"
        elif info["is_perishable"] and sh.days_to_expiry_at_arrival <= info["min_life"] * 1.5:
            sh.reason_code = "EXPIRY_PRIORITY"
        elif td is not None and td.projected_stock_delivery < td.p50_demand:
            sh.reason_code = "STOCKOUT_PROTECTION"
        else:
            sh.reason_code = "SAFETY_BUFFER"

    plan.shipments = packed
    plan.total_freight_cost = round(freight, 2)
    plan.unmet_demand_units = round(unmet, 2)
    plan.wastage_units = round(waste, 2)
    plan.truck_utilization_pct = round(total_vol / (trucks_used * truck_capacity) * 100.0, 2) if trucks_used else 0.0
    plan.total_demand_units = round(total_demand, 2)
    plan.stockout_cost = round(stockout_cost, 2)
    plan.writeoff_cost = round(writeoff_cost, 2)
    plan.trucks_used = trucks_used
    return plan


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
