"""
OptiRoute FEFO Repair & Verification Module (Dev B - solver/fefo_repair.py)
Implements an O(|B|) post-solve swap pass to eliminate FEFO violations by prioritizing
older eligible batches while preserving total units, store allocations, and truck assignments.
"""

import dataclasses
import os
import sys
import sqlite3
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import pandas as pd

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.contracts import PlanResult, ShipmentLine
try:
    from common.db import DB_PATH
except ImportError:
    DB_PATH = "optiroute_data.sqlite"


def _normalize_columns(df: pd.DataFrame, mappings: Dict[str, List[str]]) -> pd.DataFrame:
    df = df.copy()
    col_map = {}
    lower_cols = {c.lower(): c for c in df.columns}
    for standard_key, candidates in mappings.items():
        for cand in candidates:
            if cand.lower() in lower_cols:
                col_map[lower_cols[cand.lower()]] = standard_key
                break
    return df.rename(columns=col_map)


def load_fefo_metadata(db_path: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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

    sku_mappings = {
        "sku_id": ["sku_id", "id", "sku", "product_id"],
        "case_pack_size": ["case_pack_size", "pack_size", "case_pack", "case_size", "pack", "k_s"],
        "min_shelf_life": ["min_life_at_delivery", "min_shelf_life", "min_delivery_shelf_life", "shelf_life_min", "mu_s"],
        "margin": ["margin", "unit_margin", "profit_margin", "selling_price", "price", "m_s"],
    }
    skus_df = _normalize_columns(skus_df, sku_mappings)
    if "case_pack_size" not in skus_df.columns:
        skus_df["case_pack_size"] = 1
    if "min_shelf_life" not in skus_df.columns:
        skus_df["min_shelf_life"] = 0

    lane_mappings = {
        "warehouse_id": ["warehouse_id", "source_warehouse", "origin_dc", "origin", "warehouse", "from_dc", "w"],
        "store_id": ["store_id", "destination_store", "destination", "dest_store", "store", "to_store", "l"],
        "lead_time": ["lead_time", "lead_time_days", "transit_days", "transit_time", "tau_wl"],
    }
    lanes_df = _normalize_columns(lanes_df, lane_mappings)
    if "lead_time" not in lanes_df.columns:
        lanes_df["lead_time"] = 1

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


def repair_fefo_violations(plan_result: PlanResult, db_path: Optional[str] = None) -> PlanResult:
    """
    Performs an O(|B|) swap pass transferring shipments to older eligible batches in the same warehouse.
    Preserves exact store allocations, truck assignments, and lane flows.
    """
    if not plan_result.shipments:
        return plan_result

    skus_df, lanes_df, batches_df = load_fefo_metadata(db_path)

    sku_meta = {}
    for _, row in skus_df.iterrows():
        s_id = str(row["sku_id"])
        sku_meta[s_id] = {
            "case_pack_size": int(row.get("case_pack_size", 1)),
            "min_shelf_life": int(row.get("min_shelf_life", 0)),
        }

    lane_lead_times = {}
    for _, row in lanes_df.iterrows():
        w_id = str(row["warehouse_id"])
        st_id = str(row["store_id"])
        lane_lead_times[(w_id, st_id)] = int(row.get("lead_time", 1))

    # Group batches by (warehouse_id, sku_id)
    wh_sku_batches: Dict[Tuple[str, str], List[dict]] = {}
    for _, row in batches_df.iterrows():
        b_id = str(row["batch_id"])
        s_id = str(row["sku_id"])
        w_id = str(row["warehouse_id"])
        qty = float(row.get("quantity", 0))
        d_exp = int(row.get("days_to_expiry", 30))
        k_s = sku_meta.get(s_id, {}).get("case_pack_size", 1)
        key = (w_id, s_id)
        if key not in wh_sku_batches:
            wh_sku_batches[key] = []
        wh_sku_batches[key].append({
            "batch_id": b_id,
            "sku_id": s_id,
            "warehouse_id": w_id,
            "quantity": qty,
            "days_to_expiry": d_exp,
            "case_pack_size": k_s,
        })

    # Sort batches by days_to_expiry ASCENDING (strict FEFO priority)
    for key in wh_sku_batches:
        wh_sku_batches[key].sort(key=lambda b: b["days_to_expiry"])

    # Group shipments by (warehouse_id, sku_id)
    shipments_by_group: Dict[Tuple[str, str], List[ShipmentLine]] = {}
    other_shipments: List[ShipmentLine] = []
    for sh in plan_result.shipments:
        group_key = (sh.warehouse_id, sh.sku_id)
        if group_key in wh_sku_batches:
            if group_key not in shipments_by_group:
                shipments_by_group[group_key] = []
            shipments_by_group[group_key].append(sh)
        else:
            other_shipments.append(sh)

    repaired_shipments: List[ShipmentLine] = []

    for group_key, current_shipments in shipments_by_group.items():
        w_id, s_id = group_key
        batches = wh_sku_batches[group_key]
        k_s = sku_meta.get(s_id, {}).get("case_pack_size", 1)
        min_life = sku_meta.get(s_id, {}).get("min_shelf_life", 0)

        # Capacity tracking in case packs
        batch_remaining_cases = {
            b["batch_id"]: int(b["quantity"] // k_s) for b in batches
        }

        # Greedy allocation to oldest eligible batches
        for sh in current_shipments:
            needed_cases = sh.cases_shipped
            st_id = sh.store_id
            t_id = sh.truck_id
            r_code = sh.reason_code
            lead_time = lane_lead_times.get((w_id, st_id), 1)

            while needed_cases > 0:
                allocated_batch = None
                for b in batches:
                    b_id = b["batch_id"]
                    if batch_remaining_cases[b_id] > 0:
                        arrival_life = b["days_to_expiry"] - lead_time
                        if arrival_life >= min_life or min_life == 0:
                            allocated_batch = b
                            break

                # Fallback to oldest with capacity if shelf life filter isolates
                if allocated_batch is None:
                    for b in batches:
                        if batch_remaining_cases[b["batch_id"]] > 0:
                            allocated_batch = b
                            break

                if allocated_batch is None:
                    repaired_shipments.append(sh)
                    break

                b_id = allocated_batch["batch_id"]
                avail = batch_remaining_cases[b_id]
                take_cases = min(needed_cases, avail)

                batch_remaining_cases[b_id] -= take_cases
                needed_cases -= take_cases

                arrival_life = max(0, int(allocated_batch["days_to_expiry"] - lead_time))
                repaired_shipments.append(
                    ShipmentLine(
                        sku_id=s_id,
                        batch_id=b_id,
                        warehouse_id=w_id,
                        store_id=st_id,
                        cases_shipped=take_cases,
                        units_shipped=take_cases * k_s,
                        truck_id=t_id,
                        days_to_expiry_at_arrival=arrival_life,
                        reason_code=r_code,
                    )
                )

    # Consolidate identical lines
    consolidated: Dict[Tuple, ShipmentLine] = {}
    for sh in repaired_shipments + other_shipments:
        k = (sh.sku_id, sh.batch_id, sh.warehouse_id, sh.store_id, sh.truck_id, sh.reason_code)
        if k not in consolidated:
            consolidated[k] = ShipmentLine(
                sku_id=sh.sku_id,
                batch_id=sh.batch_id,
                warehouse_id=sh.warehouse_id,
                store_id=sh.store_id,
                cases_shipped=sh.cases_shipped,
                units_shipped=sh.units_shipped,
                truck_id=sh.truck_id,
                days_to_expiry_at_arrival=sh.days_to_expiry_at_arrival,
                reason_code=sh.reason_code,
            )
        else:
            existing = consolidated[k]
            existing.cases_shipped += sh.cases_shipped
            existing.units_shipped += sh.units_shipped

    return dataclasses.replace(plan_result, shipments=list(consolidated.values()))


def count_fefo_violations(plan_result: PlanResult, db_path: Optional[str] = None) -> List[str]:
    """
    A violation: a warehouse ships a newer batch of a SKU to a store while an older batch
    of the same SKU sits unpicked, and that older batch would still arrive with at least
    the minimum delivery shelf life on that lane (so it could legally have gone instead).
    """
    skus_df, lanes_df, batches_df = load_fefo_metadata(db_path)

    sku_pack_size = {str(r["sku_id"]): int(r.get("case_pack_size", 1)) for _, r in skus_df.iterrows()}
    sku_min_life = {str(r["sku_id"]): int(r.get("min_shelf_life", 0)) for _, r in skus_df.iterrows()}
    lead = {(str(r["warehouse_id"]), str(r["store_id"])): int(r.get("lead_time", 1)) for _, r in lanes_df.iterrows()}
    batch_expiry, batch_wh_sku, batch_qty = {}, {}, {}
    for _, row in batches_df.iterrows():
        b_id = str(row["batch_id"])
        batch_expiry[b_id] = int(row.get("days_to_expiry", 30))
        batch_wh_sku[b_id] = (str(row["warehouse_id"]), str(row["sku_id"]))
        batch_qty[b_id] = float(row.get("quantity", 0))

    shipped_cases: Dict[str, int] = {}
    shipped_stores: Dict[str, set] = {}
    for sh in plan_result.shipments:
        shipped_cases[sh.batch_id] = shipped_cases.get(sh.batch_id, 0) + sh.cases_shipped
        shipped_stores.setdefault(sh.batch_id, set()).add(sh.store_id)

    batches_by_group: Dict[Tuple[str, str], List[str]] = {}
    for b_id, group in batch_wh_sku.items():
        batches_by_group.setdefault(group, []).append(b_id)

    violations = []
    for (w_id, s_id), b_list in batches_by_group.items():
        k_s = sku_pack_size.get(s_id, 1)
        sorted_batches = sorted(b_list, key=lambda b: batch_expiry[b])
        for i, b_older in enumerate(sorted_batches):
            if int(batch_qty[b_older] // k_s) - shipped_cases.get(b_older, 0) <= 0:
                continue
            for b_newer in sorted_batches[i + 1:]:
                if batch_expiry[b_newer] <= batch_expiry[b_older]:
                    continue
                for st in shipped_stores.get(b_newer, ()):
                    if batch_expiry[b_older] - lead.get((w_id, st), 1) >= sku_min_life.get(s_id, 0):
                        violations.append(
                            f"{w_id}/{s_id}: older {b_older} left unpicked while newer {b_newer} shipped to {st}"
                        )
    return violations


def assert_zero_fefo_violations(plan_result: PlanResult, db_path: Optional[str] = None):
    """Raises if count_fefo_violations finds any violation."""
    violations = count_fefo_violations(plan_result, db_path)
    if violations:
        raise AssertionError("FEFO Violation: " + violations[0])
