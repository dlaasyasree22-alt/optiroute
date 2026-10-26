"""
OptiRoute Counterfactual Why-Not Engine (Dev B - solver/counterfactual.py)
Re-solves the MILP under planner-injected forced constraints to calculate financial
and operational trade-off deltas and generate plain-English justifications.
"""

import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.contracts import PlanResult, TargetDemand
from common.db import DB_PATH
from solver.optimizer import solve_allocation, load_optimizer_data


def why_not_query(
    base_plan: PlanResult,
    target_demands: List[TargetDemand],
    forced_sku: str,
    forced_wh: str,
    forced_store: str,
    forced_units: int,
    db_path: Optional[str] = None,
    **solver_kwargs,
) -> dict:
    """
    Re-runs the MILP solver with a forced lower-bound shipment constraint and
    computes exact financial and operational deltas against base_plan.
    """
    start = time.time()
    forced_constraint = [{
        "sku_id": forced_sku,
        "warehouse_id": forced_wh,
        "store_id": forced_store,
        "min_units": forced_units,
    }]

    cf_plan = solve_allocation(
        target_demands=target_demands,
        db_path=db_path,
        forced_shipments=forced_constraint,
        enable_post_solve_repair=True,
        **solver_kwargs,
    )
    elapsed = round(time.time() - start, 2)

    def lane_units(plan, wh=None):
        return int(sum(
            s.units_shipped for s in plan.shipments
            if s.sku_id == forced_sku and s.store_id == forced_store and (wh is None or s.warehouse_id == wh)
        ))

    base_from_wh = lane_units(base_plan, forced_wh)
    base_total = lane_units(base_plan)
    forced_delivered = lane_units(cf_plan, forced_wh)

    if cf_plan.solver_status == "INFEASIBLE" or forced_delivered < forced_units:
        return {
            "counterfactual_status": "INFEASIBLE",
            "solve_time_seconds": elapsed,
            "delta_kpis": None,
            "base_units_from_warehouse": base_from_wh,
            "base_units_to_store": base_total,
            "root_cause_explanation": (
                f"{forced_wh} cannot send {forced_units} units of {forced_sku} to {forced_store}. "
                f"Either it does not hold enough stock that would still arrive with the minimum shelf life, "
                f"or the lane is closed."
            ),
        }

    d_freight = cf_plan.total_freight_cost - base_plan.total_freight_cost
    d_waste = cf_plan.wastage_units - base_plan.wastage_units
    d_writeoff = cf_plan.writeoff_cost - base_plan.writeoff_cost
    d_unmet = cf_plan.unmet_demand_units - base_plan.unmet_demand_units
    d_stockout = cf_plan.stockout_cost - base_plan.stockout_cost
    d_trucks = cf_plan.trucks_used - base_plan.trucks_used
    net = d_freight + d_writeoff + d_stockout

    parts = []
    if abs(d_freight) >= 1:
        verb = "adds" if d_freight > 0 else "saves"
        truck_note = f" ({d_trucks:+d} trucks)" if d_trucks else ""
        parts.append(f"{verb} Rs {abs(d_freight):,.0f} in freight{truck_note}")
    if abs(d_writeoff) >= 1:
        verb = "adds" if d_writeoff > 0 else "avoids"
        parts.append(f"{verb} Rs {abs(d_writeoff):,.0f} of expiry write-off ({abs(d_waste):,.0f} units)")
    if abs(d_stockout) >= 1:
        verb = "adds" if d_stockout > 0 else "removes"
        parts.append(f"{verb} {abs(d_unmet):,.0f} units of unmet demand elsewhere (Rs {abs(d_stockout):,.0f})")
    summary = "; ".join(parts) if parts else "changes nothing material"
    explanation = (
        f"Forcing {forced_units} units of {forced_sku} from {forced_wh} to {forced_store} {summary}. "
        f"Net effect: Rs {abs(net):,.0f} {'more expensive' if net > 0 else 'cheaper'} than the recommended plan."
    )

    return {
        "counterfactual_status": "FEASIBLE",
        "solve_time_seconds": elapsed,
        "delta_kpis": {
            "delta_freight_cost": round(float(d_freight), 2),
            "delta_waste_units": round(float(d_waste), 2),
            "delta_writeoff_inr": round(float(d_writeoff), 2),
            "delta_stockout_units": round(float(d_unmet), 2),
            "delta_stockout_inr": round(float(d_stockout), 2),
            "delta_trucks": int(d_trucks),
            "net_financial_delta_inr": round(float(net), 2),
        },
        "base_units_from_warehouse": base_from_wh,
        "base_units_to_store": base_total,
        "root_cause_explanation": explanation,
        "counterfactual_plan": cf_plan,
    }


if __name__ == "__main__":
    from solver.fefo_repair import assert_zero_fefo_violations

    skus_df, lanes_df, batches_df = load_optimizer_data()
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

    print("1. Running Base Solve with automatic FEFO repair...")
    base_plan = solve_allocation(mock_demands, enable_post_solve_repair=True)
    print(f"Base Status: {base_plan.solver_status}, Freight: ₹{base_plan.total_freight_cost}")
    assert base_plan.solver_status == "OPTIMAL"

    print("2. Verifying FEFO Invariance...")
    assert_zero_fefo_violations(base_plan)
    print("FEFO verification passed: strictly 0 violations!")

    print("3. Running Counterfactual Why-Not Query...")
    sample_sku = str(test_skus[0])
    sample_wh = str(batches_df["warehouse_id"].iloc[0])
    sample_store = str(test_stores[0])

    cf_result = why_not_query(
        base_plan=base_plan,
        target_demands=mock_demands,
        forced_sku=sample_sku,
        forced_wh=sample_wh,
        forced_store=sample_store,
        forced_units=150,
    )

    print("Counterfactual Query Result:")
    for k, v in cf_result.items():
        print(f"  {k}: {v}")
    assert cf_result["status"] in ["FEASIBLE", "INFEASIBLE"]
    print("ALL TESTS PASSED SUCCESSFULLY!")
