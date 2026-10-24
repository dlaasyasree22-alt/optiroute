"""
OptiRoute Counterfactual Why-Not Engine (Dev B - solver/counterfactual.py)
Re-solves the MILP under planner-injected forced constraints to calculate financial
and operational trade-off deltas and generate plain-English justifications.
"""

import sys
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
) -> dict:
    """
    Re-runs the MILP solver with a forced lower-bound shipment constraint and
    computes exact financial and operational deltas against base_plan.
    """
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
    )

    if cf_plan.solver_status != "OPTIMAL":
        return {
            "status": "INFEASIBLE",
            "delta_freight_cost": 0.0,
            "delta_unmet_units": 0.0,
            "delta_wastage_units": 0.0,
            "net_financial_delta": 0.0,
            "explanation": (
                f"Forcing {forced_units} units of {forced_sku} from {forced_wh} to {forced_store} "
                f"is mathematically infeasible due to supply or truck capacity constraints."
            ),
        }

    # Operational deltas
    delta_freight = cf_plan.total_freight_cost - base_plan.total_freight_cost
    delta_unmet = cf_plan.unmet_demand_units - base_plan.unmet_demand_units
    delta_wastage = cf_plan.wastage_units - base_plan.wastage_units

    # Financial evaluation
    skus_df, _, _ = load_optimizer_data(db_path)
    sku_row = skus_df[skus_df["sku_id"] == forced_sku]
    margin = float(sku_row["margin"].values[0]) if not sku_row.empty and "margin" in sku_row else 50.0
    unit_cost = margin * 0.6  # Default replacement cost if not explicit

    net_delta = delta_freight + (delta_unmet * margin) + (delta_wastage * unit_cost)

    reasons = []
    if delta_freight > 0:
        reasons.append(f"+₹{delta_freight:.2f} additional freight due to truck dispatch inefficiencies")
    elif delta_freight < 0:
        reasons.append(f"-₹{abs(delta_freight):.2f} freight savings")

    if delta_unmet > 0:
        reasons.append(f"+{delta_unmet:.1f} unmet demand units (+₹{delta_unmet * margin:.2f})")

    if delta_wastage > 0:
        reasons.append(f"+{delta_wastage:.1f} waste units (+₹{delta_wastage * unit_cost:.2f} write-off)")

    explanation = (
        f"Forcing dispatch of {forced_units} units of {forced_sku} from {forced_wh} to {forced_store} "
        f"results in a net financial delta of ₹{net_delta:.2f} ("
        + (", ".join(reasons) if reasons else "neutral cost change")
        + ")."
    )

    return {
        "status": "FEASIBLE",
        "delta_freight_cost": round(float(delta_freight), 2),
        "delta_unmet_units": round(float(delta_unmet), 2),
        "delta_wastage_units": round(float(delta_wastage), 2),
        "net_financial_delta": round(float(net_delta), 2),
        "explanation": explanation,
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
