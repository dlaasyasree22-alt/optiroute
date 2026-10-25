"""
OptiRoute API (FastAPI). Wraps the forecasting, optimisation, FEFO repair, stress test
and backtest modules, and serves the built web app from frontend/dist.

Run locally:  uvicorn app:app --port 8000   then open http://localhost:8000
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from common.contracts import PlanResult
from common.db import DB_PATH
from ml.forecaster import generate_target_demands
from ml.generator import ensure_store_inventory
from ml.stress_test import evaluate_plan_risk
from solver.backtest import load_backtest, pro_rata_plan, run_backtest
from solver.counterfactual import why_not_query
from solver.fefo_repair import count_fefo_violations, repair_fefo_violations
from solver.optimizer import finalize_plan, solve_allocation

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="OptiRoute API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# In-memory plan store: plan_id -> {"plan", "demands", "request", "risk", "baseline", "approved"}
PLANS: Dict[str, dict] = {}

ensure_store_inventory(DB_PATH)


class GenerateRequest(BaseModel):
    cycle_date: str = "2026-10-26"
    planning_horizon_days: int = Field(7, ge=3, le=14)
    optimality_gap_tolerance: float = Field(0.01, gt=0, le=0.1)
    enable_post_solve_repair: bool = True


class ForcedConstraint(BaseModel):
    type: str = "FORCE_SHIPMENT"
    sku_id: str
    source_warehouse: str
    destination_store: str
    min_units: int = Field(..., gt=0)


class CounterfactualRequest(BaseModel):
    base_plan_id: str
    forced_constraint: ForcedConstraint


def kpis(plan: PlanResult, demands) -> dict:
    on_hand = sum(td.projected_stock_delivery for td in demands)
    shipped = sum(s.units_shipped for s in plan.shipments)
    total = plan.total_demand_units or 1.0
    delivered = max(1.0, total - plan.unmet_demand_units)
    return {
        "projected_fill_rate": round(1 - plan.unmet_demand_units / total, 4),
        "total_freight_cost": plan.total_freight_cost,
        "unmet_demand_pct": round(plan.unmet_demand_units / total * 100, 2),
        "wastage_rate_pct": round(plan.wastage_units / max(1.0, on_hand + shipped) * 100, 2),
        "truck_utilization_pct": plan.truck_utilization_pct,
        "trucks_used": plan.trucks_used,
        "units_shipped": int(shipped),
        "total_demand_units": round(total, 1),
        "unmet_demand_units": plan.unmet_demand_units,
        "wastage_units": plan.wastage_units,
        "writeoff_cost_inr": plan.writeoff_cost,
        "stockout_cost_inr": plan.stockout_cost,
        "freight_per_unit_inr": round(plan.total_freight_cost / delivered, 2),
    }


def plan_payload(plan: PlanResult, demands, extra: Optional[dict] = None) -> dict:
    body = {
        "plan_id": plan.plan_id,
        "solver_status": plan.solver_status,
        "solve_time_seconds": plan.solve_time_seconds,
        "mip_gap": round(plan.mip_gap, 4),
        "kpis": kpis(plan, demands),
        "shipments": [
            {
                "sku_id": s.sku_id,
                "batch_id": s.batch_id,
                "source_warehouse": s.warehouse_id,
                "destination_store": s.store_id,
                "cases_shipped": s.cases_shipped,
                "units_shipped": s.units_shipped,
                "assigned_truck_id": s.truck_id,
                "days_to_expiry_at_arrival": s.days_to_expiry_at_arrival,
                "reason_code": s.reason_code,
            }
            for s in plan.shipments
        ],
    }
    if extra:
        body.update(extra)
    return body


def run_pipeline(req: GenerateRequest):
    """Yields (stage, status, info) tuples as each real stage completes, ending with the result."""
    t = time.time()
    yield ("load", "running", {})
    with sqlite3.connect(DB_PATH) as conn:
        counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ["skus", "lanes", "inventory_batches", "sales_history", "store_inventory"]
        }
    yield ("load", "done", {"seconds": round(time.time() - t, 2), "counts": counts})

    t = time.time()
    yield ("forecast", "running", {})
    demands = generate_target_demands(req.cycle_date, req.planning_horizon_days)
    yield ("forecast", "done", {"seconds": round(time.time() - t, 2), "pairs": len(demands)})

    t = time.time()
    yield ("optimise", "running", {})
    plan = solve_allocation(demands, mip_gap=req.optimality_gap_tolerance, enable_post_solve_repair=False)
    if plan.solver_status == "INFEASIBLE":
        yield ("optimise", "error", {"seconds": round(time.time() - t, 2),
                                     "message": "The optimiser could not find any plan that respects stock and truck limits."})
        return
    yield ("optimise", "done", {"seconds": round(time.time() - t, 2), "status": plan.solver_status, "gap": round(plan.mip_gap, 4)})

    t = time.time()
    yield ("fefo", "running", {})
    before = len(count_fefo_violations(plan))
    if req.enable_post_solve_repair:
        plan = finalize_plan(repair_fefo_violations(plan), demands)
    after = len(count_fefo_violations(plan))
    yield ("fefo", "done", {"seconds": round(time.time() - t, 2), "violations_before": before,
                            "violations_after": after, "skipped": not req.enable_post_solve_repair})

    t = time.time()
    yield ("risk", "running", {})
    risk = evaluate_plan_risk(plan, demands)
    baseline = pro_rata_plan(demands)
    yield ("risk", "done", {"seconds": round(time.time() - t, 2), "scenarios": risk["scenarios_evaluated"]})

    PLANS[plan.plan_id] = {"plan": plan, "demands": demands, "request": req.model_dump(),
                           "risk": risk, "baseline": kpis(baseline, demands), "approved": False}
    yield ("result", "done", plan_payload(plan, demands, {
        "baseline_kpis": PLANS[plan.plan_id]["baseline"],
        "fefo_violations_repaired": before - after,
    }))


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/meta")
def meta():
    with sqlite3.connect(DB_PATH) as conn:
        skus = [dict(zip(["sku_id", "category", "is_perishable", "shelf_life_days", "min_life_at_delivery", "case_pack_size", "volume"], r))
                for r in conn.execute("SELECT sku_id, category, is_perishable, shelf_life_days, min_life_at_delivery, case_pack_size, volume_footprint FROM skus")]
        stores = [r[0] for r in conn.execute("SELECT DISTINCT store_id FROM lanes ORDER BY store_id")]
        warehouses = [r[0] for r in conn.execute("SELECT DISTINCT warehouse_id FROM lanes ORDER BY warehouse_id")]
    return {"skus": skus, "stores": stores, "warehouses": warehouses}


@app.post("/api/v1/plan/generate")
def generate(req: GenerateRequest):
    result = None
    for stage, status, info in run_pipeline(req):
        if status == "error":
            raise HTTPException(422, info["message"])
        if stage == "result":
            result = info
    return result


@app.post("/api/v1/plan/generate/stream")
def generate_stream(req: GenerateRequest):
    """Server-sent events: one event per pipeline stage, the last carrying the full plan."""
    def events():
        try:
            for stage, status, info in run_pipeline(req):
                yield f"data: {json.dumps({'stage': stage, 'status': status, 'info': info})}\n\n"
        except Exception as exc:  # surface failures to the UI instead of dropping the stream
            yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'error', 'info': {'message': str(exc)}})}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")


def _get(plan_id: str) -> dict:
    if plan_id not in PLANS:
        raise HTTPException(404, "This plan is no longer in memory. Generate a new plan.")
    return PLANS[plan_id]


@app.get("/api/v1/plan/{plan_id}/risk")
def risk(plan_id: str):
    return _get(plan_id)["risk"]


@app.post("/api/v1/plan/{plan_id}/approve")
def approve(plan_id: str):
    entry = _get(plan_id)
    entry["approved"] = True
    entry["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return {"plan_id": plan_id, "approved": True, "approved_at": entry["approved_at"]}


@app.post("/api/v1/plan/counterfactual")
def counterfactual(req: CounterfactualRequest):
    entry = _get(req.base_plan_id)
    fc = req.forced_constraint
    result = why_not_query(
        entry["plan"], entry["demands"], fc.sku_id, fc.source_warehouse, fc.destination_store, fc.min_units,
        mip_gap=entry["request"]["optimality_gap_tolerance"],
    )
    result.pop("counterfactual_plan", None)
    return result


@app.get("/api/v1/backtest")
def backtest():
    return load_backtest() or run_backtest()


@app.get("/{path:path}")
def frontend(path: str):
    """Serves the landing page at /, the planner at /app (and any unknown path); nothing outside frontend/ is served."""
    if path == "":
        return FileResponse(FRONTEND / "landing.html")
    target = (FRONTEND / path).resolve()
    if path and target.is_file() and FRONTEND.resolve() in target.parents:
        return FileResponse(target)
    return FileResponse(FRONTEND / "index.html")
