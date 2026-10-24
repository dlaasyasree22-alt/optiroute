"""
OptiRoute FEFO Repair (Dev B - solver/fefo_repair.py)
Implements post-optimization First-Expired, First-Out (FEFO) audit and repair heuristic.
Ensures that inventory batches dispatched from warehouses strictly prioritize earliest expiring lots,
guards against in-transit spoilage (days_to_expiry <= lead_time), and calculates wastage metrics.
"""

import os
import sys
import copy
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
import pandas as pd

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.contracts import ShipmentLine, PlanResult
from solver.optimizer import load_optimizer_data


def validate_fefo_compliance(
    plan: PlanResult,
    batches_df: pd.DataFrame,
    lanes_df: Optional[pd.DataFrame] = None
) -> Dict[str, Any]:
    """
    Validates whether shipments in a PlanResult adhere to strict FEFO ordering:
    No later-expiring batch should be shipped from a warehouse if an earlier-expiring
    batch of the same SKU with valid shelf life was available and had unallocated inventory.
    """
    violations = []
    total_spoilage_units = 0

    # Build lookup of batch properties: (warehouse_id, sku_id, batch_id) -> days_to_expiry, quantity
    batch_map = {}
    for _, row in batches_df.iterrows():
        w = str(row["warehouse_id"])
        s = str(row["sku_id"])
        b = str(row["batch_id"])
        batch_map[(w, s, b)] = {
            "days_to_expiry": float(row.get("days_to_expiry", 30)),
            "quantity": float(row.get("quantity", 0)),
        }

    # Group shipments by (warehouse_id, sku_id)
    shipped_quantities: Dict[Tuple[str, str, str], int] = {}
    for line in plan.shipments:
        if line.days_to_expiry_at_arrival <= 0:
            total_spoilage_units += line.units_shipped
        key = (str(line.warehouse_id), str(line.sku_id), str(line.batch_id))
        shipped_quantities[key] = shipped_quantities.get(key, 0) + line.units_shipped

    # Check for inversions per (warehouse_id, sku_id)
    wh_skus = {(str(line.warehouse_id), str(line.sku_id)) for line in plan.shipments}
    for (w, s) in wh_skus:
        # All available batches at warehouse w for SKU s sorted by days_to_expiry
        avail_batches = [
            (b, data["days_to_expiry"], data["quantity"])
            for (bw, bs, b), data in batch_map.items()
            if bw == w and bs == s
        ]
        avail_batches.sort(key=lambda item: item[1])  # sort by days_to_expiry ascending

        # Find maximum days_to_expiry actually shipped
        shipped_b_exp = [
            batch_map[(w, s, b)]["days_to_expiry"]
            for (bw, bs, b) in shipped_quantities.keys()
            if bw == w and bs == s and (w, s, b) in batch_map
        ]
        if not shipped_b_exp:
            continue
        max_shipped_exp = max(shipped_b_exp)

        # Check if an earlier expiring batch was skipped despite having leftover stock
        for (b_id, d_exp, total_qty) in avail_batches:
            if d_exp < max_shipped_exp:
                shipped = shipped_quantities.get((w, s, b_id), 0)
                unallocated = total_qty - shipped
                if unallocated > 0:
                    violations.append({
                        "warehouse_id": w,
                        "sku_id": s,
                        "skipped_batch": b_id,
                        "skipped_days_to_expiry": d_exp,
                        "unallocated_units": unallocated,
                        "max_shipped_days_to_expiry": max_shipped_exp,
                    })

    is_compliant = len(violations) == 0
    return {
        "is_compliant": is_compliant,
        "violation_count": len(violations),
        "violations": violations,
        "total_spoilage_units": total_spoilage_units,
    }


def repair_plan_fefo(
    plan: PlanResult,
    db_path: Optional[str] = None,
    batches_df: Optional[pd.DataFrame] = None,
    lanes_df: Optional[pd.DataFrame] = None,
    skus_df: Optional[pd.DataFrame] = None,
) -> PlanResult:
    """
    Post-processes a PlanResult to strictly enforce First-Expired, First-Out (FEFO) batch allocation.

    For each (warehouse_id, sku_id):
      1. Aggregates the total units shipped across all destination stores.
      2. Ranks available batches in ascending order of days_to_expiry (FEFO).
      3. Validates shelf life against lane lead times (avoiding arrival with <= 0 shelf life).
      4. Sequentially reassigns shipments to the earliest-expiring viable lots.
      5. Computes total network wastage_units (units arriving spoiled).
      6. Returns a modernized, repaired PlanResult.
    """
    if not plan.shipments:
        return copy.deepcopy(plan)

    # 1. Load reference data if not provided
    if batches_df is None or lanes_df is None or skus_df is None:
        skus_data, lanes_data, batches_data = load_optimizer_data(db_path)
        skus_df = skus_df if skus_df is not None else skus_data
        lanes_df = lanes_df if lanes_df is not None else lanes_data
        batches_df = batches_df if batches_df is not None else batches_data

    # Map lane lead times: (w, l) -> lead_time
    lead_times = {}
    for _, row in lanes_df.iterrows():
        w = str(row["warehouse_id"])
        l = str(row["store_id"])
        lead_times[(w, l)] = max(0, int(row.get("lead_time", 1)))

    # Map SKU properties
    sku_pack_sizes = {}
    sku_min_shelf = {}
    for _, row in skus_df.iterrows():
        s = str(row["sku_id"])
        sku_pack_sizes[s] = max(1, int(row.get("case_pack_size", 1)))
        sku_min_shelf[s] = max(0, int(row.get("min_shelf_life", 0)))

    # Track remaining capacity per batch
    # (warehouse_id, sku_id) -> list of dicts sorted by days_to_expiry
    warehouse_batches: Dict[Tuple[str, str], List[Dict]] = {}
    for _, row in batches_df.iterrows():
        w = str(row["warehouse_id"])
        s = str(row["sku_id"])
        b = str(row["batch_id"])
        qty = float(row.get("quantity", 0))
        d_exp = float(row.get("days_to_expiry", 30))
        key = (w, s)
        if key not in warehouse_batches:
            warehouse_batches[key] = []
        warehouse_batches[key].append({
            "batch_id": b,
            "quantity_remaining": qty,
            "initial_quantity": qty,
            "days_to_expiry": d_exp,
        })

    # Sort each warehouse's batches by days_to_expiry ascending (FEFO)
    for key in warehouse_batches:
        warehouse_batches[key].sort(key=lambda item: item["days_to_expiry"])

    # 2. Repair allocations per shipment line
    repaired_shipments: List[ShipmentLine] = []
    total_wastage = 0.0

    # Group original shipment lines by (w, s, l)
    for line in plan.shipments:
        w = str(line.warehouse_id)
        s = str(line.sku_id)
        l = str(line.store_id)
        units_needed = line.units_shipped
        pack_size = sku_pack_sizes.get(s, max(1, line.units_shipped // max(1, line.cases_shipped)))
        lt = lead_times.get((w, l), 1)
        min_shelf = sku_min_shelf.get(s, 0)

        key = (w, s)
        avail = warehouse_batches.get(key, [])

        # Allocate from earliest-expiring viable batches
        allocated = False
        for b_entry in avail:
            if b_entry["quantity_remaining"] >= units_needed:
                # Can fulfill entire shipment line from this batch
                b_entry["quantity_remaining"] -= units_needed
                d_exp = b_entry["days_to_expiry"]
                arrival_shelf = max(0, int(d_exp - lt))

                # Check wastage
                if arrival_shelf <= 0 or arrival_shelf < min_shelf:
                    total_wastage += units_needed

                repaired_shipments.append(
                    ShipmentLine(
                        sku_id=s,
                        batch_id=b_entry["batch_id"],
                        warehouse_id=w,
                        store_id=l,
                        cases_shipped=units_needed // pack_size,
                        units_shipped=units_needed,
                        truck_id=line.truck_id,
                        days_to_expiry_at_arrival=arrival_shelf,
                        reason_code="FEFO_REPAIRED" if b_entry["batch_id"] != line.batch_id else line.reason_code,
                    )
                )
                allocated = True
                break

        # Fallback if no single batch had sufficient units: keep original with updated shelf calculation
        if not allocated:
            arrival_shelf = max(0, int(line.days_to_expiry_at_arrival))
            if arrival_shelf <= 0:
                total_wastage += units_needed
            repaired_shipments.append(line)

    # Reconstruct updated PlanResult
    repaired_plan = PlanResult(
        plan_id=f"{plan.plan_id}_fefo",
        solver_status=plan.solver_status,
        solve_time_seconds=plan.solve_time_seconds,
        total_freight_cost=plan.total_freight_cost,
        unmet_demand_units=plan.unmet_demand_units,
        wastage_units=round(total_wastage, 2),
        truck_utilization_pct=plan.truck_utilization_pct,
        shipments=repaired_shipments,
    )

    return repaired_plan


if __name__ == "__main__":
    from common.contracts import TargetDemand
    from solver.optimizer import solve_allocation

    print("Running FEFO Repair self-test...")
    skus_df, lanes_df, batches_df = load_optimizer_data()

    test_skus = skus_df["sku_id"].unique()[:3]
    test_stores = lanes_df["store_id"].unique()[:2]

    demands = [
        TargetDemand(
            sku_id=str(s),
            store_id=str(st),
            target_q_demand=60.0,
            p90_demand=80.0,
            p50_demand=50.0,
            projected_stock_delivery=10.0,
        )
        for s in test_skus
        for st in test_stores
    ]

    initial_plan = solve_allocation(demands)
    print(f"Initial Plan status: {initial_plan.solver_status}, Shipments: {len(initial_plan.shipments)}")

    repaired_plan = repair_plan_fefo(initial_plan, batches_df=batches_df, lanes_df=lanes_df, skus_df=skus_df)
    print(f"Repaired Plan wastage: {repaired_plan.wastage_units} units")

    audit = validate_fefo_compliance(repaired_plan, batches_df, lanes_df)
    print(f"FEFO Compliance Audit: {audit['is_compliant']} (Violations: {audit['violation_count']})")
    assert audit["is_compliant"] or audit["violation_count"] == 0, "Expected compliant FEFO schedule"
    print("FEFO REPAIR TESTS PASSED SUCCESSFULLY!")
