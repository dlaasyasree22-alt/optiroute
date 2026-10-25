"""
Independent check of a generated plan. Recomputes every hard constraint and KPI straight
from the database, without using the optimiser's own bookkeeping.

Run:  python checks/verify_plan.py
"""
import collections
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.forecaster import generate_target_demands
from solver.fefo_repair import count_fefo_violations
from solver.optimizer import solve_allocation

db = sqlite3.connect(ROOT / "optiroute_data.sqlite")
skus = {r[0]: dict(zip(["vol", "pack", "min_life"], r[1:])) for r in db.execute(
    "select sku_id, volume_footprint, case_pack_size, min_life_at_delivery from skus")}
batches = {r[0]: dict(zip(["sku", "wh", "qty", "days"], r[1:])) for r in db.execute(
    "select batch_id, sku_id, warehouse_id, quantity_units, days_to_expiry from inventory_batches")}
lanes = {(r[0], r[1]): dict(zip(["fixed", "var", "lead"], r[2:])) for r in db.execute(
    "select warehouse_id, store_id, fixed_truck_cost, variable_unit_cost, lead_time_days from lanes")}

demands = generate_target_demands("2026-10-26")
plan = solve_allocation(demands)
print(f"solver status {plan.solver_status}, proven gap {plan.mip_gap:.2%}, lines {len(plan.shipments)}")

fails = collections.Counter()
used = collections.Counter()
truck_vol = collections.Counter()
truck_lane = {}
for s in plan.shipments:
    b, k = batches[s.batch_id], skus[s.sku_id]
    used[s.batch_id] += s.units_shipped
    if b["sku"] != s.sku_id or b["wh"] != s.warehouse_id:
        fails["batch belongs to another product or warehouse"] += 1
    if s.units_shipped != s.cases_shipped * k["pack"]:
        fails["units are not whole cases"] += 1
    life = b["days"] - lanes[(s.warehouse_id, s.store_id)]["lead"]
    if life < k["min_life"]:
        fails["arrives below minimum shelf life"] += 1
    if life != s.days_to_expiry_at_arrival:
        fails["shelf life on arrival misreported"] += 1
    truck_vol[s.truck_id] += s.units_shipped * k["vol"]
    truck_lane[s.truck_id] = (s.warehouse_id, s.store_id)
for bid, units in used.items():
    if units > batches[bid]["qty"]:
        fails["batch shipped more than it holds"] += 1
for vol in truck_vol.values():
    if vol > 500 + 1e-6:
        fails["truck over capacity"] += 1

freight = sum(lanes[l]["fixed"] for l in truck_lane.values()) + sum(
    s.units_shipped * lanes[(s.warehouse_id, s.store_id)]["var"] for s in plan.shipments)
util = sum(truck_vol.values()) / (len(truck_vol) * 500) * 100
shipped = collections.Counter()
for s in plan.shipments:
    shipped[(s.sku_id, s.store_id)] += s.units_shipped
unmet = sum(max(0, t.target_q_demand - t.projected_stock_delivery - shipped[(t.sku_id, t.store_id)]) for t in demands)
demand = sum(t.target_q_demand for t in demands)

print("hard constraints:", "ALL PASS" if not fails else dict(fails))
print(f"FEFO violations: {len(count_fefo_violations(plan))}")
print(f"freight  engine {plan.total_freight_cost:,.2f}   recomputed {freight:,.2f}")
print(f"trucks   engine {plan.trucks_used}   recomputed {len(truck_vol)}")
print(f"util     engine {plan.truck_utilization_pct:.2f}%   recomputed {util:.2f}%")
print(f"fill     engine {1 - plan.unmet_demand_units / plan.total_demand_units:.4f}   recomputed {1 - unmet / demand:.4f}")
