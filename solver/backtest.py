"""
OptiRoute 4-tier ablation backtest (solver/backtest.py)

Each policy plans once from the same starting stock and forecast. Every plan is then
replayed against 10 seeds x 8 simulated demand weeks (Negative Binomial demand with
regional LogNormal shocks), and KPI means with 95% confidence intervals across seeds
are reported. Results are cached to data/backtest.json for the dashboard.

  A  Pro-rata:       stock shared in proportion to recent sales, cheapest lane, batches in ID order
  B  Point MILP:     HiGHS MILP sized to the median forecast (P50), no shelf-life rules, no FEFO repair
  C  Quantile MILP:  as B but sized to the newsvendor target between P50 and P90
  D  Full OptiRoute: as C plus shelf-life pruning, expiry cost and FEFO repair
"""

import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.contracts import PlanResult, ShipmentLine, TargetDemand
from ml.forecaster import generate_target_demands
from ml.stress_test import NB_DISPERSION
from solver.fefo_repair import count_fefo_violations
from solver.optimizer import _lane_info, _sku_info, finalize_plan, load_optimizer_data, solve_allocation

CACHE_PATH = _REPO_ROOT / "data" / "backtest.json"
SEEDS, WEEKS = 10, 8

VARIANTS = [
    ("A", "Pro-rata (today's spreadsheet method)"),
    ("B", "Optimiser on median forecast"),
    ("C", "Optimiser on risk-sized forecast"),
    ("D", "Full OptiRoute (adds shelf life and FEFO)"),
]


def pro_rata_plan(demands: List[TargetDemand], closed_warehouses: Tuple[str, ...] = ()) -> PlanResult:
    """Baseline A: each store gets stock in proportion to its median demand, shelf life ignored."""
    skus_df, lanes_df, batches_df = load_optimizer_data()
    sku = _sku_info(skus_df)
    lanes = _lane_info(lanes_df)
    stock: Dict[Tuple[str, str], List[dict]] = {}
    for _, r in batches_df.sort_values("batch_id").iterrows():
        if r["warehouse_id"] in closed_warehouses:
            continue
        stock.setdefault((r["warehouse_id"], r["sku_id"]), []).append(
            {"batch_id": r["batch_id"], "left": int(r["quantity"]), "days": int(r["days_to_expiry"])}
        )

    by_sku: Dict[str, List[TargetDemand]] = {}
    for td in demands:
        by_sku.setdefault(td.sku_id, []).append(td)

    lines: List[ShipmentLine] = []
    for s, tds in by_sku.items():
        k = sku[s]["case_pack_size"]
        want = {td.store_id: max(0.0, td.p50_demand - td.on_hand_units) for td in tds}
        supply = sum(b["left"] for (w, ss), bl in stock.items() if ss == s for b in bl)
        scale = min(1.0, supply / max(1.0, sum(want.values())))
        for store, units in want.items():
            cases = int(units * scale // k)
            # Cheapest lane first, batches in arrival (ID) order regardless of expiry
            for w in sorted({w for (w, ss) in stock if ss == s}, key=lambda w: lanes[(w, store)]["variable_cost"]):
                for b in stock[(w, s)]:
                    take = min(cases, b["left"] // k)
                    if take <= 0:
                        continue
                    b["left"] -= take * k
                    cases -= take
                    lines.append(ShipmentLine(
                        sku_id=s, batch_id=b["batch_id"], warehouse_id=w, store_id=store,
                        cases_shipped=take, units_shipped=take * k, truck_id="",
                        days_to_expiry_at_arrival=max(0, b["days"] - lanes[(w, store)]["lead_time"]),
                        reason_code="",
                    ))
                if cases <= 0:
                    break

    plan = PlanResult(
        plan_id="baseline_pro_rata", solver_status="HEURISTIC", solve_time_seconds=0.0,
        total_freight_cost=0.0, unmet_demand_units=0.0, wastage_units=0.0,
        truck_utilization_pct=0.0, shipments=lines,
    )
    return finalize_plan(plan, demands)


def build_variant_plan(variant: str, demands: List[TargetDemand], closed_warehouses: Tuple[str, ...] = ()) -> PlanResult:
    if variant == "A":
        return pro_rata_plan(demands, closed_warehouses)
    _, lanes_df, _ = load_optimizer_data()
    closed = [(w, l) for w, l in zip(lanes_df["warehouse_id"], lanes_df["store_id"]) if w in closed_warehouses]
    if variant == "B":
        point = [dataclasses.replace(td, target_q_demand=td.p50_demand) for td in demands]
        plan = solve_allocation(point, use_shelf_life=False, enable_post_solve_repair=False, closed_lanes=closed)
        return finalize_plan(plan, demands)
    if variant == "C":
        plan = solve_allocation(demands, use_shelf_life=False, enable_post_solve_repair=False, closed_lanes=closed)
        return finalize_plan(plan, demands)
    return solve_allocation(demands, closed_lanes=closed)


def replay(plan: PlanResult, demands: List[TargetDemand], seed: int, surge: float = 1.0) -> Dict[str, float]:
    """Plays one plan against WEEKS simulated demand weeks; returns the seed's average KPIs."""
    skus_df, _, _ = load_optimizer_data()
    sku = _sku_info(skus_df)
    rng = np.random.default_rng(1000 + seed)

    regions = np.array([(int(td.store_id.split("_")[1]) - 1) % 3 for td in demands])
    p50 = np.array([td.p50_demand for td in demands]) * surge
    stock = np.array([td.projected_stock_delivery for td in demands])
    idx = {(td.sku_id, td.store_id): i for i, td in enumerate(demands)}
    shipped = np.zeros(len(demands))
    lines_by_pair: Dict[int, List[ShipmentLine]] = {}
    for sh in plan.shipments:
        i = idx.get((sh.sku_id, sh.store_id))
        if i is not None:
            shipped[i] += sh.units_shipped
            lines_by_pair.setdefault(i, []).append(sh)
    available = stock + shipped
    supply_inflow = stock.sum() + shipped.sum()

    fill, unmet_pct, waste_pct = [], [], []
    for _ in range(WEEKS):
        beta = rng.lognormal(0.0, 0.15, size=3)[regions]
        mean = np.maximum(0.01, p50 * beta)
        demand = rng.negative_binomial(NB_DISPERSION, NB_DISPERSION / (NB_DISPERSION + mean))
        delivered = np.minimum(demand, available)
        fill.append(delivered.sum() / demand.sum())
        unmet_pct.append((demand - delivered).sum() / demand.sum())

        # Units that expire at the store before this week's actual demand can sell them
        expired = 0.0
        daily = demand / demands[0].horizon_days
        for i, lines in lines_by_pair.items():
            if not sku[demands[i].sku_id]["is_perishable"]:
                continue
            ahead = stock[i]
            for sh in sorted(lines, key=lambda x: x.days_to_expiry_at_arrival):
                sellable = max(0.0, daily[i] * sh.days_to_expiry_at_arrival - ahead)
                expired += max(0.0, sh.units_shipped - sellable)
                ahead += sh.units_shipped
        waste_pct.append(expired / max(1.0, supply_inflow))

    delivered_units = float(np.mean(fill)) * float(p50.sum())
    return {
        "fill_rate_pct": float(np.mean(fill)) * 100,
        "unmet_demand_pct": float(np.mean(unmet_pct)) * 100,
        "wastage_rate_pct": float(np.mean(waste_pct)) * 100,
        "freight_cost_inr": plan.total_freight_cost,
        "freight_per_delivered_unit_inr": plan.total_freight_cost / max(1.0, delivered_units),
        "truck_utilization_pct": plan.truck_utilization_pct,
    }


def _summarise(per_seed: List[Dict[str, float]], fefo: int) -> Dict[str, Dict[str, float]]:
    out = {}
    for key in per_seed[0]:
        vals = np.array([r[key] for r in per_seed])
        half = 1.96 * vals.std(ddof=1) / np.sqrt(len(vals)) if len(vals) > 1 else 0.0
        out[key] = {"mean": round(float(vals.mean()), 3), "ci95": [round(float(vals.mean() - half), 3), round(float(vals.mean() + half), 3)]}
    out["fefo_violations"] = {"mean": fefo, "ci95": [fefo, fefo]}
    return out


def run_backtest(cycle_date: str = "2026-10-26") -> dict:
    start = time.time()
    demands = generate_target_demands(cycle_date)
    variants, plans = [], {}
    for vid, label in VARIANTS:
        plan = build_variant_plan(vid, demands)
        plans[vid] = plan
        per_seed = [replay(plan, demands, s) for s in range(SEEDS)]
        variants.append({"id": vid, "label": label, "metrics": _summarise(per_seed, len(count_fefo_violations(plan)))})

    def mean(vid, key):
        return next(v for v in variants if v["id"] == vid)["metrics"][key]["mean"]

    attribution = []
    for name, lo, hi in [
        ("Optimisation instead of pro-rata", "A", "B"),
        ("Planning for uncertain demand", "B", "C"),
        ("Shelf-life rules and FEFO repair", "C", "D"),
    ]:
        attribution.append({
            "step": name, "from": lo, "to": hi,
            "fill_rate_pp": round(mean(hi, "fill_rate_pct") - mean(lo, "fill_rate_pct"), 2),
            "wastage_pp": round(mean(hi, "wastage_rate_pct") - mean(lo, "wastage_rate_pct"), 2),
            "freight_inr": round(mean(hi, "freight_cost_inr") - mean(lo, "freight_cost_inr"), 0),
        })

    regimes = []
    for name, surge, closed in [
        ("Normal week", 1.0, ()),
        ("Demand surge +30%", 1.3, ()),
        ("Warehouse WH1 offline", 1.0, ("WH1",)),
    ]:
        pa = plans["A"] if not closed else build_variant_plan("A", demands, closed)
        pd_ = plans["D"] if not closed else build_variant_plan("D", demands, closed)
        ra = [replay(pa, demands, s, surge) for s in range(SEEDS)]
        rd = [replay(pd_, demands, s, surge) for s in range(SEEDS)]
        regimes.append({
            "regime": name,
            "baseline_fill_rate_pct": round(float(np.mean([r["fill_rate_pct"] for r in ra])), 2),
            "optiroute_fill_rate_pct": round(float(np.mean([r["fill_rate_pct"] for r in rd])), 2),
            "baseline_wastage_pct": round(float(np.mean([r["wastage_rate_pct"] for r in ra])), 2),
            "optiroute_wastage_pct": round(float(np.mean([r["wastage_rate_pct"] for r in rd])), 2),
            "seeds_optiroute_better": int(sum(d["fill_rate_pct"] > a["fill_rate_pct"] for a, d in zip(ra, rd))),
            "seeds": SEEDS,
        })

    result = {
        "cycle_date": cycle_date, "seeds": SEEDS, "weeks_per_seed": WEEKS,
        "method": "Each policy plans once from identical stock and forecasts, then is replayed against "
                  f"{SEEDS} seeds x {WEEKS} simulated demand weeks. Intervals are 95% across seeds.",
        "variants": variants, "attribution": attribution, "regimes": regimes,
        "runtime_seconds": round(time.time() - start, 1),
    }
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2))
    return result


def load_backtest() -> Optional[dict]:
    return json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else None


if __name__ == "__main__":
    r = run_backtest()
    for v in r["variants"]:
        m = v["metrics"]
        print(v["id"], {k: m[k]["mean"] for k in m})
    print(r["attribution"])
    print(r["regimes"])
    print("runtime", r["runtime_seconds"])
